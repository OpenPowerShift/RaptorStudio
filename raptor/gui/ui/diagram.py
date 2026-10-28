"""RBD diagram canvas — QGraphicsView/QGraphicsScene for blocks/nodes/links.

Model-backed: block/node items read positions from `raptor.core.model`
objects and write back on move; links track connected items and update
dynamically.

Ports the canvas behaviour of Raptor's Raptor 7; the
drawing fixtures live under `tests/data/models/` (e.g. Phasing1.rbd).
"""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req

from PySide6.QtWidgets import QFrame
import os
from collections.abc import Callable
from typing import Any

from shiboken6 import isValid
from PySide6.QtCore import (
    Qt, Signal, QEvent, QRectF, QPointF, QLineF, QSize, QTimer,
)
from PySide6.QtGui import (
    QPen, QBrush, QColor, QFont, QPainter, QPixmap, QUndoStack, QKeySequence,
    QPainterPath,
)
from PySide6.QtWidgets import (
    QApplication, QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsLineItem, QWidget, QVBoxLayout,
)

from raptor.core.fileio.rbd import RbdFile, RbdSection
from raptor.core.fileio.rbdlib import dist_param_count as _dist_param_count
from raptor.core.fileio import view_transform
from raptor.gui import row_ops
from raptor.gui.clipboard_plan import count_elements, plan_copy, plan_paste
from raptor.gui.ui.diagram_items import links_affected_by_moving
from raptor.gui.hierarchy_plan import (plan_collapse, plan_dissolve,
                                       plan_import)
from raptor.gui.model_edit import (BLOCK_COST_FIELDS, DEPENDENCY_BADGE,
                                   PM_OPTION_ATTRS, SUBLABELS, EditedFields,
                                   ModelEditor, _pos)
from raptor.core.fileio.typed import (
    block_from_row, node_from_row, link_from_row, hier_from_row,
)
from raptor.core.model import RBDBlock, RBDHier, RBDNode, Link
from raptor.gui.resources.design_tokens import (
    COLOUR, ThemeManager,
)
from contextlib import contextmanager
from raptor.gui.ui.diagram_live import LiveOverlayMixin
from raptor.gui.ui.diagram_menu import ContextMenuMixin
from raptor.gui.ui import block_labels
from raptor.gui.ui import settings as app_settings
from raptor.gui.ui.diagram_common import (  # noqa: F401
    BLOCK_H,
    BLOCK_W,
    MIN_ELEMENT_GAP,
    NODE_R,
    SNAP_SUBDIVISIONS,
    _PARENT_COLS,
    _SNAP_STATE,  # noqa: F401  (re-exported: tests reset it for bare scenes)
    _dep_focus_offered,
    _properties_offered,
    _safe_int,
    log,
)
from raptor.gui.ui.diagram_commands import (  # noqa: F401
    AddCmd,
    BulkGeometryCmd,
    LayoutCmd,
    CollapseCmd,
    DeleteCmd,
    DepResetCmd,
    DissolveCmd,
    DistCmd,
    IdCellCmd,
    LibUpdateCmd,
    MoveCmd,
    MoveOutCmd,
    ParamsCmd,
    PhaseCmd,
    ReParentCmd,
    RenameCmd,
    RowCellCmd,
    StreamCmd,
    TableCellCmd,
    TableRowCmd,
    _ViewCmd,
)
from raptor.gui.ui.diagram_items import (  # noqa: F401
    LOD_LABELS_HYSTERESIS,
    LOD_LABELS_MIN_SCALE,
    ZOOM_MAX,
    ZOOM_MIN,
    _BlockItem,
    _CLICK_DRAG_DEADZONE_PX,
    _DEP_BADGE_D,
    _EventItem,
    _GridBackground,
    _HierItem,
    _LinkItem,
    _NUDGE_DIRS,
    _NodeItem,
    _apply_hover_cursor,
    _arrow,
    _blend,
    _hover_cursor_for,
    _next_grid_line,
    _snapped_x,
    _snapped_y,
    _view_status,
)
from raptor.gui.ui.diagram_annotations import (  # noqa: F401
    ANNO_APPLICABLE_PROPS,
    ANNO_DEFAULTS,
    ANNO_KIND_NOUNS,
    ANNO_LINE_STYLES,
    ANNO_ORDER_COMMANDS,
    ANNO_Z_BANDS,
    ANNO_Z_BEHIND,
    ANNO_Z_FRONT,
    ANNO_Z_MIDDLE,
    AnnoCmd,
    AnnoEditCmd,
    CommentCmd,
    _ANNO_CLASSES,
    _ANNO_TYPES,
    _AnnoEllipse,
    _AnnoEndHandle,
    _AnnoHandle,
    _AnnoLine,
    _AnnoRect,
    _AnnoText,
    _AnnotationBase,
    _CursorHook,
    _HandleHitAreaMixin,
    _LOW_CONTRAST_RATIO,
    _anno_base_props,
    _anno_ctor_args,
    _anno_pen_brush,
    _anno_selection_ring,
    _anno_unselected_option,
    _shift_annotation,
    annotation_is_low_contrast,
)


class DiagramView(ContextMenuMixin, LiveOverlayMixin, QGraphicsView):
    block_selected = Signal(int, str)
    node_selected = Signal(int, str)
    selection_cleared = Signal()
    #: Emitted on every level change with the new current ParentId
    #: (0 = home) -- MainWindow uses it to enable Up/Home navigation.
    level_changed = Signal(int)
    hier_selected = Signal(int)
    event_selected = Signal(int)
    #: (annotation id) -- drives the Inspector's colour/font tab, same
    #: as block/node/event/hier selection.
    annotation_selected = Signal(int)
    #: Emitted when the user actually drags a block/node -- not during
    #: `load_rbd`'s own initial placement (guarded by `_loading` below).
    #: `MainWindow` uses this to mark the model dirty.
    model_changed = Signal()
    #: A change to LAYOUT ALONE: element/annotation positions, sizes and
    #: z-order. Nothing the engine simulates reads these (the results hash
    #: ignores layout too), so MainWindow marks the file dirty without
    #: invalidating its memoised validation, re-running the Raptor 7
    #: rules, or rebuilding the Inspector/Explorer/Data Tables. Moving a
    #: block on a 3,470-block model used to emit `model_changed` for every
    #: drag step, which cost a whole-model validation (seconds) --
    #: including on the click that followed the drag, because the revision
    #: it bumped had made the cached validity stale.
    layout_changed = Signal()
    #: (block_id, "fail"|"repair") -- a click landed on a block while
    #: Failure-Effects fail/repair was armed (see `_fev_armed_mode`).
    fev_target_clicked = Signal(int, str)
    #: A right-click while Failure Effects View is open (Raptor 7's menu 4); carries the global pointer position.
    fev_menu_requested = Signal(object)
    #: Emitted after a theme switch with the count of annotations that
    #: look hard to see against the new canvas. MainWindow owns the
    #: actual "fix them?" prompt (QMessageBox), same view-emits/
    #: MainWindow-shows split the rest of this app uses.
    annotations_may_be_invisible = Signal(int)
    #: Right-click "Properties" on a block/node/event -- selection (and
    #: so the Inspector's own content) already happened before the
    #: context menu was shown; this just tells MainWindow to raise/show
    #: the dock itself.
    properties_requested = Signal()
    #: Emitted after ANY selection change, with no payload -- the one
    #: hook for UI whose enabled state depends only on "what is
    #: selected" rather than on which element it is. The z-order ribbon
    #: buttons and menu items use it to enable/disable themselves
    #: (there is nothing to bring to front without a selected
    #: annotation, and nothing to bring forward once the selection is
    #: already at the front). The per-kind signals above stay for
    #: anything that needs to know *which* element.
    selection_changed = Signal()
    #: (mode) -- the armed build tool CHANGED, from either side: the user
    #: clicking a ribbon tool, or the view disarming itself (Esc, or the
    #: end of a one-shot placement). MainWindow unchecks the ribbon
    #: button when the view does the latter -- without it the button
    #: stayed lit and the next click placed another shape.
    tool_mode_changed = Signal(str)
    #: (kind, props | None) -- the saved "style for new <Kind>s" changed.
    #: `None` means the default was cleared. MainWindow persists it (it
    #: owns QSettings); the view only holds and applies it.
    annotation_style_default_changed = Signal(str, object)

    def __init__(self, parent=None):
        self._scene = QGraphicsScene()
        super().__init__(self._scene, parent)
        # This canvas's grid-snap step, read by its items through their
        # scene. It was one module-wide dict, so two open windows would
        # have snapped to each other's grid.
        self._snap_state = {"x": 0.0, "y": 0.0}
        self._scene._snap_state = self._snap_state
        # The view owns its scene. Left parentless, the scene is freed by
        # Python whenever its wrapper goes, which the cycle collector may
        # do after the view's C++ side is gone; ~QGraphicsScene then calls
        # setScene() on the dead view and crashes. As a child, the scene is
        # deleted by ~QObject after ~QGraphicsView has detached from it.
        self._scene.setParent(self)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Rubber-band drag = click-drag on empty canvas multi-selects.
        # Panning stays on the scrollbars and Ctrl+wheel; elements get a
        # pointing-hand cursor on hover.
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setBackgroundBrush(QBrush(QColor(COLOUR["canvas"])))
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._user_zoomed = False
        #: Current semantic-zoom state: None until first applied, then
        #: whether element text is shown at the present view scale.
        self._lod_labels_visible: bool | None = None
        self._lod_mode: str | None = None
        #: Which phase the canvas labels describe: -1 = the model's own
        #: (unphased) law, otherwise a zero-based PHASES index. View-only;
        #: set by the View ribbon's Show Phase combo.
        self._label_phase = -1
        #: Units for the label numbers, read once when a model loads.
        self._label_units = ""
        #: Viewport point where the current rubber band started, or None
        #: when no band gesture is in progress. Its direction (see
        #: `_band_mode`) decides enclosed vs crossing selection.
        self._band_anchor = None
        #: Semantic zoom master switch; renders/exports turn it off so an
        #: image is never exported with its labels hidden.
        self._lod_enabled = True
        self._pan_anchor: QPointF | None = None

        self._block_items: dict[int, _BlockItem] = {}
        self._node_items: dict[int, _NodeItem] = {}
        self._links: list[_LinkItem] = []
        self._moved_pending = False
        self._moved_settle = QTimer(self)
        self._moved_settle.setSingleShot(True)
        self._moved_settle.setInterval(150)
        self._moved_settle.timeout.connect(self._on_moved_settled)
        self._loading = False
        #: Dependency-marker dashed connector, shown only while hovering
        #: a badge whose target is on this level -- created/removed by
        #: `_on_dep_hover`, never persisted.
        self._dep_hover_line: QGraphicsLineItem | None = None
        #: (kind, id) -> name across the whole model, all hierarchy
        #: levels -- rebuilt in `_rebuild_scene` so a dependency badge's
        #: target name resolves even when the target lives on a
        #: different level than the one currently on screen (a raw-row
        #: scan across the whole file, same pattern `locate_element`
        #: uses -- cached per rebuild
        #: rather than per badge since a level can have many blocks).
        self._global_name_index: dict[tuple[str, int], str] = {}
        #: Block Defaults -- MainWindow seeds these from
        #: QSettings; add_block applies them to every new block. Factory
        #: defaults per Raptor 7 text: Exponential failure
        #: (community-standard choice), Lognormal repair, infinite
        #: spares ("It will have infinite spares, no logistics delays,
        #: and no advanced features are activated").
        self.default_block_fdist = 4
        self.default_block_fparams = [100.0, 0.0, 0.0]
        self.default_block_rdist = 7
        self.default_block_rparams = [10.0, 2.0, 0.0]
        self.default_block_sparing = "Infinite"
        # Node ids whose `good_paths` was explicitly edited via the
        # Inspector -- see `write_back_to_rbd`'s own comment. The file's
        # own `k=-1` sentinel (coerced to good_paths=1 at load) also
        # turns up on ordinary type_node==2 nodes, so type alone can't
        # tell safe-to-write from must-preserve. Only a node the user
        # actually touched is safe to overwrite; every other node's "k"
        # column is left byte-identical to whatever it loaded as.
        #: Which node fields the user edited (good paths, flow flags,
        #: dependency master, report analysis, standby) -- only those are
        #: written back. Also (`_edited_flow_flags`, ...) below.
        self._edited = EditedFields()
        #: The model-editing half of this class, which has no Qt in it.
        self._editor = ModelEditor(self)
        self._rbd_ref: RbdFile | None = None
        #: `{hier id: boundary LINK_DATA ids}` for the live view. Built
        #: once per model by `_hier_boundary_links`; cleared on load and
        #: on any real model edit (see `_invalidate_live_caches`). Before,
        #: every frame rescanned all LINK_DATA rows once per hierarchy.
        self._hier_boundary_cache: dict[int, tuple[int, ...]] | None = None
        #: LINK_DATA file ids in row order, for `_engine_links_by_file_id`
        #: (the engine numbers links 1..N). Same lifetime as the map above.
        self._link_file_ids_cache: list[int] | None = None
        self.model_changed.connect(self._invalidate_live_caches)
        #: Canvas tool mode: "select" | "add_block" | "add_node" | "add_event" | "connect".
        self._tool_mode = "select"
        self._delete_armed = False
        #: "fail" | "repair" | None -- Failure Effects click-to-target.
        #: Mirrors `_delete_armed`'s own arm/cursor/click pattern.
        self._fev_armed_mode: str | None = None
        #: Failure Effects View is open: a plain click on a block or event acts.
        self._fev_view_open = False
        self._anno_items: dict[int, Any] = {}
        self._anno_seq = 1
        #: kind -> prop dict, the user's saved "style for new <Kind>s"
        #: (set from an existing annotation's context menu). Seeded from
        #: QSettings by MainWindow; empty means "factory look".
        self.default_annotation_props: dict[str, dict] = {}
        self._paste_buffer: list = []
        self._connect_ghost: QGraphicsLineItem | None = None
        self._anno_drag_start: QPointF | None = None
        self._connect_from: tuple[str, int] | None = None
        #: two-click arrow placement (tail click, then head click) --
        #: mirrors _connect_from/_connect_ghost's arm/preview/drop
        #: pattern.
        self._arrow_from: QPointF | None = None
        self._arrow_ghost: QGraphicsLineItem | None = None
        #: two-click rect/ellipse placement -- same gesture as the arrow
        #: tool above, replacing a single click that dropped a fixed
        #: default size centred on the cursor.
        self._shape_anno_from: QPointF | None = None
        self._shape_anno_ghost = None
        # The view owns the undo stack -- every mutation entry point
        # funnels through a public method that pushes a command, so that is
        # where an edit Raptor 7 would refuse is refused (`rule_gate`).
        from raptor.gui.ui.rule_gate import RuleGatedStack
        self._stack: Any = RuleGatedStack(self, QUndoStack(self))
        self._drag_start: list = []
        #: Position overrides, keyed (kind, id) in model units.
        #: The typed blocks/nodes/events dicts are level-scoped (rebuilt
        #: from rows on every enter/leave), so they cannot carry a moved
        #: element's position across a level change -- the element's
        #: typed object simply doesn't exist while its level is hidden.
        #: This map does: MoveCmd/_place records every committed move
        #: here (undo/redo re-record the old value), sync_model_
        #: positions records bulk moves, and _rebuild_scene overlays an
        #: override over the parsed row -- so traversal never reverts a
        #: drag, and the file rows are only ever written by
        #: write_back_to_rbd (Save/print/autosave), never by merely
        #: changing view. Cleared in load_rbd (belongs to one file).
        self._pos_overrides: dict[tuple[str, int], tuple[float, float]] = {}
        #: Viewport-pixel anchor recorded at press, and whether the
        #: drag has moved far enough past it to count as a real drag
        #: yet -- see `mouseMoveEvent`'s own comment for why.
        self._press_viewport_pos: QPointF | None = None
        self._drag_armed = False

        # Live-theme the canvas: without this hook the background brush and
        # every item kept whichever theme was active when the file loaded.
        ThemeManager.instance().on_changed(self._retheme_current)

        self._origin_x: float = 0
        self._origin_y: float = 0
        self._flip_base: float = 0.0
        self._scale: float = 30
        #: The same transform as a value object; `_tx`/`_ty` and
        #: `scene_to_model` delegate to it so the arithmetic has one owner
        #: (see `raptor.core.fileio.view_transform`).
        self._vt = view_transform.ViewTransform(
            scale=30.0, origin_x=0.0, flip_base=0.0, dx_min=0.0, dy_min=0.0)

        # Live model objects
        self.blocks: dict[int, RBDBlock] = {}
        self.nodes: dict[int, RBDNode] = {}
        self.links: list[Link] = []
        self.events: dict[int, Any] = {}
        self._event_items: dict[int, _EventItem] = {}
        # Hierarchies: all parsed hiers + the ovals for the
        # currently-displayed level. `_current_parent` is the hier id whose
        # level we're viewing (0 = home); the canvas only shows elements
        # whose ParentId matches.
        self.hiers: dict[int, RBDHier] = {}
        self._hier_items: dict[int, _HierItem] = {}
        self._current_parent = 0

    def set_undo_stack(self, stack: QUndoStack):
        """Adopt the application-level stack (MainWindow creates it
        before this view exists, for ribbon action wiring), behind the
        Raptor 7 input-rule gate."""
        from raptor.gui.ui.rule_gate import RuleGatedStack
        self._stack = RuleGatedStack(self, stack)

    def _remove_block(self, oid: int):
        self._remove_ids({oid}, set(), set(), set())

    def _remove_node(self, oid: int):
        self._remove_ids(set(), {oid}, set(), set())

    def _remove_event(self, oid: int):
        self._remove_ids(set(), set(), {oid}, set())

    def _remove_link(self, num: int):
        self._remove_ids(set(), set(), set(), {num})

    def retheme(self):
        c = COLOUR
        self.setBackgroundBrush(QBrush(QColor(c["canvas"])))
        for item in self._scene.items():
            if isinstance(item, (_BlockItem, _NodeItem, _EventItem,
                                 _HierItem, _LinkItem)):
                item.retheme(c)
            elif isinstance(item, _GridBackground):
                item.setPen(QPen(QColor(c["border"]), 0.5))
            else:
                continue
        self.update()

    def _retheme_current(self, _dark: bool):
        """ThemeManager callback (weakref-registered)."""
        self.retheme()
        # `retheme` resets every item to its themed look, which wipes the
        # live/result overlays on the canvas -- switching to the dark
        # theme used to lose the run's results outright. Put them back,
        # and the live colours too so a flip mid-run does not freeze the
        # animation at the pre-flip colours.
        # Live colours first, result overlay on top: a finished run leaves its end-of-run state cached, and painting
        # that over the weak-link bands turned every block red after a theme flip.
        self._reapply_live_states()
        self._reapply_result_visuals()
        low = self.find_low_contrast_annotations()
        if low:
            self.annotations_may_be_invisible.emit(len(low))

    def find_low_contrast_annotations(self) -> list[int]:
        """Ids of every annotation, across all hierarchy levels (not
        just the one on screen -- `self.annotations` is the whole-model
        list, `_anno_items` is level-scoped), that reads as hard to see
        against the theme active right now."""
        return [int(a.get("id", 0)) for a in getattr(self, "annotations", [])
               if annotation_is_low_contrast(a)]

    def fix_annotation_contrast(self, ids: list[int]) -> None:
        """Repoint each flagged annotation's text/border colour at the
        current theme's own high-contrast tokens (`COLOUR["text"]`/
        `["muted"]` -- the same pair `_anno_base_props` already uses
        for a brand-new annotation, so a fixed-up old one and a fresh
        one end up looking consistent). Fill colour is left alone --
        only text/border are ever the cause of an invisibility flag on
        their own (a low-contrast fill only fires the check via its
        own comparison against canvas, and repainting a user's chosen
        fill is a bigger, more surprising change than nudging outline/
        text colour). Routes a current-level id through the normal
        undoable `set_annotation_props` (matches every other property
        edit); an other-level id has no live item to command through,
        so it's patched directly in `self.annotations` and the model
        marked dirty the same way any other direct-data edit here
        does."""
        if not ids:
            return
        id_set = set(ids)
        fixed_other_level = False
        for a in self.annotations:
            if int(a.get("id", 0)) not in id_set:
                continue
            aid = int(a.get("id", 0))
            changes = {"text_colour": COLOUR["text"],
                      "border_colour": COLOUR["muted"]}
            if aid in self._anno_items:
                self.set_annotation_props(aid, changes)
            else:
                a.update(changes)
                fixed_other_level = True
        if fixed_other_level:
            self.model_changed.emit()

    def _on_selection_changed(self):
        try:
            items = self._scene.selectedItems()
        except RuntimeError:
            # The C++ scene can be destroyed before this Python handler
            # disconnects (widget teardown order) -- Qt then still emits
            # selection changes on dead items. Non-fatal; ignore.
            return
        if not items:
            self.selection_cleared.emit()
            self.selection_changed.emit()
            return
        item = items[0]
        if isinstance(item, _BlockItem):
            self.block_selected.emit(item._block_id, "")
        elif isinstance(item, _NodeItem):
            self.node_selected.emit(item._node_id, "")
        elif isinstance(item, _EventItem):
            self.event_selected.emit(item._event_id)
        elif isinstance(item, _HierItem):
            self.hier_selected.emit(item._hier_id)
        elif isinstance(item, tuple(_ANNO_TYPES)):
            aid = getattr(item, "_anno_id", None)
            if aid is not None:
                self.annotation_selected.emit(aid)
        # Last, so every kind-specific handler above has already run and
        # an enable-state refresh sees the finished selection.
        self.selection_changed.emit()

    def clear_model(self):
        self._scene.clear()
        self._block_items.clear()
        self._node_items.clear()
        self._event_items.clear()
        self._hier_items.clear()
        self._links.clear()
        self.blocks.clear()
        self.nodes.clear()
        self.events.clear()
        # Note: self.hiers is not cleared -- the tree describes every
        # level; only the canvas is level-filtered.
        self.links.clear()

    def _tx(self, x: float) -> float:
        return self._vt.x_to_scene(x)

    def _ty(self, y: float) -> float:
        # Y is flipped relative to the file: the Raptor canvas has y
        # increasing upward, while Qt's scene axis is down-positive.
        return self._vt.y_to_scene(y)

    def _item_moved(self, obj_id: int, item=None):
        # Re-route only the links this move can change. It used to re-route
        # every link in the diagram on every drag step, each one scanning
        # the whole scene.
        for link in (links_affected_by_moving(item) if item is not None
                     else self._links):
            link._update_route()
        if self._loading:
            return
        # Leading edge + trailing edge. `layout_changed` deliberately
        # skips the whole-model path `model_changed` takes (validation,
        # Inspector/Explorer/Data Tables rebuild, results re-hash): a
        # position is not an engine input, and doing that work per drag
        # step froze the window for seconds on a large model. The first
        # step of a gesture notifies at once, so the file is marked dirty
        # exactly as before; the rest of the gesture is coalesced into one
        # more notification when the motion pauses.
        if self._moved_settle.isActive():
            self._moved_pending = True
            self._moved_settle.start()
        else:
            self.layout_changed.emit()
            self._moved_settle.start()

    def flush_pending_moves(self) -> None:
        """Send now the notification a move gesture still owes. Called
        before a run takes the model: left to its timer, it arrived DURING
        the run and read as an edit -- F5 straight after a drag or Auto
        Layout stopped the run with "Simulation in progress"."""
        if self._moved_settle.isActive():
            self._moved_settle.stop()
            self._on_moved_settled()

    def _on_moved_settled(self):
        """Motion paused: send the notification the coalesced steps owe."""
        # The rows take the new positions once the motion settles (not on
        # every mouse step); they lagged until the next full write-back.
        self.sync_model_positions()
        if self._moved_pending:
            self._moved_pending = False
            self.layout_changed.emit()

    #: Legacy files lay elements out on a fixed grid in model units;
    #: rendering at a fixed scale with a model-wide origin means items
    # appear exactly where the file puts them -- identical on every
    # open, and consistent across level switches (no per-level fit).
    LEGACY_SCALE = view_transform.LEGACY_SCALE

    def _compute_model_transform(self, rbd: RbdFile):
        """Whole-model origin/flip/scale, computed once per load and then
        frozen on the file (see `raptor.core.fileio.view_transform`)."""
        vt = view_transform.ensure(rbd)
        self._vt = vt
        self._origin_x = vt.origin_x
        self._flip_base = vt.flip_base
        self._model_dx_min = vt.dx_min
        self._model_dy_min = vt.dy_min
        self._scale = vt.scale

    def load_rbd(self, rbd: RbdFile):
        log.debug("load_rbd: start (%s)", getattr(rbd, "path", "?"))
        self._loading = True
        self._compute_model_transform(rbd)
        # annotations belong to this model (.rbds only)
        # Alias the model's list so appends/edits from the canvas
        # land on the RbdFile that Save serialises
        if not isinstance(getattr(rbd, "annotations", None), list):
            rbd.annotations = []
        self.annotations = rbd.annotations
        used = {int(a.get("id", 0)) for a in self.annotations}
        self._anno_seq = (max(used) + 1) if used else 1
        self._rbd_ref = rbd   # kept so element creation can clone real rows
        # Units for the canvas labels, read once: the per-block label
        # formatter must not re-parse SYSTEM_DATA on every refresh.
        self._label_units = ""
        try:
            from raptor.core.fileio.typed import system_config_from_row
            self._label_units = system_config_from_row(
                rbd.sections["SYSTEM_DATA"].rows[0]).units
        except (ValueError, KeyError, IndexError, AttributeError):
            pass
        # A phase choice belongs to the previous model.
        self._label_phase = -1
        self._stack.clear()   # undo history belongs to the previous file
        # result visuals belong to the previous model too (a new run
        # re-populates them); traversal within a model keeps them
        self._last_result_visuals = None
        self._last_hier_dsi: dict | None = None
        self._live_state_cache = None
        self._hier_boundary_cache = None
        self._link_file_ids_cache = None
        self._user_arranged = False
        # Parse all hierarchy rows once (they describe the whole tree);
        # the canvas then shows one level at a time, filtered by ParentId
        # (Raptor 7: hierarchies hide their children below the home
        # level; double-click descends, Up/Home ascend).
        # The position-override map must die with the file: it is the
        # traversal source of truth for moved elements, and the previous
        # file's entries (same numeric ids are routine -- every model
        # starts its ids at 1) would teleport the new model's elements.
        self._pos_overrides = {}
        self.hiers = {}
        if "HIERARCHY_DATA" in rbd.sections:
            for row in rbd.sections["HIERARCHY_DATA"].rows:
                try:
                    h = hier_from_row(row)
                except (ValueError, KeyError):
                    continue
                self.hiers[h.id] = h
        self._current_parent = 0
        log.debug("load_rbd: %d hierarchies parsed, entering _rebuild_scene",
                  len(self.hiers))
        self._rebuild_scene()
        self.level_changed.emit(0)
        log.debug("load_rbd: done")

    def _current_depth(self) -> int:
        """How many hierarchy levels deep `self._current_parent` is,
        walked fresh from the model's own `parent_id` chain each time --
        used only for `enter_level`'s "max 5 levels of indenturing" cap
        (manual). Computed fresh rather than from a maintained
        counter/stack, so it cannot desync from direct level jumps such
        as `locate_element`'s.
        """
        depth = 0
        seen: set[int] = set()
        hid = self._current_parent
        while hid and hid in self.hiers and hid not in seen:
            seen.add(hid)  # guard against a corrupt cyclic parent chain
            depth += 1
            hid = self.hiers[hid].parent_id
        return depth

    def enter_level(self, hier_id: int):
        """Descend into a hierarchy's child level (double-click oval)."""
        if hier_id not in self.hiers or hier_id == self._current_parent:
            return
        if self._current_depth() >= 5:
            return  # manual: max five levels of indenturing
        self._set_level(hier_id)

    def go_up(self):
        """Ascend one level (Up button / double-click the Out marker)."""
        if not self._current_parent:
            return
        parent = self.hiers[self._current_parent].parent_id \
            if self._current_parent in self.hiers else 0
        self._set_level(parent)

    def go_home(self):
        """Jump straight back to the home level."""
        self._set_level(0)

    def _set_level(self, parent_id: int):
        self._loading = True
        self._current_parent = parent_id
        self._rebuild_scene()
        self._loading = False
        # Live element colours survive traversal: a run in progress
        # re-paints the freshly built items from the cached live state
        # immediately, so entering a hierarchy mid-run shows its
        # elements' current colours (not defaults) and going back to the
        # parent keeps them.
        self._reapply_live_states()
        # Result visuals survive traversal too: re-apply weak bands, DSI
        # dots, and the capacity/cost/spares overlays to the freshly
        # built items, on top of the live colours.
        self._reapply_result_visuals()
        # The minimap's fit target belonged to the old level; without a
        # reset it kept the old box shape and clipped/emptied the new
        # level until a window resize.
        minimap = getattr(self, "_minimap", None)
        if minimap is not None:
            minimap._fit_target = None
            minimap._last_fitted_rect = None
            minimap._refresh()
        self.level_changed.emit(parent_id)

    _LOCATE = {"block": ("BLOCK_DATA", "ParentId", "_block_items"),
               "node": ("NODE_DATA", "parent", "_node_items"),
               "event": ("EVENT_DATA", "ParentId", "_event_items"),
               "hier": ("HIERARCHY_DATA", "Parent", "_hier_items")}

    def locate_id(self, kind: str, oid: int) -> bool:
        """Select the element `kind` `oid` and centre on it, going to the
        hierarchy level it lives on. By id, so a renamed element or two
        with one name are found exactly."""
        rbd = self._rbd_ref
        spec = self._LOCATE.get(kind)
        if rbd is None or spec is None or oid == 0:
            return False
        section, parent_col, items_attr = spec
        row = next((r for r in getattr(rbd.sections.get(section), "rows", [])
                    if str(r.get("ID")) == str(oid)), None)
        if row is None:
            return False
        try:
            parent = int(float(row.get(parent_col, 0) or 0))
        except ValueError:
            parent = 0
        if parent != self._current_parent:
            self._set_level(parent)
        target = getattr(self, items_attr).get(oid)
        if target is None:
            return False
        for it in list(self._scene.selectedItems()):
            it.setSelected(False)
        target.setSelected(True)
        self.centerOn(target)
        return True

    def locate_link(self, link_num: int) -> bool:
        """Select connector `link_num` and centre on it.

        Links are not in the block/node/event/hier collections `locate_id`
        searches -- they are `_LinkItem` objects rebuilt from the link rows
        on the current level -- so an Issues entry about a link could not
        select anything. Returns False when the connector is not on the
        level currently shown, which the caller treats as "located the
        element instead".
        """
        try:
            wanted = int(link_num)
        except (TypeError, ValueError):
            return False
        for item in self._links:
            if int(getattr(item, "_link_num", -1)) != wanted:
                continue
            for other in list(self._scene.selectedItems()):
                other.setSelected(False)
            item.setSelected(True)
            self.centerOn(item.boundingRect().center())
            return True
        return False

    def locate_element(self, kind: str, name: str) -> bool:
        """Select `name` (a block or node) and centre the view on it,
        navigating into whatever hierarchy level it actually lives on
        first if that's not the level currently shown -- `self.blocks`/
        `self.nodes` are level-scoped (`_rebuild_scene` filters by
        `ParentId == self._current_parent`), so a name from another
        level won't be in `_block_items`/`_node_items` yet. Searches the
        raw model rows (not the level-scoped dicts) so it finds the
        element regardless of which level is currently on screen.
        Returns whether an element was found."""
        rbd = self._rbd_ref
        if rbd is None:
            return False
        section = "BLOCK_DATA" if kind == "block" else "NODE_DATA"
        if section not in rbd.sections:
            return False
        parent_id = None
        oid = None
        for row in rbd.sections[section].rows:
            obj: Any
            try:
                obj = (block_from_row(row) if kind == "block"
                       else node_from_row(row))
            except (ValueError, KeyError):
                continue
            # BLOCK_DATA's ID-0 row is the file's column template, not an
            # element, and it is called "unnamed" -- the same as a new
            # model's block. Matching it first made every click on that
            # block in the event log report "Couldn't find block".
            if obj.id == 0:
                continue
            obj_name = obj.name if kind == "block" else obj.node_name
            if obj_name == name:
                parent_id = obj.parent_id
                oid = obj.id
                break
        if oid is None:
            return False
        if parent_id != self._current_parent:
            self._set_level(parent_id or 0)
        items = self._block_items if kind == "block" else self._node_items
        target = items.get(oid)
        if target is None:
            return False
        for it in list(self._scene.selectedItems()):
            it.setSelected(False)
        target.setSelected(True)
        self.centerOn(target)
        return True


    #: Master-selector dependency_num -> (badge colour token, glyph,
    #: label). Shared by `_dependency_visual` for the two "virtual
    #: target" cases (there's no single other element to point a line
    #: at for either -- Locally follows the block/node's own node,
    #: System follows the whole model's up/down state).
    _DEP_VIRTUAL = {
        -1: ("operating", "L", "Locally dependent",
            "follows its own node's up/down status"),
        -2: ("relationship", "S", "System dependent",
            "follows the overall system's up/down status"),
    }
    #: dep_type field -> (element kind, section, raw-row parser, name-getter)
    _DEP_TARGET_KINDS = {
        "RBDBlock": ("block", "BLOCK_DATA", block_from_row, lambda o: o.name),
        "RBDNode": ("node", "NODE_DATA", node_from_row, lambda o: o.node_name),
    }

    def _build_global_name_index(self) -> dict[tuple[str, int], str]:
        """(kind, id) -> name across the whole model. Same raw-row scan
        `locate_element` already uses, cached here so
        `_apply_dependency_badges` (one call per level's worth of
        blocks/nodes) doesn't re-scan the whole file per item."""
        rbd = self._rbd_ref
        index: dict[tuple[str, int], str] = {}
        if rbd is None:
            return index
        from raptor.core.fileio.typed import event_from_row
        sources = dict(self._DEP_TARGET_KINDS)
        sources["RBDEvent"] = ("event", "EVENT_DATA", event_from_row,
                               lambda o: o.name)
        for kind, section, from_row, name_of in sources.values():
            if section not in rbd.sections:
                continue
            for row in rbd.sections[section].rows:
                try:
                    obj = from_row(row)
                except (ValueError, KeyError):
                    continue
                index[(kind, obj.id)] = name_of(obj)
        return index

    def _dependency_visual(self, dependency_num: int, dep_type: str) -> dict | None:
        """Badge descriptor for a block/node's own master-element
        dependency fields (`dependency_num`/`dep_type` -- see
        `_dependency_group`'s docstring in inspector.py for the shared
        0/-1/-2/>0 encoding). Returns None for independent (0).
        `target_item` is the same-level canvas item to draw an on-hover
        dashed connector to (`_on_dep_hover`), or None when the target
        isn't on the level currently displayed -- `target_kind`/
        `target_name` are still filled in that case so a badge click
        can still navigate there via `locate_element`."""
        if not dependency_num:
            return None
        c = COLOUR
        virtual = self._DEP_VIRTUAL.get(dependency_num)
        if virtual is not None:
            colour_key, glyph, label, detail = virtual
            return {"colour": c[colour_key], "glyph": glyph,
                   "tooltip": f"{label} -- {detail}",
                   "target_kind": None, "target_name": None,
                   "target_item": None}
        spec = self._DEP_TARGET_KINDS.get(dep_type)
        if spec is None or dependency_num <= 0:
            return None
        kind = spec[0]
        name = self._global_name_index.get((kind, dependency_num))
        label = name or f"{kind} #{dependency_num}"
        same_level_items = (self._block_items if kind == "block"
                            else self._node_items)
        target_item = same_level_items.get(dependency_num)
        tooltip = f"Dependent on {kind} '{label}'"
        if target_item is None:
            tooltip += " (on a different hierarchy level -- click to locate it)"
        else:
            tooltip += " -- click to jump to it, hover to see the link"
        return {"colour": c["accent"], "glyph": "D", "tooltip": tooltip,
               "target_kind": kind, "target_name": name,
               "target_item": target_item}

    def _apply_dependency_badges(self):
        """(Re)apply every block/node's dependency badge for the
        currently displayed level -- `self.blocks`/`self.nodes` are
        level-scoped, `self._global_name_index` is whole-model (built
        just before this is called)."""
        for bid, block in self.blocks.items():
            item: Any
            item = self._block_items.get(bid)
            if item is not None:
                item.set_dependency_badge(
                    self._dependency_visual(block.dependency_num, block.dep_type))
        for nid, node in self.nodes.items():
            item = self._node_items.get(nid)
            if item is not None:
                item.set_dependency_badge(
                    self._dependency_visual(node.dependency_num, node.dep_type))

    def _on_dep_hover(self, item, entering: bool):
        """Hover callback wired into every `_BlockItem`/`_NodeItem`:
        draws a dashed connector to the badge's same-level target while
        the item is hovered, removed on leave (and unconditionally
        recreated on the next hover, so a stray line never survives a
        rebuild/selection change)."""
        if self._dep_hover_line is not None:
            self._scene.removeItem(self._dep_hover_line)
            self._dep_hover_line = None
        if not entering:
            return
        badge: Any = getattr(item, "_dep_badge", None)
        target = badge.get("target_item") if badge else None
        if target is None:
            return
        # True centres (see _LinkItem._anchor_geom's docstring) -- a
        # plain sceneBoundingRect().center() is skewed off a node's
        # real centre by its capacity-gauge/dependency-badge padding.
        line = QGraphicsLineItem(QLineF(_LinkItem._anchor_geom(item)[1],
                                        _LinkItem._anchor_geom(target)[1]))
        pen = QPen(QColor(badge["colour"]), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        line.setPen(pen)
        line.setZValue(-1)
        line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(line)
        self._dep_hover_line = line

    def _on_dep_click(self, item):
        """Navigate to a dependency's target even when it's on a
        different hierarchy level (`locate_element` already handles
        that), covering the cross-level case a same-scene dashed line
        can't reach.

        Only reachable via the right-click "Focus on Dependency"
        context-menu entry (`_show_context_menu`)."""
        badge = getattr(item, "_dep_badge", None)
        if not badge or badge.get("target_kind") is None or \
                not badge.get("target_name"):
            return
        self.locate_element(badge["target_kind"], badge["target_name"])

    def _resolve_link_endpoint(self, ref: str, oid: int):
        """The correct canvas item for one of a link's endpoints --
        `ref` is `connect_f_ref`/`connect_t_ref` ("RBDBlock"/"Block",
        "RBDNode"/"Node", etc; both the raw file token and link_from_row's
        RBD-prefixed form are accepted).

        Block/Node/Event/Hier ids are each their own independent per-kind
        counter in the file format (a Block and a Node routinely share
        the same bare id), so the dicts must be kept separate: merging
        them keyed by bare id would resolve a colliding id to whichever
        dict was merged last, wiring a link to the wrong element."""
        bare = ref.replace("RBD", "", 1) if ref else ""
        if bare == "Block":
            return self._block_items.get(oid)
        if bare == "Event":
            return self._event_items.get(oid)
        if bare == "Hier":
            return self._hier_items.get(oid)
        return self._node_items.get(oid)   # "Node" and any other/blank ref

    def _rebuild_scene(self):
        """(Re)build every canvas item for `self._current_parent`."""
        self._loading = True
        # Disable drag-snap for the duration of this bulk placement: item
        # construction below calls setPos() for every element, which
        # fires itemChange() same as an interactive drag -- a stale
        # self._snap_state left over from whatever was open/laid out before
        # (a previous file, or a previous hierarchy level with different
        # spacing) would re-snap freshly-placed items onto an unrelated
        # grid instead of exactly where their file row says. This
        # function's own _apply_scene_layout() call at the end recomputes
        # the correct values from this level's actual layout.
        self._snap_state["x"] = 0.0
        self._snap_state["y"] = 0.0
        rbd = self._rbd_ref
        # Position overlay: `_pos_overrides` holds the current position
        # of every element moved since load (MoveCmd/_place on
        # drag/nudge/undo, sync_model_positions after bulk moves), in
        # model units, across all levels. The file rows are only written
        # by write_back_to_rbd (Save/print/autosave) -- so the re-parse
        # below overlays the override over each freshly parsed row, or
        # leaving a level would re-parse stale xPos/yPos and every moved
        # element would snap back. Unmoved elements read their row
        # verbatim. The typed dicts are level-scoped, so a moved
        # element's typed object is gone while its level is hidden.
        # load_rbd clears the map (belongs to one file).
        prev_pos = self._pos_overrides
        self.clear_model()

        cur = self._current_parent
        log.debug("_rebuild_scene: level=%s", cur)
        # Stable transform: origin/scale/flip come from the whole model
        # (computed once in load_rbd), so every element renders exactly
        # where its file row says -- same on every open, and identical
        # across hierarchy enter/leave. No per-level fit reshuffling.

        def on_level(parent: int) -> bool:
            return parent == cur

        visible_blocks: dict[int, RBDBlock] = {}
        if "BLOCK_DATA" in req(rbd).sections:
            for row in req(rbd).sections["BLOCK_DATA"].rows:
                try:
                    block = block_from_row(row)
                except (ValueError, KeyError):
                    continue
                if block.id == 0 or not on_level(block.parent_id):
                    continue
                if ("block", block.id) in prev_pos:
                    block.x_position, block.y_position = \
                        prev_pos[("block", block.id)]
                visible_blocks[block.id] = block
        self.blocks.update(visible_blocks)
        log.debug("_rebuild_scene: building %d blocks", len(visible_blocks))
        for bid, block in visible_blocks.items():
            log.debug("_rebuild_scene: block id=%s name=%r pos=(%s,%s)",
                      bid, block.name, block.x_position, block.y_position)
            item: Any
            item = _BlockItem(bid, block.name,
                              self._tx(block.x_position), self._ty(block.y_position),
                              on_moved=self._item_moved,
                              on_dep_hover=self._on_dep_hover)
            self._apply_block_labels(item, block)
            item.set_phased(block.uses_phasing)
            self._scene.addItem(item)
            self._block_items[bid] = item

        if "NODE_DATA" in req(rbd).sections:
            rows = req(rbd).sections["NODE_DATA"].rows
            log.debug("_rebuild_scene: scanning %d node rows", len(rows))
            for row in rows:
                try:
                    node = node_from_row(row)
                except (ValueError, KeyError):
                    continue
                if not on_level(node.parent_id):
                    continue
                if ("node", node.id) in prev_pos:
                    node.x_position, node.y_position = \
                        prev_pos[("node", node.id)]
                self.nodes[node.id] = node
                log.debug("_rebuild_scene: node id=%s name=%r type=%s "
                          "pos=(%s,%s)", node.id, node.node_name,
                          node.type_node, node.x_position, node.y_position)
                item = _NodeItem(node.id, node.node_name, node.type_node,
                                 self._tx(node.x_position), self._ty(node.y_position),
                                 on_moved=self._item_moved,
                                 on_dep_hover=self._on_dep_hover)
                item.set_phased(node.uses_phasing)
                self._scene.addItem(item)
                self._node_items[node.id] = item

        # Dependency badges need both dicts fully populated (a block's
        # badge may point at a node target and vice versa), so this runs
        # once after both loops above, not inline in either.
        self._global_name_index = self._build_global_name_index()
        self._apply_dependency_badges()

        if "EVENT_DATA" in req(rbd).sections:
            from raptor.core.fileio.typed import event_from_row
            rows = req(rbd).sections["EVENT_DATA"].rows
            log.debug("_rebuild_scene: scanning %d event rows", len(rows))
            for row in rows:
                try:
                    event = event_from_row(row)
                except (ValueError, KeyError):
                    continue
                if not on_level(event.parent_id):
                    continue
                if ("event", event.id) in prev_pos:
                    event.x_position, event.y_position = \
                        prev_pos[("event", event.id)]
                self.events[event.id] = event
                log.debug("_rebuild_scene: event id=%s name=%r "
                          "pos=(%s,%s)", event.id, event.name,
                          event.x_position, event.y_position)
                item = _EventItem(
                    event.id, event.name,
                    self._tx(event.x_position),
                    self._ty(event.y_position),
                    on_moved=self._item_moved,
                    probability=(event.fail_mean
                                 if event.fail_distro == 17 else None))
                item.set_phased(event.uses_phasing)
                self._scene.addItem(item)
                self._event_items[event.id] = item

        # Hierarchy ovals living on this level (their children are
        # hidden). First re-parse the whole tree from the file --
        # collapse / import / undo may have added, renamed or removed
        # rows since the last build.
        if "HIERARCHY_DATA" in req(rbd).sections:
            from raptor.core.fileio.typed import hier_from_row
            fresh_hiers = {}
            for row in req(rbd).sections["HIERARCHY_DATA"].rows:
                try:
                    h = hier_from_row(row)
                except (ValueError, KeyError):
                    continue
                if ("hier", h.id) in prev_pos:
                    h.x_position, h.y_position = prev_pos[("hier", h.id)]
                fresh_hiers[h.id] = h
            self.hiers.clear()
            self.hiers.update(fresh_hiers)
        log.debug("_rebuild_scene: %d hierarchies total, filtering to level",
                  len(self.hiers))
        # Prune overrides for ids no longer in the model (undo of an
        # add, delete, ...) -- ids are recycled (_next_id scans rows),
        # and a stale override on a reused id would teleport the new
        # element to the old one's last position.
        live_ids = {
            ("block", _safe_int(r.get("ID")))
            for r in req(rbd).sections.get("BLOCK_DATA", RbdSection(
                "BLOCK_DATA", [], [])).rows} | {
            ("node", _safe_int(r.get("ID")))
            for r in req(rbd).sections.get("NODE_DATA", RbdSection("", [], [])).rows} | {
            ("event", _safe_int(r.get("ID")))
            for r in req(rbd).sections.get("EVENT_DATA", RbdSection("", [], [])).rows} | {
            ("hier", _safe_int(r.get("ID")))
            for r in req(rbd).sections.get("HIERARCHY_DATA", RbdSection("", [], [])).rows}
        self._pos_overrides = {k: v for k, v in prev_pos.items()
                               if k in live_ids}
        for hid, h in list(self.hiers.items()):
            if h.parent_id != cur:
                continue
            log.debug("_rebuild_scene: hier id=%s name=%r pos=(%s,%s)",
                      hid, h.name, h.x_position, h.y_position)
            item = _HierItem(hid, h.name,
                             self._tx(h.x_position), self._ty(h.y_position),
                             on_moved=self._item_moved)
            item.set_phased(h.uses_phasing)
            self._scene.addItem(item)
            self._hier_items[hid] = item

        if "LINK_DATA" in req(rbd).sections:
            rows = req(rbd).sections["LINK_DATA"].rows
            log.debug("_rebuild_scene: scanning %d link rows", len(rows))
            for row in rows:
                try:
                    link = link_from_row(row)
                except (ValueError, KeyError):
                    continue
                self.links.append(link)
                from_item = self._resolve_link_endpoint(
                    link.connect_f_ref, link.connect_from_id)
                to_item = self._resolve_link_endpoint(
                    link.connect_t_ref, link.connect_to_id)
                if from_item is None or to_item is None:
                    continue
                log.debug("_rebuild_scene: link id=%s %s:%s -> %s:%s",
                          link.object_num, link.connect_f_ref,
                          link.connect_from_id, link.connect_t_ref,
                          link.connect_to_id)
                link_item = _LinkItem(from_item, to_item,
                                      link_num=link.object_num)
                self._links.append(link_item)
                self._scene.addItem(link_item)
        # Ports depend on an element's other links, so recompute every
        # route once the whole topology exists (see _rebuild_link_items).
        for link_item in self._links:
            link_item._update_route()

        # K-of-N badges: a Regular node shows its k/n requirement inside
        # the circle (e.g. "1/2"), matching the real canvas's own labels.
        # Must filter by `connect_t_ref=="RBDNode"`: block/node/event ids
        # are each numbered independently (a Block and a Node can share
        # the same integer id), so counting every link with this raw
        # `connect_to_id` -- regardless of what KIND of element it
        # targets -- overcounts whenever some other element happens to
        # share the node's id, inflating the "n" half of the badge
        # (e.g. "1/3" instead of "1/2").
        incoming: dict[int, int] = {}
        for link in self.links:
            if link.connect_t_ref == "RBDNode":
                incoming[link.connect_to_id] = incoming.get(link.connect_to_id, 0) + 1
        for nid, node in self.nodes.items():
            item = self._node_items.get(nid)
            if item is not None and node.type_node == 2 and nid in incoming:
                n = incoming[nid]
                k = node.good_paths
                # A file k=-1 sentinel means "K-of-N never configured"
                # (coerced to 1 at load); only show a badge that makes
                # sense.
                if 1 <= k <= n and n > 1:
                    item.set_label_text(f"{k}/{n}")

        log.debug("_rebuild_scene: %d blocks, %d nodes, %d events, %d "
                  "hiers, %d links placed -- entering _apply_scene_layout",
                  len(self._block_items), len(self._node_items),
                  len(self._event_items), len(self._hier_items),
                  len(self._links))
        self._apply_scene_layout()
        log.debug("_rebuild_scene: _apply_scene_layout done")
        self._apply_tool_cursor()       # the new items show the armed tool

        # Annotations for this level (.rbds layer)
        self._anno_items = {}
        for a in getattr(self, "annotations", []):
            if int(a.get("parent", 0)) != cur:
                continue
            cls = _ANNO_CLASSES.get(a.get("kind"))
            if cls is None:
                continue
            item = cls(int(a.get("id", 0)), cur, a, self,
                       *_anno_ctor_args(a))
            item.setZValue(float(a.get("z", ANNO_Z_MIDDLE)))
            self._scene.addItem(item)
            self._anno_items[int(a.get("id", 0))] = item

        self._loading = False
        self.reset_zoom()   # frame the model on open (zoom stays free)

    # -------------------------------------------------- annotations --
    def _next_anno_id(self) -> int:
        used = {int(a.get("id", 0)) for a in
                getattr(self, "annotations", [])}
        used |= set(self._anno_items)
        i = getattr(self, "_anno_seq", 1)
        while i in used:
            i += 1
        return i

    def _begin_annotation(self, kind: str, scene_pos: QPointF):
        """Create a new text annotation (single click: it grows with its
        own content, unlike rect/ellipse's two-click drag placement --
        see _begin_annotation_shape). The AnnoCmd's redo builds both the
        canvas item and the model entry (so undo/redo are exact)."""
        if getattr(self, "_rbd_ref", None) is None:
            return None
        aid = self._next_anno_id()
        w0, h0 = 120, 40
        x0, y0 = round(scene_pos.x(), 2), round(scene_pos.y(), 2)
        entry = {
            "kind": kind, "id": aid,
            "parent": self._current_parent,
            "x": x0, "y": y0,
            "w": w0, "h": h0, "text": "",
            "z": self.new_annotation_z(),
            **self._anno_default_props(kind)}
        self._stack.push(AnnoCmd(self, entry, add=True))
        item = self._anno_items.get(aid)
        if kind == "text" and item is not None:
            item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            item.setFocus()
        return item

    def _begin_annotation_arrow(self, tail: QPointF, head: QPointF):
        """Second click of the two-click arrow placement: commit an
        "arrow" annotation running tail->head."""
        if getattr(self, "_rbd_ref", None) is None:
            return None
        aid = self._next_anno_id()
        entry = {
            "kind": "arrow", "id": aid,
            "parent": self._current_parent,
            "x": 0, "y": 0,
            "x1": round(tail.x(), 2), "y1": round(tail.y(), 2),
            "x2": round(head.x(), 2), "y2": round(head.y(), 2),
            "text": "",
            "z": self.new_annotation_z(),
            **self._anno_default_props("arrow")}
        self._stack.push(AnnoCmd(self, entry, add=True))
        return self._anno_items.get(aid)

    def _drop_arrow_ghost(self):
        if self._arrow_ghost is not None:
            self._scene.removeItem(self._arrow_ghost)
            self._arrow_ghost = None

    def _begin_annotation_shape(self, kind: str, p1: QPointF, p2: QPointF):
        """Second click of the two-click rect/ellipse placement (mirrors
        _begin_annotation_arrow): commit a shape spanning corners
        `p1`/`p2`. A same-spot double-click (zero drag) still produces a
        usable box, clamped to the same 12x12 minimum the resize handle
        itself enforces."""
        if getattr(self, "_rbd_ref", None) is None:
            return None
        aid = self._next_anno_id()
        x0 = min(p1.x(), p2.x())
        y0 = min(p1.y(), p2.y())
        w0 = max(12.0, abs(p2.x() - p1.x()))
        h0 = max(12.0, abs(p2.y() - p1.y()))
        entry = {
            "kind": kind, "id": aid,
            "parent": self._current_parent,
            "x": round(x0, 2), "y": round(y0, 2),
            "w": round(w0, 2), "h": round(h0, 2), "text": "",
            "z": self.new_annotation_z(),
            **self._anno_default_props(kind)}
        self._stack.push(AnnoCmd(self, entry, add=True))
        return self._anno_items.get(aid)

    def _drop_shape_ghost(self):
        if self._shape_anno_ghost is not None:
            self._scene.removeItem(self._shape_anno_ghost)
            self._shape_anno_ghost = None

    def sync_annotation_geometry(self, item):
        """Item moved/resized: mirror into the model annotations."""
        aid = getattr(item, "_anno_id", None)
        if aid is None:
            return
        for a in getattr(self, "annotations", []):
            if int(a.get("id", 0)) == aid:
                sp = item.scenePos()
                a["x"] = round(sp.x(), 2); a["y"] = round(sp.y(), 2)
                if hasattr(item, "rect"):
                    r = item.rect()
                    a["w"] = round(r.width(), 2)
                    a["h"] = round(r.height(), 2)
                if hasattr(item, "line"):
                    ln = item.line()
                    a["x1"] = round(ln.x1(), 2); a["y1"] = round(ln.y1(), 2)
                    a["x2"] = round(ln.x2(), 2); a["y2"] = round(ln.y2(), 2)
                break
        self.layout_changed.emit()

    def _sync_anno_z(self, item):
        aid = getattr(item, "_anno_id", None)
        for a in getattr(self, "annotations", []):
            if int(a.get("id", 0)) == aid:
                a["z"] = item.zValue()
                break
        self.layout_changed.emit()

    def set_annotation_text(self, item, text: str) -> None:
        """Commit an edited text annotation as one undoable command. The
        canvas item already holds the new text (the user typed it) while
        the model entry still holds the pre-edit text, so that entry is
        the "before" snapshot. Without this a typed edit was written
        straight into the model with no command, so undo/redo skipped it
        (and redo of the original "add" restored the empty initial text).
        """
        aid = getattr(item, "_anno_id", None)
        if aid is None:
            return
        entry = next((dict(a) for a in getattr(self, "annotations", [])
                      if int(a.get("id", 0)) == aid), None)
        if entry is None or entry.get("text", "") == text:
            return
        old = dict(entry)
        old.update(item.geometry_snapshot())
        old["z"] = item.zValue()
        new = dict(old)
        new["text"] = text
        self._stack.push(AnnoEditCmd(self, aid, old, new))

    def write_back_annotations(self):
        """Sync every live annotation item's geometry/text/props into
        rbd.annotations (called before save)."""
        for aid, item in getattr(self, "_anno_items", {}).items():
            for a in getattr(self, "annotations", []):
                if int(a.get("id", 0)) == aid:
                    sp = item.scenePos()
                    a["x"] = round(sp.x(), 2)
                    a["y"] = round(sp.y(), 2)
                    if hasattr(item, "rect"):
                        r = item.rect()
                        a["w"] = round(r.width(), 2)
                        a["h"] = round(r.height(), 2)
                    if hasattr(item, "line"):
                        ln = item.line()
                        a["x1"] = round(ln.x1(), 2)
                        a["y1"] = round(ln.y1(), 2)
                        a["x2"] = round(ln.x2(), 2)
                        a["y2"] = round(ln.y2(), 2)
                    if hasattr(item, "toPlainText"):
                        a["text"] = item.toPlainText()
                    for k in ANNO_DEFAULTS:
                        if k in item.props:
                            a[k] = item.props[k]
                    break
        req(self._rbd_ref).annotations = list(getattr(self, "annotations", []))

    # ---------------------------------------------- annotation z-order
    def _anno_z_values(self) -> list[float]:
        """Every z value in use by an annotation on the CURRENT level, in
        scene order, de-duplicated. This is the ladder Bring Forward /
        Send Backward step along: a single step always lands on a real
        slot someone is actually using rather than an arbitrary +1, so
        two notes that share a band are swapped in one press instead of
        needing a dozen."""
        zs = {round(float(a.get("z", ANNO_Z_MIDDLE)), 4)
              for a in getattr(self, "annotations", [])
              if int(a.get("parent", 0)) == self._current_parent}
        return sorted(zs)

    def _selected_annotation_ids(self) -> list[int]:
        """Ids of the selected annotations, in scene insertion order."""
        return [aid for aid, it in getattr(self, "_anno_items", {}).items()
                if it.isSelected()]

    def _n_deletable_selected(self) -> int:
        """How many things a Delete will actually remove.

        Not `len(selectedItems())`: an arrow annotation is deliberately
        NOT `ItemIsMovable` (it has no pos() to drag -- its two endpoints
        are its geometry), so a movable-flag count misses arrows, while
        adding the annotation count on top of a full `selectedItems()`
        count double-counts every other note. This is the one number the
        context menus label "Delete N selected" with.
        """
        return len([it for it in self._scene.selectedItems()
                    if (it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
                    or isinstance(it, tuple(_ANNO_TYPES))])

    def _add_order_actions(self, menu) -> dict:
        """Append the four z-order items to `menu` and return them keyed
        by command. Each is enabled only when it would move something --
        `annotation_order_target` is the single rule, shared with the
        ribbon buttons, so a menu can never offer a move the buttons
        would refuse."""
        out = {}
        for key, label in ANNO_ORDER_COMMANDS.items():
            act = menu.addAction(label)
            act.setEnabled(self.annotation_order_target(key) is not None)
            out[key] = act
        return out

    def _edit_annotation_text(self, item):
        """Put a text annotation into in-place editing, as a double-click
        does, so the menu is a real alternative to hunting for the
        item's middle."""
        if getattr(item, "kind", None) != "text":
            return
        item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        item.setFocus()

    def _duplicate_selected_annotations(self) -> int:
        """Copy-paste the selected annotations, offset down-right so the
        copy is visible next to Raptor 7. One undo step, same
        `AnnoCmd` path a normal paste uses."""
        if not self._selected_annotation_ids():
            return 0
        # `copy_selection`'s return is the ELEMENT count, which is 0 for
        # an annotation-only selection even though it filled the buffer
        # -- so ask the buffer what is actually in it.
        self.copy_selection()
        annos = [r for k, r in getattr(self, "_paste_buffer", [])
                 if k == "annotation"]
        if not annos:
            return 0
        self.paste(offset=(16.0, 12.0))
        return len(annos)

    def _show_background_context_menu(self, event, scene_pos):
        """Empty-canvas context menu. There was no menu here at all
        before, which made right-click paste impossible and left Select
        All and the Snap toggle reachable only from the ribbon."""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        mx, my = self.scene_to_model(scene_pos)
        paste_act = menu.addAction("Paste Here (at pointer)")
        paste_act.setEnabled(bool(self._paste_buffer))
        menu.addSeparator()
        select_all_act = menu.addAction("Select All")
        select_all_act.setShortcut(QKeySequence("Ctrl+A"))
        menu.addSeparator()
        snap_act = menu.addAction("Snap to Grid")
        snap_act.setCheckable(True)
        snap_act.setChecked(bool(self._snap_state["x"] or self._snap_state["y"]))
        fit_act = menu.addAction("Fit All")
        chosen = self._exec_menu(menu, event)
        if chosen is paste_act:
            self.paste(at_model=(mx, my))
        elif chosen is select_all_act:
            self.select_all()
        elif chosen is snap_act:
            self.set_snap_enabled(snap_act.isChecked())
        elif chosen is fit_act:
            self.fit_all()

    def select_all(self):
        """Select every movable item on the current level -- blocks,
        nodes, events, hierarchy ovals AND annotations. `Ctrl+A` skipped
        annotations even though Align & Distribute advertises working on
        them, so "select everything then tidy up" quietly left the notes
        behind."""
        for items in (self._block_items, self._node_items,
                      self._event_items,
                      getattr(self, "_hier_items", {}),
                      getattr(self, "_anno_items", {})):
            for it in items.values():
                it.setSelected(True)

    def annotation_order_target(self, command: str) -> float | None:
        """The z value `command` would move the current annotation
        selection to, or None when the command has nothing to do.

        This is the single source of truth behind the ribbon buttons, the
        context-menu items and their disabled states -- the menu asks
        here rather than re-deriving the rule, so a button can never be
        enabled for a move that would not change anything.
        """
        sel = self._selected_annotation_ids()
        if not sel:
            return None
        items: list[Any] = [self._anno_items[aid] for aid in sel]
        current = [it.zValue() for it in items]
        ladder = sorted(set(self._anno_z_values())
                        | set(ANNO_Z_BANDS))
        target: float | None
        if command == "front":
            target = ANNO_Z_FRONT
        elif command == "back":
            target = ANNO_Z_BEHIND
        elif command == "forward":
            above = [z for z in ladder if z > max(current)]
            target = above[0] if above else None
        elif command == "backward":
            below = [z for z in ladder if z < min(current)]
            target = below[-1] if below else None
        else:
            return None
        if target is None or all(abs(c - target) < 1e-6 for c in current):
            return None      # already there: nothing to do
        return target

    def set_annotation_order(self, command: str) -> bool:
        """Apply one of the four z-order commands to the whole annotation
        selection as ONE undo step. Returns True if anything moved."""
        # Checked BEFORE anything moves: this is an undoable edit, and a
        # canvas with no stack yet must not be half-changed.
        stack = getattr(self, "_stack", None)
        if stack is None:
            return False
        target = self.annotation_order_target(command)
        if target is None:
            return False
        label = ANNO_ORDER_COMMANDS[command]
        edits = []
        for aid in self._selected_annotation_ids():
            before = self._anno_state(aid)
            if before is None:
                continue
            item: Any = self._anno_items[aid]
            item.setZValue(target)
            self._sync_anno_z(item)
            after = self._anno_state(aid)
            if after is not None and after != before:
                edits.append((aid, before, after))
        if not edits:
            return False
        if len(edits) == 1:
            aid, before, after = edits[0]
            stack.push(AnnoEditCmd(self, aid, before, after, label,
                                   layout_only=True))
        else:
            # Z-order cannot break an input rule either; keep the whole
            # macro off the gate's two whole-model passes (the `ungated`
            # scope must cover beginMacro too -- that is where the "before"
            # snapshot is taken).
            with stack.ungated():
                stack.beginMacro(label)
                try:
                    for aid, before, after in edits:
                        stack.push(AnnoEditCmd(self, aid, before, after,
                                               label, layout_only=True))
                finally:
                    stack.endMacro()
        return True

    def new_annotation_z(self) -> float:
        """z for a freshly placed annotation: on top of whatever is
        already on this level, never below the middle band. Without
        this, drawing a new note after a Bring to Front silently filed it
        *under* the note that had just been sent to the front (a new one
        always got the fixed ANNO_Z_MIDDLE default)."""
        top = max(self._anno_z_values(), default=ANNO_Z_MIDDLE)
        return max(ANNO_Z_MIDDLE, top)

    def _anno_default_props(self, kind: str) -> dict:
        """Fresh props for a newly placed `kind` annotation: the
        theme-aware factory base, overlaid with the user's saved style
        default for that kind.

        The default is set from the context menu's "Set as Default for
        New <Kind>s" on an existing annotation, so the workflow is
        "style one, then place the rest" instead of re-picking the same
        colours on every shape. Keys absent from the saved default (and
        keys that don't apply to this kind, like a fill on an arrow) fall
        through to the factory value, so a default saved for one kind
        can never give another kind an invalid prop.
        """
        props = _anno_base_props()
        applicable = ANNO_APPLICABLE_PROPS.get(kind, tuple(ANNO_DEFAULTS))
        saved = getattr(self, "default_annotation_props", {}).get(kind)
        if saved:
            for key, value in saved.items():
                if key in applicable:
                    props[key] = value
        if kind == "arrow" and "border_width" not in (saved or {}):
            # The shared shape default (1px, or 0 for "no border") would
            # make a brand-new arrow all but invisible the instant it is
            # placed -- unlike a rect/ellipse/text box, the line IS the
            # whole annotation, so it needs its own visible width. A
            # saved arrow default that names a width wins over this.
            props["border_width"] = 2
        return props

    def set_annotation_style_default(self, kind: str, props: dict) -> None:
        """Adopt `props` as the style for every future `kind`
        annotation. Persisting it is MainWindow's job (it owns QSettings);
        this applies it and says so."""
        stored = {k: v for k, v in dict(props or {}).items()
                  if k in ANNO_APPLICABLE_PROPS.get(kind, ())}
        self.default_annotation_props[kind] = stored
        self.annotation_style_default_changed.emit(kind, dict(stored))
        noun = ANNO_KIND_NOUNS.get(kind, (kind, kind))[1]
        _view_status(self, f"New {noun} will use this style")

    def clear_annotation_style_default(self, kind: str) -> None:
        """Forget the saved style for `kind`, back to the factory look
        (theme-aware colours, no fill)."""
        self.default_annotation_props.pop(kind, None)
        self.annotation_style_default_changed.emit(kind, None)
        noun = ANNO_KIND_NOUNS.get(kind, (kind, kind))[1]
        _view_status(self, f"New {noun} reverted to the default style")

    def has_annotation_style_default(self, kind: str) -> bool:
        return kind in getattr(self, "default_annotation_props", {})

    def delete_selected_annotations(self) -> int:
        stack = getattr(self, "_stack", None)
        n = 0
        for aid, item in list(getattr(self, "_anno_items", {}).items()):
            if not item.isSelected():
                continue
            entry = next((a for a in getattr(self, "annotations", [])
                          if int(a.get("id", 0)) == aid), None)
            if entry is None:
                continue
            # Undoable via the existing AnnoCmd (add=False) -- before
            # this it removed the item directly, so a Ctrl+Z could not
            # bring a deleted annotation back even though the command
            # class already existed for exactly this.
            if stack is not None:
                stack.push(AnnoCmd(self, entry, add=False))
            else:
                self._scene.removeItem(item)
                del self._anno_items[aid]
                self.annotations = [
                    a for a in getattr(self, "annotations", [])
                    if int(a.get("id", 0)) != aid]
            n += 1
        if n:
            _view_status(self, f"{n} annotation(s) deleted")
            if stack is None:
                self.model_changed.emit()
        return n

    def sync_model_positions(self):
        """Typed objects <- scene items for every movable kind, and the
        matching `_pos_overrides` entries (the position overlay's own
        source -- bulk movers that place items directly without MoveCmd
        (auto layout / align / distribute) must record here or their
        arrangement is lost on the next level change)."""
        # Blocks are positioned by their top-left, the other kinds by
        # their centre; both are scene pixels and both go through the one
        # transform.
        for kind, items, by_id, half in (
            ("block", self._block_items, self.blocks,
             (BLOCK_W / 2, BLOCK_H / 2)),
            ("node", self._node_items, self.nodes, (0.0, 0.0)),
            ("event", self._event_items, self.events, (0.0, 0.0)),
            ("hier", self._hier_items, self.hiers, (0.0, 0.0)),
        ):
            for oid, item in items.items():
                obj = by_id.get(oid)
                if obj is None:
                    continue
                sp = item.scenePos()
                mx, my = self._vt.to_model(sp.x() + half[0],
                                           sp.y() + half[1])
                obj.x_position = mx
                obj.y_position = my
                self._pos_overrides[(kind, oid)] = (mx, my)
        self._write_positions_to_rows()

    def _write_positions_to_rows(self) -> None:
        """The rows take the canvas positions as they are recorded. A bulk
        move (Align, Distribute, Flip, their Undo) left the rows at the old
        place until the next full write-back, so anything reading the rows
        in between -- Issues, a run, Save As .rbd -- saw two layouts."""
        rbd = self._rbd_ref
        if rbd is None:
            return
        sections = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                    "event": "EVENT_DATA", "hier": "HIERARCHY_DATA"}
        live = {"block": self._block_items, "node": self._node_items,
                "event": self._event_items, "hier": self._hier_items}
        for kind, section in sections.items():
            sec = rbd.sections.get(section)
            if sec is None:
                continue
            for row in sec.rows:
                oid = _safe_int(row.get("ID"))
                xy = self._pos_overrides.get((kind, oid))
                if xy is None or oid not in live[kind]:
                    continue
                row["xPos"], row["yPos"] = _pos(xy[0]), _pos(xy[1])

    def write_back_to_rbd(self, rbd: RbdFile):
        self.sync_model_positions()
        self._sync_event_hier_positions(rbd)
        self._editor.write_back(rbd, hier_ids=set(self._hier_items))

    def _sync_event_hier_positions(self, rbd: RbdFile) -> None:
        """Event and hierarchy positions live on the canvas items, which
        are movable; the typed objects can lag a direct item move that has
        not been synced yet, so trust the item for every element that has a
        file row, then let the editor write the objects out."""
        for sec_name, objs, items in (
                ("EVENT_DATA", self.events, self._event_items),
                ("HIERARCHY_DATA", self.hiers, self._hier_items)):
            sec = rbd.sections.get(sec_name)
            if sec is None:
                continue
            for row in sec.rows:
                try:
                    oid = int(float(row.get("ID", "-1")))
                except (ValueError, KeyError):
                    continue
                obj, item = objs.get(oid), items.get(oid)
                if obj is None or item is None:
                    continue
                sp = item.scenePos()
                obj.x_position = sp.x() / self._scale + self._origin_x
                obj.y_position = self._flip_base - sp.y() / self._scale

    # --- Undoable mutators (public API) ---------------------------------
    # Each captures the old value, pushes a command; the command's
    # redo()/undo() call the _apply_* helpers below.

    def _element_name(self, etype: str, oid: int) -> str:
        # `RBDNode` has its own `node_name` field, separate from the
        # `RBDBasic`-inherited `name` every other element type uses.
        pools: dict[str, dict[int, Any]] = {
            "block": self.blocks, "node": self.nodes,
            "event": self.events, "hier": self.hiers}
        pool = pools.get(etype, {})
        obj = pool.get(oid)
        return (getattr(obj, "node_name", "") if etype == "node" else
                getattr(obj, "name", "")) or ""

    def current_comment_stored(self, etype: str, oid: int) -> str:
        """The COMMENTS row's raw stored value ('~' for newlines) for one
        element, or "" -- the undo command's "old" state."""
        if self._rbd_ref is None:
            return ""
        sec = self._rbd_ref.sections.get("COMMENTS")
        if sec is None:
            return ""
        name = self._element_name(etype, oid)
        for row in sec.rows:
            if (row.get("Type", "").lower() == etype
                    and row.get("Name", "") == name):
                return row.get("Comment", "") or ""
        return ""

    def _apply_element_comment_stored(self, etype: str, oid: int,
                                      stored: str) -> None:
        """Write `stored` (already '~'-joined) into the COMMENTS section.
        Emits `model_changed`; the undo command's `_done()` would double
        it, so commands call this without the emit via the wrapper below."""
        sec = self._rbd_ref.sections.get("COMMENTS") if self._rbd_ref else None
        if sec is None:
            return
        name = self._element_name(etype, oid)
        target_row = None
        for row in sec.rows:
            if (row.get("Type", "").lower() == etype
                    and row.get("Name", "") == name):
                target_row = row
                break
        if target_row is None:
            if not stored:
                return
            sec.rows.append({"Type": etype, "ID": str(oid),
                             "Name": name, "Comment": stored})
        else:
            target_row["Comment"] = stored

    def set_element_comment(self, etype: str, oid: int, text: str) -> None:
        """Write a comment for one element into the COMMENTS section
        (legacy format: 'block  <id>  <name>  <comment>', '~' separates
        lines). Editable from the Inspector (Raptor 7 comments).
        Undoable."""
        if self._rbd_ref is None:
            return
        stored = text.replace("\n", "~")
        old = self.current_comment_stored(etype, oid)
        if old == stored:
            return
        self._stack.push(CommentCmd(self, etype, oid, old, stored))

    def rename_block(self, block_id: int, name: str):
        if block_id not in self.blocks:
            return
        old = self.blocks[block_id].name
        if name == old:
            return
        self._stack.push(RenameCmd(self, "block", block_id, old, name))

    def rename_node(self, node_id: int, name: str):
        if node_id not in self.nodes:
            return
        old = self.nodes[node_id].node_name
        if name == old:
            return
        self._stack.push(RenameCmd(self, "node", node_id, old, name))

    #: Distribution setters always take CANONICAL values (Exponential =
    #: mean, Lognormal = mean + standard deviation). Raptor's lambda /
    #: mu-sigma preferences change only the entry widgets, which convert
    #: before calling these (see `raptor.gui.ui.param_entry`), so every
    #: programmatic path -- seeding on a distribution change, tests, the
    #: API -- keeps its canonical meaning regardless of the preference.
    def set_block_fail_params(self, block_id: int, values: list[float]):
        if block_id not in self.blocks:
            return
        block = self.blocks[block_id]
        old = list(block.fail_values)
        if values == old:
            return
        self._stack.push(ParamsCmd(self, "failure params", block_id,
                                   old, list(values)))

    def set_block_repair_params(self, block_id: int, values: list[float]):
        if block_id not in self.blocks:
            return
        block = self.blocks[block_id]
        old = list(block.repair_values)
        if values == old:
            return
        self._stack.push(ParamsCmd(self, "repair params", block_id,
                                   old, list(values)))

    def set_block_pm_freq(self, block_id: int, value: float):
        if block_id not in self.blocks:
            return
        old = self.blocks[block_id].pm_freq
        if value == old:
            return
        self._stack.push(ParamsCmd(self, "PM frequency", block_id,
                                   old, value))

    def set_block_pm_enabled(self, block_id: int, value: bool):
        block = self.blocks.get(block_id)
        if block is None or block.uses_pm == value:
            return
        self._stack.push(ParamsCmd(self, "block uses pm", block_id,
                                   block.uses_pm, value))

    #: PM tab's 5 option checkboxes -- one generic setter,
    #: keyed the same way `_populate_pm`'s own loop builds them.
    _PM_OPTION_ATTRS = PM_OPTION_ATTRS

    def set_block_pm_option(self, block_id: int, key: str, value: bool):
        block = self.blocks.get(block_id)
        attr = self._PM_OPTION_ATTRS.get(key)
        if block is None or attr is None or getattr(block, attr) == value:
            return
        self._stack.push(ParamsCmd(self, f"block pm {key}", block_id,
                                   getattr(block, attr), value))

    def set_block_dep_master(self, block_id: int, dependency_num: int,
                             dep_type: str):
        """Block Dependency tab's master-element selector -- same shape
        as `set_node_dep`, reusing Inspector's `_dependency_group`."""
        block = self.blocks.get(block_id)
        if block is None:
            return
        old = (block.dependency_num, block.dep_type)
        new = (dependency_num, dep_type)
        if old == new:
            return
        self._stack.push(ParamsCmd(self, "block dependency", block_id,
                                   old, new))

    def set_block_dep_default_idle(self, block_id: int, value: bool):
        block = self.blocks.get(block_id)
        if block is None or block.def_dep_state_idle == value:
            return
        self._stack.push(ParamsCmd(self, "block dep default idle",
                                   block_id, block.def_dep_state_idle, value))

    def set_block_resource_pool(self, block_id: int, name: str):
        """Assigning/clearing the pool also flips `resource_type_count` (0 or 1
        -- the only two values the engine ports, `block_lifecycle.py`
        ) together with it in one command: `resource_pool_name` alone
        is inert if `resource_type_count` stays 0."""
        block = self.blocks.get(block_id)
        if block is None:
            return
        old = (block.resource_pool_name, block.resource_type_count)
        new = (name, 1 if name else 0)
        if old == new:
            return
        self._stack.push(ParamsCmd(self, "block resource pool", block_id,
                                   old, new))

    def set_block_resource_counts(self, block_id: int, num_repair: int,
                                  num_pm: int):
        block = self.blocks.get(block_id)
        if block is None:
            return
        old = (block.repair_resources_needed, block.pm_resources_needed)
        new = (num_repair, num_pm)
        if old == new:
            return
        self._stack.push(ParamsCmd(self, "block resource counts",
                                   block_id, old, new))

    def set_block_new_spares(self, block_id: int, count: int,
                             arrival: float):
        """Custom-sparing new-spares count + arrival interval -- same
        CUSTOM-gated pattern already resolved for `init_stock`."""
        block = self.blocks.get(block_id)
        if block is None:
            return
        old = (block.new_spares, block.new_spares_arrival)
        new = (count, arrival)
        if old == new:
            return
        self._stack.push(ParamsCmd(self, "block new spares", block_id,
                                   old, new))

    def set_block_standby_stress(self, block_id: int, value: float):
        block = self.blocks.get(block_id)
        if block is None or block.sb_stress == value:
            return
        self._stack.push(ParamsCmd(self, "block standby stress",
                                   block_id, block.sb_stress, value))

    def set_block_gd_enabled(self, block_id: int, value: bool):
        """Growth/Decay on/off: off is the `GDType=0` sentinel; on defaults to Linear
        (1) if no type was ever chosen. Ported in
        `block_lifecycle.combined_stress`."""
        block = self.blocks.get(block_id)
        if block is None:
            return
        old = block.gd_type
        new = 0 if not value else (old if old != 0 else 1)
        if old == new:
            return
        self._push_gd_type(block_id, new)

    def set_block_gd_type(self, block_id: int, gd_type: int):
        block = self.blocks.get(block_id)
        if block is None or block.gd_type == gd_type:
            return
        self._push_gd_type(block_id, gd_type)

    def _push_gd_type(self, block_id: int, gd_type: int) -> None:
        """A growth/decay type change, with the rate and limit Raptor 7 would
        put back if the current ones are refused for the new type
        (`input_rules.gd_corrections`), in one undo step: the Inspector
        commits one field at a time, so a type change alone (geometric with a
        base of 0) would otherwise be refused before the rate can be set."""
        from raptor.core.input_rules import dialog_fail_mean, gd_corrections
        block = self.blocks[block_id]
        old_rl = (block.gd_rate, block.gd_limit)
        new_rl = old_rl if gd_type == 0 else gd_corrections(
            gd_type, block.gd_rate, block.gd_limit,
            dialog_fail_mean(block.fail_distro, list(block.fail_values)))
        self._stack.beginMacro("Growth/decay type")
        try:
            self._stack.push(ParamsCmd(self, "block gd type", block_id,
                                       block.gd_type, gd_type))
            if new_rl != old_rl:
                self._stack.push(ParamsCmd(self, "block gd rate limit",
                                           block_id, old_rl, new_rl))
        finally:
            self._stack.endMacro()

    def set_block_gd_rate_limit(self, block_id: int, rate: float,
                                limit: float):
        block = self.blocks.get(block_id)
        if block is None:
            return
        old = (block.gd_rate, block.gd_limit)
        new = (rate, limit)
        if old == new:
            return
        self._stack.push(ParamsCmd(self, "block gd rate limit",
                                   block_id, old, new))

    def set_node_good_paths(self, node_id: int, value: int):
        if node_id not in self.nodes:
            return
        old = self.nodes[node_id].good_paths
        if value == old:
            return
        self._stack.push(ParamsCmd(self, "node k", node_id, old, value))

    def set_node_full_flow(self, node_id: int, value: bool):
        node = self.nodes.get(node_id)
        if node is None or node.full_flow == value:
            return
        self._stack.push(ParamsCmd(self, "node full flow", node_id,
                                   node.full_flow, value))

    def set_node_any_path(self, node_id: int, value: bool):
        node = self.nodes.get(node_id)
        if node is None or node.any_path == value:
            return
        self._stack.push(ParamsCmd(self, "node any path", node_id,
                                   node.any_path, value))

    def set_node_report_anal(self, node_id: int, value: bool):
        node = self.nodes.get(node_id)
        if node is None or node.report_node_anal == value:
            return
        self._stack.push(ParamsCmd(self, "node report anal", node_id,
                                   node.report_node_anal, value))

    def set_node_cold_standby(self, node_id: int, value: bool):
        node = self.nodes.get(node_id)
        if node is None or node.cold_standby == value:
            return
        self._stack.push(ParamsCmd(self, "node cold standby", node_id,
                                   node.cold_standby, value))

    def set_node_priority_return(self, node_id: int, value: bool):
        node = self.nodes.get(node_id)
        if node is None or node.priority_return == value:
            return
        self._stack.push(ParamsCmd(self, "node priority return", node_id,
                                   node.priority_return, value))

    def set_node_check_autos_first(self, node_id: int, value: bool):
        node = self.nodes.get(node_id)
        if node is None or node.check_autos_first == value:
            return
        self._stack.push(ParamsCmd(self, "node check autos first",
                                   node_id, node.check_autos_first, value))

    def set_node_k_star(self, node_id: int, value: int):
        node = self.nodes.get(node_id)
        if node is None or node.k_star == value:
            return
        self._stack.push(ParamsCmd(self, "node k star", node_id,
                                   node.k_star, value))

    def set_node_dep(self, node_id: int, dependency_num: int, dep_type: str):
        node = self.nodes.get(node_id)
        if node is None:
            return
        old = (node.dependency_num, node.dep_type)
        new = (dependency_num, dep_type)
        if old == new:
            return
        self._stack.push(ParamsCmd(self, "node dependency", node_id,
                                   old, new))

    def set_event_costs(self, eid: int, values: list[float]):
        event = self.events.get(eid)
        if event is None or len(values) != 3:
            return
        old_value = [event.initial_cost, event.operating_cost, event.repairing_cost]
        new_value = [float(v) for v in values]
        if all(abs(a - b) < 1e-9 for a, b in zip(old_value, new_value)):
            return
        self._stack.push(ParamsCmd(self, "event costs", eid, old_value, new_value))

    def set_block_dist(self, block_id: int, which: str, code: int):
        """Distribution change from the Data Tables grid."""
        from raptor.gui.ui.data_tables import _NUM_PARAMS
        block = self.blocks.get(block_id)
        if block is None:
            return False
        param_count = _NUM_PARAMS.get(code)
        if which == "F":
            old = (block.fail_distro, block.num_fail_params)
        else:
            old = (block.repair_distro, block.num_repair_params)
        new = (code, param_count if param_count is not None else old[1])
        if new == old:
            return False
        self._stack.push(DistCmd(self, block_id, which, old, new))
        return True

    def change_block_dist(self, block_id: int, which: str, code: int) -> bool:
        """A block's failure ("F") or repair ("R") distribution changed by
        the user, from any route (Inspector, Data Tables): the new code,
        and new parameters when the old ones are not valid for it or would
        change the mean (`distro_rules.params_after_change`), in ONE undo
        step. The old numbers used to stay: Fixed 0.25 changed to Binomial
        in the grid left N Trials 0.25 and Probability 0 in the model."""
        from raptor.core.distro_rules import params_after_change
        block = self.blocks.get(block_id)
        if block is None:
            return False
        old_code = block.fail_distro if which == "F" else block.repair_distro
        vals = list(block.fail_values if which == "F" else block.repair_values)
        seeded = params_after_change(
            old_code, vals, code, "failure" if which == "F" else "repair",
            default_mean=100.0 if which == "F" else 10.0)
        padded = vals[:3] + [0.0] * (3 - len(vals[:3]))
        self._stack.beginMacro("Change distribution")
        try:
            changed = self.set_block_dist(block_id, which, code)
            if seeded is not None and seeded != padded:
                (self.set_block_fail_params if which == "F"
                 else self.set_block_repair_params)(block_id, seeded)
                changed = True
        finally:
            self._stack.endMacro()
        return changed

    #: Independent streams are seeds 1..N of FETCH_RAPTOR_SEED; system
    #: streams are the shared A..J codes 201..210; negative = antithetic
    #: (stores -1*n for the antithetic checkbox).
    STREAM_SYSTEM_MIN = 201
    STREAM_SYSTEM_MAX = 210

    def _apply_streams(self, bid: int, f: int | None,
                       r: int | None) -> None:
        self._editor.apply_streams(bid, f, r)

    @staticmethod
    def valid_stream(v: int, max_ind: int) -> bool:
        return ((-max_ind <= v <= -1) or (1 <= v <= max_ind)
                or (DiagramView.STREAM_SYSTEM_MIN <= v
                    <= DiagramView.STREAM_SYSTEM_MAX))

    def set_block_sparing_type(self, block_id: int, type_name: str):
        """Inspector sparing-type change (None/SPARE_POOL/INFINITE/
        CUSTOM via SparingType name)."""
        block = self.blocks.get(block_id)
        if block is None:
            return
        self._stack.push(ParamsCmd(self, "sparing type", block_id,
                                   block.sparing_type.name, type_name))

    def set_block_ldt_dist(self, block_id: int, which: str, code: int):
        """Pre/post-repair logistics delay distribution change."""
        block = self.blocks.get(block_id)
        if block is None:
            return
        _col = ("Pre-LDTDist" if which == "pre" else "Post-LDTDist")
        attr = ("pre_dist" if which == "pre" else "post_dist")
        old_v = getattr(block, attr)
        if old_v == code:
            return
        self._stack.push(ParamsCmd(self, f"{which} LDT dist", block_id,
                                   old_v, code))

    def set_block_pm_trigger(self, block_id: int, triggered: bool,
                             trigger_name: str):
        block = self.blocks.get(block_id)
        if block is None:
            return
        old_state = (block.pm_triggered, block.pm_trig)
        new_state = (triggered,
                     trigger_name if triggered else "")
        if old_state == new_state:
            return
        self._stack.push(ParamsCmd(self, "pm trigger", block_id,
                                   old_state, new_state))

    def set_block_pool_name(self, block_id: int, name: str):
        block = self.blocks.get(block_id)
        if block is None or block.pool_name == name:
            return
        self._stack.push(ParamsCmd(self, "pool name", block_id,
                                   block.pool_name, name))

    def set_block_stream(self, block_id: int, which: str, value: int):
        """Inspector stream change. `which`: 'F', 'R', or 'FR' (legacy
        System combo drives both sides at once)."""
        from raptor.core.rng import FETCH_RAPTOR_SEED
        block = self.blocks.get(block_id)
        if block is None or which not in ("F", "R", "FR"):
            return False
        if not self.valid_stream(value, len(FETCH_RAPTOR_SEED) - 1):
            return False
        cur_f, cur_r = block.fail_stream, block.repair_stream
        new_f = value if which in ("F", "FR") else cur_f
        new_r = value if which in ("R", "FR") else cur_r
        if (new_f, new_r) == (cur_f, cur_r):
            return False
        self._stack.push(StreamCmd(self, block_id, which,
                                   (cur_f, cur_r), (which, value)))
        return True

    def _apply_rename(self, kind: str, oid: int, name: str):
        if not self._editor.rename(kind, oid, name):
            return
        # The row too, now (as `_apply_value` does): validation, the Issues
        # list and a run read the rows, and showed the old name.
        if kind == "hier":
            self._editor.sync_hier_row(oid)
        else:
            self._editor.write_element(self._rbd_ref, kind, oid)
        item_maps: dict[str, dict[int, Any]] = {
            "block": self._block_items, "node": self._node_items,
            "event": self._event_items, "hier": self._hier_items}
        items = item_maps[kind]
        item = items.get(oid)
        if item is not None:
            item.set_name(name)
            # A block's width follows its name: its links' ports move.
            for link in links_affected_by_moving(item):
                link._update_route()

    def _sync_hier_row(self, hid: int):
        self._editor.sync_hier_row(hid)

    def rename_hier(self, hid: int, name: str):
        h = self.hiers.get(hid)
        if h is None or name == h.name:
            return
        self._stack.push(RenameCmd(self, "hier", hid, h.name, name))

    def _sync_event_row(self, eid: int):
        self._editor.sync_event_row(eid)

    def rename_event(self, eid: int, name: str):
        event = self.events.get(eid)
        if event is None or name == event.name:
            return
        self._stack.push(RenameCmd(self, "event", eid, event.name, name))

    def set_event_field(self, eid: int, field: str, value):
        """field: 'dist' | 'param' | 'stream' (EVENT_DATA's Distro /
        failVal / failStream). Undoable."""
        event = self.events.get(eid)
        if event is None:
            return
        current = {"dist": event.fail_distro, "param": event.fail_mean,
                   "stream": event.fail_stream}[field]
        if value == current:
            return
        self._stack.push(ParamsCmd(self, f"event {field}", eid,
                                   current, value))

    def _apply_block_labels(self, item, block):
        """Write a block's current label mode, phase and units onto its
        canvas item. The three item-creation/refresh call sites all go
        through here so a mode or phase change cannot leave one path
        showing stale numbers."""
        mode = str(app_settings.get("appearance/block_labels",
                                    block_labels.DEFAULT_MODE))
        item.set_sublabel(
            block_labels.sublabel(block, mode=mode,
                                  phase=self._label_phase,
                                  units=self._label_units),
            block_labels.tooltip(block, mode=mode,
                                 phase=self._label_phase,
                                 units=self._label_units))

    def set_label_phase(self, phase: int) -> None:
        """Show a phase's law in the block labels (-1 = the model's own)."""
        phase = int(phase)
        if phase == self._label_phase:
            return
        self._label_phase = phase
        self.refresh_block_labels()

    def refresh_block_labels(self) -> None:
        """Re-apply the label setting to every visible block (mode/phase)."""
        self._refresh_block_sublabels()

    def _refresh_block_sublabels(self, block_id: int | None = None):
        """Update the at-a-glance lines under every block name.
        `block_id=None` refreshes every block (after load/retheme)."""
        targets = ([block_id] if block_id is not None
                   else list(self._block_items))
        for bid in targets:
            item = self._block_items.get(bid)
            block = self.blocks.get(bid)
            if item is None or block is None:
                continue
            self._apply_block_labels(item, block)
            item._adjust_label()

    def _block_row_by_id(self, bid: int):
        return self._editor.block_row(bid)

    #: Cost tab fields, (model attr, BLOCK_DATA column), in UI order.
    BLOCK_COST_FIELDS = BLOCK_COST_FIELDS

    def set_block_ldt_params(self, block_id: int, which: str,
                             values: list[float]):
        """Pre/post-LDT params: plain floats straight to rng draws in
        block_lifecycle's ldt_wait -- no fail_mean sentinels on this
        path."""
        attr = {"pre": "pre_params", "post": "post_params"}[which]
        _cols = ("Param1#1", "Param2#1", "Param3#1") if which == "pre" \
            else ("Param1#2", "Param2#2", "Param3#2")
        block = self.blocks.get(block_id)
        if block is None:
            return
        old_value = list(getattr(block, attr))
        new_value = [float(v) for v in values]
        if all(abs(a - b) < 1e-9 for a, b in zip(old_value, new_value)):
            return
        self._stack.push(ParamsCmd(self, f"{which} LDT params", block_id,
                                   old_value, new_value))

    def set_block_pm_dist(self, block_id: int, code: int):
        block = self.blocks.get(block_id)
        if block is None or block.pm_dist == code:
            return
        self._stack.push(ParamsCmd(self, "PM distribution", block_id,
                                   block.pm_dist, code))

    def set_block_pm_params(self, block_id: int, values: list[float]):
        block = self.blocks.get(block_id)
        if block is None:
            return
        old_value = list(block.pm_params)
        new_value = [float(v) for v in values]
        if all(abs(a - b) < 1e-9 for a, b in zip(old_value, new_value)):
            return
        self._stack.push(ParamsCmd(self, "PM params", block_id,
                                   old_value, new_value))

    def set_block_always_done(self, block_id: int, flag: bool):
        block = self.blocks.get(block_id)
        if block is None or block.always_add_done_cost == flag:
            return
        self._stack.push(ParamsCmd(self, "always add done cost",
                                   block_id, block.always_add_done_cost,
                                   flag))

    def set_block_costs(self, block_id: int, values: list[float]):
        """All 14 block cost fields in UI order."""
        block = self.blocks.get(block_id)
        if block is None or len(values) != len(self.BLOCK_COST_FIELDS):
            return
        old_value = [getattr(block, attr)
                for attr, _col in self.BLOCK_COST_FIELDS]
        if all(abs(a - b) < 1e-9 for a, b in zip(old_value, values)):
            return
        self._stack.push(ParamsCmd(self, "costs", block_id, old_value,
                                   list(values)))

    def set_block_pm_stagger(self, block_id: int, value: float):
        if block_id not in self.blocks:
            return
        cur = self.blocks[block_id].pm_stagger
        if abs(cur - value) < 1e-12:
            return
        self._stack.push(ParamsCmd(self, "PM stagger", block_id,
                                   cur, value))

    def set_block_init_stock(self, block_id: int, value: int):
        block = self.blocks.get(block_id)
        if block is None or block.sparing_type.name != "CUSTOM":
            return  # engine only reads it for Custom sparing
        if block.init_stock == value:
            return
        self._stack.push(ParamsCmd(self, "initial stock", block_id,
                                   block.init_stock, value))

    def set_block_dep_fields(self, block_id: int,
                             percs_and_threshold: list[float]):
        block = self.blocks.get(block_id)
        if block is None or len(percs_and_threshold) != 5:
            return
        old_value = [block.dep_nothing_percent, block.dep_idle_percent, block.dep_pm_percent,
                block.dep_fail_percent, block.dep_pm_threshold]
        new_value = [float(x) for x in percs_and_threshold]
        if all(abs(a - b) < 1e-9 for a, b in zip(old_value, new_value)):
            return
        self._stack.push(ParamsCmd(self, "dependency fields", block_id,
                                   old_value, new_value))

    def set_annotation_props(self, aid: int, changes: dict) -> None:
        """Inspector colour/font edit for one annotation -- one
        `AnnoEditCmd` per commit, firing per field like every other
        element type's Inspector fields."""
        item = self._anno_items.get(aid)
        if item is None:
            return
        # Base on the authoritative annotations-list entry, not
        # `item.props` -- AnnoEditCmd._apply() deliberately narrows
        # item.props down to just the ANNO_DEFAULTS (colour/font) keys
        # after every commit (apply_props() never needs id/kind/geometry),
        # so starting from item.props here would drop id/kind/x/y/w/h/
        # parent from `state` on the second-and-later edit, and
        # AnnoEditCmd._apply()'s `a.clear(); a.update(state)` would then
        # wipe those fields from the real annotations list -- silently
        # orphaning the entry (id lookups in sync_annotation_geometry/
        # write_back_annotations/etc. all match by "id", which would no
        # longer be found, breaking further drags and losing it on save).
        start_state = next(
            (dict(a) for a in self.annotations
             if int(a.get("id", 0)) == aid), dict(item.props))
        start_state.update(item.geometry_snapshot())
        if hasattr(item, "toPlainText"):
            start_state["text"] = item.toPlainText()
        start_state["z"] = item.zValue()
        end_state = dict(start_state)
        end_state.update(changes)
        if end_state == start_state:
            return
        # A geometry-only props edit is a drag/resize, not a property
        # change: keep it off the whole-model validation path.
        geometry = {"x", "y", "w", "h", "x1", "y1", "x2", "y2", "z"}
        self._stack.push(AnnoEditCmd(
            self, aid, start_state, end_state,
            layout_only=set(changes) <= geometry))

    def _apply_value(self, what: str, oid: int, value):
        """Perform edit `what`. The model change is the editor's
        (Qt-free, `raptor/gui/model_edit.py`); what it reports needs
        redrawing is done here, because only the view owns the items."""
        refreshes = self._editor.apply_value(what, oid, value)
        # The row too, now: a rebuild re-parses the rows (see
        # `ModelEditor.write_element`). Positions first, as a full
        # write-back does, so the row is not given a stale x/y.
        kind = self._editor.kind_of(what)
        if kind in ("block", "node") and self._rbd_ref is not None:
            self.sync_model_positions()
        self._editor.write_element(self._rbd_ref, kind, oid)
        for refresh in refreshes:
            if refresh.what == SUBLABELS:
                self._refresh_block_sublabels(refresh.oid)
            elif refresh.what == DEPENDENCY_BADGE:
                self._refresh_dependency_badge(refresh.kind, refresh.oid)
        if kind == "event":
            event, item = self.events.get(oid), self._event_items.get(oid)
            if event is not None and item is not None:
                item.set_probability(event.fail_mean
                                     if event.fail_distro == 17 else None)

    def _refresh_dependency_badge(self, kind: str, oid: int) -> None:
        obj = (self.blocks if kind == "block" else self.nodes).get(oid)
        item = (self._block_items if kind == "block"
                else self._node_items).get(oid)
        if obj is not None and item is not None:
            item.set_dependency_badge(
                self._dependency_visual(obj.dependency_num, obj.dep_type))

    # -- ModelHost: what the Qt-free editor reads from this view ---------

    @property
    def rbd(self):
        return self._rbd_ref

    @property
    def edited(self) -> EditedFields:
        return self._edited

    # The write-back pass reads these by their old names.
    @property
    def _edited_good_paths(self) -> set[int]:
        return self._edited.good_paths

    @property
    def _edited_flow_flags(self) -> set[int]:
        return self._edited.flow_flags

    @property
    def _edited_node_deps(self) -> set[int]:
        return self._edited.node_deps

    @property
    def _edited_report_anal(self) -> set[int]:
        return self._edited.report_anal

    @property
    def _edited_standby(self) -> set[int]:
        return self._edited.standby

    def _apply_dist(self, bid: int, which: str, dist_param_count: tuple):
        self._editor.apply_dist(bid, which, dist_param_count)

    # ------------------------------------------------------------------
    # Element creation / deletion (build-mode)
    #
    # New rows are cloned from a real row of the loaded file (the ID=0
    # template for blocks) and inserted into the same `RbdFile` the app
    # saves -- so write_back_to_rbd's existing row-sync covers them with
    # no extra bookkeeping.
    # ------------------------------------------------------------------

    def _template_row(self, section_name: str, id_col: str):
        return row_ops.template_row(self._rbd_ref, section_name, id_col)

    @staticmethod
    def _sanitize_name(name: str) -> str:
        return row_ops.sanitize_name(name)

    def _next_id(self, section_name: str, id_col: str, existing: dict) -> int:
        return row_ops.next_id(self._rbd_ref, section_name, id_col, existing)

    def _insert_block(self, row: dict) -> int:
        """Build model object + canvas item from a full BLOCK_DATA row
        (used by AddCmd.redo and DeleteCmd.undo).

        Level-aware: `self.blocks`/`_block_items` are level-scoped, so a
        row parented to a different hierarchy is registered in the file
        only -- no canvas item, no dict entry. DeleteCmd.undo /
        AddCmd.undo tolerate the missing entries (`pop(iid, None)`), and
        entering that level rebuilds them from the rows as usual."""
        from raptor.core.fileio.typed import block_from_row
        bid = _safe_int(row.get("ID"))
        block = block_from_row(row)
        if _safe_int(row.get("ParentId", "0")) != self._current_parent:
            return bid
        self.blocks[bid] = block
        item = _BlockItem(bid, block.name,
                          self._tx(block.x_position), self._ty(block.y_position),
                          on_moved=self._item_moved,
                          on_dep_hover=self._on_dep_hover)
        self._apply_block_labels(item, block)
        item.set_phased(block.uses_phasing)
        # A fresh add_block() always has dependency_num==0 (no badge),
        # but DeleteCmd.undo restores an existing block that may well
        # have had one.
        item.set_dependency_badge(
            self._dependency_visual(block.dependency_num, block.dep_type))
        self._scene.addItem(item)
        self._block_items[bid] = item
        self._refresh_lod()
        self._recount_link_ends()
        return bid

    def add_block(self, name: str, model_x: float, model_y: float,
                  fdist: int | None = None,
                  fparam: float | None = None) -> int:
        """Create a block at model coordinates; defaults come from the
        Block Defaults unless given."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        if fdist is None:
            fdist = self.default_block_fdist
        fparams = list(self.default_block_fparams)
        if fparam is not None:
            fparams[0] = fparam
        repair_dist = self.default_block_rdist
        repair_params = list(self.default_block_rparams)
        sparing = self.default_block_sparing
        row = self._template_row("BLOCK_DATA", "ID") or {}
        bid = self._next_id("BLOCK_DATA", "ID", self.blocks)
        row["blockName"] = self._sanitize_name(name)
        row["ID"] = str(bid)
        # On the level being shown. The template row says 0, so a block
        # added inside a hierarchy used to land on the home level.
        row["ParentId"] = str(self._current_parent)
        row["xPos"] = f"{model_x:.6f}"
        row["yPos"] = f"{model_y:.6f}"
        row["FDist"] = str(fdist)
        # DELEGATED to the canonical arity table (`rbdlib`, sourced from
        # the legacy writer). This used to hard-code `"2" if fdist == 4`,
        # i.e. two parameters for every non-Exponential distribution --
        # but `DIST_NPARAMS` says Gamma (6), Pearson5 (11), Pearson6 (12),
        # Triangular (14) and Weibull (15) take THREE. So a Weibull placed
        # from the canvas wrote `#FParam = 2` into a file the Raptor 7 engine
        # reads out of bounds: exactly the failure the verification ladder
        # already records. One owner for "how many params does this
        # distribution take" (`api/build.py` uses the same table).
        row["#FParam"] = str(_dist_param_count(fdist))
        for i in range(3):
            row[f"FParam{i + 1}"] = f"{fparams[i] if i < _dist_param_count(fdist) else 0.0:.6f}"
        row["RDist"] = str(repair_dist)
        row["#RParam"] = str(_dist_param_count(repair_dist))
        for i in range(3):
            row[f"RParam{i + 1}"] = f"{repair_params[i] if i < _dist_param_count(repair_dist) else 0.0:.6f}"
        row["SpType"] = sparing
        self._stack.push(AddCmd(self, "block", [row]))
        return bid

    def add_node(self, name: str, model_x: float, model_y: float,
                 node_type: int = 2) -> int:
        """Create a node (Regular by default) cloned from an existing node
        row of the same type."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        section = self._rbd_ref.sections["NODE_DATA"]
        # Clone a same-type row so type-specific columns are right.
        base = None
        for row in section.rows:
            try:
                if int(float(row.get("Type", "0"))) == node_type:
                    base = dict(row)
                    break
            except (ValueError, KeyError):
                continue
        if base is None and section.rows:
            base = dict(section.rows[0])
        if base is None:
            base = {"Name": "", "ID": "0", "xPos": "0", "yPos": "0",
                    "parent": "0", "Type": str(node_type), "k": "0",
                    "n": "0", "Out": "0", "Cold": "0", "PR": "0",
                    "Autos": "0", "k*": "0", "Phases": "0", "depNum": "0",
                    "depType": "None", "divFlow": "1", "fullflow": "1",
                    "repCap": "0", "repNA": "0"}
        nid = self._next_id("NODE_DATA", "ID", self.nodes)
        base["Name"] = self._sanitize_name(name)
        base["ID"] = str(nid)
        base["Type"] = str(node_type)
        base["parent"] = str(self._current_parent)   # the level shown
        base["xPos"] = f"{model_x:.6f}"
        base["yPos"] = f"{model_y:.6f}"
        if node_type == 2:
            base.setdefault("k", "1")
        self._stack.push(AddCmd(self, "node", [base]))
        return nid

    #: HIERARCHY_DATA columns, in the exact order Raptor 7 files use them
    #: (HierarchyExample et al.) -- needed when a model has no hierarchy
    #: section yet and one must be bootstrapped.
    _HIER_COLUMNS = row_ops.HIER_COLUMNS

    def _ensure_hier_section(self):
        """Models without hierarchies have no HIERARCHY_DATA section."""
        return row_ops.ensure_section(self._rbd_ref, "HIERARCHY_DATA")

    def _build_hierarchy_rows(self, name: str, model_x: float,
                              model_y: float):
        """Fresh (hier_row, in_row, out_row) for the current level."""
        parent = self._current_parent
        h = self.hiers.get(parent)
        return row_ops.build_hierarchy_rows(
            self._rbd_ref, name=name, x=model_x, y=model_y, parent=parent,
            parent_level=h.level if h is not None else None,
            used_ids=(set(self.blocks) | set(self.nodes) | set(self.events)
                      | set(self.hiers)),
            node_ids=self.nodes)

    def add_hierarchy(self, name: str, model_x: float,
                      model_y: float) -> int:
        """Create a hierarchy at model coordinates: the HIERARCHY_DATA
        row plus its In/Out marker nodes (type 4/5, ParentId = the new
        hier's own id). One undo step via a stack macro (hier row + two
        node rows move together)."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        hier_row, in_row, out_row = self._build_hierarchy_rows(
            name, model_x, model_y)
        self._stack.beginMacro("Add hierarchy")
        try:
            self._stack.push(AddCmd(self, "hier", [hier_row]))
            self._stack.push(AddCmd(self, "node", [in_row, out_row]))
        finally:
            self._stack.endMacro()
        return _safe_int(hier_row["ID"])

    def import_rbd_as_hierarchy(self, path: str,
                                name: str | None = None) -> int:
        """Merge an existing .rbd file in as one hierarchy: default name = the file's own
        name; the imported model's Start/End nodes become the wrapper's
        In/Out markers; every id is remapped into a fresh range; the
        imported home level hangs directly under the new hier. One macro
        undo step."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        from raptor.core.fileio.rbd import load_rbd
        imported = load_rbd(path)
        if not name:
            stem = os.path.basename(path)
            name = os.path.splitext(stem)[0]

        # Wrapper first so the import remap avoids its ids too.
        hier_row, in_row, out_row = self._build_hierarchy_rows(name, 50.0, 50.0)
        hid = _safe_int(hier_row["ID"])
        plan = plan_import(
            self._rbd_ref, imported, path, hid=hid,
            wrapper_ids=(hid, _safe_int(in_row["ID"]),
                         _safe_int(out_row["ID"])))

        # The oval lands at the host level's current view centre (the
        # imported content keeps its own coordinates on its own level;
        # only the wrapper oval shows here).
        centre = self.mapToScene(self.viewport().rect().center())
        hmodel_x, hmodel_y = self.scene_to_model(centre)
        hier_row, in_row, out_row = self._build_hierarchy_rows(
            name, hmodel_x, hmodel_y)

        # The imported Start/End become this hierarchy's In/Out markers.
        hier_row["in"], hier_row["out"] = str(plan.in_id), str(plan.out_id)
        parent = self._current_parent
        level = (self.hiers[parent].level + 1) if parent in self.hiers else 1
        hier_row["Parent"] = str(parent)
        hier_row["level"] = str(level)
        hier_rows, node_rows = plan.hier_rows, plan.node_rows
        block_rows, event_rows = plan.block_rows, plan.event_rows
        link_rows = plan.link_rows

        # A file's own content, not typed input: not checked against
        # Raptor 7's input rules (`rule_gate`).
        with self._stack.ungated():
            self._stack.beginMacro(f"Import {name} as hierarchy")
            try:
                self._stack.push(AddCmd(self, "hier",
                                        [hier_row] + hier_rows))
                # No wrapper in/out marker rows here: the imported model's
                # own Start/End were converted into the markers (node_rows
                # carries them, re-typed 4/5) and `hrow["in"]/["out"]`
                # reference them. Pushing `_build_hierarchy_rows`' own
                # in_row/out_row as well would leave an unreferenced
                # duplicate in/out pair.
                self._stack.push(AddCmd(self, "node", node_rows))
                if block_rows:
                    self._stack.push(AddCmd(self, "block", block_rows))
                if event_rows:
                    self._stack.push(AddCmd(self, "event", event_rows))
                if link_rows:
                    self._stack.push(AddCmd(self, "link", link_rows))
            finally:
                self._stack.endMacro()
        return hid

    def collapse_selection(self, name: str | None = None) -> int | None:
        """Collapse every selected block/node/event into a new hierarchy
        on this level. Internal links move into it;
        boundary links are replaced by Hier-referencing ones (the real
        file convention: `Node -> Hier` / `Hier -> Node` rows on the
        parent level, `pID` = parent). Returns the new hier id."""
        if self._rbd_ref is None:
            return None
        sel_b = [bid for bid, it in self._block_items.items()
                 if it.isSelected() and bid != 0]
        sel_n = [nid for nid, it in self._node_items.items()
                 if it.isSelected() and self.nodes[nid].type_node == 2]
        sel_e = [eid for eid, it in self._event_items.items()
                 if it.isSelected()]
        if not (sel_b or sel_n or sel_e):
            return None
        keys = ({("Block", str(i)) for i in sel_b} | {("Node", str(i)) for i in sel_n}
                | {("Event", str(i)) for i in sel_e})
        crossing = [((r.get("FromType", "").replace("RBD", "", 1), str(r.get("FromID"))) in keys,
                     (r.get("ToType", "").replace("RBD", "", 1), str(r.get("ToID"))) in keys)
                    for r in self._rbd_ref.sections["LINK_DATA"].rows
                    if _safe_int(r.get("pID", "0")) == self._current_parent] \
            if "LINK_DATA" in self._rbd_ref.sections else []
        from raptor.core.input_rules import collapse_boundary_problem
        problem = collapse_boundary_problem(sum(1 for f, t in crossing if t and not f),
                                            sum(1 for f, t in crossing if f and not t))
        if problem:
            from raptor.gui.ui import msgbox
            msgbox.warning(self, "Collapse Selection", problem)
            return None
        if not name:
            name = f"Hierarchy_{len(self.hiers) + 1}"

        # Oval lands at the selection's centroid.
        points = []
        for items in ((self._block_items, sel_b), (self._node_items, sel_n),
                      (self._event_items, sel_e)):
            table, ids = items
            for oid in ids:
                c = table[oid].sceneBoundingRect().center()
                points.append((c.x(), c.y()))
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        mx, my = self.scene_to_model(QPointF(cx, cy))
        hier_row, in_row, out_row = self._build_hierarchy_rows(name, mx, my)
        hid = _safe_int(hier_row["ID"])

        plan = plan_collapse(
            self._rbd_ref, hid=hid, in_id=_safe_int(hier_row["in"]),
            out_id=_safe_int(hier_row["out"]), parent=self._current_parent,
            blocks=sel_b, nodes=sel_n, events=sel_e)
        parent_moves, link_ops = plan.parent_moves, plan.link_ops
        # Selected annotations at this level follow into the hierarchy
        # -- their parent level becomes the new hier id.
        moved_annos = []
        for a in getattr(self, "annotations", []):
            if int(a.get("parent", 0)) == self._current_parent:
                it = self._anno_items.get(int(a.get("id", 0)))
                if it is not None and it.isSelected():
                    moved_annos.append((int(a.get("id", 0)),
                                        int(a.get("parent", 0))))
        self._stack.push(CollapseCmd(self, [hier_row, in_row, out_row],
                                     parent_moves, link_ops,
                                     moved_annotations=moved_annos))
        # apply annotation re-parent after the command (command rebuilds
        # the scene); entries live in self.annotations
        for aid, _old_parent in moved_annos:
            for a in getattr(self, "annotations", []):
                if int(a.get("id", 0)) == aid:
                    a["parent"] = hid
        return hid

    def _insert_node(self, row: dict) -> int:
        from raptor.core.fileio.typed import node_from_row
        nid = _safe_int(row.get("ID"))
        node = node_from_row(row)
        # In/Out hierarchy markers are always canvas-visible on their
        # hierarchy's parent level (they sit beside the oval there,
        # Raptor 7) even though their `parent` column names the
        # hierarchy itself -- the user double-clicks them to
        # ascend/descend from the parent side. Only skip other kinds of
        # foreign-level rows.
        is_hier_marker = node.type_node in (4, 5)
        if not is_hier_marker and \
                _safe_int(row.get("parent", "0")) != self._current_parent:
            return nid   # another level's row -- file only (see _insert_block)
        self.nodes[nid] = node
        item = _NodeItem(nid, node.node_name, node.type_node,
                         self._tx(node.x_position), self._ty(node.y_position),
                         on_moved=self._item_moved,
                         on_dep_hover=self._on_dep_hover)
        item.set_phased(node.uses_phasing)
        item.set_dependency_badge(
            self._dependency_visual(node.dependency_num, node.dep_type))
        self._scene.addItem(item)
        self._node_items[nid] = item
        self._refresh_lod()
        self._recount_link_ends()
        return nid

    def _insert_hier(self, row: dict) -> int:
        from raptor.core.fileio.typed import hier_from_row
        hid = _safe_int(row.get("ID"))
        hier = hier_from_row(row)
        self.hiers[hid] = hier
        # Only the current level renders its own hierarchy ovals --
        # a newly created oval must appear immediately, but ovals
        # restored by Undo deep inside other levels stay hidden until
        # navigated to (same rule as load_rbd's rebuild).
        if hier.parent_id == self._current_parent:
            item = _HierItem(hid, hier.name,
                             self._tx(hier.x_position),
                             self._ty(hier.y_position),
                             on_moved=self._item_moved)
            item.set_phased(hier.uses_phasing)
            self._scene.addItem(item)
            self._hier_items[hid] = item
            self._refresh_lod()
        return hid

    def _remove_hier(self, oid: int):
        item = self._hier_items.pop(oid, None)
        if item is not None:
            self._scene.removeItem(item)
        self.hiers.pop(oid, None)

    def connect_elements(self, from_type: str, from_id: int,
                         to_type: str, to_id: int) -> int | None:
        """Create a LINK_DATA row between two elements (e.g. Node->Block).
        Exact duplicates are ignored; a second outgoing link from the same
        element is allowed by the file format but the engine only ports
        one outgoing link per block/event -- callers surface that through
        `model_changed` -> Run's NotImplementedError dialog."""
        if self._rbd_ref is None:
            return None

        def same_ref(a: str, b: str) -> bool:
            # Parsed refs carry the model-class prefix ("RBDBlock") while
            # callers pass the file token ("Block") -- compare with the
            # prefix stripped, or the duplicate check is dead code and
            # repeated connect clicks stack identical links.
            return a.replace("RBD", "", 1) == b.replace("RBD", "", 1)

        # Exact duplicate?
        for existing_link in self.links:
            if (same_ref(existing_link.connect_f_ref, from_type)
                    and existing_link.connect_from_id == from_id
                    and same_ref(existing_link.connect_t_ref, to_type)
                    and existing_link.connect_to_id == to_id):
                return None
        section = self._rbd_ref.sections.get("LINK_DATA")
        if section is None:
            return None
        nums = [link.object_num for link in self.links]
        num = max(nums) + 1 if nums else 1
        row = dict(section.rows[0]) if section.rows else {
            "ID": "0", "FromType": "Node", "FromID": "0", "ToType": "Block",
            "ToID": "0", "pID": "0", "ColdPri": "50", "AProb": "1.000000",
            "AutoTime": "0.000000", "ManTime": "0.000000", "CapPri": "50",
            "nomFlow": "-2", "MaxFlow": "-1",
        }
        row["ID"] = str(num)
        # File tokens: LINK_DATA stores unprefixed kinds ("Block",
        # "Node", "Hier") -- link_from_row adds the RBD prefix on parse.
        # Storing caller-style "RBDBlock" here would produce RBDRBDBlock
        # refs that the engine's forward walk rejects ("no outgoing link
        # found").
        row["FromType"] = from_type.replace("RBD", "", 1)
        row["FromID"] = str(from_id)
        row["ToType"] = to_type.replace("RBD", "", 1)
        row["ToID"] = str(to_id)
        self._stack.push(AddCmd(self, "link", [row]))
        return num

    def _recount_link_ends(self) -> None:
        """Recompute In/Out and node n from the actual link rows.

        The file's `In`/`Out` and node `n` are stored, and Raptor 7
        rejects a model whose declared `n` differs from its real incoming
        link count. The GUI used to change the topology without touching
        those counts, so adding/removing a link (or an element) left a
        valid model looking invalid. `fix_link_counts` is the same owner
        the API/save path uses, so there is one rule.
        """
        rbd = self._rbd_ref
        if rbd is None:
            return

        def rows(section: str) -> list[dict]:
            sec = rbd.sections.get(section)
            return sec.rows if sec is not None else []

        blocks, nodes = rows("BLOCK_DATA"), rows("NODE_DATA")
        events, links = rows("EVENT_DATA"), rows("LINK_DATA")
        from raptor.core.fileio.rbdlib import fix_link_counts
        fix_link_counts(blocks, nodes, events, links)

        b_rows = {_safe_int(r.get("ID")): r for r in blocks}
        n_rows = {_safe_int(r.get("ID")): r for r in nodes}
        e_rows = {_safe_int(r.get("ID")): r for r in events}
        for oid, b in self.blocks.items():
            r = b_rows.get(oid)
            if r is not None:
                b.connect_into_num = _safe_int(r.get("In"))
                b.connect_out_of_num = _safe_int(r.get("Out"))
        for oid, n in self.nodes.items():
            r = n_rows.get(oid)
            if r is not None:
                n.connect_out_of_num = _safe_int(r.get("Out"))
        for oid, event in self.events.items():
            r = e_rows.get(oid)
            if r is not None:
                event.connect_into_num = _safe_int(r.get("In"))
                event.connect_out_of_num = _safe_int(r.get("Out"))

    def _insert_link(self, row: dict) -> int:
        from raptor.core.fileio.typed import link_from_row
        num = _safe_int(row.get("ID"))
        link = link_from_row(row)
        self.links.append(link)
        src = self._resolve_link_endpoint(
            link.connect_f_ref, link.connect_from_id)
        dst = self._resolve_link_endpoint(
            link.connect_t_ref, link.connect_to_id)
        if src is not None and dst is not None:
            li = _LinkItem(src, dst, link_num=num)
            self._links.append(li)
            self._scene.addItem(li)
            # A new link can change its endpoints' entry/exit sides.
            for existing in self._links:
                existing._update_route()
        self._recount_link_ends()
        return num

    def add_event(self, name: str, model_x: float, model_y: float,
                  probability: float = 0.5) -> int:
        """Create an event (Bernoulli by default) cloned from an existing
        EVENT_DATA row."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        _section = self._rbd_ref.sections["EVENT_DATA"]
        row = self._template_row("EVENT_DATA", "ID") or {
            "eventName": "", "ID": "0", "xPos": "0", "yPos": "0",
            "ParentId": "0", "In": "1", "Out": "1", "Phasing": "0",
            "Distro": "17", "failStream": "203", "failVal": "0.5",
            "InitCost": "1.000000", "OpCost": "1.000000",
            "RepCost": "1.000000",
        }
        eid = self._next_id("EVENT_DATA", "ID", self.events)
        row["eventName"] = f"P={probability:g}"
        row["ID"] = str(eid)
        row["ParentId"] = str(self._current_parent)   # the level shown
        row["xPos"] = f"{model_x:.6f}"
        row["yPos"] = f"{model_y:.6f}"
        row.setdefault("Distro", "17")
        row["failVal"] = f"{probability:.6f}"
        self._stack.push(AddCmd(self, "event", [row]))
        return eid

    def _insert_event(self, row: dict) -> int:
        from raptor.core.fileio.typed import event_from_row
        eid = _safe_int(row.get("ID"))
        event = event_from_row(row)
        if _safe_int(row.get("ParentId", "0")) != self._current_parent:
            return eid   # another level's row -- file only (see _insert_block)
        self.events[eid] = event
        item = _EventItem(eid, event.name,
                          self._tx(event.x_position), self._ty(event.y_position),
                          on_moved=self._item_moved,
                          probability=(event.fail_mean
                                       if event.fail_distro == 17
                                       else None))
        item.set_phased(event.uses_phasing)
        self._scene.addItem(item)
        self._event_items[eid] = item
        self._refresh_lod()
        self._recount_link_ends()
        return eid

    # --- Cut / Copy / Paste ------

    def copy_selection(self) -> int:
        """Snapshot the selected elements (+ their fully-internal links)
        into the paste buffer. Returns the element count."""
        if self._rbd_ref is None:
            return 0
        sel_b = [bid for bid, it in self._block_items.items()
                 if it.isSelected() and bid != 0]
        sel_n = [nid for nid, it in self._node_items.items()
                 if it.isSelected() and self.nodes[nid].type_node == 2]
        sel_e = [eid for eid, it in self._event_items.items()
                 if it.isSelected()]

        # Selected text/rect/ellipse overlays travel with the copy as
        # full entry dicts.
        sel_annos = [dict(a) for aid, it in
                     getattr(self, "_anno_items", {}).items()
                     if it.isSelected()
                     for a in getattr(self, "annotations", [])
                     if int(a.get("id", 0)) == aid]
        sel_h = [hid for hid, it in
                 getattr(self, "_hier_items", {}).items()
                 if it.isSelected()]
        buffer = plan_copy(self._rbd_ref, self.hiers, blocks=sel_b,
                           nodes=sel_n, events=sel_e, sel_hiers=sel_h,
                           parent=self._current_parent)
        for a in sel_annos:
            buffer.append(("annotation", dict(a)))
        self._paste_buffer = buffer
        return count_elements(buffer)

    def cut_selection(self) -> int:
        """Copy then delete, in one gesture.

        Whether to delete is decided from the SELECTION, not from
        `copy_selection`'s return value: that number is the count of
        copied *elements*, which is 0 for an annotation-only selection
        even though the buffer filled fine. Gating the delete on it made
        Cut a silent no-op on any annotation -- the user pressed Cut,
        the note filled the clipboard, and it stayed on the canvas.
        """
        n = self._n_deletable_selected()
        if not n:
            return 0
        self.copy_selection()
        self.delete_selected()
        return n

    def paste(self, at_model=None, offset=(8.0, 6.0)):
        """Re-insert buffered elements with fresh ids; internal links are
        remapped to the new ids. `at_model` anchors the cluster's top-left
        to a model point (context-menu paste). One macro undo step.
        Returns the number of elements pasted."""
        if not self._paste_buffer or self._rbd_ref is None:
            return 0
        parent = self._current_parent

        anno_entries = [r for k, r in self._paste_buffer
                        if k == "annotation"]
        elements = [(k, r) for k, r in self._paste_buffer
                 if k not in ("link", "annotation")]

        # Anchor and target, both in SCENE coordinates, then converted
        # per-kind. This used to compute the anchor from the elements'
        # raw `xPos` (model units) and add that same model-space delta to
        # the annotations' `x`/`y` (SCENE units) -- so a pasted
        # annotation moved by 1/scale of the intended distance, and an
        # annotation-only buffer (no elements to take an anchor from)
        # anchored at 0.0 and moved by the raw model coordinate of the
        # click. Everything below works in scene space and converts once.
        scene_anchors: list[tuple[float, float]] = [
            (self._tx(float(r.get("xPos", 0))),
             self._ty(float(r.get("yPos", 0)))) for _, r in elements]
        for a in anno_entries:
            if a.get("kind") == "arrow":
                # An arrow's own x/y are always 0 -- its geometry lives
                # entirely in the two endpoints (see _AnnoLine), so
                # anchoring it on x/y would put the reference point at
                # the scene origin and shift the copy by the wrong
                # amount.
                scene_anchors.append((float(a.get("x1", 0)),
                                      float(a.get("y1", 0))))
            else:
                scene_anchors.append((float(a.get("x", 0)),
                                      float(a.get("y", 0))))
        anchor_sx = min((p[0] for p in scene_anchors), default=0.0)
        anchor_sy = min((p[1] for p in scene_anchors), default=0.0)
        if at_model is not None:
            target_sx, target_sy = self._model_to_scene(at_model)
        else:
            # Plain paste: anchor at the mouse cursor when it is over
            # the canvas, else the viewport centre -- so Ctrl+V puts
            # the copy right where the user is looking. `offset` nudges
            # it clear of the original so repeated Ctrl+V cascades
            # visibly instead of stacking into an exact overlap.
            from PySide6.QtGui import QCursor
            vp = self.viewport()
            local = vp.mapFromGlobal(QCursor.pos())
            if vp.rect().contains(local):
                sp = self.mapToScene(local)
            else:
                sp = self.mapToScene(vp.rect().center())
            target_sx = sp.x() + offset[0]
            target_sy = sp.y() + offset[1]
        # A plain paste must never land exactly on its own source. The
        # `offset` nudge is 8 scene px, so whenever the cursor (or the
        # viewport centre, headless) sat within that of the source the
        # copy was drawn on top of the original: the user sees nothing
        # happen, and repeated Ctrl+V looks broken. Guarantee a real
        # cascade step instead -- down and to the right, by one block.
        if (abs(target_sx - anchor_sx) < 1e-6
                and abs(target_sy - anchor_sy) < 1e-6):
            target_sx += BLOCK_W / 2.0
            target_sy += BLOCK_H
        sdx = target_sx - anchor_sx
        sdy = target_sy - anchor_sy
        # Element rows are in model units, and y is flipped: a scene
        # delta of +sdy is a model delta of -sdy / scale.
        dx = sdx / self._scale
        dy = -sdy / self._scale
        plan = plan_paste(
            self._rbd_ref, self._paste_buffer,
            {"block": self.blocks, "node": self.nodes,
             "event": self.events, "hier": self.hiers},
            dx=dx, dy=dy, parent=parent,
            parent_level=(self.hiers[parent].level
                          if parent in self.hiers else None))
        new_rows = plan.new_rows

        # Pasted content, not typed input: Raptor 7's PasteObject checks only
        # the edition's block limit and hierarchy depth, not the dialog or
        # link rules, so neither does `rule_gate`.
        with self._stack.ungated():
            self._stack.beginMacro("Paste elements")
            try:
                if new_rows["block"]:
                    self._stack.push(AddCmd(self, "block", new_rows["block"]))
                if new_rows["node"]:
                    self._stack.push(AddCmd(self, "node", new_rows["node"]))
                if new_rows["event"]:
                    self._stack.push(AddCmd(self, "event", new_rows["event"]))
                if new_rows["hier"]:
                    self._stack.push(AddCmd(self, "hier", new_rows["hier"]))
                # Internal links last: both ends must exist when they are added.
                if plan.link_rows:
                    self._stack.push(AddCmd(self, "link", plan.link_rows))
                # Pasted annotations: fresh ids, offset by the SCENE delta
                # (`sdx`/`sdy`, not the model-space `dx`/`dy` the element
                # rows use -- annotation x/y are scene coordinates).
                #
                # Pushed as `AnnoCmd(add=True)` rather than appended to
                # `self.annotations` directly, so it lands in this macro like
                # the element rows. Appending it raw left the copy outside
                # the undo stack: Ctrl+Z after pasting a note removed the
                # elements beside it (or nothing at all) and left the note
                # sitting on the canvas, and a later redo added a second
                # one.
                for a in anno_entries:
                    a2 = dict(a)
                    used_a = {int(x.get("id", 0)) for x in self.annotations}
                    nid = max(used_a | {0}) + 1
                    a2["id"] = nid
                    a2["x"] = round(float(a.get("x", 0)) + sdx, 2)
                    a2["y"] = round(float(a.get("y", 0)) + sdy, 2)
                    if a2.get("kind") == "arrow":
                        a2["x1"] = round(float(a.get("x1", 0)) + sdx, 2)
                        a2["y1"] = round(float(a.get("y1", 0)) + sdy, 2)
                        a2["x2"] = round(float(a.get("x2", 100)) + sdx, 2)
                        a2["y2"] = round(float(a.get("y2", 0)) + sdy, 2)
                    a2["parent"] = parent
                    self._stack.push(AnnoCmd(self, a2, add=True))
            finally:
                self._stack.endMacro()
        # Select the pasted elements.
        self._scene.clearSelection()
        for kind, rows in new_rows.items():
            items = {"block": self._block_items, "node": self._node_items,
                     "event": self._event_items,
                     "hier": getattr(self, "_hier_items", {})}[kind]
            for r in rows:
                it = items.get(_safe_int(r["ID"]))
                if it is not None:
                    it.setSelected(True)
        return len(elements)

    def _delete_annos(self, anno_entries: list, macro: str | None):
        """Push one `AnnoCmd(add=False)` per annotation entry, optionally
        wrapped in a named macro. `macro=None` pushes them individually
        (a caller that is already inside its own macro -- the hierarchy
        dissolve -- must not nest)."""
        if not anno_entries:
            return
        stack = self._stack

        def _push():
            for entry in anno_entries:
                stack.push(AnnoCmd(self, entry, add=False))
        if macro is None:
            _push()
        else:
            stack.beginMacro(macro)
            try:
                _push()
            finally:
                stack.endMacro()

    def delete_selected(self):
        """Delete every selected block/node/event/link/annotation (plus
        any links orphaned by the deletion)."""
        # Selected ANNOTATIONS ride along with everything else. They used
        # to be deleted only when NO block/node/event was also selected,
        # so a mixed selection silently left the notes behind while the
        # context menu said "Delete N selected".
        sel_annos = {int(aid) for aid, it in
                     getattr(self, "_anno_items", {}).items()
                     if it.isSelected()}
        anno_entries = [a for a in getattr(self, "annotations", [])
                        if int(a.get("id", 0)) in sel_annos]
        # pushes one DeleteCmd that removes the underlying file rows,
        # so Save really deletes and Undo restores them.
        if self._rbd_ref is None:
            return
        dead_blocks = {iid for iid, it in self._block_items.items()
                       if it.isSelected() and iid != 0}
        dead_nodes = {iid for iid, it in self._node_items.items()
                      if it.isSelected()
                      # Start/End/In/Out markers are structural (the
                      # manual: "cannot be deleted").
                      and self.nodes[iid].type_node in (2,)}
        dead_events = {iid for iid, it in self._event_items.items()
                       if it.isSelected()}
        dead_link_nums = {it._link_num for it in self._links
                          if it.isSelected() and it._link_num is not None}

        # Selected hierarchy ovals: dissolve (children promote to the
        # enclosing level; oval + markers die; oval-referencing links
        # are cut). Handled as its own undoable command, one per hier.
        sel_hiers = [hid for hid, it in
                     getattr(self, "_hier_items", {}).items()
                     if it.isSelected()]
        if sel_hiers and not (dead_blocks or dead_nodes or dead_events):
            # A hierarchy dissolve on its own. Delete any annotations in
            # the same selection first (not nested -- this is its own
            # top-level undo step, as it always was).
            self._delete_annos(anno_entries,
                               f"Delete {len(anno_entries)} annotation(s)"
                               if anno_entries else None)
            self._dissolve_hierarchies(sel_hiers)
            return
        dead_any = dead_blocks | dead_nodes | dead_events

        block_section = self._rbd_ref.sections.get("BLOCK_DATA")
        node_section = self._rbd_ref.sections.get("NODE_DATA")
        event_section = self._rbd_ref.sections.get("EVENT_DATA")
        link_section = self._rbd_ref.sections.get("LINK_DATA")

        def _rows(sec, id_col, ids):
            return [dict(r) for r in (sec.rows if sec else [])
                    if _safe_int(r.get(id_col)) in ids]

        # Links to remove: explicitly selected or orphaned by the deletion.
        # Match the endpoint TYPE as well as its id: ids are numbered
        # independently per kind, so deleting Node 3 must not also drop a
        # link that touches Block 3 (reported as "earlier links break"
        # when deleting nodes whose ids collide with blocks).
        dead_by_kind = {"Block": dead_blocks, "Node": dead_nodes,
                        "Event": dead_events}

        def _end_dead(row: dict, side: str) -> bool:
            kind = row.get(f"{side}Type", "")
            oid = _safe_int(row.get(f"{side}ID"))
            return oid in dead_by_kind.get(kind, ())

        link_rows = [dict(r) for r in (link_section.rows if link_section else [])
                     if (_safe_int(r.get("ID")) in dead_link_nums
                         or _end_dead(r, "From")
                         or _end_dead(r, "To"))]
        if not (dead_blocks or dead_nodes or dead_events or link_rows
                or anno_entries):
            return
        # Dependency warning: replicate Raptor's behaviour when deleting
        # elements other elements depend on -- its own reset routine is
        # ResetDepNums (Raptor 7, called from ClearObject).
        # Collect the surviving elements whose DependencyNum
        # points at anything in the selection, warn with their names, and
        # -- on confirm -- reset them to independent in the same undoable
        # macro as the deletion, so dangling DependencyNums can never
        # survive a Save.
        reset_entries: list[tuple[str, int, int, str]] = []
        dependent_lines: list[str] = []
        for section, obj_attr in (("BLOCK_DATA", "blocks"),
                                  ("NODE_DATA", "nodes"),
                                  ("EVENT_DATA", "events")):
            sec = self._rbd_ref.sections.get(section)
            if sec is None:
                continue
            for r in sec.rows:
                oid = _safe_int(r.get("ID"))
                dep = _safe_int(r.get("DepNum" if section == "BLOCK_DATA"
                                       else "depNum"))
                if oid in dead_any or dep <= 0 or dep not in dead_any:
                    continue
                obj = getattr(self, obj_attr, {}).get(oid)
                old = (getattr(obj, "dependency_num", dep) if obj else dep,
                       getattr(obj, "dep_type", "") if obj else "")
                reset_entries.append((section, oid, old[0], old[1]))
                name = getattr(obj, "name", None) or r.get(
                    "blockName", r.get("Name", r.get("eventName", oid)))
                target_name = ((getattr(self, "_global_name_index", {})
                                 or {}).get(
                    ("block" if dep in dead_blocks
                     else "node" if dep in dead_nodes else "event", dep))
                    or f"#{dep}")
                dependent_lines.append(f"• {name} — depends on {target_name}")
        if dependent_lines:
            from PySide6.QtWidgets import QMessageBox as _MB
            detail = "\n".join(dependent_lines)
            n = len(reset_entries)
            answer = _MB.warning(
                self,
                "Dependencies on deleted element"
                + ("s" if len(dead_any) != 1 else ""),
                "At least one other element depends on what you are "
                "deleting:\n\n" + detail + "\n\n"
                f"{'They will' if n != 1 else 'It will'} be RESET TO "
                "INDEPENDENT when the deletion is made.\n\n"
                "Delete anyway?",
                _MB.StandardButton.Yes | _MB.StandardButton.No, _MB.StandardButton.No)
            if answer != _MB.StandardButton.Yes:
                return
        # One macro for the whole deletion, so a single Ctrl+Z restores
        # the elements, the links AND the annotations together -- the
        # mixed selection is one gesture and undoes as one.
        n_annos = len(anno_entries)
        label = ("Delete selection + reset dependents" if reset_entries
                 else (f"Delete {n_annos} annotation(s)"
                       if not (dead_blocks or dead_nodes or dead_events
                               or link_rows)
                       else "Delete selection"))
        self._stack.beginMacro(label)
        try:
            if reset_entries:
                self._stack.push(DepResetCmd(self, reset_entries))
            has_elements = bool(dead_blocks or dead_nodes or dead_events
                                or link_rows)
            if has_elements:
                self._stack.push(DeleteCmd(
                    self,
                    _rows(block_section, "ID", dead_blocks),
                    _rows(node_section, "ID", dead_nodes),
                    _rows(event_section, "ID", dead_events),
                    link_rows))
            self._delete_annos(anno_entries, macro=None)
        finally:
            self._stack.endMacro()
        if n_annos:
            _view_status(self, f"Deleted {n_annos} annotation(s)"
                         + (" and the selected elements" if has_elements
                            else ""))

    def remove_links_selected(self):
        """Delete every link touching a selected block/node/event/
        hierarchy without deleting the elements themselves -- unlike
        delete_selected, which takes the elements (and their links) with
        it."""
        if self._rbd_ref is None:
            return
        endpoints = set()
        for kind, items in (("Block", self._block_items),
                            ("Node", self._node_items),
                            ("Event", self._event_items),
                            ("Hier", getattr(self, "_hier_items", {}))):
            for oid, it in items.items():
                if it.isSelected():
                    endpoints.add((kind, oid))
        if not endpoints:
            return

        def touches(link) -> bool:
            f = (link.connect_f_ref.replace("RBD", "", 1),
                 link.connect_from_id)
            t = (link.connect_t_ref.replace("RBD", "", 1),
                 link.connect_to_id)
            return f in endpoints or t in endpoints

        dead_link_nums = {link.object_num for link in self.links
                          if touches(link)}
        if not dead_link_nums:
            _view_status(self, "No links to remove")
            return
        link_section = self._rbd_ref.sections.get("LINK_DATA")
        link_rows = [dict(r) for r in (link_section.rows if link_section else [])
                     if _safe_int(r.get("ID")) in dead_link_nums]
        self._stack.push(DeleteCmd(self, [], [], [], link_rows))
        _view_status(self, f"Removed {len(dead_link_nums)} link(s)")

    def _remove_ids(self, dead_blocks, dead_nodes, dead_events, dead_link_nums):
        """Shared removal: drops file rows, canvas items, and model
        entries (used by DeleteCmd.redo and AddCmd.undo)."""
        block_section = req(self._rbd_ref).sections.get("BLOCK_DATA")
        node_section = req(self._rbd_ref).sections.get("NODE_DATA")
        event_section = req(self._rbd_ref).sections.get("EVENT_DATA")
        link_section = req(self._rbd_ref).sections.get("LINK_DATA")

        _dead_any = dead_blocks | dead_nodes | dead_events

        def _drop_rows(sec, id_col, ids):
            if sec is not None and ids:
                sec.rows[:] = [r for r in sec.rows
                               if _safe_int(r.get(id_col)) not in ids]

        _drop_rows(block_section, "ID", dead_blocks)
        _drop_rows(node_section, "ID", dead_nodes)
        _drop_rows(event_section, "ID", dead_events)
        if link_section is not None and dead_link_nums:
            link_section.rows[:] = [r for r in link_section.rows
                            if _safe_int(r.get("ID")) not in dead_link_nums]
        # A link explicitly selected and deleted (not just orphaned by a
        # block/node/event deletion) has both endpoints still alive, so
        # the endpoint-aliveness filter alone would keep redrawing it --
        # self.links must drop it by object_num here too.
        if dead_link_nums:
            self.links[:] = [link for link in self.links
                             if link.object_num not in dead_link_nums]
        for iid in dead_blocks:
            item: Any
            item = self._block_items.pop(iid, None)
            if item is not None:
                self._scene.removeItem(item)
            self.blocks.pop(iid, None)
        for iid in dead_nodes:
            item = self._node_items.pop(iid, None)
            if item is not None:
                self._scene.removeItem(item)
            self.nodes.pop(iid, None)
        for iid in dead_events:
            item = self._event_items.pop(iid, None)
            if item is not None:
                self._scene.removeItem(item)
            self.events.pop(iid, None)
        self._rebuild_link_items()
        self._recount_link_ends()
        if not self._loading:
            self.model_changed.emit()

    def _rebuild_link_items(self):
        """Recreate every _LinkItem from the live links list, dropping
        links whose endpoints are gone. Endpoint liveness is checked per
        type -- ids collide across collections (a node and a deleted
        block can both be id 1)."""
        def alive(ref: str, oid: int) -> bool:
            # LINK_DATA stores "Block"/"Node"/..., but link_from_row maps
            # refs to the model class names ("RBDBlock", ...) -- match both.
            if ref in ("Block", "RBDBlock"):
                return oid in self.blocks
            if ref in ("Event", "RBDEvent"):
                return oid in self.events
            if ref in ("Hier", "RBDHier"):
                return oid in self.hiers  # boundary links reference the oval
            return oid in self.nodes  # Node / RBDNode

        self.links[:] = [
            existing_link for existing_link in self.links
            if alive(existing_link.connect_f_ref, existing_link.connect_from_id)
            and alive(existing_link.connect_t_ref, existing_link.connect_to_id)]
        for item in list(self._scene.items()):
            if isinstance(item, _LinkItem):
                self._scene.removeItem(item)
        self._links[:] = []
        for link in self.links:
            src = self._resolve_link_endpoint(
                link.connect_f_ref, link.connect_from_id)
            dst = self._resolve_link_endpoint(
                link.connect_t_ref, link.connect_to_id)
            if src is not None and dst is not None:
                li = _LinkItem(src, dst, link_num=link.object_num)
                self._links.append(li)
                self._scene.addItem(li)
        # Ports depend on an element's other links (its incoming side
        # fixes its outgoing side), so recompute every route once they
        # all exist rather than leaving the earlier ones with a partial
        # picture of the topology.
        for li in self._links:
            li._update_route()

    def scene_to_model(self, scene_pos: QPointF) -> tuple[float, float]:
        return self._vt.to_model(scene_pos.x(), scene_pos.y())

    def _model_to_scene(self, model_xy: tuple[float, float]) \
            -> tuple[float, float]:
        """Inverse of `scene_to_model` -- the same `_tx`/`_ty` the
        loader uses. `paste` needs it to turn a context-menu click's
        model coordinate into the scene space annotations are stored in
        (a model coordinate written into an annotation lands it in the
        wrong place by the transform scale)."""
        return (self._tx(model_xy[0]), self._ty(model_xy[1]))

    #: Emitted when the connect tool needs the app to name a new element
    #: or when an element was created (id, kind) -- MainWindow refreshes
    #: the Explorer/Data Tables.
    element_created = Signal(str, int)

    def _element_at(self, scene_pos: QPointF):
        """Topmost element at a scene point, with a nearest-within-15px
        fallback -- integer-rounded view->scene mapping can miss a shape
        by a couple of pixels, which made Connect clicks silently fail.

        The fallback measures true squared distance from `scene_pos` to
        each item's own rect (0 if already inside it, else the distance
        to its nearest edge/corner). `_LinkItem._anchor_geom` supplies
        that rect -- for a node, its real circle-bounding square, not the
        badge-skewed `sceneBoundingRect()`."""
        for it in self._scene.items(scene_pos):
            if isinstance(it, (_BlockItem, _NodeItem, _EventItem,
                               _HierItem)):
                return it
        best, best_d = None, 15 * 15
        for it in (*self._block_items.values(), *self._node_items.values(),
                   *self._event_items.values(),
                   *getattr(self, "_hier_items", {}).values()):
            rect = _LinkItem._anchor_geom(it)[0]
            dx = max(rect.left() - scene_pos.x(), 0.0,
                     scene_pos.x() - rect.right())
            dy = max(rect.top() - scene_pos.y(), 0.0,
                     scene_pos.y() - rect.bottom())
            d = dx * dx + dy * dy
            if d < best_d:
                best, best_d = it, d
        return best

    #: Cursor shown per build tool. Built from painted glyphs, theme-aware.
    _TOOL_CURSORS: dict[str, Any] = {}

    @property
    def tool_mode(self) -> str:
        return self._tool_mode

    @tool_mode.setter
    def tool_mode(self, mode: str):
        changed = mode != self._tool_mode
        self._tool_mode = mode
        if mode != "select":
            self._delete_armed = False
            self._fev_armed_mode = None
        try:
            self._apply_tool_cursor()
        except RuntimeError:
            pass
        if changed:
            # The view disarms tools on its own initiative too -- Esc,
            # and the end of a one-shot placement like the text tool.
            # Without this the ribbon button stayed lit after the tool
            # was no longer armed, so the next canvas click silently
            # placed another shape.
            self.tool_mode_changed.emit(mode)

    def _sync_item_cursors(self, tool_cursor) -> None:
        """Every element shows the armed tool's cursor (the Connect pen,
        the delete cross...) or, with no tool armed, the pointing hand.
        An item's own cursor overrides the canvas's, and items set theirs
        once, when built -- so over a block the Connect cursor turned into
        a hand, just where a link starts or ends."""
        from PySide6.QtGui import QCursor
        hand = QCursor(Qt.CursorShape.PointingHandCursor)
        for items in (self._block_items, self._node_items,
                      self._event_items, self._hier_items):
            for item in items.values():
                item.setCursor(tool_cursor if tool_cursor is not None
                               else hand)

    def _apply_tool_cursor(self):
        from PySide6.QtGui import QCursor, QPixmap, QPainter, QColor
        from raptor.gui.resources.design_tokens import ThemeManager
        from raptor.gui.resources import icons as _ic
        c = ThemeManager.instance().colour["accent"]
        mode = self.tool_mode
        fev_mode = getattr(self, "_fev_armed_mode", None)
        if fev_mode == "click":
            # Failure Effects View is open: a plain click acts on the element,
            # so the pointer stays the ordinary arrow.
            self.viewport().unsetCursor()
            self._sync_item_cursors(None)
            return
        if mode == "select" and not getattr(self, "_delete_armed", False) \
                and fev_mode is None:
            self.viewport().unsetCursor()
            self._sync_item_cursors(None)
            return
        pm = QPixmap(32, 32)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        try:
            if getattr(self, "_delete_armed", False):
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon("#D00000").pixmap(32, 32))
                p.setPen(QPen(QColor("#D00000"), 3))
                p.drawLine(20, 6, 30, 16)
                p.drawLine(30, 6, 20, 16)
            elif fev_mode == "fail":
                fc = ThemeManager.instance().colour["failed"]
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(fc).pixmap(32, 32))
                p.setPen(QPen(QColor(fc), 3))
                p.drawLine(20, 6, 30, 16)
                p.drawLine(30, 6, 20, 16)
            elif fev_mode == "repair":
                oc = ThemeManager.instance().colour["operating"]
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(oc).pixmap(32, 32))
                p.setPen(QPen(QColor(oc), 3))
                p.drawLine(19, 11, 24, 16)
                p.drawLine(24, 16, 31, 5)
            elif mode == "add_block":
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(c).pixmap(32, 32))
                p.setBrush(QColor(c)); p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(22, 4, 9, 7)
            elif mode == "add_node":
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(c).pixmap(32, 32))
                p.setBrush(QColor(c)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(21, 3, 11, 11)
            elif mode == "add_event":
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(c).pixmap(32, 32))
                p.setPen(QPen(QColor(c), 2))
                p.drawLine(21, 10, 31, 4); p.drawLine(26, 1, 26, 13)
            elif mode == "add_hierarchy":
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(c).pixmap(32, 32))
                p.setPen(QPen(QColor(c), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(19, 2, 14, 8)
            elif mode == "anno_text":
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(c).pixmap(32, 32))
                f = QFont("Georgia", 13); f.setBold(True)
                p.setFont(f); p.setPen(QPen(QColor(c), 1))
                p.drawText(QPointF(19, 16), "T")
            elif mode == "anno_rect":
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(c).pixmap(32, 32))
                p.setPen(QPen(QColor(c), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(20, 3, 11, 9)
            elif mode == "anno_ellipse":
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(c).pixmap(32, 32))
                p.setPen(QPen(QColor(c), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(20, 3, 12, 9)
            elif mode == "anno_arrow":
                p.drawPixmap(0, 0, 32, 32,
                             _ic.mouse_arrow_icon(c).pixmap(32, 32))
                p.setPen(QPen(QColor(c), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(18, 14, 30, 2)
                p.setBrush(QColor(c)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(27, -1, 6, 6)
            else:   # connect
                p.setPen(QPen(QColor(c), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(6, 26, 26, 6)
                p.setBrush(QColor(c)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(2, 22, 8, 8)
                p.drawEllipse(24, 2, 8, 8)
        finally:
            p.end()
        self.viewport().setCursor(QCursor(pm))
        self._sync_item_cursors(QCursor(pm))

    def set_delete_armed(self, armed: bool):
        """Del with nothing selected arms click-to-delete."""
        self._delete_armed = armed
        if armed:
            self.tool_mode = "select"   # reuse select plumbing
            self._fev_armed_mode = None   # mutually exclusive arm states
        self._apply_tool_cursor()

    def set_fev_view_open(self, open_: bool) -> None:
        """Failure Effects View on/off. While open, a plain click on a block
        or event is Raptor 7's left click (fail / restore / cycle)."""
        self._fev_view_open = bool(open_)
        self.set_fev_armed("click" if open_ else None)

    def set_fev_armed(self, mode: str | None):
        """Failure Effects action-first order: clicking Fail/Repair with
        nothing selected arms click-to-target, same shape as
        `set_delete_armed`. `mode` is "fail"/"repair"/"click"/None."""
        self._fev_armed_mode = mode
        if mode is not None:
            self.tool_mode = "select"   # reuse select plumbing
            self._delete_armed = False   # mutually exclusive arm states
        self._apply_tool_cursor()

    def _endpoint_name(self, kind: str, oid: int) -> str:
        pools: dict[str, dict[int, Any]] = {
            "Block": self.blocks, "Node": self.nodes,
            "Event": self.events, "Hier": self.hiers}
        pool = pools.get(kind, {})
        obj = pool.get(oid)
        return getattr(obj, "name", "") or f"{kind} {oid}"

    def _connect_refusal_reason(self, src_kind: str, src_id: int,
                                kind: str, eid: int) -> str | None:
        """Connection rules enforced at draw time instead of failing at
        Run: a block/event has exactly one outgoing link, and an
        identical A->B pair is a duplicate. Returns the status-bar
        message, or None when allowed."""
        src_name = self._endpoint_name(src_kind, src_id)
        existing_out = next((l for l in self.links
                             if l.connect_f_ref.replace("RBD", "", 1)
                             == src_kind
                             and l.connect_from_id == src_id), None)
        if existing_out is not None \
                and existing_out.connect_t_ref.replace("RBD", "", 1) == kind \
                and existing_out.connect_to_id == eid:
            return (f"{src_name} is already connected to "
                    f"{self._endpoint_name(kind, eid)}")
        if src_kind in ("Block", "Event") and existing_out is not None:
            target = self._endpoint_name(
                existing_out.connect_t_ref.replace("RBD", "", 1),
                existing_out.connect_to_id)
            return (f"{src_name} already has an outgoing link to {target} — "
                    f"a block has exactly one output (delete it first)")
        return None

    def _handle_tool_click(self, scene_pos: QPointF):
        """Build-mode clicks: place a new element, or complete a link."""
        if self.tool_mode == "anno_text":
            item = self._begin_annotation("text", scene_pos)
            self._scene.clearSelection()
            if item is not None:
                item.setSelected(True)
            # One-shot: the tool is spent. The user asked for the icon
            # to have to be clicked again for the next text box, so
            # every click after this one moves/selects instead of
            # dropping another empty box on the canvas. Disarmed through
            # the property so the ribbon button unchecks too.
            self.tool_mode = "select"
            _view_status(self, "Text annotation added — type your text, "
                         "drag to move, corner handle to resize, "
                         "right-click for colours/font. Click the Text "
                         "tool again for another box.")
            return
        if self.tool_mode in ("anno_rect", "anno_ellipse"):
            # Two-click placement, same gesture as the arrow tool: first
            # click places one corner and arms the tool; the ghost shape
            # (drawn in mouseMoveEvent) previews the box up to the
            # cursor; the second click places the opposite corner and
            # commits.
            kind = self.tool_mode[5:]
            if self._shape_anno_from is None:
                self._shape_anno_from = scene_pos
                _view_status(self, f"{kind.capitalize()}: click to set "
                             "the opposite corner (Esc to cancel)")
                return
            p1 = self._shape_anno_from
            self._shape_anno_from = None
            self._drop_shape_ghost()
            item = self._begin_annotation_shape(kind, p1, scene_pos)
            self._scene.clearSelection()
            item.setSelected(True)
            # Spent, as with the text tool: the second click of the
            # two-click gesture completes the shape, it does not arm
            # another one.
            self.tool_mode = "select"
            _view_status(self, f"{kind.capitalize()} added — drag to "
                         "move, corner handle to resize, right-click "
                         "for colours/font")
            return
        if self.tool_mode == "anno_arrow":
            # First click places the tail and arms the tool; the ghost
            # line (drawn in mouseMoveEvent) previews the shaft up to
            # the cursor; the second click places the head and commits
            # the annotation.
            if self._arrow_from is None:
                self._arrow_from = scene_pos
                _view_status(self, "Arrow: click to place the head "
                             "(Esc to cancel)")
                return
            tail = self._arrow_from
            self._arrow_from = None
            self._drop_arrow_ghost()
            item = self._begin_annotation_arrow(tail, scene_pos)
            self._scene.clearSelection()
            item.setSelected(True)
            self.tool_mode = "select"
            _view_status(self, "Arrow added — drag either end to "
                         "adjust, right-click for colour/line style")
            return
        if self.tool_mode in ("add_block", "add_node", "add_event",
                              "add_hierarchy"):
            mx, my = self.scene_to_model(scene_pos)
            if self.tool_mode == "add_block":
                bid = self.add_block(f"Block_{len(self.blocks) + 1}", mx, my)
                self.element_created.emit("block", bid)
            elif self.tool_mode == "add_event":
                eid_ = self.add_event("P=0.5", mx, my)
                self.element_created.emit("event", eid_)
            elif self.tool_mode == "add_hierarchy":
                hid = self.add_hierarchy(
                    f"Hierarchy_{len(self.hiers) + 1}", mx, my)
                self.element_created.emit("hier", hid)
            else:
                nid = self.add_node(f"node_{len(self.nodes) + 1}", mx, my)
                self.element_created.emit("node", nid)
            return
        if self.tool_mode == "connect":
            item = self._element_at(scene_pos)
            if item is None:
                self._connect_from = None
                return
            if isinstance(item, _BlockItem):
                kind, eid = "Block", item._block_id
            elif isinstance(item, _NodeItem):
                kind, eid = "Node", item._node_id
            elif isinstance(item, _HierItem):
                kind, eid = "Hier", item._hier_id
            else:
                kind, eid = "Event", item._event_id
            if self._connect_from is None:
                self._connect_from = (kind, eid)
                _view_status(self, f"Linking from "
                             f"{self._endpoint_name(kind, eid)} — "
                             f"click (left or right) the target element")
                return
            src_kind, src_id = self._connect_from
            if (src_kind, src_id) != (kind, eid):
                reason = self._connect_refusal_reason(src_kind, src_id,
                                                      kind, eid)
                if reason:
                    _view_status(self, reason)
                    self._connect_from = None
                    return
                created = self.connect_elements(src_kind, src_id,
                                                kind, eid)
                if created:
                    _view_status(self, f"Connected "
                                 f"{self._endpoint_name(src_kind, src_id)}"
                                 f" -> {self._endpoint_name(kind, eid)}")
                self._connect_from = None

    def fit_all(self):
        """Zoom to fit the actual content bounding box. Computed fresh
        from the current scene items, not the cached `_content_rect`
        (only refreshed by `_apply_scene_layout` on a full rebuild, so it
        can go stale after an ad hoc drag) and not the scene's own
        `sceneRect()` (padded far past the content for infinite-canvas
        panning)."""
        content = QRectF()
        for it in self._scene.items():
            if isinstance(it, _GridBackground):
                continue
            content = content.united(it.sceneBoundingRect())
        if content.isEmpty():
            return
        self._user_zoomed = False
        self.fitInView(content.adjusted(-30, -30, 30, 30), Qt.AspectRatioMode.KeepAspectRatio)
        self._apply_lod()

    def _apply_scene_layout(self):
        # Measure elements only: strip old grids first, or the world-
        # sized grid from the previous build pollutes the bounding rect
        # and fitInView zooms out to ant-scale (blocks tiny, miles
        # apart).
        for it in list(self._scene.items()):
            if isinstance(it, _GridBackground):
                self._scene.removeItem(it)
        content = QRectF()
        for it in self._scene.items():
            content = content.united(it.sceneBoundingRect())
        if content.isEmpty():
            return
        log.debug("_apply_scene_layout: content=%s (w=%s h=%s)",
                  content, content.width(), content.height())
        # Fit target = the elements themselves + a slim margin.
        padded = content.adjusted(-30, -30, 30, 30)
        # Infinite canvas: the scene rect extends far beyond the model so
        # middle-drag panning always has room (a content-sized scene left
        # zero scrollbar range at fit-zoom -- pan felt dead). Zoom-to-fit
        # still frames `padded`, not the world.
        # World span: content + generous panning margin, capped -- an
        # uncapped world on a tightly-packed file at high scale produced
        # a multi-million-pixel scene that segfaulted real display
        # drivers. Only the added margin is capped; `padded` itself is
        # unbounded if `content` is huge.
        span = min(6000.0,
                   max(padded.width(), padded.height()) * 2.0)
        world = padded.adjusted(-span, -span, span, span)
        log.debug("_apply_scene_layout: span=%s world=%s (w=%s h=%s)",
                  span, world, world.width(), world.height())
        self._content_rect = QRectF(padded)
        self._scene.setSceneRect(world)

        # A slightly coarser grid than a dense default.
        step = max(84, int(min(padded.width(), padded.height()) / 10 / 60) * 60)
        for it in self._scene.items():
            if isinstance(it, _GridBackground):
                self._scene.removeItem(it)
        # Drawn grid pitch: prefer the model's own authored spacing
        # (scaled to scene pixels) over the heuristic `step*5` fallback,
        # so every legacy element's row/column sits on a grid line
        # instead of the grid being an unrelated backdrop. x and y
        # spacing are kept independent (a file's horizontal and vertical
        # spacing routinely differ). Below ~20px the grid would be denser
        # than useful -- fall back to the heuristic pitch for that axis.
        fallback_grid = step * 5
        x_grid = self._model_dx_min * self._scale \
            if getattr(self, "_model_dx_min", 0.0) > 0 else 0.0
        y_grid = self._model_dy_min * self._scale \
            if getattr(self, "_model_dy_min", 0.0) > 0 else 0.0
        x_grid = x_grid if x_grid >= 20.0 else fallback_grid
        y_grid = y_grid if y_grid >= 20.0 else fallback_grid
        log.debug("_apply_scene_layout: step=%s fallback_grid=%s "
                  "x_grid=%s y_grid=%s -- building _GridBackground",
                  step, fallback_grid, x_grid, y_grid)
        # Snap-to-grid (ribbon-toggleable): drag positions align to the
        # same grid that's actually drawn -- not the unrelated heuristic
        # `step` value, which knocked a moved element off the rows/
        # columns the rest were sitting on. Applies only to new user
        # moves -- file positions are never rewritten on open.
        #
        # The snap increment is a fraction of the drawn grid line pitch
        # (SNAP_SUBDIVISIONS), not the full pitch. A full x_grid/y_grid
        # is still exactly SNAP_SUBDIVISIONS snap-steps away, so existing
        # rows/columns stay reachable; there's just finer control getting
        # there.
        self._x_grid_current = x_grid
        self._y_grid_current = y_grid
        snap_on = getattr(self, "snap_enabled", True)
        self._snap_state["x"] = (x_grid / SNAP_SUBDIVISIONS) if snap_on else 0.0
        self._snap_state["y"] = (y_grid / SNAP_SUBDIVISIONS) if snap_on else 0.0
        # Grid covers the whole world so panning never hits blank space.
        self._scene.addItem(_GridBackground(world, x_grid, y_grid))
        log.debug("_apply_scene_layout: grid built, calling fitInView")

        self._user_zoomed = False
        self.fitInView(padded, Qt.AspectRatioMode.KeepAspectRatio)
        # Fresh items were just built; forget the cached LOD state so
        # `_apply_lod` re-applies it to the new labels.
        self._lod_labels_visible = None
        self._apply_lod()
        log.debug("_apply_scene_layout: fitInView done")

    def wheelEvent(self, event):
        # Plain wheel = zoom in/out toward the cursor; middle-button drag
        # pans; left-drag rubber-band selects.
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom_by(factor)

    # Middle-click-drag panning: the rubber-band drag mode owns the left
    # button (multi-select) and Ctrl+wheel owns zoom, so the middle button
    # is free for grab-and-pan -- the standard CAD-tool convention.
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            for it in self._scene.items(scene_pos):
                if isinstance(it, _HierItem):
                    self.enter_level(it._hier_id)
                    event.accept()
                    return
                if isinstance(it, _NodeItem) and it._node_type in (4, 5):
                    # Double-clicking an In or Out marker ascends
                    self.go_up()
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    def _connect_source_item(self):
        kind, oid = self._connect_from or ("", 0)
        return {"Block": self._block_items, "Node": self._node_items,
                "Event": self._event_items,
                "Hier": getattr(self, "_hier_items", {})}.get(
                    kind, {}).get(oid)

    def _drop_connect_ghost(self):
        if self._connect_ghost is not None:
            self._scene.removeItem(self._connect_ghost)
            self._connect_ghost = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_anchor = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            # A tool in the middle of placing owns the right-click: it
            # cancels that placement and does NOT also open the context
            # menu. This covers Connect (even over a valid target -- a
            # right-click is a cancel, not a second way to complete the
            # link) and every Add tool.
            placing_tools = ("connect", "add_block", "add_node",
                             "add_event", "add_hierarchy")
            if self.tool_mode in placing_tools:
                self._drop_connect_ghost()
                self._connect_from = None
                kind = {
                    "connect": "Connect",
                    "add_block": "Block",
                    "add_node": "Node",
                    "add_event": "Event",
                    "add_hierarchy": "Hierarchy",
                }[self.tool_mode]
                self.tool_mode = "select"
                _view_status(self, f"{kind} placement cancelled")
                event.accept()
                return
            if self.tool_mode != "select" or \
                    getattr(self, "_delete_armed", False):
                # An in-progress GESTURE owns the right-click: it
                # cancels, and the context menu does not ALSO open. The
                # old code cleared the ghost and the armed point and
                # then carried on to `_show_context_menu`, so
                # right-clicking halfway through an arrow or a rectangle
                # both threw the gesture away and popped a menu over the
                # canvas the user was still working on.
                placing = (self._arrow_from is not None
                           or self._shape_anno_from is not None)
                armed_kind = ""
                if self._arrow_from is not None:
                    armed_kind = "Arrow"
                elif self._shape_anno_from is not None:
                    armed_kind = self.tool_mode[5:].capitalize()
                self.tool_mode = "select"
                self.set_delete_armed(False)
                # Right-click also cancels an in-progress arrow
                # placement (armed tail + ghost), same as Escape --
                # without this, tool_mode switching to "select" alone
                # leaves a stale _arrow_from/ghost behind (the setter
                # doesn't clear either), which would silently reuse a
                # long-stale tail position if the tool were re-armed.
                self._drop_arrow_ghost()
                self._arrow_from = None
                # ...and an in-progress rect/ellipse placement, same idea.
                self._drop_shape_ghost()
                self._shape_anno_from = None
                if placing:
                    _view_status(self,
                                 f"{armed_kind} placement cancelled")
                    event.accept()
                    return
            self._show_context_menu(event)
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton and self._delete_armed):
            item = self._element_at(self.mapToScene(
                event.position().toPoint()))
            if item is not None and not item.isSelected():
                item.setSelected(True)
            self.delete_selected()
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and getattr(self, "_fev_armed_mode", None) is not None):
            item = self._element_at(self.mapToScene(
                event.position().toPoint()))
            mode = self._fev_armed_mode
            if isinstance(item, _BlockItem):
                self.fev_target_clicked.emit(item._block_id, mode)
            elif isinstance(item, _EventItem) and mode == "click":
                self.fev_target_clicked.emit(item._event_id, "event")
            elif mode != "click":
                _view_status(self, "Failure Effects only targets blocks "
                             "— click a block")
            if mode == "click" and item is None:
                super().mousePressEvent(event)       # rubber band, pan
                return
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool_mode in ("add_block", "add_node", "add_event",
                                       "add_hierarchy", "connect",
                                       "anno_text", "anno_rect",
                                       "anno_ellipse", "anno_arrow")):
            # QPointF has no mapToScene overload (only QPoint).
            self._handle_tool_click(self.mapToScene(event.position().toPoint()))
            if self.tool_mode == "connect":
                self._drop_connect_ghost()
            if self.tool_mode == "anno_arrow":
                self._drop_arrow_ghost()
            if self.tool_mode in ("anno_rect", "anno_ellipse"):
                self._drop_shape_ghost()
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool_mode == "select"):
            # Capture pre-drag positions of the current selection (after
            # super() updates selection) so mouseReleaseEvent can push a
            # single MoveCmd for the whole gesture.
            super().mousePressEvent(event)
            self._drag_start = []
            self._press_viewport_pos = event.position()
            self._drag_armed = False
            iid: Any
            it: Any
            for iid, it in self._block_items.items():
                if it.isSelected():
                    p = it.scenePos()
                    self._drag_start.append(("block", iid, (p.x(), p.y())))
            for iid, it in self._node_items.items():
                if it.isSelected():
                    p = it.scenePos()
                    self._drag_start.append(("node", iid, (p.x(), p.y())))
            for iid, it in self._event_items.items():
                if it.isSelected():
                    p = it.scenePos()
                    self._drag_start.append(("event", iid, (p.x(), p.y())))
            # Hierarchy ovals drag like everything else (ItemIsMovable) --
            # capturing them here makes the release push one MoveCmd for
            # the whole gesture, same as blocks/nodes/events.
            for iid, it in self._hier_items.items():
                if it.isSelected():
                    p = it.scenePos()
                    self._drag_start.append(("hier", iid, (p.x(), p.y())))
            # Annotations drag/resize like everything else; capture their
            # full pre-gesture state so the release can push an undoable
            # AnnoEditCmd. Includes the annotation under the press even
            # if Qt's own selection handling hasn't marked it selected
            # yet.
            for aid, it in getattr(self, "_anno_items", {}).items():
                if it.isSelected():
                    state = self._anno_state(aid)
                    if state is not None:
                        self._drag_start.append(("anno", aid, state))
            hit = self.itemAt(event.position().toPoint())
            # Arm the direction-aware band only when the press did NOT
            # land on something movable -- otherwise Qt is starting an
            # item move, not a rubber band, and `_band_anchor` must
            # stay None so `_apply_band_selection` stays out of it.
            # Pressing a link or the grid background counts as empty
            # canvas (neither is movable), matching what Qt itself
            # treats as a band drag.
            self._band_anchor = (
                None if (hit is not None
                         and hit.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
                else event.position())
            hit_aid = getattr(hit, "_anno_id", None)
            if hit_aid is not None and not any(
                    k == "anno" and o == hit_aid
                    for k, o, _s in self._drag_start):
                state = self._anno_state(hit_aid)
                if state is not None:
                    self._drag_start.append(("anno", hit_aid, state))
            return

    # -------------------------------------------------- align/distribute
    def _selected_items(self):
        """All selected movable items (blocks, nodes, events, ovals,
        annotations) in selection order."""
        sel = [it for it in self._scene.selectedItems()
               if it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable]
        return sel

    @staticmethod
    @staticmethod
    def _move_by(item, dx, dy):
        item.setPos(item.pos() + QPointF(dx, dy))

    def _layout_state(self) -> tuple[dict, dict | None]:
        """This level's element positions in model units (from the typed
        objects -- call `sync_model_positions` first) and the canvas
        transform: what Auto Layout changes, for its undo record."""
        pos: dict = {}
        for kind, table in (("block", self.blocks), ("node", self.nodes),
                            ("event", self.events), ("hier", self.hiers)):
            for oid, obj in table.items():
                if kind == "hier" and obj.parent_id != self._current_parent:
                    continue
                pos[(kind, oid)] = (obj.x_position, obj.y_position)
        rbd = self._rbd_ref
        vt = getattr(rbd, "view_transform", None) if rbd is not None else None
        return pos, (dict(vt) if vt else None)

    def _restore_layout(self, state: tuple[dict, dict | None]) -> None:
        """Put back a `_layout_state`: the rows and the overlay take the
        positions, the transform is restored (or re-derived from those
        rows), and the level is rebuilt from them."""
        pos, vt = state
        rbd = self._rbd_ref
        if rbd is None:
            return
        cols = {"block": ("BLOCK_DATA", "ID"), "node": ("NODE_DATA", "ID"),
                "event": ("EVENT_DATA", "ID"), "hier": ("HIERARCHY_DATA", "ID")}
        for (kind, oid), (x, y) in pos.items():
            sec = rbd.sections.get(cols[kind][0])
            for row in (sec.rows if sec else []):
                if _safe_int(row.get("ID")) == oid:
                    row["xPos"], row["yPos"] = _pos(x), _pos(y)
                    break
            self._pos_overrides[(kind, oid)] = (x, y)
        rbd.view_transform = dict(vt) if vt else None
        self._compute_model_transform(rbd)
        self._rebuild_scene()
        self._apply_scene_layout()

    def _push_bulk_geometry(self, text, old_positions):
        """Wrap a completed bulk placement (align/distribute/auto-layout)
        in an undo command. `old_positions` is [(item, old QPointF)]."""
        new_positions = [(it, it.pos()) for it, _ in old_positions]
        stack = getattr(self, "_stack", None)
        if stack is not None:
            stack.push(BulkGeometryCmd(self, text, old_positions,
                                       new_positions))
        else:
            self.sync_model_positions()
            self.layout_changed.emit()

    def _anno_state(self, aid: int) -> dict | None:
        """Full undo state for one annotation (entry fields + live
        geometry/text/z), the same shape `set_annotation_props` and
        `AnnoEditCmd` use. Wraps annotation drag/resize and z-order
        changes in a command."""
        item = getattr(self, "_anno_items", {}).get(aid)
        entry = next((dict(a) for a in getattr(self, "annotations", [])
                      if int(a.get("id", 0)) == aid), None)
        if item is None or entry is None:
            return None
        state = dict(entry)
        state.update(item.geometry_snapshot())
        if hasattr(item, "toPlainText"):
            state["text"] = item.toPlainText()
        state["z"] = item.zValue()
        return state

    def align_selected(self, mode: str):
        """mode: left | right | top | bottom | hcenter | vcenter.
        Aligns all selected movable items to the first-selected item
        (the anchor), which matches every drawing package."""
        sel = self._selected_items()
        if len(sel) < 2:
            return
        old_positions = [(it, it.pos()) for it in sel]
        anchor = sel[0]
        ar = anchor.sceneBoundingRect()
        for it in sel[1:]:
            r = it.sceneBoundingRect()
            _cur = it.pos()
            if mode == "left":
                dx = ar.left() - r.left()
                dy = 0
            elif mode == "right":
                dx = ar.right() - r.right()
                dy = 0
            elif mode == "top":
                dx = 0; dy = ar.top() - r.top()
            elif mode == "bottom":
                dx = 0; dy = ar.bottom() - r.bottom()
            elif mode == "hcenter":
                dx = ar.center().x() - r.center().x(); dy = 0
            else:   # vcenter
                dx = 0; dy = ar.center().y() - r.center().y()
            self._move_by(it, dx, dy)
        # _move_by places items directly (no MoveCmd) -- sync the typed
        # objects or the position overlay reverts the alignment on the
        # next level change (same rule auto_layout obeys below). The
        # placement is wrapped in an undo command.
        self.sync_model_positions()
        self._push_bulk_geometry("Align", old_positions)

    def flip_selected(self, axis: str) -> bool:
        """axis: 'h' (mirror left-right) or 'v' (mirror top-bottom).

        Mirrors the arrangement of the selected items about the middle of
        the span of their CENTRES: the item furthest left ends up furthest
        right, one near the top ends up near the bottom. Each item keeps
        its own shape and size (only its position reflects), so a chain
        drawn left-to-right can be turned to read right-to-left without
        redrawing it. Needs two or more items; one undo step. Returns
        whether anything moved.

        The axis comes from the centres, not from the union of the items'
        bounding rectangles: those include per-item badges and labels, so
        the union changes after a flip and a second flip would mirror about
        a different line and drift. Reflecting the centres is an exact
        involution -- flipping twice restores the layout."""
        sel = self._selected_items()
        if len(sel) < 2:
            return False
        horiz = axis == "h"
        centres = [(it.sceneBoundingRect().center().x() if horiz
                    else it.sceneBoundingRect().center().y()) for it in sel]
        mirror = min(centres) + max(centres)          # 2 x the axis
        old_positions = [(it, it.pos()) for it in sel]
        # setPos() fires itemChange() like an interactive drag, which would
        # snap every item to the drag grid and make the result an
        # approximate mirror (and a second flip drift). Suspend the snap for
        # this bulk placement, as Auto Layout does, and put it back.
        snap = dict(self._snap_state)
        self._snap_state["x"] = self._snap_state["y"] = 0.0
        try:
            for it, c in zip(sel, centres):
                delta = mirror - 2 * c                # new centre - old
                self._move_by(it, delta if horiz else 0,
                              0 if horiz else delta)
        finally:
            self._snap_state.update(snap)
        # Placed directly (no MoveCmd): sync the typed objects or the
        # position overlay reverts the flip on the next level change.
        self.sync_model_positions()
        self._push_bulk_geometry(
            "Flip Horizontal" if horiz else "Flip Vertical", old_positions)
        return True

    def distribute_selected(self, axis: str):
        """axis: 'h' or 'v'. Evenly distributes the gaps between >=3
        selected items' edges along that axis, not their centres --
        equal centre-to-centre spacing only reads as even gaps when every
        item is the same size (true for blocks/nodes, not for annotations
        of mixed sizes). The first and last items (by position) anchor
        the span and don't move; every item between is placed so the gap
        after it equals every other gap."""
        sel = self._selected_items()
        if len(sel) < 3:
            return
        old_positions = [(it, it.pos()) for it in sel]
        horiz = axis == "h"

        def edges(it):
            r = it.sceneBoundingRect()
            return (r.left(), r.right()) if horiz else (r.top(), r.bottom())

        keyed = sorted(((edges(it), it) for it in sel),
                       key=lambda kv: kv[0][0])
        span_lo = keyed[0][0][0]
        span_hi = keyed[-1][0][1]
        occupied = sum(hi - lo for (lo, hi), _it in keyed)
        n = len(keyed)
        gap = (span_hi - span_lo - occupied) / (n - 1)
        cursor = span_lo
        for (lo, hi), it in keyed:
            delta = cursor - lo
            if abs(delta) > 1e-6:
                self._move_by(it, delta if horiz else 0,
                             0 if horiz else delta)
            cursor += (hi - lo) + gap
        self.sync_model_positions()   # see align_selected's comment
        self._push_bulk_geometry("Distribute", old_positions)


    def _editing_annotation(self):
        item = self._scene.focusItem()
        if isinstance(item, _AnnoText) and \
                item.textInteractionFlags() & Qt.TextEditorInteraction:
            return item
        return None

    def has_armed(self) -> bool:
        """Something on the canvas that Esc cancels."""
        return (self._editing_annotation() is not None
                or self.tool_mode != "select"
                or getattr(self, "_delete_armed", False)
                or getattr(self, "_fev_armed_mode", None) in ("fail", "repair")
                or getattr(self, "_connect_from", None) is not None)

    def cancel_armed(self) -> None:
        """Esc on the canvas: drop the armed tool, delete mode, a Failure
        Effects fail/repair target, and any half-placed link, arrow or
        shape. The window's Esc calls this directly -- forwarding a key
        press to the canvas never arrived (Qt matches the window's own Esc
        shortcut on the forwarded event first)."""
        editing = self._editing_annotation()
        if editing is not None:
            editing.clearFocus()          # ends the edit (focusOutEvent)
        if getattr(self, "_delete_armed", False):
            self.set_delete_armed(False)
        if getattr(self, "_fev_armed_mode", None) in ("fail", "repair"):
            self.set_fev_armed("click" if self._fev_view_open else None)
        if self.tool_mode != "select":
            self.tool_mode = "select"
        self._drop_connect_ghost()
        self._connect_from = None
        self._drop_arrow_ghost()
        self._arrow_from = None
        self._drop_shape_ghost()
        self._shape_anno_from = None

    def keyPressEvent(self, event):
        # A text annotation being edited owns Backspace/Delete.
        focus = QApplication.focusWidget()
        if isinstance(focus, _AnnoText) and \
                focus.textInteractionFlags() & Qt.TextEditorInteraction:
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if not self._scene.selectedItems() and \
                    not getattr(self, "_delete_armed", False):
                # Del with nothing selected arms click-to-delete.
                self.set_delete_armed(True)
                _view_status(self, "Delete mode: click elements to "
                             "remove them; right-click or Esc to exit")
            else:
                self.delete_selected()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_armed()
            event.accept()
            return
        # Arrow-key nudge: move the current selection without a mouse
        # drag. Plain arrow = one grid step (stays on the grid the rest
        # of the diagram sits on); Shift+arrow = 1px, bypassing snap
        # entirely. Same (kind, id, old, new) shape mouse-drag release
        # uses, so this is undoable exactly like a drag (MoveCmd).
        if event.key() in _NUDGE_DIRS:
            selected = [it for it in self._scene.selectedItems()
                       if isinstance(it, (_BlockItem, _NodeItem, _EventItem,
                                          _HierItem, _AnnotationBase))]
            if selected:
                dxs, dys = _NUDGE_DIRS[event.key()]
                off_grid = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                step_x = 0.0 if off_grid else self._snap_state["x"]
                step_y = 0.0 if off_grid else self._snap_state["y"]
                # itemChange() applies whatever self._snap_state currently
                # holds to every setPos() unconditionally -- an off-grid
                # nudge has to silence it for the duration, or the 1px
                # move below gets immediately re-snapped back onto the
                # grid it was deliberately trying to leave.
                saved_snap = None
                if off_grid:
                    saved_snap = (self._snap_state["x"], self._snap_state["y"])
                    self._snap_state["x"] = self._snap_state["y"] = 0.0
                moves = []
                anno_edits = []
                item: Any
                for item in selected:
                    if isinstance(item, _AnnotationBase):
                        # Annotations aren't grid-snapped, so an
                        # arrow-key nudge moves by one snap increment (or
                        # 1px when snapping is off) rather than to the
                        # next grid line. Arrows shift both endpoints via
                        # the item's own translate_by override.
                        aid = item._anno_id
                        old_state = self._anno_state(aid)
                        if old_state is None:
                            continue
                        adx = dxs * (1.0 if (off_grid or step_x <= 0)
                                     else step_x)
                        ady = dys * (1.0 if (off_grid or step_y <= 0)
                                     else step_y)
                        _shift_annotation(item, adx, ady)
                        new_state = self._anno_state(aid)
                        if new_state is not None and new_state != old_state:
                            anno_edits.append((aid, old_state, new_state))
                        continue
                    kind = ("block" if isinstance(item, _BlockItem)
                           else "node" if isinstance(item, _NodeItem)
                           else "event" if isinstance(item, _EventItem)
                           else "hier")
                    oid = (item._block_id if kind == "block"
                          else item._node_id if kind == "node"
                          else item._event_id if kind == "event"
                          else item._hier_id)
                    old = (item.pos().x(), item.pos().y())
                    # Work in centre space (matches how each item's own
                    # itemChange snaps -- _BlockItem's pos() is its
                    # top-left, not its centre) then convert back.
                    # `_true_centre`, NOT `sceneBoundingRect().center()`:
                    # a node's bounding rect is inflated by the capacity
                    # gauge and the dependency badge, which skews its
                    # reported centre a few px off the circle. `_next_grid_line`
                    # then picked a grid line for that skewed point and
                    # `itemChange`'s snap-to-grid snapped it straight
                    # back onto the line the node already sat on -- so
                    # Up (the direction the skew works against) was a
                    # no-op on nodes.
                    centre = _LinkItem._true_centre(item)
                    offset = QPointF(old[0] - centre.x(), old[1] - centre.y())
                    if off_grid or step_x <= 0:
                        new_cx = centre.x() + dxs * 1.0
                    else:
                        new_cx = (_next_grid_line(centre.x(), step_x, dxs)
                                  if dxs else centre.x())
                    if off_grid or step_y <= 0:
                        new_cy = centre.y() + dys * 1.0
                    else:
                        new_cy = (_next_grid_line(centre.y(), step_y, dys)
                                  if dys else centre.y())
                    item.setPos(new_cx + offset.x(), new_cy + offset.y())
                    new = (item.pos().x(), item.pos().y())
                    if old != new:
                        moves.append((kind, oid, old, new))
                if saved_snap is not None:
                    self._snap_state["x"], self._snap_state["y"] = saved_snap
                for aid, old_state, new_state in anno_edits:
                    self._stack.push(AnnoEditCmd(self, aid, old_state,
                                                 new_state, layout_only=True))
                if moves:
                    self._stack.push(MoveCmd(self, moves))
                event.accept()
                return
        # Scoped clipboard shortcuts: only when the canvas has focus, so
        # text fields keep their own Ctrl+C/V semantics.
        mods = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if mods and self.hasFocus():
            if event.key() == Qt.Key.Key_C:
                self.copy_selection()
                event.accept()
                return
            if event.key() == Qt.Key.Key_X:
                self.cut_selection()
                event.accept()
                return
            if event.key() == Qt.Key.Key_V:
                self.paste()
                event.accept()
                return
        super().keyPressEvent(event)

    #: `(kind, id) -> str | None`; when set, the element under the cursor is described to
    #: `status_message_sink` (Raptor 7's `/dev` hover text).
    hover_inspector: Callable[..., str] | None = None

    def _inspect_under_cursor(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        while item is not None and item.parentItem() is not None:
            item = item.parentItem()
        key = None
        for attr, kind in (("_block_id", "block"), ("_node_id", "node"),
                           ("_event_id", "event"), ("_hier_id", "hier"),
                           ("_link_num", "link")):
            if item is not None and hasattr(item, attr):
                key = (kind, getattr(item, attr))
                break
        if key is None or key == getattr(self, "_inspected", None):
            self._inspected = key
            return
        self._inspected = key
        text = req(self.hover_inspector)(*key)
        sink: Any = getattr(self, "status_message_sink", None)
        if text and sink is not None:
            sink(text)

    def mouseMoveEvent(self, event):
        if self.hover_inspector is not None:
            self._inspect_under_cursor(event)
        if event.buttons() & Qt.MouseButton.MiddleButton and hasattr(self, "_pan_anchor"):
            delta = event.position() - self._pan_anchor
            self._pan_anchor = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - round(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - round(delta.y()))
            event.accept()
            return
        # Connect rubber-band: while a source is armed, draw a line from
        # it to the cursor.
        if self.tool_mode == "connect" and self._connect_from is not None:
            src_item = self._connect_source_item()
            if src_item is not None:
                cur = self.mapToScene(event.position().toPoint())
                # True centre, not sceneBoundingRect().center() -- see
                # _LinkItem._anchor_geom's own docstring for why that's
                # skewed off-centre for a node.
                fr = _LinkItem._anchor_geom(src_item)[1]
                if self._connect_ghost is None:
                    from PySide6.QtGui import QPen as _QPen
                    self._connect_ghost = QGraphicsLineItem()
                    self._connect_ghost.setPen(_QPen(
                        QColor(COLOUR["accent"]), 2, Qt.PenStyle.DashLine))
                    self._connect_ghost.setZValue(15)
                    self._scene.addItem(self._connect_ghost)
                self._connect_ghost.setLine(fr.x(), fr.y(), cur.x(),
                                            cur.y())
        # Arrow rubber-band: same preview pattern as Connect, while the
        # tail is armed and waiting for the head click.
        if self.tool_mode == "anno_arrow" and self._arrow_from is not None:
            cur = self.mapToScene(event.position().toPoint())
            if self._arrow_ghost is None:
                from PySide6.QtGui import QPen as _QPen
                self._arrow_ghost = QGraphicsLineItem()
                self._arrow_ghost.setPen(_QPen(
                    QColor(COLOUR["accent"]), 2, Qt.PenStyle.DashLine))
                self._arrow_ghost.setZValue(15)
                self._scene.addItem(self._arrow_ghost)
            self._arrow_ghost.setLine(self._arrow_from.x(),
                                      self._arrow_from.y(),
                                      cur.x(), cur.y())
        # Rect/ellipse rubber-band: same preview pattern as the arrow
        # tool above, while the first corner is armed.
        if self.tool_mode in ("anno_rect", "anno_ellipse") \
                and self._shape_anno_from is not None:
            cur = self.mapToScene(event.position().toPoint())
            fr = self._shape_anno_from
            x0, y0 = min(fr.x(), cur.x()), min(fr.y(), cur.y())
            w, h = abs(cur.x() - fr.x()), abs(cur.y() - fr.y())
            if self._shape_anno_ghost is None:
                from PySide6.QtGui import QPen as _QPen
                cls = (QGraphicsRectItem if self.tool_mode == "anno_rect"
                      else QGraphicsEllipseItem)
                self._shape_anno_ghost = cls()
                self._shape_anno_ghost.setPen(_QPen(
                    QColor(COLOUR["accent"]), 2, Qt.PenStyle.DashLine))
                self._shape_anno_ghost.setBrush(Qt.BrushStyle.NoBrush)
                self._shape_anno_ghost.setZValue(15)
                self._scene.addItem(self._shape_anno_ghost)
            self._shape_anno_ghost.setRect(x0, y0, w, h)
        if (self.tool_mode == "select" and self._drag_start
                and event.buttons() & Qt.MouseButton.LeftButton
                and not self._drag_armed):
            # See `_CLICK_DRAG_DEADZONE_PX`'s own comment: swallow the
            # move entirely (don't forward to Qt's default handling at
            # all) until the pointer has travelled far enough from
            # press to count as a genuine drag -- a plain click/double-
            # click that only jitters a sub-pixel amount never reaches
            # any item's itemChange at all, so nothing snaps or moves.
            anchor = self._press_viewport_pos
            if anchor is not None and (event.position() - anchor
                    ).manhattanLength() < _CLICK_DRAG_DEADZONE_PX:
                event.accept()
                return
            self._drag_armed = True
        super().mouseMoveEvent(event)
        # Direction-aware rubber band. Qt's built-in
        # `RubberBandDrag` crossed-selects in BOTH directions: dragging
        # a box around five blocks picked up anything it grazed, so it
        # was impossible to select exactly the group you could see.
        # Re-derive the selection from the same rect with a
        # direction-dependent mode AFTER super() has run (super sets the
        # band geometry and its own crossing selection; we only replace
        # the selection, so the band and item-drag behaviour are
        # untouched).
        if self._band_anchor is not None:
            self._apply_band_selection(event)

    def _band_mode(self, pos) -> Qt.ItemSelectionMode:
        """Enclosed-only for a top-left -> bottom-right drag, crossing
        for the other diagonal. Screen coords: y grows downward, so
        "top-left -> bottom-right" is both deltas non-negative."""
        anchor = self._band_anchor
        if anchor is None:
            return Qt.ItemSelectionMode.IntersectsItemShape
        if pos.x() >= anchor.x() and pos.y() >= anchor.y():
            return Qt.ItemSelectionMode.ContainsItemShape
        return Qt.ItemSelectionMode.IntersectsItemShape

    def _apply_band_selection(self, event) -> None:
        anchor = self._band_anchor
        if anchor is None:
            return
        rect = QRectF(anchor, event.position()).normalized()
        # `mapToScene` has no QPointF or QRectF overload (only QPoint,
        # QRect, QPolygon and QPainterPath); the path is the exact
        # rectangle, and maps all four corners through the view
        # transform as it must.
        band_path = QPainterPath()
        band_path.addRect(rect)
        # viewport -> scene. `mapToScene` has no QPointF/QRectF overload
        # and its QPoint/QRect ones are relative to the *view*, not the
        # viewport the event position is in; inverting the view
        # transform is the exact inverse of what the view itself uses
        # and carries no frame-offset guesswork.
        scene_path = self.viewportTransform().inverted()[0].map(band_path)
        mods = event.modifiers()
        # Ctrl toggles, Shift adds -- the same semantics Qt's own
        # rubber band offers via the selection model's flags.
        toggle = bool(mods & Qt.KeyboardModifier.ControlModifier)
        add = toggle or bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if not add:
            for item in self._scene.selectedItems():
                item.setSelected(False)
        for item in self._scene.items(scene_path, self._band_mode(
                event.position())):
            # The shape query returns the elements' label child items
            # too; only the selectable element itself is a selection
            # target (and a block whose own shape is enclosed selects
            # regardless of its label, which is a sibling of nothing).
            if not item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                continue
            if toggle and item.isSelected():
                item.setSelected(False)
            else:
                item.setSelected(True)

    def mouseReleaseEvent(self, event):
        self._band_anchor = None
        if event.button() == Qt.MouseButton.MiddleButton:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self._pan_anchor = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start:
            # A drag gesture just ended: one MoveCmd for every element
            # whose scene position actually changed.
            moves = []
            anno_edits = []
            for kind, oid, start in self._drag_start:
                if kind == "anno":
                    end = self._anno_state(oid)
                    if end is not None and end != start:
                        anno_edits.append((oid, start, end))
                    continue
                item: Any
                item = (self._block_items.get(oid) if kind == "block"
                        else self._node_items.get(oid) if kind == "node"
                        else self._event_items.get(oid) if kind == "event"
                        else self._hier_items.get(oid))
                if item is None:
                    continue
                now = (item.scenePos().x(), item.scenePos().y())
                if (abs(now[0] - start[0]) > 0.5
                        or abs(now[1] - start[1]) > 0.5):
                    moves.append((kind, oid, tuple(start), now))
            drag_start, self._drag_start = self._drag_start, []
            if anno_edits:
                for aid, old_state, new_state in anno_edits:
                    self._stack.push(AnnoEditCmd(self, aid, old_state,
                                                 new_state, layout_only=True))
            if moves:
                # A drag can now carry hierarchy ovals too -- they ride
                # along in the same gesture but never reparent (only
                # blocks/nodes/events transport into a dropped-on oval;
                # `_reparent_into`'s section table has no hier entry).
                element_moves = [m for m in moves if m[0] != "hier"]
                oval_moves = [m for m in moves if m[0] == "hier"]
                drop_hid = self._drop_target_hier(element_moves)
                if drop_hid is not None:
                    # Dropping onto a hierarchy oval transports the whole
                    # selection into it -- positions are not
                    # committed; the child level lays out on its own.
                    self._reparent_into(drop_hid, element_moves)
                    if oval_moves:
                        self._stack.push(MoveCmd(self, oval_moves))
                else:
                    self._stack.push(MoveCmd(self, moves))
            elif len(drag_start) == 1:
                # A plain click (no drag) on a single element centres the
                # view on it, e.g. after panning/zooming left it near an
                # edge. Skipped for a multi-select click (shift-click adds
                # to `_drag_start` too) since there's no single element to
                # centre on, and skipped for items already comfortably
                # on-screen.
                kind, oid, _start = drag_start[0]
                item = (self._block_items.get(oid) if kind == "block"
                        else self._node_items.get(oid) if kind == "node"
                        else self._event_items.get(oid) if kind == "event"
                        else self._anno_items.get(oid) if kind == "anno"
                        else None)
                # Only pan when the element is completely outside the
                # viewport.
                if item is not None:
                    viewport_scene = self.mapToScene(
                        self.viewport().rect()).boundingRect()
                    if not viewport_scene.intersects(
                            item.sceneBoundingRect()):
                        self.centerOn(item)
        super().mouseReleaseEvent(event)

    def _item_comfortably_visible(self, item) -> bool:
        """Whether `item` is at least partially inside the current
        viewport."""
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        return visible.intersects(item.sceneBoundingRect())

    def _drop_target_hier(self, moves) -> int | None:
        """The hierarchy oval the drag gesture ended on (primary moved
        element's new centre inside the oval), if any. `moves` holds
        Element moves only (hierarchy ovals never reparent -- see the
        release handler); empty -> no target."""
        if not moves:
            return None
        kind, oid, _start, now = moves[0]
        table = (self._block_items if kind == "block"
                 else self._node_items if kind == "node"
                 else self._event_items)
        item = table.get(oid)
        if item is None:
            return None
        center = QPointF(now[0], now[1])
        for hid, oval in self._hier_items.items():
            if oval.sceneBoundingRect().contains(center):
                return hid
        return None

    def _reparent_into(self, hid: int, moves):
        """Transport the dragged selection into hierarchy `hid`. Links
        fully inside the selection travel with it; links that would now
        cross the boundary are removed (the tutorial wires In/Out
        markers manually afterwards)."""
        section_of = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                      "event": "EVENT_DATA"}
        parent_moves = []
        moved_by_section: dict[str, set[int]] = {}
        for kind, oid, _start, _now in moves:
            section = section_of[kind]
            moved_by_section.setdefault(section, set()).add(oid)
            sec = req(self._rbd_ref).sections.get(section)
            row = next((r for r in req(sec).rows
                        if _safe_int(r.get("ID")) == oid), None)
            old = row.get(_PARENT_COLS[section], "0") if row else "0"
            if old != str(hid):
                parent_moves.append((section, oid, old))
        removed, internal = [], []
        link_section = req(self._rbd_ref).sections.get("LINK_DATA")
        if link_section is not None:
            for idx, row in enumerate(list(link_section.rows)):
                if _safe_int(row.get("pID")) != self._current_parent:
                    continue
                f_in = self._link_end_in(row, "From", moved_by_section)
                t_in = self._link_end_in(row, "To", moved_by_section)
                if f_in and t_in:
                    internal.append((row, idx))
                elif f_in or t_in:
                    removed.append((row, idx))
        self._stack.push(ReParentCmd(self, hid, parent_moves, removed,
                                     internal))

    def move_out_of_hierarchy(self, oids_by_section: dict) -> bool:
        """Move the given elements out of the current hierarchy level to
        its parent (Raptor 7 out-drag counterpart). Links fully
        inside the moving set travel; boundary links are cut. Returns
        True when anything moved."""
        if self._rbd_ref is None or self._current_parent == 0:
            return False
        target = self.hiers[self._current_parent].parent_id or 0
        parent_moves = []
        moved_by_section: dict[str, set[int]] = {}
        for section, oids in oids_by_section.items():
            sec = self._rbd_ref.sections.get(section)
            if sec is None:
                continue
            for oid in oids:
                row = next((r for r in sec.rows
                            if _safe_int(r.get("ID")) == oid), None)
                old = row.get(_PARENT_COLS[section], "0") if row else "0"
                if str(old) != str(self._current_parent):
                    continue
                parent_moves.append((section, oid, int(old)))
                moved_by_section.setdefault(section, set()).add(oid)
        if not parent_moves:
            return False
        removed, internal = [], []
        link_section = self._rbd_ref.sections.get("LINK_DATA")
        if link_section is not None:
            for idx, row in enumerate(list(link_section.rows)):
                if _safe_int(row.get("pID")) != self._current_parent:
                    continue
                f_in = self._link_end_in(row, "From", moved_by_section)
                t_in = self._link_end_in(row, "To", moved_by_section)
                if f_in and t_in:
                    internal.append((row, idx))
                elif f_in or t_in:
                    removed.append((row, idx))
        self._stack.push(MoveOutCmd(self, target, parent_moves,
                                    removed, internal))
        return True

    def _dissolve_hierarchies(self, hids):
        """Delete hierarchy ovals: promote children to the enclosing
        level, remove markers + oval, cut links that referenced the
        oval or the markers. One DissolveCmd per hier, all inside a
        macro so multi-select undoes together. What changes is planned
        by `hierarchy_plan.plan_dissolve`."""
        n_in = n_out = 0
        self._stack.beginMacro(f"Delete {len(hids)} hierarchy(ies)")
        try:
            for hid in sorted(hids):
                hobj = self.hiers.get(hid)
                if hobj is None:
                    continue
                plan = plan_dissolve(self._rbd_ref, hobj, hid)
                n_in, n_out = plan.n_outside_in, plan.n_outside_out
                self._stack.push(DissolveCmd(
                    self, plan.hier_row, plan.marker_rows,
                    plan.removed_links, plan.parent_rewrites,
                    plan.link_pid_rewrites, plan.rewired_links))
        finally:
            self._stack.endMacro()
        _view_status(self, f"Hierarchy deleted — {len(hids)} dissolved; "
                     f"children promoted; "
                     f"{n_in + n_out} boundary link(s) "
                     f"rewired")

    def _selected_moves(self):
        """The current selection as `_reparent_into`'s `moves` shape
        (kind, oid, ...) -- blocks, non-marker nodes and events."""
        moves = []
        it: Any
        for bid, it in self._block_items.items():
            if it.isSelected() and bid != 0:
                moves.append(("block", bid, None, None))
        for nid, it in self._node_items.items():
            if it.isSelected() and self.nodes[nid].type_node == 2:
                moves.append(("node", nid, None, None))
        for eid, it in self._event_items.items():
            if it.isSelected():
                moves.append(("event", eid, None, None))
        return moves

    def _hier_move_submenu(self, menu):
        """'Move to hierarchy' submenu: one action per hierarchy oval on
        the current level -- Raptor 7's drag-into-oval transport
        (`_reparent_into`) driven from a menu, so a selected group can be
        moved into a hierarchy without a drag. None when this level has
        no hierarchies to move into."""
        if not getattr(self, "hiers", None):
            return None
        sub = menu.addMenu("Move to hierarchy")
        for hid in sorted(self.hiers):
            h = self.hiers[hid]
            sub.addAction(h.name,
                          lambda hid2=hid: self._reparent_selection_into(hid2))
        return sub

    def _reparent_selection_into(self, hid: int):
        moves = self._selected_moves()
        if not moves:
            return
        self._reparent_into(hid, moves)

    def _move_selection_to_parent(self):
        sel_b = [bid for bid, it in self._block_items.items()
                 if it.isSelected() and bid != 0]
        sel_n = [nid for nid, it in self._node_items.items()
                 if it.isSelected() and self.nodes[nid].type_node == 2]
        sel_e = [eid for eid, it in self._event_items.items()
                 if it.isSelected()]
        oids = {}
        if sel_b:
            oids["BLOCK_DATA"] = sel_b
        if sel_n:
            oids["NODE_DATA"] = sel_n
        if sel_e:
            oids["EVENT_DATA"] = sel_e
        if not oids:
            return
        if self.move_out_of_hierarchy(oids):
            self._statusbar_msg = None

    def _link_end_in(self, row, side: str, moved_by_section) -> bool:
        ref = row.get(side + "Type")
        oid = _safe_int(row.get(side + "ID"))
        section = {"Block": "BLOCK_DATA", "Node": "NODE_DATA",
                   "Event": "EVENT_DATA"}.get(ref)
        if section is None:
            return False
        return oid in moved_by_section.get(section, set())

    # --- Element-level phase assignment ------------------

    _PHASE_SECTION = {"block": ("BLOCK_DATA", "ParentId", "Phasing"),
                      "node": ("NODE_DATA", "parent", "Phases"),
                      "event": ("EVENT_DATA", "ParentId", "Phasing"),
                      # Hierarchies have no per-phase value columns of
                      # their own (they're containers, not attribute-
                      # bearing elements) -- just the bare `Phs` flag.
                      # `entries` is always [] for this kind; the generic
                      # padding loop below is a no-op since HIERARCHY_DATA
                      # has no "P..." columns to pad.
                      "hier": ("HIERARCHY_DATA", "Parent", "Phs")}

    def set_link_flow_field(self, link_id: int, col: str, old: str,
                            new: str) -> bool:
        """Undoable single LINK_DATA cell edit for the Node Capacity
        tab's flow-assignment tables. `col` is one of nomFlow / MaxFlow
        (Flow In) or CapPri (Flow Out)."""
        if col not in ("nomFlow", "MaxFlow", "CapPri"):
            return False
        self._stack.push(IdCellCmd(
            self, "LINK_DATA", "ID", link_id, col, old, new,
            undo_text=f"Edit flow {col}"))
        return True

    def set_link_standby_field(self, link_id: int, col: str, old: str,
                               new: str) -> bool:
        """Undoable single LINK_DATA cell edit for the Node Standby tab's
        switchover table. `col` is one of ColdPri / AProb / AutoTime /
        ManTime."""
        if col not in ("ColdPri", "AProb", "AutoTime", "ManTime"):
            return False
        self._stack.push(IdCellCmd(
            self, "LINK_DATA", "ID", link_id, col, old, new,
            undo_text=f"Edit switchover {col}"))
        return True

    def resync_link(self, link_id: int) -> None:
        """Re-parse one link's live `Link` object from its (just-edited)
        LINK_DATA row so the canvas/engine copies match the row."""
        from raptor.core.fileio.typed import link_from_row
        link_section = self._rbd_ref.sections.get("LINK_DATA") if self._rbd_ref \
            else None
        if link_section is None:
            return
        for _i, row in enumerate(link_section.rows):
            if _safe_int(row.get("ID")) == link_id:
                link = link_from_row(row)
                for j, existing_link in enumerate(self.links):
                    if existing_link.object_num == link_id:
                        self.links[j] = link
                        break
                return

    def set_element_phasing(self, kind: str, oid: int, phased: bool,
                            entries: list[tuple[str, str]]) -> bool:
        """Assign phase behaviour to a block/node/event/hierarchy.

        `entries` per phase, in order:
          blocks/events: [("A"| "L" | "C", float_value), ...]
          nodes:         [("C", -1) / ("L", 0) / (str(k), k), ...]
          hier:          [] always (flag-only, see _PHASE_SECTION)
        Token formats match the Raptor 7 files exactly (`P 001`="A: 1.500"
        for blocks/events; bare `C`/`L`/integer `P001` for nodes -- NB
        the flag column differs too: blocks/events use `Phasing`, NODES
        use `Phases`, hierarchies use `Phs`). One undo step."""
        section_name, _pc, flag_col = self._PHASE_SECTION[kind]
        sec = req(self._rbd_ref).sections.get(section_name)
        if sec is None:
            return False
        row = next((r for r in sec.rows
                    if _safe_int(r.get("ID")) == oid), None)
        if row is None:
            return False
        old_tokens = {k: row[k] for k in PhaseCmd._token_keys(row)}
        old_state = {"flag": row.get(flag_col, "0"), "flag_col": flag_col,
                     "tokens": old_tokens}
        new_tokens: dict[str, str] = {}

        def token(i: int, entry) -> str:
            if kind == "node":
                # Nodes: `P001` (no space), bare token -- "C"/"L"/integer
                # (`_node_phase_column` does int(token) on non-C/L).
                return entry[0] if entry[0] in ("C", "L") else \
                    str(int(entry[1]))
            return f"{entry[0]}: {entry[1]:.3f}"

        # Pad to the section's full P-column width: a phased row must
        # carry a token for every P column (the parser reads exactly
        # that many when the flag is set -- an empty token crashes it).
        max_p = 0
        for c in sec.columns:
            digits = c[2:] if c.startswith("P ") else c[1:]
            if c.startswith("P") and digits.isdigit():
                _idx = c.index(digits)
                max_p = max(max_p, int(digits))
        padded: list[tuple[str, Any]] = list(entries)
        while len(padded) < max_p:
            padded.append(padded[-1] if padded else ("A", 1.0))
        for i, entry in enumerate(padded, start=1):
            key = f"P{i:03d}" if kind == "node" else f"P {i:03d}"
            new_tokens[key] = token(i, entry)
        new_state = {"flag": "1" if phased else "0", "flag_col": flag_col,
                     "tokens": new_tokens if phased else {}}
        if old_state == new_state:
            return False
        self._stack.push(PhaseCmd(self, kind, oid, section_name,
                                  _PARENT_COLS[section_name],
                                  old_state, new_state))
        return True

    def edit_phases(self, text: str, edit):
        """Run `edit(rbd)` (an insert/delete/clear from `core.fileio.phase_edit`) as one undoable step. Returns the
        edit's result; raises `PhaseEditError` with nothing changed when the edit is refused."""
        import copy
        from raptor.gui.ui.diagram_commands import PhaseStructureCmd
        edit(copy.deepcopy(self._rbd_ref))     # a refusal must surface here, before anything is on the undo stack
        cmd = PhaseStructureCmd(self, text, edit)
        self._stack.push(cmd)
        return cmd.result

    # --- Keyed-row editing (pools / triggers: no ID column; the row key
    # --- is PoolName / TriggerName). Engine re-parses these from the
    # --- file at the next run, so editing rows directly is sufficient.

    def _keyed_section_row(self, section_name: str, key_col: str,
                           key_val: str):
        sec = self._rbd_ref.sections.get(section_name) \
            if self._rbd_ref else None
        if sec is None:
            return None, None
        for r in sec.rows:
            if r.get(key_col) == key_val:
                return sec, r
        return sec, None

    def set_keyed_cell(self, section_name: str, key_col: str, key_val: str,
                       col: str, value: str):
        """Undoable single-cell edit in a name-keyed row."""
        _sec, row = self._keyed_section_row(section_name, key_col, key_val)
        if row is None or row.get(col) == value:
            return
        self._stack.push(TableCellCmd(self, section_name, key_col,
                                      key_val, col, row.get(col, ""),
                                      value))

    def add_keyed_row(self, section_name: str, key_col: str,
                      base: dict) -> str:
        """Append a cloned template row with a fresh key; returns it."""
        sec = self._ensure_section(section_name)
        row = dict(base)
        used = {r.get(key_col, "") for r in sec.rows}
        n = 1
        while f"{n}" in used or row.get(key_col) in used:
            row[key_col] = f"{base.get(key_col, 'Pool')}_{n}"
            n += 1
        self._stack.push(TableRowCmd(self, section_name, row, len(sec.rows),
                                     add=True))
        return row[key_col]

    def remove_keyed_row(self, section_name: str, key_col: str,
                         key_val: str):
        sec, row = self._keyed_section_row(section_name, key_col, key_val)
        if row is None:
            return
        idx = list(sec.rows).index(row)
        self._stack.push(TableRowCmd(self, section_name, row, idx,
                                     add=False))

    def _ensure_section(self, section_name: str):
        return row_ops.ensure_section(self._rbd_ref, section_name)

    def zoom_by(self, factor: float):
        """Multiplicative zoom, clamped only at the extreme ends."""
        current = self.transform().m11()
        target = max(ZOOM_MIN, min(ZOOM_MAX, current * factor))
        self.scale(target / current, target / current)
        self._user_zoomed = True
        self._apply_lod()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Only auto-fit while the user hasn't taken over the zoom. Fits
        # the content rect, never the world (the scene rect extends far
        # beyond the model for panning).
        if not self._user_zoomed and getattr(self, "_content_rect", None):
            self.fitInView(self._content_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._apply_lod()

    def zoom_percent(self) -> int:
        return round(self.transform().m11() * 100)

    def set_zoom_percent(self, percent: float) -> bool:
        """Absolute zoom from the Zoom Percent dialog. Returns False
        (unchanged) when the value is outside the clamp."""
        current = self.transform().m11()
        target = max(ZOOM_MIN, min(ZOOM_MAX, percent / 100.0))
        if abs(target - current) < 1e-9:
            return False
        self.scale(target / current, target / current)
        self._user_zoomed = True
        self._apply_lod()
        return True

    def reset_zoom(self):
        self._user_zoomed = False
        if getattr(self, "_content_rect", None):
            self.fitInView(self._content_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._apply_lod()

    #: Item attributes that hold a text label, per element kind -- hidden
    #: together by `_apply_lod` when zoomed out (semantic zoom).
    _LOD_LABEL_ATTRS = ("_label", "_sub", "_name_label")

    def _apply_lod(self):
        """Semantic zoom for element text. Two modes, by scale:

        * **near** (>= `LOD_LABELS_MIN_SCALE`, 25%): everything as drawn.
        * **off**: all text hidden -- shapes, links and badges remain, so
          the model reads as a clean miniature.

        There is no middle "counter-scaled name" mode: holding a name at
        its 100%-zoom on-screen size meant ×5/×10 labels that covered the
        topology they were meant to annotate. Below a quarter scale the
        miniature is the honest view; zoom in to read names.

        View concern only: nothing is written to the model or the saved
        file. Idempotent -- only touches items when the state changes."""
        scale = self.transform().m11()
        prev = getattr(self, "_lod_mode", None)
        if getattr(self, "_lod_enabled", True):
            # Hysteresis so dragging a splitter / resizing at the
            # threshold does not flap the labels every pixel.
            edge = LOD_LABELS_MIN_SCALE + (
                LOD_LABELS_HYSTERESIS if prev == "off"
                else -LOD_LABELS_HYSTERESIS)
            mode = "near" if scale >= edge else "off"
        else:
            mode = "near"       # disabled: always show (renders/exports)
        forced = self._lod_labels_visible is None
        if not forced and mode == prev:
            return
        self._lod_mode = mode
        self._lod_labels_visible = (mode == "near")

        show_all = (mode == "near")
        for coll in (getattr(self, "_block_items", {}),
                     getattr(self, "_hier_items", {}),
                     getattr(self, "_node_items", {}),
                     getattr(self, "_event_items", {})):
            for item in coll.values():
                for attr in self._LOD_LABEL_ATTRS:
                    label = getattr(item, attr, None)
                    if label is not None and hasattr(label, "setVisible"):
                        label.setVisible(show_all)

    def set_lod_enabled(self, enabled: bool):
        """Turn semantic zoom on/off. Disabling forces element labels
        visible regardless of zoom, for exports and renders where a
        label-less image would be wrong."""
        self._lod_enabled = bool(enabled)
        self._lod_labels_visible = None
        self._apply_lod()

    def _refresh_lod(self):
        """Re-apply semantic zoom after items are added outside a full
        rebuild (insert/paste/undo-of-delete), where the cached state would
        otherwise leave a new item's labels visible while the rest are
        hidden (or vice versa) until the next zoom change."""
        self._lod_labels_visible = None
        self._apply_lod()

    @contextmanager
    def labels_forced(self):
        """Context manager: force every element label visible for the
        duration (then restore the previous LOD state). Use around ANY
        scene render that becomes an artefact -- bitmap, SVG, print, PDF,
        report -- so an export is never label-less just because the view
        happened to be zoomed out."""
        was = getattr(self, "_lod_enabled", True)
        if was:
            self.set_lod_enabled(False)
        try:
            yield
        finally:
            if was:
                self.set_lod_enabled(True)

    @contextmanager
    def grid_hidden(self):
        """Context manager: hide the world-spanning grid for the
        duration (then restore it). `QGraphicsScene.render` has no item
        filter, so a grid item left in the scene is painted into every
        artefact -- skipping it when *measuring* the content rect is not
        enough. Use around any export/print/report render whose output
        should carry only the model, not the editing aid."""
        grids = [it for it in self._scene.items()
                 if isinstance(it, _GridBackground)]
        was = [g.isVisible() for g in grids]
        for g in grids:
            g.setVisible(False)
        try:
            yield
        finally:
            for g, vis in zip(grids, was):
                g.setVisible(vis)

    def fit_items(self, items) -> bool:
        """Zoom/pan to fit the given scene items (plus a slim margin),
        then re-apply semantic zoom. Returns False when *items* is empty."""
        content = QRectF()
        for it in items:
            try:
                content = content.united(it.sceneBoundingRect())
            except RuntimeError:
                continue   # C++-deleted item still in a stale selection
        if content.isEmpty():
            return False
        self._user_zoomed = True
        self.fitInView(content.adjusted(-30, -30, 30, 30), Qt.AspectRatioMode.KeepAspectRatio)
        self._apply_lod()
        return True

    def zoom_to_selection(self) -> int:
        """Zoom to fit the currently selected items. Returns how many."""
        items = list(self._scene.selectedItems())
        if not items:
            return 0
        self.fit_items(items)
        return len(items)

    def zoom_to_hierarchy(self) -> bool:
        """Descend into the selected hierarchy oval (and fit its level).
        Returns False when no hierarchy is selected."""
        for it in self._scene.selectedItems():
            if isinstance(it, _HierItem):
                self.enter_level(it._hier_id)
                # Truthful result: `enter_level` refuses at the depth cap
                # (or for the current level), so report the actual level.
                return self._current_parent == it._hier_id
        return False


    #: Columns a library "update matching" refreshes (the manual's
    #: monthly failure/repair-data update workflow). Includes the LDT
    #: pre/post distribution+params and the block-level Dependency
    #: fields: Raptor's Import Library offers separate Fail / Repair /
    #: LDT / Dependency checkboxes. The parser disambiguates the
    #: duplicated LDT param headers as `Param1#1` (pre) / `Param1#2`
    #: (post).
    LIBRARY_UPDATE_COLS = ("FDist", "#FParam", "FStream",
                           "FParam1", "FParam2", "FParam3",
                           "RDist", "#RParam", "RStream",
                           "RParam1", "RParam2", "RParam3",
                           "Pre-LDTDist", "Param1#1", "Param2#1",
                           "Param3#1",
                           "Post-LDTDist", "Param1#2", "Param2#2",
                           "Param3#2",
                           "DepNum", "DepType", "DepNothing", "DepIdle",
                           "DepPM", "DepFail", "DepPMThresh",
                           "defDepStateIdle")

    def add_block_from_template(self, row: dict, at_model=None) -> int:
        """Insert a library template as a new block (fresh id, undoable,
        placed at `at_model` or near the viewport centre). Returns the
        new block id."""
        self._paste_buffer = [("block", dict(row))]
        n = self.paste(at_model=at_model)
        if n != 1:
            return 0
        return max(self.blocks)

    def update_blocks_from_library(self, templates: list[dict]) -> int:
        """Overwrite the F/R distribution+param+stream columns of every
        block whose name matches a template (Raptor 7's monthly data
        update). One undo step restores both the file rows and the
        typed objects. Returns blocks changed."""
        by_name = {t["row"].get("blockName", ""): t["row"]
                   for t in templates}
        sec = req(self._rbd_ref).sections.get("BLOCK_DATA")
        if sec is None:
            return 0
        entries: list[tuple[int, int, dict, dict]] = []
        for idx, r in enumerate(sec.rows):
            try:
                if int(float(r.get("ID", "0"))) == 0:
                    continue
            except (TypeError, ValueError):
                continue
            template = by_name.get(r.get("blockName", ""))
            if template is None:
                continue
            old_cols = {c: r.get(c, "") for c in self.LIBRARY_UPDATE_COLS}
            new_cols = {c: template.get(c, old_cols[c])
                        for c in self.LIBRARY_UPDATE_COLS}
            if old_cols != new_cols:
                entries.append((idx, _safe_int(r.get("ID")),
                                old_cols, new_cols))
        if not entries:
            return 0
        self._stack.push(LibUpdateCmd(self, sec, entries))
        return len(entries)

    def set_snap_enabled(self, enabled: bool):
        """Ribbon toggle: align dragged/placed items to the visible
        grid. Off = free movement. Never rewrites file positions."""
        self.snap_enabled = enabled
        # recompute snap step directly from the current grid (avoids a
        # full scene rebuild just to flip the toggle) -- same
        # SNAP_SUBDIVISIONS fraction _apply_scene_layout uses.
        x_grid = getattr(self, "_x_grid_current", 0.0)
        y_grid = getattr(self, "_y_grid_current", 0.0)
        self._snap_state["x"] = (x_grid / SNAP_SUBDIVISIONS) if enabled else 0.0
        self._snap_state["y"] = (y_grid / SNAP_SUBDIVISIONS) if enabled else 0.0

    @property
    def snap_enabled(self) -> bool:
        return getattr(self, "_snap_enabled", True)

    @snap_enabled.setter
    def snap_enabled(self, value: bool) -> None:
        self._snap_enabled = value

    def selected_elements(self) -> tuple[list[int], list[int], list[int],
                                         list[int]]:
        """Selected (blocks, regular nodes, events, hierarchies): the model
        elements an element-wise command such as Mass Edit acts on. The
        ID=0 defaults block and the Start/End/In/Out markers are not
        elements the user edits."""
        # Skip items that no longer exist. Entering or leaving a hierarchy
        # rebuilds the scene while selection-changed signals are still being
        # delivered, and the item maps briefly hold wrappers of items Qt has
        # already deleted -- touching one raises RuntimeError.
        def selected(items):
            return [(oid, it) for oid, it in items.items()
                    if isValid(it) and it.isSelected()]
        blocks = [bid for bid, _it in selected(self._block_items) if bid != 0]
        nodes = [nid for nid, _it in selected(self._node_items)
                 if nid in self.nodes and self.nodes[nid].type_node == 2]
        events = [eid for eid, _it in selected(self._event_items)]
        hiers = [hid for hid, _it in selected(self._hier_items)]
        return blocks, nodes, events, hiers

    def hierarchy_count(self) -> int:
        """Hierarchy ovals on the level being viewed."""
        return sum(1 for it in self._hier_items.values() if isValid(it))

    def selected_element_count(self) -> int:
        return sum(len(ids) for ids in self.selected_elements())

    def selected_movable_count(self) -> int:
        """Selected items that can be dragged (elements and annotations)."""
        try:
            items = self._scene.selectedItems()
        except RuntimeError:        # scene torn down during shutdown
            return 0
        return len([it for it in items if it.flags()
                    & QGraphicsItem.GraphicsItemFlag.ItemIsMovable])

    def apply_hier_dsi(self, dsi: dict[int, dict]) -> None:
        """Paint end-of-run Descendant Status Indicators on the ovals
        of the current level (`{hier_id: {"state", "nodes", "down",
        "degraded"}}`; missing/empty entries clear). Called by
        MainWindow after results land. The payload is kept so level
        traversal can re-apply it without a new run."""
        self._last_hier_dsi = dict(dsi or {})
        for hid, item in self._hier_items.items():
            item.set_dsi(dsi.get(hid))

    def auto_layout(self):
        """Rearrange the whole level by graph topology: columns follow the
        longest-path layer from the Start node(s) (left = upstream), rows
        spread evenly within each column. Marks the model dirty via
        `model_changed` -- positions are real edits, written back on Save.

        A selection is NOT a scope. It used to be: only the selected
        elements moved, to where a full layout would put them, while their
        neighbours stayed put -- so selecting one block of a straight chain
        and pressing Auto Layout lifted it off the line ("wonky"), and the
        re-selection afterwards made the next press partial too. The whole
        level is laid out every time; the selection is kept."""
        # Bipartite adjacency, ref-aware: ('Node', 3) and ('Block', 3)
        # are different elements -- keying by bare id let a layering walk
        # jump between kinds. Members = this level's blocks/nodes/hier
        # ovals/events; events are vertices on the flow (Node -> Event ->
        # Node), so the layout places them instead of leaving them where
        # their unrelated hierarchy ParentId puts them.
        members: dict[tuple[str, int], QGraphicsItem] = {}
        members.update({("block", bid): item
                        for bid, item in self._block_items.items()})
        members.update({("node", nid): item
                        for nid, item in self._node_items.items()})
        members.update({("hier", hid): item
                        for hid, item in getattr(self, "_hier_items",
                                                 {}).items()})
        # Events are flow objects too (Node -> Event -> Node), so they
        # must be laid out as vertices. They used to be left out and
        # shifted by their parent block's delta, but an event's
        # EVENT_DATA ParentId is its hierarchy LEVEL, not a block, so on
        # the home level they never moved at all.
        members.update({("event", eid): item
                        for eid, item in getattr(self, "_event_items",
                                                 {}).items()})

        edges: list[tuple[tuple[str, int], tuple[str, int]]] = []
        link: Any
        for link in self.links:
            a = (link.connect_f_ref.replace("RBD", "", 1).lower(),
                 link.connect_from_id)
            b = (link.connect_t_ref.replace("RBD", "", 1).lower(),
                 link.connect_to_id)
            if a in members and b in members:
                edges.append((a, b))

        # Seed from the current level's entry point: home uses its Start
        # markers; inside a hierarchy the flow enters through that
        # hierarchy's own In node -- otherwise there is nothing reachable
        # to seed from on sub-level contents.
        cur_parent = self._current_parent
        if cur_parent and cur_parent in self.hiers:
            h = self.hiers[cur_parent]
            starts = [("node", h.in_id)]
            pins = {("node", h.out_id)}
        else:
            starts = [("node", nid) for nid, n in self.nodes.items()
                      if n.type_node == 1]
            # Pin only the terminal marker(s) to the centre line. The
            # old rule pinned every regular (type 2) node, so two
            # same-layer regular nodes were forced to identical
            # coordinates and rendered stacked.
            pins = {("node", nid) for nid, n in self.nodes.items()
                    if n.type_node in (3, 5)}

        # Positions come from the shared layout engine (legacy_layout.
        # layout_container_positions): longest-path layering, barycenter
        # row ordering, and collision refinement so no straight edge
        # runs through a block -- identical output to what .rbds/.rbd
        # save normalization produces.
        if not members:
            return
        # Remember the selection to restore it afterwards (the rebuild
        # below recreates every item).
        selected_keys = {k for k, it in members.items()
                         if it.isSelected()}
        for it in self._scene.selectedItems():
            it.setSelected(False)
        moving = set(members)

        # The undo record: model-unit positions and the canvas transform,
        # NOT the items -- the rebuild at the end recreates every item at
        # a re-derived scale, so an item-level record pointed at deleted
        # items and replayed old-scale pixels into the new scale ("undo
        # scrambles the diagram").
        self.sync_model_positions()
        before = self._layout_state()
        self._loading = True
        # Disable drag-snap for this bulk placement: setPos() below fires
        # itemChange() same as an interactive drag, and the old snap grid
        # (matching the file's pre-auto-layout spacing) has nothing to do
        # with the layout pitches -- it would collapse distinct rows onto
        # the same line. The _apply_scene_layout() call at the end
        # recomputes self._snap_state to match the new grid before returning.
        self._snap_state["x"] = 0.0
        self._snap_state["y"] = 0.0
        try:
            # Place in model units, not scene pixels. Pixel-step
            # placement then divided by `self._scale` on save collapses
            # the saved pitches to fractions of a model unit, so blocks
            # sit on the connector lines in the saved file. Model-unit
            # pitches survive the scene<->model round trip verbatim.
            from raptor.core.fileio.legacy_layout import (
                layout_container_positions, LAYER_PITCH, ROW_PITCH,
            )
            pos = layout_container_positions(members.keys(), edges,
                                             starts, pins=pins)
            for k, (mx, my) in pos.items():
                if k not in moving:
                    continue
                item = members[k]
                sx, sy = self._vt.to_scene(mx, my)
                if isinstance(item, _BlockItem):
                    item.setPos(sx - BLOCK_W / 2,
                                sy - BLOCK_H / 2)      # top-left
                else:
                    item.setPos(sx, sy)                # centred
                x_step = LAYER_PITCH
            y_step = ROW_PITCH
        finally:
            self._loading = False
        for link in self._links:
            link._update_route()
        self._user_arranged = True
        # sync only (not write_back): the position overlay reads the
        # typed objects on level rebuilds, so traversal keeps the
        # arrangement; rows are written on Save like any other edit.
        self.sync_model_positions()
        # Auto Layout places items on the model-unit grid (LAYER_PITCH/
        # ROW_PITCH above), so the background grid must be drawn at that
        # same model spacing -- sync_model_positions() already wrote the
        # new positions back into model units through the same
        # `self._scale`.
        self._model_dx_min = x_step
        self._model_dy_min = y_step
        # Re-derive the view transform, because a global re-layout is
        # exactly the case the frozen transform is NOT meant to cover.
        #
        # The transform is frozen at load so that moving ONE element --
        # which changes the model's global closest-spacing -- does not
        # rescale the whole canvas and shift every untouched element on
        # reopen (`RbdFile.view_transform`, and `_compute_model_transform`
        # above). Auto Layout is the opposite of an incidental edit: it
        # replaces the entire geometry, so the frozen scale is stale by
        # construction.
        #
        # The failure this fixes, reported as "it doesn't space
        # adequately even though it gives a small separation": a file
        # whose blocks all share one coordinate (every stored position
        # identical) derives a closest-spacing of 0, so the scale falls to
        # its floor of 8.0 and freezes there. The layout then correctly
        # places blocks 2.5 model units apart, which at scale 8.0 renders
        # as 20 scene px between 108 px blocks -- every block overlapping
        # its neighbour. Re-deriving gives the scale that pitch needs,
        # and the same layout then reads as ~268 px apart.
        #
        # The model is the source, and it takes two steps to get there:
        # `sync_model_positions` above wrote the TYPED objects and the
        # `_pos_overrides` overlay, while `_compute_model_transform`
        # reads the SECTION ROWS. So the rows must be written back before
        # the transform is re-derived, or the derivation still sees the
        # file's original zeros. `write_back_to_rbd` costs nothing extra
        # here: Auto Layout is an edit, so the model is dirty either way
        # and Save writes the same rows.
        rbd = self._rbd_ref
        if rbd is not None:
            self.write_back_to_rbd(rbd)
            rbd.view_transform = None
            self._compute_model_transform(rbd)
            self._rebuild_scene()
        # Up/Home (which rebuild from rows) and Save keep the layout
        self._apply_scene_layout()
        self.sync_model_positions()
        stack = getattr(self, "_stack", None)
        if stack is not None:
            # Pushed AFTER the rebuild: it records model positions, so the
            # rebuild cannot invalidate it. Its first redo is a no-op.
            stack.push(LayoutCmd(self, "Auto Layout", before,
                                 self._layout_state()))
        else:
            self.layout_changed.emit()
        if selected_keys:
            # Re-select: the layout above had to clear the selection, and
            # leaving the canvas with nothing selected reads as "the
            # layout lost my blocks".
            # Resolved from the view's own id->item maps, not from
            # `members`: `_rebuild_scene` above deleted those item
            # objects and touching one raises "Internal C++ object
            # already deleted".
            tables: dict[str, dict[int, Any]] = {
                "block": self._block_items, "node": self._node_items,
                "hier": getattr(self, "_hier_items", {})}
            for kind, oid in sorted(selected_keys):
                table_item = tables[kind].get(oid)
                if table_item is not None:
                    table_item.setSelected(True)

def _minimap_node_colour(item) -> str:
    """Start green, End red, everything else the relationship colour."""
    kind = getattr(item, "_node_type", 2)
    if kind == 1:
        return COLOUR["operating"]
    if kind in (3, 5):
        return COLOUR["failed"]
    return COLOUR["relationship"]


class _MinimapView(QGraphicsView):
    """Navigation minimap -- a small always-on-top overview of the whole
    current level's content, sharing the main view's own `QGraphicsScene`
    (so what it shows is always the live model), with a frame showing the
    main view's current viewport and click/drag-to-pan.

    It does NOT ask the scene to render itself. Doing so drew every item's
    full paint routine -- labels, gradients, arrowheads -- at thumbnail size
    on every drag event, which cost as much as repainting the canvas itself
    and is what made dragging in it feel sticky. Instead the overview is
    drawn once as simple, high-contrast shapes (blocks as solid boxes, nodes
    as dots, links as lines, all with a minimum on-screen size so nothing
    vanishes when the diagram is large) into a cached pixmap that is redrawn
    only when the model or the fit changes. Moving the viewport frame over it
    is then nearly free.

    A QTimer polls the main view's geometry (QGraphicsView has no single
    "visible area changed" signal) and its scroll bars repaint it directly,
    so it follows the canvas as you scroll or zoom."""

    #: Fixed on-screen size (device-independent pixels).
    SIZE = QSize(230, 160)
    _MARGIN = 10
    #: Smallest a shape is drawn, on screen, so it stays visible.
    _MIN_BOX = 5.0
    _MIN_DOT = 3.0

    def __init__(self, main_view: DiagramView):
        # A child of the view itself, NOT of its viewport. Scrolling moves
        # the viewport's pixels AND its children, so a minimap parented to
        # the viewport slid along with every pan and only snapped back on
        # the next poll -- the "jump" when dragging the canvas.
        super().__init__(main_view)
        self._main_view = main_view
        main_view.installEventFilter(self)     # follow its resizes
        self.setScene(main_view._scene)
        self.setFixedSize(self.SIZE)
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(2)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setInteractive(False)   # don't steal clicks from real items
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to pan the diagram, click to jump, wheel to "
                        "scroll")
        # A clear, visible boundary.
        c = ThemeManager.instance().colour
        self.setStyleSheet(
            f"QGraphicsView {{ border: 2px solid {c['border']}; }}")
        self._dragging = False
        self._grab_offset = QPointF()
        self._last_fitted_rect: QRectF | None = None
        #: What the minimap is fitted to. Follows the live content, but only
        #: changes when the content outgrows it or shrinks a lot, so the
        #: picture stays put while things are dragged about.
        self._fit_target: QRectF | None = None
        self._glide_goal: QPointF | None = None
        self._glide = QTimer(self)
        self._glide.setInterval(16)
        self._glide.timeout.connect(self._glide_step)
        self._overview: QPixmap | None = None
        self._overview_key: tuple | None = None
        self._overview_dirty = True
        # Changes are noticed by the poll below (a cheap signature of the
        # element positions), NOT by listening to the scene's `changed`
        # signal: that fires on every repaint request, runs a Python slot
        # each time, and its mere presence reshuffled event ordering enough
        # to change how the docks sized themselves. The overview is redrawn
        # a moment after the last change rather than once per drag step.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(80)
        self._settle.timeout.connect(self._on_model_settled)
        self._signature: tuple | None = None
        # Follow the canvas as it scrolls, without waiting for the poll.
        for bar in (main_view.horizontalScrollBar(),
                    main_view.verticalScrollBar()):
            bar.valueChanged.connect(self._follow_canvas)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._reposition()
        self.hide()   # shown by _refresh once there's real overflow content

    # -- geometry -----------------------------------------------------

    def _content_rect(self) -> QRectF:
        """What to fit the minimap to."""
        if self._fit_target is not None:
            return self._fit_target
        return (getattr(self._main_view, "_content_rect", None)
               or self._main_view.sceneRect())

    def _live_content(self) -> QRectF:
        """Bounds of the elements as they are now (the cached layout rect
        goes stale as soon as something is dragged), with a slim margin."""
        box = QRectF()
        for it in self.scene().items():
            if isinstance(it, (_BlockItem, _NodeItem, _EventItem,
                               _HierItem)):
                box = box.united(it.sceneBoundingRect())
        return box.adjusted(-30, -30, 30, 30) if not box.isEmpty() else box

    def _update_fit_target(self) -> None:
        """Re-target the fit -- but only when the picture would be wrong:
        the content no longer fits inside it, or has shrunk to under half
        of it. Otherwise it stays exactly where it is, so dragging elements
        (or the canvas) never makes the minimap rescale under you."""
        live = self._live_content()
        if live.isEmpty():
            return
        cur = self._fit_target
        outgrown = cur is None or not cur.contains(live)
        shrunk = (cur is not None and live.width() * live.height()
                  < 0.5 * cur.width() * cur.height())
        if outgrown or shrunk:
            self._fit_target = live

    def _adapt_size(self, content: QRectF) -> None:
        """Shape the box to the diagram. A long, thin diagram (a chain of
        blocks in one row) in a fixed box leaves a thin sliver in the middle
        of a lot of empty space; give it a wider, shorter box so the
        diagram fills it. A tall one gets the opposite."""
        if content.width() <= 0 or content.height() <= 0:
            return
        aspect = content.width() / content.height()
        base_w, base_h, pad = 230, 160, 12
        if aspect >= base_w / base_h:
            w = 360 if aspect > 2.5 else base_w
            h = int(max(64, min(base_h + 10, w / aspect + pad)))
        else:
            h = base_h + 10
            w = int(max(120, min(base_w, h * aspect + pad)))
        new = QSize(w, h)
        if new != self.SIZE:
            self.SIZE = new            # instance value shadows the default
            self.setFixedSize(new)
            self._last_fitted_rect = None      # re-fit into the new box
            self._overview_dirty = True

    def _reposition(self):
        g = self._main_view.viewport().geometry()   # in the view's own space
        self.move(g.left() + g.width() - self.SIZE.width() - self._MARGIN,
                  g.top() + g.height() - self.SIZE.height() - self._MARGIN)

    def eventFilter(self, obj, event):
        if obj is self._main_view and event.type() in (QEvent.Type.Resize,
                                                       QEvent.Type.Move):
            self._reposition()
        return False

    def _refresh(self):
        if not self._main_view.isVisible():
            return
        self._notice_changes()
        content = self._content_rect()
        self._adapt_size(content)
        self._reposition()
        # Auto-hide when everything already fits in the real viewport --
        # a minimap of a diagram with nothing to scroll to is clutter,
        # not navigation.
        visible_scene_rect = self._main_view.mapToScene(
            self._main_view.viewport().rect()).boundingRect()
        overflows = (content.width() > visible_scene_rect.width() * 1.02
                    or content.height() > visible_scene_rect.height() * 1.02)
        if overflows and not self.isVisible():
            self.setVisible(True)
            self.raise_()          # once, above the viewport -- not per tick
        elif not overflows:
            self.setVisible(False)
            return
        # Only re-fit when the content rect actually changed size (a real
        # edit), and never while a drag is in progress: re-fitting on every
        # tick re-derived a very slightly different transform each time
        # from floating-point rounding, which made a held drag jerky.
        if not self._dragging and content != self._last_fitted_rect:
            if content.width() > 0 and content.height() > 0:
                # The minimap's OWN scene rect: it used to follow the
                # scene's (a huge, changing "world"), so anything that
                # resized the world shifted the picture.
                self.setSceneRect(content)
                self.fitInView(content, Qt.AspectRatioMode.KeepAspectRatio)
                self._last_fitted_rect = QRectF(content)
                self._overview_dirty = True
        self.viewport().update()   # redraw the viewport-frame overlay

    # -- keeping the picture current ------------------------------------

    def _scene_signature(self) -> tuple:
        """Cheap fingerprint of what the overview shows: how many elements
        and links there are, and where the elements are."""
        mv = self._main_view
        acc, n = 0, 0
        for items in (mv._block_items, mv._node_items, mv._event_items,
                      mv._hier_items):
            for it in items.values():
                if isValid(it):
                    p = it.pos()
                    acc = (acc * 31 + hash((round(p.x(), 1),
                                            round(p.y(), 1)))) & 0xFFFFFFFF
                    n += 1
        return (n, acc, len(mv._links))

    def _notice_changes(self) -> None:
        sig = self._scene_signature()
        if sig != self._signature:
            self._signature = sig
            self._overview_dirty = True
            self._settle.start()          # restarts: trailing edge

    def _on_model_settled(self):
        self._overview_dirty = True
        self._update_fit_target()
        if self.isVisible():
            self._refresh()
            self.viewport().update()          # rebuilds: no longer settling

    def _follow_canvas(self, _value=0):
        if self.isVisible():
            self.viewport().update()

    # -- drawing ---------------------------------------------------------

    def _style_key(self) -> tuple:
        t = self.viewportTransform()
        return (t.m11(), t.m22(), t.dx(), t.dy(), self.viewport().size(),
                self.devicePixelRatioF(), COLOUR["canvas"], COLOUR["accent"],
                COLOUR["muted"])

    def _overview_pixmap(self) -> QPixmap:
        key = self._style_key()
        if self._overview is not None and key == self._overview_key:
            # Up to date -- or the model is still changing (a drag): show
            # the last picture and let the settle timer redraw it once the
            # motion pauses, instead of rebuilding it on every step.
            if not self._overview_dirty or self._settle.isActive():
                return self._overview
        size = self.viewport().size()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(size.width() * dpr), int(size.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(COLOUR["canvas"]))
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self.viewportTransform()
        painter.setTransform(t)
        self._paint_shapes(painter, max(t.m11(), 1e-6))
        painter.end()
        self._overview, self._overview_key = pm, key
        self._overview_dirty = False
        return pm

    def _paint_shapes(self, painter: QPainter, scale: float) -> None:
        """Every element as a plain shape, at least a few pixels across on
        screen. `scale` is scene units -> minimap pixels."""
        box = self._MIN_BOX / scale
        dot = self._MIN_DOT / scale
        items = self.scene().items()
        # Links first, so elements sit on top of them.
        link_pen = QPen(QColor(COLOUR["muted"]), 1.4)
        link_pen.setCosmetic(True)
        painter.setPen(link_pen)
        for it in items:
            if isinstance(it, _LinkItem):
                painter.drawLine(it._p0, it._p3)
        edge = QPen(QColor(COLOUR["text"]), 1.0)
        edge.setCosmetic(True)
        accent = QColor(COLOUR["accent"])
        for it in items:
            if isinstance(it, _BlockItem):
                r = it.mapRectToScene(it.rect())
                w, h = max(r.width(), box), max(r.height(), box * 0.6)
                painter.setPen(edge)
                painter.setBrush(accent)
                painter.drawRect(QRectF(r.center().x() - w / 2,
                                   r.center().y() - h / 2, w, h))
            elif isinstance(it, _NodeItem):
                c = it.scenePos()
                rad = max(it._r, dot)
                painter.setPen(edge)
                painter.setBrush(QColor(_minimap_node_colour(it)))
                painter.drawEllipse(c, rad, rad)
            elif isinstance(it, (_EventItem, _HierItem)):
                r = it.sceneBoundingRect()
                w, h = max(r.width(), box), max(r.height(), box)
                painter.setPen(edge)
                painter.setBrush(QColor(COLOUR["relationship"]))
                painter.drawEllipse(r.center(), w / 2, h / 2)

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.drawPixmap(0, 0, self._overview_pixmap())
        # The main view's visible area: a frame that stays legible over
        # anything (opaque outline, light tint) rather than a faint wash.
        pen = QPen(QColor(COLOUR["accent"]), 2)
        painter.setPen(pen)
        painter.setBrush(_minimap_viewport_fill())
        painter.drawRect(self._viewport_rect_here())
        painter.end()

    # -- interaction ------------------------------------------------------

    @staticmethod
    def _pos_of(event):
        return (event.position().toPoint() if hasattr(event, "position")
                else event.pos())

    def _viewport_rect_here(self) -> QRectF:
        """The main view's visible area in this minimap's coordinates."""
        scene_rect = self._main_view.mapToScene(
            self._main_view.viewport().rect()).boundingRect()
        # Through the transform, not `mapFromScene`: that rounds to whole
        # pixels, which is several scene units at thumbnail scale.
        return self.viewportTransform().mapRect(scene_rect)

    def _pan_to(self, pos):
        """Centre the canvas on minimap point `pos` (shifted by the grab
        offset, so dragging the frame moves it by exactly the cursor's
        travel instead of snapping its centre to the cursor)."""
        target = QPointF(pos) - self._grab_offset
        # Map with the full-precision inverse transform: rounding the
        # position to a whole minimap pixel moves the canvas by several
        # scene units, which showed as a jump when grabbing the frame.
        inv, ok = self.viewportTransform().inverted()
        self._glide_goal = (inv.map(target) if ok
                            else QPointF(self.mapToScene(target.toPoint())))
        # One minimap pixel is many canvas pixels, so following the cursor
        # exactly moves the canvas in coarse hops. Ease toward it instead:
        # the first step happens now (so the canvas answers immediately),
        # and a 60 Hz timer carries on until it arrives.
        self._glide_step()
        if self._glide_goal is not None:
            self._glide.start()

    #: Fraction of the remaining distance covered per 16 ms tick.
    _GLIDE = 0.42

    def _glide_step(self) -> None:
        goal = self._glide_goal
        if goal is None:
            self._glide.stop()
            return
        mv = self._main_view
        cur = mv.mapToScene(mv.viewport().rect().center())
        delta = goal - cur
        px = max(abs(delta.x()), abs(delta.y())) * mv.transform().m11()
        if px < 0.75:                       # arrived (to under a pixel)
            mv.centerOn(goal)
            self._glide_goal = None
            self._glide.stop()
        else:
            mv.centerOn(cur + delta * self._GLIDE)
            after = mv.mapToScene(mv.viewport().rect().center())
            if after == cur:                # clamped at the edge: give up
                self._glide_goal = None
                self._glide.stop()
        self.viewport().update()

    def mousePressEvent(self, event):
        self._dragging = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        pos = self._pos_of(event)
        frame = self._viewport_rect_here()
        # Grab the frame where it was pressed; a press outside it jumps the
        # canvas there first and then drags from its centre.
        self._grab_offset = (QPointF(pos) - frame.center()
                             if frame.contains(QPointF(pos)) else QPointF())
        self._pan_to(pos)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._pan_to(self._pos_of(event))

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._grab_offset = QPointF()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        # One final re-fit now that the drag has ended, in case the
        # gesture panned somewhere that would otherwise wait up to
        # 200ms for the next tick.
        self._refresh()

    def wheelEvent(self, event):
        """Scroll the canvas from the minimap: the wheel moves it
        vertically (Shift: horizontally), a trackpad moves it both ways.
        The minimap itself never scrolls, so the wheel used to do nothing
        here."""
        v, h = self._main_view.verticalScrollBar(), \
            self._main_view.horizontalScrollBar()
        px = event.pixelDelta()
        if not px.isNull():
            dx, dy = -px.x(), -px.y()
        else:
            ang = event.angleDelta()
            step = 60 / 120.0     # a notch moves 60 px of canvas
            dx, dy = -ang.x() * step, -ang.y() * step
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and dx == 0:
                dx, dy = dy, 0
        h.setValue(h.value() + int(dx))
        v.setValue(v.value() + int(dy))
        event.accept()

def _minimap_viewport_fill() -> QBrush:
    c = QColor(COLOUR["accent"])
    c.setAlpha(40)
    return QBrush(c)

class DiagramDock(QWidget):
    selection_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = DiagramView()
        # The canvas should stay usable, but a hard 430px floor summed
        # with the ribbon, menus and an expanded bottom dock exceeded
        # short screens -- the layout overflowed and clipped the status
        # bar off the window. 260px keeps context visible while letting
        # every other region get its share.
        self.view.setMinimumHeight(260)
        self._minimap = _MinimapView(self.view)

        # A second, duplicate search box once lived here at the top of
        # the canvas (find-by-name for blocks/nodes only). There is now
        # exactly one Find widget, in the Model Explorer dock
        # (MainWindow._find_edit, covering Block/Node/Event/Hier alike).
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view)

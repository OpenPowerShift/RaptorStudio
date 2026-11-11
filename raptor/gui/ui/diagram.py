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
from collections.abc import Callable
from typing import Any, cast

from shiboken6 import isValid
from PySide6.QtCore import (
    Qt, Signal, QEvent, QRectF, QPointF, QLineF, QSize,
    QTimer,
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
from raptor.core.fileio import view_transform
from raptor.core.annotations import (
    ANNO_Z_DEFAULT,
)
from raptor.core.edit import row_ops
from raptor.gui import clipboard_payload, text_editing
from raptor.gui.ui.diagram_items import links_affected_by_moving, routing_batch
from raptor.gui.model_edit import (BLOCK_COST_FIELDS, PM_OPTION_ATTRS, EditedFields,
                                   ModelEditor, _pos)
from raptor.core.fileio.typed import (
    block_from_row, node_from_row, link_from_row, hier_from_row,
)
from raptor.core.model import RBDBlock, RBDHier, RBDNode, Link
from raptor.gui.resources.design_tokens import (
    COLOUR, ThemeManager,
)
from raptor.gui.ui.diagram_live import LiveOverlayMixin
from raptor.gui.ui.diagram_menu import ContextMenuMixin
from raptor.gui.ui.diagram_zoom_mixin import ZoomMixin
from raptor.gui.ui.diagram_link_insert_mixin import LinkInsertMixin
from raptor.gui.ui.diagram_clipboard_mixin import ClipboardMixin
from raptor.gui.ui.diagram_structure_mixin import StructureMixin
from raptor.gui.ui.diagram_element_edit_mixin import ElementEditMixin
from raptor.gui.ui.diagram_annotation_mixin import AnnotationMixin
from raptor.gui.ui import block_labels
from raptor.gui.ui import settings as app_settings
from raptor.gui.ui.diagram_common import (  # noqa: F401
    BLOCK_H,
    BLOCK_W,
    FINE_MOVE_SUBDIVISIONS,
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
    BlockCostCmd,
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
    RouteGripCmd,
    RowCellCmd,
    SplitLinkCmd,
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
    _RouteGripItem,
    _NUDGE_DIRS,
    _NodeItem,
    _apply_hover_cursor,
    set_flow_direction,
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
    apply_anno_state,
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
from raptor.gui.ui.diagram_hierarchy_move_mixin import HierarchyMoveMixin
from raptor.gui.ui.diagram_phase_mixin import PhaseKeyedMixin
from raptor.gui.ui.diagram_navigation_mixin import NavigationMixin
from raptor.gui.ui.diagram_auto_layout_mixin import AutoLayoutMixin
from raptor.gui.ui.diagram_nudge_mixin import _NUDGEABLE_TYPES, NudgeMixin



class DiagramView(NudgeMixin, AutoLayoutMixin, NavigationMixin,
                  PhaseKeyedMixin, HierarchyMoveMixin, ZoomMixin,
                  LinkInsertMixin, ClipboardMixin, StructureMixin,
                  ElementEditMixin, AnnotationMixin, ContextMenuMixin,
                  LiveOverlayMixin, QGraphicsView):
    block_selected = Signal(int, str)
    node_selected = Signal(int, str)
    selection_cleared = Signal()
    #: Emitted on every level change with the new current ParentId
    #: (0 = home) -- MainWindow uses it to enable Up/Home navigation.
    level_changed = Signal(int)
    #: Emitted BEFORE a level rebuild (and before `load_rbd` swaps the
    #: model). MainWindow uses it to flush a pending Inspector comment
    #: draft while its old element/editor still exists.
    level_about_to_change = Signal()
    #: Emitted at the start of a structural edit (delete/collapse/reparent/
    #: move-out), before it pushes its undo command. MainWindow flushes a
    #: pending Comment draft here so the comment command lands BELOW the
    #: structural command: one Ctrl+Z undoes the structure, not the text.
    structural_about_to_change = Signal()
    hier_selected = Signal(int)
    event_selected = Signal(int)
    #: (annotation id) -- drives the Inspector's colour/font tab, same
    #: as block/node/event/hier selection.
    annotation_selected = Signal(int)
    #: Emitted when the user actually drags a block/node -- not during
    #: `load_rbd`'s own initial placement (guarded by `_loading` below).
    #: `MainWindow` uses this to mark the model dirty.
    model_changed = Signal()
    #: (etype, id, raw stored comment) -- a COMMENTS row changed, including
    #: undo/redo. MainWindow updates the live Inspector comment editor in
    #: place from this instead of rebuilding the pane under the caret.
    comment_changed = Signal(str, int, str)
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
    #: The paste buffer changed: Paste may have become (un)available.
    paste_state_changed = Signal()
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
        self._scene._snap_state = self._snap_state  # type: ignore[attr-defined]
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
        #: levels, for a dependency badge's target name even when the
        #: target lives on a different level than the one on screen. The
        #: whole-file scan is expensive, so it is lazy: `_rebuild_scene`
        #: leaves this None when the visible level has no dependencies,
        #: and `_name_index()` fills it on first use (a dependency added
        #: live through the Inspector, for instance).
        self._global_name_index: dict[tuple[str, int], str] | None = None
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
        #: Add-tool hover-on-link state. The link is held only while it is
        #: the unique/clear nearest candidate; the state is "allowed" or
        #: "blocked" so the item can paint an accent or warning halo.
        #: The hovered insert target: a link (series) or an element
        #: (parallel). The name predates the parallel case.
        self._insert_hover_link: Any = None
        #: Overlays round a parallel target (`_insert_halo`, strong) and
        #: the nodes/branches it will join (lighter); all in
        #: `_insert_halos`.
        self._insert_halo: Any = None
        self._insert_halos: list = []
        #: Cursor elements show under the pointer (None = pointing hand);
        #: kept in step with the armed tool by `_sync_item_cursors`.
        self._element_cursor: Any = None
        self._insert_hover_state: str | None = None
        self._insert_hover_status = ""
        #: Cached page-flow inference for hover. Keyed by the layout
        #: generation, link count and current hierarchy level so a move or
        #: a topology change re-runs the (linear) vote, not every mouse move.
        self._page_flow_cache: tuple[tuple, str] | None = None
        self._delete_armed = False
        #: "fail" | "repair" | None -- Failure Effects click-to-target.
        #: Mirrors `_delete_armed`'s own arm/cursor/click pattern.
        self._fev_armed_mode: str | None = None
        #: Failure Effects View is open: a plain click on a block or event acts.
        self._fev_view_open = False
        self._anno_items: dict[int, Any] = {}
        #: link file id -> the GUI-only dog-leg grips drawn on that link.
        #: Never part of the model; persisted as `.rbds` annotations.
        self._route_grips: dict[int, list] = {}
        self._anno_seq = 1
        #: kind -> prop dict, the user's saved "style for new <Kind>s"
        #: (set from an existing annotation's context menu). Seeded from
        #: QSettings by MainWindow; empty means "factory look".
        self.default_annotation_props: dict[str, dict] = {}
        self._paste_buffer: list = []
        self._defer_block_refresh = False   # True while a batch is inserted
        self._model_token = clipboard_payload.new_model_token()
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
        #: The item under the press and its start position, used to carry
        #: the rest of a multi-selection by the same delta Qt moves it.
        self._drag_primary = None
        self._drag_primary_start: QPointF | None = None
        #: A routing point pressed for a drag: {entry id: (x, y) at press}.
        self._grip_press: dict[int, tuple[float, float]] = {}
        self._drag_last_delta: tuple[float, float] = (0.0, 0.0)
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
        #: Elements moved since the last coalesced settle. A drag updates
        #: only these on the settle; `sync_model_positions()` used to walk
        #: every element and every row for a one-block move.
        self._dirty_moves: set[tuple[str, int]] = set()
        #: Set when a move was signalled by an item that is not a movable
        #: element (so its id is unknown): the settle then takes the full
        #: pass rather than trust an empty dirty set.
        self._dirty_moves_unknown = False
        #: Bumped whenever an item's scene position changes or the level is
        #: rebuilt. The minimap polls a signature several times a second;
        #: hashing every item's position there was O(n) on an idle canvas.
        self._layout_generation = 0
        #: id -> file row lookup for `_write_positions_to_rows(only=...)`,
        #: keyed per section on the row list's identity, length and first/
        #: last row; a row addition, removal or replacement invalidates
        #: its entry (see `_row_for`).
        self._row_index_cache: dict[str, tuple[tuple, dict[int, dict]]] = {}
        #: Viewport-pixel anchor recorded at press, and whether the
        #: drag has moved far enough past it to count as a real drag
        #: yet -- see `mouseMoveEvent`'s own comment for why.
        self._press_viewport_pos: QPointF | None = None
        self._drag_armed = False
        #: Alt state captured at press, so a drag stays fine even on
        #: platforms/test harnesses whose synthetic move events omit the
        #: keyboard modifiers.
        self._fine_drag = False

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
        # Level-scoped parsed links: only links whose BOTH endpoints are on
        # the level currently shown (`_rebuild_scene` filters raw rows before
        # parsing). Consumers that need the whole model must read
        # LINK_DATA, not this list.
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
        self._refresh_anchor_leader()
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
        # Before the scene is cleared: clearing deletes every item and emits a
        # selection change for a selected one, and the enable-state refresh it
        # triggers reads `_anno_items` (issue #2: "_AnnoText already deleted").
        self._anno_items = {}
        self._anchor_leader = None
        # A pick armed for an annotation in the old model must not follow
        # into the new one (its id would name something else, or nothing).
        self._anchor_pick_for = None
        self._hidden_following = []
        self._layout_generation += 1
        self._row_index_cache.clear()
        # Ids from the level that is going away must not reach the next
        # settle: same numeric ids name different elements on a new level.
        self._dirty_moves.clear()
        self._dirty_moves_unknown = False
        self._scene.clear()
        self._block_items.clear()
        self._node_items.clear()
        self._event_items.clear()
        self._hier_items.clear()
        self._links.clear()
        self._route_grips = {}
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
        affected = (links_affected_by_moving(item) if item is not None
                    else self._links)
        # Port-side and centre decisions are cached during a batch rebuild
        # (entering a hierarchy). A move invalidates them for every endpoint
        # of every link whose route can change.
        for link in affected:
            for end in cast(
                    "tuple[Any, Any]", (link._from, link._to)):
                end._route_centre = None
                end._route_horizontal = None
                end._route_flow_sign = None
                end._route_entry_side = None
                end._route_exit_side = None
        if item is not None:
            item._route_centre = None
            item._route_horizontal = None
            item._route_flow_sign = None
            item._route_entry_side = None
            item._route_exit_side = None
        for link in affected:
            link._update_route()
        if self._loading:
            return
        self._layout_generation += 1
        # Which kind moved is a property of the item's class (O(1)); the
        # old form probed four id->item dicts on every mouse step. An item
        # whose kind cannot be told (not a movable element) forces the
        # settle's full pass instead of silently losing its coordinates.
        if isinstance(item, _BlockItem):
            self._dirty_moves.add(("block", obj_id))
        elif isinstance(item, _NodeItem):
            self._dirty_moves.add(("node", obj_id))
        elif isinstance(item, _EventItem):
            self._dirty_moves.add(("event", obj_id))
        elif isinstance(item, _HierItem):
            self._dirty_moves.add(("hier", obj_id))
        else:
            self._dirty_moves_unknown = True
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
        # every mouse step); only the elements this gesture moved need a
        # pass on a large level. An empty set means the gesture moved
        # nothing (a click that only armed the timer): no sync at all, in
        # particular not the full O(n) walk an `or None` fallback would do.
        only = set(self._dirty_moves)
        unknown = self._dirty_moves_unknown
        self._dirty_moves.clear()
        self._dirty_moves_unknown = False
        if only or unknown:
            self.sync_model_positions(only=only or None)
        if self._moved_pending:
            self._moved_pending = False
            self.layout_changed.emit()

    #: Legacy files lay elements out on a fixed grid in model units;
    #: rendering at a fixed scale with a model-wide origin means items
    # appear exactly where the file puts them -- identical on every
    # open, and consistent across level switches (no per-level fit).
    LEGACY_SCALE = view_transform.LEGACY_SCALE

    def _compute_model_transform(self, rbd: RbdFile, spacing: float = 1.0,
                                 band_aware: bool = False):
        """Whole-model origin/flip/scale, computed once per load and then
        frozen on the file (see `raptor.core.fileio.view_transform`)."""
        vt = view_transform.ensure(rbd, spacing, band_aware)
        self._vt = vt
        self._origin_x = vt.origin_x
        self._flip_base = vt.flip_base
        self._model_dx_min = vt.dx_min
        self._model_dy_min = vt.dy_min
        self._scale = vt.scale

    def load_rbd(self, rbd: RbdFile):
        log.debug("load_rbd: start (%s)", getattr(rbd, "path", "?"))
        # The Inspector may still hold a draft for the model being
        # replaced (Close/New/Open all end here). Flush before the swap.
        self.level_about_to_change.emit()
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
        self._model_token = clipboard_payload.new_model_token()
        # Units for the canvas labels, read once: the per-block label
        # formatter must not re-parse SYSTEM_DATA on every refresh.
        self._label_units = ""
        self._label_mode_cache: str | None = None
        self._deferred_sublabels = False
        try:
            from raptor.core.fileio.typed import system_config_from_row
            self._label_units = system_config_from_row(
                rbd.sections["SYSTEM_DATA"].rows[0]).units
        except (ValueError, KeyError, IndexError, AttributeError):
            pass
        # A phase choice belongs to the previous model.
        self._label_phase = -1
        # A hand-drawn diagram keeps the neighbour inference; Auto Layout
        # sets an explicit axis on the level it lays out.
        set_flow_direction("auto")
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
        self._dirty_moves.clear()
        self._dirty_moves_unknown = False
        self._row_index_cache.clear()
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


    _LOCATE = {"block": ("BLOCK_DATA", "ParentId", "_block_items"),
               "node": ("NODE_DATA", "parent", "_node_items"),
               "event": ("EVENT_DATA", "ParentId", "_event_items"),
               "hier": ("HIERARCHY_DATA", "Parent", "_hier_items")}


    #: Live Log row kind -> (element kind, RBD section, name column).
    _LOG_ELEMENT_KINDS = {
        "Block": ("block", "BLOCK_DATA", "blockName"),
        "Node": ("node", "NODE_DATA", "Name"),
        "Event": ("event", "EVENT_DATA", "eventName"),
        "Hier": ("hier", "HIERARCHY_DATA", "hierarchyName"),
    }


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
        name = self._name_index().get((kind, dependency_num))
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
        level-scoped, while the name index is whole-model and built on
        first use by `_name_index()`."""
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
        self._clear_link_insert_hover()
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
        # Large levels are usually shown fit-to-window, where LOD hides
        # labels. Build names only; one refresh after the fit fills the
        # sub-labels if the final zoom actually shows them.
        self._deferred_sublabels = True

        cur = self._current_parent
        log.debug("_rebuild_scene: level=%s", cur)
        # The page flow is per level: a level Auto Layout never touched
        # must not take its port sides from another level's direction.
        set_flow_direction(self.level_flow_direction())
        # Stable transform: origin/scale/flip come from the whole model
        # (computed once in load_rbd), so every element renders exactly
        # where its file row says -- same on every open, and identical
        # across hierarchy enter/leave. No per-level fit reshuffling.

        def on_level(parent: int) -> bool:
            return parent == cur

        def row_parent(row, col: str = "ParentId") -> int:
            """The raw row's parent, without parsing the whole element."""
            try:
                return int(float(row.get(col, 0) or 0))
            except (TypeError, ValueError, OverflowError):
                return 0

        # Filter raw rows by parent BEFORE block_from_row/node_from_row.
        # The old rebuild parsed every row in the model and then discarded
        # the hidden levels: on a model with thousands of elements that is
        # most of the cost of entering or leaving a hierarchy.
        visible_blocks: dict[int, RBDBlock] = {}
        if "BLOCK_DATA" in req(rbd).sections:
            for row in req(rbd).sections["BLOCK_DATA"].rows:
                if row_parent(row) != cur:
                    continue
                try:
                    block = block_from_row(row)
                except (ValueError, KeyError):
                    continue
                if block.id == 0:
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
            if self._deferred_sublabels:
                item.setToolTip(block.name)
            else:
                self._apply_block_labels(item, block)
            item.set_phased(block.uses_phasing)
            self._scene.addItem(item)
            self._block_items[bid] = item

        if "NODE_DATA" in req(rbd).sections:
            rows = req(rbd).sections["NODE_DATA"].rows
            log.debug("_rebuild_scene: scanning %d node rows", len(rows))
            for row in rows:
                if row_parent(row, "parent") != cur:
                    continue
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
        # The whole-model name index is only needed when at least one
        # visible block/node actually carries a dependency badge. Building
        # it unconditionally parsed every block/node/event a SECOND time on
        # every level change, even though most models have no dependencies.
        needs_name_index = any(
            getattr(b, "dependency_num", 0) for b in self.blocks.values()
        ) or any(
            getattr(n, "dependency_num", 0) for n in self.nodes.values()
        )
        self._global_name_index = (
            self._build_global_name_index() if needs_name_index else None)
        if needs_name_index:
            self._apply_dependency_badges()

        if "EVENT_DATA" in req(rbd).sections:
            from raptor.core.fileio.typed import event_from_row
            rows = req(rbd).sections["EVENT_DATA"].rows
            log.debug("_rebuild_scene: scanning %d event rows", len(rows))
            for row in rows:
                if row_parent(row) != cur:
                    continue
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
            visible_keys = (
                {("Block", i) for i in self._block_items}
                | {("Node", i) for i in self._node_items}
                | {("Event", i) for i in self._event_items}
                | {("Hier", i) for i in getattr(self, "_hier_items", {})}
            )

            def endpoint(row, side: str):
                try:
                    kind = str(row.get(f"{side}Type", "")).replace(
                        "RBD", "", 1)
                    return kind, int(float(row.get(f"{side}ID", 0)))
                except (TypeError, ValueError, OverflowError):
                    return None

            for row in rows:
                # A link can only be drawn when both ends are on this
                # level. Parsing every link in the file and then dropping
                # the hidden ones was a large part of the traversal cost.
                ends = (endpoint(row, "From"), endpoint(row, "To"))
                if None in ends or ends[0] not in visible_keys \
                        or ends[1] not in visible_keys:
                    continue
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
                                      link_num=link.object_num,
                                      defer_route=True)
                self._links.append(link_item)
                self._scene.addItem(link_item)
        # Ports depend on an element's other links, so recompute every
        # route once the whole topology exists. The deferred constructor
        # keeps the scene-wide link index from being rebuilt after every
        # insertion; the first `_update_route` below builds it once, and
        # the rest of the batch reuses it.
        with routing_batch(self._scene):
            for link_item in self._links:
                link_item._update_route()

        self._refresh_node_badges()

        log.debug("_rebuild_scene: %d blocks, %d nodes, %d events, %d "
                  "hiers, %d links placed -- entering _apply_scene_layout",
                  len(self._block_items), len(self._node_items),
                  len(self._event_items), len(self._hier_items),
                  len(self._links))
        self._apply_scene_layout()
        self._remember_render_extents()
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
            item.setZValue(float(a.get("z", ANNO_Z_DEFAULT)))
            self._scene.addItem(item)
            self._anno_items[int(a.get("id", 0))] = item

        # GUI-only routing grips ride in the annotation layer (so `.rbds`
        # round-trips them) but are not annotation items.
        self._route_grips = {}
        for entry in getattr(self._rbd_ref, "link_grips", []):
            link_id = _safe_int(entry.get("link"))
            found = next((item for item in self._links
                          if item._link_num == link_id), None)
            if found is None:
                continue
            link = cast(Any, found)
            grip = _RouteGripItem(
                link, QPointF(float(entry.get("x", 0.0)),
                              float(entry.get("y", 0.0))),
                entry_id=int(entry.get("id", 0)),
                active=bool(entry.get("active", True)))
            grip.setParentItem(link)
            link.add_grip(grip)
            self._route_grips.setdefault(link_id, []).append(grip)

        self._loading = False
        self.reset_zoom()   # frame the model on open (zoom stays free)


    # --- annotation anchors ------------------------------------------------
    # An annotation placed next to an element keeps an `anchor`
    # ({target, ref, location, gap}, or an arrow's
    # {from, to, from_ref, to_ref});
    # its position is then a function of the element's, so it moves with
    # the element (drag, align, undo, Auto Layout). Dragging the annotation
    # itself removes the anchor. `raptor.api.annotations` owns the format.

    _REF_KIND = {"Block": "block", "Node": "node", "Event": "event",
                 "Hier": "hier", "Annotation": "annotation"}
    _KIND_REF = {v: k for k, v in _REF_KIND.items()}


    #: Annotations hidden for the duration of a drag of the target they
    #: follow. Hiding them avoids showing the note at its old position (or
    #: over the space the target has left) until the element is dropped and
    #: `follow_anchors` has re-placed it.
    _hidden_following: list[int]


    #: The annotation waiting for a canvas click to choose its anchor, or
    #: None. Armed by the Inspector's "Pick on canvas…" button; the next
    #: click on an element or another annotation anchors to it.
    _anchor_pick_for: int | None = None

    #: Which end of an arrow the pending pick is for ("from"/"to"), or None
    #: for a box annotation (which has one target).
    _anchor_pick_end: str | None = None

    def _show_background_context_menu(self, event, scene_pos):
        """Empty-canvas context menu. There was no menu here at all
        before, which made right-click paste impossible and left Select
        All and the Snap toggle reachable only from the ribbon."""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        mx, my = self.scene_to_model(scene_pos)
        paste_act = menu.addAction("Paste Here (at pointer)")
        paste_act.setEnabled(self.can_paste())
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

    def _keys_of_items(self, items) -> set[tuple[str, int]] | None:
        """(kind, id) keys for `items`, or None when any item is not a
        movable element (an annotation, or a wrapper Qt has deleted).

        Used to keep bulk placements (Align/Flip/Distribute and their
        Undo) from taking the whole-level sync they used to, while still
        falling back to it for the kinds this map cannot name."""
        keys: set[tuple[str, int]] = set()
        for it in items:
            try:
                if isinstance(it, _BlockItem):
                    keys.add(("block", it._block_id))
                elif isinstance(it, _NodeItem):
                    keys.add(("node", it._node_id))
                elif isinstance(it, _EventItem):
                    keys.add(("event", it._event_id))
                elif isinstance(it, _HierItem):
                    keys.add(("hier", it._hier_id))
                else:
                    return None
            except RuntimeError:      # deleted C++ object
                return None
        return keys

    def _sync_items(self, items) -> None:
        """Record the canvas positions of just `items` (see
        `_keys_of_items`; falls back to the full pass when a kind is not
        understood)."""
        self.sync_model_positions(only=self._keys_of_items(items))

    def sync_model_positions(self, only: set[tuple[str, int]] | None = None):
        """Typed objects <- scene items for every movable kind, and the
        matching `_pos_overrides` entries (the position overlay's own
        source -- bulk movers that place items directly without MoveCmd
        (auto layout / align / distribute) must record here or their
        arrangement is lost on the next level change).

        `only` limits the pass to the elements a drag actually moved. A
        one-block move on a 10,000-element level used to walk every item
        and every file row just to record one coordinate.
        """
        # Blocks are positioned by their top-left, the other kinds by
        # their centre; both are scene pixels and both go through the one
        # transform.
        tables: dict[str, tuple[Any, Any, tuple[float, float]]] = {
            "block": (self._block_items, self.blocks,
                      (BLOCK_W / 2, BLOCK_H / 2)),
            "node": (self._node_items, self.nodes, (0.0, 0.0)),
            "event": (self._event_items, self.events, (0.0, 0.0)),
            "hier": (self._hier_items, self.hiers, (0.0, 0.0)),
        }
        if only is None:
            work = ((kind, oid, item)
                    for kind, (items, _by_id, _half) in tables.items()
                    for oid, item in items.items())
        else:
            # The selective pass exists so a one-element move is O(1):
            # iterate the ids that moved, not every item on the level.
            work = ((kind, oid, tables[kind][0][oid])
                    for kind, oid in only if kind in tables
                    and oid in tables[kind][0])
        for kind, oid, item in work:
            by_id, half = tables[kind][1], tables[kind][2]
            obj = by_id.get(oid)
            if obj is None:
                continue
            sp = item.scenePos()
            mx, my = self._vt.to_model(sp.x() + half[0],
                                       sp.y() + half[1])
            obj.x_position = mx
            obj.y_position = my
            self._pos_overrides[(kind, oid)] = (mx, my)
        self._write_positions_to_rows(only)

    def _row_for(self, section_name: str, oid: int) -> dict | None:
        """The file row for `oid` in one section, via a cached id index.

        A selective write-back used to scan every row to find one: O(n) per
        drag settle on a 10,000-element level. The cache is keyed on the
        row list's identity, its length and the identity of its first and
        last row -- enough to catch every append, removal and wholesale
        row replacement (the phase-edit restore deep-copies the list's
        rows in place, keeping list identity and length). A lookup that
        disagrees with the cached row's id rebuilds the index once and
        then falls back to a scan."""
        rbd = self._rbd_ref
        if rbd is None:
            return None
        sec = rbd.sections.get(section_name)
        if sec is None:
            return None

        def build() -> dict[int, dict]:
            key = (id(sec.rows), len(sec.rows),
                   id(sec.rows[0]) if sec.rows else 0,
                   id(sec.rows[-1]) if sec.rows else 0)
            cached = self._row_index_cache.get(section_name)
            if cached is not None and cached[0] == key:
                return cached[1]
            index: dict[int, dict] = {}
            for row in sec.rows:
                rid = _safe_int(row.get("ID"))
                if rid >= 0:
                    index.setdefault(rid, row)
            self._row_index_cache[section_name] = (key, index)
            return index

        row = build().get(oid)
        if row is not None and _safe_int(row.get("ID")) == oid:
            return row
        self._row_index_cache.pop(section_name, None)
        row = build().get(oid)
        if row is not None and _safe_int(row.get("ID")) == oid:
            return row
        # A genuinely misspelt/stale index (or no such row): one scan is
        # correct, and the rebuilt index makes the next lookup cheap.
        return next((r for r in sec.rows
                     if _safe_int(r.get("ID")) == oid), None)

    def _write_positions_to_rows(self,
                                 only: set[tuple[str, int]] | None = None) -> None:
        """The rows take the canvas positions as they are recorded. A bulk
        move (Align, Distribute, Flip, their Undo) left the rows at the old
        place until the next full write-back, so anything reading the rows
        in between -- Issues, a run, Save As .rbd -- saw two layouts."""
        rbd = self._rbd_ref
        if rbd is None:
            return
        sections = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                    "event": "EVENT_DATA", "hier": "HIERARCHY_DATA"}
        live: dict[str, dict[int, Any]] = {
            "block": self._block_items, "node": self._node_items,
            "event": self._event_items, "hier": self._hier_items}
        if only is not None:
            for kind, oid in only:
                section = sections.get(kind)
                if section is None:
                    continue
                xy = self._pos_overrides.get((kind, oid))
                if xy is None or oid not in live[kind]:
                    continue
                row = self._row_for(section, oid)
                if row is None:
                    continue
                row["xPos"], row["yPos"] = _pos(xy[0]), _pos(xy[1])
            return
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

    def view_options(self) -> dict:
        """The view-wide options a saved file carries (see `RbdFile.view`)."""
        from raptor.gui.ui.diagram_items import link_routing
        return {"link_routing": link_routing(),
                "block_labels": self._block_label_mode()}

    def apply_view_options(self, opts: dict) -> None:
        """Take a file's saved view options. Unknown or missing values leave
        the current setting."""
        from raptor.core.routing import ROUTING_MODES
        from raptor.gui.ui.diagram_items import set_link_routing
        mode = opts.get("link_routing")
        if mode in ROUTING_MODES:
            set_link_routing(mode)
        labels = opts.get("block_labels")
        if labels in dict(block_labels.MODES):
            app_settings.set_value("appearance/block_labels", labels)
            self._label_mode_cache = None

    def write_back_to_rbd(self, rbd: RbdFile):
        rbd.view = self.view_options()
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


    #: PM tab's 5 option checkboxes -- one generic setter,
    #: keyed the same way `_populate_pm`'s own loop builds them.
    _PM_OPTION_ATTRS = PM_OPTION_ATTRS


    #: Independent streams are seeds 1..N of FETCH_RAPTOR_SEED; system
    #: streams are the shared A..J codes 201..210; negative = antithetic
    #: (stores -1*n for the antithetic checkbox).
    STREAM_SYSTEM_MIN = 201
    STREAM_SYSTEM_MAX = 210


    #: Cost tab fields, (model attr, BLOCK_DATA column), in UI order.
    BLOCK_COST_FIELDS = BLOCK_COST_FIELDS


    # -- ModelHost: what the Qt-free editor reads from this view ---------


    # ------------------------------------------------------------------
    # Element creation / deletion (build-mode)
    #
    # New rows are cloned from a real row of the loaded file (the ID=0
    # template for blocks) and inserted into the same `RbdFile` the app
    # saves -- so write_back_to_rbd's existing row-sync covers them with
    # no extra bookkeeping.
    # ------------------------------------------------------------------


    #: HIERARCHY_DATA columns, in the exact order Raptor 7 files use them
    #: (HierarchyExample et al.) -- needed when a model has no hierarchy
    #: section yet and one must be bootstrapped.
    _HIER_COLUMNS = row_ops.HIER_COLUMNS


    # --- Cut / Copy / Paste ------

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

    #: Add tools that may insert in series on a connector: tool-mode ->
    #: (model kind, LINK_DATA endpoint token, status noun).
    _INSERT_TOOL_KINDS: dict[str, tuple[str, str, str]] = {
        "add_block": ("block", "Block", "Block"),
        "add_node": ("node", "Node", "Node"),
        "add_event": ("event", "Event", "Event"),
        "add_hierarchy": ("hier", "Hier", "Hierarchy"),
    }

    #: Screen-pixel pick radius for add-tool link hover, and the margin
    #: inside which two links count as ambiguous. Both convert through the
    #: view scale so the feel is the same at every zoom.
    _LINK_PICK_RADIUS_PX = 14.0
    _LINK_AMBIGUITY_PX = 5.0
    #: Insert-on-link side gaps are deliberately roomier than Auto Layout's
    #: minimum: an inserted element sits between two existing symbols and
    #: reads cramped at the bare minimum, even though the same gap is fine
    #: when Auto Layout places a whole chain.
    _INSERT_GAP_SCALE = 1.5

    @property
    def tool_mode(self) -> str:
        return self._tool_mode

    @tool_mode.setter
    def tool_mode(self, mode: str):
        changed = mode != self._tool_mode
        self._tool_mode = mode
        if changed:
            self._clear_link_insert_hover()
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
        # Kept for items that set their cursor again on hover (blocks and
        # nodes): they must show this one, not a hand, or the armed tool's
        # cursor is lost whenever an element passes under the pointer --
        # e.g. while wheel-zooming.
        self._element_cursor = tool_cursor
        for items in (self._block_items, self._node_items,
                      self._event_items, self._hier_items):
            for item in items.values():
                item.setCursor(tool_cursor if tool_cursor is not None
                               else hand)

    def element_cursor(self):
        """The cursor an element shows under the pointer: the armed tool's
        cursor, or the pointing hand when no tool is armed."""
        from PySide6.QtGui import QCursor
        cursor = getattr(self, "_element_cursor", None)
        return cursor if cursor is not None else QCursor(
            Qt.CursorShape.PointingHandCursor)

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
        # Nodes keep their name in `node_name`; every other kind in `name`.
        return (getattr(obj, "name", "") or getattr(obj, "node_name", "")
                or f"{kind} {oid}")

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
            info = self._insert_tool_info()
            if info is not None:
                target, state, block_reason = (
                    self._resolve_link_insert_target(scene_pos))
                if target is not None:
                    if state == "blocked":
                        # A refused target consumes the click: the element
                        # is not dropped somewhere the user did not mean.
                        _view_status(self, self._insert_message(
                            target, state, block_reason))
                        return
                    if isinstance(target, _LinkItem):
                        link_num = target._link_num
                        if link_num is not None and \
                                self.insert_element_on_link(
                                    info[0], link_num) is not None:
                            return
                    elif self.insert_element_in_parallel(
                            info[0], target) is not None:
                        return
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
                _view_status(self, "Node placed: one good path in is enough "
                             "(k = 1). For k-out-of-n, connect its paths, "
                             "then set Required good (k) in the Inspector.")
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
            if (src_kind, src_id) == (kind, eid):
                _view_status(self, "An element cannot be linked to itself "
                             "— click a different target")
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

    def leaveEvent(self, event):
        self._clear_link_insert_hover()
        super().leaveEvent(event)

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
        self._fine_drag = bool(
            event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if self._fine_drag:
            # Alt+drag is a fine move, not the start of a key-tip chord.
            self._suppress_key_tips()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_anchor = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._anchor_pick_for is not None:
            # Pick-anchor mode owns the next click: choose the target
            # rather than selecting, dragging or placing anything.
            if event.button() == Qt.MouseButton.RightButton:
                self.cancel_anchor_pick("Anchoring cancelled")
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                self._handle_anchor_pick(
                    self.mapToScene(event.position().toPoint()))
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
            self._grip_press = ({hit._entry_id: (hit.pos().x(), hit.pos().y())}
                                if isinstance(hit, _RouteGripItem) else {})
            primary = self._element_at(
                self.mapToScene(event.position().toPoint()))
            if primary is None and isinstance(hit, _AnnotationBase):
                primary = hit
            if (primary is not None and primary.isSelected()
                    and primary.flags()
                    & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
                self._drag_primary = primary
                self._drag_primary_start = primary.scenePos()
                self._drag_last_delta = (0.0, 0.0)
            else:
                self._drag_primary = None
                self._drag_primary_start = None
            # Arm the direction-aware band only when the press did NOT
            # land on something movable -- otherwise Qt is starting an
            # item move, not a rubber band, and `_band_anchor` must
            # stay None so `_apply_band_selection` stays out of it.
            # Pressing a link or the grid background counts as empty
            # canvas (neither is movable), matching what Qt itself
            # treats as a band drag.
            # Judge the ELEMENT, not the part pressed: a block's name is a
            # child text item that is not movable itself. Taking it for
            # empty canvas armed the band under an item drag, so every
            # element the dragged one passed over was selected and dragged
            # along (outside the gesture's undo record).
            # The nearest movable item at or above the one pressed: a
            # block's label is a child of the (movable) block, and a routing
            # point on a link is movable itself though its parent link is
            # not. Taking the top-level item made a grip drag arm the band,
            # which selected whatever the grip passed over, and Qt then
            # dragged those along with it.
            owner = hit
            while (owner is not None and not owner.flags()
                   & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
                owner = owner.parentItem()
            if owner is None and hit is not None:
                owner = hit.topLevelItem()
            self._band_anchor = (
                None if (owner is not None
                         and owner.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
                else event.position())
            # Where every element was at the press: whatever the gesture
            # moves, the release records it for undo.
            self._press_positions = {
                (kind, oid): (it.scenePos().x(), it.scenePos().y())
                for kind, table in self._element_tables()
                for oid, it in table.items()}
            hit_aid = getattr(hit, "_anno_id", None)
            if hit_aid is not None and not any(
                    k == "anno" and o == hit_aid
                    for k, o, _s in self._drag_start):
                state = self._anno_state(hit_aid)
                if state is not None:
                    self._drag_start.append(("anno", hit_aid, state))
            # Notes that follow a moving element would otherwise sit at
            # their old position, or over the space it has left, for the
            # whole gesture. Hide them until the release has re-placed
            # them.
            moved = {(k, o) for k, o, _s in self._drag_start
                     if k != "anno"}
            if moved:
                self._hide_following_annotations(moved)
            return

    # -------------------------------------------------- align/distribute
    def _selected_items(self):
        """All selected movable items (blocks, nodes, events, ovals,
        annotations) in selection order."""
        sel = [it for it in self._scene.selectedItems()
               if it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable]
        return sel

    @staticmethod
    def _move_by(item, dx, dy):
        item.setPos(item.pos() + QPointF(dx, dy))

    def _element_tables(self):
        """(kind, id -> canvas item) for every movable element kind."""
        return (("block", self._block_items), ("node", self._node_items),
                ("event", self._event_items),
                ("hier", getattr(self, "_hier_items", {})))

    def _move_group_with_primary(self) -> None:
        """Carry every other selected item by the primary item's drag delta.

        Qt's own item drag moves only the item under the cursor. A
        multi-selection should move as one block, so the remaining
        selected elements follow the primary's snapped delta; annotations
        use `_shift_annotation` because an arrow has two endpoints.
        """
        primary = self._drag_primary
        start = self._drag_primary_start
        if primary is None or start is None or len(self._drag_start) < 2:
            return
        if not isValid(primary) or not primary.isSelected():
            return
        now = primary.scenePos()
        delta = (now.x() - start.x(), now.y() - start.y())
        ldx = delta[0] - self._drag_last_delta[0]
        ldy = delta[1] - self._drag_last_delta[1]
        if abs(ldx) < 1e-9 and abs(ldy) < 1e-9:
            return
        self._drag_last_delta = delta
        saved = (self._snap_state["x"], self._snap_state["y"])
        # The primary already snapped. Re-snapping each follower on its own
        # would change their offsets, so apply the same unsnapped delta.
        self._snap_state["x"] = self._snap_state["y"] = 0.0
        try:
            tables = {
                "block": self._block_items,
                "node": self._node_items,
                "event": self._event_items,
                "hier": getattr(self, "_hier_items", {}),
                "anno": getattr(self, "_anno_items", {}),
            }
            for kind, oid, _start in self._drag_start:
                item = tables.get(kind, {}).get(oid)
                if item is None or item is primary or not isValid(item):
                    continue
                if kind == "anno":
                    _shift_annotation(item, ldx, ldy)
                else:
                    item.setPos(item.pos() + QPointF(ldx, ldy))
        finally:
            self._snap_state["x"], self._snap_state["y"] = saved

    def _editing_annotation(self):
        item = self._scene.focusItem()
        if isinstance(item, _AnnoText) and \
                item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction:
            return item
        return None

    def has_armed(self) -> bool:
        """Something on the canvas that Esc cancels."""
        return (self._editing_annotation() is not None
                or self.tool_mode != "select"
                or getattr(self, "_delete_armed", False)
                or getattr(self, "_anchor_pick_for", None) is not None
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
        self.cancel_anchor_pick("Anchoring cancelled")
        # A drag interrupted by Esc must not leave its following notes
        # hidden for good.
        self._show_following_annotations()
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
        if event.key() == Qt.Key.Key_F2:
            # Explicit text-edit entry, matching every other text control
            # in the app. Only a single text annotation has an unambiguous
            # F2 target.
            notes = [it for it in self._scene.selectedItems()
                     if isinstance(it, _AnnoText)]
            if len(notes) == 1:
                self._edit_annotation_text(notes[0])
                event.accept()
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
        # entirely; Alt+arrow = step/10. Same (kind, id, old, new) shape
        # mouse-drag release uses, so this is undoable exactly like a
        # drag (MoveCmd).
        if event.key() in _NUDGE_DIRS:
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                # Every Alt+arrow chord, with or without a selection, is a
                # canvas gesture: never let a later Alt release pop the
                # ribbon key tips.
                self._suppress_key_tips()
            dxs, dys = _NUDGE_DIRS[event.key()]
            off_grid = bool(event.modifiers()
                            & Qt.KeyboardModifier.ShiftModifier)
            fine = bool(event.modifiers()
                        & Qt.KeyboardModifier.AltModifier) and not off_grid
            if self.nudge_selection(dxs, dys, fine=fine, off_grid=off_grid):
                event.accept()
                return
            if (event.key() == Qt.Key.Key_Up
                    and event.modifiers()
                    & Qt.KeyboardModifier.AltModifier):
                # The Alt+Up command normally handles this before the key
                # reaches the canvas; when the view itself receives the
                # chord (no QAction match), an empty selection still
                # means the original hierarchy-up fallback.
                self._suppress_key_tips()
                self.go_up()
                event.accept()
                return
        # Scoped clipboard shortcuts: only when the canvas has focus, so
        # text fields keep their own Ctrl+C/V semantics.
        mods = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if mods and self.hasFocus() and text_editing.active_editor() is None:
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
    hover_inspector: Callable[..., str | None] | None = None
    #: Where the view sends a transient message (the window's status bar). Set by
    #: MainWindow; the mixins read it with `getattr(..., None)` so a bare view works.
    status_message_sink: Callable[[str], None] | None = None

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
        if self._insert_tool_info() is not None:
            self._update_link_insert_hover(event)
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
        # Alt+drag is a fine move: Qt's own item drag calls setPos(), and
        # every itemChange re-snaps to `_snap_state`, so the snap step
        # itself has to be subdivided for the duration of the move. Alt
        # may be pressed AFTER the press: latch it so the release uses
        # the fine epsilon for the one-step move that follows.
        fine_now = self._fine_drag or bool(
            event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if fine_now:
            self._fine_drag = True
        with self._fine_snap(fine_now):
            super().mouseMoveEvent(event)
        if self._drag_primary is not None and self._drag_armed:
            self._move_group_with_primary()
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
        fine_gesture = self._fine_drag or bool(
            event.modifiers() & Qt.KeyboardModifier.AltModifier)
        self._fine_drag = False
        # A minimum grid's tenth step can be well under half a scene unit
        # (e.g. snap 4 -> fine 0.4). The old 0.5 "did it move?" threshold
        # then discarded a legitimate one-step fine drag, so the gesture
        # showed on screen but produced no MoveCmd. Fine gestures accept
        # any real movement; ordinary drags keep the noise threshold.
        move_eps = 1e-6 if fine_gesture else 0.5
        if event.button() == Qt.MouseButton.MiddleButton:
            # Back to the armed tool's cursor, not a plain arrow: a pan
            # must not disarm the placement cursor.
            self._apply_tool_cursor()
            self._pan_anchor = None
            event.accept()
            return
        grip_moves = []
        if event.button() == Qt.MouseButton.LeftButton and self._grip_press:
            # A routing point was dragged: collect it here and let the
            # same gesture helper below fold it into a macro if anything
            # else moved too.
            pressed, self._grip_press = self._grip_press, {}
            for entry_id, old in pressed.items():
                grip = self.route_grip_by_entry(entry_id)
                if grip is None:
                    continue
                new = (grip.pos().x(), grip.pos().y())
                if (abs(new[0] - old[0]) > move_eps
                        or abs(new[1] - old[1]) > move_eps):
                    grip_moves.append((entry_id, old, new))
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
                if (abs(now[0] - start[0]) > move_eps
                        or abs(now[1] - start[1]) > move_eps):
                    moves.append((kind, oid, tuple(start), now))
            started = {(k, o) for k, o, _s in self._drag_start}
            tables = dict(self._element_tables())
            for (kind, oid), start in getattr(
                    self, "_press_positions", {}).items():
                if (kind, oid) in started:
                    continue
                it = tables[kind].get(oid)
                if it is None:
                    continue
                now = (it.scenePos().x(), it.scenePos().y())
                if (abs(now[0] - start[0]) > move_eps
                        or abs(now[1] - start[1]) > move_eps):
                    moves.append((kind, oid, start, now))
            self._press_positions = {}
            drag_start, self._drag_start = self._drag_start, []
            self._drag_primary = None
            self._drag_primary_start = None
            self._drag_last_delta = (0.0, 0.0)
            if anno_edits:
                moved_keys = {(k, i) for k, i, _o, _n in moves}
                # An annotation that is itself a target can move in
                # the same gesture; do not detach the notes that
                # follow it.
                moved_keys |= {("annotation", aid)
                               for aid, _old, _new in anno_edits}
                for _aid, old_state, new_state in anno_edits:
                    self._detach_if_moved_alone(old_state, new_state, moved_keys)
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
                    # selection into it -- positions are not committed;
                    # the child level lays out on its own. The reparent
                    # can affect rules, so this outer macro stays GATED;
                    # its move commands nest inside it (their own macro
                    # is ungated, but a nested macro is not checked).
                    # Flush before beginMacro so a pending CommentCmd is
                    # BELOW this macro: a refused macro must not take the
                    # typed comment with it.
                    self.structural_about_to_change.emit()
                    self._stack.beginMacro("Move into hierarchy")
                    try:
                        self._reparent_into(drop_hid, element_moves)
                        self._push_move_commands(
                            oval_moves, anno_edits, grip_moves,
                            label="Move")
                    finally:
                        self._stack.endMacro()
                else:
                    self._push_move_commands(
                        moves, anno_edits, grip_moves, label="Move")
                grip_moves = []
            elif anno_edits or grip_moves:
                self._push_move_commands(
                    [], anno_edits, grip_moves, label="Move")
                grip_moves = []
            if anno_edits:
                # Anything anchored to the annotations that just moved
                # follows them, including notes chained through another
                # annotation.
                self.follow_anchors({("annotation", aid)
                                     for aid, _old, _new in anno_edits})
            if not moves and not anno_edits and not grip_moves \
                    and len(drag_start) == 1:
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
            # The gesture is over and `follow_anchors` has put every note
            # where it belongs: show the ones hidden at press.
            self._show_following_annotations()
        if event.button() == Qt.MouseButton.LeftButton and grip_moves:
            # A routing-point drag with no element/annotation part (the
            # common case) still needs its one undo command.
            self._push_move_commands([], [], grip_moves)
        super().mouseReleaseEvent(event)

    def _item_comfortably_visible(self, item) -> bool:
        """Whether `item` is at least partially inside the current
        viewport."""
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        return visible.intersects(item.sceneBoundingRect())


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


    # --- Keyed-row editing (pools / triggers: no ID column; the row key
    # --- is PoolName / TriggerName). Engine re-parses these from the
    # --- file at the next run, so editing rows directly is sufficient.


    #: Item attributes that hold a text label, per element kind -- hidden
    #: together by `_apply_lod` when zoomed out (semantic zoom).
    _LOD_LABEL_ATTRS = ("_label", "_sub", "_name_label")


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
        if at_model is None:
            at_model = self.scene_to_model(
                self.mapToScene(self.viewport().rect().center()))
        ids = self._add_template_rows([row], [at_model], "Add block")
        return ids[0] if ids else 0

    def add_blocks_from_templates(self, rows: list[dict],
                                  centre: QPointF) -> list[int]:
        """Insert several library templates as new blocks, laid out in a
        grid (at most four across) around the scene point `centre`, as one
        undo step and one pass of the input-rule gate. Returns the new
        block ids (empty if the edit was refused)."""
        if not rows:
            return []
        across = min(len(rows), 4)
        step_x, step_y = BLOCK_W + 40.0, BLOCK_H + 30.0
        down = -(-len(rows) // across)
        spots = []
        for i in range(len(rows)):
            col, line = i % across, i // across
            spots.append(self.scene_to_model(QPointF(
                centre.x() + (col - (across - 1) / 2.0) * step_x,
                centre.y() + (line - (down - 1) / 2.0) * step_y)))
        return self._add_template_rows(
            rows, spots, "Add block" if len(rows) == 1
            else f"Add {len(rows)} blocks")

    def _add_template_rows(self, rows: list[dict], spots: list,
                           text: str) -> list[int]:
        """The template columns laid over a fresh block's full row (a library
        carries failure, repair, LDT and dependency data only; the rest of a
        block is the Block Defaults), added by one command. The input-rule
        gate checks the whole add once, and refuses it as a whole."""
        # The lowest free ids (gaps left by deleted blocks and ids held at
        # other levels are skipped), found in one pass over the rows.
        rbd = req(self._rbd_ref)
        used = set(self.blocks)
        for r in rbd.sections["BLOCK_DATA"].rows:
            used.add(_safe_int(r.get("ID")))
        new_ids: list[int] = []
        nid = 1
        while len(new_ids) < len(rows):
            if nid not in used:
                new_ids.append(nid)
            nid += 1
        new_rows = []
        for bid, tpl, (mx, my) in zip(new_ids, rows, spots):
            full = self._new_block_row(tpl.get("blockName", ""), mx, my,
                                       bid=bid)
            for col, val in tpl.items():
                if col not in ("blockName", "ID", "ParentId", "xPos", "yPos"):
                    full[col] = val
            new_rows.append(full)
        cmd = AddCmd(self, "block", new_rows)
        cmd.setText(text)
        before = len(rbd.sections["BLOCK_DATA"].rows)
        self._stack.push(cmd)
        if len(rbd.sections["BLOCK_DATA"].rows) == before:
            return []                  # the gate refused it
        return new_ids

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
        # Walk the scene's selection (O(selected)), not every id->item map
        # on the level (O(n)): this runs twice per click -- from
        # `block_selected` and again from `selection_changed` -- so on a
        # 10,000-element level the map walks cost tens of milliseconds per
        # click. Asking the scene also skips wrappers Qt has already
        # deleted (entering/leaving a hierarchy rebuilds the scene while
        # selection-changed signals are still being delivered).
        blocks: list[int] = []
        nodes: list[int] = []
        events: list[int] = []
        hiers: list[int] = []
        try:
            items = self._scene.selectedItems()
        except RuntimeError:          # scene torn down during shutdown
            return [], [], [], []
        for it in items:
            if isinstance(it, _BlockItem):
                if it._block_id != 0:
                    blocks.append(it._block_id)
            elif isinstance(it, _NodeItem):
                node = self.nodes.get(it._node_id)
                if node is not None and node.type_node == 2:
                    nodes.append(it._node_id)
            elif isinstance(it, _EventItem):
                events.append(it._event_id)
            elif isinstance(it, _HierItem):
                hiers.append(it._hier_id)
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

    def has_nudgeable_selection(self) -> bool:
        """Whether `nudge_selection` would move anything.

        Deliberately the same type tuple it uses, not
        `selected_movable_count`: an arrow annotation is not
        `ItemIsMovable` (its two ends move independently), but an
        arrow-only selection must still make Alt+Up nudge rather than
        silently ascend a hierarchy.
        """
        try:
            return any(isinstance(it, _NUDGEABLE_TYPES)
                       for it in self._scene.selectedItems())
        except RuntimeError:        # scene torn down during shutdown
            return False

    def apply_hier_dsi(self, dsi: dict[int, dict]) -> None:
        """Paint end-of-run Descendant Status Indicators on the ovals
        of the current level (`{hier_id: {"state", "nodes", "down",
        "degraded"}}`; missing/empty entries clear). Called by
        MainWindow after results land. The payload is kept so level
        traversal can re-apply it without a new run."""
        self._last_hier_dsi = dict(dsi or {})
        for hid, item in self._hier_items.items():
            item.set_dsi(dsi.get(hid))


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
        and links there are, and (via the view's layout generation) where
        the elements are.

        This polled every 200 ms, so hashing every element's position made
        an idle 10,000-element canvas do 50,000 hash-and-round operations a
        second for a picture that had not changed. The generation counter
        is bumped by the view whenever an item moves or the level is
        rebuilt, which is exactly what the fingerprint has to notice."""
        mv = self._main_view
        n = (len(mv._block_items) + len(mv._node_items)
             + len(mv._event_items) + len(getattr(mv, "_hier_items", {})))
        gen = getattr(mv, "_layout_generation", None)
        if gen is not None:
            return (n, gen, len(mv._links))
        # No generation counter (a caller with a plain view): fall back to
        # the positional fingerprint.
        acc = 0
        for items in (mv._block_items, mv._node_items, mv._event_items,
                      getattr(mv, "_hier_items", {})):
            for it in items.values():
                if isValid(it):
                    p = it.pos()
                    acc = (acc * 31 + hash((round(p.x(), 1),
                                            round(p.y(), 1)))) & 0xFFFFFFFF
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
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for it in items:
            if isinstance(it, _LinkItem):
                # The link's own route -- straight, angled, right-angle or
                # through routing points -- not a line between its ports.
                painter.drawPath(it.mapToScene(it.path()))
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

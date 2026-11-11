"""Moving the selection: align, flip, distribute and arrow-key nudging, as undoable commands.

Expects on `self`: the scene, `_rbd`, the undo stack and the item tables."""
from __future__ import annotations


from typing import Any

from shiboken6 import isValid
from PySide6.QtCore import (
    QPointF,
)

from raptor.gui.model_edit import (_pos)
from contextlib import contextmanager
from raptor.gui.ui.diagram_common import (
    FINE_MOVE_SUBDIVISIONS,
    _safe_int,
)
from raptor.gui.ui.diagram_commands import (
    BulkGeometryCmd,
    MoveCmd,
)
from raptor.gui.ui.diagram_items import (
    _BlockItem,
    _HierItem,
    _EventItem,
    _LinkItem,
    _RouteGripItem,
    _NodeItem,
    _next_grid_line,
)
from raptor.gui.ui.diagram_annotations import (
    AnnoEditCmd,
    _AnnotationBase,
    _shift_annotation,
)


#: Everything arrow-key nudge knows how to move. Arrow annotations are
#: included even though they are not `ItemIsMovable` (their endpoints are
#: independent); the Alt+Up enablement/handler must use this same predicate
#: or an arrow-only selection silently ascends instead of nudging.
from typing import TYPE_CHECKING

#: Everything arrow-key nudge knows how to move. Arrow annotations are
#: included even though they are not `ItemIsMovable` (their endpoints are
#: independent); the Alt+Up enablement/handler must use this same predicate
#: or an arrow-only selection silently ascends instead of nudging.
_NUDGEABLE_TYPES = (_BlockItem, _NodeItem, _EventItem, _HierItem,
                    _AnnotationBase, _RouteGripItem)


if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsView as _Base
    import PySide6.QtCore
    import PySide6.QtWidgets
    import raptor.core.model
    import raptor.gui.ui.diagram_element_items
    import raptor.gui.ui.diagram_item_core
    import raptor.gui.ui.diagram_link_items
else:
    _Base = object


class NudgeMixin(_Base):
    """Moving the selection: align, flip, distribute and arrow-key nudging, as undoable commands."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        def _apply_scene_layout(self) -> Any: ...
        _block_items: dict[int, raptor.gui.ui.diagram_item_core._BlockItem]
        def _compute_model_transform(self, rbd: Any, spacing: float = ..., band_aware: bool = ...) -> Any: ...
        _current_parent: int
        def _detach_if_moved_alone(self, old_state: dict, new_state: dict, moved: set) -> None: ...
        _event_items: dict[int, raptor.gui.ui.diagram_element_items._EventItem]
        _hier_items: dict[int, raptor.gui.ui.diagram_element_items._HierItem]
        def _keys_of_items(self, items: Any) -> set[tuple[str, int]] | None: ...
        _links: list[raptor.gui.ui.diagram_link_items._LinkItem]
        @staticmethod
        def _move_by(item: Any, dx: Any, dy: Any) -> Any: ...
        _node_items: dict[int, raptor.gui.ui.diagram_item_core._NodeItem]
        _pos_overrides: dict[tuple[str, int], tuple[float, float]]
        _rbd_ref: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _rebuild_scene(self) -> Any: ...
        _scene: PySide6.QtWidgets.QGraphicsScene
        def _selected_items(self) -> Any: ...
        _snap_state: dict[str, float]
        _stack: Any
        def _sync_items(self, items: Any) -> None: ...
        blocks: dict[int, raptor.core.model.RBDBlock]
        events: dict[int, Any]
        def follow_anchors(self, moved: set | None = ...) -> int: ...
        hiers: dict[int, raptor.core.model.RBDHier]
        layout_changed: PySide6.QtCore.SignalInstance
        nodes: dict[int, raptor.core.model.RBDNode]
        def route_grip_by_entry(self, entry_id: int) -> Any: ...
        def sync_model_positions(self, only: set[tuple[str, int]] | None = ...) -> Any: ...

    def _layout_state(self) -> tuple[dict, dict | None, tuple[str, str | None]]:
        """This level's element positions in model units (from the typed
        objects -- call `sync_model_positions` first), the canvas transform
        and the level's saved page flow: what Auto Layout changes, for its
        undo record."""
        pos: dict = {}
        for kind, table in (("block", self.blocks), ("node", self.nodes),
                            ("event", self.events), ("hier", self.hiers)):
            for oid, obj in table.items():
                if kind == "hier" and obj.parent_id != self._current_parent:
                    continue
                pos[(kind, oid)] = (obj.x_position, obj.y_position)
        # Annotations' geometry too: Auto Layout moves them with the diagram.
        for a in getattr(self, "annotations", []):
            pos[("anno", int(a.get("id", 0)))] = tuple(
                (k, a.get(k)) for k in ("x", "y", "x1", "y1", "x2", "y2") if k in a)
        rbd = self._rbd_ref
        vt = getattr(rbd, "view_transform", None) if rbd is not None else None
        flows = getattr(rbd, "flow_directions", None) or {}
        key = str(self._current_parent)
        flow = (key, flows.get(key))
        return pos, (dict(vt) if vt else None), flow

    def _restore_layout(
            self, state: tuple[dict, dict | None, tuple[str, str | None]]
    ) -> None:
        """Put back a `_layout_state`: the rows and the overlay take the
        positions, the transform is restored (or re-derived from those
        rows), and the level is rebuilt from them."""
        pos, vt, (flow_key, flow) = state
        rbd = self._rbd_ref
        if rbd is None:
            return
        # The level's page flow is part of what Auto Layout changed: put it
        # back too, or the link sides stay as the undone layout had them
        # and a later save keeps them. (`_rebuild_scene` applies it.)
        if not isinstance(getattr(rbd, "flow_directions", None), dict):
            rbd.flow_directions = {}
        if flow is None:
            rbd.flow_directions.pop(flow_key, None)
        else:
            rbd.flow_directions[flow_key] = flow
        cols = {"block": ("BLOCK_DATA", "ID"), "node": ("NODE_DATA", "ID"),
                "event": ("EVENT_DATA", "ID"), "hier": ("HIERARCHY_DATA", "ID")}
        annos = {aid: dict(geom) for (kind, aid), geom in pos.items() if kind == "anno"}
        for a in getattr(self, "annotations", []):
            if int(a.get("id", 0)) in annos:
                a.update(annos[int(a.get("id", 0))])
        for (kind, oid), value in pos.items():
            if kind == "anno":
                continue
            x, y = value
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
        keys = self._keys_of_items([it for it, _ in old_positions])
        stack = getattr(self, "_stack", None)
        if stack is not None:
            stack.push(BulkGeometryCmd(self, text, old_positions,
                                       new_positions, keys=keys))
        else:
            self.sync_model_positions(only=keys)
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
        if hasattr(item, "text_source"):
            state["text"] = item.text_source()
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
        # next level change (same rule auto_layout obeys below). Only the
        # selection moved: a full pass here was O(level) for aligning two
        # blocks. The placement is wrapped in an undo command.
        self._sync_items(sel)
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
        self._sync_items(sel)
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
        self._sync_items(sel)         # see align_selected's comment
        self._push_bulk_geometry("Distribute", old_positions)

    def _nudge_route_grips(self, chosen, moves, dxs, dys, off_grid,
                           step_x, step_y, fine=False):
        """Routing points for an arrow-key nudge: [(entry id, old, new)].

        A selected point moves by one grid step (1 px with Shift, a tenth
        of a step with Alt) like any element. A point on a link whose
        BOTH ends are being nudged goes with them by the same distance,
        so the dog-leg keeps its shape; only points already drawn
        (active) follow, so a point that has not been dragged yet does
        not start changing a route."""
        by_id: dict[int, tuple[float, float]] = {}
        shift: dict[int, tuple[float, float]] = {}
        for kind, oid, old, new in moves:
            item = (self._block_items.get(oid) if kind == "block"
                    else self._node_items.get(oid) if kind == "node"
                    else self._event_items.get(oid) if kind == "event"
                    else self._hier_items.get(oid))
            if item is not None:
                shift[id(item)] = (new[0] - old[0], new[1] - old[1])
        for link in self._links:
            a, b = shift.get(id(link._from)), shift.get(id(link._to))
            if a is None or b is None:
                continue
            for grip in link.grip_items():
                if getattr(grip, "_active", True):
                    by_id[grip._entry_id] = a
        out = []
        done: set[int] = set()
        for grip in list(chosen) + [self.route_grip_by_entry(i)
                                    for i in by_id]:
            if grip is None or not isValid(grip) or grip._entry_id in done:
                continue
            done.add(grip._entry_id)
            old = (grip.pos().x(), grip.pos().y())
            follow = by_id.get(grip._entry_id)
            if follow is not None:
                new = (old[0] + follow[0], old[1] + follow[1])
            else:
                # Alt (fine) adds the fractional step directly rather
                # than seeking the next fine grid line: an unaligned
                # point then still moves by exactly step/10 instead of
                # catching up to a line.
                if fine and not off_grid and step_x > 0 and dxs:
                    nx = old[0] + dxs * step_x
                else:
                    nx = old[0] + dxs if (off_grid or step_x <= 0) \
                        else (_next_grid_line(old[0], step_x, dxs)
                              if dxs else old[0])
                if fine and not off_grid and step_y > 0 and dys:
                    ny = old[1] + dys * step_y
                else:
                    ny = old[1] + dys if (off_grid or step_y <= 0) \
                        else (_next_grid_line(old[1], step_y, dys)
                              if dys else old[1])
                new = (nx, ny)
            if new != old:
                out.append((grip._entry_id, old, new))
        saved = (self._snap_state["x"], self._snap_state["y"])
        self._snap_state["x"] = self._snap_state["y"] = 0.0
        try:
            for entry_id, _old, new in out:
                grip = self.route_grip_by_entry(entry_id)
                if grip is not None:
                    grip.setPos(*new)
        finally:
            self._snap_state["x"], self._snap_state["y"] = saved
        return out

    def _suppress_key_tips(self) -> None:
        """A canvas Alt+arrow chord must not arm the ribbon key tips.

        The tips show on a quick, otherwise-unused Alt release; when the
        canvas consumes the arrow itself the window never sees that key
        and its `_alt_alone` flag would stay set, so Alt release popped
        the tips over the diagram. Clear it (and any already-shown tips)
        from the gesture that used the modifier.
        """
        win = self.window()
        if getattr(win, "_key_tips_active", False):
            dismiss = getattr(win, "_dismiss_key_tips", None)
            if callable(dismiss):
                dismiss()
        if hasattr(win, "_alt_alone"):
            win._alt_alone = False

    @contextmanager
    def _fine_snap(self, fine: bool):
        """Scale the live snap state to the Alt fine step for a gesture.

        Every item's `itemChange` re-snaps whatever position it is given
        to the CURRENT `_snap_state`, so simply dividing a drag/nudge
        distance never survives. Scaling the state (exactly the way
        Shift bypasses it by zeroing) makes the item land on the tenth
        grid while the gesture runs; the normal step is restored after.
        """
        if not fine:
            yield
            return
        saved = (self._snap_state["x"], self._snap_state["y"])
        self._snap_state["x"] = (saved[0] / FINE_MOVE_SUBDIVISIONS
                                 if saved[0] > 0 else 0.0)
        self._snap_state["y"] = (saved[1] / FINE_MOVE_SUBDIVISIONS
                                 if saved[1] > 0 else 0.0)
        try:
            yield
        finally:
            self._snap_state["x"], self._snap_state["y"] = saved

    def _push_move_commands(self, moves, anno_edits, grip_moves=(),
                            label: str = "Move") -> None:
        """Push one gesture's moves as one undo step.

        A gesture can produce two command kinds (element MoveCmd plus one
        AnnoEditCmd per annotation, or annotations plus a routed grip).
        Anything above one command becomes a macro, wrapped in
        `_stack.ungated()`: the rule gate would otherwise take its two
        whole-model snapshots at `beginMacro`/`endMacro` for a gesture
        that cannot break an input rule (nothing here changes model
        values, only geometry).
        """
        stack = self._stack
        grip_only = bool(grip_moves) and not moves and not anno_edits
        if grip_only:
            cmd = MoveCmd(self, [], grips=list(grip_moves))
            cmd.setText("Move routing point")
            stack.push(cmd)
            return
        has_move = bool(moves or grip_moves)
        total = len(anno_edits) + (1 if has_move else 0)
        if not total:
            return
        if total == 1:
            if anno_edits:
                aid, old_state, new_state = anno_edits[0]
                stack.push(AnnoEditCmd(self, aid, old_state, new_state,
                                       layout_only=True))
            else:
                stack.push(MoveCmd(self, moves, grips=list(grip_moves)))
            return
        # The ungated scope must cover beginMacro itself: that is where
        # the gate takes its "before" snapshot.
        with stack.ungated():
            stack.beginMacro(label)
            try:
                for aid, old_state, new_state in anno_edits:
                    stack.push(AnnoEditCmd(self, aid, old_state,
                                           new_state, layout_only=True))
                if has_move:
                    stack.push(MoveCmd(self, moves,
                                       grips=list(grip_moves)))
            finally:
                stack.endMacro()

    def nudge_selection(self, dxs: int, dys: int, *, fine: bool = False,
                        off_grid: bool = False) -> bool:
        """Move the selected elements/annotations/grips by one step.

        Plain arrows use one snap step, Shift uses 1 px off-grid, and Alt
        (fine) uses `step / FINE_MOVE_SUBDIVISIONS`. Same (kind, id, old,
        new) shape a mouse-drag release uses, so the whole nudge is one
        MoveCmd (plus one AnnoEditCmd for an annotation that carries its
        own geometry). Returns whether there was anything to move, so the
        Alt+Up command can fall back to hierarchy navigation.
        """
        selected = [it for it in self._scene.selectedItems()
                    if isinstance(it, _NUDGEABLE_TYPES)]
        if not selected:
            return False
        if fine:
            self._suppress_key_tips()
        base_x, base_y = self._snap_state["x"], self._snap_state["y"]
        if off_grid:
            step_x = step_y = 0.0
        else:
            step_x, step_y = base_x, base_y
        # Alt only subdivides a snap that is actually on; with snapping
        # off (or that axis at 0) it behaves exactly like a plain nudge.
        fine = fine and not off_grid and (base_x > 0 or base_y > 0)
        if fine:
            step_x = (base_x / FINE_MOVE_SUBDIVISIONS
                      if base_x > 0 else 0.0)
            step_y = (base_y / FINE_MOVE_SUBDIVISIONS
                      if base_y > 0 else 0.0)
        # itemChange() applies whatever self._snap_state currently holds
        # to every setPos(). Both off-grid and fine nudges silence it
        # while the explicit distance below is applied: a fine nudge must
        # be exactly step/10 even from an element that is not already on
        # the fine grid, and re-snapping to the nearest fine line would
        # otherwise make the distance depend on the starting offset.
        saved_snap = None
        if off_grid or fine:
            saved_snap = (base_x, base_y)
            self._snap_state["x"] = self._snap_state["y"] = 0.0
        moves = []
        anno_edits = []
        item: Any
        chosen_grips = [it for it in selected
                        if isinstance(it, _RouteGripItem)]
        try:
            for item in selected:
                if isinstance(item, _RouteGripItem):
                    continue                 # placed with the others below
                if isinstance(item, _AnnotationBase):
                    # Annotations aren't grid-snapped, so an arrow-key
                    # nudge moves by one snap increment (or 1px when
                    # snapping is off) rather than to the next grid line.
                    # Arrows shift both endpoints via the item's own
                    # translate_by override.
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
                # reported centre a few px off the circle.
                centre = _LinkItem._true_centre(item)
                offset = QPointF(old[0] - centre.x(), old[1] - centre.y())
                if off_grid or step_x <= 0:
                    new_cx = centre.x() + dxs * 1.0
                elif fine:
                    # Add the fractional step directly (snap is silenced
                    # for the gesture): an unaligned element still moves
                    # by exactly step/10 instead of catching up to a
                    # grid line.
                    new_cx = centre.x() + dxs * step_x
                else:
                    new_cx = (_next_grid_line(centre.x(), step_x, dxs)
                              if dxs else centre.x())
                if off_grid or step_y <= 0:
                    new_cy = centre.y() + dys * 1.0
                elif fine:
                    new_cy = centre.y() + dys * step_y
                else:
                    new_cy = (_next_grid_line(centre.y(), step_y, dys)
                              if dys else centre.y())
                item.setPos(new_cx + offset.x(), new_cy + offset.y())
                new = (item.pos().x(), item.pos().y())
                if old != new:
                    moves.append((kind, oid, old, new))
            grip_moves = self._nudge_route_grips(
                chosen_grips, moves, dxs, dys, off_grid, step_x, step_y,
                fine=fine)
        finally:
            if saved_snap is not None:
                self._snap_state["x"], self._snap_state["y"] = saved_snap
        moved_keys = {(k, i) for k, i, _o, _n in moves}
        # An annotation that is itself a target can move in the same
        # gesture; do not detach the notes that follow it.
        moved_keys |= {("annotation", aid)
                       for aid, _old, _new in anno_edits}
        for _aid, old_state, new_state in anno_edits:
            self._detach_if_moved_alone(old_state, new_state, moved_keys)
        # Two command kinds in one gesture become one macro (and one undo
        # step), off the whole-model rule gate.
        self._push_move_commands(moves, anno_edits, grip_moves,
                                 label="Nudge")
        if anno_edits:
            self.follow_anchors({("annotation", aid)
                                 for aid, _old, _new in anno_edits})
        return True

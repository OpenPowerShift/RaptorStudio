"""Auto Layout of the current level.

Expects on `self`: the scene, `_rbd`, the undo stack and the item tables."""
from __future__ import annotations


from typing import Any


from raptor.gui.ui.diagram_items import routing_batch
from raptor.gui.layout_options import (
    element_extents, grid_gap, layout_seeds, normalise_layout_options)
from raptor.gui.ui import settings as app_settings
from raptor.gui.ui.diagram_common import (
    BLOCK_H,
    BLOCK_W,
)
from raptor.gui.ui.diagram_commands import (
    LayoutCmd,
    RouteGripCmd,
)
from raptor.gui.ui.diagram_items import (
    _BlockItem,
    set_flow_direction,
)


#: Everything arrow-key nudge knows how to move. Arrow annotations are
#: included even though they are not `ItemIsMovable` (their endpoints are
#: independent); the Alt+Up enablement/handler must use this same predicate
#: or an arrow-only selection silently ascends instead of nudging.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsView as _Base
else:
    _Base = object


class AutoLayoutMixin(_Base):
    """Auto Layout of the current level."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        _apply_route_grip: Any
        def _apply_scene_layout(self) -> Any: ...
        _auto_layout_selection: Any
        _block_items: Any
        def _compute_model_transform(self, rbd: Any, spacing: float = ..., band_aware: bool = ...) -> Any: ...
        _current_parent: Any
        _layout_graph: Any
        _layout_state: Any
        _links: Any
        _loading: Any
        _model_dx_min: Any
        _model_dy_min: Any
        _node_items: Any
        _pos_overrides: dict[tuple[str, int], tuple[float, float]]
        _push_handover_grips: Any
        _rbd_ref: Any
        def _rebuild_scene(self) -> Any: ...
        _remember_flow_direction: Any
        _scene: Any
        _snap_state: Any
        _user_arranged: Any
        _vt: Any
        blocks: Any
        events: dict[int, Any]
        hiers: Any
        layout_changed: Any
        nodes: Any
        def sync_model_positions(self, only: set[tuple[str, int]] | None = ...) -> Any: ...
        def write_back_to_rbd(self, rbd: Any) -> Any: ...

    def auto_layout(self, direction: str | None = None,
                    max_columns: int | None = None,
                    spacing: float | None = None,
                    label_side: str | None = None):
        """Rearrange the whole level by graph topology: columns follow the
        longest-path layer from the Start node(s) (left = upstream), rows
        spread evenly within each column. Marks the model dirty via
        `model_changed` -- positions are real edits, written back on Save.

        With a selection, only the selected elements are laid out -- in
        isolation -- and translated so the group keeps its old centre;
        neighbours are untouched. With no selection, the whole level is
        laid out. The selection is kept either way."""
        direction, max_columns, spacing, label_side = normalise_layout_options(
            direction, max_columns, spacing, label_side, app_settings.get)
        set_flow_direction(direction)
        # Bipartite adjacency, ref-aware: ('Node', 3) and ('Block', 3)
        # are different elements -- keying by bare id let a layering walk
        # jump between kinds. Members = this level's blocks/nodes/hier
        # ovals/events; events are vertices on the flow (Node -> Event ->
        # Node), so the layout places them instead of leaving them where
        # their unrelated hierarchy ParentId puts them.
        members, edges = self._layout_graph()

        selected = {k for k, it in members.items() if it.isSelected()}
        if selected and len(selected) < len(members):
            sink: Any = getattr(self, "status_message_sink", None)
            if sink is not None:
                sink(f"Auto Layout moved only the {len(selected)} selected "
                     f"element(s); the other {len(members) - len(selected)} "
                     "stay where they are. Deselect all to lay out the "
                     "whole level.")
            return self._auto_layout_selection(
                selected, members, edges, direction=direction,
                max_columns=max_columns, spacing=spacing)

        # Seed from the current level's entry point: home uses its Start
        # markers; inside a hierarchy the flow enters through that
        # hierarchy's own In node -- otherwise there is nothing reachable
        # to seed from on sub-level contents.
        starts, pins = layout_seeds(self._current_parent, self.hiers, self.nodes)

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
        # A manual dog-leg is tied to the old positions. Keeping it would
        # leave several points sitting on top of each other after the
        # rebuild, so Auto Layout removes this level's points and draws
        # clean routes. They are restored by Undo with the positions.
        removed_grips = [
            dict(entry) for entry in getattr(self._rbd_ref, "link_grips", [])
            if int(entry.get("parent", 0)) == self._current_parent]
        for entry in removed_grips:
            self._apply_route_grip(entry, False)
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
                GUI_REFINE_BUDGET_S, layout_container_positions,
                refine_budget, LAYER_PITCH, ROW_PITCH,
            )
            from raptor.core.fileio.element_size import REFERENCE_SCALE
            rects = {key: item.sceneBoundingRect() for key, item in members.items()}
            sizes, render_extents = element_extents(
                {key: (r.width(), r.height()) for key, r in rects.items()},
                REFERENCE_SCALE)
            # This blocks the UI thread: the shorter GUI budget, not the
            # engine's default.
            with refine_budget(GUI_REFINE_BUDGET_S):
                pos = layout_container_positions(
                    members.keys(), edges, starts, pins=pins,
                    sizes=sizes, scale=REFERENCE_SCALE, direction=direction,
                    max_columns=max_columns, spacing=spacing)
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
            # Grid pitch = the smallest resulting column/row gap, so the
            # drawn grid follows the size-aware pitches rather than the
            # fixed constants.
            x_step = grid_gap({round(mx, 6) for mx, _my in pos.values()}, LAYER_PITCH)
            y_step = grid_gap({round(my, 6) for _mx, my in pos.values()}, ROW_PITCH)
        finally:
            self._loading = False
        with routing_batch(self._scene):
            for link in self._links:
                link._update_route()
        self._user_arranged = True
        self._remember_flow_direction()
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
            old_transform = dict(rbd.view_transform) if rbd.view_transform else None
            rbd.view_transform = None
            # Exact drawn extents for this layout. `view_transform.derive`
            # uses them for the scale; without this it would reserve the
            # fixed 108 px block and let a 216 px name overlap.
            # Merged with the other levels' extents seen so far: the scale
            # is file-wide, so a level laid out later must not forget how
            # wide the rest are drawn, or its scale (and every level's
            # spacing with it) would depend on which level was laid out last.
            rbd._render_extents = {  # type: ignore[attr-defined]
                **getattr(rbd, "_render_extents", {}), **render_extents}
            self._compute_model_transform(rbd, spacing, band_aware=True)
            # Annotations go with the diagram: anchored ones next to their
            # element again, the others at the same place in the model.
            from raptor.api.annotations import (
                follow_layout,
                make_room_for_annotations,
            )
            # Stretch gaps so anchored notes fit between stacked elements,
            # and tell the position overlay (a rebuild reads it, not the
            # rows) about every element that moved.
            kinds = {"BLOCK_DATA": "block", "NODE_DATA": "node",
                     "EVENT_DATA": "event", "HIERARCHY_DATA": "hier"}
            for (section, oid), xy in make_room_for_annotations(rbd).items():
                self._pos_overrides[(kinds[section], oid)] = xy
                room_tables: dict[str, dict[int, Any]] = {
                    "block": self.blocks, "node": self.nodes,
                    "event": self.events, "hier": self.hiers}
                obj = room_tables[kinds[section]].get(oid)
                if obj is not None:
                    obj.x_position, obj.y_position = xy
            follow_layout(rbd, old_transform, direction=direction,
                          label_side=label_side)
            self._rebuild_scene()
        # Up/Home (which rebuild from rows) and Save keep the layout
        self._apply_scene_layout()
        self.sync_model_positions()
        stack = getattr(self, "_stack", None)
        if stack is not None:
            # Pushed AFTER the rebuild: it records model positions, so the
            # rebuild cannot invalidate it. Its first redo is a no-op.
            stack.beginMacro("Auto Layout")
            try:
                for entry in removed_grips:
                    stack.push(RouteGripCmd(self, entry, add=False))
                stack.push(LayoutCmd(self, "Auto Layout", before,
                                     self._layout_state()))
                if max_columns is not None:
                    self._push_handover_grips(stack, direction)
            finally:
                stack.endMacro()
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

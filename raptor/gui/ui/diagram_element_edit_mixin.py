"""Undoable per-element edits on the canvas: names, comments, block and node parameters.

Expects on `self`: `_rbd`, the undo stack and the item tables."""
from __future__ import annotations


import math
from typing import Any, cast

from PySide6.QtCore import (
    QPointF,
)

from raptor.core.fileio.comments import (
    comment_text,
    find_comment_row,
    is_legacy_comment_row,
    names_agree,
)
from raptor.core.annotations import (
    ANNO_AUTO_LAYOUT_GAP,
    ANNO_DEFAULT_LOCATION,
)
from raptor.gui.ui.diagram_items import links_affected_by_moving, routing_batch
from raptor.gui.model_edit import (DEPENDENCY_BADGE, NODE_BADGE,
                                   SUBLABELS, EditedFields)
from raptor.gui.ui import block_labels
from raptor.gui.ui import settings as app_settings
from raptor.gui.ui.diagram_common import (
    _safe_int,
)
from raptor.gui.ui.diagram_items import flow_direction
from raptor.gui.ui.diagram_commands import (
    BlockCostCmd,
    DistCmd,
    ParamsCmd,
    RenameCmd,
    StreamCmd,
)
from raptor.gui.ui.diagram_items import (
    _LinkItem,
    _view_status,
)
from raptor.gui.ui.diagram_annotations import (
    AnnoCmd,
    AnnoEditCmd,
    CommentCmd,
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


class ElementEditMixin(_Base):
    """Undoable per-element edits on the canvas: names, comments, block and node parameters."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        STREAM_SYSTEM_MAX: Any
        STREAM_SYSTEM_MIN: Any
        _current_parent: Any
        _deferred_sublabels: Any
        _label_mode_cache: Any
        _label_phase: Any
        _layout_generation: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

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

    def _element_name_any_level(self, etype: str, oid: int) -> str:
        """Element name from the raw RBD rows, regardless of level.

        A comment command can replay after the user has moved the element
        into another hierarchy; the level-scoped pools no longer contain
        it, but the row and its name do.
        """
        section = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                   "event": "EVENT_DATA",
                   "hier": "HIERARCHY_DATA"}.get(etype)
        name_col = {"block": "blockName", "node": "Name",
                    "event": "eventName", "hier": "hierarchyName"}.get(etype)
        if section is None or name_col is None:
            return self._element_name(etype, oid)
        sec = (self._rbd_ref.sections.get(section)
               if self._rbd_ref is not None else None)
        for row in (sec.rows if sec is not None else []):
            if _safe_int(row.get("ID")) == int(oid):
                return row.get(name_col) or row.get("Name") or ""
        return self._element_name(etype, oid)

    def _comment_name_shared(self, etype: str, oid: int, name: str) -> bool:
        """Whether a surviving element of the same kind uses this name."""
        section, name_col = {
            "block": ("BLOCK_DATA", "blockName"),
            "node": ("NODE_DATA", "Name"),
            "event": ("EVENT_DATA", "eventName"),
            "hier": ("HIERARCHY_DATA", "hierarchyName"),
        }.get(etype, (None, None))
        if section is None or name_col is None:
            return False
        sec = (self._rbd_ref.sections.get(section)
               if self._rbd_ref is not None else None)
        return any(
            _safe_int(row.get("ID")) > 0
            and _safe_int(row.get("ID")) != int(oid)
            and names_agree(row.get(name_col, ""), name)
            for row in (sec.rows if sec is not None else []))

    def _comment_row_for(self, etype: str, oid: int, *,
                         stamp: bool = False):
        """The COMMENTS row for an element, by stored Type+ID first.

        Reads must leave a shared legacy no-ID row alone; only mutating
        callers pass ``stamp=True``. Names are compared through the
        legacy 20-char cap (a save may add a `~n` to one side only), and
        an ID-bearing row for another element is never reused just
        because the names match.
        """
        if self._rbd_ref is None:
            return None
        name = (self._element_name(etype, oid)
                or self._element_name_any_level(etype, oid))
        return find_comment_row(
            self._rbd_ref.sections.get("COMMENTS"), etype, oid, name,
            stamp=stamp)

    def current_comment_stored(self, etype: str, oid: int) -> str:
        """The COMMENTS row's raw stored value ('~' for newlines) for one
        element, or "" -- the undo command's "old" state.

        Read-only: never stamps a legacy row (menu enablement and
        Inspector population go through this).
        """
        if self._rbd_ref is None:
            return ""
        name = (self._element_name(etype, oid)
                or self._element_name_any_level(etype, oid))
        return comment_text(
            self._rbd_ref.sections.get("COMMENTS"), etype, oid, name)

    def has_convertible_comment(self, etype: str, oid: int) -> bool:
        """Whether a comment has visible text after `~` newlines expand.

        A row holding only separators ("~~") must offer no conversion:
        the action would be enabled but `comment_to_annotation` correctly
        refuses the blank result, so the click would look dead.
        """
        return bool(self.current_comment_stored(etype, oid)
                    .replace("~", "\n").strip())

    def _apply_element_comment_stored(self, etype: str, oid: int,
                                      stored: str) -> None:
        """Write `stored` (already '~'-joined) into the COMMENTS section.
        Emits `model_changed`; the undo command's `_done()` would double
        it, so commands call this without the emit via the wrapper below."""
        sec = self._rbd_ref.sections.get("COMMENTS") if self._rbd_ref else None
        if sec is None:
            return
        # Prefer the raw-row name: the element may already have moved to
        # another hierarchy level by the time this undo/redo runs.
        name = self._element_name_any_level(etype, oid)
        target_row = find_comment_row(sec, etype, oid, name)
        shadow_shared = False
        if (target_row is not None
                and is_legacy_comment_row(target_row)
                and self._comment_name_shared(etype, oid, name)):
            # A no-ID row shared by duplicate names belongs to no one in
            # particular: create this element's own ID row instead of
            # stamping/overwriting the sibling's comment. An empty write
            # still needs that ID row, so this element reads blank while
            # the sibling keeps the legacy text.
            target_row = None
            shadow_shared = True
        if target_row is None:
            if not stored and not shadow_shared:
                return
            sec.rows.append({"Type": etype, "ID": str(oid),
                             "Name": name, "Comment": stored})
        else:
            if is_legacy_comment_row(target_row):
                target_row["ID"] = str(oid)
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

    def comment_to_annotation(self, etype: str, oid: int,
                              scene_pos: QPointF | None = None):
        """Turn one element's stored comment into a text annotation.

        The comment (with the legacy `~` newlines restored) becomes the
        annotation's raw Markdown source. Placement is beside the element
        on this level (`above` by default, or `scene_pos` when a caller
        supplies one), and the new note is selected. Creation is one
        `AnnoCmd`, so undo removes it in one step. Returns the new item,
        or None when the element/comment is not there (or is blank).
        """
        etype = str(etype).lower()
        source = self.current_comment_stored(etype, oid).replace("~", "\n")
        if not source.strip() or self._rbd_ref is None:
            return None
        item = {"block": self._block_items, "node": self._node_items,
                "event": self._event_items,
                "hier": getattr(self, "_hier_items", {})}.get(
                    etype, {}).get(int(oid))
        if item is None:
            return None
        # One note per element comment: converting again selects the
        # existing note instead of creating a second linked copy.
        for a in getattr(self, "annotations", []):
            link = a.get("comment_of") or {}
            try:
                same = (link.get("type") == etype
                        and int(link.get("id", -1)) == int(oid))
            except (TypeError, ValueError):
                same = False
            if same:
                existing = self._anno_items.get(int(a.get("id", -1)))
                if existing is not None:
                    self._scene.clearSelection()
                    existing.setSelected(True)
                    self.centerOn(existing)
                    return existing
        from raptor.api.annotations import _auto_size, _place_at_location
        w, h = _auto_size(source)
        anchor = None
        if scene_pos is not None:
            x, y = scene_pos.x(), scene_pos.y()
        else:
            # Same geometry helper anchors use, so blocks/nodes/events/
            # hierarchy ovals all get a note just outside their outline
            # rather than at an arbitrary corner of the bounding box.
            ref_kind = self._KIND_REF.get(etype)
            geometry = (self._anchor_geometry(f"{ref_kind}:{int(oid)}")
                        if ref_kind else None)
            if geometry is None:
                centre = item.sceneBoundingRect().center()
                geometry = {"cx": centre.x(), "cy": centre.y(),
                            "half_w": item.sceneBoundingRect().width() / 2,
                            "half_h": item.sceneBoundingRect().height() / 2}
            try:
                spacing = float(app_settings.get("layout/spacing", 1.0) or 1.0)
            except (TypeError, ValueError):
                spacing = 1.0
            if not math.isfinite(spacing) or spacing <= 0.0:
                spacing = 1.0
            gap = ANNO_AUTO_LAYOUT_GAP * spacing
            x, y = _place_at_location(geometry, ANNO_DEFAULT_LOCATION,
                                      gap, w, h)
            if ref_kind:
                anchor = {
                    "target": self._anchor_element_name(etype, int(oid)),
                    "ref": f"{ref_kind}:{int(oid)}",
                    "location": ANNO_DEFAULT_LOCATION,
                    "gap": gap,
                }
        aid = self._next_anno_id()
        entry = {
            "kind": "text", "id": aid,
            "parent": self._current_parent,
            "x": round(x, 2), "y": round(y, 2),
            "w": w, "h": h, "text": source,
            "comment_of": {"type": etype, "id": int(oid)},
            "z": self.new_annotation_z(),
            **self._anno_default_props("text")}
        if anchor is not None:
            entry["anchor"] = anchor
        self._stack.push(AnnoCmd(self, entry, add=True))
        note = self._anno_items.get(aid)
        if note is not None:
            self._scene.clearSelection()
            note.setSelected(True)
        return note

    def _note_duplicate_name(self, kind: str, eid: int, name: str) -> None:
        """Status-bar notice (not a refusal: Raptor 7 allows a shared name)
        when another element of the same kind already has this name, ignoring
        case. Reports and messages quote names, so a repeat is ambiguous."""
        pool = {"block": lambda: {i: b.name for i, b in self.blocks.items()},
                "node": lambda: {i: n.node_name for i, n in self.nodes.items()},
                "event": lambda: {i: e.name for i, e in self.events.items()},
                "hier": lambda: {i: h.name for i, h in self.hiers.items()}}[kind]()
        key = name.casefold()
        same = [i for i, n in pool.items()
                if i != eid and str(n).casefold() == key]
        if same:
            others = ", ".join(str(i) for i in same[:3])
            _view_status(self, f"Note: another {kind} ({others}) is already "
                         f"named '{name}' (compared ignoring case "
                         "— reports will show both the same")

    def rename_block(self, block_id: int, name: str):
        if block_id not in self.blocks:
            return
        old = self.blocks[block_id].name
        if name == old:
            return
        self._stack.push(RenameCmd(self, "block", block_id, old, name))
        self._note_duplicate_name("block", block_id, name)

    def rename_node(self, node_id: int, name: str):
        if node_id not in self.nodes:
            return
        old = self.nodes[node_id].node_name
        if name == old:
            return
        self._stack.push(RenameCmd(self, "node", node_id, old, name))
        self._note_duplicate_name("node", node_id, name)

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

    def set_block_duty_cycle(self, block_id: int, value: float):
        block = self.blocks.get(block_id)
        if block is None or block.duty_cycle == value:
            return
        self._stack.push(ParamsCmd(self, "block duty cycle", block_id,
                                   block.duty_cycle, value))

    def set_block_start_age(self, block_id: int, value: float):
        block = self.blocks.get(block_id)
        if block is None or block.start_age == value:
            return
        self._stack.push(ParamsCmd(self, "block start age", block_id,
                                   block.start_age, value))

    def set_block_restoration_factor(self, block_id: int, value: float):
        """A factor on a block whose restoration is off also turns on Type I
        (one undo step), as the API does: the factor alone would be ignored,
        and validation refuses a factor with restoration off."""
        block = self.blocks.get(block_id)
        if block is None or block.restoration_factor == value:
            return
        self._stack.beginMacro("Restoration factor")
        try:
            if value > 0.0 and block.restoration_type not in (1, 2):
                self._stack.push(ParamsCmd(self, "block restoration type",
                                           block_id, block.restoration_type, 1))
            self._stack.push(ParamsCmd(self, "block restoration factor",
                                       block_id, block.restoration_factor,
                                       value))
        finally:
            self._stack.endMacro()

    def set_block_restoration_type(self, block_id: int, value: int):
        """Turning restoration off also clears the factor (one undo step):
        off ignores it, and validation refuses a factor with restoration
        off."""
        block = self.blocks.get(block_id)
        if block is None or block.restoration_type == value:
            return
        self._stack.beginMacro("Restoration type")
        try:
            if value == 0 and block.restoration_factor != 0.0:
                self._stack.push(ParamsCmd(self, "block restoration factor",
                                           block_id, block.restoration_factor,
                                           0.0))
            self._stack.push(ParamsCmd(self, "block restoration type",
                                       block_id, block.restoration_type, value))
        finally:
            self._stack.endMacro()

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

    def _apply_streams(self, bid: int, f: int | None,
                       r: int | None) -> None:
        self._editor.apply_streams(bid, f, r)

    @classmethod
    def valid_stream(cls, v: int, max_ind: int) -> bool:
        return ((-max_ind <= v <= -1) or (1 <= v <= max_ind)
                or (cls.STREAM_SYSTEM_MIN <= v <= cls.STREAM_SYSTEM_MAX))

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
        # Anchors keep the target's name beside its id: keep them in step.
        if self._rbd_ref is not None:
            from raptor.api.annotations import refresh_anchor_names
            refresh_anchor_names(self._rbd_ref)
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
        self._note_duplicate_name("hier", hid, name)

    def _sync_event_row(self, eid: int):
        self._editor.sync_event_row(eid)

    def rename_event(self, eid: int, name: str):
        event = self.events.get(eid)
        if event is None or name == event.name:
            return
        self._stack.push(RenameCmd(self, "event", eid, event.name, name))
        self._note_duplicate_name("event", eid, name)

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

    def _block_label_mode(self) -> str:
        """The label mode, read once per rebuild/refresh, not per block."""
        cached = getattr(self, "_label_mode_cache", None)
        if cached is not None:
            return str(cached)
        mode = str(app_settings.get("appearance/block_labels",
                                    block_labels.DEFAULT_MODE))
        self._label_mode_cache = mode
        return mode

    def _apply_block_labels(self, item, block):
        """Write a block's current label mode, phase and units onto its
        canvas item. The three item-creation/refresh call sites all go
        through here so a mode or phase change cannot leave one path
        showing stale numbers."""
        lines, tip = block_labels.describe(
            block, mode=self._block_label_mode(),
            phase=self._label_phase, units=self._label_units)
        item.set_sublabel(lines, tip)

    def set_label_phase(self, phase: int) -> None:
        """Show a phase's law in the block labels (-1 = the model's own)."""
        phase = int(phase)
        if phase == self._label_phase:
            return
        self._label_phase = phase
        self.refresh_block_labels()

    def level_flow_direction(self) -> str:
        """The page flow Auto Layout last used on the level on screen, or
        "auto" (infer it from the level's own links)."""
        flows = getattr(self._rbd_ref, "flow_directions", None) or {}
        return str(flows.get(str(self._current_parent), "auto"))

    def _remember_flow_direction(self) -> None:
        rbd = self._rbd_ref
        if rbd is None:
            return
        if not isinstance(getattr(rbd, "flow_directions", None), dict):
            rbd.flow_directions = {}
        rbd.flow_directions[str(self._current_parent)] = flow_direction()

    def _remember_render_extents(self) -> None:
        """Record how wide this level's elements are drawn, for the
        file-wide scale `view_transform.derive` picks after an Auto Layout."""
        rbd = self._rbd_ref
        if rbd is None:
            return
        seen = getattr(rbd, "_render_extents", None)
        if not isinstance(seen, dict):
            seen = {}
            rbd._render_extents = seen  # type: ignore[attr-defined]
        for section, table in (("BLOCK_DATA", self._block_items),
                               ("NODE_DATA", self._node_items),
                               ("HIERARCHY_DATA",
                                getattr(self, "_hier_items", {}))):
            for oid, item in cast("dict[int, Any]", table).items():
                rect = item.sceneBoundingRect()
                seen[(section, str(oid))] = (rect.width() / 2.0,
                                             rect.height() / 2.0)

    def reroute_links(self) -> None:
        """Redraw every link (after the routing style changes). Layout only:
        no element moves and the model is not marked changed."""
        with routing_batch(self.scene()):
            for it in list(self.scene().items()):
                if isinstance(it, _LinkItem):
                    it._update_route()
        # The minimap redraws when the layout generation moves.
        self._layout_generation += 1

    def refresh_block_labels(self) -> None:
        """Re-apply the label setting to every visible block (mode/phase)."""
        self._label_mode_cache = None
        self._deferred_sublabels = False
        self._refresh_block_sublabels()

    def _refresh_block_sublabels(self, block_id: int | None = None):
        """Update the at-a-glance lines under every block name.
        `block_id=None` refreshes every block (after load/retheme)."""
        targets = ([block_id] if block_id is not None
                   else list(self._block_items))
        resized: list[Any] = []
        for bid in targets:
            item = self._block_items.get(bid)
            block = self.blocks.get(bid)
            if item is None or block is None:
                continue
            width = item.rect().width()
            self._apply_block_labels(item, block)
            item._adjust_label()
            # A longer label widens the box; the link ports were computed
            # against the old width and would now sit inside the block.
            if item.rect().width() != width:
                resized.append(item)
        if resized:
            # Also while a level is still being built: the deferred
            # sub-labels are applied at the end of `_rebuild_scene`, after
            # the links were routed against the unlabelled 108 px boxes.
            # Skipping this then left every route 2 px off its port, so
            # the next re-route (a link-style change) moved them.
            self._clear_route_caches()
            affected: dict[int, Any] = {}
            for item in resized:
                for link in links_affected_by_moving(item):
                    affected[id(link)] = link
            with routing_batch(self._scene):
                for link in affected.values():
                    link._update_route()

    def _block_row_by_id(self, bid: int):
        return self._editor.block_row(bid)

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

    def set_block_costs(self, block_id: int, values: list[float],
                        index: int | None = None):
        """Block cost fields.

        With `index`, change just `values[index]` and write only that
        BLOCK_DATA column: an unrelated cost edit must not reformat the
        other 13 (which would truncate long decimals and turn blanks into
        `0.000000`). Without it, all 14 fields, as the Inspector's Apply
        does.
        """
        block = self.blocks.get(block_id)
        if block is None or len(values) != len(self.BLOCK_COST_FIELDS):
            return
        if index is not None:
            if not 0 <= index < len(self.BLOCK_COST_FIELDS):
                return
            attr, col = self.BLOCK_COST_FIELDS[index]
            old = float(getattr(block, attr))
            new = float(values[index])
            if abs(old - new) < 1e-9:
                return
            row = self._block_row_by_id(block_id)
            old_text = (row.get(col, "") if row is not None
                        else f"{old:.6f}")
            self._stack.push(BlockCostCmd(self, block_id, attr, col,
                                          old, old_text, new))
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
        if hasattr(item, "text_source"):
            start_state["text"] = item.text_source()
        start_state["z"] = item.zValue()
        end_state = dict(start_state)
        changes = dict(changes)
        if "anchor" in changes:
            # Anchor or unanchor from the Inspector: anchoring places the
            # annotation next to its element at once; removing the anchor
            # leaves it where it is.
            anchor = changes.pop("anchor")
            if anchor is None:
                end_state.pop("anchor", None)
            else:
                end_state["anchor"] = dict(anchor)
                placed = self._anchored_geometry(end_state)
                if placed is None:
                    sink: Any = getattr(self, "status_message_sink", None)
                    if sink is not None:
                        sink("Cannot anchor there: that target is not on this level")
                    return
                end_state.update(placed)
        end_state.update(changes)
        if end_state == start_state:
            return
        # A geometry-only props edit is a drag/resize, not a property
        # change: keep it off the whole-model validation path.
        geometry = {"x", "y", "w", "h", "x1", "y1", "x2", "y2", "z"}
        self._stack.push(AnnoEditCmd(
            self, aid, start_state, end_state,
            layout_only=set(changes) <= geometry))

    def _apply_block_cost(self, bid: int, attr: str, col: str,
                          value: float, text: str) -> None:
        """One block cost field, in the model and ONLY its BLOCK_DATA
        column. `text` is the row's exact serialized value, so undo can
        restore a long decimal or a blank verbatim."""
        block = self.blocks.get(bid)
        if block is not None:
            setattr(block, attr, value)
        if self._rbd_ref is None:
            return
        row = self._editor.block_row(bid)
        if row is not None:
            row[col] = text

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
            # Only the edited element can have an unsynced canvas position
            # here. The full pass walked every item and row on the level,
            # which made each Inspector/Data Tables edit O(n).
            self.sync_model_positions(only={(kind, oid)})
        self._editor.write_element(self._rbd_ref, kind, oid)
        for refresh in refreshes:
            if refresh.what == SUBLABELS:
                self._refresh_block_sublabels(refresh.oid)
            elif refresh.what == DEPENDENCY_BADGE:
                self._refresh_dependency_badge(refresh.kind, refresh.oid)
            elif refresh.what == NODE_BADGE:
                self._refresh_node_badges()
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
        # The label shows the law's mean: a new type (repair None, say) is
        # a new mean even when the parameters are left as they were.
        self._refresh_block_sublabels(bid)

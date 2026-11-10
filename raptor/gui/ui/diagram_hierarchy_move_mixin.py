"""Moving elements into, out of and between hierarchies.

Expects on `self`: the scene, `_rbd`, the undo stack and the item tables."""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req

from typing import Any

from PySide6.QtCore import (
    QPointF,
)

from raptor.core.edit.hierarchy_plan import (plan_dissolve)
from raptor.gui.ui.diagram_common import (
    _PARENT_COLS,
    _safe_int,
)
from raptor.gui.ui.diagram_commands import (
    DissolveCmd,
    MoveOutCmd,
    ReParentCmd,
)
from raptor.gui.ui.diagram_items import (
    _view_status,
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


class HierarchyMoveMixin(_Base):
    """Moving elements into, out of and between hierarchies."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _statusbar_msg: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

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
        self.structural_about_to_change.emit()   # draft before ReParentCmd
        section_of = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                      "event": "EVENT_DATA"}
        parent_moves = []
        moved_by_section: dict[str, set[int]] = {}
        for kind, oid, _start, _now in moves:
            section = section_of[kind]
            moved_by_section.setdefault(section, set()).add(oid)
            row = self._row_for(section, oid)
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
        self.structural_about_to_change.emit()   # draft before the command
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
                row = self._row_for(section, oid)
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

"""Phase assignment and keyed-row editing (pools and triggers) on the canvas.

Expects on `self`: the scene, `_rbd`, the undo stack and the item tables."""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req

from typing import Any


from raptor.core.edit import row_ops
from raptor.core.fileio.typed import (
    link_from_row,
)
from raptor.gui.ui.diagram_common import (
    _PARENT_COLS,
    _safe_int,
)
from raptor.gui.ui.diagram_commands import (
    IdCellCmd,
    PhaseCmd,
    TableCellCmd,
    TableRowCmd,
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


class PhaseKeyedMixin(_Base):
    """Phase assignment and keyed-row editing (pools and triggers) on the canvas."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        links: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

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

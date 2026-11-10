"""Module-level helpers and small widgets shared by the Data Tables dock and its mixins."""
from __future__ import annotations

from collections.abc import Callable



from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QTableWidget, QTableWidgetItem,
)

from raptor.gui.ui.table_export import cell_text


def _fmt_stat(val) -> str:
    if val is None:
        return "—"
    if isinstance(val, str):
        return val
    return f"{val:.4g}"


#: A grid with more rows than this builds its cell widgets (combos, entry
#: fields) only for the rows on screen plus a margin, and the rest as they
#: scroll into view (#146): a widget costs ~1 ms, a row has up to 9.
_LAZY_ROWS = 60
_LAZY_MARGIN = 12

#: Columns every grid shares; a grid's own tip for the same name wins.
_COMMON_COL_TIPS = {
    "Name": "The element's name",
    "ID": "The element's id, as the Explorer, the Inspector and exported "
          "tables show it in brackets (read-only)",
    "Level": "The hierarchy level this element is on (Whole model only; "
             "read-only)",
}


class _DataTable(QTableWidget):
    """A grid that refuses a sort on a cell-widget column.

    Distribution/parameter cells are widgets, not items. Qt still showed
    a sort arrow and reordered by empty keys, so the arrow lied. Treat
    those headers as non-sortable.
    """

    #: Set by the dock: called with the clipboard's text when Ctrl+V is
    #: pressed on the grid itself (not in a cell's editor). True = handled.
    paste_text: Callable[[str], bool] | None = None

    def keyPressEvent(self, event) -> None:
        from PySide6.QtGui import QKeySequence
        if (self.paste_text is not None
                and event.matches(QKeySequence.StandardKey.Paste)):
            if self.paste_text(QApplication.clipboard().text()):
                event.accept()
                return
        super().keyPressEvent(event)

    def sortItems(self, column: int,
                  order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        if any(self.cellWidget(row, column) is not None
               for row in range(self.rowCount())):
            self.horizontalHeader().setSortIndicator(
                -1, Qt.SortOrder.AscendingOrder)
            return
        super().sortItems(column, order)


class _SortItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically when both cells parse as
    numbers. The default item compares its text lexically, which puts
    "10" before "9" and "2" after "19" on every ID/param/count column.

    A cell with a stashed full-precision value (`RAW_VALUE_ROLE`, the
    Costs grid's cost) sorts by that value, not its rounded display text.
    """

    def _number(self) -> float | None:
        table = self.tableWidget()
        try:
            if table is not None:
                return float(cell_text(table, self.row(), self.column(),
                                       raw=True).replace(",", ""))
            return float(self.text())
        except (TypeError, ValueError):
            return None

    def __lt__(self, other):
        mine, theirs = self._number(), other._number()
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text().casefold() < other.text().casefold()


#: Canonical parameter counts per distribution, from Raptor 7.
_NUM_PARAMS = {1: 2, 2: 1, 3: 2, 4: 2, 5: 2, 6: 3, 7: 2, 8: 2, 9: 2, 10: 2,
               11: 3, 12: 3, 13: 1, 14: 3, 15: 3, 16: 0, 17: 1, 18: 1,
               19: 1, 20: 3, 21: 2, 22: 2}


def _is_time_param(label: str) -> bool:
    """Same exclusion the Inspector's own `_distribution_params_row` uses: Shape,
    N Trials and Probability slots are never time-dimensioned
    regardless of the model's own units."""
    return bool(label) and label not in (
        "Shape", "Shape2", "N Trials", "Probability")


class _WholeModel:
    """Every level's blocks, nodes and events, read from the file's rows.

    The view's own `blocks`/`nodes`/`events` hold the level on screen only.
    Here the level on screen keeps its live objects (they are the ones the
    edit paths change) and every other level is built from its rows. Nothing
    is built on the canvas for another level. `level` names where an element
    lives ("Home", "Pump Bank", "Pump Bank / Stage 2")."""

    def __init__(self, view) -> None:
        from raptor.core.fileio.typed import (
            block_from_row, event_from_row, hier_from_row, node_from_row)
        rbd = view._rbd_ref
        #: What this cache was built from. `set_view` clears it for a new
        #: model; the view's change signals clear it for a level change or
        #: an element/hierarchy edit. `matches` is the defensive check.
        self.view = view
        self.rbd = rbd
        self.live_blocks = set(view.blocks)
        self.live_nodes = set(view.nodes)
        self.live_events = set(view.events)
        self.blocks: dict = {}
        self.nodes: dict = {}
        self.events: dict = {}
        self._parent: dict[tuple[str, int], int] = {}
        hiers: dict[int, tuple[str, int]] = {}
        sec = rbd.sections.get("HIERARCHY_DATA")
        for row in (sec.rows if sec is not None else []):
            try:
                h = hier_from_row(row)
            except (ValueError, KeyError):
                continue
            if h.id:
                hiers[h.id] = (h.name, h.parent_id)
        self.level_count = 1 + len(hiers)
        names: dict[int, str] = {0: "Home"}

        def level_name(pid: int) -> str:
            if pid in names:
                return names[pid]
            chain, seen, cur = [], set(), pid
            while cur and cur in hiers and cur not in seen:
                seen.add(cur)
                chain.append(hiers[cur][0])
                cur = hiers[cur][1]
            names[pid] = " / ".join(reversed(chain)) or "Home"
            return names[pid]
        self._level_name = level_name
        for kind, section, build, live, store in (
                ("block", "BLOCK_DATA", block_from_row, view.blocks,
                 self.blocks),
                ("node", "NODE_DATA", node_from_row, view.nodes,
                 self.nodes),
                ("event", "EVENT_DATA", event_from_row, view.events,
                 self.events)):
            sec = rbd.sections.get(section)
            for row in (sec.rows if sec is not None else []):
                try:
                    obj = build(row)
                except (ValueError, KeyError):
                    continue
                if obj.id == 0 and kind != "node":
                    continue          # the column-template row
                if kind == "node" and obj.id == 0:
                    continue
                store[obj.id] = live.get(obj.id, obj)
                self._parent[(kind, obj.id)] = obj.parent_id

    def level(self, kind: str, oid: int) -> str:
        return self._level_name(self._parent.get((kind, oid), 0))

    def is_live(self, kind: str, oid: int) -> bool:
        return oid in {"block": self.live_blocks, "node": self.live_nodes,
                       "event": self.live_events}[kind]

    def matches(self, view) -> bool:
        """True while this cache still describes `view` and its file.
        `set_view` and the view's own change signals clear/invalidate it
        on a new model, a level change, or an element/hierarchy edit."""
        return (self.view is view and self.rbd is view._rbd_ref
                and self.live_blocks == set(view.blocks)
                and self.live_nodes == set(view.nodes)
                and self.live_events == set(view.events))

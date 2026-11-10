"""Data Tables: the cost columns and their editing.

Expects on `self`: the dock's tabs and tables."""
from __future__ import annotations

import math
from typing import Any

from raptor.gui.ui.qtcompat import req


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QTableWidgetItem,
)

from raptor.gui.model_edit import (
    BLOCK_COST_DECIMALS, BLOCK_COST_FIELDS, BLOCK_COST_LIMIT,
    cost_field_readonly)
from raptor.gui.ui.table_export import RAW_VALUE_ROLE
from raptor.core.model import SparingType


from raptor.gui.ui.data_tables_common import (
    _SortItem,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
else:
    _Base = object


class DataTablesCostsMixin(_Base):
    """Data Tables: the cost columns and their editing."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _COST_DECIMALS: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    @classmethod
    def _fmt_cost(cls, value: float) -> str:
        """A cost cell's text: the Inspector's three decimals."""
        return f"{float(value):.{cls._COST_DECIMALS}f}"

    @staticmethod
    def _parse_cost(text: str) -> float | None:
        """The typed cost, or None when it is not a finite, in-range
        number. The Inspector's spin boxes cover the same range."""
        try:
            value = float(text.strip())
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or abs(value) > BLOCK_COST_LIMIT:
            return None
        return round(value, BLOCK_COST_DECIMALS)

    @staticmethod
    def _cost_field_readonly(block, attr: str) -> bool:
        """A Pooled block's spare and emergency-shipping costs come from
        its pool, not its own row (the Inspector greys them for the same
        reason)."""
        return cost_field_readonly(block, attr)

    def _set_cost_text(self, item: QTableWidgetItem, value: float) -> None:
        """Put a canonical value back in a cost cell without re-entering
        the commit handler."""
        table = item.tableWidget()
        if table is None:
            return
        table.blockSignals(True)
        try:
            item.setText(self._fmt_cost(value))
            item.setData(RAW_VALUE_ROLE, float(value))
        finally:
            table.blockSignals(False)

    def _populate_costs(self):
        """The Costs grid: one row per block, the 14 BLOCK_DATA cost
        columns as editable items. Plain items (not cell widgets) keep
        numeric sorting and CSV export working; the commit handler routes
        the whole row through `set_block_costs`, so one edit is one undo
        step and the file row is written at once."""
        t = self._costs_table
        t.setUpdatesEnabled(False)
        t.blockSignals(True)
        try:
            t.setRowCount(0)
            if self._view is None:
                return
            src = self._active_src()
            bids = sorted(src.blocks)
            t.setRowCount(len(bids))
            for r, bid in enumerate(bids):
                b = src.blocks[bid]
                t.setItem(r, t.columnCount() - 1,
                          self._level_item("block", bid))
                name_item = _SortItem(b.name)
                name_item.setFlags(name_item.flags()
                                   & ~Qt.ItemFlag.ItemIsEditable)
                t.setItem(r, 0, name_item)
                id_item = _SortItem(str(bid))
                id_item.setFlags(id_item.flags()
                                 & ~Qt.ItemFlag.ItemIsEditable)
                t.setItem(r, 1, id_item)
                for i, (attr, _col) in enumerate(BLOCK_COST_FIELDS):
                    value = float(getattr(b, attr))
                    item = _SortItem(self._fmt_cost(value))
                    item.setData(RAW_VALUE_ROLE, value)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                    if self._cost_field_readonly(b, attr):
                        item.setFlags(item.flags()
                                      & ~Qt.ItemFlag.ItemIsEditable)
                        item.setForeground(Qt.GlobalColor.gray)
                    t.setItem(r, 2 + i, item)
                self._tag_row_with_name(t, r, b.name)
                # Exact Inspector wording, not the name-prefixed cell
                # convention: the two read-only cells must say where the
                # cost really lives.
                if b.sparing_type == SparingType.SPARE_POOL:
                    for i, (attr, _col) in enumerate(BLOCK_COST_FIELDS):
                        if self._cost_field_readonly(b, attr):
                            self._set_cell_tip(t, r, 2 + i,
                                               self._POOLED_COST_TIP)
        finally:
            t.blockSignals(False)
            t.setUpdatesEnabled(True)

    def _on_cost_cell_changed(self, item: QTableWidgetItem):
        """Commit a Costs cell as one `set_block_costs` command.

        Only the edited column takes the typed number; the other 13
        values come from the model, so a rounded display never rewrites
        them. With several rows selected the same column is set on each
        (other values preserved) inside one macro.
        """
        t = self._costs_table
        if self._view is None:
            return
        col = item.column()
        first = 2
        last = first + len(BLOCK_COST_FIELDS)
        if not first <= col < last:
            return
        bid = self._row_key(t, item.row())
        if bid is None:
            return
        src = self._src()
        cached = src.blocks.get(bid)
        block = self._view.blocks.get(bid) or cached
        if block is None:
            return
        idx = col - first
        attr, raw_header = BLOCK_COST_FIELDS[idx]
        header = self._COST_LABELS.get(raw_header, raw_header)
        old = float(getattr(block, attr))
        if self._cost_field_readonly(block, attr):
            self._set_cost_text(item, old)
            self.status_message.emit(
                f"{header}: a pooled block takes this cost from its pool")
            return
        value = self._parse_cost(item.text())
        if value is None:
            self._set_cost_text(item, old)
            self.validation_failed.emit(
                f"{header}: enter a finite number between "
                f"-{self._COST_LIMIT:g} and {self._COST_LIMIT:g}.")
            return
        keys = self._targets(t, bid)
        if abs(value - old) < 1e-9 and len(keys) == 1:
            self._set_cost_text(item, old)
            return

        read_only = 0
        pooled = 0
        todo: list[tuple[int, list[float]]] = []
        for k in keys:
            if self._is_foreign("block", k):
                read_only += 1
                continue
            other = self._view.blocks.get(k)
            if other is None:
                continue
            if self._cost_field_readonly(other, attr):
                pooled += 1
                continue
            values = [getattr(other, a) for a, _c in BLOCK_COST_FIELDS]
            if abs(values[idx] - value) < 1e-9:
                continue
            values[idx] = value
            todo.append((k, values))
        if not todo:
            self._set_cost_text(item, old)
            if read_only or pooled:
                self.status_message.emit(
                    f"Nothing to set: {self._row_count(len(keys))} "
                    f"selected, none can take a {header}.")
            return
        if len(keys) > 1 and not self._confirm_multi(len(todo)):
            self._set_cost_text(item, old)
            return

        stack = req(self._view)._stack
        before = stack.count()
        grouped = len(todo) > 1
        if grouped:
            stack.beginMacro(f"Set {header} on {self._row_count(len(todo))}")
        try:
            for k, values in todo:
                self._view.set_block_costs(k, values, index=idx)
        finally:
            if grouped:
                stack.endMacro()
        if stack.count() == before:
            self._set_cost_text(item, old)     # refused: put the value back
            return
        self._set_cost_text(item, value)
        if len(todo) > 1:
            rows_by_id = {self._row_key(t, r): r
                          for r in range(t.rowCount())}
            for k, _values in todo:
                if k == bid:
                    continue
                row_idx = rows_by_id.get(k)
                cell = t.item(row_idx, col) if row_idx is not None else None
                if cell is not None:
                    self._set_cost_text(cell, value)
        self.model_edited.emit()
        if len(keys) > 1:
            note = (f"Set {header} to {self._fmt_cost(value)} on "
                    f"{self._row_count(len(todo))}")
            if read_only:
                note += ("; 1 read-only row on another level skipped"
                         if read_only == 1 else
                         f"; {read_only} read-only rows on other levels "
                         "skipped")
            if pooled:
                note += ("; 1 pooled row takes it from its pool"
                         if pooled == 1 else
                         f"; {pooled} pooled rows take it from their pool")
            self.status_message.emit(note)

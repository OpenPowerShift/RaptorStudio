"""Data Tables: multi-row edits, fill-down, paste and parameter setters.

Expects on `self`: the dock's tabs and tables."""
from __future__ import annotations

from typing import Any

from raptor.gui.ui.qtcompat import req


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QTableWidget, QComboBox,
)

from raptor.gui.ui.distro_combo import distro_display_name
from raptor.gui.ui import param_entry
from raptor.core.distro_rules import (
    DISTRO_PARAM_LABELS, validate_distro_params,
)
from raptor.core.fileio.rbdlib import DIST_NPARAMS as DIST_NPARAMS_COUNT


from raptor.gui.ui.data_tables_common import (
    _is_time_param,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
else:
    _Base = object


class DataTablesEditMixin(_Base):
    """Data Tables: multi-row edits, fill-down, paste and parameter setters."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def fill_down(self) -> bool:
        """Ctrl+D: the first editable selected row's value in the current
        column goes to the other selected rows.

        The source must be editable: a read-only Pooled or foreign row
        must not lend its value to the rows that can take an edit."""
        table = self._edit_table()
        col = table.currentColumn()
        rows = sorted({i.row() for i in table.selectionModel().selectedIndexes()
                       if not table.isRowHidden(i.row())})
        if col < 0 or len(rows) < 2:
            return False
        from PySide6.QtWidgets import QLineEdit

        def other_rows(row: int) -> list[int]:
            """Rows after the source first, so its own commit is not a
            no-op in the cell's editor."""
            at = rows.index(row)
            return rows[at + 1:] + rows[:at]

        for row in rows:
            w = table.cellWidget(row, col)
            if w is not None:
                edit = w.findChild(QLineEdit)
                if edit is not None:
                    if edit.isEnabled():
                        return self._fill_text(
                            table, other_rows(row), col, edit.text())
                    continue
                if isinstance(w, QComboBox) and w.isEnabled():
                    return self._fill_combo(table, rows, col, w)
                continue
            item = table.item(row, col)
            if item is not None and (
                    item.flags() & Qt.ItemFlag.ItemIsEditable):
                return self._fill_text(
                    table, other_rows(row), col, item.text())
        return False

    def _fill_text(self, table: QTableWidget, rows: list[int], col: int,
                   text: str, start: int = 0) -> bool:
        """Type `text` into column `col` of one of `rows` (the first from
        `start`): its commit fans out to every selected row."""
        from PySide6.QtWidgets import QLineEdit
        done = False
        for r in rows[start:]:
            w = table.cellWidget(r, col)
            if w is None:
                # A plain item cell (the Costs grid's costs): its commit
                # runs from itemChanged, and a multi-row selection fans
                # that one commit out to every selected row.
                item = table.item(r, col)
                if item is None or not (
                        item.flags() & Qt.ItemFlag.ItemIsEditable):
                    continue
                if table is self._costs_table:
                    # Fill/Paste always commits, even when the first
                    # editable row already holds this text: a multi-row
                    # selection still has to carry it to the others.
                    table.blockSignals(True)
                    try:
                        item.setText(text)
                    finally:
                        table.blockSignals(False)
                    self._on_cost_cell_changed(item)
                else:
                    item.setText(text)
                done = True
                break
            e = w.findChild(QLineEdit)
            if e is None or not e.isEnabled():
                continue
            e.setText(text)
            e.editingFinished.emit()           # the row's own commit path
            done = True
            break      # the first commit fans out to every selected row
        return done

    def _fill_combo(self, table: QTableWidget, rows: list[int], col: int,
                    combo: QComboBox) -> bool:
        for row in rows:
            target = table.cellWidget(row, col)
            if (target is combo or not isinstance(target, QComboBox)
                    or not target.isEnabled()):
                continue
            if target.currentIndex() == combo.currentIndex():
                continue            # no change signal: try the next target
            target.setCurrentIndex(combo.currentIndex())
            return True
        return False

    def paste_into_selection(self, text: str) -> bool:
        """Paste of one plain value with several rows selected: it goes to
        the current column of every selected row."""
        table = self._edit_table()
        sm = table.selectionModel()
        rows = sorted({i.row() for i in sm.selectedIndexes()
                       if not table.isRowHidden(i.row())}) if sm else []
        text = text.strip()
        if len(rows) < 2 or "\n" in text or "\t" in text or not text:
            return False
        return self._fill_text(table, rows, table.currentColumn(), text)

    def _entry_values(self, distro: int, values: list[float]) -> list[float]:
        """Canonical values -> what the entry-mode shows, in shown units."""
        from raptor.core.units_conv import native_to_display
        native_u = getattr(self, "_native_units", "") or ""
        display_u = getattr(self, "_display_unit", None)
        labels = DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
        # Empirical distributions carry up to 12 data points, while the
        # label table describes the three named parameters. Values past the
        # label list have no time-parameter label, so they are shown as-is;
        # indexing `labels` by the value position crashed the whole model
        # load for `Distribution1_Empirical12`.
        shown = [
            native_to_display(v, native_u, display_u)
            if j < len(labels) and _is_time_param(labels[j]) else v
            for j, v in enumerate(values)]
        return param_entry.to_entry(distro, shown)

    def _has_param(self, bid: int, which: str, index: int) -> bool:
        """Whether block `bid`'s distribution has a `index`-th parameter."""
        b = req(self._src()).blocks.get(bid)
        if b is None:
            return False
        code = b.fail_distro if which == "F" else b.repair_distro
        return index < DIST_NPARAMS_COUNT.get(code, 3)

    def _set_param_entry(self, bid: int, which: str, index: int,
                         raw: float) -> bool:
        use = "Failure" if which == "F" else "Repair"
        return self._apply_rows(
            bid, f"{use} Param {index + 1}", f"{raw:g}",
            lambda k: self._set_param_entry_one(k, which, index, raw),
            applicable=lambda k: self._has_param(k, which, index))

    def _set_param_entry_one(self, bid: int, which: str, index: int,
                             raw: float) -> bool:
        """Commit one cell typed in Raptor's entry mode."""
        block = req(self._view).blocks.get(bid)
        if block is None:
            return False
        distro = block.fail_distro if which == "F" else block.repair_distro
        use = "Failure" if which == "F" else "Repair"
        old = list(block.fail_values if which == "F" else block.repair_values)
        if param_entry.is_rate_field(distro, index) and raw <= 0.0:
            self.validation_failed.emit(f"{use} Rate must be greater than 0")
            return False
        entered = self._entry_values(distro, (old + [0.0] * 3)[:3])
        entered[index] = raw
        from raptor.core.units_conv import native_to_display, display_to_native
        native_u = getattr(self, "_native_units", "") or ""
        display_u = getattr(self, "_display_unit", None)
        base_labels = DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
        shown_old = [
            native_to_display(v, native_u, display_u)
            if _is_time_param(base_labels[j]) else v
            for j, v in enumerate((old + [0.0] * 3)[:3])]
        converted = param_entry.from_entry(distro, entered, old=shown_old)
        vals = [
            display_to_native(converted[j], native_u, display_u)
            if _is_time_param(base_labels[j]) else converted[j]
            for j in range(3)]
        err = validate_distro_params(distro, vals, use)
        if err is not None:
            return False
        if which == "F":
            req(self._view).set_block_fail_params(bid, vals)
        else:
            req(self._view).set_block_repair_params(bid, vals)
        self.model_edited.emit()
        return True

    def _set_param(self, bid: int, which: str, index: int, value: float) -> bool:
        use = "Failure" if which == "F" else "Repair"
        return self._apply_rows(
            bid, f"{use} Param {index + 1}", f"{value:g}",
            lambda k: self._set_param_one(k, which, index, value),
            applicable=lambda k: self._has_param(k, which, index))

    def _set_param_one(self, bid: int, which: str, index: int,
                       value: float) -> bool:
        block = req(self._view).blocks.get(bid)
        if block is None:
            return False
        distro = block.fail_distro if which == "F" else block.repair_distro
        use = "Failure" if which == "F" else "Repair"
        vals = list(block.fail_values if which == "F" else block.repair_values)
        vals[index] = value
        err = validate_distro_params(distro, vals, use)
        if err is not None:
            return False
        if which == "F":
            req(self._view).set_block_fail_params(bid, vals)
        else:
            req(self._view).set_block_repair_params(bid, vals)
        self.model_edited.emit()
        return True

    def _set_dist(self, bid: int, which: str, code: int) -> bool:
        # Routed through the view so the change lands on the undo stack.
        use = "Failure Dist" if which == "F" else "Repair Dist"
        ok = self._apply_rows(
            bid, use, distro_display_name(code, is_failure=which == "F"),
            lambda k: req(self._view).change_block_dist(k, which, code))
        if ok:
            self.model_edited.emit()
        return ok

    def _set_pm(self, bid: int, value: float) -> bool:
        self._apply_rows(
            bid, "PM Frequency", f"{value:g}",
            lambda k: req(self._view).set_block_pm_freq(k, value) or True)
        self.model_edited.emit()
        return True

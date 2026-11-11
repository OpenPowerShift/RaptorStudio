"""Data Tables: the sparing and preventive-maintenance tables.

Expects on `self`: the dock's tabs and tables."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.gui.ui.qtcompat import req


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QTableWidgetItem,
    QComboBox,
)



from raptor.gui.ui.data_tables_common import (
    _SortItem,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
    import PySide6.QtCore
    import PySide6.QtWidgets
    import raptor.gui.ui.data_tables_common
else:
    _Base = object


class DataTablesSparingMixin(_Base):
    """Data Tables: the sparing and preventive-maintenance tables."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        def _active_src(self) -> Any: ...
        def _apply_rows(self, oid: int, what: str, value: str, fn: Any, applicable: Any = ...) -> bool: ...
        def _label_row_widgets(self, table: Any, r: int) -> None: ...
        _lazy_pending: dict[PySide6.QtWidgets.QTableWidget, set[int]]
        def _level_item(self, kind: str, oid: int) -> Any: ...
        def _param_cell(self, table: Any, row: Any, col: Any, value: float, on_commit: Any, is_time: bool = ..., *, shown_value: float | None = ..., on_commit_entry: Any = ...) -> Any: ...
        _pm_table: raptor.gui.ui.data_tables_common._DataTable
        def _realize_initial(self, t: Any) -> None: ...
        def _set_pm(self, bid: int, value: float) -> bool: ...
        _sparing_table: raptor.gui.ui.data_tables_common._DataTable
        @classmethod
        def _tag_row_with_name(cls, t: Any, row: int, name: str) -> None: ...
        def _text_cell(self, table: Any, row: int, col: int, value: str, on_commit: Any) -> Any: ...
        _view: Any  # really Any | None
        model_edited: PySide6.QtCore.SignalInstance

    def _populate_sparing(self):
        """Sparing tab (Raptor 7 "Block Input Tables" -- Sparing
        tab): one row per block, columns for every sparing-related
        field regardless of that block's current type (a block that
        isn't Custom/Pooled/Resource just has blank/inert cells for
        the fields that don't apply to it, matching a plain grid's
        natural shape rather than hiding columns per row)."""
        t = self._sparing_table
        t.setUpdatesEnabled(False)
        t.setRowCount(0)
        self._lazy_pending[t] = set()
        if self._view is None:
            t.setUpdatesEnabled(True)
            return
        src = self._active_src()
        bids = sorted(src.blocks)
        t.setRowCount(len(bids))
        for r, bid in enumerate(bids):
            b = src.blocks[bid]
            t.setItem(r, t.columnCount() - 1,
                      self._level_item("block", bid))
            name_item = _SortItem(b.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 0, name_item)
            id_item = _SortItem(str(bid))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 1, id_item)
        self._lazy_pending[t] = set(bids)
        t.setUpdatesEnabled(True)
        self._realize_initial(t)

    def _build_sparing_widgets(self, t, r: int, bid: int) -> None:
        """The cell widgets of one Sparing row, their hover text and names."""
        b = req(self._active_src()).blocks.get(bid)
        if b is None:
            return
        from PySide6.QtWidgets import QLineEdit
        from raptor.core.fileio.typed import (
            _SPARING_TYPE_BY_NAME, SPARING_TYPE_TO_FILE_STRING)
        from raptor.core.units_conv import native_to_display, display_to_native
        native_u = getattr(self, "_native_units", "") or ""
        display_u = getattr(self, "_display_unit", None)
        combo = QComboBox()
        # Only the 4 sparing types a file can carry; ColdPool/Resource
        # strings load as None, so they are not offered.
        current_file_string = SPARING_TYPE_TO_FILE_STRING.get(
            b.sparing_type, "None")
        offered = ["None", "Infinite", "Custom", "Pooled"]
        if current_file_string not in offered:
            # Show an unexpected stored value accurately rather
            # than silently mislabeling the cell as "None".
            offered.append(current_file_string)
        for file_string in offered:
            combo.addItem(file_string, file_string)
        idx = combo.findData(current_file_string)
        combo.setCurrentIndex(max(idx, 0))

        def commit_sparing(file_string, bid=bid, cmb=combo):
            self._apply_rows(
                bid, "Sparing Type", file_string,
                lambda k: req(self._view).set_block_sparing_type(
                    k, _SPARING_TYPE_BY_NAME[file_string].name) or True)
            self.model_edited.emit()
        combo.currentIndexChanged.connect(
            lambda _i, cmb=combo, cb=commit_sparing:
            cb(cmb.currentData()))
        t.setCellWidget(r, 2, combo)

        self._text_cell(
            t, r, 3, b.pool_name,
            lambda text, bid=bid: self._apply_rows(
                bid, "Pool Name", text,
                lambda k: req(self._view).set_block_pool_name(k, text)
                or True))
        self._text_cell(
            t, r, 4, b.resource_pool_name,
            lambda text, bid=bid: self._apply_rows(
                bid, "Resource", text,
                lambda k: req(self._view).set_block_resource_pool(k, text)
                or True))

        def commit_new_spares(bid=bid, row=r):
            cw_c = t.cellWidget(row, 5)
            cw_i = t.cellWidget(row, 6)
            if cw_c is None or cw_i is None:
                return
            c_edit = cw_c.findChild(QLineEdit)
            i_edit = cw_i.findChild(QLineEdit)
            try:
                count = int(req(c_edit).text().strip())
                interval = float(req(i_edit).text().strip())
            except ValueError:
                return
            if display_u:
                interval = display_to_native(
                    interval, native_u, display_u)
            req(self._view).set_block_new_spares(bid, count, interval)
            self.model_edited.emit()

        self._text_cell(t, r, 5, str(int(b.new_spares)),
                        lambda text, cb=commit_new_spares:
                        (cb() or True))
        shown_interval = (native_to_display(
            b.new_spares_arrival, native_u, display_u)
            if display_u else b.new_spares_arrival)
        self._text_cell(t, r, 6, f"{shown_interval:.6g}",
                        lambda text, cb=commit_new_spares:
                        (cb() or True))
        self._tag_row_with_name(t, r, b.name)
        self._label_row_widgets(t, r)

    def _populate_pm(self):
        """Preventive Maintenance tab: "only the
        seven components for which we checked the preventive
        maintenance checkbox are listed on this tab" -- filtered to
        `block.uses_pm`, with the 5 PM option checkboxes as the last
        columns, matching the manual's own description verbatim."""
        t = self._pm_table
        t.setUpdatesEnabled(False)
        t.blockSignals(True)
        t.setRowCount(0)
        if self._view is None:
            t.setUpdatesEnabled(True)
            t.blockSignals(False)
            return
        src = self._active_src()
        bids = [bid for bid in sorted(src.blocks)
                if src.blocks[bid].uses_pm]
        t.setRowCount(len(bids))
        for r, bid in enumerate(bids):
            b = src.blocks[bid]
            t.setItem(r, t.columnCount() - 1,
                      self._level_item("block", bid))
            name_item = _SortItem(b.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 0, name_item)
            id_item = _SortItem(str(bid))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 1, id_item)
            self._param_cell(t, r, 2, b.pm_freq,
                             lambda v, bid=bid:
                             self._set_pm(bid, v), is_time=True)
            for col, (attr, key) in enumerate((
                    ("pm_spare_needed", "spare_needed"),
                    ("pm_refresh", "refresh"),
                    ("pm_mis_defer", "mis_defer"),
                    ("pm_req_defer", "req_defer"),
                    ("pm_fail_reset", "fail_reset")), start=3):
                item = _SortItem()
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                             | Qt.ItemFlag.ItemIsSelectable)
                item.setCheckState(
                    Qt.CheckState.Checked if getattr(b, attr) else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, (bid, key))
                t.setItem(r, col, item)
            self._tag_row_with_name(t, r, b.name)
        t.blockSignals(False)
        t.setUpdatesEnabled(True)

    def _on_pm_cell_changed(self, item: QTableWidgetItem):
        payload = item.data(Qt.ItemDataRole.UserRole)
        if payload is None or self._view is None:
            return
        bid, key = payload
        on = item.checkState() == Qt.CheckState.Checked
        self._apply_rows(
            bid, req(req(item.tableWidget()).horizontalHeaderItem(
                item.column())).text(),
            "on" if on else "off",
            lambda k: self._view.set_block_pm_option(k, key, on) or True)
        self.model_edited.emit()

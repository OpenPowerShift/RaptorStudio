"""Data Tables: pool and trigger tables edited by key.

Expects on `self`: the dock's tabs and tables."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.gui.ui.qtcompat import req


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QTableWidgetItem,
)

from raptor.core.distro_rules import (
    DISTRO_PARAM_LABELS, validate_distro_params,
)
from raptor.core.distro_rules import DISTRO_NAMES


from raptor.gui.ui.data_tables_common import (
    _SortItem,
    _is_time_param,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
else:
    _Base = object


class DataTablesKeyedMixin(_Base):
    """Data Tables: pool and trigger tables edited by key."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        _KEYED_TEMPLATES: Any
        _KEYED_TIME_FIELDS: Any
        @staticmethod
        def _cell_tip(t: Any, row: int, col: int, *, header: bool = ...) -> str: ...
        _keyed: Any
        def _param_cell(self, table: Any, row: Any, col: Any, value: float, on_commit: Any, is_time: bool = ..., *, shown_value: float | None = ..., on_commit_entry: Any = ...) -> Any: ...
        @staticmethod
        def _set_cell_tip(t: Any, row: int, col: int, text: str) -> None: ...
        _tabs: Any
        _view: Any
        model_edited: Any
        def refresh(self, force: bool = ...) -> Any: ...
        validation_failed: Any

    def _populate_keyed(self, table):
        section_name, key_col, kinds = self._keyed[table]
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        table.setRowCount(0)
        time_cols = self._KEYED_TIME_FIELDS.get(section_name, set())
        if self._view is not None and self._view._rbd_ref is not None:
            sec = self._view._rbd_ref.sections.get(section_name)
            rows = sec.rows if sec else []
            table.setRowCount(len(rows))
            for row_idx, r in enumerate(rows):
                has_dist = "Dist" in sec.columns
                try:
                    dist_code = (int(float(r.get("Dist", "0") or 0))
                                if has_dist else None)
                except ValueError:
                    dist_code = 0
                plabels = (DISTRO_PARAM_LABELS.get(dist_code, ("", "", ""))
                          if dist_code is not None else ("", "", ""))

                for ci, col in enumerate(sec.columns):
                    is_dist_param = (
                        col in ("Param1", "Param2", "Param3")
                        and dist_code is not None)
                    if is_dist_param or col in time_cols:
                        # Display-unit override for these keyed tables:
                        # a Trigger/Phase's Param slots are only
                        # time-dimensioned depending on their own row's
                        # current Dist, mirroring the Blocks table's
                        # per-row `_is_time_param` use.
                        is_time = (_is_time_param(plabels[int(col[-1]) - 1])
                                  if is_dist_param else True)
                        try:
                            val = float(r.get(col, 0) or 0)
                        except ValueError:
                            val = 0.0
                        self._param_cell(
                            table, row_idx, ci, val,
                            lambda v, sec_n=section_name, kc=key_col,
                                  key=r.get(key_col, ""), c=col, t=table:  # noqa: B008 -- binds this row's key
                                self._commit_keyed_float(
                                    t, sec_n, kc, key, c, v),
                            is_time=is_time)
                        continue
                    item2: QTableWidgetItem
                    if kinds.get(col) is int and col == "Dist":
                        # Distribution columns show the NAME (manual
                        # screenshots) -- a bare code is unreadable.
                        item2 = _SortItem(
                            DISTRO_NAMES.get(dist_code or 0, f"#{dist_code}"))
                    else:
                        item2 = _SortItem(r.get(col, ""))
                    if col == key_col:
                        item2.setData(Qt.ItemDataRole.UserRole, r.get(key_col, ""))
                        # Spare/Resource pools: a one-sentence plain-
                        # English policy summary as a hover tooltip on
                        # the name cell. Imported locally because
                        # `pool_dialogs` imports this module.
                        if section_name in ("SPARE_POOL_DATA",
                                            "RESOURCE_DATA"):
                            from raptor.gui.ui.pool_dialogs import \
                                plain_english_summary
                            sentence = plain_english_summary(
                                section_name, r,
                                getattr(self, "_native_units", "") or "")
                            if sentence:
                                item2.setToolTip(sentence)
                    table.setItem(row_idx, ci, item2)
                # Hover meaning for the cells that have none yet: a
                # Param's meaning for THIS row's distribution, else the
                # column's own. (Widget cells included.)
                dname = DISTRO_NAMES.get(dist_code or 0, "?")
                for ci, col in enumerate(sec.columns):
                    if self._cell_tip(table, row_idx, ci, header=False):
                        continue            # the pool-name sentence, etc.
                    if col in ("Param1", "Param2", "Param3") \
                            and dist_code is not None:
                        label = plabels[int(col[-1]) - 1]
                        tip = (f"{label} of this row's {dname} distribution"
                               if label else
                               f"Not used by this row's {dname} distribution")
                    else:
                        tip = self._cell_tip(table, row_idx, ci)
                    if tip:
                        self._set_cell_tip(table, row_idx, ci, tip)
        # Readability: generous first column, content-sized rest.
        table.resizeColumnsToContents()
        if table.columnCount() > 0:
            table.setColumnWidth(0, max(140,
                                        table.columnWidth(0) + 24))
        table.blockSignals(False)
        table.setUpdatesEnabled(True)

    def _commit_keyed_float(self, table, section_name: str, key_col: str,
                            key, col: str, value: float) -> bool:
        """`_param_cell`'s `on_commit` for a PHASES/TRIGGERS/SPARE_POOL_
        DATA keyed row's time-dimensioned cell (ArriveEvery/EmerTime/
        SLOTime/InitUsed, or Param1-3) -- same
        validate-then-`set_keyed_cell` path `_on_keyed_cell_changed`
        uses for its own plain-item columns, just entered from a cell
        WIDGET's commit callback instead of `itemChanged` (`_param_cell`
        already emits `model_edited` itself on a truthy return, so this
        doesn't emit again)."""
        if self._view is None or key is None:
            return False
        sec = self._view._rbd_ref.sections.get(section_name)
        if sec is None:
            return False
        row = next((r for r in sec.rows if r.get(key_col) == key), None)
        if row is None:
            return False
        value_str = str(float(value))
        if col in ("Param1", "Param2", "Param3"):
            def cur(c, default="0"):
                return value_str if c == col else row.get(c, default)
            try:
                dist = int(float(cur("Dist")))
                params = [float(cur(f"Param{i}")) for i in (1, 2, 3)]
            except (ValueError, TypeError):
                dist, params = None, None
            if dist is not None and params is not None:
                label = "Phase" if section_name == "PHASES" else "Trigger"
                err = validate_distro_params(dist, params, label)
                if err:
                    self.validation_failed.emit(f"{key}: {err}")
                    return False
        self._view.set_keyed_cell(section_name, key_col, key, col, value_str)
        if section_name in ("SPARE_POOL_DATA", "RESOURCE_DATA"):
            # Keep the key-cell's plain-English tooltip fresh (it is
            # computed from the whole row at populate time). These two
            # sections have no Dist column, so the Dist-triggered
            # repopulate in `_on_keyed_cell_changed` never covers them.
            # Re-entrant but safe: QTableWidget defers cell-widget
            # deletion via `deleteLater`, so rebuilding the table from
            # inside this cell widget's own commit is safe.
            self._populate_keyed_guarded(table)
        return True

    def _populate_keyed_guarded(self, table):
        """Re-populate ONE keyed table mid-session (Dist change, keyed
        float commit): sorting must be off while rows are inserted, then
        restored to the user's choice."""
        header = table.horizontalHeader()
        order = (header.sortIndicatorSection(),
                 header.sortIndicatorOrder())
        table.setSortingEnabled(False)
        self._populate_keyed(table)
        if order[0] >= 0:
            table.setSortingEnabled(True)
            table.sortItems(order[0], order[1])

    def _on_keyed_cell_changed(self, item: QTableWidgetItem):
        table = item.tableWidget()
        section_name, key_col, kinds = self._keyed[table]
        old_key = item.data(Qt.ItemDataRole.UserRole) if item.column() == 0 else None
        row_idx = item.row()
        # The row's live key: from column-0's data role (stable), else
        # read the name cell.
        key = (req(table.item(row_idx, 0)).data(Qt.ItemDataRole.UserRole)
               if table.item(row_idx, 0) is not None else None)
        if self._view is None or key is None:
            return
        sec = self._view._rbd_ref.sections.get(section_name)
        if sec is None:
            return
        col = sec.columns[item.column()]
        kind = kinds.get(col, str)
        raw = item.text().strip()
        if kind is float:
            try:
                value = str(float(raw))
            except ValueError:
                self._reload_keyed_row(table, row_idx, sec, key_col, key)
                return
        elif kind is int:
            if col == "Dist":
                # Distribution cells DISPLAY the name -- accept a name
                # (or a raw code) and store the code.
                name = raw.lower()
                code = next((c for c, n in DISTRO_NAMES.items()
                             if n.lower() == name), None)
                if code is None:
                    try:
                        code = int(float(raw))
                    except ValueError:
                        code = None
                if code is None:
                    self._reload_keyed_row(table, row_idx, sec,
                                           key_col, key)
                    return
                from raptor.core.distro_rules import RAPTOR7_LISTS
                if code not in RAPTOR7_LISTS["delay"]:
                    # Raptor 7's phase and trigger distribution dialog
                    # (`DistBox`) does not list it, so there is no Raptor 7
                    # alert to quote: it cannot be chosen at all. Empirical
                    # in particular could not be kept, as a phase or trigger
                    # row has three parameter cells and no point list.
                    self._reload_keyed_row(table, row_idx, sec, key_col, key)
                    self.validation_failed.emit(
                        f"{key}: {DISTRO_NAMES.get(code, f'#{code}')} is not "
                        "available for a phase or trigger distribution.")
                    return
                value = str(code)
            else:
                try:
                    value = str(int(float(raw)))
                except ValueError:
                    self._reload_keyed_row(table, row_idx, sec,
                                           key_col, key)
                    return
        else:
            value = "_".join(raw.split()) or key
            if value != item.text():
                item.setText(value)
            if col == key_col and value != key and any(
                    r.get(key_col) == value for r in sec.rows if r.get(key_col) != key):
                # Pools, triggers and phases are referred to by name, so two with one name cannot both be used.
                self._reload_keyed_row(table, row_idx, sec, key_col, key)
                self.validation_failed.emit(
                    f"'{value}' is already used by another entry in this table; names must be unique.")
                return
        target_key = old_key if (item.column() == 0 and old_key) else key

        # Distribution-parameter validation: only the PHASES/TRIGGERS
        # keyed tables have a Dist/Param1-3 quadruple (Spare Pools and
        # Resources don't, so `col in (...)` scopes this to those two).
        # Validate the full row -- the candidate value for the committed
        # cell, the file's current values for the other three -- so
        # editing Param3 to 0.0 on a saved Pearson6 row is caught even
        # though Param3 alone looks like "just a float".
        seeded = None
        if col in ("Dist", "Param1", "Param2", "Param3"):
            row = next((r for r in sec.rows if r.get(key_col) == key), None)
            if row is not None:
                def cur(c, default="0"):
                    return value if c == col else row.get(c, default)
                try:
                    dist = int(float(cur("Dist")))
                    params = [float(cur(f"Param{i}")) for i in (1, 2, 3)]
                except (ValueError, TypeError):
                    dist, params = None, None
                if col == "Dist" and dist is not None and params is not None:
                    # A new distribution gets parameters that suit it, with
                    # the old mean: checked against the OLD numbers, Fixed
                    # 0.25 -> Binomial was refused (N Trials 0.25).
                    from raptor.core.distro_rules import params_after_change
                    try:
                        old_dist = int(float(row.get("Dist", "19")))
                    except (TypeError, ValueError):
                        old_dist = dist
                    seeded = params_after_change(
                        old_dist, params, dist,
                        "Phase" if section_name == "PHASES" else "Trigger")
                    if seeded is not None:
                        params = list(seeded)
                if dist is not None and params is not None:
                    label = "Phase" if section_name == "PHASES" else "Trigger"
                    err = validate_distro_params(dist, params, label)
                    if err:
                        self._reload_keyed_row(table, row_idx, sec,
                                               key_col, key)
                        self.validation_failed.emit(f"{key}: {err}")
                        return

        stack = self._view._stack
        stack.beginMacro(f"{key}: {col}")
        try:
            self._view.set_keyed_cell(section_name, key_col, target_key,
                                      col, value)
            if seeded is not None and row is not None:
                for i, pv in enumerate(seeded, start=1):
                    if row.get(f"Param{i}") != f"{pv:.6f}":
                        self._view.set_keyed_cell(
                            section_name, key_col, target_key,
                            f"Param{i}", f"{pv:.6f}")
        finally:
            stack.endMacro()
        if item.column() == 0:
            item.setData(Qt.ItemDataRole.UserRole, value)
        self.model_edited.emit()
        if col == "Dist":
            # Re-populate so the Dist cell shows the resolved NAME
            # (not whatever the user typed, e.g. a bare code) and so
            # Param1-3 follow the new distribution immediately, not
            # just on the next unrelated refresh.
            self._populate_keyed_guarded(table)
        elif section_name in ("SPARE_POOL_DATA", "RESOURCE_DATA"):
            # Keep the key-cell's plain-English tooltip fresh -- see
            # `_commit_keyed_float`'s identical re-populate for these
            # two sections' time-dimensioned (cell-widget) columns.
            self._populate_keyed_guarded(table)

    def _reload_keyed_row(self, table, row_idx, sec, key_col, key):
        """Revert an invalid edit by re-reading the file row."""
        table.blockSignals(True)
        for r in sec.rows:
            if r.get(key_col) == key:
                for ci, col in enumerate(sec.columns):
                    it = table.item(row_idx, ci)
                    if it is not None and it.text() != r.get(col, ""):
                        it.setText(r.get(col, ""))
                break
        table.blockSignals(False)

    def _current_keyed_table(self):
        w = self._tabs.currentWidget()
        return w if w in self._keyed else None

    def _add_keyed_row(self):
        table = self._current_keyed_table()
        if table is None or self._view is None:
            return
        section_name, key_col, _kinds = self._keyed[table]
        base = dict(self._KEYED_TEMPLATES[section_name])
        new_key = self._view.add_keyed_row(section_name, key_col, base)
        self.model_edited.emit()
        self.refresh()
        # Select the fresh row for immediate editing.
        for r in range(table.rowCount()):
            it = table.item(r, 0)
            if it is not None and it.text() == new_key:
                table.setCurrentCell(r, 0)
                return

    def _remove_selected_keyed_row(self):
        table = self._current_keyed_table()
        if table is None or self._view is None:
            return
        row_idx = table.currentRow()
        if row_idx < 0:
            return
        key_item = table.item(row_idx, 0)
        if key_item is None:
            return
        key = key_item.data(Qt.ItemDataRole.UserRole) or key_item.text()
        section_name, key_col, _kinds = self._keyed[table]
        self._view.remove_keyed_row(section_name, key_col, key)
        self.model_edited.emit()
        self.refresh()

"""The table of every item behind a ranked chart, with Export CSV.

A ranking chart draws its largest few items and gathers the rest into an
"N others" cell; people worry about the 11th item, so this lists them all,
unfolded, with the chart's own value, share and cumulative share.  The rows
come from `raptor.api.insight.ranking.ranked_table`, so the MCP server and
the CLI can write the same file.
"""
from __future__ import annotations

import csv
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from raptor.api.insight import ranking
from raptor.api.insight.axes import currency, format_value
from raptor.gui import i18n


class _SortItem(QTableWidgetItem):
    """Sorts by the stored number, not the formatted text."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(Qt.ItemDataRole.UserRole)
        right = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) < float(right)
        if isinstance(left, (int, float)) != isinstance(right, (int, float)):
            return isinstance(left, (int, float))     # a blank sorts last up
        return super().__lt__(other)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def cell_text(column: ranking.Column, value: Any, fmt: str) -> str:
    """One cell as the chart words it."""
    if value is None or value == "":
        return ""
    if not _number(value):
        return str(value)
    if column.kind == "int":
        return f"{int(value):,}"
    if column.kind == "percent":
        return format_value(float(value), "percent", 3)
    if column.kind == "value":
        return format_value(float(value), fmt, 5)
    return f"{float(value):,.4g}"


class RankedItemsDialog(QDialog):
    """Every item behind one ranked chart: sortable, with Export CSV."""

    def __init__(self, table: ranking.RankedTable,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self.ranked = table
        title = i18n.tr(table.title)
        self.setWindowTitle(i18n.tr("All items: %1").replace("%1", title))
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        count = len(table.rows)
        hidden = count - table.shown if table.shown else 0
        if table.shown and hidden > 0:
            text = i18n.tr("%1 items in “%2”; the chart draws %3. "
                           "Click a heading to sort.").replace(
                "%3", f"{table.shown:,}")
        else:
            text = i18n.tr("%1 items in “%2”. Click a heading to sort.")
        text = text.replace("%1", f"{count:,}").replace("%2", title)
        if table.note:
            text += " " + i18n.tr(table.note[:1].upper() + table.note[1:] + ".")
        self.summary = QLabel(text)
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.table_widget = QTableWidget(count, len(table.columns))
        self.table_widget.setAccessibleName("All items")
        self.table_widget.setHorizontalHeaderLabels(
            [i18n.tr(c.title) for c in table.columns])
        self.table_widget.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_widget.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_widget.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table_widget.horizontalHeader().setStretchLastSection(True)
        self.table_widget.verticalHeader().setVisible(False)
        with currency(table.currency):
            for r, row in enumerate(table.rows):
                for c, column in enumerate(table.columns):
                    value = row.get(column.key)
                    item = _SortItem(cell_text(column, value, table.format))
                    if isinstance(value, (int, float)):
                        item.setData(Qt.ItemDataRole.UserRole, float(value))
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                              | Qt.AlignmentFlag.AlignVCenter)
                    self.table_widget.setItem(r, c, item)
        # sorting goes on after the fill: each setItem would re-sort a
        # sorted table and scatter the row's cells (#351)
        self.table_widget.setSortingEnabled(True)
        self.table_widget.sortItems(0, Qt.SortOrder.AscendingOrder)
        lay.addWidget(self.table_widget, 1)
        foot = QHBoxLayout()
        self.export_button = QPushButton("Export CSV…")
        self.export_button.setToolTip(
            "Save every item, with its value, share and cumulative share, "
            "as a CSV file. Shares are fractions (1 = 100 %).")
        self.export_button.clicked.connect(self.export_csv)
        foot.addWidget(self.export_button)
        foot.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        foot.addWidget(buttons)
        lay.addLayout(foot)

    def write_csv(self, path: str) -> None:
        write_ranked_csv(self.ranked, path)

    def export_csv(self) -> None:
        from raptor.gui.ui.chart_widget import ask_chart_path
        if path := ask_chart_path(self, "Export all items", "all-items",
                                  "CSV", ".csv"):
            self.write_csv(path)


def write_ranked_csv(table: ranking.RankedTable, path: str) -> None:
    """Every item as a CSV: the header names the columns, shares are
    fractions, and the rows are in rank order."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(table.header())
        writer.writerows(table.grid())


def show_ranked_items(spec: Any, parent: Any = None) -> bool:
    """Open the all-items table for `spec`; False where it is no ranking."""
    table = ranking.ranked_table(spec)
    if table is None:
        return False
    dialog = RankedItemsDialog(table, parent)
    i18n.exec_translated(dialog)
    return True

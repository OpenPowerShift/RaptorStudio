"""Results ▸ Availability & Criticality: additive run metrics in the GUI.

The legacy Final Results text is left byte-identical to the captured
reference output. These tables read the same per-run objects and present the
new fields: MTTFF, availability classifications, Red-time cause buckets, and
per-block failure/downtime/cost criticality.
"""
from __future__ import annotations

from statistics import fmean

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QMessageBox, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


def _mean(values):
    finite = [v for v in values
              if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return fmean(finite) if finite else None


def _fmt(value):
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.9g}"
    return str(value)


class DerivedResultsDialog(QDialog):
    def __init__(self, win, report_data) -> None:
        super().__init__(win)
        self.setWindowTitle("Availability & Criticality")
        self.resize(820, 560)
        self._data = list(report_data)
        lay = QVBoxLayout(self)
        intro = QLabel(
            f"Additive run metrics over {len(self._data)} run(s): "
            "availability classifications, MTTFF, Red-time causes and "
            "criticality ratios. Report text is unchanged.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        tabs = QTabWidget()
        tabs.addTab(self._summary_tab(), "Availability")
        tabs.addTab(self._criticality_tab(), "Criticality")
        lay.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setAutoDefault(False)
            close_btn.setDefault(False)
        buttons.rejected.connect(self.close)
        lay.addWidget(buttons)

    def _summary_tab(self):
        table = QTableWidget(0, 2)
        table.setAccessibleName("Availability classifications")
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setHorizontalHeaderLabels(["Measure", "Mean over runs"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        rows = [
            ("Operational availability", "operational_availability"),
            ("Inherent availability", "inherent_availability"),
            ("Achieved availability", "achieved_availability"),
            ("MTTFF", "mttff"),
            ("MTBF (total time)", "mtbf_total"),
            ("MTBE (total time)", "mtbe_total"),
            ("Component CMs", "num_component_cms"),
            ("Component PMs", "num_component_pms"),
            ("Corrective Red time", "red_corrective_time"),
            ("PM Red time", "red_pm_time"),
            ("Logistics Red time", "red_logistics_time"),
            ("Done Red time", "red_done_time"),
            ("Other Red time", "red_other_time"),
        ]
        for label, attr in rows:
            value = _mean([getattr(rd, attr, None) for rd in self._data])
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(label))
            value_item = QTableWidgetItem(_fmt(value))
            value_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row, 1, value_item)
        return table

    def _criticality_tab(self):
        table = QTableWidget(0, 4)
        table.setAccessibleName("Per-block criticality ratios")
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setHorizontalHeaderLabels(
            ["Element", "Failure share (%)", "Downtime share (%)",
             "Cost share (%)"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        rows: dict[int, dict[str, list[float | None]]] = {}
        names: dict[int, str] = {}
        for rd in self._data:
            for bid, values in (rd.criticality or {}).items():
                names[bid] = values.get("name", str(bid))
                entry = rows.setdefault(bid, {
                    "failure": [], "downtime": [], "cost": [],
                    "downing_event": []})
                for field in entry:
                    entry[field].append(values.get(field))
        def sort_key(bid):
            value = _mean(rows[bid]["failure"])
            return (value is None, -(value or 0.0), names[bid].lower())

        if not rows:
            table.setRowCount(1)
            table.setSpan(0, 0, 1, 4)
            table.setItem(0, 0, QTableWidgetItem(
                "No criticality data: run with Weak Link analysis for "
                "downtime and Cost analysis for cost shares."))
            return table
        for bid in sorted(rows, key=sort_key):
            entry = rows[bid]
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(names[bid]))
            for column, field in enumerate(
                    ("failure", "downtime", "cost"), 1):
                value = _mean(entry[field])
                text = "N/A" if value is None else f"{100.0 * value:.2f}"
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight
                    | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row, column, item)
        return table


def open_derived_dialog(win):
    data = getattr(win._results, "_report_data", None)
    if not data:
        QMessageBox.information(
            win, "Availability & Criticality",
            "There are no simulation results yet. Run the model first.")
        return None
    dialog = DerivedResultsDialog(win, data)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog

"""Analyse ▸ What If: compare scenarios with the model, paired (plan 8.2.3).

Each scenario is a name and a list of PATH=VALUE changes. Presets build one
for a block (half the repair time, one more spare, no PM, ...: the levers
Recommend proposes); any change `raptor set` accepts can be typed. Run runs
Base and every scenario with the same seed (`raptor.api.scenarios.compare`)
on a copy and lists each one's availability, the paired change against Base
with its standard error, the chance it is better, and the change in mean
downtime. Apply writes the selected scenario into the model as one undo step.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLayout,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from raptor.gui import cores
from raptor.gui.ui.analysis_dialog import AnalysisDialog, Work, trials_box

RESULT_COLUMNS = ("Scenario", "Availability", "Change vs Base", "Chance better",
                  "Mean downtime change")


def _pts(value: float | None, sem: float | None = None) -> str:
    if value is None:
        return "—"
    text = f"{100 * value:+.3g} pts"
    return text + (f" ± {100 * sem:.2g}" if sem else "")


class WhatIfDialog(AnalysisDialog):
    TITLE = "What If"
    INTRO = ("Compare changes with this model. Each scenario is a list of "
             "changes (PATH=VALUE, as `raptor set` takes them); Base and every "
             "scenario run with the same seed, so the difference is measured "
             "pair by pair. Runs on a copy: the model changes only when you "
             "press Apply.")
    FIRST_LABEL = "Running Base"
    SIZE = (1040, 620)

    # ---- inputs: the scenario list --------------------------------------------

    def build_inputs(self) -> QLayout:
        win = self._win
        self._block = QComboBox()
        self._block.setMinimumWidth(180)
        self._block.setToolTip("The block the preset changes")
        self._preset = QComboBox()
        self._preset.setMinimumWidth(220)
        self._block.currentTextChanged.connect(self._fill_presets)
        add_preset = QPushButton("Add scenario")
        add_preset.setToolTip("Add a scenario with the preset change for this block")
        add_preset.clicked.connect(self._add_preset)
        add_blank = QPushButton("Add blank")
        add_blank.setToolTip("Add an empty scenario to type changes into")
        add_blank.clicked.connect(lambda: self.add_scenario("Scenario", []))
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        presets = QWidget()
        row = QHBoxLayout(presets)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Block"))
        row.addWidget(self._block)
        row.addWidget(QLabel("Change"))
        row.addWidget(self._preset)
        row.addWidget(add_preset)
        row.addSpacing(12)
        row.addWidget(add_blank)
        row.addWidget(remove)
        row.addStretch(1)

        self.scenarios = QTableWidget(0, 2)
        self.scenarios.setHorizontalHeaderLabels(("Scenario", "Changes (PATH=VALUE; separate with ;)"))
        self.scenarios.verticalHeader().setVisible(False)
        self.scenarios.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.scenarios.setColumnWidth(0, 240)
        self.scenarios.setMinimumHeight(110)
        self.scenarios.setMaximumHeight(170)

        self._trials = trials_box(win, "Trials for Base and for every scenario (paired)")
        settings = QWidget()
        srow = QHBoxLayout(settings)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.addWidget(QLabel("Trials"))
        srow.addWidget(self._trials)
        srow.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(presets)
        layout.addWidget(self.scenarios)
        layout.addWidget(settings)
        self._fill_blocks()
        return layout

    def _fill_blocks(self) -> None:
        rbd = self._win._rbd
        names = [str(r.get("blockName") or r.get("ID"))
                 for r in (rbd.section("BLOCK_DATA").rows if rbd is not None else [])
                 if str(r.get("ID", "0")) != "0"]
        self._block.addItems(names)
        chosen = self._selected_block()
        if chosen in names:
            self._block.setCurrentText(chosen)
        self._fill_presets(self._block.currentText())

    def _selected_block(self) -> str | None:
        view = self._win._diagram_dock.view
        rbd = self._win._rbd
        for bid, item in getattr(view, "_block_items", {}).items():
            if item.isSelected() and rbd is not None:
                for row in rbd.section("BLOCK_DATA").rows:
                    if str(row.get("ID")) == str(bid):
                        return str(row.get("blockName") or bid)
        return None

    def _fill_presets(self, block: str) -> None:
        from raptor.api.scenarios.recommend import presets
        self._preset.clear()
        if not block or self._win._rbd is None:
            return
        try:
            options = presets(self._win._rbd, block)
        except ValueError:
            options = []
        for label, changes in options:
            self._preset.addItem(label, list(changes))
        self._preset.setEnabled(bool(options))
        if not options:
            self._preset.addItem("no ready-made change for this block")

    def _add_preset(self) -> None:
        changes = self._preset.currentData()
        if changes:
            self.add_scenario(f"{self._block.currentText()}: {self._preset.currentText()}",
                              list(changes))

    def add_scenario(self, name: str, changes: list[str]) -> None:
        row = self.scenarios.rowCount()
        self.scenarios.insertRow(row)
        self.scenarios.setItem(row, 0, QTableWidgetItem(name))
        self.scenarios.setItem(row, 1, QTableWidgetItem("; ".join(changes)))
        self.scenarios.setCurrentCell(row, 1)

    def _remove(self) -> None:
        row = self.scenarios.currentRow()
        if row >= 0:
            self.scenarios.removeRow(row)

    def scenario_list(self) -> list[dict[str, Any]]:
        out = []
        for row in range(self.scenarios.rowCount()):
            name_item, changes_item = self.scenarios.item(row, 0), self.scenarios.item(row, 1)
            name = (name_item.text() if name_item else "").strip() or f"Scenario {row + 1}"
            text = changes_item.text() if changes_item else ""
            changes = [c.strip() for c in text.replace("\n", ";").split(";") if c.strip()]
            out.append({"name": name, "changes": changes})
        return out

    # ---- results -----------------------------------------------------------------

    def build_result_area(self) -> QWidget:
        self.table = QTableWidget(0, len(RESULT_COLUMNS))
        self.table.setHorizontalHeaderLabels(RESULT_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(RESULT_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.sync_buttons)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        return splitter

    def make_work(self) -> Work | str:
        scenarios = self.scenario_list()
        if not scenarios:
            return "Add a scenario first: pick a block and a change, or Add blank and type one."
        if any(not s["changes"] for s in scenarios):
            empty = next(s["name"] for s in scenarios if not s["changes"])
            return f"{empty} has no change: type PATH=VALUE, or remove it."
        trials, workers = self._trials.value(), cores.chosen()

        def work(ws, mid, progress, abort):
            from raptor.api.scenarios import compare
            return compare(ws, mid, scenarios, trials=trials, workers=workers, seed=1,
                           timeout_s=24 * 3600.0, progress=progress, abort=abort)
        return work

    def show_result(self, result: Any) -> None:
        base = (result.base.get("metrics") or {}).get("availability") or {}
        rows: list[tuple[Any, ...]] = [("Base", base.get("mean"), None, None, None, None)]
        for row in result.rows:
            metric = (row.get("metrics") or {}).get("availability") or {}
            mdt = (row.get("metrics") or {}).get("mdt") or {}
            delta = metric.get("delta") or {}
            rows.append((row["name"], (metric.get("value") or {}).get("mean"),
                         delta, metric.get("p_better"), (mdt.get("delta") or {}).get("mean"),
                         row.get("error")))
        units = (self._win._current_units() or "") if hasattr(self._win, "_current_units") else ""
        header = self.table.horizontalHeaderItem(len(RESULT_COLUMNS) - 1)
        if header is not None:
            header.setText(f"{RESULT_COLUMNS[-1]} ({units})" if units else RESULT_COLUMNS[-1])
        self.table.setRowCount(len(rows))
        for index, (name, value, delta, chance, mdt, error) in enumerate(rows):
            texts = (name,
                     "—" if value is None else f"{100 * value:.5g} %",
                     ("did not run: " + error) if error else
                     ("—" if not delta else _pts(delta.get("mean"), delta.get("sem"))),
                     "—" if chance is None else f"{100 * chance:.0f} %",
                     "—" if mdt is None else f"{mdt:+,.4g}")
            for column, text in enumerate(texts):
                item = QTableWidgetItem(text)
                if index:
                    item.setToolTip("; ".join(result.rows[index - 1]["changes"]))
                self.table.setItem(index, column, item)
        self.caption.setText(f"{result.caption}\n{result.note}")
        if len(rows) > 1:
            self.table.selectRow(1)

    def selection(self) -> tuple[Any, str, list[str]] | None:
        result = self.result
        index = self.table.currentRow() - 1          # row 0 is Base
        if result is None or not 0 <= index < len(result.rows):
            return None
        row = result.rows[index]
        if row.get("error"):
            return None
        return index, row["name"], list(row["changes"])

    def result_text(self) -> str:
        from raptor.api.scenarios.runner import comparison_lines
        return "\n".join(comparison_lines(self.result)) if self.result is not None else ""


def open_whatif(win: Any) -> WhatIfDialog:
    from raptor.gui.ui.analysis_dialog import open_analysis
    dlg = open_analysis(win, WhatIfDialog, "_whatif_dialog")
    assert isinstance(dlg, WhatIfDialog)
    return dlg

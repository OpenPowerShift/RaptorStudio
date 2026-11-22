"""Review and apply a spreadsheet data-table import.

The plan is computed and validated by `raptor.core.import_tables`; this
module only presents it and wraps the whole apply in one QUndoCommand.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from raptor.gui import i18n
from raptor.core import import_tables as I


class ImportTableCommand(QUndoCommand):
    """One History step for the whole imported table."""

    def __init__(self, view: Any, rbd: Any,
                 before: dict[int, dict], after: dict[int, dict],
                 text: str = "Import data tables"):
        super().__init__(text)
        self._view = view
        self._rbd = rbd
        self._before = copy.deepcopy(before)
        self._after = copy.deepcopy(after)

    def _apply(self, rows: dict[int, dict]) -> None:
        I.write_rows(self._rbd, rows)
        refresh = getattr(self._view, "refresh_from_model", None)
        if callable(refresh):
            refresh()
        else:
            emitter = getattr(self._view, "model_changed", None)
            if emitter is not None:
                emitter.emit()

    def redo(self) -> None:
        self._apply(self._after)

    def undo(self) -> None:
        self._apply(self._before)


class ImportTableDialog(QDialog):
    """Show every proposed change, its formula and any blocking problem."""

    def __init__(self, parent: Any, plan: I.ImportPlan):
        super().__init__(parent)
        self._plan = plan
        self.setWindowTitle(
            f"Import Data Table — {Path(plan.source).name}")
        self.setMinimumSize(900, 560)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        intro = QLabel(
            f"<b>{Path(self._plan.source).name}</b> — sheet "
            f"<b>{self._plan.sheet}</b><br>{self._plan.summary()}. "
            "Nothing is written until Apply; the whole import is one "
            "undo step.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        if self._plan.changes:
            self._build_changes(lay, self._plan.changes)
        else:
            empty = QLabel("No field in this sheet would change a block.")
            empty.setWordWrap(True)
            lay.addWidget(empty, 1)

        problems = sorted(self._plan.problems,
                          key=lambda p: (p.level != "error", p.row))
        if problems:
            box = QPlainTextEdit()
            box.setReadOnly(True)
            box.setMaximumHeight(150)
            lines = []
            for problem in problems:
                marker = "ERROR" if problem.level == "error" else "warning"
                lines.append(f"{marker}: {problem.text()}")
            box.setPlainText("\n".join(lines))
            box.setStyleSheet(
                "QPlainTextEdit { color: #b91c1c; }"
                if self._plan.has_errors() else "")
            lay.addWidget(box)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Apply import")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        apply_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        apply_btn.setEnabled(bool(self._plan.changes)
                             and not self._plan.has_errors())
        lay.addWidget(self._buttons)

    def _build_changes(self, lay: QVBoxLayout,
                       changes: list[I.Change]) -> None:
        self._table = QTableWidget(len(changes), 6)
        self._table.setHorizontalHeaderLabels(i18n.header_labels(
            ["Row", "Element", "Field", "Current", "Imported", "Formula"]))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        for row, change in enumerate(changes):
            values = [str(change.row) if change.row else "",
                      f"{change.name} ({change.id})",
                      _field_label(change.field),
                      change.old_text(), change.new_text(), change.formula]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col in (3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                if col == 5 and text:
                    item.setToolTip("The spreadsheet formula behind the "
                                    "imported value")
                self._table.setItem(row, col, item)
        header = self._table.horizontalHeader()
        for col in range(5):
            header.setSectionResizeMode(col,
                                        QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._table, 1)


def _field_label(field: str) -> str:
    return {
        "fail_dist": "Failure distribution",
        "fail_params": "Failure parameters",
        "repair_dist": "Repair distribution",
        "repair_params": "Repair parameters",
        "pm_freq": "PM frequency",
        "uses_pm": "Uses PM",
        "stock": "Initial stock",
    }.get(field, field.replace("_", " ").capitalize())

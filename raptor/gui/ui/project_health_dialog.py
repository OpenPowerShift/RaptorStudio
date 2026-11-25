"""Project Health and the share-safe export options."""
from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)

from raptor.api.health import HealthReport
from raptor.gui.ui import filedialogs
from raptor.core.redact import RedactOptions, write_share_copy


class ProjectHealthDialog(QDialog):
    """Counts, validation findings and project-level housekeeping issues."""

    def __init__(self, parent: Any, report: HealthReport,
                 on_locate: Callable[[str], bool] | None = None,
                 on_share: Callable[[], None] | None = None):
        super().__init__(parent)
        self._report = report
        self._on_locate = on_locate
        self._on_share = on_share
        self.setWindowTitle("Project Health")
        self.setMinimumSize(760, 520)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        head = QLabel(f"<b>{self._report.summary()}</b>")
        head.setWordWrap(True)
        lay.addWidget(head)
        counts = QLabel(
            f"{self._report.annotations} annotation(s) "
            f"({self._report.text_annotations} text) · "
            f"{self._report.comments} comment(s) · "
            f"{self._report.formulas} formula(s) · "
            f"{self._report.rates} rate(s)")
        counts.setObjectName("SectionNote")
        counts.setWordWrap(True)
        lay.addWidget(counts)
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setWordWrap(True)
        self._list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._list.setSpacing(2)
        self._list.itemDoubleClicked.connect(
            lambda _item: self._locate())
        for issue in self._report.issues:
            item = QListWidgetItem(f"{issue.severity.upper():<7} {issue.text()}")
            item.setData(Qt.ItemDataRole.UserRole, issue.element)
            self._list.addItem(item)
        lay.addWidget(self._list, 1)
        self._empty = QLabel(
            "No issues found. The model is ready to run and share; check the "
            "counts above before sending it outside the project.")
        self._empty.setObjectName("SectionNote")
        self._empty.setWordWrap(True)
        self._empty.setVisible(not self._report.issues)
        lay.addWidget(self._empty)
        self._hint = QLabel("")
        self._hint.setObjectName("SectionNote")
        self._hint.setWordWrap(True)
        self._hint.setVisible(False)
        lay.addWidget(self._hint)
        note = QLabel(
            "Double-click an issue to locate its element. Validation errors "
            "must be fixed before a run; warnings are judgements for the "
            "reviewer.")
        note.setObjectName("SectionNote")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        self._share = QPushButton("Share-safe copy…")
        self._share.setEnabled(self._on_share is not None)
        self._share.setToolTip(
            "Write a copy with names, comments and chosen annotations removed")
        self._share.clicked.connect(self._share_clicked)
        row.addWidget(self._share)
        row.addStretch(1)
        self._locate_btn = QPushButton("Locate")
        self._locate_btn.clicked.connect(self._locate)
        row.addWidget(self._locate_btn)
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._list.currentRowChanged.connect(self._sync_buttons)
        self._sync_buttons()

    def _selected_element(self) -> str:
        item = self._list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _sync_buttons(self, *_args) -> None:
        self._locate_btn.setEnabled(bool(self._selected_element())
                                    and self._on_locate is not None)

    def _locate(self) -> None:
        element = self._selected_element()
        if not element or self._on_locate is None:
            return
        if self._on_locate(element):
            self.accept()
            return
        # The dialog used to close whenever a callback ran, even when the
        # element was no longer on a level the view could reach. Stay open
        # and say so instead.
        self._hint.setText(
            f"Could not locate {element} in the diagram; it may have been "
            "deleted after the report was made.")
        self._hint.setVisible(True)

    def _share_clicked(self) -> None:
        if self._on_share is not None:
            self._on_share()


class ShareSafeDialog(QDialog):
    """Choose what the share-safe copy removes."""

    def __init__(self, parent: Any, default_path: str = "",
                 source: str = ""):
        super().__init__(parent)
        self._default_path = default_path
        self._source = source
        self.path: str | None = None
        self.report: Any = None   # a RedactReport once written
        self.setWindowTitle("Share-safe Copy")
        self.setMinimumWidth(560)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        intro = QLabel(
            "A share-safe copy keeps the model's IDs, links, distributions "
            "and results inputs, but removes the labels and notes that "
            "describe what the system is. Your own file is not changed.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self._names = QCheckBox("Replace element and hierarchy names")
        self._names.setChecked(True)
        self._comments = QCheckBox("Remove element comments")
        self._comments.setChecked(True)
        self._annotations = QComboBox()
        self._annotations.addItem("Remove all annotations", "all")
        self._annotations.addItem("Remove text annotations only", "text")
        self._annotations.addItem("Keep annotations", "keep")
        self._metadata = QCheckBox("Clear project metadata and preferences")
        self._metadata.setChecked(True)
        self._advisor = QCheckBox("Clear Advisor notes")
        self._advisor.setChecked(True)
        self._rates = QCheckBox("Remove rate names, descriptions and formulas")
        self._rates.setChecked(True)
        form.addRow("", self._names)
        form.addRow("", self._comments)
        form.addRow("Annotations:", self._annotations)
        form.addRow("", self._metadata)
        form.addRow("", self._advisor)
        form.addRow("", self._rates)
        lay.addLayout(form)
        self._risk = QLabel(
            "<b>Residual risk:</b> the copy still contains the structure, "
            "element IDs, links, positions, costs and all numerical data. "
            "Those can identify a system even without names. Keep names, "
            "comments, annotations, rate descriptions, project metadata or "
            "Advisor notes only when you mean to share that prose. Remove "
            "confidential costs from the source before sharing.")
        self._risk.setObjectName("SectionNote")
        self._risk.setWordWrap(True)
        lay.addWidget(self._risk)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("Choose file…")
        box.accepted.connect(self._choose)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def options(self) -> RedactOptions:
        return RedactOptions(
            names=self._names.isChecked(),
            comments=self._comments.isChecked(),
            annotations=str(self._annotations.currentData()),
            metadata=self._metadata.isChecked(),
            advisor=self._advisor.isChecked(),
            rates=self._rates.isChecked())

    def _choose(self) -> None:
        start = self._default_path or "shared-model.rbds"
        path, _filter = filedialogs.getSaveFileName(
            self, "Share-safe Copy", start,
            "RAPTOR Studio model (*.rbds)")
        if not path:
            return
        if not path.lower().endswith(".rbds"):
            path += ".rbds"
        self.path = path
        self.accept()

    def write(self, rbd: Any) -> Path:
        if not self.path:
            raise ValueError("no destination chosen")
        dest, report = write_share_copy(
            rbd, self.path, self.options(), source=self._source or None)
        self.report = report
        return dest

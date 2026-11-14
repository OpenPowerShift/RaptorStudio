"""The measure explainer: what a number means, on the clock it uses.

Opens beside the Results tabs when the user clicks a metric card or a row
label.  Every sentence comes from `raptor.api.insight.measures`; the panel
only chooses what to show and draws the availability ladder when it has
one.  Non-modal: it never blocks the Results dock.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from raptor.api.insight import measures
from raptor.gui.resources.design_tokens import SPACING, active_colour


class LadderBar(QWidget):
    """The three availabilities as rungs, with the gap the next cause takes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ladder: dict[str, Any] = {}
        self.setMinimumHeight(96)

    def set_ladder(self, ladder: dict[str, Any] | None) -> None:
        self._ladder = ladder or {}
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt name
        palette = active_colour()
        rows = (("inherent", "Inherent (repair only)", "#0ea5e9"),
                ("achieved", "Achieved (+ planned maintenance)", "#22c55e"),
                ("operational", "Operational (+ logistics, spares, crews)",
                 "#f59e0b"))
        painter = self._painter()
        width = max(1, self.width() - 8)
        top = 4
        for key, label, colour in rows:
            entry = self._ladder.get(key) or {}
            value = entry.get("value")
            painter.setPen(QColor(palette.get("text_muted", "#667")))
            painter.drawText(2, top + 10, label)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.get("panel_alt", "#eeeeee")))
            painter.drawRoundedRect(2, top + 14, width, 10, 3, 3)
            if isinstance(value, (int, float)):
                painter.setBrush(QColor(colour))
                painter.drawRoundedRect(
                    2, top + 14,
                    max(0, int(width * max(0.0, min(1.0, value)))),
                    10, 3, 3)
                painter.setPen(QColor(palette.get("text", "#111111")))
                painter.drawText(2, top + 38, f"{100 * value:.4g} %")
            else:
                painter.setPen(QColor(palette.get("text_muted", "#667")))
                painter.drawText(2, top + 38, "not reported for this run")
            top += 30
        painter.end()

    def _painter(self):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        return painter


class MeasureExplainer(QFrame):
    """A non-modal panel with one measure's definition and this run's clock."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MeasureExplainer")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(300)
        self.setMaximumWidth(420)
        self._measure_id: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACING["md"], SPACING["md"],
                               SPACING["md"], SPACING["md"])
        lay.setSpacing(SPACING["sm"])

        header = QHBoxLayout()
        self._title = QLabel("")
        self._title.setObjectName("SectionTitle")
        self._title.setWordWrap(True)
        header.addWidget(self._title, 1)
        self._close = QPushButton("×")
        self._close.setObjectName("ExplainerClose")
        self._close.setFixedSize(22, 22)
        self._close.setToolTip("Close the explainer")
        self._close.clicked.connect(self.hide)
        header.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(header)

        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.PlainText)
        self._body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._body.setObjectName("ExplainerBody")
        lay.addWidget(self._body, 1)

        self._ladder = LadderBar()
        self._ladder.hide()
        lay.addWidget(self._ladder)

    # -- public ---------------------------------------------------------

    def measure_id(self) -> str | None:
        return self._measure_id

    def show_measure(self, measure_id: str, *,
                     ladder: dict[str, Any] | None = None,
                     values: dict[str, Any] | None = None) -> None:
        """Show one measure.  Unknown ids are ignored, not fatal."""
        try:
            measure = measures.get(measure_id)
        except ValueError:
            return
        self._measure_id = measure.id
        self._title.setText(f"{measure.name} ({measure.short})")
        self._body.setText(measures.explainer_text(measure.id, values=values))
        self._ladder.set_ladder(ladder)
        self._ladder.setVisible(ladder is not None)
        self.show()

    def explain_label(self, label: str, **kwargs: Any) -> bool:
        """Show the measure a report label or acronym names."""
        measure = measures.for_label(label)
        if measure is None:
            return False
        self.show_measure(measure.id, **kwargs)
        return True

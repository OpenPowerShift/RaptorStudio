"""The Story page: the answer first, then the evidence.

Painted with QPainter like the other result views; no plotting dependency.
The page only draws `raptor.api.insight.story.Story`; the numbers come from
the same builder the command line and MCP use.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from raptor.api.insight import measures
from raptor.api.insight.story import Story
from raptor.gui.resources.design_tokens import active_colour
from raptor.gui.ui.chart_theme import cause_brush, cause_colour, has_pattern

DOT_H = 86
DRIVER_ROW = 26


def _pct(value: float | None) -> str:
    return "" if value is None else f"{100 * value:.4g} %"


class MeasureChip(QLabel):
    """A key-number label that opens the measure explainer when clicked."""

    clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._measure_id: str | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_measure(self, measure_id: str | None) -> None:
        self._measure_id = measure_id

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt name
        if (event.button() == Qt.MouseButton.LeftButton
                and self._measure_id):
            self.clicked.emit(self._measure_id)
        super().mouseReleaseEvent(event)


class DotPlot(QWidget):
    """One dot per quantile, stacked by value, with the target line."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._story: Story | None = None
        self.setFixedHeight(DOT_H + 12)
        self.setMinimumWidth(220)

    def set_story(self, story: Story | None) -> None:
        self._story = story
        self.update()

    def sizeHint(self):  # noqa: N802 - Qt name
        from PySide6.QtCore import QSize
        return QSize(600, DOT_H + 12)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt name
        story = self._story
        if story is None or not story.dots:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = active_colour()
        muted = QColor(colour["muted"])
        accent, stop = QColor(colour["accent"]), QColor(colour["stop"])
        values = [d["value"] for d in story.dots]
        target = story.dot_target
        low = min(values + ([target] if target is not None else []))
        high = max(values + ([target] if target is not None else []))
        pad = max(1e-9, (high - low) * 0.08)
        low, high = low - pad, high + pad
        left, width = 10.0, self.width() - 20.0
        y = 8.0
        height = DOT_H

        def px(value: float) -> float:
            return left + width * (value - low) / (high - low)

        if target is not None:
            p.setPen(QPen(stop, 1, Qt.PenStyle.DashLine))
            p.drawLine(int(px(target)), int(y), int(px(target)), int(y + height))
            p.setPen(stop)
            p.drawText(QRectF(px(target) + 3, y, 120, 16),
                       Qt.AlignmentFlag.AlignLeft,
                       f"target {100 * target:.4g} %")
        # dots stack upward; a row has at most ~14 px per dot
        per_row = max(1, int(width // 14))
        for i, dot in enumerate(story.dots):
            row, col = divmod(i, per_row)
            x = left + 7 + col * 14
            cy = y + height - 10 - row * 13
            p.setPen(QPen(QColor(colour["border"]), 1))
            p.setBrush(accent if (target is None or dot["value"] >= target) else stop)
            p.drawEllipse(QRectF(x - 4, cy - 4, 8, 8))
            if dot["count"] > 1:
                p.setPen(muted)
                p.drawText(QRectF(x + 5, cy - 8, 18, 16),
                           Qt.AlignmentFlag.AlignLeft, str(dot["count"]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(muted)
        fm = QFontMetrics(self.font())
        caption = story.proportion or "no target stated"
        p.drawText(QRectF(left, y + height + 2, width, 14),
                   Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(caption, Qt.TextElideMode.ElideRight, int(width)))
        p.end()


class DriverStrip(QWidget):
    """Top three drivers plus the rest, each bar split by cause."""

    element_clicked = Signal(str, int, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._story: Story | None = None
        self._hits: list[tuple[QRectF, Any]] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def set_story(self, story: Story | None) -> None:
        self._story = story
        self.setFixedHeight(self.sizeHint().height())
        self.update()

    def sizeHint(self):  # noqa: N802 - Qt name
        from PySide6.QtCore import QSize
        rows = len((self._story.drivers if self._story else []) or [])
        return QSize(600, 8 + max(1, rows) * DRIVER_ROW + 8)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt name
        story = self._story
        if story is None or not story.drivers:
            return
        p = QPainter(self)
        colour = active_colour()
        text, muted = QColor(colour["text"]), QColor(colour["muted"])
        fm = QFontMetrics(self.font())
        self._hits = []
        x0, w = 8.0, self.width() - 16.0
        name_w, value_w = 170.0, 90.0
        bar_w = max(40.0, w - name_w - value_w)
        biggest = max([d.share for d in story.drivers] or [1.0]) or 1.0
        y = 6.0
        for driver in story.drivers:
            self._hits.append((QRectF(x0, y, w, DRIVER_ROW), driver))
            p.setPen(text)
            p.drawText(QRectF(x0, y, name_w - 6, DRIVER_ROW - 4),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(driver.name, Qt.TextElideMode.ElideRight,
                                     int(name_w - 6)))
            bx = x0 + name_w
            full = bar_w * driver.share / biggest
            for cause, part in sorted(driver.causes.items(), key=lambda kv: -kv[1]):
                width = full * part
                if width <= 0:
                    continue
                rect = QRectF(bx, y + 4, width, DRIVER_ROW - 10)
                p.fillRect(rect, cause_colour(cause))
                if has_pattern(cause) and rect.width() > 3:
                    p.fillRect(rect, cause_brush(cause))
                bx += width
            p.setPen(text)
            p.drawText(QRectF(x0 + name_w + bar_w + 4, y, value_w - 4,
                              DRIVER_ROW - 4),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       _pct(driver.share))
            y += DRIVER_ROW
        if story.other_drivers_share > 0:
            p.setPen(muted)
            p.drawText(QRectF(x0, y, w, 16), Qt.AlignmentFlag.AlignLeft,
                       f"others {_pct(story.other_drivers_share)}")
        p.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt name
        for rect, driver in self._hits:
            if rect.contains(event.position()):
                self.element_clicked.emit(driver.kind, int(driver.id), driver.name)
                return

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt name
        for rect, driver in self._hits:
            if rect.contains(event.position()):
                parts = ", ".join(f"{c} {_pct(v)}" for c, v in driver.causes.items())
                self.setToolTip(
                    f"{driver.name}\n{_pct(driver.share)} of the loss\n"
                    f"Downtime share {_pct(driver.downtime_share)}\n{parts}\n"
                    "Click to select it on the diagram")
                return
        self.setToolTip("")


class StoryPage(QWidget):
    """The Story tab's content: headline, dots, drivers, findings, chips."""

    element_clicked = Signal(str, int, str)
    measure_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)
        self.headline = QLabel("Run the model to see its story.")
        self.headline.setWordWrap(True)
        self.headline.setObjectName("StoryHeadline")
        head_font = QFont(self.headline.font())
        head_font.setPointSizeF(head_font.pointSizeF() + 6)
        head_font.setBold(True)
        self.headline.setFont(head_font)
        lay.addWidget(self.headline)
        self.target = QLabel("")
        self.target.setWordWrap(True)
        self.target.setObjectName("StoryTarget")
        lay.addWidget(self.target)
        self.dots = DotPlot()
        lay.addWidget(self.dots)
        self.drivers_caption = QLabel("")
        self.drivers_caption.setObjectName("SectionNote")
        lay.addWidget(self.drivers_caption)
        self.drivers = DriverStrip()
        self.drivers.element_clicked.connect(self.element_clicked)
        lay.addWidget(self.drivers)
        self.findings = QLabel("")
        self.findings.setWordWrap(True)
        self.findings.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.findings)
        self.numbers = QGridLayout()
        self.numbers.setHorizontalSpacing(14)
        lay.addLayout(self.numbers)
        self.sections = QLabel("")
        self.sections.setWordWrap(True)
        self.sections.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.sections)
        self.provenance = QLabel("")
        self.provenance.setObjectName("SectionNote")
        self.provenance.setWordWrap(True)
        lay.addWidget(self.provenance)
        lay.addStretch(1)
        self._story: Story | None = None
        self._chips: list[tuple[MeasureChip, QLabel]] = []
        self._build_chips()

    def _build_chips(self) -> None:
        while self.numbers.count():
            item = self.numbers.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._chips = []
        for i in range(6):
            label = QLabel("")
            label.setObjectName("StoryNumberLabel")
            value = MeasureChip()
            value.setObjectName("StoryNumberValue")
            value.clicked.connect(self.measure_clicked)
            font = QFont(value.font())
            font.setBold(True)
            value.setFont(font)
            self.numbers.addWidget(value, (i // 3) * 2, i % 3)
            self.numbers.addWidget(label, (i // 3) * 2 + 1, i % 3)
            self._chips.append((value, label))

    def story(self) -> Story | None:
        return self._story

    def set_story(self, story: Story | None) -> None:
        self._story = story
        if story is None or story.headline is None:
            self._clear_story()
            return
        self._show_story(story)

    def _clear_story(self) -> None:
        self.headline.setText("Run the model to see its story.")
        self.target.clear()
        self.dots.set_story(None)
        self.drivers.set_story(None)
        self.drivers_caption.clear()
        self.findings.clear()
        self.sections.clear()
        self.provenance.clear()
        for value, label in self._chips:
            value.clear()
            value.set_measure(None)
            value.setToolTip("")
            label.clear()
            label.setToolTip("")

    def _show_story(self, story: Story) -> None:  # noqa: C901, PLR0912
        head = story.headline
        assert head is not None
        self.headline.setText(f"{head.label}  {head.value_text}")
        target = head.target_text or "No target stated for this result."
        if head.target_text:
            target = f"{target} · {head.gap_text} · {story.proportion}"
        self.target.setText(target)
        self.dots.set_story(story)
        if story.drivers_available:
            ranked = ("lost capacity" if story.provenance.get("has_capacity")
                      else "downtime")
            self.drivers_caption.setText(f"Where the {ranked} comes from")
            self.drivers.set_story(story)
        else:
            self.drivers_caption.setText(
                "Turn on downtime attribution and re-run to see what drives the loss.")
            self.drivers.set_story(None)
        if story.findings:
            self.findings.setText(
                "\n".join(f"<b>{f.severity.title()}</b> · {f.message}"
                          for f in story.findings))
        else:
            self.findings.setText("No findings for this run.")
        for i, chip in enumerate(story.numbers):
            if i >= len(self._chips):
                break
            value, label = self._chips[i]
            if chip.value is None:
                shown = "n/a"
            elif chip.field in ("availability", "reliability", "dependability",
                                "capacity_weighted_availability"):
                shown = f"{100 * chip.value:.6g} %"
            else:
                shown = f"{chip.value:,.6g}" + (f" {chip.units}" if chip.units else "")
            value.setText(shown)
            label.setText(chip.label)
            definition = measures.for_metric(chip.field)
            value.set_measure(definition.id if definition is not None else None)
            if definition is not None:
                value.setToolTip("\n".join(measures.explain_lines(definition.id)))
                label.setToolTip(value.toolTip())
        for i in range(len(story.numbers), len(self._chips)):
            value, label = self._chips[i]
            value.clear()
            value.set_measure(None)
            label.clear()
        self.sections.setText("\n".join(
            f"<b>{section.title}</b> · {section.line}" for section in story.sections))
        prov = story.provenance
        parts = [f"{prov.get('runs', 0):,} runs"]
        if prov.get("run_length"):
            parts.append(f"{prov['run_length']:,.4g} {prov.get('units', '')} each")
        if prov.get("model_hash"):
            parts.append(f"model {str(prov['model_hash'])[:8]}")
        if prov.get("run_id"):
            parts.append(f"run {prov['run_id']}")
        self.provenance.setText(" · ".join(parts))

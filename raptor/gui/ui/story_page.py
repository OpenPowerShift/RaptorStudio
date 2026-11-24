"""The Overview's story section: what the key-number cards do not say.

The cards above it already show availability, reliability and the other
averages, so this section adds only:

* the verdict against a stated target, and a run-to-run dot plot showing how
  many runs in 100 met it (shown only when there is a target: without one the
  dots answer no question);
* one line naming what drives the downtime, linking to the Drivers tab where
  the detail lives (no second copy of the Drivers bars here);
* the Advisor's findings for the run.

The numbers come from `raptor.api.insight.story.build`, the builder the
command line and MCP use.
"""
from __future__ import annotations

import html

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from raptor.api.insight.attribution import CAUSE_LABELS
from raptor.api.insight.dots import meets
from raptor.api.insight.story import Story
from raptor.gui.resources.design_tokens import active_colour
from raptor.api.insight.axes import pct, nice_range, nice_ticks, stack_dots, tick_text

DOT_AREA_H = 72          # the stacked dots
AXIS_H = 20              # tick labels under them
CAPTION_H = 18


def _pct(value: float | None) -> str:
    return "" if value is None else f"{pct(value)}"


class DotPlot(QWidget):
    """Quantile dots placed at their value and stacked (Wilkinson), with
    the target as a dashed line; dots that missed it are drawn hollow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._story: Story | None = None
        self.setFixedHeight(DOT_AREA_H + AXIS_H + CAPTION_H + 8)
        self.setMinimumWidth(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_story(self, story: Story | None) -> None:
        self._story = story
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt name
        return QSize(600, self.height())

    def layout_dots(self, width: float) -> tuple[list[tuple[float, float, bool]],
                                                 float, tuple[float, float]]:
        """(x, y, met) per dot, the dot diameter and the value range."""
        story = self._story
        if story is None or not story.dots:
            return [], 0.0, (0.0, 1.0)
        values: list[float] = []
        for dot in story.dots:
            values.extend([float(dot["value"])] * int(dot.get("count", 1)))
        target = story.dot_target
        low = min(values + ([target] if target is not None else []))
        high = max(values + ([target] if target is not None else []))
        low, high = nice_range(low, high, 4)
        left, span = 12.0, max(1.0, width - 24.0)

        def px(value: float) -> float:
            return left + span * (value - low) / (high - low)

        # The dot size is the largest that keeps the tallest stack inside
        # the area, so a run set where most runs are perfect still fits.
        diameter = 10.0
        for _ in range(12):
            levels = stack_dots([px(v) for v in values], diameter)
            tallest = max(level for _x, level in levels) + 1
            if tallest * diameter <= DOT_AREA_H or diameter <= 3.0:
                break
            diameter = max(3.0, DOT_AREA_H / tallest)
        # A very tall stack overlaps its dots rather than leave the area.
        step = min(diameter, (DOT_AREA_H - diameter) / max(1, tallest - 1))
        out = []
        for value, (x, level) in zip(values, levels, strict=True):
            met = target is None or meets(value, target,
                                          story.headline.op if story.headline else None)
            out.append((x, DOT_AREA_H - diameter / 2 - level * step, met))
        return out, diameter, (low, high)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt name
        story = self._story
        if story is None or not story.dots:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = active_colour()
        muted, text = QColor(colour["muted"]), QColor(colour["text"])
        accent, stop = QColor(colour["accent"]), QColor(colour["stop"])
        dots, diameter, (low, high) = self.layout_dots(self.width())
        left, span = 12.0, max(1.0, self.width() - 24.0)

        def px(value: float) -> float:
            return left + span * (value - low) / (high - low)

        radius = diameter / 2 - 0.6
        for x, y, met in dots:
            if met:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(accent)
            else:
                p.setPen(QPen(stop, 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(x - radius, y - radius, 2 * radius, 2 * radius))
        base = float(DOT_AREA_H)
        p.setPen(QPen(QColor(colour["border"]), 1))
        p.drawLine(int(left), int(base), int(left + span), int(base))
        ticks = nice_ticks(low, high, 4)
        fm = QFontMetrics(self.font())
        p.setPen(muted)
        for tick in ticks:
            label = tick_text(tick, ticks, percent=True)
            x = px(tick)
            p.drawLine(int(x), int(base), int(x), int(base + 3))
            p.drawText(QRectF(x - 50, base + 3, 100, AXIS_H - 3),
                       Qt.AlignmentFlag.AlignHCenter, label)
        target = story.dot_target
        if target is not None:
            x = px(target)
            p.setPen(QPen(stop, 1.5, Qt.PenStyle.DashLine))
            p.drawLine(int(x), 0, int(x), int(base))
            p.setPen(stop)
            label = f"target {100 * target:.4g} %"
            w = fm.horizontalAdvance(label) + 6
            p.drawText(QRectF(min(x + 4, self.width() - w), 0, w, 16),
                       Qt.AlignmentFlag.AlignLeft, label)
        p.setPen(text)
        caption = (f"Each dot is 1 run in {len(dots)}; filled dots met the target. "
                   f"{story.proportion[:1].upper()}{story.proportion[1:]}.")
        p.drawText(QRectF(left, base + AXIS_H, span, CAPTION_H),
                   Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(caption, Qt.TextElideMode.ElideRight, int(span)))
        p.end()


class StoryPage(QWidget):
    """Verdict, run-to-run dots, the drivers line and the findings."""

    element_clicked = Signal(str, int, str)
    measure_clicked = Signal(str)
    drivers_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.headline = QLabel("Run the model to see its story.")
        self.headline.setWordWrap(True)
        self.headline.setObjectName("StoryHeadline")
        lay.addWidget(self.headline)
        self.target = QLabel("")
        self.target.setWordWrap(True)
        self.target.setTextFormat(Qt.TextFormat.PlainText)   # "< 0.01 %" is text
        self.target.setObjectName("SectionNote")
        lay.addWidget(self.target)
        self.dots = DotPlot()
        lay.addWidget(self.dots)
        # the verdict on the capacity target, when one is set
        self.capacity_verdict = QLabel("")
        self.capacity_verdict.setWordWrap(True)
        self.capacity_verdict.setTextFormat(Qt.TextFormat.PlainText)
        self.capacity_verdict.setObjectName("SectionNote")
        self.capacity_verdict.setVisible(False)
        lay.addWidget(self.capacity_verdict)
        self.drivers_caption = QLabel("")
        self.drivers_caption.setWordWrap(True)
        self.drivers_caption.setTextFormat(Qt.TextFormat.RichText)
        self.drivers_caption.linkActivated.connect(self._on_link)
        lay.addWidget(self.drivers_caption)
        self.hierarchies = QLabel("")
        self.hierarchies.setWordWrap(True)
        self.hierarchies.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.hierarchies)
        self.phases = QLabel("")
        self.phases.setWordWrap(True)
        self.phases.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.phases)
        self.findings = QLabel("")
        self.findings.setWordWrap(True)
        self.findings.setTextFormat(Qt.TextFormat.RichText)
        self.findings.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.findings)
        self._story: Story | None = None
        self._target_note = ""
        self._clear_story()

    def story(self) -> Story | None:
        return self._story

    def set_target_note(self, text: str) -> None:
        """Where the target came from, after the verdict line."""
        self._target_note = text
        if self._story is not None and self._story.headline is not None:
            self._show_story(self._story)

    def set_story(self, story: Story | None) -> None:
        self._story = story
        if story is None or story.headline is None:
            self._clear_story()
            return
        self._show_story(story)

    def _clear_story(self) -> None:
        self.headline.setText("Run the model to see its story.")
        self.headline.setVisible(False)
        self.target.clear()
        self.target.setVisible(False)
        self.dots.set_story(None)
        self.dots.setVisible(False)
        self.capacity_verdict.clear()
        self.capacity_verdict.setVisible(False)
        self.drivers_caption.clear()
        self.hierarchies.clear()
        self.findings.clear()

    def _show_story(self, story: Story) -> None:
        head = story.headline
        assert head is not None
        has_target = bool(head.target_text)
        # A verdict only means something against a target; without one the
        # cards above already show the value.
        self.headline.setVisible(has_target)
        self.target.setVisible(has_target)
        self.dots.setVisible(has_target and bool(story.dots))
        if has_target:
            self.headline.setText(
                f"<b>{html.escape(head.label)}</b> {html.escape(head.value_text)}"
                f" — {html.escape(head.target_text or '')}")
            self.target.setText(f"{head.gap_text} · {story.proportion}"
                                + (f" · {self._target_note}" if self._target_note else ""))
        self.dots.set_story(story if has_target else None)
        self.capacity_verdict.setText(story.capacity_verdict)
        self.capacity_verdict.setVisible(bool(story.capacity_verdict))
        self.drivers_caption.setText(self._drivers_text(story))
        self.hierarchies.setText(self._hierarchy_text(story))
        self.hierarchies.setVisible(bool(story.hierarchies))
        self.phases.setText(self._phase_text(story))
        self.phases.setVisible(bool(story.phases))
        if story.findings:
            self.findings.setText("<br>".join(
                f"<b>{html.escape(f.severity.title())}</b> · {html.escape(f.message)}"
                for f in story.findings))
        else:
            self.findings.setText("The Advisor has no findings for this run.")

    @staticmethod
    def _phase_text(story: Story) -> str:
        if not story.phases:
            return ""
        parts = []
        for item in story.phases[:4]:
            if not item.entered:
                parts.append(f"{html.escape(item.name)}: not entered")
                continue
            availability = ("n/a" if item.availability is None
                            else _pct(item.availability))
            mark = "" if item.mission else " (non-mission)"
            parts.append(f"{html.escape(item.name)} {availability}"
                         f" · {_pct(item.share)} down{mark}")
        if len(story.phases) > 4:
            parts.append(f"+{len(story.phases) - 4} more")
        return "<b>By phase:</b> " + " · ".join(parts)

    @staticmethod
    def _hierarchy_text(story: Story) -> str:
        if not story.hierarchies:
            return ""
        parts = []
        for item in story.hierarchies[:4]:
            reason = CAUSE_LABELS.get(item.main_cause, item.main_cause).lower()
            if item.status:
                parts.append(f"{html.escape(item.name)}: {item.status}")
            else:
                parts.append(f"{html.escape(item.name)} {_pct(item.share)}"
                             + (f" ({html.escape(reason)})" if reason else ""))
        if len(story.hierarchies) > 4:
            parts.append(f"+{len(story.hierarchies) - 4} more")
        return "<b>By hierarchy:</b> " + " · ".join(parts)

    @staticmethod
    def _drivers_text(story: Story) -> str:
        if not story.drivers_available:
            from raptor.api import run_needs
            return ("What drives the downtime: worked out from the event "
                    "log. " + run_needs.gui_hint("attribution"))
        if not story.drivers:
            return "No system outage in these runs."
        ranked = story.drivers_measure
        parts = []
        for driver in story.drivers:
            main = max(driver.causes.items(), key=lambda kv: kv[1])[0] \
                if driver.causes else ""
            reason = CAUSE_LABELS.get(main, main).lower()
            link = (f"el:{driver.kind}:{driver.id}:"
                    f"{html.escape(driver.name, quote=True)}")
            parts.append(f'<a href="{link}">{html.escape(driver.name)}</a> '
                         f"{html.escape(_pct(driver.share))}"
                         + (f" ({html.escape(reason)})" if reason else ""))
        if story.other_drivers_share > 0:
            parts.append(f"others {html.escape(_pct(story.other_drivers_share))}")
        return (f"<b>What drives the {ranked}:</b> " + " · ".join(parts)
                + ' &nbsp; <a href="drivers">Open Drivers ›</a>')

    def _on_link(self, link: str) -> None:
        if link == "drivers":
            self.drivers_requested.emit()
            return
        if link.startswith("el:"):
            _el, kind, oid, name = link.split(":", 3)
            self.element_clicked.emit(kind, int(oid), html.unescape(name))

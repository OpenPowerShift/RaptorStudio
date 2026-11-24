"""ChartSpec -> QPainter, with hover, a reading cursor, locate and export.

One widget paints every chart the Qt-free `raptor.api.insight.charts` module
produces; it never computes a value.  Every chart with axes uses one frame
(`_Frame`): round 1-2-5 ticks with gridlines, labels in the theme's text
colours, and values printed in the chart's own format (percent, money or
plain).  A chart whose bars are split by cause draws a key, since each cause
has its own pattern as well as its colour (so the chart reads in greyscale
and for colour-blind readers), and puts the element names in a gutter beside
the bars rather than on the patterns.

The curve charts (chance of reaching a value, convergence, the year fan)
follow the mouse with a cross-hair and read the value under it.  A mark that
carries a name selects the element in the canvas when clicked.
"""
from __future__ import annotations

import bisect
import csv
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import QApplication, QMenu, QToolTip, QWidget

from raptor.api.insight.attribution import CAUSE_LABELS
from raptor.api.insight.axes import (
    FAN_LINE_MIN, compact, currency, fit_dots, format_value, log_ticks, nice_range, nice_ticks, pct,
    period_axis, place_labels, tick_text,
)
from raptor.api.insight.charts import ChartSpec, bubble_tip
from raptor.gui.resources.design_tokens import active_colour
from raptor.gui.ui.chart_theme import cause_brush, cause_colour, cause_ink

PAD = 16              # outer margin
TITLE_H = 44          # title and subtitle
KEY_H = 20            # one row of the cause key
CAPTION_H = 36        # up to two lines of caption
COMPACT_H = 330       # below this: no subtitle, one key row, one caption line
TICK_GAP = 6


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


@dataclass
class _Frame:
    """A plot rectangle and the data range it maps."""
    rect: QRectF
    x_low: float
    x_high: float
    y_low: float
    y_high: float
    x_log: bool = False

    def x(self, value: float) -> float:
        low, high, v = self.x_low, self.x_high, value
        if self.x_log:
            low, high = math.log10(low), math.log10(high)
            v = math.log10(max(value, 1e-300))
        if high <= low:
            return self.rect.center().x()
        return self.rect.left() + (v - low) / (high - low) * self.rect.width()

    def y(self, value: float) -> float:
        if self.y_high <= self.y_low:
            return self.rect.center().y()
        return self.rect.bottom() - ((value - self.y_low)
                                     / (self.y_high - self.y_low)
                                     * self.rect.height())

    def value_at_x(self, px: float) -> float:
        share = (px - self.rect.left()) / max(1.0, self.rect.width())
        if self.x_log:
            low, high = math.log10(self.x_low), math.log10(self.x_high)
            return 10 ** (low + share * (high - low))
        return self.x_low + share * (self.x_high - self.x_low)


class ChartWidget(QWidget):
    """Paints one `ChartSpec`; click a named mark to locate the element."""

    locate_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spec: ChartSpec | None = None
        #: (rect, kind, name) per clickable mark, and its tooltip
        self._hits: list[tuple[QRectF, str, str]] = []
        self._tips: list[str] = []
        #: treemap cells that gather several items, with the rows behind them
        self._detail_hits: list[tuple[QRectF, dict]] = []
        self._cursor: QPointF | None = None
        #: the reading cursor for curve charts: frame and x -> marks
        self._probe: tuple[_Frame, Callable[[float], list[tuple[QPointF, str]]]] | None = None
        #: U17: the run-to-run chart's zoomed time window, the drag that
        #: chooses one, and the frame it was drawn in
        self._zoom: tuple[float, float] | None = None
        self._drag_from: QPointF | None = None
        self._zoom_frame: _Frame | None = None
        self._caption_in_body = False
        # Small on purpose: the results dock is as tall as its tallest
        # page's minimum, and a tall floor here stops the user dragging the
        # bottom splitter (the chart repaints at whatever height it gets).
        self.setMinimumHeight(60)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    # -- public ---------------------------------------------------------

    def spec(self) -> ChartSpec | None:
        return self._spec

    def set_spec(self, spec: ChartSpec | None) -> None:
        self._spec = spec
        self._hits, self._tips = [], []
        self._detail_hits = []
        self._probe = None
        self._zoom = self._drag_from = self._zoom_frame = None
        self.setAccessibleName(
            "Result chart" if spec is None else str(spec.title or "Result chart"))
        self.setAccessibleDescription(
            "" if spec is None else " ".join(
                part for part in (spec.subtitle, spec.caption) if part))
        self.update()

    def zoom_to(self, start: float | None, end: float | None = None) -> None:
        """Show only `start`..`end` of the run-to-run chart's time axis
        (None: the whole run)."""
        self._zoom = None if start is None or end is None or end <= start \
            else (float(start), float(end))
        self.update()

    #: The page a saved or copied chart is drawn for (`charts_svg.PAGES`).
    export_page = "a4"

    def save_svg(self, path: str) -> None:
        from raptor.gui.ui import chart_export
        if self._spec is not None:
            chart_export.save_svg(self._spec, path, self.export_page)

    def save_png(self, path: str) -> None:
        """The saved drawing at print resolution, not a screen grab: the
        same layout as the SVG and PDF, every label in full."""
        from raptor.gui.ui import chart_export
        if self._spec is not None:
            chart_export.save_png(self._spec, path, self.export_page)

    def save_pdf(self, path: str) -> None:
        from raptor.gui.ui import chart_export
        if self._spec is not None:
            chart_export.save_pdf(self._spec, path, self.export_page)

    def save_csv(self, path: str) -> None:
        spec = self._spec
        if spec is None:
            return
        from raptor.api.insight.charts import csv_table
        header, rows = csv_table(spec)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)

    def copy_image(self) -> None:
        """The saved drawing, for pasting into a document or slide."""
        from raptor.gui.ui import chart_export
        if self._spec is not None:
            QApplication.clipboard().setImage(
                chart_export.image(self._spec, self.export_page, dpi=200))

    # -- formatting -----------------------------------------------------

    def _fmt(self) -> str:
        return str((self._spec.provenance if self._spec else {}).get("format", ""))

    def _value(self, value: float, digits: int = 4) -> str:
        return format_value(float(value), self._fmt(), digits)

    # -- painting -------------------------------------------------------

    def _currency(self) -> str | None:
        """The currency this chart's money is printed in (its spec's)."""
        return (self._spec.provenance.get("currency")
                if self._spec is not None else None)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt name
        with currency(self._currency()):
            self._paint_event(event)

    def _paint_event(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = active_colour()
        painter.fillRect(self.rect(), QColor(palette["surface"]))
        spec = self._spec
        if spec is None:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No chart for this result.")
            painter.end()
            return
        # Compact below COMPACT_H: in a 190 px Results panel (a 1280 x 720
        # screen) the title, a two-row key and the caption took all the
        # height and no plot was drawn at all.
        compact = self.height() < COMPACT_H
        title_h = 30 if compact else TITLE_H
        caption_h = 20 if compact else CAPTION_H
        fm = QFontMetrics(self.font())
        title_font = QFont(self.font())
        title_font.setPointSizeF(title_font.pointSizeF() + (1 if compact else 2))
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(palette["text"]))
        title_w = QFontMetrics(title_font).horizontalAdvance(spec.title)
        painter.drawText(QRectF(PAD, 4 if compact else 6, self.width() - 2 * PAD, 24),
                         Qt.AlignmentFlag.AlignLeft if compact
                         else Qt.AlignmentFlag.AlignHCenter, spec.title)
        painter.setFont(self.font())
        if not compact:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(QRectF(PAD, 28, self.width() - 2 * PAD, 16),
                             Qt.AlignmentFlag.AlignHCenter,
                             fm.elidedText(spec.subtitle, Qt.TextElideMode.ElideRight,
                                           self.width() - 2 * PAD))
        self._hits, self._tips = [], []
        self._detail_hits = []
        self._probe = None
        self._caption_in_body = False
        if compact:
            # the key on the title's line, after it
            key_h = self._paint_key(painter, spec, palette, 6, max_rows=1,
                                    start=PAD + title_w + 24)
            top = max(float(title_h), 6 + key_h)
        else:
            top = title_h + self._paint_key(painter, spec, palette, title_h)
        area = QRectF(PAD, top + 4, max(1.0, self.width() - 2 * PAD),
                      max(1.0, self.height() - top - 4 - caption_h))
        if area.height() >= 40 and area.width() >= 120:
            draw = getattr(self, f"_paint_{spec.kind}", self._paint_none)
            draw(painter, spec, palette, area)
            self._paint_probe(painter, palette)
        else:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(QRectF(PAD, top, self.width() - 2 * PAD,
                                    max(16.0, self.height() - top - caption_h)),
                             Qt.AlignmentFlag.AlignCenter,
                             "Make this panel taller (or float it) to see the chart.")
        if spec.caption and not self._caption_in_body:
            painter.setPen(QColor(palette["text"]))
            rect = QRectF(PAD, self.height() - caption_h + 2,
                          self.width() - 2 * PAD, caption_h - 4)
            if compact:
                painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 fm.elidedText(spec.caption, Qt.TextElideMode.ElideRight,
                                               int(rect.width())))
            else:
                painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                                 spec.caption)
        painter.end()

    # The cause key: one swatch per cause the chart shows.

    @staticmethod
    def _causes(spec: ChartSpec) -> list[str]:  # noqa: C901
        seen: list[str] = []

        def add(cause) -> None:
            if cause and str(cause) not in seen and str(cause) != "None":
                seen.append(str(cause))

        if spec.kind in ("pareto",):
            for row in spec.series:
                for cause in (row.get("causes") or {}):
                    add(cause)
        elif spec.kind in ("waterfall", "treemap", "icicle"):
            # a cell with no cause ("(no single element)", "N others") is
            # painted as Other, so the key must list Other for it
            for row in spec.series:
                if row.get("is_group"):
                    continue
                cause = row.get("cause")
                add(cause if cause and str(cause) != "None" else "other")
        elif spec.kind == "sankey":
            for row in spec.series:
                add(row.get("cause"))
        return seen

    def _key_entries(self, painter: QPainter, spec: ChartSpec,
                     palette: dict[str, str]) -> list[tuple[str, Callable[[QRectF], None]]]:
        """(label, swatch painter) for each thing the chart's key explains."""
        def solid(colour: str) -> Callable[[QRectF], None]:
            return lambda rect: painter.fillRect(rect, QColor(colour))

        def patterned(cause: str) -> Callable[[QRectF], None]:
            def swatch(rect: QRectF) -> None:
                painter.fillRect(rect, cause_colour(cause))
                painter.fillRect(rect, cause_brush(cause))
            return swatch

        def whisker(rect: QRectF) -> None:
            painter.setPen(QPen(QColor(palette["text"]), 1.2))
            y = rect.center().y()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.drawLine(QPointF(rect.left(), y - 4), QPointF(rect.left(), y + 4))
            painter.drawLine(QPointF(rect.right(), y - 4), QPointF(rect.right(), y + 4))

        labels = {**CAUSE_LABELS, **(spec.provenance.get("group_labels") or {})}
        entries = [(labels.get(c, c), patterned(c)) for c in self._causes(spec)]
        if spec.kind == "sunburst":
            from raptor.api.insight.hierarchy import SERIES_COLOURS
            roots = [node for node in spec.series if not node.get("parent")]
            roots.sort(key=lambda node: -float(node.get("value") or 0.0))
            entries += [(str(node.get("name") or ""),
                         solid(SERIES_COLOURS[i % len(SERIES_COLOURS)]))
                        for i, node in enumerate(roots)]
        if spec.kind == "pareto" and any(_number(r.get("sem")) for r in spec.series):
            entries.append(("± 2 standard errors", whisker))
        solids = {"capacity_pareto": (("lost capacity", "accent"), ("downtime", "muted")),
                  "ribbon": (("system up", "result_green"), ("system down", "result_red"))}
        for label, key in solids.get(spec.kind, ()):
            entries.append((label, solid(palette[key])))
        if spec.kind == "timeline":
            from raptor.api.insight.timecharts import in_key
            for layer in filter(in_key, spec.series):
                entries.append((str(layer.get("name", "")),
                                self._layer_swatch(painter, palette, layer)))
        return entries

    def _layer_swatch(self, painter: QPainter, palette: dict[str, str],
                      layer: dict) -> Callable[[QRectF], None]:
        colour = _tone(palette, layer)

        def swatch(rect: QRectF) -> None:
            if layer.get("role") == "area":
                painter.fillRect(rect, colour)
                return
            painter.setPen(_layer_pen(palette, layer))
            y = rect.center().y()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        return swatch

    def _paint_key(self, painter: QPainter, spec: ChartSpec,
                   palette: dict[str, str], top: float, max_rows: int = 0,
                   start: float = PAD) -> float:
        """The key, wrapped onto rows; at most `max_rows` (0: no limit),
        the entries that do not fit then counted as "+N more". The first
        row starts at `start`."""
        entries = self._key_entries(painter, spec, palette)
        if not entries:
            return 0.0
        fm = QFontMetrics(self.font())
        x, y, rows = float(start), top, 1
        for index, (label, draw) in enumerate(entries):
            width = 18 + fm.horizontalAdvance(label) + 14
            if x + width > self.width() - PAD and x > PAD:
                if max_rows and rows >= max_rows:
                    painter.setPen(QColor(palette["muted"]))
                    painter.drawText(QRectF(x, y, self.width() - PAD - x, KEY_H),
                                     Qt.AlignmentFlag.AlignVCenter,
                                     f"+{len(entries) - index} more")
                    break
                x, y, rows = float(PAD), y + KEY_H, rows + 1
            draw(QRectF(x, y + 4, 14, 12))
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(x + 18, y, width - 18, KEY_H),
                             Qt.AlignmentFlag.AlignVCenter, label)
            x += width
        return rows * KEY_H

    # The common frame.

    def _frame(self, painter: QPainter, spec: ChartSpec,
               palette: dict[str, str], area: QRectF, *,
               x_range: tuple[float, float], y_range: tuple[float, float],
               x_fmt: str = "", y_fmt: str = "", x_log: bool = False,
               x_ticks: Sequence[float] | None = None,
               y_ticks: Sequence[float] | None = None,
               x_label: str | None = None, y_label: str | None = None,
               y_axis: bool = True) -> _Frame:
        fm = QFontMetrics(self.font())
        yt = list(y_ticks) if y_ticks is not None else nice_ticks(*y_range, 5)
        xt = list(x_ticks) if x_ticks is not None else nice_ticks(*x_range, 6)
        y_text = [tick_text(t, yt, fmt=y_fmt) for t in yt]
        x_text = ([_short(t) for t in xt] if x_log
                  else [tick_text(t, xt, fmt=x_fmt) for t in xt])
        gutter = max([fm.horizontalAdvance(t) for t in y_text] + [10]) + TICK_GAP
        y_title = (y_label if y_label is not None else spec.y_label) or ""
        x_title = (x_label if x_label is not None else spec.x_label) or ""
        if self.height() < COMPACT_H:
            x_title = ""          # the plot needs the line more than the title
        left = area.left() + gutter + (fm.height() + 4 if y_title else 0)
        bottom = area.bottom() - fm.height() - 4 - (fm.height() + 2 if x_title else 0)
        rect = QRectF(left, area.top() + fm.height() / 2,
                      max(1.0, area.right() - left - 8),
                      max(1.0, bottom - area.top() - fm.height() / 2))
        frame = _Frame(rect, x_range[0], x_range[1], y_range[0], y_range[1],
                       x_log)
        grid = QColor(palette["border"])
        axis = QColor(palette["muted"])
        painter.setPen(QPen(grid, 1))
        for t in yt:
            y = frame.y(t)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        painter.setPen(axis)
        last_y = 1e9
        for t, text in zip(yt, y_text, strict=True):
            y = frame.y(t)
            if abs(last_y - y) < fm.height():
                continue          # a short plot: never stack tick labels
            last_y = y
            painter.drawText(QRectF(rect.left() - gutter - 2, y - fm.height() / 2,
                                    gutter - 2, fm.height()),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             text)
        last_right = -1e9
        for t, text in zip(xt, x_text, strict=True):
            x = frame.x(t)
            width = fm.horizontalAdvance(text)
            painter.drawLine(QPointF(x, rect.bottom()), QPointF(x, rect.bottom() + 4))
            if x - width / 2 < last_right + 6:
                continue               # never print two labels on top of each other
            painter.drawText(QRectF(x - width / 2 - 2, rect.bottom() + 4,
                                    width + 4, fm.height()),
                             Qt.AlignmentFlag.AlignHCenter, text)
            last_right = x + width / 2
        painter.setPen(QPen(axis, 1))
        painter.drawLine(QPointF(rect.left(), rect.bottom()),
                         QPointF(rect.right(), rect.bottom()))
        if y_axis:
            painter.drawLine(QPointF(rect.left(), rect.top()),
                             QPointF(rect.left(), rect.bottom()))
        painter.setPen(QColor(palette["text"]))
        if x_title:
            painter.drawText(QRectF(rect.left(), rect.bottom() + fm.height() + 6,
                                    rect.width(), fm.height()),
                             Qt.AlignmentFlag.AlignHCenter, x_title)
        if y_title:
            painter.save()
            painter.translate(area.left() + fm.height() / 2 + 1, rect.center().y())
            painter.rotate(-90)
            painter.drawText(QRectF(-rect.height() / 2, -fm.height() / 2,
                                    rect.height(), fm.height()),
                             Qt.AlignmentFlag.AlignCenter,
                             fm.elidedText(y_title, Qt.TextElideMode.ElideRight,
                                           int(rect.height())))
            painter.restore()
        return frame

    def _target_line(self, painter, palette, frame: _Frame, value: float, *,
                     horizontal: bool, label: str) -> None:
        stop = QColor(palette["stop"])
        painter.setPen(QPen(stop, 1.6, Qt.PenStyle.DashLine))
        fm = QFontMetrics(self.font())
        r = frame.rect
        if not label:
            if horizontal:
                painter.drawLine(QPointF(r.left(), frame.y(value)),
                                 QPointF(r.right(), frame.y(value)))
            else:
                painter.drawLine(QPointF(frame.x(value), r.top()),
                                 QPointF(frame.x(value), r.bottom()))
            return
        if horizontal:
            y = frame.y(value)
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            w = fm.horizontalAdvance(label) + 8
            plate = QRectF(r.right() - w, y - fm.height() - 1, w, fm.height())
            painter.fillRect(plate, QColor(palette["surface"]))
            painter.setPen(stop)
            painter.drawText(plate, Qt.AlignmentFlag.AlignCenter, label)
        else:
            x = frame.x(value)
            painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            w = fm.horizontalAdvance(label) + 8
            left = x + 4 if x + 4 + w <= r.right() else x - 4 - w
            plate = QRectF(left, r.top(), w, fm.height())
            painter.fillRect(plate, QColor(palette["surface"]))
            painter.setPen(stop)
            painter.drawText(plate, Qt.AlignmentFlag.AlignCenter, label)

    def _quartile_lines(self, painter, spec, palette, frame: _Frame, *,
                        at_share: bool) -> float:
        """U16: quiet dotted lines at the quartiles, labelled along the top
        under the target's label; a label that would overlap the one
        before it drops a line.  Returns the bottom of the lowest label
        (the plot top when there are none), so marks drawn after it can
        stay clear of the labels."""
        marks = [m for m in spec.marks
                 if m.get("kind") == "quartile" and _number(m.get("value"))]
        if not marks:
            return frame.rect.top()
        fm = QFontMetrics(self.font())
        r = frame.rect
        muted = QColor(palette["muted"])
        last_right, row = -1e9, 0
        lowest = r.top()
        for mark in marks:
            x = frame.x(float(mark["share"]) if at_share else float(mark["value"]))
            painter.setPen(QPen(muted, 1.0, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            label = (f"{mark.get('label', '')}: " if at_share else f"{mark.get('label', '')} ") \
                + self._value(float(mark["value"]), 4)
            w = fm.horizontalAdvance(label) + 6
            left = min(max(r.left(), x - w / 2), r.right() - w)
            row = row + 1 if left < last_right + 4 else 0
            plate = QRectF(left, r.top() + fm.height() * (1 + row) + 2, w, fm.height())
            painter.fillRect(plate, QColor(palette["surface"]))
            painter.setPen(muted)
            painter.drawText(plate, Qt.AlignmentFlag.AlignCenter, label)
            last_right = left + w
            lowest = max(lowest, plate.bottom())
        return lowest

    def _hit(self, rect: QRectF, kind: str, name: str, tip: str) -> None:
        self._hits.append((rect, kind, name))
        self._tips.append(tip)

    def _paint_none(self, painter, spec, palette, area) -> None:
        """Nothing to draw: say why, in the middle. It said "This chart
        cannot be drawn from the data", which read as a fault, with the
        reason small at the bottom."""
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(area.adjusted(20, 0, -20, 0),
                         Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                         spec.caption or "Nothing to show for this run.")
        self._caption_in_body = True

    def _fit_rows(self, rows: list, area: QRectF) -> tuple[list, int]:
        """As many bar rows as the height can label (16 px a row), and how
        many were left out: in a short panel ten names were drawn on top
        of each other."""
        room = max(1, int((area.height() - 40) // 16))
        return (rows, 0) if len(rows) <= room else (rows[:room], len(rows) - room)

    def _more_rows(self, painter, palette, frame: _Frame, hidden: int) -> None:
        spec = self._spec
        items = spec.provenance.get("items") if spec is not None else None
        shown = len(spec.series) - hidden if spec is not None else 0
        compact = self.height() < COMPACT_H     # no subtitle to carry "top 10 of 220"
        if isinstance(items, int) and items > shown and (hidden or compact):
            text = f"{shown} of {items:,} shown"
            if hidden:
                text += ": make the panel taller to see more"
        elif hidden:
            text = f"{hidden} more not shown: make the panel taller"
        else:
            return
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(QRectF(frame.rect.left(), frame.rect.top() - 16,
                                frame.rect.width(), 16),
                         Qt.AlignmentFlag.AlignRight, text)

    # -- the charts -----------------------------------------------------

    def _target(self, spec: ChartSpec) -> float | None:
        return next((float(m["value"]) for m in spec.marks
                     if m.get("kind") == "target" and _number(m.get("value"))),
                    None)

    def _paint_dot_plot(self, painter, spec, palette, area) -> None:
        values: list[float] = []
        for dot in (spec.series[0].get("dots", []) if spec.series else []):
            if _number(dot.get("value")):
                values.extend([float(dot["value"])] * max(1, int(dot.get("count", 1))))
        if not values:
            return self._paint_none(painter, spec, palette, area)
        target = self._target(spec)
        low = min(values + ([target] if target is not None else []))
        high = max(values + ([target] if target is not None else []))
        if spec.provenance.get("x_range"):
            low, high = (float(v) for v in spec.provenance["x_range"])
        else:
            low, high = nice_range(low, high, 5, share=self._fmt() == "percent")
            pad = max(1e-9, (high - low) * 0.03)
            low, high = low - pad, high + pad
        fmt = self._fmt()
        frame = self._frame(painter, spec, palette, area, x_range=(low, high),
                            y_range=(0, 1), x_fmt=fmt, y_ticks=[], y_label="",
                            y_axis=False)
        r = frame.rect
        labels = self._quartile_lines(painter, spec, palette, frame, at_share=False)
        # a tall stack used to paint over the quartile labels ("Q3 9_9 %")
        room = min(r.height() * 0.85, r.bottom() - labels - 2)
        diameter, levels = fit_dots([frame.x(v) for v in values], max(room, 8.0))
        accent, stop = QColor(palette["accent"]), QColor(palette["stop"])
        radius = diameter / 2 - 0.6
        for value, (x, level) in zip(values, levels, strict=True):
            x = min(max(x, r.left() + diameter), r.right() - diameter)
            y = r.bottom() - diameter / 2 - level * diameter
            higher = spec.provenance.get("higher_is_better", True)
            met = target is None or (value >= target if higher
                                     else value <= target)
            if met:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
            else:
                painter.setPen(QPen(stop, 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(x, y), radius, radius)
            self._hit(QRectF(x - radius, y - radius, 2 * radius, 2 * radius),
                      "dot", "", self._value(value, 6))
        if target is not None:
            self._target_line(painter, palette, frame, target, horizontal=False,
                              label=f"target {self._value(target, 5)}")

    def _paint_ecdf(self, painter, spec, palette, area) -> None:
        # A spec from before the chart became an exceedance curve.
        self._paint_none(painter, spec, palette, area)

    def _paint_exceedance(self, painter, spec, palette, area) -> None:
        points = [p for p in (spec.series[0].get("points") or [])
                  if _number(p.get("value")) and _number(p.get("share"))] \
            if spec.series else []
        if not points:
            return self._paint_none(painter, spec, palette, area)
        fmt = self._fmt()
        target = self._target(spec)
        values = [float(p["value"]) for p in points]
        low = min(values + ([target] if target is not None else []))
        high = max(values + ([target] if target is not None else []))
        low, high = nice_range(low, high, 5, share=self._fmt() == "percent")
        frame = self._frame(painter, spec, palette, area, x_range=(0.0, 1.0),
                            y_range=(low, high), x_fmt="percent", y_fmt=fmt,
                            x_ticks=[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7,
                                     0.8, 0.9, 1.0])
        self._quartile_lines(painter, spec, palette, frame, at_share=True)
        # A step curve: the value holds until the share of runs reaching it
        # rises past the next run.
        path = QPainterPath(QPointF(frame.x(0.0), frame.y(values[0])))
        previous = 0.0
        for point in points:
            share, value = float(point["share"]), float(point["value"])
            path.lineTo(QPointF(frame.x(previous), frame.y(value)))
            path.lineTo(QPointF(frame.x(share), frame.y(value)))
            previous = share
        painter.setPen(QPen(QColor(palette["accent"]), 2.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        word = str(spec.provenance.get("reached") or "at least")
        if target is not None:
            higher = bool(spec.provenance.get("higher_is_better", True))
            met = max((float(p["share"]) for p in points
                       if (float(p["value"]) >= target if higher
                           else float(p["value"]) <= target)), default=0.0)
            self._target_line(painter, palette, frame, target, horizontal=True,
                              label=f"target {self._value(target, 5)}: "
                                    f"{pct(met)} of runs reach it")

        def read(px: float) -> list[tuple[QPointF, str]]:
            share = min(1.0, max(1.0 / len(values) if values else 0.0,
                                 frame.value_at_x(px)))
            value = next((float(p["value"]) for p in points
                          if float(p["share"]) >= share - 1e-12),
                         float(points[-1]["value"]))
            return [(QPointF(frame.x(share), frame.y(value)),
                     f"{pct(share)} of runs reached {word} "
                     f"{format_value(value, fmt, 5)}")]

        self._probe = (frame, read)

    def _paint_pareto(self, painter, spec, palette, area) -> None:
        rows = [row for row in spec.series if _number(row.get("value"))]
        if not rows:
            return self._paint_none(painter, spec, palette, area)
        rows, hidden = self._fit_rows(rows, area)
        fm = QFontMetrics(self.font())
        fmt = self._fmt()
        money = fmt == "money"
        gutter = min(area.width() * 0.4,
                     max(fm.horizontalAdvance(str(r["name"])) for r in rows) + 10)
        labels = [self._bar_label(row, money) for row in rows]
        value_w = min(area.width() * 0.42,
                      max(fm.horizontalAdvance(t) for t in labels) + 10)
        labels = [fm.elidedText(text, Qt.TextElideMode.ElideRight,
                                int(max(1.0, value_w - 8)))
                  for text in labels]
        # a share's uncertainty range stops at 0 and 100 %, and so does its axis
        ceiling = 1.0 if fmt == "percent" and all(float(r["value"]) <= 1.0
                                                   for r in rows) else math.inf
        peak = max(min(ceiling, float(r["value"]) + 2 * float(r.get("sem") or 0))
                   for r in rows)
        peak = min(ceiling, nice_range(0.0, peak or 1.0, 4)[1])
        plot = QRectF(area.left() + gutter, area.top(),
                      max(40.0, area.width() - gutter - value_w),
                      area.height())
        frame = self._frame(painter, spec, palette, plot, x_range=(0.0, peak),
                            y_range=(0, 1), x_fmt=fmt, y_ticks=[], y_label="",
                            x_label=spec.y_label)
        self._more_rows(painter, palette, frame, hidden)
        r = frame.rect
        band = r.height() / len(rows)
        bar_h = max(2.0, min(26.0, band * 0.64))
        for i, (row, label) in enumerate(zip(rows, labels, strict=True)):
            top = r.top() + i * band + (band - bar_h) / 2
            width = frame.x(float(row["value"])) - r.left()
            causes = row.get("causes") or {}
            if causes:
                cursor = r.left()
                for cause, share in causes.items():
                    part = width * float(share)
                    cell = QRectF(cursor, top, part, bar_h)
                    painter.fillRect(cell, cause_colour(cause))
                    painter.fillRect(cell, cause_brush(cause))
                    cursor += part
            else:
                painter.fillRect(QRectF(r.left(), top, width, bar_h),
                                 QColor(palette["accent"]))
            sem = row.get("sem")
            if _number(sem) and float(sem) > 0:
                lo = frame.x(max(0.0, float(row["value"]) - 2 * float(sem)))
                hi = frame.x(min(ceiling, float(row["value"]) + 2 * float(sem)))
                mid = top + bar_h / 2
                painter.setPen(QPen(QColor(palette["text"]), 1.2))
                painter.drawLine(QPointF(lo, mid), QPointF(hi, mid))
                painter.drawLine(QPointF(lo, mid - 4), QPointF(lo, mid + 4))
                painter.drawLine(QPointF(hi, mid - 4), QPointF(hi, mid + 4))
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(area.left(), top - 4, gutter - 8, bar_h + 8),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(str(row["name"]),
                                           Qt.TextElideMode.ElideMiddle,
                                           int(gutter - 8)))
            end = max(width, frame.x(min(ceiling, float(row["value"])
                                         + 2 * float(row.get("sem") or 0))) - r.left())
            painter.drawText(QRectF(r.left() + end + 6, top - 4, value_w, bar_h + 8),
                             Qt.AlignmentFlag.AlignVCenter, label)
            tip = f"{row['name']}: {label}"
            if causes:
                tip += "\n" + "\n".join(
                    f"  {CAUSE_LABELS.get(c, c)}: {pct(float(v))}"
                    for c, v in causes.items())
            self._hit(QRectF(area.left(), top, gutter + max(8.0, width), bar_h),
                      str(row.get("kind") or
                          spec.provenance.get("row_kind") or "element"),
                      str(row["name"]), tip)

    def _bar_label(self, row: dict, money: bool) -> str:
        if money:
            text = format_value(float(row["value"]), "money")
            if _number(row.get("share")):
                text += f" ({pct(float(row['share']))})"
            return text
        text = self._value(float(row["value"]), 3)
        if _number(row.get("availability")):
            text += f" (availability {format_value(float(row['availability']), 'percent', 5)})"
        if row.get("mission") is False:
            text += " (non-mission)"
        if row.get("entered") is False:
            text += " (not entered)"
        return text

    def _paint_waterfall(self, painter, spec, palette, area) -> None:
        steps = [s for s in spec.series if _number(s.get("from")) and _number(s.get("to"))]
        if not steps:
            return self._paint_none(painter, spec, palette, area)
        operational = spec.provenance.get("operational")
        target = self._target(spec) if _number(operational) else None
        ends = [float(s["to"]) for s in steps] + [float(s["from"]) for s in steps] \
            + ([target] if target is not None else [])
        # Zoomed so the steps fill the height, with room under the result
        # so its bar is never a sliver.
        span = max(ends) - min(ends)
        low, high = nice_range(min(ends) - 0.35 * span, max(ends), 5)
        low = max(0.0, low)
        high = min(1.0, high) if max(ends) <= 1.0 else high
        # With the run's availability the steps fall from 100 % to it and a
        # last column shows the result; as shares of the loss they fall to
        # zero and a result column would be empty.
        columns = len(steps) + (1 if _number(operational) else 0)
        frame = self._frame(painter, spec, palette, area,
                            x_range=(0, columns), y_range=(low, high),
                            y_fmt="percent", x_ticks=[], x_label="",
                            y_label="availability" if _number(operational)
                            else spec.y_label)
        r = frame.rect
        fm = QFontMetrics(self.font())
        width = r.width() / columns
        for i, step in enumerate(steps):
            x = r.left() + i * width
            y0, y1 = frame.y(float(step["from"])), frame.y(float(step["to"]))
            cell = QRectF(x + width * 0.15, min(y0, y1), width * 0.7,
                          max(1.5, abs(y0 - y1)))
            cause = str(step.get("cause"))
            painter.fillRect(cell, cause_colour(cause))
            painter.fillRect(cell, cause_brush(cause))
            if i + 1 < len(steps):
                painter.setPen(QPen(QColor(palette["muted"]), 1, Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(cell.right(), y1),
                                 QPointF(cell.right() + width * 0.3, y1))
            drop = (f"−{_points(float(step['value']))} pts" if _number(operational)
                    else f"{pct(float(step['value']))}")
            painter.setPen(QColor(palette["text"]))
            # Under the step, where the fall ends, unless that is the axis:
            # then just inside the top of the bar, in the bar's own ink.
            if max(y0, y1) + fm.height() + 2 < r.bottom():
                painter.drawText(QRectF(x, max(y0, y1) + 2, width, fm.height()),
                                 Qt.AlignmentFlag.AlignHCenter, drop)
            elif abs(y0 - y1) < fm.height() + 6:
                # a sliver on the axis: the label sits above it, clear of
                # the column name under the axis
                painter.drawText(QRectF(x, min(y0, y1) - fm.height() - 2, width,
                                        fm.height()),
                                 Qt.AlignmentFlag.AlignHCenter, drop)
            else:
                painter.setPen(cause_ink(cause))
                plate = QRectF(x + width / 2 - fm.horizontalAdvance(drop) / 2 - 3,
                               min(y0, y1) + 3, fm.horizontalAdvance(drop) + 6,
                               fm.height())
                painter.fillRect(plate, cause_colour(cause))
                painter.drawText(plate, Qt.AlignmentFlag.AlignCenter, drop)
            self._column_label(painter, palette, QRectF(x, r.bottom() + 4, width,
                                                        2 * fm.height()),
                               str(step["name"]))
            self._hit(cell, "cause", cause, f"{step['name']}: {drop}")
        if not _number(operational):
            return
        end = float(steps[-1]["to"])
        x = r.left() + len(steps) * width
        cell = QRectF(x + width * 0.15, frame.y(end), width * 0.7,
                      max(1.5, r.bottom() - frame.y(end)))
        painter.fillRect(cell, QColor(palette["accent"]))
        painter.setPen(QColor(palette["text"]))
        painter.drawText(QRectF(x, frame.y(end) - fm.height() - 2, width, fm.height()),
                         Qt.AlignmentFlag.AlignHCenter,
                         format_value(end, "percent", 6))
        self._column_label(painter, palette, QRectF(x, r.bottom() + 4, width,
                                                    2 * fm.height()),
                           "achieved" if _number(operational) else "rest")
        if target is not None:
            # last, over the result column, so its label is not hidden
            self._target_line(painter, palette, frame, target, horizontal=True,
                              label=f"target {format_value(target, 'percent', 5)}")

    def _column_label(self, painter, palette, rect: QRectF, text: str) -> None:
        fm = QFontMetrics(self.font())
        painter.setPen(QColor(palette["text"]))
        lines = _wrap(text, fm, int(rect.width() - 4), 2)
        painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         "\n".join(lines))

    def _paint_ladder(self, painter, spec, palette, area) -> None:
        rows = [row for row in spec.series if _number(row.get("value"))]
        if not rows:
            return self._paint_none(painter, spec, palette, area)
        fm = QFontMetrics(self.font())
        values = [float(r["value"]) for r in rows]
        target = self._target(spec)
        # Zoomed towards 100 %: the rungs differ by small amounts, and on a
        # 0-100 % axis every bar would look full.
        top_value = 1.0 if max(values) <= 1.0 else max(values)
        bottom = min(values + ([target] if target is not None else []))
        span = max(top_value - bottom, 1e-9)
        low, high = nice_range(bottom - 0.6 * span, top_value, 4,
                               share=top_value <= 1.0)
        low, high = max(0.0, low), (min(1.0, high) if top_value <= 1.0 else high)
        gutter = min(area.width() * 0.45,
                     max(fm.horizontalAdvance(str(r["name"])) for r in rows) + 10)
        texts = []
        previous = None
        for value in values:
            text = format_value(value, "percent", 6)
            if previous is not None and previous - value > 1e-12:
                text += f"  (−{_points(previous - value)} pts)"
            texts.append(text)
            previous = value
        value_w = max(fm.horizontalAdvance(t) for t in texts) + 10
        plot = QRectF(area.left() + gutter, area.top(),
                      max(40.0, area.width() - gutter - value_w), area.height())
        frame = self._frame(painter, spec, palette, plot, x_range=(low, high),
                            y_range=(0, 1), x_fmt="percent", y_ticks=[],
                            y_label="", x_label="availability (axis zoomed)")
        r = frame.rect
        band = r.height() / len(rows)
        bar_h = max(2.0, min(24.0, band * 0.6))
        colours = [palette["result_green"], palette["accent"], palette["relationship"]]
        for i, (row, text) in enumerate(zip(rows, texts, strict=True)):
            top = r.top() + i * band + (band - bar_h) / 2
            value = float(row["value"])
            right = frame.x(value)
            painter.fillRect(QRectF(r.left(), top, right - r.left(), bar_h),
                             QColor(colours[i % len(colours)]))
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(area.left(), top - 4, gutter - 8, bar_h + 8),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(str(row["name"]), Qt.TextElideMode.ElideRight,
                                           int(gutter - 8)))
            painter.drawText(QRectF(right + 6, top - 4, area.right() - right, bar_h + 8),
                             Qt.AlignmentFlag.AlignVCenter, text)
            self._hit(QRectF(r.left(), top, r.width(), bar_h), "rung",
                      str(row["name"]), f"{row['name']}: {text}")
        if target is not None:
            self._target_line(painter, palette, frame, target, horizontal=False,
                              label=f"target {format_value(target, 'percent', 5)}")

    def _paint_histogram(self, painter, spec, palette, area) -> None:
        bins = [b for b in spec.series if _number(b.get("lo")) and _number(b.get("hi"))
                and _number(b.get("count"))]
        if not bins:
            return self._paint_none(painter, spec, palette, area)
        lo = max(float(bins[0]["lo"]), 1e-12)
        hi = max(float(bins[-1]["hi"]), lo * 10)
        peak = max(float(b["count"]) for b in bins) or 1.0
        decades = range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1)
        steps = (1, 2, 5) if len(decades) <= 4 else (1,)
        ticks = [m * 10.0 ** k for k in decades for m in steps
                 if lo <= m * 10.0 ** k <= hi]
        if len(ticks) < 2:
            ticks = sorted({lo, hi})
        y_range = nice_range(0.0, peak, 4)
        counts = nice_ticks(*y_range, 4)
        if all(float(b["count"]).is_integer() for b in bins):
            counts = [t for t in counts if float(t).is_integer()]
        frame = self._frame(painter, spec, palette, area, x_range=(lo, hi),
                            y_range=y_range, y_ticks=counts, x_log=True,
                            x_ticks=ticks, y_fmt="count",
                            x_label=f"{spec.x_label} (log scale)")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["accent"]))
        for row in bins:
            x0, x1 = frame.x(float(row["lo"])), frame.x(float(row["hi"]))
            y = frame.y(float(row["count"]))
            rect = QRectF(x0 + 1, y, max(1.0, x1 - x0 - 2), frame.rect.bottom() - y)
            painter.drawRect(rect)
            self._hit(rect, "bin", "", f"{_short(float(row['lo']))} to "
                      f"{_short(float(row['hi']))}: {float(row['count']):,.0f} outages")
        last_right = -1e9
        fm = QFontMetrics(self.font())
        for mark in spec.marks:
            if mark.get("kind") == "quantile" and _number(mark.get("value")):
                value = float(mark["value"])
                x = frame.x(value)
                label = str(mark.get("label") or "")
                shown = _short(value)
                if shown not in label:
                    label = f"{label} {shown}".strip()
                width = fm.horizontalAdvance(label) + 8
                if x - last_right < width + 4:
                    label = ""
                else:
                    last_right = x + width
                self._target_line(painter, palette, frame, value,
                                  horizontal=False, label=label)

    def _scatter_frame(self, painter, spec, palette, area, points, *,
                       square: bool = False, include: Sequence[tuple[float, float]] = (),
                       x_fmt: str | None = None, y_fmt: str | None = None):
        xs = [float(p["x"]) for p in points] + [x for x, _y in include]
        ys = [float(p["y"]) for p in points] + [y for _x, y in include]
        fmt = self._fmt()
        x_high = nice_range(0.0, max(xs) * 1.08 or 1.0, 5)[1]
        y_high = nice_range(0.0, max(ys) * 1.12 or 1.0, 5)[1]
        if fmt == "percent":
            # shares stop at 100 % (the axes ran to 125 %)
            x_high = min(x_high, 1.0) if max(xs) <= 1.0 else x_high
            y_high = min(y_high, 1.0) if max(ys) <= 1.0 else y_high
        if square:
            x_high = y_high = max(x_high, y_high)
        if spec.provenance.get("x_log") and min(xs) > 0:
            # RAW from 1 to 2,600: a linear axis piled most blocks at the left
            lo, hi = min(xs) / 1.5, max(xs) * 1.5
            return self._frame(painter, spec, palette, area, x_range=(lo, hi),
                               y_range=(0.0, y_high), x_log=True,
                               x_ticks=log_ticks(lo, hi),
                               x_label=f"{spec.x_label} (log scale)",
                               x_fmt=fmt if x_fmt is None else x_fmt,
                               y_fmt=fmt if y_fmt is None else y_fmt)
        return self._frame(painter, spec, palette, area, x_range=(0.0, x_high),
                           y_range=(0.0, y_high),
                           x_fmt=fmt if x_fmt is None else x_fmt,
                           y_fmt=fmt if y_fmt is None else y_fmt)

    def _label_points(self, painter, palette, frame: _Frame,
                      items: list[tuple[float, float, float, str]]) -> None:
        """Label every point without two labels on top of each other: a
        crowded label moves out and a thin leader joins it to its dot."""
        fm = QFontMetrics(self.font())
        r = frame.rect
        placed = place_labels([(x, y, radius) for x, y, radius, _t in items],
                              [(fm.horizontalAdvance(t) + 4, fm.height())
                               for *_xy, t in items],
                              (r.left(), r.top(), r.width(), r.height()))
        for (x, y, radius, text), (rect, end) in zip(items, placed, strict=True):
            if end is not None:
                painter.setPen(QPen(QColor(palette["muted"]), 0.8))
                dx, dy = end[0] - x, end[1] - y
                dist = max(1e-6, (dx * dx + dy * dy) ** 0.5)
                painter.drawLine(QPointF(x + dx / dist * radius, y + dy / dist * radius),
                                 QPointF(*end))
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(*rect), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_bubble(self, painter, spec, palette, area) -> None:
        points = [p for p in spec.series if _number(p.get("x")) and _number(p.get("y"))]
        if not points:
            return self._paint_none(painter, spec, palette, area)
        frame = self._scatter_frame(painter, spec, palette, area, points,
                                    x_fmt=spec.provenance.get("x_format", "percent"),
                                    y_fmt="")
        area_peak = max(float(p.get("area") or 0) for p in points) or 1.0
        fill = QColor(palette["accent"])
        fill.setAlpha(110)
        labels: list[tuple[float, float, float, str]] = []
        for point in sorted(points, key=lambda p: -float(p.get("area") or 0)):
            x, y = frame.x(float(point["x"])), frame.y(float(point["y"]))
            radius = 4 + 20 * (float(point.get("area") or 0) / area_peak) ** 0.5
            y = min(max(y, frame.rect.top() + radius),
                    frame.rect.bottom() - radius)
            painter.setPen(QPen(QColor(palette["accent"]), 1.2))
            painter.setBrush(fill)
            painter.drawEllipse(QPointF(x, y), radius, radius)
            if len(labels) < 8:
                labels.append((x, y, radius, str(point["name"])))
            self._hit(QRectF(x - radius, y - radius, radius * 2, radius * 2),
                      "element", str(point["name"]),
                      bubble_tip(spec, point))
        self._label_points(painter, palette, frame, labels)

    def _paint_quadrant(self, painter, spec, palette, area) -> None:
        points = [p for p in spec.series if _number(p.get("x")) and _number(p.get("y"))]
        if not points:
            return self._paint_none(painter, spec, palette, area)
        frame = self._scatter_frame(painter, spec, palette, area, points,
                                    include=[(1.0, 1.0)])
        painter.setPen(QPen(QColor(palette["muted"]), 1, Qt.PenStyle.DashLine))
        r = frame.rect
        painter.drawLine(QPointF(frame.x(1.0), r.top()), QPointF(frame.x(1.0), r.bottom()))
        painter.drawLine(QPointF(r.left(), frame.y(1.0)), QPointF(r.right(), frame.y(1.0)))
        self._points(painter, palette, frame, points, lambda p:
                     f"{p['name']}: RAW {float(p['x']):.3g}, RRW {float(p['y']):.3g}")

    def _points(self, painter, palette, frame, points, tip) -> None:
        labels = []
        for index, point in enumerate(points):
            x, y = frame.x(float(point["x"])), frame.y(float(point["y"]))
            y = min(max(y, frame.rect.top() + 5), frame.rect.bottom() - 5)
            x = min(max(x, frame.rect.left() + 5), frame.rect.right() - 5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette["accent"]))
            painter.drawEllipse(QPointF(x, y), 4.5, 4.5)
            if index < 8:
                labels.append((x, y, 5.0, str(point["name"])))
            self._hit(QRectF(x - 6, y - 6, 12, 12), "element", str(point["name"]),
                      tip(point))
        self._label_points(painter, palette, frame, labels)

    def _paint_scatter(self, painter, spec, palette, area) -> None:
        points = [p for p in spec.series if _number(p.get("x")) and _number(p.get("y"))]
        if not points:
            return self._paint_none(painter, spec, palette, area)
        frame = self._scatter_frame(painter, spec, palette, area, points, square=True)
        if any(mark.get("kind") == "diagonal" for mark in spec.marks):
            r = frame.rect
            painter.setPen(QPen(QColor(palette["muted"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.top()))
        self._points(painter, palette, frame, points, lambda p:
                     f"{p['name']}: {pct(float(p['x']))} of downtime, "
                     f"{pct(float(p['y']))} of lost capacity")

    def _paint_convergence(self, painter, spec, palette, area) -> None:
        points = [p for p in spec.series if _number(p.get("mean"))]
        if not points:
            return self._paint_none(painter, spec, palette, area)
        fmt = self._fmt()
        means = [float(p["mean"]) for p in points]
        spreads = [2 * float(p["sem"]) if _number(p.get("sem")) else 0.0
                   for p in points]
        low = min(m - s for m, s in zip(means, spreads, strict=True))
        high = max(m + s for m, s in zip(means, spreads, strict=True))
        # a share's +/- 2 SE band stops at 0 and 100 % (it drew a 125 % tick)
        low, high = nice_range(low, high, 5,
                               share=fmt == "percent" and max(means) <= 1.0)
        n = len(points)
        frame = self._frame(painter, spec, palette, area,
                            x_range=(1.0, max(2.0, float(n))), y_range=(low, high),
                            x_fmt="count", y_fmt=fmt,
                            # whole runs only: one run gave ticks 1.2, 1.4
                            x_ticks=[t for t in nice_ticks(1.0, max(2.0, float(n)), 6)
                                     if float(t).is_integer()],
                            y_label="running mean")

        def pt(i: int, value: float) -> QPointF:
            return QPointF(frame.x(i + 1), frame.y(min(high, max(low, value))))

        band = QPolygonF([pt(i, means[i] + spreads[i]) for i in range(n)]
                         + [pt(i, means[i] - spreads[i]) for i in reversed(range(n))])
        fill = QColor(palette["accent"])
        fill.setAlpha(45)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPolygon(band)
        path = QPainterPath(pt(0, means[0]))
        for i in range(1, n):
            path.lineTo(pt(i, means[i]))
        painter.setPen(QPen(QColor(palette["accent"]), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        target = self._target(spec)
        if target is not None and low <= target <= high:
            self._target_line(painter, palette, frame, target, horizontal=True,
                              label=f"target {format_value(target, fmt, 5)}")
        median = next((float(m["value"]) for m in spec.marks
                       if m.get("kind") == "median" and _number(m.get("value"))),
                      None)
        if median is not None and low <= median <= high:
            fm = QFontMetrics(self.font())
            y = frame.y(median)
            painter.setPen(QPen(QColor(palette["muted"]), 1.2,
                                Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(frame.rect.left(), y),
                             QPointF(frame.rect.right(), y))
            label = f"median {format_value(median, fmt, 5)}"
            w = fm.horizontalAdvance(label) + 8
            painter.fillRect(QRectF(frame.rect.left(), y - fm.height() - 1,
                                    w, fm.height()), QColor(palette["surface"]))
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(QRectF(frame.rect.left() + 4, y - fm.height(), w,
                                    fm.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             label)

        def read(px: float) -> list[tuple[QPointF, str]]:
            i = min(n - 1, max(0, round(frame.value_at_x(px)) - 1))
            text = f"after {i + 1:,} runs: {format_value(means[i], fmt, 5)}"
            if spreads[i]:
                text += f" ± {format_value(spreads[i], fmt, 2)}"
            return [(pt(i, means[i]), text)]

        self._probe = (frame, read)

    def _paint_curve(self, painter, spec, palette, area) -> None:
        series = spec.series[0] if spec.series else {}
        points = [p for p in series.get("points") or []
                  if _number(p.get("x")) and _number(p.get("y"))]
        if not points:
            return self._paint_none(painter, spec, palette, area)
        xs = [float(p["x"]) for p in points]
        ys = [float(p["y"]) for p in points]
        frame = self._frame(painter, spec, palette, area,
                            x_range=nice_range(min(xs), max(xs), 6),
                            y_range=nice_range(min(ys), max(ys), 5))
        path = QPainterPath(QPointF(frame.x(xs[0]), frame.y(ys[0])))
        for x, y in zip(xs[1:], ys[1:], strict=True):
            path.lineTo(QPointF(frame.x(x), frame.y(y)))
        painter.setPen(QPen(QColor(palette["accent"]), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        def read(px: float) -> list[tuple[QPointF, str]]:
            x = frame.value_at_x(px)
            i = min(range(len(xs)), key=lambda k: abs(xs[k] - x))
            return [(QPointF(frame.x(xs[i]), frame.y(ys[i])),
                     f"{_short(xs[i])}: {_short(ys[i])}")]

        self._probe = (frame, read)

    def _paint_capacity_pareto(self, painter, spec, palette, area) -> None:
        rows = spec.series
        if not rows:
            return self._paint_none(painter, spec, palette, area)
        rows, hidden = self._fit_rows(list(rows), area)
        fm = QFontMetrics(self.font())
        peak = max(max(float(row.get("value") or 0), float(row.get("downtime") or 0))
                   for row in rows) or 1.0
        peak = nice_range(0.0, peak, 4)[1]
        gutter = min(area.width() * 0.4,
                     max(fm.horizontalAdvance(str(r["name"])) for r in rows) + 10)
        value_w = fm.horizontalAdvance("100 %") + 10
        plot = QRectF(area.left() + gutter, area.top(),
                      max(40.0, area.width() - gutter - value_w), area.height())
        frame = self._frame(painter, spec, palette, plot, x_range=(0.0, peak),
                            y_range=(0, 1), x_fmt="percent", y_ticks=[],
                            y_label="", x_label="share of the total")
        self._more_rows(painter, palette, frame, hidden)
        r = frame.rect
        band = r.height() / len(rows)
        bar_h = max(2.0, min(12.0, band * 0.32))
        labels: list[tuple[QRectF, str]] = []
        for i, row in enumerate(rows):
            top = r.top() + i * band + band / 2 - bar_h - 1
            for j, (key, colour) in enumerate((("value", palette["accent"]),
                                               ("downtime", palette["muted"]))):
                value = float(row.get(key) or 0)
                y = top + j * (bar_h + 2)
                painter.fillRect(QRectF(r.left(), y, frame.x(value) - r.left(), bar_h),
                                 QColor(colour))
                # the values go on after every bar, so no bar covers a label
                labels.append((QRectF(frame.x(value) + 4, y - 3, value_w + 20, bar_h + 6),
                               format_value(value, "percent", 3)))
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(area.left(), top - 2, gutter - 8, 2 * bar_h + 6),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(str(row["name"]), Qt.TextElideMode.ElideMiddle,
                                           int(gutter - 8)))
            self._hit(QRectF(area.left(), top, gutter + r.width(), 2 * bar_h + 2),
                      "element", str(row["name"]),
                      f"{row['name']}: {pct(float(row.get('value') or 0))} of "
                      f"lost capacity, {pct(float(row.get('downtime') or 0))} "
                      "of downtime")
        painter.setPen(QColor(palette["text"]))
        for rect, text in labels:
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_fan(self, painter, spec, palette, area) -> None:
        rows = [row for row in spec.series if _number(row.get("p50"))]
        if not rows:
            return self._paint_none(painter, spec, palette, area)
        fmt = self._fmt()
        target = self._target(spec)
        lows = [float(r["p5"]) for r in rows if _number(r.get("p5"))]
        highs = [float(r["p95"]) for r in rows if _number(r.get("p95"))]
        mids = [float(r["p50"]) for r in rows]
        low = min(lows + mids + ([target] if target is not None else []))
        high = max(highs + mids + ([target] if target is not None else []))
        low, high = nice_range(low, high, 5, share=fmt == "percent")
        if fmt == "percent":
            low, high = max(0.0, low), min(1.0, high)
        n = len(rows)
        x_range, x_ticks = period_axis(n)
        frame = self._frame(painter, spec, palette, area,
                            x_range=x_range, y_range=(low, high),
                            x_ticks=x_ticks, x_fmt="count", y_fmt=fmt)
        if n < FAN_LINE_MIN:
            self._paint_period_boxes(painter, palette, frame, rows)
        else:
            self._paint_fan_bands(painter, palette, frame, rows)
        if target is not None:
            word = next((str(m.get("label")) for m in spec.marks
                         if m.get("kind") == "target" and m.get("label")), "threshold")
            self._target_line(painter, palette, frame, target, horizontal=True,
                              label=f"{word} {format_value(target, fmt, 5)}")

        def read(px: float) -> list[tuple[QPointF, str]]:
            i = min(n - 1, max(0, round(frame.value_at_x(px)) - 1))
            row = rows[i]
            text = f"{row.get('name', i + 1)}: median {format_value(mids[i], fmt, 5)}"
            if _number(row.get("p5")) and _number(row.get("p95")):
                text += (f", 90 % between {format_value(float(row['p5']), fmt, 4)}"
                         f" and {format_value(float(row['p95']), fmt, 4)}")
            return [(QPointF(frame.x(i + 1), frame.y(mids[i])), text)]

        self._probe = (frame, read)

    def _paint_period_boxes(self, painter, palette, frame, rows) -> None:
        """One box per period: no line to suggest a trend between them."""
        accent = QColor(palette["accent"])
        half = min(40.0, (frame.x(2) - frame.x(1)) * 0.3)
        fill = QColor(accent)
        fill.setAlpha(90)
        for i, r in enumerate(rows):
            x = frame.x(i + 1)
            if _number(r.get("p5")) and _number(r.get("p95")):
                painter.setPen(QPen(accent, 1.5))
                painter.drawLine(QPointF(x, frame.y(float(r["p95"]))),
                                 QPointF(x, frame.y(float(r["p5"]))))
            if _number(r.get("p25")) and _number(r.get("p75")):
                top, bottom = frame.y(float(r["p75"])), frame.y(float(r["p25"]))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRect(QRectF(x - half, top, 2 * half, bottom - top))
            y = frame.y(float(r["p50"]))
            painter.setPen(QPen(accent, 2.5))
            painter.drawLine(QPointF(x - half, y), QPointF(x + half, y))

    def _paint_fan_bands(self, painter, palette, frame, rows) -> None:
        n = len(rows)
        mids = [float(r["p50"]) for r in rows]

        def pt(i: int, value: float) -> QPointF:
            return QPointF(frame.x(i + 1), frame.y(value))

        def band(upper: str, lower: str, alpha: int) -> None:
            if not all(_number(r.get(upper)) and _number(r.get(lower)) for r in rows):
                return
            polygon = QPolygonF([pt(i, float(r[upper])) for i, r in enumerate(rows)]
                                + [pt(i, float(rows[i][lower]))
                                   for i in reversed(range(n))])
            fill = QColor(palette["accent"])
            fill.setAlpha(alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPolygon(polygon)

        band("p95", "p5", 35)
        band("p75", "p25", 70)
        path = QPainterPath(pt(0, mids[0]))
        for i in range(1, n):
            path.lineTo(pt(i, mids[i]))
        painter.setPen(QPen(QColor(palette["accent"]), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _paint_timeline(self, painter, spec, palette, area) -> None:  # noqa: C901
        layers = [layer for layer in spec.series if layer.get("t")]
        if not layers:
            return self._paint_none(painter, spec, palette, area)
        labelled = ([layer for layer in layers if layer.get("end_text")]
                    if spec.provenance.get("end_labels") else [])
        label_w = 0.0
        if labelled:
            # right margin for the direct end labels (never over a third)
            fm = QFontMetrics(self.font())
            label_w = min(max(fm.horizontalAdvance(str(layer["end_text"]))
                              for layer in labelled) + 8, area.width() * 0.4)
            area = QRectF(area.left(), area.top(),
                          max(1.0, area.width() - label_w - 6), area.height())
        fmt = self._fmt()
        t_end = max(float(layer["t"][-1]) for layer in layers)
        t_start = min(float(layer["t"][0]) for layer in layers)
        whole = (t_start, t_end)
        if self._zoom is not None:
            t_start, t_end = max(t_start, self._zoom[0]), min(t_end, self._zoom[1])
            if t_end <= t_start:
                t_start, t_end = whole
        zoomed = (t_start, t_end) != whole
        y_low, y_high = (float(v) for v in (spec.provenance.get("y_range") or [0.0, 1.0])[:2])
        if y_high <= y_low:
            y_high = y_low + 1.0
        if fmt != "percent" and not spec.provenance.get("y_axis_exact"):
            y_low, y_high = nice_range(y_low, y_high, 5)
        y_ticks = nice_ticks(y_low, y_high, 5)
        if fmt == "percent" and y_high >= 1.0:
            # room above 100 %: a run that never went down drew its line on
            # the frame's top edge, and the chart looked empty
            y_high += (y_high - y_low) * 0.04
        x_label = (f"{spec.x_label} · drag across or Ctrl+wheel to zoom in"
                   + (", double-click for the whole run" if zoomed else ""))
        frame = self._frame(painter, spec, palette, area,
                            x_range=(t_start, max(t_end, t_start + 1e-9)),
                            y_range=(y_low, y_high), y_fmt=fmt,
                            y_ticks=y_ticks, x_label=x_label)
        self._zoom_frame = frame

        def pt(t: float, value: float) -> QPointF:
            return QPointF(frame.x(t), frame.y(min(y_high, max(y_low, value))))

        painter.save()
        painter.setClipRect(frame.rect)
        for layer in layers:
            self._timeline_layer(painter, palette, layer, pt, y_low)
        painter.restore()
        if labelled:
            self._timeline_end_labels(painter, palette, frame, labelled, pt,
                                      label_w, t_end)
        target = self._target(spec)
        if target is not None and y_low <= target <= y_high:
            self._target_line(painter, palette, frame, target, horizontal=True,
                              label=f"target {format_value(target, fmt, 5)}")
        self._probe = (frame, _timeline_reader(
            layers, fmt, str(spec.provenance.get("time_units") or ""), frame, pt))

    def _timeline_end_labels(self, painter: QPainter, palette: dict[str, str],
                             frame: _Frame, layers: list[dict],
                             pt: Callable[[float, float], QPointF], label_w: float,
                             t_end: float) -> None:
        """Name and end value beside each line's end, in the line's colour;
        labels nudged apart so none overlap, with a leader from a moved one."""
        from raptor.api.insight.timecharts import spread_labels
        fm = QFontMetrics(self.font())
        ends = []
        for layer in layers:
            ts = [float(t) for t in layer["t"]]
            k = max(0, bisect.bisect_right(ts, t_end) - 1)   # last visible point
            ends.append(pt(ts[k], float(layer["y"][k])))
        gap = fm.height() + 1.0
        rect = frame.rect
        placed = spread_labels([p.y() for p in ends], gap,
                               rect.top() + gap / 2, rect.bottom() - gap / 2)
        x = rect.right() + 8
        bold = QFont(self.font())
        bold.setBold(True)
        bold_fm = QFontMetrics(bold)
        for layer, end, ly in zip(layers, ends, placed, strict=True):
            colour = _tone(palette, layer)
            if abs(ly - end.y()) > 1.5:
                painter.setPen(QPen(colour, 0.8))
                painter.drawLine(QPointF(min(end.x(), rect.right()), end.y()),
                                 QPointF(x - 2, ly))
            painter.setFont(bold)
            painter.setPen(colour)
            value = str(layer.get("end_value") or "")
            room = label_w - bold_fm.horizontalAdvance(f" {value}")
            text = (bold_fm.elidedText(str(layer.get("name") or ""),
                                       Qt.TextElideMode.ElideRight, int(max(room, 20)))
                    + f" {value}")
            painter.drawText(QRectF(x, ly - gap / 2, label_w + 4, gap),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             text)
        painter.setFont(self.font())

    @staticmethod
    def _timeline_layer(painter: QPainter, palette: dict[str, str], layer: dict,
                        pt: Callable[[float, float], QPointF], y_low: float) -> None:
        """One layer: an area between `lo` and `hi`, or a line through `y`."""
        ts = [float(x) for x in layer["t"]]
        colour = _tone(palette, layer)
        if layer.get("role") == "area":
            lo = [float(v) for v in layer["lo"]]
            hi = [float(v) for v in layer["hi"]]
            polygon = QPolygonF([pt(t, v) for t, v in zip(ts, hi, strict=True)]
                                + [pt(t, v) for t, v in zip(reversed(ts), reversed(lo),
                                                             strict=True)])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawPolygon(polygon)
            return
        ys = [float(v) for v in layer["y"]]
        if layer.get("fill"):
            under = QColor(colour)
            under.setAlpha(40)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(under)
            painter.drawPolygon(QPolygonF([pt(t, v) for t, v in zip(ts, ys, strict=True)]
                                          + [pt(ts[-1], y_low), pt(ts[0], y_low)]))
        path = QPainterPath(pt(ts[0], ys[0]))
        for t, v in zip(ts[1:], ys[1:], strict=True):
            path.lineTo(pt(t, v))
        painter.setPen(_layer_pen(palette, layer))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _cell_text(self, painter, rect: QRectF, cause: str, lines: Sequence[str]) -> None:
        fm = QFontMetrics(self.font())
        if rect.width() < 34 or rect.height() < fm.height() + 2:
            return
        ink = cause_ink(cause)
        # A plate behind the text, in the cell's own colour, so the pattern
        # never runs through the letters.
        y = rect.top() + 2
        for line in lines:
            if y + fm.height() > rect.bottom() - 1:
                break
            text = fm.elidedText(line, Qt.TextElideMode.ElideRight, int(rect.width() - 8))
            if not text or text == "…":
                break
            plate = QRectF(rect.left() + 2, y, fm.horizontalAdvance(text) + 6, fm.height())
            painter.fillRect(plate, cause_colour(cause))
            painter.setPen(ink)
            painter.drawText(plate.adjusted(3, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)
            y += fm.height()

    def _paint_treemap(self, painter, spec, palette, area) -> None:
        rows = [row for row in spec.series if float(row.get("w") or 0) > 0]
        if not rows:
            return self._paint_none(painter, spec, palette, area)
        width = max(float(row["x"]) + float(row["w"]) for row in rows) or 1.0
        height = max(float(row["y"]) + float(row["h"]) for row in rows) or 1.0
        group_labels: list[tuple[QRectF, str]] = []
        for row in rows:
            cell = QRectF(area.left() + float(row["x"]) / width * area.width(),
                          area.top() + float(row["y"]) / height * area.height(),
                          float(row["w"]) / width * area.width(),
                          float(row["h"]) / height * area.height())
            if row.get("is_group"):
                painter.fillRect(cell, QColor(palette["group_header"]))
                painter.setPen(QPen(QColor(palette["muted"]), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(cell)
                group_labels.append((cell, str(row.get("name") or "")))
                continue
            cause = str(row.get("cause") or "other")
            painter.fillRect(cell, cause_colour(cause))
            painter.fillRect(cell, cause_brush(cause))
            painter.setPen(QPen(QColor(palette["surface"]), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(cell)
            fmt = str(spec.provenance.get("format") or "percent")
            shown = (float(row.get("share") or 0.0) if fmt == "percent"
                     and _number(row.get("share")) else float(row["value"]))
            amount = format_value(shown, fmt, 3)
            if fmt != "percent" and _number(row.get("share")):
                amount += f" ({pct(float(row['share']))})"
            self._cell_text(painter, cell, cause, [str(row["name"]), amount])
            what = str(spec.provenance.get("measure") or "system downtime")
            labels = {**CAUSE_LABELS, **(spec.provenance.get("group_labels") or {})}
            if str(row.get("name")) != "Not in a hierarchy":
                self._hit(cell, str(spec.provenance.get("row_kind") or "element"),
                          str(row["name"]),
                          f"{row['name']}: {amount}"
                          + (" of " + what if fmt == "percent" else "")
                          + f", mostly {labels.get(cause, cause).lower()}")
            if row.get("items"):
                # "96 others" and other gathered cells: right-click lists them
                self._detail_hits.append((cell, row))
        fm = QFontMetrics(self.font())
        for cell, name in group_labels:
            if cell.height() < 18 or cell.width() < 24:
                continue
            width = min(max(1.0, cell.width() - 8),
                        fm.horizontalAdvance(name) + 8)
            plate = QRectF(cell.left() + 4, cell.top() + 2, width,
                           min(16.0, cell.height() - 4))
            painter.fillRect(plate, QColor(palette["group_header"]))
            painter.setPen(QColor(palette["text"]))
            painter.drawText(
                plate.adjusted(4, 0, 0, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm.elidedText(name, Qt.TextElideMode.ElideRight,
                              int(max(1.0, width - 8))))
            if name != "Not in a hierarchy":
                self._hit(cell, "hierarchy", name, f"{name}: open this hierarchy")

    def _paint_icicle(self, painter, spec, palette, area) -> None:
        rows = [row for row in spec.series if float(row.get("value") or 0) > 0]
        if not rows:
            return self._paint_none(painter, spec, palette, area)
        fm = QFontMetrics(self.font())
        columns = max(int(row["column"]) for row in rows) + 1
        body = QRectF(area.left(), area.top(), area.width(),
                      area.height() - fm.height() - 4)
        width = body.width() / columns
        total = sum(float(row["value"]) for row in rows if int(row["column"]) == 0) or 1.0
        for column in range(columns):
            y = body.top()
            for row in (r for r in rows if int(r["column"]) == column):
                h = body.height() * float(row["value"]) / total
                cell = QRectF(body.left() + column * width + 1, y + 1,
                              max(1.0, width - 2), max(1.0, h - 2))
                cause = str(row.get("cause") or "other")
                painter.fillRect(cell, cause_colour(cause))
                painter.fillRect(cell, cause_brush(cause))
                share = format_value(float(row["value"]), "percent", 3)
                self._cell_text(painter, cell, cause, [str(row["label"]), share])
                self._hit(cell, str(spec.provenance.get("row_kind") or "element"),
                          str(row["label"]),
                          f"step {column + 1}: {row['label']} "
                          f"({CAUSE_LABELS.get(cause, cause).lower()}), {share} of downtime")
                y += h
            if (column in {int(r["column"]) for r in rows}
                    and not (columns == 1
                             and spec.provenance.get("single_column"))):
                painter.setPen(QColor(palette["muted"]))
                painter.drawText(QRectF(body.left() + column * width, body.bottom() + 4,
                                        width, fm.height()),
                                 Qt.AlignmentFlag.AlignHCenter,
                                 "first" if column == 0 else f"then (step {column + 1})")

    def _paint_scorecard(self, painter, spec, palette, area) -> None:  # noqa: C901
        from raptor.api.insight.charts import scorecard_badge
        rows = spec.series
        if not rows:
            return self._paint_none(painter, spec, palette, area)
        columns = 2 if len(rows) > 2 and area.width() >= 520 else 1
        gap = 8
        width = (area.width() - gap * (columns - 1)) / columns
        lines = (len(rows) + columns - 1) // columns
        height = min(150.0, (area.height() - gap * (lines - 1)) / max(1, lines))
        compact = height < 96
        for i, row in enumerate(rows):
            col, line = i % columns, i // columns
            rect = QRectF(area.left() + col * (width + gap),
                          area.top() + line * (height + gap), width, height)
            painter.fillRect(rect, QColor(palette["group_header"]))
            painter.setPen(QPen(QColor(palette["border"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            painter.setPen(QColor(palette["text"]))
            painter.drawText(rect.adjusted(8, 4, -8, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             str(row.get("name") or ""))
            value = float(row.get("value") or 0.0)
            big = QFont(self.font())
            big.setPointSizeF(big.pointSizeF() + 4)
            big.setBold(True)
            painter.setFont(big)
            painter.setPen(QColor(palette["accent"] if value else palette["muted"]))
            painter.drawText(rect.adjusted(8, 20, -8, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             format_value(value, "percent", 3))
            painter.setFont(self.font())
            sem = row.get("sem")
            detail = (f"± {200 * float(sem):.2g} pts" if _number(sem) else
                      "no spread to judge")
            painter.setPen(QColor(palette["muted"]))
            if not compact:
                painter.drawText(rect.adjusted(8, 43, -8, 0),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                 detail)
            if not compact:
                painter.drawText(rect.adjusted(8, 0, -8, -6),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                                 f"{int(row.get('members') or 0)} members · "
                                 f"{int(row.get('starters') or 0)} started outages")
            badge = scorecard_badge(row)
            if badge is not None:
                tone, text = badge
                painter.setPen(QColor(palette["result_green"] if tone == "good" else
                                      palette["result_red"] if tone == "bad" else
                                      palette["muted"]))
                bold = QFont(self.font())
                bold.setBold(True)
                painter.setFont(bold)
                badge_rect = rect.adjusted(8, 43 if compact else 60, -8, 0)
                painter.drawText(badge_rect,
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                 QFontMetrics(bold).elidedText(
                                     text, Qt.TextElideMode.ElideRight,
                                     int(badge_rect.width())))
                painter.setFont(self.font())
            if row.get("dots") and height >= 64:
                self._scorecard_strip(painter, palette, row, rect,
                                      float(spec.provenance.get("dot_max") or 0.0))
            cause = str(row.get("cause") or "")
            painter.setPen(QColor(palette["muted"]))
            if cause:
                painter.drawText(rect.adjusted(8, 4, -8, 0),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                                 CAUSE_LABELS.get(cause, cause))
            self._hit(rect, "hierarchy", str(row.get("name") or ""),
                      f"{row.get('name')}: {format_value(value, 'percent', 3)} of "
                      "system downtime")

    def _scorecard_strip(self, painter: QPainter, palette: dict[str, str],
                         row: dict, rect: QRectF, dot_max: float) -> None:
        """The tile's per-run quantile dots, on the scale every tile shares,
        with the mean as a tick; the scale's ends named beneath."""
        from raptor.api.insight.charts import scorecard_scale_text, scorecard_strip
        fm = QFontMetrics(self.font())
        left, right = rect.left() + rect.width() * 0.5, rect.right() - 12
        base = rect.bottom() - fm.height() - 4
        radius = 2.6
        painter.setPen(QPen(QColor(palette["border"]), 1))
        painter.drawLine(QPointF(left, base), QPointF(right, base))
        accent = QColor(palette["accent"])
        accent.setAlphaF(0.75)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        for (dx, level) in scorecard_strip(row["dots"], dot_max, right - left, radius):
            painter.drawEllipse(QPointF(left + dx, base - radius - 1 - level * 2 * radius),
                                radius, radius)
        if dot_max > 0:
            mx = left + float(row.get("value") or 0.0) / dot_max * (right - left)
            painter.setPen(QPen(QColor(palette["text"]), 1.4))
            painter.drawLine(QPointF(mx, base - 22), QPointF(mx, base + 3))
        painter.setPen(QColor(palette["muted"]))
        small = QFont(self.font())
        small.setPointSizeF(max(6.0, small.pointSizeF() - 1.5))
        painter.setFont(small)
        sfm = QFontMetrics(small)
        label = QRectF(left, base + 3, right - left, sfm.height())
        painter.drawText(label, Qt.AlignmentFlag.AlignLeft, "0")
        painter.drawText(label, Qt.AlignmentFlag.AlignRight,
                         scorecard_scale_text(dot_max))
        painter.drawText(label, Qt.AlignmentFlag.AlignHCenter, "each dot a run")
        painter.setFont(self.font())

    def _paint_delta(self, painter, spec, palette, area) -> None:
        rows = spec.series
        if not rows:
            return self._paint_none(painter, spec, palette, area)
        values = [float(row.get("value") or 0.0) for row in rows]
        if not any(abs(value) > 1e-12 for value in values):
            return self._paint_none(painter, spec, palette, area)
        bound = max(max(abs(v) for v in values), 1e-9)
        bound = nice_range(-bound, bound, 4)[1]
        labels = [str(row.get("name") or "") for row in rows]
        gutter = min(area.width() * 0.4,
                     max(60, max((QFontMetrics(self.font()).horizontalAdvance(name)
                                  for name in labels), default=0) + 10))
        plot = QRectF(area.left() + gutter, area.top(),
                      max(1.0, area.width() - gutter), area.height())
        fmt = "percent" if spec.provenance.get("format") == "percent" else ""
        frame = self._frame(painter, spec, palette, plot,
                            x_range=(-bound, bound),
                            y_range=(0.0, float(len(rows))), y_ticks=[],
                            x_fmt=fmt, x_label=spec.x_label,
                            y_label="hierarchy")
        box = frame.rect
        band = box.height() / len(rows)
        bar_h = max(3.0, min(16.0, band * 0.45))
        zero = frame.x(0.0)
        painter.setPen(QPen(QColor(palette["muted"]), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(zero, box.top()), QPointF(zero, box.bottom()))
        for i, row in enumerate(rows):
            value = float(row.get("value") or 0.0)
            y = box.top() + i * band + (band - bar_h) / 2
            x = frame.x(value)
            painter.fillRect(QRectF(min(zero, x), y, max(1.0, abs(x - zero)), bar_h),
                             QColor(palette["stop"] if value > 0 else
                                    palette["result_green"] if value < 0 else
                                    palette["muted"]))
            sem = row.get("sem")
            if _number(sem) and float(sem) > 0:
                lo = frame.x(value - 2 * float(sem))
                hi = frame.x(value + 2 * float(sem))
                mid = y + bar_h / 2
                painter.setPen(QPen(QColor(palette["text"]), 1.2))
                painter.drawLine(QPointF(lo, mid), QPointF(hi, mid))
                painter.drawLine(QPointF(lo, mid - 4), QPointF(lo, mid + 4))
                painter.drawLine(QPointF(hi, mid - 4), QPointF(hi, mid + 4))
            painter.setPen(QColor(palette["text"]))
            units = str(spec.provenance.get("units") or "")
            unit = " h" if units.lower().startswith("hour") else (
                f" {units}" if units else "")
            text = format_value(value, fmt, 3) + unit
            label_x = (min(x, zero) + 4 if value >= 0
                       else max(box.left() + 2, x - 80))
            painter.drawText(QRectF(label_x, y, 80, bar_h),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             text)
            painter.drawText(
                QRectF(area.left(), y, gutter - 6, bar_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                QFontMetrics(self.font()).elidedText(
                    str(row.get("name") or ""), Qt.TextElideMode.ElideMiddle,
                    int(max(1.0, gutter - 10))))
            self._hit(QRectF(box.left(), y, box.width(), bar_h), "hierarchy",
                      str(row.get("name") or ""),
                      f"{row.get('name')}: {100 * value:+.3g} points")

    def _paint_heatmap(self, painter, spec, palette, area) -> None:
        rows = spec.series
        columns = [str(item.get("key")) for item in
                   spec.provenance.get("columns") or []]
        if not rows or not columns:
            return self._paint_none(painter, spec, palette, area)
        labels = [str(item.get("label")) for item in
                  spec.provenance.get("columns") or []]
        fm = QFontMetrics(self.font())
        gutter = min(150, max((fm.horizontalAdvance(str(row.get("name") or ""))
                               for row in rows), default=70) + 12)
        left = area.left() + gutter
        top = area.top() + 28
        width = max(1.0, area.right() - left)
        height = max(1.0, area.bottom() - top - 8)
        col_w = width / len(columns)
        row_h = height / len(rows)
        largest = max((float((row.get("cells") or {}).get(c) or 0.0)
                       for row in rows for c in columns), default=0.0)
        for i, label in enumerate(labels):
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(QRectF(left + i * col_w, area.top(), col_w, 20),
                             Qt.AlignmentFlag.AlignHCenter,
                             fm.elidedText(label, Qt.TextElideMode.ElideRight,
                                           int(max(1.0, col_w - 4))))
        for i, row in enumerate(rows):
            y = top + i * row_h
            painter.setPen(QColor(palette["text"]))
            painter.drawText(
                QRectF(area.left(), y, gutter - 6, row_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                fm.elidedText(str(row.get("name") or ""),
                              Qt.TextElideMode.ElideMiddle,
                              int(max(1.0, gutter - 10))))
            for j, cause in enumerate(columns):
                value = float((row.get("cells") or {}).get(cause) or 0.0)
                share = value / largest if largest else 0.0
                colour = QColor(palette["surface"]).lighter()
                colour = QColor(
                    round(colour.red() + (QColor(palette["accent"]).red()
                                          - colour.red()) * share),
                    round(colour.green() + (QColor(palette["accent"]).green()
                                            - colour.green()) * share),
                    round(colour.blue() + (QColor(palette["accent"]).blue()
                                           - colour.blue()) * share))
                painter.fillRect(QRectF(left + j * col_w + 1, y + 1,
                                        col_w - 2, row_h - 2), colour)
                if col_w >= 38 and row_h >= 18:
                    from raptor.gui.ui.chart_theme import ink_on
                    painter.setPen(ink_on(colour))
                    painter.drawText(QRectF(left + j * col_w, y, col_w, row_h),
                                     Qt.AlignmentFlag.AlignCenter,
                                     f"{100 * value:.0f}%")
                self._hit(QRectF(left + j * col_w, y, col_w, row_h),
                          str(row.get("kind") or
                              spec.provenance.get("row_kind") or "hierarchy"),
                          str(row.get("name") or ""),
                          f"{row.get('name')} / {cause}: {100 * value:.3g} %")

    def _paint_sankey(self, painter, spec, palette, area) -> None:  # noqa: C901, PLR0915
        nodes = list(spec.provenance.get("nodes") or [])
        links = spec.series
        if not nodes or not links:
            return self._paint_none(painter, spec, palette, area)
        columns = sorted({int(node.get("column") or 0) for node in nodes})
        fm = painter.fontMetrics()
        lost_total = next((float(node.get("value") or 0.0) for node in nodes
                           if node.get("id") == "lost"), 0.0)

        def amount_of(raw: float, node: dict | None = None) -> str:
            text = (compact(raw) if abs(raw) >= 1e6
                    else f"{raw:,.0f}" if abs(raw) >= 1000 else f"{raw:,.4g}")
            ident = str((node or {}).get("id") or "")
            if (node is not None and lost_total > 0
                    and (ident.startswith("cause-")
                         or ident.startswith("hier-")
                         or ident == "unattributed")):
                text += f" ({pct(raw / lost_total)})"
            return text

        first_labels = [str(n.get("name") or "") for n in nodes
                        if int(n.get("column") or 0) == columns[0]]
        last_labels = [f"{n.get('name') or ''} · "
                       f"{amount_of(float(n.get('value') or 0), n)}"
                       for n in nodes
                       if int(n.get("column") or 0) == columns[-1]]
        left_pad = max(110.0, max((fm.horizontalAdvance(x) for x in first_labels),
                                  default=0) + 18.0)
        right_pad = max(300.0, max((fm.horizontalAdvance(x) for x in last_labels),
                                   default=0) + 60.0)
        left_pad = min(left_pad, area.width() * 0.28)
        right_pad = min(right_pad, area.width() * 0.55)
        span = max(1.0, area.width() - left_pad - right_pad)
        col_x = {col: area.left() + left_pad + span * i / max(1, len(columns) - 1)
                 for i, col in enumerate(columns)}
        totals = {col: sum(float(node.get("value") or 0.0) for node in nodes
                           if int(node.get("column") or 0) == col)
                  for col in columns}
        gap = 6.0
        placed: dict[str, tuple[float, float, float]] = {}
        for col in columns:
            members = [node for node in nodes if int(node.get("column") or 0) == col]
            total = totals[col] or 1.0
            available = max(1.0, area.height() - gap * max(0, len(members) - 1))
            y = area.top()
            for node in members:
                h = max(2.0, available * float(node.get("value") or 0.0) / total)
                placed[str(node["id"])] = (col_x[col], y, h)
                y += h + gap
        for link in links:
            source = placed.get(str(link.get("source")))
            target = placed.get(str(link.get("target")))
            if source is None or target is None:
                continue
            value = float(link.get("value") or 0.0)
            thickness = max(1.0, value / (totals[columns[0]] or 1.0)
                            * area.height() * 0.45)
            x0, y0, h0 = source
            x1, y1, h1 = target
            y0 += h0 / 2
            y1 += h1 / 2
            cause = str(link.get("cause") or "other")
            colour = QColor(cause_colour(cause) if link.get("cause")
                            else palette["muted"])
            colour.setAlpha(95)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            path = QPainterPath(QPointF(x0 + 14, y0 - thickness / 2))
            path.cubicTo(QPointF((x0 + x1) / 2, y0 - thickness / 2),
                         QPointF((x0 + x1) / 2, y1 - thickness / 2),
                         QPointF(x1, y1 - thickness / 2))
            path.lineTo(QPointF(x1, y1 + thickness / 2))
            path.cubicTo(QPointF((x0 + x1) / 2, y1 + thickness / 2),
                         QPointF((x0 + x1) / 2, y0 + thickness / 2),
                         QPointF(x0 + 14, y0 + thickness / 2))
            path.closeSubpath()
            painter.drawPath(path)
        for node in nodes:
            x, y, h = placed[str(node["id"])]
            painter.fillRect(QRectF(x, y, 14, h), QColor(palette["muted"]))
            kind = ("hierarchy" if str(node.get("id") or "").startswith("hier-")
                    else "note")
            self._hit(QRectF(x - 2, y, 18, h), kind,
                      str(node.get("name") or ""),
                      f"{node.get('name')}: {float(node.get('value') or 0):,.4g}")
            painter.setPen(QColor(palette["text"]))
            label = str(node.get("name") or "")
            if str(node.get("id") or "").startswith("cause-"):
                from raptor.api.insight.attribution import CAUSE_LABELS
                label = CAUSE_LABELS.get(str(node["id"])[6:], label)
            raw = float(node.get('value') or 0)
            amount = amount_of(raw, node)
            if int(node.get("column") or 0) == 0:
                painter.drawText(QRectF(x - left_pad + 2, y, left_pad - 8, h),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                 fm.elidedText(label, Qt.TextElideMode.ElideRight,
                                               int(left_pad - 10)))
            elif int(node.get("column") or 0) == max(columns):
                width = max(1.0, right_pad - 24)
                if h >= fm.height() * 2 + 4:
                    painter.drawText(
                        QRectF(x + 18, y + h / 2 - fm.height(), width,
                               fm.height()),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        fm.elidedText(label, Qt.TextElideMode.ElideRight,
                                      int(max(1.0, width))))
                    painter.drawText(
                        QRectF(x + 18, y + h / 2, width, fm.height()),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        fm.elidedText(amount, Qt.TextElideMode.ElideRight,
                                      int(max(1.0, width))))
                else:
                    text = f"{label} · {amount}"
                    # a sliver node at the foot of the column centred its
                    # one-line label half below the chart: keep it inside
                    line = float(fm.height())
                    top = min(max(y + h / 2 - line / 2, area.top()),
                              area.bottom() - line)
                    painter.drawText(
                        QRectF(x + 18, top, width, line),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        fm.elidedText(text, Qt.TextElideMode.ElideRight,
                                      int(max(1.0, right_pad - 28))))
            else:
                painter.drawText(QRectF(x + 18, y, 220, h),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 f"{label} · {amount}")

    def _paint_sunburst(self, painter, spec, palette, area) -> None:  # noqa: PLR0915
        from raptor.api.insight.hierarchy import SERIES_COLOURS, sunburst_layout
        nodes = sunburst_layout(spec.series)
        if not nodes:
            return self._paint_none(painter, spec, palette, area)
        cx = (area.left() + area.right()) / 2
        cy = (area.top() + area.bottom()) / 2
        radius = min(area.width(), area.height()) / 2 - 10
        if radius <= 20:
            return self._paint_none(painter, spec, palette, area)
        import math
        max_depth = max(int(node.get("depth") or 0) for node in nodes)
        inner = radius * 0.28
        ring = (radius - inner) / (max_depth + 1)
        for node in nodes:
            depth = int(node.get("depth") or 0)
            start = float(node.get("start") or 0.0)
            end = float(node.get("end") or 0.0)
            root = int(node.get("root_index") or 0)
            colour = QColor(SERIES_COLOURS[root % len(SERIES_COLOURS)])
            colour = colour.lighter(100 + min(58, 14 * depth))
            self._ring(painter, cx, cy, inner + depth * ring + 1.5,
                       inner + (depth + 1) * ring - 1.5, start, end, colour)
            angle = (start + end) / 2
            mid = inner + (depth + 0.5) * ring
            name = str(node.get("name") or "").split(" > ")[-1]
            if name != "Not in a hierarchy":
                self._hit(QRectF(cx + mid * math.cos(angle) - 10,
                                 cy + mid * math.sin(angle) - 10, 20, 20),
                          "hierarchy", name,
                          f"{name}: {float(node.get('value') or 0):.4g} of its parent")
        placed: list[QRectF] = []
        fm = QFontMetrics(self.font())
        for node in sorted(nodes, key=lambda item: int(item.get("depth") or 0)):
            depth = int(node.get("depth") or 0)
            start = float(node.get("start") or 0.0)
            end = float(node.get("end") or 0.0)
            if depth == 0 or end - start < 0.30 or ring < 10:
                continue
            label = str(node.get("name") or "").split(" > ")[-1]
            angle = (start + end) / 2
            mid = inner + (depth + (0.85 if depth == 0 else 0.5)) * ring
            width = fm.horizontalAdvance(label) + 8
            if (end - start) * mid < width * 1.2:
                continue
            rect = QRectF(cx + mid * math.cos(angle) - width / 2,
                          cy + mid * math.sin(angle) - 8, width, 15)
            if (not area.contains(rect) or
                    any(rect.intersects(other) for other in placed)):
                continue
            placed.append(rect)
            old = painter.font()
            font = QFont(old)
            font.setPointSizeF(max(7.0, old.pointSizeF() - 3))
            painter.setFont(font)
            from raptor.gui.ui.chart_theme import ink_on
            painter.setPen(ink_on(colour))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            painter.setFont(old)
        painter.setPen(QColor(palette["text"]))
        centre = "System"
        if spec.scope and str(spec.scope.get("kind") or "") != "system":
            centre = str(spec.scope.get("name") or "System")
        painter.drawText(QRectF(cx - inner * 0.9, cy - 8, inner * 1.8, 16),
                         Qt.AlignmentFlag.AlignCenter, centre)

    @staticmethod
    def _ring(painter, cx, cy, inner, outer, start, end, colour) -> None:
        import math
        steps = 16
        points = []
        for k in range(steps + 1):
            angle = start + (end - start) * k / steps
            points.append(QPointF(cx + outer * math.cos(angle),
                                  cy + outer * math.sin(angle)))
        for k in range(steps + 1):
            angle = end - (end - start) * k / steps
            points.append(QPointF(cx + inner * math.cos(angle),
                                  cy + inner * math.sin(angle)))
        painter.setPen(QPen(QColor("#ffffff"), 0.8))
        painter.setBrush(colour)
        painter.drawPolygon(QPolygonF(points))

    def _paint_small_multiples(self, painter, spec, palette, area) -> None:
        facets = spec.facets
        if not facets:
            return self._paint_none(painter, spec, palette, area)
        columns = 1 if len(facets) == 1 else 2 if len(facets) <= 4 else 3
        lines = (len(facets) + columns - 1) // columns
        gap = 10
        width = (area.width() - gap * (columns - 1)) / columns
        height = (area.height() - gap * (lines - 1)) / max(1, lines)
        original = self._spec
        try:
            for i, facet in enumerate(facets):
                col, line = i % columns, i // columns
                x = area.left() + col * (width + gap)
                y = area.top() + line * (height + gap)
                rect = QRectF(x, y, width, height)
                painter.fillRect(rect, QColor(palette["surface"]))
                painter.setPen(QPen(QColor(palette["border"]), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
                painter.setPen(QColor(palette["text"]))
                painter.drawText(rect.adjusted(6, 2, -6, 0),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                 facet.title)
                self._spec = facet
                draw = getattr(self, f"_paint_{facet.kind}", self._paint_none)
                draw(painter, facet, palette,
                     QRectF(x + 4, y + 18, width - 8, height - 22))
        finally:
            self._spec = original
            self._probe = None

    def _paint_ribbon(self, painter, spec, palette, area) -> None:  # noqa: C901
        rows = [row for row in spec.series if row.get("span")]
        if not rows:
            return self._paint_none(painter, spec, palette, area)
        start = min(float(row["start"]) for row in rows)
        end = max(float(row["end"]) for row in rows)
        whole = (start, end)
        if self._zoom is not None:
            start, end = max(start, self._zoom[0]), min(end, self._zoom[1])
            if end <= start:
                start, end = whole
        zoomed = (start, end) != whole
        x_label = (f"{spec.x_label} · drag across or Ctrl+wheel to zoom in"
                   + (", double-click for the whole run" if zoomed else ""))
        names = [str(row.get("name") or "") for row in rows if row.get("name")]
        fm = QFontMetrics(self.font())
        gutter = min(180, max([fm.horizontalAdvance(name) for name in names] + [0]) + 18) \
            if names else 0
        plot = QRectF(area.left() + gutter, area.top(),
                      max(1.0, area.width() - gutter), area.height())
        frame = self._frame(painter, spec, palette, plot,
                            x_range=(start, end), y_range=(0.0, float(len(rows))),
                            y_ticks=[], y_label=_ribbon_rows(spec, len(rows)),
                            x_label=x_label)
        self._zoom_frame = frame
        r = frame.rect
        height = r.height() / len(rows)
        up, down = QColor(palette["result_green"]), QColor(palette["result_red"])
        for i, row in enumerate(rows):
            y = r.top() + i * height
            painter.fillRect(QRectF(r.left(), y, r.width(), max(1.0, height - 0.4)), up)
            for a, b in row.get("red") or []:
                a, b = max(float(a), start), min(float(b), end)
                if b < a:
                    continue                      # outside the zoomed window
                x0, x1 = frame.x(a), frame.x(b)
                painter.fillRect(QRectF(x0, y, max(1.0, x1 - x0),
                                        max(1.0, height - 0.4)), down)
            if row.get("name"):
                painter.setPen(QColor(palette["text"]))
                painter.drawText(
                    QRectF(area.left(), y, gutter - 6, max(1.0, height)),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    fm.elidedText(str(row.get("label") or row["name"]),
                              Qt.TextElideMode.ElideMiddle,
                              int(max(1.0, gutter - 10))))
            # the reading cursor says this and more; no second tooltip
            self._hit(QRectF(r.left(), y, r.width(), max(1.0, height)), "run",
                      f"run {row.get('run', '')}", "")
        if self._drag_from is not None and self._cursor is not None:
            band = QColor(palette["accent"])
            band.setAlpha(60)
            x0 = min(max(self._drag_from.x(), r.left()), r.right())
            x1 = min(max(self._cursor.x(), r.left()), r.right())
            painter.fillRect(QRectF(min(x0, x1), r.top(), abs(x1 - x0), r.height()), band)

        def read(px: float) -> list[tuple[QPointF, str]]:
            cursor = self._cursor
            if cursor is None:
                return []
            index = min(len(rows) - 1, max(0, int((cursor.y() - r.top()) / height)))
            return [(QPointF(px, r.top() + (index + 0.5) * height),
                     ribbon_reading(rows, index, frame.value_at_x(px)))]

        self._probe = (frame, read)

    # The reading cursor.

    def _paint_probe(self, painter: QPainter, palette: dict[str, str]) -> None:
        if self._probe is None or self._cursor is None:
            return
        frame, read = self._probe
        r = frame.rect
        if not r.contains(self._cursor):
            return
        marks = read(self._cursor.x())
        if not marks:
            return
        fm = QFontMetrics(self.font())
        guide = QColor(palette["text"])
        guide.setAlpha(150)
        for point, text in marks:
            painter.setPen(QPen(guide, 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(point.x(), r.top()), QPointF(point.x(), r.bottom()))
            painter.drawLine(QPointF(r.left(), point.y()), QPointF(r.right(), point.y()))
            painter.setPen(QPen(QColor(palette["surface"]), 2))
            painter.setBrush(QColor(palette["accent"]))
            painter.drawEllipse(point, 5, 5)
            rows = text.split("\n")
            w = max(fm.horizontalAdvance(row) for row in rows) + 14
            h = fm.height() * len(rows) + 8
            left = point.x() + 10 if point.x() + 10 + w <= r.right() else point.x() - 10 - w
            top = point.y() - h - 8 if point.y() - h - 8 >= r.top() else point.y() + 8
            box = QRectF(max(r.left(), left), top, w, h)
            painter.setPen(QPen(QColor(palette["border"]), 1))
            painter.setBrush(QColor(palette["surface"]))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor(palette["text"]))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    # -- interaction ----------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt name
        # the hover reading prints money too
        with currency(self._currency()):
            self._mouse_move(event)

    def _mouse_move(self, event) -> None:
        point = event.position()
        if self._probe is not None:
            self._cursor = point
            self.update()
        for (rect, _kind, _name), tip in zip(self._hits, self._tips, strict=True):
            if rect.contains(point):
                QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                break
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt name
        self._cursor = None
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt name
        """Ctrl+wheel zooms a time chart around the pointer."""
        if (self._spec is not None and self._spec.kind in ("ribbon", "timeline")
                and self._zoom_frame is not None
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            frame = self._zoom_frame
            anchor = frame.value_at_x(event.position().x())
            low, high = frame.x_low, frame.x_high
            factor = 0.8 if event.angleDelta().y() > 0 else 1.25
            new_low = anchor - (anchor - low) * factor
            new_high = anchor + (high - anchor) * factor
            if new_high - new_low >= 1e-9:
                self.zoom_to(new_low, new_high)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt name
        if (event.button() == Qt.MouseButton.LeftButton and self._spec is not None
                and self._spec.kind in ("ribbon", "timeline")
                and self._zoom_frame is not None
                and self._zoom_frame.rect.contains(event.position())):
            self._drag_from = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, kind, name in self._hits:
                if rect.contains(event.position()) and name and kind in (
                        "element", "cause", "hierarchy"):
                    self.locate_requested.emit(kind, name)
                    break
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt name
        start, frame = self._drag_from, self._zoom_frame
        self._drag_from = None
        if start is not None and frame is not None \
                and abs(event.position().x() - start.x()) >= 6:
            a = frame.value_at_x(start.x())
            b = frame.value_at_x(event.position().x())
            self.zoom_to(min(a, b), max(a, b))
        else:
            self.update()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt name
        if (self._spec is not None and self._spec.kind in ("ribbon", "timeline")
                and self._zoom):
            self.zoom_to(None)
        super().mouseDoubleClickEvent(event)

    def _menu(self, position) -> None:
        if self._spec is None:
            return
        menu = QMenu(self)
        point = QPointF(position)
        details = next((row for rect, row in self._detail_hits
                        if rect.contains(point)), None)
        if details is not None and details.get("items"):
            count = len(details["items"])
            menu.addAction(
                f"Show {count} {'item' if count == 1 else 'items'} in "
                f"“{details.get('name')}”…",
                lambda row=details: self.show_details(row))
            menu.addSeparator()
        if self._spec.kind in ("ribbon", "timeline") and self._zoom is not None:
            menu.addAction("Show the whole run", lambda: self.zoom_to(None))
        menu.addAction("Copy image", self.copy_image)
        menu.addAction("Save PNG…", self._ask_png)
        menu.addAction("Save SVG…", self._ask_svg)
        menu.addAction("Save PDF…", self._ask_pdf)
        menu.addAction("Save CSV…", self._ask_csv)
        menu.exec(self.mapToGlobal(position))

    def show_details(self, row: dict) -> None:
        """A scrollable, sortable table of the items a gathered cell holds."""
        with currency(self._currency()):
            self._show_details(row)

    def _show_details(self, row: dict) -> None:
        from PySide6.QtWidgets import (
            QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
            QTableWidget, QTableWidgetItem, QVBoxLayout,
        )
        items = [dict(item) for item in row.get("items") or []]
        if not items:
            return

        class _NumberItem(QTableWidgetItem):
            """Sort by the stored number, not the formatted text."""
            def __lt__(self, other):  # noqa: D105 - Qt protocol
                left = self.data(Qt.ItemDataRole.UserRole)
                right = other.data(Qt.ItemDataRole.UserRole)
                if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                    return float(left) < float(right)
                return super().__lt__(other)

        spec = self._spec
        fmt = str((spec.provenance if spec else {}).get("format") or "")
        labels = {**CAUSE_LABELS,
                  **((spec.provenance.get("group_labels") if spec else None) or {})}
        dialog = QDialog(self)
        dialog.setWindowTitle(str(row.get("name") or "Details"))
        dialog.resize(620, 460)
        layout = QVBoxLayout(dialog)
        total = sum(float(item.get("value") or 0.0) for item in items)
        title = QLabel(
            f"{len(items)} items in “{row.get('name')}”, {format_value(total, fmt, 4)}"
            " together. Click a heading to sort.")
        title.setWordWrap(True)
        layout.addWidget(title)
        table = QTableWidget(len(items), 4)
        table.setHorizontalHeaderLabels(
            ["Name", "Value", "Share", "Cause"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSortingEnabled(True)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for i, item in enumerate(items):
            name = QTableWidgetItem(str(item.get("name") or ""))
            value = float(item.get("value") or 0.0)
            value_item = _NumberItem(format_value(value, fmt, 5))
            value_item.setData(Qt.ItemDataRole.UserRole, value)
            share = float(item["share"]) if _number(item.get("share")) else None
            share_item = _NumberItem(
                format_value(share, "percent", 3) if share is not None else "")
            share_item.setData(Qt.ItemDataRole.UserRole, share or 0.0)
            cause = str(item.get("cause") or "")
            cause_item = QTableWidgetItem(labels.get(cause, cause))
            table.setItem(i, 0, name)
            table.setItem(i, 1, value_item)
            table.setItem(i, 2, share_item)
            table.setItem(i, 3, cause_item)
        table.setSortingEnabled(True)
        table.sortItems(1, Qt.SortOrder.DescendingOrder)
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        from raptor.gui import i18n
        i18n.exec_translated(dialog)

    def _ask_png(self) -> None:
        if path := ask_chart_path(self, "Save chart", "", "PNG image", ".png"):
            self.save_png(path)

    def _ask_svg(self) -> None:
        if path := ask_chart_path(self, "Save chart", "", "SVG image", ".svg"):
            self.save_svg(path)

    def _ask_pdf(self) -> None:
        if path := ask_chart_path(self, "Save chart", "", "PDF document", ".pdf"):
            self.save_pdf(path)

    def _ask_csv(self) -> None:
        if path := ask_chart_path(self, "Save chart data", "", "CSV", ".csv"):
            self.save_csv(path)


def ask_chart_path(parent, caption: str, stem: str, label: str, ext: str) -> str:
    """Where to save a chart, always with `ext` on the end ("" if cancelled).

    The save dialog returns the name as typed, and "outage" saved as PNG
    wrote nothing at all: the image format comes from the extension. A
    name without it gets it added; as the dialog did not check that name,
    replacing a file of that name is asked here."""
    import os
    from raptor.gui.ui.filedialogs import getSaveFileName
    path, _ = getSaveFileName(parent, caption, stem, f"{label} (*{ext})")
    if not path:
        return ""
    if os.path.splitext(path)[1].lower() != ext:
        path += ext
        if os.path.exists(path):
            from raptor.gui.ui import dialogs
            if not dialogs.question_box(
                    parent, caption, f"{os.path.basename(path)} already exists. Replace it?",
                    yes_text="Replace", no_text="Cancel"):
                return ""
    return path


def ribbon_reading(rows: Sequence[dict], index: int, t: float) -> str:
    """U17: what the run-to-run chart says at time `t` on row `index`:
    the run, whether it is up or in an outage (with its start, end and
    length), and how many of all the runs are down at that time."""
    row = rows[index]
    outage = next(((float(a), float(b)) for a, b in row.get("red") or []
                   if float(a) <= t <= float(b)), None)
    down = sum(1 for other in rows
               if any(float(a) <= t <= float(b) for a, b in other.get("red") or []))
    span = float(row.get("span") or 0)
    up = (f" ({100 * (1 - float(row.get('downtime') or 0) / span):.4g} % up)"
          if span > 0 else "")
    hierarchy_row = "run" not in row and bool(row.get("name"))
    if hierarchy_row:
        head = f"{row['name']} at {_short(t)}"
        state = (f"down from {_short(outage[0])} to {_short(outage[1])}, "
                 f"for {_short(outage[1] - outage[0])}" if outage else
                 "no attributed downtime")
        return (f"{head}: {state}\n{down} of {len(rows)} hierarchies down "
                "at this time")
    head = f"run {row.get('run', '')}{up} at {_short(t)}"
    state = (f"down from {_short(outage[0])} to {_short(outage[1])}, "
             f"for {_short(outage[1] - outage[0])}" if outage else "up")
    return f"{head}: {state}\n{down} of {len(rows)} runs down at this time"


_TONES = {"up": "result_green", "degraded": "result_yellow", "down": "result_red",
          "accent": "accent", "muted": "muted", "ink": "text"}




def _tone(palette: dict[str, str], layer: dict) -> QColor:
    """A timeline layer's colour: an explicit colour or its theme tone."""
    colour = QColor(str(layer.get("colour") or
                        palette[_TONES.get(str(layer.get("tone")), "accent")]))
    if layer.get("role") == "area":
        alpha = layer.get("alpha")
        alpha = float(alpha) if isinstance(alpha, (int, float)) else 0.85
        if QColor(palette["surface"]).lightnessF() < 0.5 and alpha < 0.5:
            # a pale band over a dark surface all but vanished (0.14 of the
            # accent on near-black): the same steps, stronger
            alpha = min(0.6, alpha * 1.8)
        colour.setAlphaF(alpha)
    return colour


def _layer_pen(palette: dict[str, str], layer: dict) -> QPen:
    pen = QPen(_tone(palette, layer), float(layer.get("width") or 1.5))
    if layer.get("dash"):
        pen.setStyle(Qt.PenStyle.DashLine)
    return pen


def _at(ts: Sequence[float], values: Sequence[float], t: float) -> float:
    """A layer's value at time `t`, joining its corner points."""
    from raptor.api.series import _interp
    return _interp(list(ts), [float(v) for v in values], t)


def _timeline_reader(layers: list[dict], fmt: str, units: str, frame: _Frame,
                     pt: Callable[[float, float], QPointF]
                     ) -> Callable[[float], list[tuple[QPointF, str]]]:
    """The cursor of a chart over time: every layer's value at one moment."""
    def shown(value: float) -> str:
        return format_value(value, fmt, 5) if fmt == "percent" else f"{value:,.4g}"

    def read(px: float) -> list[tuple[QPointF, str]]:
        t = frame.value_at_x(px)
        lines = [f"at {_short(t)} {units}".rstrip()]
        anchor = None
        for layer in layers:
            ts = [float(x) for x in layer["t"]]
            how = layer.get("read", "span")
            if how == "none":
                continue
            if layer.get("role") != "area":
                value = _at(ts, layer["y"], t)
                anchor = anchor or pt(t, value)
                text = shown(value)
            elif how == "range":
                text = f"{shown(_at(ts, layer['lo'], t))} to {shown(_at(ts, layer['hi'], t))}"
            else:
                text = shown(_at(ts, layer["hi"], t) - _at(ts, layer["lo"], t))
            lines.append(f"{layer.get('name', '')}: {text}")
        return [(anchor or QPointF(px, frame.rect.center().y()), "\n".join(lines))]

    return read


def _ribbon_rows(spec: ChartSpec, shown: int) -> str:
    """'200 of 205 runs, best at the top' when the ribbon is a sample."""
    if any(row.get("name") for row in spec.series):
        return str(spec.y_label or "hierarchies")
    total = spec.provenance.get("runs")
    if isinstance(total, int) and total > shown:
        return f"{shown} of {total:,} runs, best at the top"
    return f"{shown} run{'s' if shown != 1 else ''}, best at the top"


def _points(fraction: float) -> str:
    """A loss in percentage points with its leading digits: 0.0054, 0.000081."""
    value = 100 * abs(fraction)
    if value == 0:
        return "0"
    if value >= 1e-3:
        # three figures, as the caption: "-25 pts" sat beside "24.7 points"
        return f"{value:.3g}"
    places = max(0, min(9, 1 - math.floor(math.log10(value))))
    return f"{value:.{places}f}"


def _short(value: float) -> str:
    """A compact number for a log axis or a reading: 0.5, 12, 1.2k."""
    size = abs(value)
    if size >= 1e4:
        return compact(value)
    if size >= 100:
        return f"{value:,.0f}"
    return f"{value:.3g}"


def _wrap(text: str, fm: QFontMetrics, width: int, lines: int) -> list[str]:
    words, out, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if fm.horizontalAdvance(trial) <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = fm.elidedText(out[-1] + " …", Qt.TextElideMode.ElideRight, width)
    return [fm.elidedText(item, Qt.TextElideMode.ElideRight, width) for item in out]

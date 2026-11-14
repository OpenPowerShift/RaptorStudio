"""ChartSpec -> QPainter, with hover, locate, copy and export (WP-2.5).

One widget paints every chart the Qt-free `raptor.api.insight.charts` module
produces; it never computes a value.  A mark carries a name when clicking it
should select the element in the canvas, and a tooltip with the value and
its interval.
"""
from __future__ import annotations

import csv

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMenu, QToolTip, QWidget,
)

from raptor.api.insight.charts import ChartSpec
from raptor.gui.resources.design_tokens import active_colour
from raptor.gui.ui.chart_theme import cause_brush, cause_colour

PAD_LEFT = 64
PAD_RIGHT = 24
PAD_TOP = 52
PAD_BOTTOM = 62


def _fmt(value: float) -> str:
    return f"{value:,.6g}"


def _scale(value: float, low: float, high: float, a: float, b: float) -> float:
    if high <= low:
        return (a + b) / 2
    return a + (value - low) / (high - low) * (b - a)


def _value_text(value: float, sem: float | None = None) -> str:
    text = _fmt(value)
    if isinstance(sem, (int, float)) and sem:
        text += f" ± {_fmt(sem)}"
    return text


class ChartWidget(QWidget):
    """Paints one `ChartSpec`; click a named mark to locate the element."""

    locate_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spec: ChartSpec | None = None
        self._hits: list[tuple[QRectF, str, str]] = []
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    # -- public ---------------------------------------------------------

    def spec(self) -> ChartSpec | None:
        return self._spec

    def set_spec(self, spec: ChartSpec | None) -> None:
        self._spec = spec
        self._hits = []
        self.update()

    def save_svg(self, path: str) -> None:
        from raptor.render.charts_svg import write
        if self._spec is not None:
            write(self._spec, path)

    def save_png(self, path: str) -> None:
        image = self.grab().toImage()
        image.save(path)

    def save_csv(self, path: str) -> None:
        spec = self._spec
        if spec is None:
            return
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["series", "name", "x", "y", "value", "note"])
            for series in spec.series:
                rows = series.get("dots") or series.get("points") or [series]
                for row in rows:
                    writer.writerow([
                        series.get("name", ""), row.get("name", ""),
                        row.get("x", row.get("n", "")),
                        row.get("y", ""), row.get("value", ""),
                        row.get("lo", ""), ])

    def copy_image(self) -> None:
        QApplication.clipboard().setImage(self.grab().toImage())

    # -- painting -------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt name
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
        painter.setPen(QColor(palette["text"]))
        title_font = QFont(self.font())
        title_font.setPointSizeF(title_font.pointSizeF() + 2)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(QRectF(0, 8, self.width(), 24),
                         Qt.AlignmentFlag.AlignHCenter, spec.title)
        painter.setFont(self.font())
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(QRectF(0, 28, self.width(), 18),
                         Qt.AlignmentFlag.AlignHCenter, spec.subtitle)
        self._hits = []
        painter.setPen(QColor(palette["text"]))
        draw = getattr(self, f"_paint_{spec.kind}", self._paint_none)
        draw(painter, spec, palette)
        if spec.caption:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(QRectF(PAD_LEFT, self.height() - 24,
                                    self.width() - PAD_LEFT - PAD_RIGHT, 20),
                             Qt.AlignmentFlag.AlignLeft, spec.caption)
        painter.end()

    def _rect(self) -> QRectF:
        return QRectF(PAD_LEFT, PAD_TOP, max(1, self.width() - PAD_LEFT - PAD_RIGHT),
                      max(1, self.height() - PAD_TOP - PAD_BOTTOM))

    def _axis(self, painter: QPainter, spec: ChartSpec, palette: dict[str, str],
              x_low: float, x_high: float, y_low: float, y_high: float) -> None:
        r = self._rect()
        painter.setPen(QPen(QColor(palette["border"]), 1))
        painter.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.left(), r.bottom()))
        painter.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 18),
                         Qt.AlignmentFlag.AlignHCenter, spec.x_label)
        painter.save()
        painter.translate(16, r.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-r.height() / 2, -10, r.height(), 18),
                         Qt.AlignmentFlag.AlignCenter, spec.y_label)
        painter.restore()
        painter.drawText(QRectF(r.left() - 58, r.bottom() - 10, 52, 18),
                         Qt.AlignmentFlag.AlignRight, _fmt(y_low))
        painter.drawText(QRectF(r.left() - 58, r.top() - 8, 52, 18),
                         Qt.AlignmentFlag.AlignRight, _fmt(y_high))
        painter.drawText(QRectF(r.left(), r.bottom() + 20, 60, 18),
                         Qt.AlignmentFlag.AlignLeft, _fmt(x_low))
        painter.drawText(QRectF(r.right() - 60, r.bottom() + 20, 60, 18),
                         Qt.AlignmentFlag.AlignRight, _fmt(x_high))

    def _paint_none(self, painter, spec, palette) -> None:  # noqa: ARG002
        painter.drawText(self._rect(), Qt.AlignmentFlag.AlignCenter,
                         "This chart cannot be drawn from the data.")

    def _paint_dot_plot(self, painter, spec, palette) -> None:
        dots = [d for d in spec.series[0].get("dots", [])
                if isinstance(d.get("value"), (int, float))]
        if not dots:
            return self._paint_none(painter, spec, palette)
        values = [float(d["value"]) for d in dots]
        target = next((m["value"] for m in spec.marks
                       if m.get("kind") == "target" and m.get("value") is not None),
                      None)
        low, high = min(values), max(values)
        if target is not None:
            low, high = min(low, target), max(high, target)
        self._axis(painter, spec, palette, low, high, 0, 1)
        r = self._rect()
        mid = r.center().y()
        painter.setPen(QPen(QColor(palette["border"]), 1))
        painter.drawLine(QPointF(r.left(), mid), QPointF(r.right(), mid))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2F6FB7"))
        for _dot, value in zip(dots, values, strict=True):
            x = _scale(value, low, high, r.left(), r.right())
            painter.drawEllipse(QPointF(x, mid), 3.2, 3.2)
        if target is not None:
            x = _scale(float(target), low, high, r.left(), r.right())
            painter.setPen(QPen(QColor("#C4472F"), 2, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            painter.drawText(QRectF(x - 60, r.top() - 20, 120, 18),
                             Qt.AlignmentFlag.AlignCenter,
                             f"target {_fmt(float(target))}")

    def _paint_ecdf(self, painter, spec, palette) -> None:
        points = spec.series[0].get("points") or []
        if not points:
            return self._paint_none(painter, spec, palette)
        xs = [float(p["x"]) for p in points]
        ys = [float(p["y"]) for p in points]
        self._axis(painter, spec, palette, min(xs), max(xs), 0, 1)
        r = self._rect()
        poly = QPolygonF([QPointF(_scale(x, min(xs), max(xs), r.left(), r.right()),
                                  _scale(y, 0, 1, r.bottom(), r.top()))
                          for x, y in zip(xs, ys, strict=True)])
        painter.setPen(QPen(QColor("#2F6FB7"), 2))
        painter.drawPolyline(poly)

    def _paint_pareto(self, painter, spec, palette) -> None:
        rows = spec.series
        if not rows:
            return self._paint_none(painter, spec, palette)
        r = self._rect()
        peak = max(float(row["value"]) for row in rows) or 1.0
        height = r.height() / len(rows)
        for i, row in enumerate(rows):
            y = r.top() + i * height
            width = r.width() * float(row["value"]) / peak
            painter.fillRect(QRectF(r.left(), y + 3, width, max(1, height - 6)),
                             QColor("#2F6FB7").lighter(160))
            cursor = r.left()
            for cause, share in (row.get("causes") or {}).items():
                part = width * float(share)
                painter.fillRect(QRectF(cursor, y + 3, part,
                                        max(1, height - 6)),
                                 cause_colour(cause))
                painter.fillRect(QRectF(cursor, y + 3, part,
                                        max(1, height - 6)),
                                 cause_brush(cause))
                cursor += part
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(r.left() + 4, y, max(1, width - 8), height),
                             Qt.AlignmentFlag.AlignVCenter, str(row["name"]))
            painter.drawText(QRectF(r.left() + width + 6, y, 80, height),
                             Qt.AlignmentFlag.AlignVCenter,
                             f"{100 * float(row['value']):.3g} %")
            self._hits.append((QRectF(r.left(), y, max(8.0, width), height),
                               "element", str(row["name"])))

    def _paint_waterfall(self, painter, spec, palette) -> None:
        steps = spec.series
        if not steps:
            return self._paint_none(painter, spec, palette)
        r = self._rect()
        width = r.width() / len(steps)
        for i, step in enumerate(steps):
            x = r.left() + i * width
            y0 = _scale(float(step["from"]), 0, 1, r.bottom(), r.top())
            y1 = _scale(float(step["to"]), 0, 1, r.bottom(), r.top())
            colour = cause_colour(str(step.get("cause")))
            painter.fillRect(QRectF(x + 2, min(y0, y1),
                                    max(1, width - 4), max(1, abs(y0 - y1))),
                             colour)
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(x, r.bottom() + 4, width, 18),
                             Qt.AlignmentFlag.AlignHCenter, str(step["name"]))
            painter.drawText(QRectF(x, min(y0, y1) - 18, width, 18),
                             Qt.AlignmentFlag.AlignHCenter,
                             f"-{100 * float(step['value']):.3g}")
            self._hits.append((QRectF(x, min(y0, y1), width,
                                      max(4.0, abs(y0 - y1))),
                               "cause", str(step.get("cause", ""))))

    def _paint_ladder(self, painter, spec, palette) -> None:
        rows = [row for row in spec.series if row.get("value") is not None]
        if not rows:
            return self._paint_none(painter, spec, palette)
        r = self._rect()
        height = r.height() / (len(rows) + 1)
        for i, row in enumerate(rows):
            y = r.top() + i * height
            width = r.width() * max(0.0, min(1.0, float(row["value"])))
            painter.fillRect(QRectF(r.left(), y, r.width(), height * 0.6),
                             QColor(palette["group_header"]))
            painter.fillRect(QRectF(r.left(), y, width, height * 0.6),
                             QColor("#22c55e"))
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(r.left() + 4, y, r.width() - 8,
                                    height * 0.6),
                             Qt.AlignmentFlag.AlignVCenter, str(row["name"]))
            painter.drawText(QRectF(r.right() - 120, y, 120, height * 0.6),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{100 * float(row['value']):.4g} %")

    def _paint_histogram(self, painter, spec, palette) -> None:
        bins = spec.series
        if not bins:
            return self._paint_none(painter, spec, palette)
        r = self._rect()
        peak = max(float(b["count"]) for b in bins) or 1.0
        width = r.width() / len(bins)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#7A5AA6"))
        for i, row in enumerate(bins):
            height = r.height() * float(row["count"]) / peak
            painter.drawRect(QRectF(r.left() + i * width + 1,
                                    r.bottom() - height,
                                    max(1, width - 2), height))
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 18),
                         Qt.AlignmentFlag.AlignLeft,
                         f"log scale from {_fmt(float(bins[0]['lo']))} "
                         f"to {_fmt(float(bins[-1]['hi']))}")

    def _paint_bubble(self, painter, spec, palette) -> None:
        points = [p for p in spec.series
                  if isinstance(p.get("x"), (int, float))
                  and isinstance(p.get("y"), (int, float))]
        if not points:
            return self._paint_none(painter, spec, palette)
        xs = [float(p["x"]) for p in points]
        ys = [float(p["y"]) for p in points]
        x_high, y_high = max(xs) or 1.0, max(ys) or 1.0
        area_peak = max(float(p.get("area") or 0) for p in points) or 1.0
        self._axis(painter, spec, palette, 0, x_high, 0, y_high)
        r = self._rect()
        painter.setPen(QPen(QColor("#8a5c00"), 1))
        painter.setBrush(QColor(201, 138, 11, 140))
        for point in points:
            x = _scale(float(point["x"]), 0, x_high, r.left(), r.right())
            y = _scale(float(point["y"]), 0, y_high, r.bottom(), r.top())
            radius = 5 + 22 * (float(point.get("area") or 0) / area_peak) ** 0.5
            painter.drawEllipse(QPointF(x, y), radius, radius)
            painter.drawText(QRectF(x - 50, y - radius - 18, 100, 16),
                             Qt.AlignmentFlag.AlignCenter, str(point["name"]))
            self._hits.append((QRectF(x - radius, y - radius,
                                      radius * 2, radius * 2),
                               "element", str(point["name"])))

    def _paint_quadrant(self, painter, spec, palette) -> None:
        points = [p for p in spec.series
                  if isinstance(p.get("x"), (int, float))
                  and isinstance(p.get("y"), (int, float))]
        if not points:
            return self._paint_none(painter, spec, palette)
        xs = [float(p["x"]) for p in points] + [1.0]
        ys = [float(p["y"]) for p in points] + [1.0]
        x_high, y_high = max(xs) * 1.1, max(ys) * 1.1
        self._axis(painter, spec, palette, 0, x_high, 0, y_high)
        r = self._rect()
        x1 = _scale(1.0, 0, x_high, r.left(), r.right())
        y1 = _scale(1.0, 0, y_high, r.bottom(), r.top())
        painter.setPen(QPen(QColor(palette["muted"]), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x1, r.top()), QPointF(x1, r.bottom()))
        painter.drawLine(QPointF(r.left(), y1), QPointF(r.right(), y1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2F6FB7"))
        for point in points:
            x = _scale(float(point["x"]), 0, x_high, r.left(), r.right())
            y = _scale(float(point["y"]), 0, y_high, r.bottom(), r.top())
            painter.drawEllipse(QPointF(x, y), 4, 4)
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(x + 6, y - 16, 120, 16), str(point["name"]))
            painter.setPen(Qt.PenStyle.NoPen)
            self._hits.append((QRectF(x - 6, y - 6, 12, 12),
                               "element", str(point["name"])))

    def _paint_convergence(self, painter, spec, palette) -> None:
        points = [p for p in spec.series if isinstance(p.get("mean"), (int, float))]
        if not points:
            return self._paint_none(painter, spec, palette)
        means = [float(p["mean"]) for p in points]
        spreads = [2 * float(p["sem"]) if isinstance(p.get("sem"), (int, float))
                   else 0.0 for p in points]
        low = min(m - s for m, s in zip(means, spreads, strict=True))
        high = max(m + s for m, s in zip(means, spreads, strict=True))
        if high <= low:
            high = low + 1e-9
        self._axis(painter, spec, palette, 1, max(2, len(points)), low, high)
        r = self._rect()

        def point(i: int, value: float) -> QPointF:
            return QPointF(
                _scale(i + 1, 1, max(2, len(points)), r.left(), r.right()),
                _scale(value, low, high, r.bottom(), r.top()))

        band = QPolygonF([point(i, means[i] + spreads[i])
                          for i in range(len(points))]
                         + [point(i, means[i] - spreads[i])
                            for i in reversed(range(len(points)))])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(47, 111, 183, 40))
        painter.drawPolygon(band)
        painter.setPen(QPen(QColor("#2F6FB7"), 2))
        path = QPainterPath(point(0, means[0]))
        for i in range(1, len(points)):
            path.lineTo(point(i, means[i]))
        painter.drawPath(path)

    def _paint_curve(self, painter, spec, palette) -> None:
        series = spec.series[0] if spec.series else {}
        points = series.get("points") or []
        if not points:
            return self._paint_none(painter, spec, palette)
        xs = [float(p["x"]) for p in points]
        ys = [float(p["y"]) for p in points]
        self._axis(painter, spec, palette, min(xs), max(xs), min(ys), max(ys))
        r = self._rect()
        path = QPainterPath()
        for i, (x, y) in enumerate(zip(xs, ys, strict=True)):
            position = QPointF(_scale(x, min(xs), max(xs), r.left(), r.right()),
                               _scale(y, min(ys), max(ys), r.bottom(), r.top()))
            if i == 0:
                path.moveTo(position)
            else:
                path.lineTo(position)
        painter.setPen(QPen(QColor("#2F6FB7"), 2))
        painter.drawPath(path)

    def _paint_capacity_pareto(self, painter, spec, palette) -> None:
        rows = spec.series
        if not rows:
            return self._paint_none(painter, spec, palette)
        r = self._rect()
        peak = max(max(float(row.get("value") or 0),
                       float(row.get("downtime") or 0)) for row in rows) or 1.0
        height = r.height() / len(rows)
        for i, row in enumerate(rows):
            y = r.top() + i * height
            for offset, key, colour in (
                    (0.04, "downtime", QColor("#9AA5B1")),
                    (0.38, "value", QColor("#2F6FB7"))):
                width = r.width() * float(row.get(key) or 0) / peak
                painter.fillRect(QRectF(r.left(), y + height * offset,
                                        width, height * 0.3), colour)
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(r.left() + 4, y, r.width() - 8, height),
                             Qt.AlignmentFlag.AlignVCenter, str(row["name"]))
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(QRectF(r.left(), r.top() - 18, r.width(), 16),
                         Qt.AlignmentFlag.AlignLeft,
                         "capacity share (blue) against downtime share (grey)")

    def _paint_scatter(self, painter, spec, palette) -> None:
        points = [p for p in spec.series
                  if isinstance(p.get("x"), (int, float))
                  and isinstance(p.get("y"), (int, float))]
        if not points:
            return self._paint_none(painter, spec, palette)
        high = max(max(float(p["x"]) for p in points),
                   max(float(p["y"]) for p in points)) * 1.1 or 1.0
        self._axis(painter, spec, palette, 0, high, 0, high)
        r = self._rect()
        if any(mark.get("kind") == "diagonal" for mark in spec.marks):
            painter.setPen(QPen(QColor(palette["muted"]), 1,
                                Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(r.left(), r.bottom()),
                             QPointF(r.right(), r.top()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2F6FB7"))
        for point in points:
            x = _scale(float(point["x"]), 0, high, r.left(), r.right())
            y = _scale(float(point["y"]), 0, high, r.bottom(), r.top())
            painter.drawEllipse(QPointF(x, y), 4, 4)
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(x + 6, y - 16, 140, 16),
                             str(point["name"]))
            painter.setPen(Qt.PenStyle.NoPen)
            self._hits.append((QRectF(x - 6, y - 6, 12, 12),
                               "element", str(point["name"])))

    def _paint_fan(self, painter, spec, palette) -> None:
        rows = [row for row in spec.series if row.get("p50") is not None]
        if not rows:
            return self._paint_none(painter, spec, palette)
        lows = [float(row["p5"]) for row in rows
                if isinstance(row.get("p5"), (int, float))]
        highs = [float(row["p95"]) for row in rows
                 if isinstance(row.get("p95"), (int, float))]
        target = next((m["value"] for m in spec.marks
                       if m.get("kind") == "target"
                       and m.get("value") is not None), None)
        low = min(lows + ([target] if target is not None else []))
        high = max(highs + ([target] if target is not None else []))
        if high <= low:
            high = low + 1e-9
        self._axis(painter, spec, palette, 1, max(2, len(rows)), low, high)
        r = self._rect()

        def point(i: int, value: float) -> QPointF:
            return QPointF(
                _scale(i + 1, 1, max(2, len(rows)), r.left(), r.right()),
                _scale(value, low, high, r.bottom(), r.top()))

        def band(upper: str, lower: str, alpha: int) -> None:
            polygon = QPolygonF(
                [point(i, float(row[upper])) for i, row in enumerate(rows)]
                + [point(i, float(rows[i][lower]))
                   for i in reversed(range(len(rows)))])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(47, 111, 183, alpha))
            painter.drawPolygon(polygon)

        if all(isinstance(row.get("p5"), (int, float))
               and isinstance(row.get("p95"), (int, float)) for row in rows):
            band("p95", "p5", 30)
        if all(isinstance(row.get("p25"), (int, float))
               and isinstance(row.get("p75"), (int, float)) for row in rows):
            band("p75", "p25", 55)
        painter.setPen(QPen(QColor("#2F6FB7"), 2))
        path = QPainterPath(point(0, float(rows[0]["p50"])))
        for i in range(1, len(rows)):
            path.lineTo(point(i, float(rows[i]["p50"])))
        painter.drawPath(path)
        if target is not None:
            painter.setPen(QPen(QColor("#C4472F"), 2, Qt.PenStyle.DashLine))
            y = _scale(float(target), low, high, r.bottom(), r.top())
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

    def _paint_treemap(self, painter, spec, palette) -> None:
        rows = [row for row in spec.series if float(row.get("w") or 0) > 0]
        if not rows:
            return self._paint_none(painter, spec, palette)
        r = self._rect()
        width = max(float(row["x"]) + float(row["w"]) for row in rows) or 1.0
        height = max(float(row["y"]) + float(row["h"]) for row in rows) or 1.0
        for row in rows:
            x = r.left() + float(row["x"]) / width * r.width()
            y = r.top() + float(row["y"]) / height * r.height()
            w = float(row["w"]) / width * r.width()
            h = float(row["h"]) / height * r.height()
            colour = cause_colour(str(row.get("cause")))
            painter.fillRect(QRectF(x, y, w, h), colour)
            painter.setPen(QPen(QColor(palette["surface"]), 1))
            painter.drawRect(QRectF(x, y, w, h))
            if w > 52 and h > 16:
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QRectF(x + 4, y + 2, w - 6, 16),
                                 Qt.AlignmentFlag.AlignLeft,
                                 str(row["name"])[:14])
                if h > 30:
                    painter.drawText(
                        QRectF(x + 4, y + 16, w - 6, 14),
                        Qt.AlignmentFlag.AlignLeft,
                        f"{100 * float(row['value']):.3g} %")
            self._hits.append((QRectF(x, y, max(4.0, w), max(4.0, h)),
                               "element", str(row["name"])))

    def _paint_icicle(self, painter, spec, palette) -> None:
        rows = [row for row in spec.series
                if float(row.get("value") or 0) > 0]
        if not rows:
            return self._paint_none(painter, spec, palette)
        r = self._rect()
        columns = max(int(row["column"]) for row in rows) + 1
        width = r.width() / columns
        painter.setPen(QColor(palette["text"]))
        for column in range(columns):
            column_rows = [row for row in rows if int(row["column"]) == column]
            total = sum(float(row["value"]) for row in column_rows) or 1.0
            y = r.top()
            for row in column_rows:
                height = r.height() * float(row["value"]) / total
                x = r.left() + column * width
                painter.fillRect(QRectF(x + 1, y + 1, max(1, width - 2),
                                        max(1, height - 2)),
                                 cause_colour(str(row.get("cause"))))
                if height > 18 and width > 70:
                    painter.setPen(QColor("#ffffff"))
                    painter.drawText(QRectF(x + 4, y + 2, width - 8, 16),
                                     str(row["label"])[:10])
                self._hits.append((QRectF(x, y, width, max(4.0, height)),
                                   "element", str(row["label"])))
                y += height
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(QRectF(r.left() + column * width,
                                    r.bottom() + 4, width, 16),
                             Qt.AlignmentFlag.AlignHCenter,
                             f"step {column + 1}")

    # -- interaction ----------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt name
        point = event.position()
        for rect, kind, name in self._hits:
            if rect.contains(point):
                if kind == "element" and self._spec is not None:
                    row = next((r for r in self._spec.series
                                if str(r.get("name")) == name), None)
                    text = f"{name}"
                    if row is not None and isinstance(row.get("value"), (int, float)):
                        text += f": {100 * float(row['value']):.3g} %"
                    QToolTip.showText(event.globalPosition().toPoint(), text, self)
                break
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt name
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, kind, name in self._hits:
                if rect.contains(event.position()) and name:
                    self.locate_requested.emit(kind, name)
                    break
        super().mousePressEvent(event)

    def _menu(self, position) -> None:
        if self._spec is None:
            return
        menu = QMenu(self)
        menu.addAction("Copy image", self.copy_image)
        menu.addAction("Save PNG…", self._ask_png)
        menu.addAction("Save SVG…", self._ask_svg)
        menu.addAction("Save CSV…", self._ask_csv)
        menu.exec(self.mapToGlobal(position))

    def _ask_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", "",
                                              "PNG image (*.png)")
        if path:
            self.save_png(path)

    def _ask_svg(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", "",
                                              "SVG image (*.svg)")
        if path:
            self.save_svg(path)

    def _ask_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save chart data", "",
                                              "CSV (*.csv)")
        if path:
            self.save_csv(path)

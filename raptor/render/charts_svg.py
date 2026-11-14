"""ChartSpec -> SVG, for reports, the command line and MCP.

The same spec the GUI paints, rendered as plain SVG with no dependency.
Text is escaped, numbers are formatted to six significant digits, and the
output is deterministic: the same spec always produces the same bytes, so a
committed snapshot is a meaningful test.
"""
from __future__ import annotations

import html
import math
from typing import Any

from raptor.api.insight.charts import ChartSpec

WIDTH = 680
HEIGHT = 360
PAD_LEFT = 64
PAD_RIGHT = 24
PAD_TOP = 56
PAD_BOTTOM = 64

#: Cause fills, matching the GUI palette; every cause also has a pattern so
#: the chart reads without colour.
CAUSE_COLOURS = {
    "repair": "#C4472F", "wait_spare": "#C98A0B",
    "wait_resource": "#2F6FB7", "logistics": "#7A5AA6",
    "pm": "#2A8C82", "pm_wait": "#5E9C3F", "dependency": "#6B7785",
    "done": "#3C4653", "other": "#9AA5B1",
}
_PATTERN_PATHS = {
    "repair": "",
    "wait_spare": "M0,4 l4,-4 M-1,1 l2,-2 M3,5 l2,-2",
    "wait_resource": "M0,0 l4,4 M-1,3 l2,2 M3,-1 l2,2",
    "logistics": "M0,2 h4 M0,6 h4",
    "pm": "M0,0 v4 M4,4 v4",
    "pm_wait": "M0,0 h4 M0,4 h4",
    "dependency": "M0,0 l4,4 M4,0 l-4,4",
    "done": "M0,0 l4,4 M4,0 l-4,4 M0,2 h4 M2,0 v4",
    "other": "M0,0 h2 v2 h-2 z",
}


def _fmt(value: float) -> str:
    return f"{value:.6g}"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _scale(value: float, low: float, high: float, a: float, b: float) -> float:
    if high <= low:
        return (a + b) / 2
    return a + (value - low) / (high - low) * (b - a)


def _cause_defs() -> list[str]:
    out = ["<defs>"]
    for cause, colour in CAUSE_COLOURS.items():
        path = _PATTERN_PATHS.get(cause, "")
        if not path:
            continue
        out.append(
            f"<pattern id='pat-{cause}' width='4' height='4' "
            "patternUnits='userSpaceOnUse'>"
            f"<rect width='4' height='4' fill='{colour}'/>"
            f"<path d='{path}' stroke='#ffffff' stroke-opacity='0.55' "
            "stroke-width='1'/></pattern>")
    out.append("</defs>")
    return out


def _text(x: float, y: float, body: str, *, size: int = 12,
          anchor: str = "start", weight: str = "400",
          colour: str = "#333333") -> str:
    return (f"<text x='{_fmt(x)}' y='{_fmt(y)}' font-size='{size}' "
            f"font-weight='{weight}' text-anchor='{anchor}' "
            f"fill='{colour}' font-family='sans-serif'>{_esc(body)}</text>")


def _axes(spec: ChartSpec, *, x_low: float, x_high: float,
          y_low: float, y_high: float) -> list[str]:
    left, top, right, bottom = PAD_LEFT, PAD_TOP, WIDTH - PAD_RIGHT, HEIGHT - PAD_BOTTOM
    out = [
        f"<line x1='{left}' y1='{bottom}' x2='{right}' y2='{bottom}' "
        "stroke='#999999' stroke-width='1'/>",
        f"<line x1='{left}' y1='{top}' x2='{left}' y2='{bottom}' "
        "stroke='#999999' stroke-width='1'/>",
        _text((left + right) / 2, HEIGHT - 18, spec.x_label, anchor="middle"),
        f"<text x='14' y='{(top + bottom) / 2}' font-size='12' "
        f"text-anchor='middle' fill='#333333' font-family='sans-serif' "
        f"transform='rotate(-90 14 {(top + bottom) / 2})'>{_esc(spec.y_label)}</text>",
    ]
    for value in (x_low, x_high):
        out.append(_text(_scale(value, x_low, x_high, left, right),
                         bottom + 16, _fmt(value), size=10, anchor="middle"))
    for value in (y_low, y_high):
        out.append(_text(left - 6, _scale(value, y_low, y_high, bottom, top) + 4,
                         _fmt(value), size=10, anchor="end"))
    return out


def _dot_plot(spec: ChartSpec) -> list[str]:
    values = [float(d["value"]) for d in spec.series[0].get("dots", [])
              if isinstance(d.get("value"), (int, float))]
    if not values:
        return []
    low, high = min(values), max(values)
    target = next((m["value"] for m in spec.marks
                   if m.get("kind") == "target" and m.get("value") is not None),
                  None)
    if target is not None:
        low, high = min(low, target), max(high, target)
    if high <= low:
        high = low + 1e-9
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    mid = (PAD_TOP + HEIGHT - PAD_BOTTOM) / 2
    out = _axes(spec, x_low=low, x_high=high,
                y_low=0, y_high=1)
    out.append(f"<line x1='{left}' y1='{mid}' x2='{right}' y2='{mid}' "
               "stroke='#cccccc'/>")
    for dot in spec.series[0].get("dots", []):
        x = _scale(float(dot["value"]), low, high, left, right)
        out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(mid)}' r='3.2' "
                   "fill='#2F6FB7' fill-opacity='0.75'/>")
    if target is not None:
        x = _scale(float(target), low, high, left, right)
        out.append(f"<line x1='{_fmt(x)}' y1='{PAD_TOP}' x2='{_fmt(x)}' "
                   f"y2='{HEIGHT - PAD_BOTTOM}' stroke='#C4472F' "
                   "stroke-width='2' stroke-dasharray='5,3'/>")
        out.append(_text(x, PAD_TOP - 8, f"target {_fmt(float(target))}",
                         size=10, anchor="middle", colour="#C4472F"))
    return out


def _pareto(spec: ChartSpec) -> list[str]:
    rows = spec.series
    if not rows:
        return []
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    peak = max(float(row["value"]) for row in rows) or 1.0
    height = (bottom - top) / len(rows)
    out = []
    for i, row in enumerate(rows):
        y = top + i * height
        width = (right - left) * float(row["value"]) / peak
        out.append(f"<rect x='{left}' y='{_fmt(y + 3)}' width='{_fmt(width)}' "
                   f"height='{_fmt(max(1, height - 6))}' fill='#2F6FB7' "
                   "fill-opacity='0.35'/>")
        causes = row.get("causes") or {}
        cursor = float(left)
        for cause, share in causes.items():
            part = width * float(share)
            colour = CAUSE_COLOURS.get(cause, CAUSE_COLOURS["other"])
            fill = f"url(#pat-{cause})" if _PATTERN_PATHS.get(cause) else colour
            out.append(f"<rect x='{_fmt(cursor)}' y='{_fmt(y + 3)}' "
                       f"width='{_fmt(part)}' height='{_fmt(max(1, height - 6))}' "
                       f"fill='{fill}'/>")
            cursor += part
        out.append(_text(left + 4, y + height / 2 + 4, str(row["name"]),
                         size=11))
        out.append(_text(left + width + 6, y + height / 2 + 4,
                         f"{100 * float(row['value']):.3g} %", size=10))
    return out


def _waterfall(spec: ChartSpec) -> list[str]:
    steps = spec.series
    if not steps:
        return []
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    high = 1.0
    width = (right - left) / len(steps)
    out = []
    for i, step in enumerate(steps):
        x = left + i * width
        y0 = _scale(float(step["from"]), 0, high, bottom, top)
        y1 = _scale(float(step["to"]), 0, high, bottom, top)
        colour = CAUSE_COLOURS.get(str(step.get("cause")), CAUSE_COLOURS["other"])
        out.append(f"<rect x='{_fmt(x + 2)}' y='{_fmt(min(y0, y1))}' "
                   f"width='{_fmt(max(1, width - 4))}' "
                   f"height='{_fmt(max(1, abs(y0 - y1)))}' fill='{colour}'/>")
        out.append(_text(x + width / 2, bottom + 16,
                         str(step["name"]), size=9, anchor="middle"))
        out.append(_text(x + width / 2, min(y0, y1) - 4,
                         f"-{100 * float(step['value']):.3g}", size=9,
                         anchor="middle"))
    return out


def _ladder(spec: ChartSpec) -> list[str]:
    rows = [row for row in spec.series if row.get("value") is not None]
    if not rows:
        return []
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    height = (bottom - top) / (len(rows) + 1)
    out = []
    for i, row in enumerate(rows):
        y = top + i * height
        width = (right - left) * max(0.0, min(1.0, float(row["value"])))
        out.append(f"<rect x='{left}' y='{_fmt(y)}' width='{right - left}' "
                   f"height='{_fmt(height * 0.6)}' fill='#eeeeee'/>")
        out.append(f"<rect x='{left}' y='{_fmt(y)}' width='{_fmt(width)}' "
                   f"height='{_fmt(height * 0.6)}' "
                   "fill='#22c55e' fill-opacity='0.8'/>")
        out.append(_text(left + 4, y + height * 0.42, str(row["name"]),
                         size=11))
        out.append(_text(right, y + height * 0.42,
                         f"{100 * float(row['value']):.4g} %", size=11,
                         anchor="end"))
    return out


def _histogram(spec: ChartSpec) -> list[str]:
    bins = spec.series
    if not bins:
        return []
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    peak = max(float(b["count"]) for b in bins) or 1.0
    low = math.log(max(float(bins[0]["lo"]), 1e-12))
    high = math.log(max(float(bins[-1]["hi"]), 1e-12))
    if high <= low:
        high = low + 1e-9
    width = (right - left) / len(bins)
    out = []
    for i, row in enumerate(bins):
        x = left + i * width
        height = (bottom - top) * float(row["count"]) / peak
        out.append(f"<rect x='{_fmt(x + 1)}' y='{_fmt(bottom - height)}' "
                   f"width='{_fmt(max(1, width - 2))}' height='{_fmt(height)}' "
                   "fill='#7A5AA6' fill-opacity='0.75'/>")
        out.append(_text(x + width / 2, bottom + 16, _fmt(float(row["lo"])),
                         size=8, anchor="middle"))
    for mark in spec.marks:
        value = mark.get("value")
        if mark.get("kind") != "quantile" or value is None:
            continue
        x = _scale(math.log(max(float(value), 1e-12)), low, high, left, right)
        out.append(f"<line x1='{_fmt(x)}' y1='{PAD_TOP}' x2='{_fmt(x)}' "
                   f"y2='{bottom}' stroke='#111111' stroke-dasharray='4,3'/>")
        out.append(_text(x, PAD_TOP - 6, str(mark.get("label", "")),
                         size=9, anchor="middle"))
    return out


def _bubble(spec: ChartSpec) -> list[str]:
    points = [p for p in spec.series
              if isinstance(p.get("x"), (int, float))
              and isinstance(p.get("y"), (int, float))]
    if not points:
        return []
    xs = [float(p["x"]) for p in points]
    ys = [float(p["y"]) for p in points]
    x_high = max(xs) or 1.0
    y_high = max(ys) or 1.0
    areas = [float(p.get("area") or 0) for p in points]
    area_peak = max(areas) or 1.0
    out = _axes(spec, x_low=0, x_high=x_high, y_low=0, y_high=y_high)
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    for point in points:
        x = _scale(float(point["x"]), 0, x_high, left, right)
        y = _scale(float(point["y"]), 0, y_high, bottom, top)
        radius = 5 + 22 * (float(point.get("area") or 0) / area_peak) ** 0.5
        out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(y)}' "
                   f"r='{_fmt(radius)}' fill='#C98A0B' fill-opacity='0.55' "
                   "stroke='#8a5c00'/>")
        out.append(_text(x, y - radius - 3, str(point["name"]), size=9,
                         anchor="middle"))
    return out


def _quadrant(spec: ChartSpec) -> list[str]:
    points = [p for p in spec.series
              if isinstance(p.get("x"), (int, float))
              and isinstance(p.get("y"), (int, float))]
    if not points:
        return []
    xs = [float(p["x"]) for p in points] + [1.0]
    ys = [float(p["y"]) for p in points] + [1.0]
    x_high = max(xs) * 1.1 or 1.0
    y_high = max(ys) * 1.1 or 1.0
    out = _axes(spec, x_low=0, x_high=x_high, y_low=0, y_high=y_high)
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    x1 = _scale(1.0, 0, x_high, left, right)
    y1 = _scale(1.0, 0, y_high, bottom, top)
    out.append(f"<line x1='{_fmt(x1)}' y1='{top}' x2='{_fmt(x1)}' "
               f"y2='{bottom}' stroke='#999999' stroke-dasharray='4,3'/>")
    out.append(f"<line x1='{left}' y1='{_fmt(y1)}' x2='{right}' "
               f"y2='{_fmt(y1)}' stroke='#999999' stroke-dasharray='4,3'/>")
    for point in points:
        x = _scale(float(point["x"]), 0, x_high, left, right)
        y = _scale(float(point["y"]), 0, y_high, bottom, top)
        out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(y)}' r='4' "
                   "fill='#2F6FB7'/>")
        out.append(_text(x + 6, y - 4, str(point["name"]), size=9))
    return out


def _convergence(spec: ChartSpec) -> list[str]:
    points = [p for p in spec.series if isinstance(p.get("mean"), (int, float))]
    if not points:
        return []
    means = [float(p["mean"]) for p in points]
    lows, highs = [], []
    for point in points:
        sem = point.get("sem")
        spread = 2 * float(sem) if isinstance(sem, (int, float)) else 0.0
        lows.append(float(point["mean"]) - spread)
        highs.append(float(point["mean"]) + spread)
    low, high = min(lows), max(highs)
    if high <= low:
        high = low + 1e-9
    out = _axes(spec, x_low=1, x_high=max(2, len(points)),
                y_low=low, y_high=high)
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    band = []
    for i, _point in enumerate(points):
        band.append(f"{_fmt(_scale(i + 1, 1, max(2, len(points)), left, right))},"
                    f"{_fmt(_scale(highs[i], low, high, bottom, top))}")
    for i in reversed(range(len(points))):
        band.append(f"{_fmt(_scale(i + 1, 1, max(2, len(points)), left, right))},"
                    f"{_fmt(_scale(lows[i], low, high, bottom, top))}")
    out.append(f"<polygon points='{' '.join(band)}' fill='#2F6FB7' "
               "fill-opacity='0.15'/>")
    line = " ".join(
        f"{_fmt(_scale(i + 1, 1, max(2, len(points)), left, right))},"
        f"{_fmt(_scale(means[i], low, high, bottom, top))}"
        for i in range(len(points)))
    out.append(f"<polyline points='{line}' fill='none' stroke='#2F6FB7' "
               "stroke-width='2'/>")
    return out


def _curve(spec: ChartSpec) -> list[str]:
    for series in spec.series:
        points = series.get("points") or []
        if not points:
            continue
        xs = [float(p["x"]) for p in points]
        ys = [float(p["y"]) for p in points]
        low, high = min(xs), max(xs)
        y_low, y_high = min(ys), max(ys)
        if high <= low:
            high = low + 1e-9
        if y_high <= y_low:
            y_high = y_low + 1e-9
        out = _axes(spec, x_low=low, x_high=high, y_low=y_low, y_high=y_high)
        left, right = PAD_LEFT, WIDTH - PAD_RIGHT
        top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
        line = " ".join(
            f"{_fmt(_scale(x, low, high, left, right))},"
            f"{_fmt(_scale(y, y_low, y_high, bottom, top))}"
            for x, y in zip(xs, ys, strict=True))
        out.append(f"<polyline points='{line}' fill='none' stroke='#2F6FB7' "
                   "stroke-width='2'/>")
        return out + [f"<text x='{left}' y='{HEIGHT - 18}' font-size='12' "
                      f"fill='#333333'>{_esc(spec.x_label)}</text>"]
    return []


def _capacity_pareto(spec: ChartSpec) -> list[str]:
    rows = spec.series
    if not rows:
        return []
    r_left, r_right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    peak = max(max(float(row.get("value") or 0),
                   float(row.get("downtime") or 0)) for row in rows) or 1.0
    height = (bottom - top) / len(rows)
    out = []
    for i, row in enumerate(rows):
        y = top + i * height
        for offset, key, colour in ((0.04, "downtime", "#9AA5B1"),
                                    (0.36, "value", "#2F6FB7")):
            width = (r_right - r_left) * float(row.get(key) or 0) / peak
            out.append(f"<rect x='{r_left}' y='{_fmt(y + height * offset)}' "
                       f"width='{_fmt(width)}' height='{_fmt(height * 0.28)}' "
                       f"fill='{colour}'/>")
        out.append(_text(r_left + 4, y + height * 0.55, str(row["name"]),
                         size=11))
        out.append(_text(r_right, y + height * 0.55,
                         f"{100 * float(row.get('value') or 0):.3g} %",
                         size=10, anchor="end"))
    return out


def _scatter(spec: ChartSpec) -> list[str]:
    points = [p for p in spec.series
              if isinstance(p.get("x"), (int, float))
              and isinstance(p.get("y"), (int, float))]
    if not points:
        return []
    x_high = max(float(p["x"]) for p in points) * 1.1 or 1.0
    y_high = max(float(p["y"]) for p in points) * 1.1 or 1.0
    high = max(x_high, y_high)
    out = _axes(spec, x_low=0, x_high=high, y_low=0, y_high=high)
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    if any(m.get("kind") == "diagonal" for m in spec.marks):
        out.append(f"<line x1='{left}' y1='{bottom}' x2='{right}' "
                   f"y2='{top}' stroke='#999999' stroke-dasharray='5,3'/>")
    for point in points:
        x = _scale(float(point["x"]), 0, high, left, right)
        y = _scale(float(point["y"]), 0, high, bottom, top)
        out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(y)}' r='4' "
                   "fill='#2F6FB7'/>")
        out.append(_text(x + 6, y - 4, str(point["name"]), size=9))
    return out


def _fan(spec: ChartSpec) -> list[str]:
    rows = [row for row in spec.series if row.get("p50") is not None]
    if not rows:
        return []
    low_values = [float(row["p5"]) for row in rows
                  if isinstance(row.get("p5"), (int, float))]
    high_values = [float(row["p95"]) for row in rows
                   if isinstance(row.get("p95"), (int, float))]
    target = next((m["value"] for m in spec.marks
                   if m.get("kind") == "target" and m.get("value") is not None),
                  None)
    low = min(low_values + ([target] if target is not None else []))
    high = max(high_values + ([target] if target is not None else []))
    if high <= low:
        high = low + 1e-9
    count = len(rows)
    out = _axes(spec, x_low=1, x_high=max(2, count), y_low=low, y_high=high)
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM

    def x_of(i: int) -> float:
        return _scale(i + 1, 1, max(2, count), left, right)

    def y_of(value: float) -> float:
        return _scale(value, low, high, bottom, top)

    def band(upper: str, lower: str, colour: str, opacity: str) -> None:
        points = [f"{_fmt(x_of(i))},{_fmt(y_of(float(rows[i][upper])))}"
                  for i in range(count)]
        points += [f"{_fmt(x_of(i))},{_fmt(y_of(float(rows[i][lower])))}"
                   for i in reversed(range(count))]
        out.append(f"<polygon points='{' '.join(points)}' fill='{colour}' "
                   f"fill-opacity='{opacity}'/>")

    if all(isinstance(row.get("p5"), (int, float))
           and isinstance(row.get("p95"), (int, float)) for row in rows):
        band("p95", "p5", "#2F6FB7", "0.12")
    if all(isinstance(row.get("p25"), (int, float))
           and isinstance(row.get("p75"), (int, float)) for row in rows):
        band("p75", "p25", "#2F6FB7", "0.22")
    line = " ".join(f"{_fmt(x_of(i))},{_fmt(y_of(float(row['p50'])))}"
                    for i, row in enumerate(rows))
    out.append(f"<polyline points='{line}' fill='none' stroke='#2F6FB7' "
               "stroke-width='2'/>")
    if target is not None:
        y = y_of(float(target))
        out.append(f"<line x1='{left}' y1='{_fmt(y)}' x2='{right}' "
                   f"y2='{_fmt(y)}' stroke='#C4472F' stroke-width='2' "
                   "stroke-dasharray='5,3'/>")
    return out


def _treemap(spec: ChartSpec) -> list[str]:
    rows = [row for row in spec.series if float(row.get("w") or 0) > 0]
    if not rows:
        return []
    width = max(float(row["x"]) + float(row["w"]) for row in rows) or 1.0
    height = max(float(row["y"]) + float(row["h"]) for row in rows) or 1.0
    left, top = PAD_LEFT, PAD_TOP
    scale_x = (WIDTH - PAD_LEFT - PAD_RIGHT) / width
    scale_y = (HEIGHT - PAD_TOP - PAD_BOTTOM) / height
    out = []
    for row in rows:
        x = left + float(row["x"]) * scale_x
        y = top + float(row["y"]) * scale_y
        w = float(row["w"]) * scale_x
        h = float(row["h"]) * scale_y
        colour = CAUSE_COLOURS.get(str(row.get("cause")), "#6B7785")
        out.append(f"<rect x='{_fmt(x)}' y='{_fmt(y)}' width='{_fmt(w)}' "
                   f"height='{_fmt(h)}' fill='{colour}' fill-opacity='0.72' "
                   "stroke='#ffffff' stroke-width='1'/>")
        if w > 52 and h > 16:
            out.append(_text(x + 4, y + 14, str(row["name"])[:14], size=9,
                             colour="#ffffff"))
            if h > 30:
                out.append(_text(x + 4, y + 28,
                                 f"{100 * float(row['value']):.3g} %",
                                 size=8, colour="#ffffff"))
    return out


def _icicle(spec: ChartSpec) -> list[str]:
    rows = [row for row in spec.series if float(row.get("value") or 0) > 0]
    if not rows:
        return []
    columns = max(int(row["column"]) for row in rows) + 1
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    width = (right - left) / columns
    out = []
    for column in range(columns):
        column_rows = [row for row in rows if int(row["column"]) == column]
        total = sum(float(row["value"]) for row in column_rows) or 1.0
        y = float(top)
        for row in column_rows:
            height = (bottom - top) * float(row["value"]) / total
            colour = CAUSE_COLOURS.get(str(row.get("cause")),
                                       CAUSE_COLOURS["other"])
            x = left + column * width
            out.append(f"<rect x='{_fmt(x + 1)}' y='{_fmt(y + 1)}' "
                       f"width='{_fmt(max(1, width - 2))}' "
                       f"height='{_fmt(max(1, height - 2))}' fill='{colour}' "
                       "fill-opacity='0.78' stroke='#ffffff'/>")
            if height > 18 and width > 70:
                out.append(_text(x + 4, y + 14, str(row["label"])[:10],
                                 size=9, colour="#ffffff"))
            y += height
    return out


def _ribbon(spec: ChartSpec) -> list[str]:
    rows = [row for row in spec.series if row.get("span")]
    if not rows:
        return []
    left, right = PAD_LEFT, WIDTH - PAD_RIGHT
    top, bottom = PAD_TOP, HEIGHT - PAD_BOTTOM
    start = min(float(row["start"]) for row in rows)
    end = max(float(row["end"]) for row in rows)
    height = (bottom - top) / len(rows)
    out = []
    for i, row in enumerate(rows):
        y = top + i * height
        out.append(f"<rect x='{left}' y='{_fmt(y)}' width='{right - left}' "
                   f"height='{_fmt(max(1, height - 0.4))}' fill='#22c55e' "
                   "fill-opacity='0.55'/>")
        for a, b in row.get("red") or []:
            x0 = _scale(float(a), start, end, left, right)
            x1 = _scale(float(b), start, end, left, right)
            out.append(f"<rect x='{_fmt(x0)}' y='{_fmt(y)}' "
                       f"width='{_fmt(max(1, x1 - x0))}' "
                       f"height='{_fmt(max(1, height - 0.4))}' fill='#C4472F'/>")
    return out


_RENDERERS = {
    "dot_plot": _dot_plot,
    "ecdf": _curve,
    "pareto": _pareto,
    "waterfall": _waterfall,
    "ladder": _ladder,
    "histogram": _histogram,
    "bubble": _bubble,
    "quadrant": _quadrant,
    "convergence": _convergence,
    "curve": _curve,
    "capacity_pareto": _capacity_pareto,
    "scatter": _scatter,
    "fan": _fan,
    "treemap": _treemap,
    "icicle": _icicle,
    "ribbon": _ribbon,
}


def render(spec: ChartSpec, *, width: int = WIDTH, height: int = HEIGHT,
           background: str = "#ffffff", text_colour: str = "#333333") -> str:
    """One ChartSpec as a complete SVG document."""
    body: list[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' "
        f"height='{height}' viewBox='0 0 {width} {height}' "
        f"role='img' aria-label='{_esc(spec.title)}'>",
        f"<rect width='{width}' height='{height}' fill='{background}'/>",
        _text(width / 2, 24, spec.title, size=16, anchor="middle",
              weight="600", colour=text_colour),
        _text(width / 2, 42, spec.subtitle, size=11, anchor="middle",
              colour=text_colour),
        *_cause_defs(),
    ]
    renderer = _RENDERERS.get(spec.kind)
    body.extend(renderer(spec) if renderer is not None else [])
    if spec.caption:
        body.append(_text(PAD_LEFT, height - 8, spec.caption, size=10,
                          colour=text_colour))
    body.append("</svg>")
    return "\n".join(body)


def write(spec: ChartSpec, path: str | Any) -> str:
    """Write the SVG and return the path as text."""
    text = render(spec)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return str(path)

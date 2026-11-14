"""ChartSpec -> SVG, for reports, the command line and MCP.

The same spec the GUI paints (`raptor.gui.ui.chart_widget`), drawn the same
way: round 1-2-5 ticks with gridlines from `raptor.api.insight.axes`, values
in the chart's own format (percent, money or plain), element names in a
gutter beside the bars, and a key for the cause colours and patterns.  Plain
SVG with no dependency; text is escaped and the output is deterministic (the
same spec always gives the same bytes), so a committed snapshot is a
meaningful test.
"""
from __future__ import annotations

import html
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from raptor.api.insight.attribution import CAUSE_LABELS
from raptor.api.insight.axes import (
    format_value, nice_range, nice_ticks, stack_dots, tick_text,
)
from raptor.api.insight.charts import ChartSpec

WIDTH = 760
HEIGHT = 400
PAD = 16
TITLE_H = 48
KEY_H = 18
CAPTION_H = 26
CHAR_W = 6.4          # mean advance of an 11 px sans-serif character
LINE_H = 14

INK = "#132238"
MUTED = "#5D7080"
GRID = "#D7E0E8"
ACCENT = "#1479C9"
STOP = "#C62828"
UP = "#218B61"
DOWN = "#CC4545"

#: Cause fills, matching the GUI palette; every cause but repair also has a
#: pattern so the chart reads without colour.
CAUSE_COLOURS = {
    "repair": "#C4472F", "wait_spare": "#C98A0B",
    "wait_resource": "#2F6FB7", "logistics": "#7A5AA6",
    "pm": "#2A8C82", "pm_wait": "#5E9C3F", "dependency": "#6B7785",
    "done": "#3C4653", "other": "#9AA5B1",
    # cost components (the cost treemap); spares share wait_spare's colour
    "purchase": "#2F6FB7", "operating": "#B5651D", "sparing": "#C98A0B",
    "disposal": "#6B7785", "spare_pool": "#8E5B9E",
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
    "purchase": "",
    "operating": "M0,2 h4",
    "sparing": "M0,4 l4,-4 M-1,1 l2,-2 M3,5 l2,-2",
    "disposal": "M0,0 l4,4 M4,0 l-4,4",
    "spare_pool": "M0,2 h4 M0,6 h4",
}


def _fmt(value: float) -> str:
    return f"{value:.6g}"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def _width(text: str) -> float:
    return len(str(text)) * CHAR_W


def _fit(text: str, width: float) -> str:
    text = str(text)
    room = max(1, int(width / CHAR_W))
    return text if len(text) <= room else text[:max(1, room - 1)] + "…"


def _darker(colour: str) -> str:
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{int(r * 0.6):02x}{int(g * 0.6):02x}{int(b * 0.6):02x}"


def _ink_on(colour: str) -> str:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    lum = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return "#111111" if (lum + 0.05) / 0.05 > 1.05 / (lum + 0.05) else "#ffffff"


def _cause_fill(cause: str) -> str:
    cause = cause if cause in CAUSE_COLOURS else "other"
    return f"url(#pat-{cause})" if _PATTERN_PATHS.get(cause) else CAUSE_COLOURS[cause]


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
            f"<path d='{path}' stroke='{_darker(colour)}' stroke-width='1'/>"
            "</pattern>")
    out.append("</defs>")
    return out


def _text(x: float, y: float, body: str, *, size: int = 11,
          anchor: str = "start", weight: str = "400",
          colour: str = INK) -> str:
    return (f"<text x='{_fmt(x)}' y='{_fmt(y)}' font-size='{size}' "
            f"font-weight='{weight}' text-anchor='{anchor}' "
            f"fill='{colour}' font-family='sans-serif'>{_esc(body)}</text>")


def _rect(x: float, y: float, w: float, h: float, fill: str, extra: str = "") -> str:
    return (f"<rect x='{_fmt(x)}' y='{_fmt(y)}' width='{_fmt(max(0.0, w))}' "
            f"height='{_fmt(max(0.0, h))}' fill='{fill}'{extra}/>")


def _line(x1: float, y1: float, x2: float, y2: float, colour: str, *,
          width: float = 1.0, dash: str = "") -> str:
    style = f" stroke-dasharray='{dash}'" if dash else ""
    return (f"<line x1='{_fmt(x1)}' y1='{_fmt(y1)}' x2='{_fmt(x2)}' "
            f"y2='{_fmt(y2)}' stroke='{colour}' stroke-width='{_fmt(width)}'{style}/>")


@dataclass
class _Box:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class _Frame:
    box: _Box
    x_low: float
    x_high: float
    y_low: float
    y_high: float
    x_log: bool = False

    def x(self, value: float) -> float:
        low, high, v = self.x_low, self.x_high, value
        if self.x_log:
            low, high, v = math.log10(low), math.log10(high), math.log10(max(value, 1e-300))
        if high <= low:
            return (self.box.left + self.box.right) / 2
        return self.box.left + (v - low) / (high - low) * self.box.width

    def y(self, value: float) -> float:
        if self.y_high <= self.y_low:
            return (self.box.top + self.box.bottom) / 2
        return self.box.bottom - (value - self.y_low) / (self.y_high - self.y_low) \
            * self.box.height


def _frame(out: list[str], spec: ChartSpec, area: _Box, *,
           x_range: tuple[float, float], y_range: tuple[float, float],
           x_fmt: str = "", y_fmt: str = "", x_log: bool = False,
           x_ticks: Sequence[float] | None = None,
           y_ticks: Sequence[float] | None = None,
           x_label: str | None = None, y_label: str | None = None,
           y_axis: bool = True) -> _Frame:
    yt = list(y_ticks) if y_ticks is not None else nice_ticks(*y_range, 5)
    xt = list(x_ticks) if x_ticks is not None else nice_ticks(*x_range, 6)
    y_text = [tick_text(t, yt, fmt=y_fmt) for t in yt]
    x_text = ([_short(t) for t in xt] if x_log
              else [tick_text(t, xt, fmt=x_fmt) for t in xt])
    gutter = max([_width(t) for t in y_text] + [10]) + 6
    y_title = (y_label if y_label is not None else spec.y_label) or ""
    x_title = (x_label if x_label is not None else spec.x_label) or ""
    left = area.left + gutter + (LINE_H + 4 if y_title else 0)
    bottom = area.bottom - LINE_H - 4 - (LINE_H + 2 if x_title else 0)
    box = _Box(left, area.top + LINE_H / 2, area.right - 8, bottom)
    frame = _Frame(box, x_range[0], x_range[1], y_range[0], y_range[1], x_log)
    for t in yt:
        out.append(_line(box.left, frame.y(t), box.right, frame.y(t), GRID))
    for t, text in zip(yt, y_text, strict=True):
        out.append(_text(box.left - 6, frame.y(t) + 4, text, size=10,
                         anchor="end", colour=MUTED))
    last_right = -1e9
    for t, text in zip(xt, x_text, strict=True):
        x = frame.x(t)
        out.append(_line(x, box.bottom, x, box.bottom + 4, MUTED))
        if x - _width(text) / 2 < last_right + 6:
            continue
        out.append(_text(x, box.bottom + 15, text, size=10, anchor="middle",
                         colour=MUTED))
        last_right = x + _width(text) / 2
    out.append(_line(box.left, box.bottom, box.right, box.bottom, MUTED))
    if y_axis:
        out.append(_line(box.left, box.top, box.left, box.bottom, MUTED))
    if x_title:
        out.append(_text((box.left + box.right) / 2, box.bottom + 31, x_title,
                         anchor="middle"))
    if y_title:
        cy = (box.top + box.bottom) / 2
        cx = area.left + 10
        out.append(f"<text x='{_fmt(cx)}' y='{_fmt(cy)}' font-size='11' "
                   f"text-anchor='middle' fill='{INK}' font-family='sans-serif' "
                   f"transform='rotate(-90 {_fmt(cx)} {_fmt(cy)})'>"
                   f"{_esc(y_title)}</text>")
    return frame


def _target(spec: ChartSpec) -> float | None:
    return next((float(m["value"]) for m in spec.marks
                 if m.get("kind") == "target" and _number(m.get("value"))), None)


def _target_line(out: list[str], frame: _Frame, value: float, *,
                 horizontal: bool, label: str) -> None:
    box = frame.box
    w = _width(label) + 8
    if horizontal:
        y = frame.y(value)
        out.append(_line(box.left, y, box.right, y, STOP, width=1.6, dash="5,3"))
        out.append(_rect(box.right - w, y - LINE_H - 1, w, LINE_H, "#ffffff"))
        out.append(_text(box.right - 4, y - 4, label, size=10, anchor="end",
                         colour=STOP))
    else:
        x = frame.x(value)
        out.append(_line(x, box.top, x, box.bottom, STOP, width=1.6, dash="5,3"))
        left = x + 4 if x + 4 + w <= box.right else x - 4 - w
        out.append(_rect(left, box.top, w, LINE_H, "#ffffff"))
        out.append(_text(left + 4, box.top + 11, label, size=10, colour=STOP))


def _fmt_of(spec: ChartSpec) -> str:
    return str(spec.provenance.get("format", ""))


# -- the charts ----------------------------------------------------------


def _dot_plot(spec: ChartSpec, area: _Box) -> list[str]:
    values: list[float] = []
    for dot in (spec.series[0].get("dots", []) if spec.series else []):
        if _number(dot.get("value")):
            values.extend([float(dot["value"])] * max(1, int(dot.get("count", 1))))
    if not values:
        return []
    target = _target(spec)
    fmt = _fmt_of(spec)
    low, high = nice_range(min(values + ([target] if target is not None else [])),
                           max(values + ([target] if target is not None else [])), 5)
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(low, high), y_range=(0, 1),
                   x_fmt=fmt, y_ticks=[], y_label="", y_axis=False)
    box = frame.box
    diameter = min(14.0, max(4.0, box.width / max(12, len(values)) * 1.6))
    levels = stack_dots([frame.x(v) for v in values], diameter)
    tallest = max(level for _x, level in levels) + 1
    step = min(diameter, (box.height - diameter) / max(1, tallest - 1)) \
        if tallest > 1 else diameter
    radius = diameter / 2 - 0.6
    for value, (x, level) in zip(values, levels, strict=True):
        y = box.bottom - diameter / 2 - level * step
        if target is None or value >= target:
            out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(y)}' r='{_fmt(radius)}' "
                       f"fill='{ACCENT}'/>")
        else:
            out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(y)}' r='{_fmt(radius)}' "
                       f"fill='none' stroke='{STOP}' stroke-width='1.4'/>")
    if target is not None:
        _target_line(out, frame, target, horizontal=False,
                     label=f"target {format_value(target, fmt, 5)}")
    return out


def _exceedance(spec: ChartSpec, area: _Box) -> list[str]:
    points = [p for p in (spec.series[0].get("points") or [])
              if _number(p.get("value")) and _number(p.get("share"))] \
        if spec.series else []
    if not points:
        return []
    fmt = _fmt_of(spec)
    target = _target(spec)
    values = [float(p["value"]) for p in points]
    low, high = nice_range(min(values + ([target] if target is not None else [])),
                           max(values + ([target] if target is not None else [])), 5)
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(0.0, 1.0), y_range=(low, high),
                   x_fmt="percent", y_fmt=fmt,
                   x_ticks=[i / 10 for i in range(11)])
    path = [f"M{_fmt(frame.x(0.0))},{_fmt(frame.y(values[0]))}"]
    previous = 0.0
    for point in points:
        share, value = float(point["share"]), float(point["value"])
        path.append(f"L{_fmt(frame.x(previous))},{_fmt(frame.y(value))}")
        path.append(f"L{_fmt(frame.x(share))},{_fmt(frame.y(value))}")
        previous = share
    out.append(f"<path d='{' '.join(path)}' fill='none' stroke='{ACCENT}' "
               "stroke-width='2.2'/>")
    if target is not None:
        higher = bool(spec.provenance.get("higher_is_better", True))
        met = max((float(p["share"]) for p in points
                   if (float(p["value"]) >= target if higher
                       else float(p["value"]) <= target)), default=0.0)
        _target_line(out, frame, target, horizontal=True,
                     label=f"target {format_value(target, fmt, 5)}: "
                           f"{100 * met:.3g} % of runs reach it")
    return out


def _key(spec: ChartSpec) -> list[tuple[str, str]]:
    """(label, fill) for every cause the chart shows."""
    seen: list[str] = []
    if spec.kind == "pareto":
        for row in spec.series:
            for cause in row.get("causes") or {}:
                if cause not in seen:
                    seen.append(cause)
    elif spec.kind in ("waterfall", "treemap", "icicle"):
        for row in spec.series:
            cause = row.get("cause")
            if cause and str(cause) not in seen:
                seen.append(str(cause))
    labels = {**CAUSE_LABELS, **(spec.provenance.get("group_labels") or {})}
    entries = [(labels.get(c, c), _cause_fill(c)) for c in seen]
    if spec.kind == "capacity_pareto":
        entries = [("lost capacity", ACCENT), ("downtime", MUTED)]
    if spec.kind == "ribbon":
        entries = [("system up", UP), ("system down", DOWN)]
    return entries


def _bars(spec: ChartSpec, area: _Box, rows: list[dict], *, fmt: str,
          labels: list[str], peak: float, x_label: str,
          draw: Callable[[list[str], _Frame, dict, float, float], float | None],
          ) -> list[str]:
    """Horizontal bars with names in a gutter and a value after each bar."""
    gutter = min(area.width * 0.4, max(_width(r["name"]) for r in rows) + 10)
    value_w = max(_width(t) for t in labels) + 10
    plot = _Box(area.left + gutter, area.top,
                max(area.left + gutter + 40, area.right - value_w), area.bottom)
    out: list[str] = []
    frame = _frame(out, spec, plot, x_range=(0.0, peak), y_range=(0, 1),
                   x_fmt=fmt, y_ticks=[], y_label="", x_label=x_label)
    box = frame.box
    band = box.height / len(rows)
    bar_h = max(2.0, min(26.0, band * 0.64))
    for i, (row, label) in enumerate(zip(rows, labels, strict=True)):
        top = box.top + i * band + (band - bar_h) / 2
        end = draw(out, frame, row, top, bar_h)
        out.append(_text(area.left + gutter - 8, top + bar_h / 2 + 4,
                         _fit(row["name"], gutter - 8), anchor="end"))
        out.append(_text(box.left + (end or 0.0) + 6, top + bar_h / 2 + 4, label,
                         size=10))
    return out


def _pareto(spec: ChartSpec, area: _Box) -> list[str]:
    rows = [r for r in spec.series if _number(r.get("value"))]
    if not rows:
        return []
    fmt = _fmt_of(spec)
    money = fmt == "money"

    def label(row: dict) -> str:
        if money:
            text = format_value(float(row["value"]), "money")
            if _number(row.get("share")):
                text += f" ({100 * float(row['share']):.3g} %)"
            return text
        return format_value(float(row["value"]), fmt, 3)

    peak = max(float(r["value"]) + 2 * float(r.get("sem") or 0) for r in rows)
    peak = nice_range(0.0, peak or 1.0, 4)[1]

    def draw(out: list[str], frame: _Frame, row: dict, top: float, bar_h: float) -> float:
        width = frame.x(float(row["value"])) - frame.box.left
        causes = row.get("causes") or {}
        if causes:
            cursor = frame.box.left
            for cause, share in causes.items():
                part = width * float(share)
                out.append(_rect(cursor, top, part, bar_h, _cause_fill(cause)))
                cursor += part
        else:
            out.append(_rect(frame.box.left, top, width, bar_h, ACCENT))
        sem = float(row.get("sem") or 0.0)
        end = width
        if sem > 0:
            lo = frame.x(max(0.0, float(row["value"]) - 2 * sem))
            hi = frame.x(float(row["value"]) + 2 * sem)
            mid = top + bar_h / 2
            out.append(_line(lo, mid, hi, mid, INK, width=1.2))
            out.append(_line(lo, mid - 4, lo, mid + 4, INK, width=1.2))
            out.append(_line(hi, mid - 4, hi, mid + 4, INK, width=1.2))
            end = max(end, hi - frame.box.left)
        return end

    return _bars(spec, area, rows, fmt=fmt, labels=[label(r) for r in rows],
                 peak=peak, x_label=spec.y_label, draw=draw)


def _points(fraction: float) -> str:
    value = 100 * abs(fraction)
    if value == 0:
        return "0"
    places = max(0, min(9, 1 - math.floor(math.log10(value))))
    return f"{value:.{places}f}"


def _waterfall(spec: ChartSpec, area: _Box) -> list[str]:
    steps = [s for s in spec.series if _number(s.get("from")) and _number(s.get("to"))]
    if not steps:
        return []
    operational = spec.provenance.get("operational")
    ends = [float(s["to"]) for s in steps] + [float(s["from"]) for s in steps]
    span = max(ends) - min(ends)
    low, high = nice_range(min(ends) - 0.35 * span, max(ends), 5)
    low = max(0.0, low)
    high = min(1.0, high) if max(ends) <= 1.0 else high
    columns = len(steps) + (1 if _number(operational) else 0)
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(0, columns), y_range=(low, high),
                   y_fmt="percent", x_ticks=[], x_label="",
                   y_label="availability" if _number(operational) else spec.y_label)
    box = frame.box
    width = box.width / columns
    for i, step in enumerate(steps):
        x = box.left + i * width
        y0, y1 = frame.y(float(step["from"])), frame.y(float(step["to"]))
        out.append(_rect(x + width * 0.15, min(y0, y1), width * 0.7,
                         max(1.5, abs(y0 - y1)), _cause_fill(str(step.get("cause")))))
        drop = (f"−{_points(float(step['value']))} pts" if _number(operational)
                else f"{100 * float(step['value']):.3g} %")
        if max(y0, y1) + 16 < box.bottom:
            out.append(_text(x + width / 2, max(y0, y1) + 14, drop, size=10,
                             anchor="middle"))
        else:
            out.append(_text(x + width / 2, min(y0, y1) + 14, drop, size=10,
                             anchor="middle",
                             colour=_ink_on(CAUSE_COLOURS.get(
                                 str(step.get("cause")), CAUSE_COLOURS["other"]))))
        out.append(_text(x + width / 2, box.bottom + 15,
                         _fit(step["name"], width - 4), size=10, anchor="middle"))
    if not _number(operational):
        return out
    end = float(steps[-1]["to"])
    x = box.left + len(steps) * width
    out.append(_rect(x + width * 0.15, frame.y(end), width * 0.7,
                     max(1.5, box.bottom - frame.y(end)), ACCENT))
    out.append(_text(x + width / 2, frame.y(end) - 4, format_value(end, "percent", 6),
                     size=10, anchor="middle"))
    out.append(_text(x + width / 2, box.bottom + 15,
                     "achieved" if _number(operational) else "rest", size=10,
                     anchor="middle"))
    return out


def _ladder(spec: ChartSpec, area: _Box) -> list[str]:
    rows = [row for row in spec.series if _number(row.get("value"))]
    if not rows:
        return []
    values = [float(r["value"]) for r in rows]
    top_value = 1.0 if max(values) <= 1.0 else max(values)
    span = max(top_value - min(values), 1e-9)
    low, high = nice_range(min(values) - 0.6 * span, top_value, 4)
    low, high = max(0.0, low), (min(1.0, high) if top_value <= 1.0 else high)
    labels, previous = [], None
    for value in values:
        text = format_value(value, "percent", 6)
        if previous is not None and previous - value > 1e-12:
            text += f"  (−{_points(previous - value)} pts)"
        labels.append(text)
        previous = value
    return _ladder_zoomed(spec, area, rows, labels, (low, high),
                          [UP, ACCENT, "#7454A6"])


def _ladder_zoomed(spec: ChartSpec, area: _Box, rows: list[dict], labels: list[str],
                   x_range: tuple[float, float], colours: list[str]) -> list[str]:
    gutter = min(area.width * 0.45, max(_width(r["name"]) for r in rows) + 10)
    value_w = max(_width(t) for t in labels) + 10
    plot = _Box(area.left + gutter, area.top,
                max(area.left + gutter + 40, area.right - value_w), area.bottom)
    out: list[str] = []
    frame = _frame(out, spec, plot, x_range=x_range, y_range=(0, 1),
                   x_fmt="percent", y_ticks=[], y_label="",
                   x_label="availability (axis zoomed)")
    box = frame.box
    band = box.height / len(rows)
    bar_h = max(2.0, min(24.0, band * 0.6))
    for i, (row, label) in enumerate(zip(rows, labels, strict=True)):
        top = box.top + i * band + (band - bar_h) / 2
        right = frame.x(float(row["value"]))
        out.append(_rect(box.left, top, right - box.left, bar_h,
                         colours[i % len(colours)]))
        out.append(_text(area.left + gutter - 8, top + bar_h / 2 + 4,
                         _fit(row["name"], gutter - 8), anchor="end"))
        out.append(_text(right + 6, top + bar_h / 2 + 4, label, size=10))
    return out


def _histogram(spec: ChartSpec, area: _Box) -> list[str]:
    bins = [b for b in spec.series if _number(b.get("lo")) and _number(b.get("hi"))
            and _number(b.get("count"))]
    if not bins:
        return []
    lo = max(float(bins[0]["lo"]), 1e-12)
    hi = max(float(bins[-1]["hi"]), lo * 10)
    peak = max(float(b["count"]) for b in bins) or 1.0
    decades = range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1)
    steps = (1, 2, 5) if len(decades) <= 4 else (1,)
    ticks = [m * 10.0 ** k for k in decades for m in steps if lo <= m * 10.0 ** k <= hi]
    if len(ticks) < 2:
        ticks = sorted({lo, hi})
    y_range = nice_range(0.0, peak, 4)
    counts = nice_ticks(*y_range, 4)
    if all(float(b["count"]).is_integer() for b in bins):
        counts = [t for t in counts if float(t).is_integer()]
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(lo, hi), y_range=y_range,
                   y_ticks=counts, x_log=True, x_ticks=ticks, y_fmt="count",
                   x_label=f"{spec.x_label} (log scale)")
    for row in bins:
        x0, x1 = frame.x(float(row["lo"])), frame.x(float(row["hi"]))
        y = frame.y(float(row["count"]))
        out.append(_rect(x0 + 1, y, max(1.0, x1 - x0 - 2), frame.box.bottom - y,
                         ACCENT))
    for mark in spec.marks:
        if mark.get("kind") == "quantile" and _number(mark.get("value")):
            _target_line(out, frame, float(mark["value"]), horizontal=False,
                         label=f"{mark.get('label', '')} {_short(float(mark['value']))}")
    return out


def _scatter_frame(out: list[str], spec: ChartSpec, area: _Box, points: list[dict], *,
                   square: bool = False, include: Sequence[tuple[float, float]] = (),
                   x_fmt: str | None = None, y_fmt: str | None = None) -> _Frame:
    xs = [float(p["x"]) for p in points] + [x for x, _y in include]
    ys = [float(p["y"]) for p in points] + [y for _x, y in include]
    x_high = nice_range(0.0, max(xs) * 1.08 or 1.0, 5)[1]
    y_high = nice_range(0.0, max(ys) * 1.12 or 1.0, 5)[1]
    if square:
        x_high = y_high = max(x_high, y_high)
    fmt = _fmt_of(spec)
    return _frame(out, spec, area, x_range=(0.0, x_high), y_range=(0.0, y_high),
                  x_fmt=fmt if x_fmt is None else x_fmt,
                  y_fmt=fmt if y_fmt is None else y_fmt)


def _label_point(out: list[str], frame: _Frame, x: float, y: float, radius: float,
                 text: str) -> None:
    w = _width(text)
    box = frame.box
    cx = min(max(box.left + w / 2, x), box.right - w / 2)
    ty = y - radius - 3
    if ty - 10 < box.top:
        ty = y + radius + 12
    out.append(_text(cx, ty, text, size=10, anchor="middle"))


def _xy(spec: ChartSpec) -> list[dict]:
    return [p for p in spec.series if _number(p.get("x")) and _number(p.get("y"))]


def _bubble(spec: ChartSpec, area: _Box) -> list[str]:
    points = _xy(spec)
    if not points:
        return []
    out: list[str] = []
    frame = _scatter_frame(out, spec, area, points, x_fmt="percent", y_fmt="")
    area_peak = max(float(p.get("area") or 0) for p in points) or 1.0
    for point in sorted(points, key=lambda p: -float(p.get("area") or 0)):
        x, y = frame.x(float(point["x"])), frame.y(float(point["y"]))
        radius = 4 + 20 * (float(point.get("area") or 0) / area_peak) ** 0.5
        out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(y)}' r='{_fmt(radius)}' "
                   f"fill='{ACCENT}' fill-opacity='0.43' stroke='{ACCENT}'/>")
        _label_point(out, frame, x, y, radius, str(point["name"]))
    return out


def _dots(out: list[str], frame: _Frame, points: list[dict]) -> None:
    for point in points:
        x, y = frame.x(float(point["x"])), frame.y(float(point["y"]))
        out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(y)}' r='4.5' fill='{ACCENT}'/>")
        _label_point(out, frame, x, y, 5, str(point["name"]))


def _quadrant(spec: ChartSpec, area: _Box) -> list[str]:
    points = _xy(spec)
    if not points:
        return []
    out: list[str] = []
    frame = _scatter_frame(out, spec, area, points, include=[(1.0, 1.0)])
    box = frame.box
    out.append(_line(frame.x(1.0), box.top, frame.x(1.0), box.bottom, MUTED, dash="4,3"))
    out.append(_line(box.left, frame.y(1.0), box.right, frame.y(1.0), MUTED, dash="4,3"))
    _dots(out, frame, points)
    return out


def _scatter(spec: ChartSpec, area: _Box) -> list[str]:
    points = _xy(spec)
    if not points:
        return []
    out: list[str] = []
    frame = _scatter_frame(out, spec, area, points, square=True)
    if any(m.get("kind") == "diagonal" for m in spec.marks):
        box = frame.box
        out.append(_line(box.left, box.bottom, box.right, box.top, MUTED, dash="5,3"))
    _dots(out, frame, points)
    return out


def _polyline(points: list[tuple[float, float]], colour: str, width: float = 2.0) -> str:
    text = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in points)
    return (f"<polyline points='{text}' fill='none' stroke='{colour}' "
            f"stroke-width='{_fmt(width)}'/>")


def _polygon(points: list[tuple[float, float]], colour: str, opacity: str) -> str:
    text = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in points)
    return f"<polygon points='{text}' fill='{colour}' fill-opacity='{opacity}'/>"


def _convergence(spec: ChartSpec, area: _Box) -> list[str]:
    points = [p for p in spec.series if _number(p.get("mean"))]
    if not points:
        return []
    fmt = _fmt_of(spec)
    means = [float(p["mean"]) for p in points]
    spreads = [2 * float(p["sem"]) if _number(p.get("sem")) else 0.0 for p in points]
    upper = [m + s for m, s in zip(means, spreads, strict=True)]
    lower = [m - s for m, s in zip(means, spreads, strict=True)]
    low, high = nice_range(min(lower), max(upper), 5)
    if fmt == "percent":
        low = max(0.0, low)
        high = min(1.0, high) if max(upper) <= 1.0 else high
    n = len(points)
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(1.0, max(2.0, float(n))),
                   y_range=(low, high), x_fmt="count", y_fmt=fmt,
                   y_label="running mean")

    def pt(i: int, value: float) -> tuple[float, float]:
        return frame.x(i + 1), frame.y(min(high, max(low, value)))

    out.append(_polygon([pt(i, upper[i]) for i in range(n)]
                        + [pt(i, lower[i]) for i in reversed(range(n))],
                        ACCENT, "0.18"))
    out.append(_polyline([pt(i, means[i]) for i in range(n)], ACCENT))
    target = _target(spec)
    if target is not None and low <= target <= high:
        _target_line(out, frame, target, horizontal=True,
                     label=f"target {format_value(target, fmt, 5)}")
    return out


def _curve(spec: ChartSpec, area: _Box) -> list[str]:
    for series in spec.series:
        points = [p for p in series.get("points") or []
                  if _number(p.get("x")) and _number(p.get("y"))]
        if not points:
            continue
        xs = [float(p["x"]) for p in points]
        ys = [float(p["y"]) for p in points]
        out: list[str] = []
        frame = _frame(out, spec, area, x_range=nice_range(min(xs), max(xs), 6),
                       y_range=nice_range(min(ys), max(ys), 5))
        out.append(_polyline([(frame.x(x), frame.y(y))
                              for x, y in zip(xs, ys, strict=True)], ACCENT))
        return out
    return []


def _capacity_pareto(spec: ChartSpec, area: _Box) -> list[str]:
    rows = spec.series
    if not rows:
        return []
    peak = max(max(float(r.get("value") or 0), float(r.get("downtime") or 0))
               for r in rows) or 1.0
    peak = nice_range(0.0, peak, 4)[1]
    gutter = min(area.width * 0.4, max(_width(r["name"]) for r in rows) + 10)
    value_w = _width("100 %") + 10
    plot = _Box(area.left + gutter, area.top,
                max(area.left + gutter + 40, area.right - value_w), area.bottom)
    out: list[str] = []
    frame = _frame(out, spec, plot, x_range=(0.0, peak), y_range=(0, 1),
                   x_fmt="percent", y_ticks=[], y_label="", x_label="share")
    box = frame.box
    band = box.height / len(rows)
    bar_h = max(2.0, min(12.0, band * 0.32))
    for i, row in enumerate(rows):
        top = box.top + i * band + band / 2 - bar_h - 1
        for j, (key, colour) in enumerate((("value", ACCENT), ("downtime", MUTED))):
            value = float(row.get(key) or 0)
            y = top + j * (bar_h + 2)
            out.append(_rect(box.left, y, frame.x(value) - box.left, bar_h, colour))
            out.append(_text(frame.x(value) + 4, y + bar_h - 1,
                             format_value(value, "percent", 3), size=9))
        out.append(_text(area.left + gutter - 8, top + bar_h + 4,
                         _fit(row["name"], gutter - 8), anchor="end"))
    return out


def _fan(spec: ChartSpec, area: _Box) -> list[str]:
    rows = [row for row in spec.series if _number(row.get("p50"))]
    if not rows:
        return []
    fmt = _fmt_of(spec)
    target = _target(spec)
    lows = [float(r["p5"]) for r in rows if _number(r.get("p5"))]
    highs = [float(r["p95"]) for r in rows if _number(r.get("p95"))]
    mids = [float(r["p50"]) for r in rows]
    low = min(lows + mids + ([target] if target is not None else []))
    high = max(highs + mids + ([target] if target is not None else []))
    low, high = nice_range(low, high, 5)
    if fmt == "percent":
        low, high = max(0.0, low), min(1.0, high)
    n = len(rows)
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(1.0, max(2.0, float(n))),
                   y_range=(low, high), x_ticks=nice_ticks(1, max(2, n), min(n, 8)),
                   x_fmt="count", y_fmt=fmt)

    def pt(i: int, value: float) -> tuple[float, float]:
        return frame.x(i + 1), frame.y(value)

    for upper, lower, opacity in (("p95", "p5", "0.14"), ("p75", "p25", "0.28")):
        if all(_number(r.get(upper)) and _number(r.get(lower)) for r in rows):
            out.append(_polygon([pt(i, float(r[upper])) for i, r in enumerate(rows)]
                                + [pt(i, float(rows[i][lower]))
                                   for i in reversed(range(n))], ACCENT, opacity))
    out.append(_polyline([pt(i, mids[i]) for i in range(n)], ACCENT))
    if target is not None:
        _target_line(out, frame, target, horizontal=True,
                     label=f"threshold {format_value(target, fmt, 5)}")
    return out


def _cell_text(out: list[str], x: float, y: float, w: float, h: float,
               cause: str, lines: Sequence[str]) -> None:
    if w < 28 or h < LINE_H + 2:
        return
    colour = CAUSE_COLOURS.get(cause, CAUSE_COLOURS["other"])
    ink = _ink_on(colour)
    top = y + 2
    for line in lines:
        if top + LINE_H > y + h - 1:
            break
        text = _fit(line, w - 10)
        out.append(_rect(x + 2, top, _width(text) + 6, LINE_H, colour))
        out.append(_text(x + 5, top + 11, text, size=10, colour=ink))
        top += LINE_H


def _treemap_amount(spec: ChartSpec, row: dict) -> str:
    """A cell's figure: a share as a percent, money with its share."""
    fmt = _fmt_of(spec)
    text = format_value(float(row["value"]), fmt, 3)
    if fmt != "percent" and _number(row.get("share")):
        text += f" ({100 * float(row['share']):.3g} %)"
    return text


def _treemap(spec: ChartSpec, area: _Box) -> list[str]:
    rows = [row for row in spec.series if float(row.get("w") or 0) > 0]
    if not rows:
        return []
    width = max(float(r["x"]) + float(r["w"]) for r in rows) or 1.0
    height = max(float(r["y"]) + float(r["h"]) for r in rows) or 1.0
    out: list[str] = []
    for row in rows:
        x = area.left + float(row["x"]) / width * area.width
        y = area.top + float(row["y"]) / height * area.height
        w = float(row["w"]) / width * area.width
        h = float(row["h"]) / height * area.height
        cause = str(row.get("cause") or "other")
        out.append(_rect(x, y, w, h, _cause_fill(cause),
                         " stroke='#ffffff' stroke-width='1.5'"))
        _cell_text(out, x, y, w, h, cause, [str(row["name"]), _treemap_amount(spec, row)])
    return out


def _icicle(spec: ChartSpec, area: _Box) -> list[str]:
    rows = [row for row in spec.series if float(row.get("value") or 0) > 0]
    if not rows:
        return []
    columns = max(int(row["column"]) for row in rows) + 1
    body = _Box(area.left, area.top, area.right, area.bottom - LINE_H - 4)
    width = body.width / columns
    total = sum(float(r["value"]) for r in rows if int(r["column"]) == 0) or 1.0
    out: list[str] = []
    for column in range(columns):
        y = body.top
        for row in (r for r in rows if int(r["column"]) == column):
            h = body.height * float(row["value"]) / total
            cause = str(row.get("cause") or "other")
            x = body.left + column * width
            out.append(_rect(x + 1, y + 1, max(1.0, width - 2), max(1.0, h - 2),
                             _cause_fill(cause)))
            _cell_text(out, x + 1, y + 1, width - 2, h - 2, cause,
                       [str(row["label"]),
                        format_value(float(row["value"]), "percent", 3)])
            y += h
        out.append(_text(body.left + column * width + width / 2, body.bottom + 14,
                         "first" if column == 0 else f"then (step {column + 1})",
                         size=10, anchor="middle", colour=MUTED))
    return out


def _ribbon(spec: ChartSpec, area: _Box) -> list[str]:
    rows = [row for row in spec.series if row.get("span")]
    if not rows:
        return []
    start = min(float(row["start"]) for row in rows)
    end = max(float(row["end"]) for row in rows)
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(start, end),
                   y_range=(0.0, float(len(rows))), y_ticks=[],
                   y_label=f"{len(rows)} runs, best at the top")
    box = frame.box
    height = box.height / len(rows)
    for i, row in enumerate(rows):
        y = box.top + i * height
        out.append(_rect(box.left, y, box.width, max(1.0, height - 0.4), UP))
        for a, b in row.get("red") or []:
            x0, x1 = frame.x(float(a)), frame.x(float(b))
            out.append(_rect(x0, y, max(1.0, x1 - x0), max(1.0, height - 0.4), DOWN))
    return out


def _short(value: float) -> str:
    size = abs(value)
    if size >= 1e4:
        return format_value(value, "money")
    if size >= 100:
        return f"{value:,.0f}"
    return f"{value:.3g}"


_RENDERERS: dict[str, Callable[[ChartSpec, _Box], list[str]]] = {
    "dot_plot": _dot_plot,
    "exceedance": _exceedance,
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
           background: str = "#ffffff", text_colour: str = INK) -> str:
    """One ChartSpec as a complete SVG document."""
    body: list[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' "
        f"height='{height}' viewBox='0 0 {width} {height}' "
        f"role='img' aria-label='{_esc(spec.title)}'>",
        f"<rect width='{width}' height='{height}' fill='{background}'/>",
        _text(width / 2, 22, spec.title, size=15, anchor="middle",
              weight="600", colour=text_colour),
        _text(width / 2, 39, spec.subtitle, size=11, anchor="middle",
              colour=MUTED),
        *_cause_defs(),
    ]
    top = float(TITLE_H)
    x = float(PAD)
    for label, fill in _key(spec):
        w = 18 + _width(label) + 14
        if x + w > width - PAD and x > PAD:
            x, top = float(PAD), top + KEY_H
        body.append(_rect(x, top + 3, 14, 11, fill))
        body.append(_text(x + 18, top + 13, label, size=11, colour=text_colour))
        x += w
    if _key(spec):
        top += KEY_H
    area = _Box(PAD, top + 4, width - PAD, height - CAPTION_H)
    renderer = _RENDERERS.get(spec.kind)
    body.extend(renderer(spec, area) if renderer is not None else [])
    if spec.caption:
        body.append(_text(PAD, height - 9, spec.caption, size=11,
                          colour=text_colour))
    body.append("</svg>")
    return "\n".join(body)


def write(spec: ChartSpec, path: str | Any) -> str:
    """Write the SVG and return the path as text."""
    text = render(spec)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return str(path)

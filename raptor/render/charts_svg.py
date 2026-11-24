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
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from raptor.api.insight.attribution import CAUSE_LABELS
from raptor.api.insight.axes import (
    FAN_LINE_MIN, compact, currency, fit_dots, format_value, log_ticks, nice_range, nice_ticks, pct,
    period_axis, place_labels, tick_text,
)
from raptor.api.insight import palette as _palette
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
ACCENT = "#1371BB"
STOP = "#C62828"
UP = "#1E7D57"
DOWN = "#C93939"
DEGRADED = "#BB7E16"
_TONES = {"up": UP, "degraded": DEGRADED, "down": DOWN, "accent": ACCENT,
          "muted": MUTED, "ink": INK}

#: Cause fills, matching the GUI palette; every cause but repair also has a
#: pattern so the chart reads without colour.
class _CauseColours(Mapping[str, str]):
    """Live view of the active palette's light fills."""

    def __getitem__(self, cause: str) -> str:
        return _palette.colour(cause)

    def __iter__(self):
        return iter(_palette.colours())

    def __len__(self) -> int:
        return len(_palette.colours())

    def items(self):
        return _palette.colours().items()


#: The active palette's light fills, kept live for every renderer.
CAUSE_COLOURS = _CauseColours()
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


def _shrink(text: str, width: float) -> str:
    """Text that fits `width`, with an ellipsis when it has to lose letters."""
    if _width(text) <= width:
        return text
    while text and _width(text + "…") > width:
        text = text[:-1]
    return text + "…"


def _width(text: str) -> float:
    return len(str(text)) * CHAR_W


def _tokens(text: str) -> list[tuple[str, bool]]:
    """(piece, starts a new word) - words split after `_ - /` and after a
    dot followed by a letter as well, so an element name such as
    Ground_Control can break at its underscore (but 10.4 never breaks)."""
    out: list[tuple[str, bool]] = []
    for word in str(text).split():
        for i, piece in enumerate(re.split(r"(?<=[_\-/])(?=.)|(?<=\.)(?=[A-Za-z])", word)):
            out.append((piece, i == 0))
    return out


def _wrap(text: str, width: float, *, size: int = 11) -> list[str]:
    """`text` broken into lines no wider than `width`, never shortened.

    Saved charts go into reports and slides, where a name cut to "Gro…"
    is useless: a name too long for its space takes more lines instead
    (and a single piece longer than a line breaks at the line's edge)."""
    room = max(1, int(width / (CHAR_W * size / 11)))
    lines: list[str] = []
    line = ""
    for piece, new_word in _tokens(text):
        while len(piece) > room:
            if line:
                lines.append(line)
                line = ""
            lines.append(piece[:room])
            piece = piece[room:]
        joined = (f"{line} {piece}" if new_word else line + piece) if line else piece
        if len(joined) <= room:
            line = joined
        else:
            lines.append(line)
            line = piece
    if line:
        lines.append(line)
    return lines or [""]


def _darker(colour: str) -> str:
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{int(r * 0.6):02x}{int(g * 0.6):02x}{int(b * 0.6):02x}"


def _luminance(colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _ink_on(colour: str) -> str:
    """The near-black or white ink with the higher WCAG contrast on `colour`."""
    return max(("#111111", "#ffffff"), key=lambda ink: _contrast(colour, ink))


def _cause_fill(cause: str) -> str:
    cause = cause if cause in CAUSE_COLOURS else "other"
    return (f"url(#pat-{cause})" if _PATTERN_PATHS.get(cause)
            else _palette.colour(cause))


def _cause_defs() -> list[str]:
    out = ["<defs>"]
    for cause in CAUSE_COLOURS:
        colour = _palette.colour(cause)
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


def _lines(x: float, y: float, lines: Sequence[str], *, size: int = 11,
           anchor: str = "start", weight: str = "400", colour: str = INK,
           step: float = LINE_H) -> str:
    """Several lines, one text element each (no tspans, which SVG Tiny
    renderers and slide software place inconsistently); `y` is the first
    line's baseline."""
    return "\n".join(_text(x, y + i * step, line, size=size, anchor=anchor,
                           weight=weight, colour=colour)
                     for i, line in enumerate(lines))


def _name_lines(out: list[str], right: float, middle: float, name: str,
                width: float) -> None:
    """An element name right-aligned in a gutter, wrapped, centred on `middle`."""
    lines = _wrap(name, width)
    first = middle + 4 - (len(lines) - 1) * LINE_H / 2
    out.append(_lines(right, first, lines, anchor="end"))


def _gutter(area: _Box, names: Sequence[str], rows: int, share: float = 0.4) -> float:
    """A name gutter: as wide as the longest name, at most `share` of the
    width, widened to half when the names would need more lines than a row
    has room for."""
    natural = max((_width(n) for n in names), default=0.0) + 10
    gutter = min(area.width * share, natural)
    band = (area.height - 3 * LINE_H) / max(1, rows)
    fits = max(1, int(band / LINE_H))
    if any(len(_wrap(n, gutter - 8)) > fits for n in names):
        gutter = min(area.width * 0.5, natural)
    return gutter


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


def _target_word(spec: ChartSpec) -> str:
    """"target" for the Charts page's target, "threshold" for a summary's."""
    return next((str(m["label"]) for m in spec.marks
                 if m.get("kind") == "target" and m.get("label")), "threshold")


def _target_line(out: list[str], frame: _Frame, value: float, *,
                 horizontal: bool, label: str) -> None:
    box = frame.box
    if not label:
        if horizontal:
            out.append(_line(box.left, frame.y(value), box.right, frame.y(value),
                             STOP, width=1.6, dash="5,3"))
        else:
            out.append(_line(frame.x(value), box.top, frame.x(value), box.bottom,
                             STOP, width=1.6, dash="5,3"))
        return
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


def _quartile_lines(out: list[str], spec: ChartSpec, frame: _Frame, *,
                    at_share: bool) -> None:
    """U16: the GUI's dotted quartile lines, labelled along the top."""
    box = frame.box
    fmt = _fmt_of(spec)
    last_right, row = -1e9, 0
    for mark in spec.marks:
        if mark.get("kind") != "quartile" or not _number(mark.get("value")):
            continue
        x = frame.x(float(mark["share"]) if at_share else float(mark["value"]))
        out.append(_line(x, box.top, x, box.bottom, MUTED, width=1.0, dash="2,3"))
        label = (f"{mark.get('label', '')}: " if at_share else f"{mark.get('label', '')} ") \
            + format_value(float(mark["value"]), fmt, 4)
        w = _width(label) + 6
        left = min(max(box.left, x - w / 2), box.right - w)
        row = row + 1 if left < last_right + 4 else 0
        top = box.top + LINE_H * (1 + row) + 2
        out.append(_rect(left, top, w, LINE_H, "#ffffff"))
        out.append(_text(left + w / 2, top + 11, label, size=10, anchor="middle",
                         colour=MUTED))
        last_right = left + w


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
    if spec.provenance.get("x_range"):
        low, high = (float(v) for v in spec.provenance["x_range"])
    else:
        low, high = nice_range(
            min(values + ([target] if target is not None else [])),
            max(values + ([target] if target is not None else [])), 5,
            share=fmt == "percent")
        pad = max(1e-9, (high - low) * 0.03)
        low, high = low - pad, high + pad
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(low, high), y_range=(0, 1),
                   x_fmt=fmt, y_ticks=[], y_label="", y_axis=False)
    box = frame.box
    _quartile_lines(out, spec, frame, at_share=False)
    diameter, levels = fit_dots([frame.x(v) for v in values], box.height * 0.85)
    radius = diameter / 2 - 0.6
    for value, (x, level) in zip(values, levels, strict=True):
        y = box.bottom - diameter / 2 - level * diameter
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
    if spec.provenance.get("x_range"):
        low, high = (float(v) for v in spec.provenance["x_range"])
    else:
        low, high = nice_range(
            min(values + ([target] if target is not None else [])),
            max(values + ([target] if target is not None else [])), 5,
            share=fmt == "percent")
        pad = max(1e-9, (high - low) * 0.03)
        low, high = low - pad, high + pad
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(0.0, 1.0), y_range=(low, high),
                   x_fmt="percent", y_fmt=fmt,
                   x_ticks=[i / 10 for i in range(11)])
    _quartile_lines(out, spec, frame, at_share=True)
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
                           f"{pct(met)} of runs reach it")
    return out


_FIXED_KEYS = {"capacity_pareto": [("lost capacity", ACCENT), ("downtime", MUTED)],
               "ribbon": [("system up", UP), ("system down", DOWN)]}


def _swatch(x: float, y: float, fill: str, colour: str) -> str:
    """One key swatch. `fill` is a colour, or "line:<colour>[:dash]",
    "area:<colour>:<opacity>" or "whisker" for what is drawn that way."""
    if fill == "whisker":
        mid = y + 8.5
        return (f"<path d='M{x:.1f},{mid:.1f}h14M{x:.1f},{mid - 4:.1f}v8"
                f"M{x + 14:.1f},{mid - 4:.1f}v8' stroke='{colour}' "
                "stroke-width='1.2' fill='none'/>")
    if fill.startswith("line:"):
        parts = fill.split(":")
        dash = " stroke-dasharray='4,2'" if parts[2:] == ["dash"] else ""
        return (f"<line x1='{x:.1f}' y1='{y + 8.5:.1f}' x2='{x + 14:.1f}' "
                f"y2='{y + 8.5:.1f}' stroke='{parts[1]}' stroke-width='2'{dash}/>")
    if fill.startswith("area:"):
        _, tone, opacity = fill.split(":")
        return (f"<rect x='{x:.1f}' y='{y + 3:.1f}' width='14' height='11' "
                f"fill='{tone}' fill-opacity='{opacity}'/>")
    return _rect(x, y + 3, 14, 11, fill)


def _key(spec: ChartSpec) -> list[tuple[str, str]]:  # noqa: C901, PLR0912
    """(label, fill) for every cause, series or layer the chart shows
    (`_swatch` says how a fill is drawn)."""
    if spec.kind in _FIXED_KEYS:
        return list(_FIXED_KEYS[spec.kind])
    if spec.kind == "sunburst":
        from raptor.api.insight.hierarchy import SERIES_COLOURS
        roots = [node for node in spec.series if not node.get("parent")]
        roots.sort(key=lambda node: -float(node.get("value") or 0.0))
        return [(str(node.get("name") or ""),
                 SERIES_COLOURS[i % len(SERIES_COLOURS)])
                for i, node in enumerate(roots)]
    if spec.kind == "timeline":
        # the bands share a colour and differ in opacity: two plain swatches
        # of one colour could not be told apart
        from raptor.api.insight.timecharts import in_key
        out = []
        for layer in filter(in_key, spec.series):
            tone = layer.get("colour") or _TONES.get(str(layer.get("tone")), ACCENT)
            if layer.get("role") == "area":
                alpha = layer.get("alpha")
                fill = f"area:{tone}:{_fmt(float(alpha)) if isinstance(alpha, (int, float)) else '0.85'}"
            else:
                fill = f"line:{tone}" + (":dash" if layer.get("dash") else "")
            out.append((str(layer.get("name", "")), fill))
        return out
    seen: list[str] = []
    if spec.kind == "pareto":
        causes = [str(c) for row in spec.series for c in (row.get("causes") or {})]
    elif spec.kind in ("waterfall", "treemap", "icicle"):
        # a cell with no cause is drawn as Other, so Other is in the key
        causes = [str(row.get("cause") or "other") for row in spec.series
                  if not row.get("is_group")]
    elif spec.kind == "sankey":
        causes = [str(link.get("cause")) for link in spec.series
                  if link.get("cause")]
    else:
        causes = []
    for cause in causes:
        if cause not in seen:
            seen.append(cause)
    labels = {**CAUSE_LABELS, **(spec.provenance.get("group_labels") or {})}
    out = [(labels.get(c, c), _cause_fill(c)) for c in seen]
    if spec.kind == "pareto" and any(_number(r.get("sem")) for r in spec.series):
        out.append(("± 2 standard errors", "whisker"))   # the whiskers, explained
    return out


def _bars(spec: ChartSpec, area: _Box, rows: list[dict], *, fmt: str,
          labels: list[str], peak: float, x_label: str,
          draw: Callable[[list[str], _Frame, dict, float, float], float | None],
          ) -> list[str]:
    """Horizontal bars with names in a gutter and a value after each bar."""
    gutter = _gutter(area, [str(r["name"]) for r in rows], len(rows))
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
        _name_lines(out, area.left + gutter - 8, top + bar_h / 2, str(row["name"]),
                    gutter - 8)
        out.append(_text(box.left + (end or 0.0) + 6, top + bar_h / 2 + 4, label,
                         size=10))
    return out


def _pareto(spec: ChartSpec, area: _Box) -> list[str]:  # noqa: C901
    rows = [r for r in spec.series if _number(r.get("value"))]
    if not rows:
        return []
    fmt = _fmt_of(spec)
    money = fmt == "money"

    def label(row: dict) -> str:
        if money:
            text = format_value(float(row["value"]), "money")
            if _number(row.get("share")):
                text += f" ({pct(float(row['share']))})"
            return text
        text = format_value(float(row["value"]), fmt, 3)
        if _number(row.get("availability")):
            text += f" (availability {format_value(float(row['availability']), 'percent', 5)})"
        if row.get("mission") is False:
            text += " (non-mission)"
        if row.get("entered") is False:
            text += " (not entered)"
        return text

    # a share's uncertainty range stops at 0 and 100 %, and so does its axis
    ceiling = 1.0 if fmt == "percent" and all(float(r["value"]) <= 1.0
                                               for r in rows) else math.inf
    peak = max(min(ceiling, float(r["value"]) + 2 * float(r.get("sem") or 0))
               for r in rows)
    peak = min(ceiling, nice_range(0.0, peak or 1.0, 4)[1])

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
            hi = frame.x(min(ceiling, float(row["value"]) + 2 * sem))
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
    if value >= 1e-3:
        # three figures, as the caption: "-25 pts" sat beside "24.7 points"
        return f"{value:.3g}"
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
    column_w = (area.width - 60) / columns
    names = [_wrap(str(s["name"]), column_w - 6, size=10) for s in steps]
    extra = (max(len(n) for n in names) - 1) * 12
    area = _Box(area.left, area.top, area.right, area.bottom - extra)
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
                else f"{pct(float(step['value']))}")
        if max(y0, y1) + 16 < box.bottom:
            out.append(_text(x + width / 2, max(y0, y1) + 14, drop, size=10,
                             anchor="middle"))
        elif abs(y0 - y1) < LINE_H + 6:
            # a sliver on the axis: above it, clear of the column name
            out.append(_text(x + width / 2, min(y0, y1) - 4, drop, size=10,
                             anchor="middle"))
        else:
            # inside the bar: on a plate of the bar's colour, so the hatch
            # never runs through the figure
            fill = _palette.colour(str(step.get("cause")))
            out.append(_rect(x + width / 2 - _width(drop) / 2 - 3, min(y0, y1) + 3,
                             _width(drop) + 6, LINE_H, fill))
            out.append(_text(x + width / 2, min(y0, y1) + 14, drop, size=10,
                             anchor="middle", colour=_ink_on(fill)))
        out.append(_lines(x + width / 2, box.bottom + 15, names[i], size=10,
                          anchor="middle", step=12))
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
    low, high = nice_range(min(values) - 0.6 * span, top_value, 4,
                           share=top_value <= 1.0)
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
    gutter = _gutter(area, [str(r["name"]) for r in rows], len(rows), 0.45)
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
        _name_lines(out, area.left + gutter - 8, top + bar_h / 2, str(row["name"]),
                    gutter - 8)
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
    last_right = -1e9
    for mark in spec.marks:
        if mark.get("kind") == "quantile" and _number(mark.get("value")):
            value = float(mark["value"])
            label = str(mark.get("label") or "")
            shown = _short(value)
            if shown not in label:
                label = f"{label} {shown}".strip()
            x = frame.x(value)
            width = _width(label) + 8
            if x - last_right < width + 4:
                label = ""
            else:
                last_right = x + width
            _target_line(out, frame, value, horizontal=False, label=label)
    return out


def _scatter_frame(out: list[str], spec: ChartSpec, area: _Box, points: list[dict], *,
                   square: bool = False, include: Sequence[tuple[float, float]] = (),
                   x_fmt: str | None = None, y_fmt: str | None = None) -> _Frame:
    xs = [float(p["x"]) for p in points] + [x for x, _y in include]
    ys = [float(p["y"]) for p in points] + [y for _x, y in include]
    fmt = _fmt_of(spec)
    x_high = nice_range(0.0, max(xs) * 1.08 or 1.0, 5)[1]
    y_high = nice_range(0.0, max(ys) * 1.12 or 1.0, 5)[1]
    if fmt == "percent":
        # shares stop at 100 % (the axes ran to 125 %)
        x_high = min(x_high, 1.0) if max(xs) <= 1.0 else x_high
        y_high = min(y_high, 1.0) if max(ys) <= 1.0 else y_high
    if square:
        x_high = y_high = max(x_high, y_high)
    if spec.provenance.get("x_log") and min(xs) > 0:
        lo, hi = min(xs) / 1.5, max(xs) * 1.5
        return _frame(out, spec, area, x_range=(lo, hi), y_range=(0.0, y_high),
                      x_log=True, x_ticks=log_ticks(lo, hi),
                      x_label=f"{spec.x_label} (log scale)",
                      x_fmt=fmt if x_fmt is None else x_fmt,
                      y_fmt=fmt if y_fmt is None else y_fmt)
    return _frame(out, spec, area, x_range=(0.0, x_high), y_range=(0.0, y_high),
                  x_fmt=fmt if x_fmt is None else x_fmt,
                  y_fmt=fmt if y_fmt is None else y_fmt)


def _label_points(out: list[str], frame: _Frame,
                  items: list[tuple[float, float, float, str]]) -> None:
    """Every point's label, none on top of another; a crowded one moves out
    on a thin leader to its dot."""
    box = frame.box
    placed = place_labels([(x, y, r) for x, y, r, _t in items],
                          [(_width(t) + 4, 12.0) for *_xy, t in items],
                          (box.left, box.top, box.width, box.bottom - box.top))
    for (x, y, radius, text), (rect, end) in zip(items, placed, strict=True):
        if end is not None:
            dx, dy = end[0] - x, end[1] - y
            dist = max(1e-6, (dx * dx + dy * dy) ** 0.5)
            out.append(_line(x + dx / dist * radius, y + dy / dist * radius,
                             end[0], end[1], MUTED))
        out.append(_text(rect[0] + rect[2] / 2, rect[1] + 10, text, size=10,
                         anchor="middle"))


def _xy(spec: ChartSpec) -> list[dict]:
    return [p for p in spec.series if _number(p.get("x")) and _number(p.get("y"))]


def _bubble(spec: ChartSpec, area: _Box) -> list[str]:
    points = _xy(spec)
    if not points:
        return []
    out: list[str] = []
    frame = _scatter_frame(out, spec, area, points,
                           x_fmt=spec.provenance.get("x_format", "percent"), y_fmt="")
    area_peak = max(float(p.get("area") or 0) for p in points) or 1.0
    labels = []
    for point in sorted(points, key=lambda p: -float(p.get("area") or 0)):
        x, y = frame.x(float(point["x"])), frame.y(float(point["y"]))
        radius = 4 + 20 * (float(point.get("area") or 0) / area_peak) ** 0.5
        out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(y)}' r='{_fmt(radius)}' "
                   f"fill='{ACCENT}' fill-opacity='0.43' stroke='{ACCENT}'/>")
        labels.append((x, y, radius, str(point["name"])))
    _label_points(out, frame, labels)
    return out


def _dots(out: list[str], frame: _Frame, points: list[dict]) -> None:
    labels = []
    for point in points:
        x, y = frame.x(float(point["x"])), frame.y(float(point["y"]))
        out.append(f"<circle cx='{_fmt(x)}' cy='{_fmt(y)}' r='4.5' fill='{ACCENT}'/>")
        labels.append((x, y, 5.0, str(point["name"])))
    _label_points(out, frame, labels)


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
    # a share's +/- 2 SE band stops at 0 and 100 % (it drew a 125 % tick)
    low, high = nice_range(min(lower), max(upper), 5,
                           share=fmt == "percent" and max(means) <= 1.0)
    n = len(points)
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(1.0, max(2.0, float(n))),
                   y_range=(low, high), x_fmt="count", y_fmt=fmt,
                   # whole runs only: one run gave ticks 1.2, 1.4
                   x_ticks=[t for t in nice_ticks(1.0, max(2.0, float(n)), 6)
                            if float(t).is_integer()],
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
    median = next((float(m["value"]) for m in spec.marks
                   if m.get("kind") == "median" and _number(m.get("value"))),
                  None)
    if median is not None and low <= median <= high:
        y = frame.y(median)
        out.append(_line(frame.box.left, y, frame.box.right, y, MUTED,
                         width=1.2, dash="4,3"))
        label = f"median {format_value(median, fmt, 5)}"
        out.append(_text(frame.box.left + 4, y - 4, label, size=9))
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
    gutter = _gutter(area, [str(r["name"]) for r in rows], len(rows))
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
        _name_lines(out, area.left + gutter - 8, top + bar_h + 1, str(row["name"]),
                    gutter - 8)
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
    low, high = nice_range(low, high, 5, share=fmt == "percent")
    if fmt == "percent":
        low, high = max(0.0, low), min(1.0, high)
    n = len(rows)
    out: list[str] = []
    x_range, x_ticks = period_axis(n)
    frame = _frame(out, spec, area, x_range=x_range,
                   y_range=(low, high), x_ticks=x_ticks,
                   x_fmt="count", y_fmt=fmt)
    if n < FAN_LINE_MIN:
        out.extend(_period_boxes(rows, frame))
    else:
        _fan_bands(out, rows, frame)
    if target is not None:
        _target_line(out, frame, target, horizontal=True,
                     label=f"{_target_word(spec)} {format_value(target, fmt, 5)}")
    return out


def _period_boxes(rows: list[dict[str, Any]], frame: Any) -> list[str]:
    """One box per period: no line to suggest a trend between them."""
    out: list[str] = []
    half = min(40.0, (frame.x(2) - frame.x(1)) * 0.3)
    for i, r in enumerate(rows):
        x = frame.x(i + 1)
        if _number(r.get("p5")) and _number(r.get("p95")):
            out.append(_line(x, frame.y(float(r["p95"])), x, frame.y(float(r["p5"])),
                             ACCENT, width=1.5))
        if _number(r.get("p25")) and _number(r.get("p75")):
            top, bottom = frame.y(float(r["p75"])), frame.y(float(r["p25"]))
            out.append(_rect(x - half, top, 2 * half, bottom - top, ACCENT,
                             " fill-opacity='0.35'"))
        y = frame.y(float(r["p50"]))
        out.append(_line(x - half, y, x + half, y, ACCENT, width=2.5))
    return out


def _fan_bands(out: list[str], rows: list[dict[str, Any]], frame: Any) -> None:
    n = len(rows)
    mids = [float(r["p50"]) for r in rows]

    def pt(i: int, value: float) -> tuple[float, float]:
        return frame.x(i + 1), frame.y(value)

    for upper, lower, opacity in (("p95", "p5", "0.14"), ("p75", "p25", "0.28")):
        if all(_number(r.get(upper)) and _number(r.get(lower)) for r in rows):
            out.append(_polygon([pt(i, float(r[upper])) for i, r in enumerate(rows)]
                                + [pt(i, float(rows[i][lower]))
                                   for i in reversed(range(n))], ACCENT, opacity))
    out.append(_polyline([pt(i, mids[i]) for i in range(n)], ACCENT))


def _timeline(spec: ChartSpec, area: _Box) -> list[str]:  # noqa: C901 - one pass per layer kind
    """Areas and lines over simulated time (`raptor.api.insight.timecharts`)."""
    layers = [layer for layer in spec.series if layer.get("t")]
    if not layers:
        return []
    fmt = _fmt_of(spec)
    t_start = min(float(layer["t"][0]) for layer in layers)
    t_end = max(float(layer["t"][-1]) for layer in layers)
    low, high = (float(v) for v in (spec.provenance.get("y_range") or [0.0, 1.0])[:2])
    if high <= low:
        high = low + 1.0
    if fmt != "percent" and not spec.provenance.get("y_axis_exact"):
        low, high = nice_range(low, high, 5)
    ticks = nice_ticks(low, high, 5)
    if fmt == "percent" and high >= 1.0:
        # room above 100 %: a run that never went down drew its line on
        # the frame's top edge, and the chart looked empty
        high += (high - low) * 0.04
    out: list[str] = []
    frame = _frame(out, spec, area, x_range=(t_start, max(t_end, t_start + 1e-9)),
                   y_range=(low, high), y_fmt=fmt, y_ticks=ticks)

    def pt(t: float, value: float) -> tuple[float, float]:
        return frame.x(t), frame.y(min(high, max(low, value)))

    for layer in layers:
        ts = [float(x) for x in layer["t"]]
        colour = layer.get("colour") or _TONES.get(str(layer.get("tone")), ACCENT)
        if layer.get("role") == "area":
            alpha = layer.get("alpha")
            out.append(_polygon(
                [pt(t, float(v)) for t, v in zip(ts, layer["hi"], strict=True)]
                + [pt(t, float(v)) for t, v in zip(reversed(ts), reversed(layer["lo"]),
                                                    strict=True)],
                colour, _fmt(float(alpha)) if isinstance(alpha, (int, float)) else "0.85"))
            continue
        ys = [float(v) for v in layer["y"]]
        if layer.get("fill"):
            out.append(_polygon([pt(t, v) for t, v in zip(ts, ys, strict=True)]
                                + [pt(ts[-1], low), pt(ts[0], low)], colour, "0.16"))
        line = _polyline([pt(t, v) for t, v in zip(ts, ys, strict=True)], colour,
                         float(layer.get("width") or 1.5))
        if layer.get("dash"):
            line = line.replace("/>", " stroke-dasharray='5,3'/>")
        out.append(line)
    target = _target(spec)
    if target is not None and low <= target <= high:
        _target_line(out, frame, target, horizontal=True,
                     label=f"target {format_value(target, fmt, 5)}")
    median = next((float(m["value"]) for m in spec.marks
                   if m.get("kind") == "median" and _number(m.get("value"))),
                  None)
    if median is not None and low <= median <= high:
        y = frame.y(median)
        out.append(_line(frame.box.left, y, frame.box.right, y, MUTED,
                         width=1.2, dash="4,3"))
        label = f"median {format_value(median, fmt, 5)}"
        out.append(_text(frame.box.left + 4, y - 4, label, size=9))
    return out


@dataclass
class _Unlabelled:
    """A cell too small for its name: a number in the cell (when even that
    fits) and an entry in the footnote under the chart."""
    text: str
    share: float
    marker: tuple[float, float] | None = None
    number: int = 0


def _cell_text(out: list[str], x: float, y: float, w: float, h: float,
               cause: str, lines: Sequence[str]) -> bool:
    """Label a cell in full (name wrapped, then the amount) on a plate of
    the cell's colour.  Returns False, drawing nothing, when the whole label
    does not fit: a cell is never labelled with a shortened name."""
    if w < 28:
        return False
    wrapped = [part for line in lines for part in _wrap(line, w - 10, size=10)]
    if len(wrapped) * LINE_H + 3 > h:
        return False
    colour = _palette.colour(cause)
    ink = _ink_on(colour)
    top = y + 2
    for text in wrapped:
        out.append(_rect(x + 2, top, _width(text) * 10 / 11 + 6, LINE_H, colour))
        out.append(_text(x + 5, top + 11, text, size=10, colour=ink))
        top += LINE_H
    return True


def _footnote(missing: list[_Unlabelled], limit: int) -> list[str]:
    """The footnote's entries: every numbered cell, then the others by size
    while they are at least 0.5 % and there are fewer than `limit`, then one
    entry summing the rest (so the footnote never outgrows the chart)."""
    entries: list[str] = []
    rest: list[_Unlabelled] = []
    for item in sorted(missing, key=lambda m: (m.number == 0, m.number, -m.share)):
        if len(entries) < limit and (item.number or item.share >= 0.005):
            entries.append(f"({item.number}) {item.text}" if item.number else item.text)
        else:
            item.number = 0          # not in the footnote: no number drawn
            rest.append(item)
    if rest:
        entries.append(f"+ {len(rest)} smaller cells, together "
                       f"{format_value(sum(m.share for m in rest), 'percent', 3)}")
    return entries


def _pack(entries: list[str], width: float, *, size: int = 10,
          lead: str = "Not labelled in the chart:") -> list[str]:
    """Entries joined by " · " into lines no wider than `width`, an entry
    never split across lines unless it is wider than a line by itself."""
    room = width / (CHAR_W * size / 11)
    lines, line = [], lead
    for entry in entries:
        joined = f"{line} {entry}" if line == lead else f"{line} · {entry}"
        if len(joined) <= room:
            line = joined
        elif len(entry) > room:
            lines.append(line)
            *full, line = _wrap(entry, width, size=size)
            lines.extend(full)
        else:
            lines.append(line)
            line = entry
    lines.append(line)
    return lines


def _with_footnote(area: _Box,  # noqa: C901 - fit, then fall back in steps
                   draw: Callable[[_Box], tuple[list[str], list[_Unlabelled]]],
                   limit: int = 40) -> list[str]:
    """Draw cells in `area` less room for the footnote naming the cells too
    small to label, re-drawing until the footnote's height settles."""
    # The footnote keeps at most about a third of the height: with 186
    # cells it took the whole page and the cells were drawn 0 high.
    max_lines = max(2, int(area.height * 0.3 // LINE_H))

    def numbered(found: list[_Unlabelled]) -> None:
        number = 0
        for item in found:
            item.number = 0
            if item.marker is not None:
                number += 1
                item.number = number

    def footnote(found: list[_Unlabelled]) -> list[str]:
        cap = limit
        while True:
            numbered(found)
            packed = _pack(_footnote(found, cap), area.width) if found else []
            if len(packed) <= max_lines or cap <= 1:
                return packed
            cap = max(1, cap * 2 // 3)

    lines: list[str] = []
    out: list[str] = []
    missing: list[_Unlabelled] = []
    for _attempt in range(4):
        body = _Box(area.left, area.top, area.right,
                    area.bottom - (len(lines) * LINE_H + 6 if lines else 0))
        out, missing = draw(body)
        new = footnote(missing)
        if len(new) <= len(lines):
            break
        lines = new
    lines = footnote(missing)
    for item in missing:
        if item.marker is not None and item.number:
            mx, my = item.marker
            label = str(item.number)
            w = _width(label) * 9 / 11 + 6
            out.append(_rect(mx + 1, my + 1, w, 12, "#ffffff",
                             " fill-opacity='0.92' rx='2'"))
            out.append(_text(mx + 1 + w / 2, my + 10.5, label, size=9,
                             anchor="middle", colour=INK))
    if lines:
        out.append(_lines(area.left, area.bottom - len(lines) * LINE_H + 10, lines,
                          size=10, colour=MUTED))
    return out


def _treemap_amount(spec: ChartSpec, row: dict) -> str:
    """A cell's figure: a share as a percent, money with its share."""
    fmt = _fmt_of(spec)
    text = format_value(float(row["value"]), fmt, 3)
    if fmt != "percent" and _number(row.get("share")):
        text += f" ({pct(float(row['share']))})"
    return text


def _share(row: dict, total: float) -> float:
    if _number(row.get("share")):
        return float(row["share"])
    return float(row.get("value") or 0.0) / total if total else 0.0


def _treemap(spec: ChartSpec, area: _Box) -> list[str]:
    rows = [row for row in spec.series if float(row.get("w") or 0) > 0]
    if not rows:
        return []
    width = max(float(r["x"]) + float(r["w"]) for r in rows) or 1.0
    height = max(float(r["y"]) + float(r["h"]) for r in rows) or 1.0
    total = sum(float(r.get("value") or 0.0) for r in rows)

    def draw(body: _Box) -> tuple[list[str], list[_Unlabelled]]:
        out: list[str] = []
        missing: list[_Unlabelled] = []
        group_labels: list[tuple[float, float, str, float]] = []
        for row in rows:
            x = body.left + float(row["x"]) / width * body.width
            y = body.top + float(row["y"]) / height * body.height
            w = float(row["w"]) / width * body.width
            h = float(row["h"]) / height * body.height
            if row.get("is_group"):
                # a hierarchy group is a background frame, not a cause cell:
                # its children carry the cause colours and the area
                out.append(_rect(x, y, w, h, "#EDF2F7",
                                 " stroke='#8FA3B5' stroke-width='1.3'"))
                if h >= 18 and w >= 24:
                    group_labels.append((x + 6, y + 15, str(row["name"]),
                                         w - 8))
                continue
            cause = str(row.get("cause") or "other")
            out.append(_rect(x, y, w, h, _cause_fill(cause),
                             " stroke='#ffffff' stroke-width='1.5'"))
            amount = _treemap_amount(spec, row)
            if not _cell_text(out, x, y, w, h, cause, [str(row["name"]), amount]):
                missing.append(_Unlabelled(
                    f"{row['name']}: {amount}", _share(row, total),
                    (x, y) if w >= 16 and h >= 14 else None))
        for x, y, name, label_w in group_labels:
            name = _shrink(name, label_w - 6)
            out.append(_rect(x - 2, y - 10, _width(name) + 6, 15, "#EDF2F7"))
            out.append(_text(x, y + 2, name, size=11, weight="600", colour=INK))
        return out, missing

    return _with_footnote(area, draw)


def _icicle(spec: ChartSpec, area: _Box) -> list[str]:
    rows = [row for row in spec.series if float(row.get("value") or 0) > 0]
    if not rows:
        return []
    columns = max(int(row["column"]) for row in rows) + 1
    total = sum(float(r["value"]) for r in rows if int(r["column"]) == 0) or 1.0

    def draw(region: _Box) -> tuple[list[str], list[_Unlabelled]]:
        body = _Box(region.left, region.top, region.right, region.bottom - LINE_H - 4)
        width = body.width / columns
        out: list[str] = []
        missing: list[_Unlabelled] = []
        for column in range(columns):
            y = body.top
            for row in (r for r in rows if int(r["column"]) == column):
                h = body.height * float(row["value"]) / total
                cause = str(row.get("cause") or "other")
                x = body.left + column * width
                out.append(_rect(x + 1, y + 1, max(1.0, width - 2), max(1.0, h - 2),
                                 _cause_fill(cause)))
                amount = format_value(float(row["value"]), "percent", 3)
                if not _cell_text(out, x + 1, y + 1, width - 2, h - 2, cause,
                                  [str(row["label"]), amount]):
                    missing.append(_Unlabelled(
                        f"step {column + 1} {row['label']}: {amount}",
                        float(row["value"]) / total,
                        (x + 1, y + 1) if h - 2 >= 14 else None))
                y += h
            if not (columns == 1 and spec.provenance.get("single_column")):
                out.append(_text(body.left + column * width + width / 2, body.bottom + 14,
                                 "first" if column == 0 else f"then (step {column + 1})",
                                 size=10, anchor="middle", colour=MUTED))
        return out, missing

    return _with_footnote(area, draw, limit=8)


def _ribbon(spec: ChartSpec, area: _Box) -> list[str]:
    rows = [row for row in spec.series if row.get("span")]
    if not rows:
        return []
    start = min(float(row["start"]) for row in rows)
    end = max(float(row["end"]) for row in rows)
    out: list[str] = []
    labels = [str(row.get("label") or row.get("name") or "") for row in rows
              if row.get("name")]
    gutter = min(180.0, max((_width(name) for name in labels), default=0.0) + 18)
    plot = _Box(area.left + gutter, area.top, area.right, area.bottom)
    frame = _frame(out, spec, plot, x_range=(start, end),
                   y_range=(0.0, float(len(rows))), y_ticks=[],
                   y_label=(str(spec.y_label) if any(row.get("name") for row in rows)
                            else f"{len(rows)} of {spec.provenance['runs']:,} runs, best at the top"
                            if isinstance(spec.provenance.get("runs"), int)
                            and spec.provenance["runs"] > len(rows)
                            else f"{len(rows)} runs, best at the top"))
    box = frame.box
    height = box.height / len(rows)
    for i, row in enumerate(rows):
        y = box.top + i * height
        out.append(_rect(box.left, y, box.width, max(1.0, height - 0.4), UP))
        for a, b in row.get("red") or []:
            x0, x1 = frame.x(float(a)), frame.x(float(b))
            out.append(_rect(x0, y, max(1.0, x1 - x0), max(1.0, height - 0.4), DOWN))
        if row.get("name"):
            out.append(_text(area.left + gutter - 6, y + height / 2 + 4,
                             _shrink(str(row.get("label") or row["name"]),
                                     gutter - 10), size=9, anchor="end"))
    return out


def _delta(spec: ChartSpec, area: _Box) -> list[str]:
    rows = spec.series
    if not rows:
        return []
    values = [float(row.get("value") or 0.0) for row in rows]
    if not any(abs(value) > 1e-12 for value in values):
        return []
    bound = max(max(abs(v) for v in values), 1e-9)
    bound = nice_range(-bound, bound, 4)[1]
    labels = [str(row.get("name") or "") for row in rows]
    gutter = min(area.width * 0.4, _gutter(area, labels, len(rows)))
    out: list[str] = []
    fmt = "percent" if spec.provenance.get("format") == "percent" else ""
    frame = _frame(out, spec, area, x_range=(-bound, bound),
                   y_range=(0.0, float(len(rows))), y_ticks=[],
                   x_fmt=fmt, x_label=spec.x_label,
                   y_label="hierarchy")
    box = frame.box
    band = box.height / len(rows)
    bar_h = max(3.0, min(16.0, band * 0.45))
    zero = frame.x(0.0)
    out.append(_line(zero, box.top, zero, box.bottom, MUTED, width=1.2,
                     dash="4,3"))
    for i, row in enumerate(rows):
        value = float(row.get("value") or 0.0)
        y = box.top + i * band + (band - bar_h) / 2
        x = frame.x(value)
        colour = DOWN if value > 0 else UP if value < 0 else MUTED
        out.append(_rect(min(zero, x), y, max(1.0, abs(x - zero)), bar_h, colour))
        sem = float(row["sem"]) if _number(row.get("sem")) else 0.0
        if sem > 0:
            lo, hi = frame.x(value - 2 * sem), frame.x(value + 2 * sem)
            mid = y + bar_h / 2
            out.append(_line(lo, mid, hi, mid, INK, width=1.2))
            out.append(_line(lo, mid - 4, lo, mid + 4, INK, width=1.2))
            out.append(_line(hi, mid - 4, hi, mid + 4, INK, width=1.2))
        units = str(spec.provenance.get("units") or "")
        unit = " h" if units.lower().startswith("hour") else (
            f" {units}" if units else "")
        text = format_value(value, fmt, 3) + unit
        left = x + 4 if value >= 0 else x - _width(text) - 4
        out.append(_text(left, y + bar_h - 2, text, size=10,
                         anchor="start" if value >= 0 else "end"))
        out.append(_text(area.left + gutter - 8, y + bar_h - 2,
                         _shrink(str(row.get("name") or ""), gutter - 10),
                         size=10, anchor="end"))
    return out


def _heat_colour(value: float, largest: float) -> str:
    """A sequential blue from the surface to the accent for 0..largest."""
    if largest <= 0:
        return "#F7FAFC"
    share = max(0.0, min(1.0, value / largest))
    lo = (247, 250, 252)
    hi = (19, 113, 187)
    rgb = tuple(round(lo[i] + (hi[i] - lo[i]) * share) for i in range(3))
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _heatmap(spec: ChartSpec, area: _Box) -> list[str]:
    rows = spec.series
    columns = [str(item.get("key")) for item in
               spec.provenance.get("columns") or []]
    if not rows or not columns:
        return []
    labels = [str(item.get("label")) for item in spec.provenance.get("columns") or []]
    fm_gutter = max((_width(str(row.get("name") or "")) for row in rows), default=80)
    fm_gutter = min(max(90.0, fm_gutter + 14), area.width * 0.32)
    left = area.left + fm_gutter
    top = area.top + 30
    width = max(1.0, area.right - left)
    height = max(1.0, area.bottom - top - 12)
    col_w = width / len(columns)
    row_h = height / len(rows)
    largest = max((float((row.get("cells") or {}).get(c) or 0.0)
                   for row in rows for c in columns), default=0.0)
    out: list[str] = []
    for i, label in enumerate(labels):
        x = left + i * col_w + col_w / 2
        out.append(_text(x, top - 8, _shrink(label, col_w - 4), size=9,
                         anchor="middle"))
    for i, row in enumerate(rows):
        y = top + i * row_h
        out.append(_text(left - 7, y + row_h / 2 + 4,
                         _shrink(str(row.get("name") or ""), fm_gutter - 10),
                         size=10, anchor="end"))
        for j, cause in enumerate(columns):
            value = float((row.get("cells") or {}).get(cause) or 0.0)
            x = left + j * col_w
            out.append(_rect(x + 1, y + 1, col_w - 2, row_h - 2,
                             _heat_colour(value, largest)))
            if col_w >= 38 and row_h >= 18:
                ink = "#ffffff" if value / largest > 0.55 else INK
                out.append(_text(x + col_w / 2, y + row_h / 2 + 4,
                                 f"{100 * value:.0f}%", size=9,
                                 anchor="middle", colour=ink))
    return out


def _sankey(spec: ChartSpec, area: _Box) -> list[str]:  # noqa: C901, PLR0915 - one flow layout
    nodes = list(spec.provenance.get("nodes") or [])
    links = spec.series
    if not nodes or not links:
        return []
    total_lost = next((float(n.get("value") or 0.0) for n in nodes
                       if n.get("id") == "lost"), 0.0)

    def amount_of(raw: float) -> str:
        return (compact(raw) if abs(raw) >= 1e6
                else f"{raw:,.0f}" if abs(raw) >= 1000 else f"{raw:,.4g}")

    def share_of(node: dict, amount: str) -> str:
        """A loss node's amount, with its share of the lost flow."""
        ident = str(node.get("id") or "")
        if total_lost <= 0 or not (ident.startswith(("cause-", "hier-"))
                                   or ident == "unattributed"):
            return amount
        return f"{amount} ({pct(float(node.get('value') or 0.0) / total_lost)})"

    columns = sorted({int(node.get("column") or 0) for node in nodes})
    first = [str(n.get("name") or "") for n in nodes
             if int(n.get("column") or 0) == columns[0]]
    last = [f"{n.get('name') or ''} · "
            f"{share_of(n, amount_of(float(n.get('value') or 0)))}"
            for n in nodes if int(n.get("column") or 0) == columns[-1]]
    left_pad = min(area.width * 0.28,
                   max(110.0, max((_width(x) for x in first), default=0) + 18))
    right_pad = min(area.width * 0.50,
                    max(300.0, max((_width(x) for x in last), default=0) + 22))
    span = max(1.0, area.width - left_pad - right_pad)
    col_x = {col: area.left + left_pad + span * i / max(1, len(columns) - 1)
             for i, col in enumerate(columns)}
    totals = {col: sum(float(node.get("value") or 0.0) for node in nodes
                       if int(node.get("column") or 0) == col)
              for col in columns}
    gap = 6.0
    placed: dict[str, tuple[float, float, float]] = {}
    for col in columns:
        members = [node for node in nodes if int(node.get("column") or 0) == col]
        total = totals[col] or 1.0
        available = max(1.0, area.height - gap * max(0, len(members) - 1))
        y = area.top
        for node in members:
            h = max(2.0, available * float(node.get("value") or 0.0) / total)
            placed[str(node["id"])] = (col_x[col], y, h)
            y += h + gap
    out: list[str] = []
    # links behind nodes
    for link in links:
        source = placed.get(str(link.get("source")))
        target = placed.get(str(link.get("target")))
        if source is None or target is None:
            continue
        value = float(link.get("value") or 0.0)
        thickness = max(1.0, value / (totals[0] or 1.0) * area.height * 0.45)
        x0, y0, h0 = source
        x1, y1, h1 = target
        y0 += h0 / 2
        y1 += h1 / 2
        cause = str(link.get("cause") or "other")
        colour = _palette.colour(cause) if link.get("cause") else "#9FB3C8"
        cx = (x0 + x1) / 2
        out.append(
            f"<path d='M{_fmt(x0 + 14)},{_fmt(y0 - thickness / 2)} "
            f"C{_fmt(cx)},{_fmt(y0 - thickness / 2)} {_fmt(cx)},{_fmt(y1 - thickness / 2)} "
            f"{_fmt(x1)},{_fmt(y1 - thickness / 2)} L{_fmt(x1)},{_fmt(y1 + thickness / 2)} "
            f"C{_fmt(cx)},{_fmt(y1 + thickness / 2)} {_fmt(cx)},{_fmt(y0 + thickness / 2)} "
            f"{_fmt(x0 + 14)},{_fmt(y0 + thickness / 2)} Z' "
            f"fill='{colour}' fill-opacity='0.34'/>")
    for node in nodes:
        x, y, h = placed[str(node["id"])]
        out.append(_rect(x, y, 14, h, "#6B7785"))
        label = str(node.get("name") or "")
        if str(node.get("id") or "").startswith("cause-"):
            label = CAUSE_LABELS.get(str(node["id"])[6:], label)
        raw = float(node.get('value') or 0)
        amount = share_of(node, amount_of(raw))
        if int(node.get("column") or 0) == 0:
            out.append(_text(x - 6, y + h / 2 + 4,
                             _shrink(label, left_pad - 12), size=10,
                             anchor="end"))
        elif int(node.get("column") or 0) == max(columns):
            # a sliver node at the foot of the column: keep its label inside
            out.append(_text(x + 18, min(y + h / 2 + 4, area.bottom - 2),
                             _shrink(f"{label} · {amount}", right_pad - 20),
                             size=9))
        else:
            out.append(_text(x + 18, y + h / 2 + 4,
                             _shrink(f"{label} · {amount}", span / max(1, len(columns) - 1) - 20),
                             size=9))
    return out


def _arc_path(cx: float, cy: float, inner: float, outer: float,
              start: float, end: float) -> str:
    import math as _math
    large = 1 if end - start > _math.pi else 0
    p0 = (cx + inner * _math.cos(start), cy + inner * _math.sin(start))
    p1 = (cx + outer * _math.cos(start), cy + outer * _math.sin(start))
    p2 = (cx + outer * _math.cos(end), cy + outer * _math.sin(end))
    p3 = (cx + inner * _math.cos(end), cy + inner * _math.sin(end))
    return (f"M{_fmt(p0[0])},{_fmt(p0[1])} L{_fmt(p1[0])},{_fmt(p1[1])} "
            f"A{_fmt(outer)},{_fmt(outer)} 0 {large} 1 {_fmt(p2[0])},{_fmt(p2[1])} "
            f"L{_fmt(p3[0])},{_fmt(p3[1])} "
            f"A{_fmt(inner)},{_fmt(inner)} 0 {large} 0 {_fmt(p0[0])},{_fmt(p0[1])} Z")


def _mix_colour(colour: str, other: str, amount: float) -> str:
    a = tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(other[i:i + 2], 16) for i in (1, 3, 5))
    mixed = tuple(round(a[i] + (b[i] - a[i]) * amount) for i in range(3))
    return f"#{mixed[0]:02x}{mixed[1]:02x}{mixed[2]:02x}"


def _aria_label(spec: ChartSpec) -> str:
    """The title, with the hierarchy scope when the chart has one."""
    if not spec.scope:
        return spec.title
    from raptor.api.insight.hierarchy import scope_label
    return f"{spec.title} (scope: {scope_label(spec.scope)})"


def _sunburst(spec: ChartSpec, area: _Box) -> list[str]:
    from raptor.api.insight.hierarchy import SERIES_COLOURS, sunburst_layout
    nodes = sunburst_layout(spec.series)
    if not nodes:
        return []
    cx = (area.left + area.right) / 2
    cy = (area.top + area.bottom) / 2
    radius = min(area.width, area.height) / 2 - 12
    if radius <= 20:
        return []
    import math
    max_depth = max(int(node.get("depth") or 0) for node in nodes)
    inner = radius * 0.28
    ring = (radius - inner) / (max_depth + 1)
    out: list[str] = []
    for node in nodes:
        depth = int(node.get("depth") or 0)
        outer = inner + (depth + 1) * ring
        radius_in = inner + depth * ring
        start, end = float(node.get("start") or 0.0), float(node.get("end") or 0.0)
        root = int(node.get("root_index") or 0)
        colour = _mix_colour(SERIES_COLOURS[root % len(SERIES_COLOURS)],
                             "#FFFFFF", min(0.58, 0.14 * depth))
        out.append(f"<path d='{_arc_path(cx, cy, radius_in + 1.5, outer - 1.5, start, end)}' "
                   f"fill='{colour}' stroke='#ffffff' stroke-width='1'/>")
    placed: list[tuple[float, float, float, float]] = []
    for node in sorted(nodes, key=lambda item: int(item.get("depth") or 0)):
        depth = int(node.get("depth") or 0)
        start, end = float(node.get("start") or 0.0), float(node.get("end") or 0.0)
        span = end - start
        if depth == 0 or span < 0.30 or ring < 10:
            continue
        angle = (start + end) / 2
        label = str(node.get("name") or "").split(" > ")[-1]
        mid = inner + (depth + (0.85 if depth == 0 else 0.5)) * ring
        if span * mid < _width(label) * 1.2:
            continue
        x = cx + mid * math.cos(angle) - _width(label) / 2
        y = cy + mid * math.sin(angle) - 5
        box = (x - 2, y - 2, _width(label) + 4, 13)
        if (box[0] < area.left or box[0] + box[2] > area.right
                or box[1] < area.top or box[1] + box[3] > area.bottom):
            continue
        if any(not (box[0] + box[2] < other[0] or other[0] + other[2] < box[0]
                    or box[1] + box[3] < other[1] or other[1] + other[3] < box[1])
               for other in placed):
            continue
        placed.append(box)
        out.append(_text(x, y + 9, label, size=8, colour=_ink_on(colour)))
    centre = "System"
    if spec.scope and str(spec.scope.get("kind") or "") != "system":
        centre = str(spec.scope.get("name") or "System")
    out.append(_text(cx, cy + 4, centre, size=11, anchor="middle"))
    return out


def _scorecard(spec: ChartSpec, area: _Box) -> list[str]:
    rows = spec.series
    if not rows:
        return []
    columns = 2 if len(rows) > 2 else 1
    gap = 10.0
    width = (area.width - gap * (columns - 1)) / columns
    rows_n = (len(rows) + columns - 1) // columns
    height = (area.height - gap * (rows_n - 1)) / max(1, rows_n)
    out: list[str] = []
    for i, row in enumerate(rows):
        col, line = i % columns, i // columns
        x = area.left + col * (width + gap)
        y = area.top + line * (height + gap)
        out.append(_rect(x, y, width, height, "#F5F9FC",
                         " stroke='#D7E0E8' stroke-width='1'"))
        out.append(_text(x + 8, y + 18, str(row.get("name") or ""),
                         size=11, weight="600"))
        value = float(row.get("value") or 0.0)
        out.append(_text(x + 8, y + 42, format_value(value, "percent", 3),
                         size=20, weight="600", colour=ACCENT if value else MUTED))
        if _number(row.get("sem")):
            out.append(_text(x + 8, y + 58, f"± {2 * float(row['sem']) * 100:.2g} pts",
                             size=9, colour=MUTED))
        detail = (f"{int(row.get('members') or 0)} inside · "
                  f"{int(row.get('starters') or 0)} started")
        out.append(_text(x + 8, y + height - 8, detail, size=9, colour=MUTED))
        cause = str(row.get("cause") or "")
        if cause:
            out.append(_text(x + width - 8, y + 18, CAUSE_LABELS.get(cause, cause),
                             size=9, anchor="end", colour=MUTED))
    return out


def _small_multiples(spec: ChartSpec, area: _Box) -> list[str]:
    facets = spec.facets
    if not facets:
        return []
    columns = 1 if len(facets) == 1 else 2 if len(facets) <= 4 else 3
    rows = (len(facets) + columns - 1) // columns
    gap = 12.0
    width = (area.width - gap * (columns - 1)) / columns
    height = (area.height - gap * (rows - 1)) / max(1, rows)
    out: list[str] = []
    for i, facet in enumerate(facets):
        col, line = i % columns, i // columns
        x = area.left + col * (width + gap)
        y = area.top + line * (height + gap)
        out.append(_rect(x, y, width, height, "#FFFFFF",
                         " stroke='#D7E0E8' stroke-width='0.8'"))
        out.append(_text(x + 6, y + 14, facet.title, size=10, weight="600"))
        renderer = _RENDERERS.get(facet.kind)
        if renderer is not None:
            out.extend(renderer(facet, _Box(x + 4, y + 18, x + width - 4,
                                            y + height - 4)))
    return out


def _short(value: float) -> str:
    size = abs(value)
    if size >= 1e4:
        return compact(value)
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
    "timeline": _timeline,
    "delta": _delta,
    "heatmap": _heatmap,
    "sankey": _sankey,
    "sunburst": _sunburst,
    "scorecard": _scorecard,
    "small_multiples": _small_multiples,
}


#: Page sizes for saved charts: the drawing size in SVG units and the
#: physical size the file declares (None: plain pixels).  Text keeps its
#: size in drawing units, so a bigger page gives the names more room (A3
#: holds what A4 must wrap) and prints at a readable size: 11 units is
#: 8 pt on A4 and A3, 13 pt on a 16:9 slide.
PAGES: dict[str, tuple[int, int, tuple[str, str] | None]] = {
    "screen": (WIDTH, HEIGHT, None),
    "slide": (800, 450, ("338.67mm", "190.5mm")),
    "a4": (1123, 794, ("297mm", "210mm")),
    "a3": (1587, 1123, ("420mm", "297mm")),
}


def render(spec: ChartSpec, *, width: int = WIDTH, height: int = HEIGHT,
           page: str | None = None, background: str = "#ffffff",
           text_colour: str = INK, id_prefix: str = "") -> str:
    """One ChartSpec as a complete SVG document.

    `page` (a `PAGES` name) sets the size, overriding `width` and `height`.
    No label is ever shortened: names wrap onto more lines, and a treemap
    cell too small for its name is numbered and named in a footnote.
    `id_prefix` makes the pattern ids unique when several charts are inlined
    in one HTML page (the report): repeated ids are invalid there."""
    # money in the spec's currency (`provenance["currency"]`, else the
    # caller's), for every tick, bar label and reading
    with currency(spec.provenance.get("currency")):
        return _render(spec, width=width, height=height, page=page,
                       background=background, text_colour=text_colour,
                       id_prefix=id_prefix)


def _render(spec: ChartSpec, *, width: int, height: int, page: str | None,
            background: str, text_colour: str, id_prefix: str) -> str:
    physical: tuple[str, str] | None = None
    if page is not None:
        if page not in PAGES:
            raise ValueError(f"unknown page {page!r}; known pages: "
                             f"{', '.join(PAGES)}")
        width, height, physical = PAGES[page]
    size = (f"width='{physical[0]}' height='{physical[1]}'" if physical
            else f"width='{width}' height='{height}'")
    title = _wrap(spec.title, width - 2 * PAD, size=15)
    # a saved picture cannot be clicked
    static = (spec.subtitle or "").replace("; click a cell to locate", "")
    subtitle = _wrap(static, width - 2 * PAD) if static else []
    body: list[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' {size} "
        f"viewBox='0 0 {width} {height}' "
        f"role='img' aria-label='{_esc(_aria_label(spec))}'>",
        f"<rect width='{width}' height='{height}' fill='{background}'/>",
        _lines(width / 2, 22, title, size=15, anchor="middle", weight="600",
               colour=text_colour, step=19),
        *_cause_defs(),
    ]
    sub_y = 22 + 19 * (len(title) - 1) + 17
    if subtitle:
        body.append(_lines(width / 2, sub_y, subtitle, anchor="middle", colour=MUTED))
    top = sub_y + LINE_H * (len(subtitle) - 1) + 9.0 if subtitle else sub_y - 8.0
    top = max(top, float(TITLE_H))
    if not spec.series and not spec.annotations and not spec.facets and spec.caption:
        # nothing to draw: say why where the plot would be, not in small
        # print under an empty frame (an empty chart read as a broken one)
        reason = _wrap(spec.caption, min(width - 2 * PAD, 560), size=14)
        middle = (top + height) / 2 - 10 * (len(reason) - 1)
        body.append(_lines(width / 2, middle, reason, size=14, anchor="middle",
                           colour=MUTED, step=20))
        body.append("</svg>")
        return _prefixed("\n".join(body), id_prefix)
    x = float(PAD)
    for label, fill in _key(spec):
        w = 18 + _width(label) + 14
        if x + w > width - PAD and x > PAD:
            x, top = float(PAD), top + KEY_H
        body.append(_swatch(x, top, fill, text_colour))
        body.append(_text(x + 18, top + 13, label, size=11, colour=text_colour))
        x += w
    if _key(spec):
        top += KEY_H
    caption = _wrap(spec.caption, width - 2 * PAD) if spec.caption else []
    bottom = height - CAPTION_H - LINE_H * max(0, len(caption) - 1)
    area = _Box(PAD, top + 4, width - PAD, bottom)
    renderer = _RENDERERS.get(spec.kind)
    body.extend(renderer(spec, area) if renderer is not None else [])
    if caption:
        body.append(_lines(PAD, height - 9 - LINE_H * (len(caption) - 1), caption,
                           colour=text_colour))
    body.append("</svg>")
    return _prefixed("\n".join(body), id_prefix)


def _prefixed(svg: str, id_prefix: str) -> str:
    """Pattern ids made unique for a page holding several charts."""
    if id_prefix:
        svg = svg.replace("id='pat-", f"id='{id_prefix}pat-").replace(
            "url(#pat-", f"url(#{id_prefix}pat-")
    return svg


def write(spec: ChartSpec, path: str | Any, *, page: str | None = None) -> str:
    """Write the SVG (at a `PAGES` size when `page` is given) and return
    the path as text."""
    text = render(spec, page=page)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return str(path)

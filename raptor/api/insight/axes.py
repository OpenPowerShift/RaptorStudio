"""Axis ticks, tick labels and dot stacking shared by the result charts.

Pure functions (no painting), used by the Qt painter
(`raptor.gui.ui.chart_widget`) and the SVG renderer
(`raptor.render.charts_svg`) so both draw the same axes:
`nice_ticks` picks 1-2-5 steps, `tick_text` formats a tick so that
neighbouring ticks never print the same text, and `stack_dots` lays out a
Wilkinson dot plot (each dot at its own value, equal values stacked).
"""
from __future__ import annotations

import math
from collections.abc import Sequence


def nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    """About `count` round tick values covering [low, high]."""
    if not (math.isfinite(low) and math.isfinite(high)):
        return []
    if high < low:
        low, high = high, low
    if high == low:
        return [low]
    raw = (high - low) / max(1, count)
    power = 10.0 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)
    first = math.ceil(low / step - 1e-9) * step
    # Rounded to the step's own precision, so 0.2 * 3 is 0.6, not
    # 0.6000000000000001.
    places = max(0, 2 - math.floor(math.log10(step)))
    ticks = []
    value = first
    while value <= high + step * 1e-9:
        ticks.append(0.0 if abs(value) < step * 1e-9 else round(value, places))
        value = first + step * len(ticks)
    return ticks


def nice_range(low: float, high: float, count: int = 5) -> tuple[float, float]:
    """[low, high] widened to whole tick steps (so the axis ends on a tick)."""
    if high <= low:
        pad = abs(low) * 0.01 or 1.0
        return low - pad, high + pad
    ticks = nice_ticks(low, high, count)
    if len(ticks) < 2:
        return low, high
    step = ticks[1] - ticks[0]
    places = max(0, 2 - math.floor(math.log10(step)))
    return (round(math.floor(low / step + 1e-9) * step, places),
            round(math.ceil(high / step - 1e-9) * step, places))


def tick_text(value: float, ticks: Sequence[float], *, percent: bool = False,
              suffix: str = "", fmt: str = "") -> str:
    """A tick label with just enough decimals to tell neighbours apart.

    `fmt` is a chart's value format (see `format_value`); ``"percent"`` is
    the same as ``percent=True``."""
    if fmt == "percent":
        percent = True
    elif fmt == "money":
        return _money(value, step=abs(ticks[1] - ticks[0]) if len(ticks) > 1
                      else abs(value))
    scale = 100.0 if percent else 1.0
    step = (abs(ticks[1] - ticks[0]) if len(ticks) > 1 else abs(value) or 1.0)
    step *= scale
    mantissa = round(step / 10 ** math.floor(math.log10(step)), 6)
    decimals = max(0, min(8, -math.floor(math.log10(step))
                          + (1 if mantissa == 2.5 else 0)))
    shown = value * scale
    if abs(shown) >= 10_000 and decimals == 0:
        text = f"{shown:,.0f}"
    else:
        text = f"{shown:.{decimals}f}"
    return text + (" %" if percent else suffix)


def _money(value: float, step: float | None = None) -> str:
    """1,234 / 12.3k / 4.56M: short enough for a tick or a bar label."""
    size = abs(value)
    for limit, unit in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if size >= limit:
            scaled = value / limit
            places = 0
            if step is not None and step > 0:
                places = max(0, min(3, -math.floor(math.log10(step / limit))))
            elif abs(scaled) < 100:
                places = 1 if abs(scaled) >= 10 else 2
            return f"{scaled:,.{places}f}{unit}"
    return f"{value:,.0f}" if size >= 10 or value == 0 else f"{value:.3g}"


#: below this many percent a share is printed as "< 0.01 %", never as
#: 4.73e-09 %: a number that small only says "almost none"
SMALLEST_PERCENT = 0.01


def pct(fraction: float, digits: int = 3) -> str:
    """A fraction as a percentage for people: ``0.4381`` -> ``43.8 %``, a
    tiny non-zero share -> ``< 0.01 %``, never scientific notation."""
    value = 100.0 * float(fraction)
    if value != value:  # NaN
        return "— %"
    if 0 < abs(value) < SMALLEST_PERCENT:
        return f"{'−' if value < 0 else ''}< {SMALLEST_PERCENT:g} %"
    return f"{value:.{digits}g} %"


def format_value(value: float, fmt: str = "", digits: int = 4) -> str:
    """One value as a chart prints it beside a mark.

    ``percent`` for fractions (0.9934 -> ``99.34 %``), ``money`` for costs
    (``1.8M``), ``count`` for whole numbers, anything else six significant
    digits."""
    if fmt == "percent":
        return pct(value, digits)
    if fmt == "money":
        return _money(value)
    if fmt == "count":
        return f"{value:,.0f}" if abs(value - round(value)) < 1e-9 \
            else f"{value:,.3g}"
    return f"{value:,.{max(digits, 4)}g}"


def stack_dots(xs: Sequence[float], diameter: float) -> list[tuple[float, int]]:
    """Wilkinson dot plot: (x, level) for each input x, in input order.

    Dots closer than one diameter share a column (placed at the column's
    first dot) and stack upward, level 0 at the baseline.
    """
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out: list[tuple[float, int]] = [(0.0, 0)] * len(xs)
    column_x: float | None = None
    level = 0
    for i in order:
        x = float(xs[i])
        if column_x is None or x - column_x >= diameter:
            column_x, level = x, 0
        else:
            level += 1
        out[i] = (column_x, level)
    return out


#: (left, top, width, height) of a placed label, in drawing units
Rect = tuple[float, float, float, float]


def _overlaps(a: Rect, b: Rect) -> bool:
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
                or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def place_labels(points: Sequence[tuple[float, float, float]],
                 sizes: Sequence[tuple[float, float]],
                 bounds: Rect) -> list[tuple[Rect, tuple[float, float] | None]]:
    """Put a label beside each point so that no two labels, and no label and
    another point's dot, overlap.

    ``points`` are (x, y, radius) and ``sizes`` each label's (width, height),
    in drawing units with y growing downward. Each point first tries the eight
    places around its dot; when all are taken it moves further out, and the
    result then carries a leader: the (x, y) on the label's edge to draw a line
    to the dot from. ``None`` means the label sits next to its dot. Points are
    placed in the order given, so pass the most important first. A label that
    fits nowhere is put above its dot regardless (its tooltip still names it).
    """
    left, top, width, height = bounds
    dots = [(x - r, y - r, 2 * r, 2 * r) for x, y, r in points]
    placed: list[Rect] = []
    out: list[tuple[Rect, tuple[float, float] | None]] = []
    for index, ((x, y, r), (w, h)) in enumerate(zip(points, sizes, strict=True)):
        chosen: Rect | None = None
        leader = False
        for ring in range(7):
            gap = r + 2 + ring * h
            # above, below, right, left, then the four corners
            for dx, dy in ((0, -1), (0, 1), (1, 0), (-1, 0),
                           (1, -1), (-1, -1), (1, 1), (-1, 1)):
                cx = x + dx * (gap + w / 2)
                cy = y + dy * (gap + h / 2)
                rect = (cx - w / 2, cy - h / 2, w, h)
                if (rect[0] < left or rect[1] < top or rect[0] + w > left + width
                        or rect[1] + h > top + height):
                    continue
                if any(_overlaps(rect, other) for other in placed):
                    continue
                if any(_overlaps(rect, dot) for j, dot in enumerate(dots) if j != index):
                    continue
                chosen, leader = rect, ring > 0
                break
            if chosen is not None:
                break
        if chosen is None:
            cx = min(max(left + w / 2, x), left + width - w / 2)
            chosen, leader = (cx - w / 2, max(top, y - r - 2 - h), w, h), False
        placed.append(chosen)
        end = None
        if leader:
            end = (min(max(x, chosen[0]), chosen[0] + chosen[2]),
                   min(max(y, chosen[1]), chosen[1] + chosen[3]))
        out.append((chosen, end))
    return out

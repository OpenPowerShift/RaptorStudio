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


def format_value(value: float, fmt: str = "", digits: int = 4) -> str:
    """One value as a chart prints it beside a mark.

    ``percent`` for fractions (0.9934 -> ``99.34 %``), ``money`` for costs
    (``1.8M``), ``count`` for whole numbers, anything else six significant
    digits."""
    if fmt == "percent":
        return f"{100 * value:.{digits}g} %"
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

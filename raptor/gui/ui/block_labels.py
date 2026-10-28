"""What the canvas writes under a block's name.

Raptor 7 draws nothing there; the two `F=…` / `R=…` lines were this
product's own addition, and they printed the distribution's FIRST PARAMETER
rather than a mean -- right by accident for an exponential law, wrong for a
lognormal, Weibull, normal or triangular one.

The numbers here come from the engine's own mean (`distro_mean`) and its new
companion `distro_sigma`, so a label can never disagree with the run.

A phased block has a different law per phase: its per-phase entry is a
stress multiplier that the engine divides the failure time by
("PMTimeLeft < FailTimeLeft/stress"), and a phase can
also cut the block or link it. `phase` selects which one the canvas shows;
`-1` is the model's own (unphased) law.
"""
from __future__ import annotations

import html
import math

#: Label modes, in menu order. `off` is the clean canvas.
MODES: tuple[tuple[str, str], ...] = (
    ("mean", "Mean"),
    ("spread", "Mean ± σ"),
    ("rate", "Failure rate"),
    ("off", "Off"),
)

DEFAULT_MODE = "mean"

#: The phase entry meaning "the block is cut in this phase" (`C`) and
#: "linked" (`L`); see `set_element_phasing`.
_CUT, _LINK = "C", "L"


def mode_label(mode: str) -> str:
    """The menu label for a mode key ('mean' -> 'Mean')."""
    return dict(MODES).get(mode, dict(MODES)[DEFAULT_MODE])


def engineering(value: float) -> str:
    """A number a reader can scan: 1,200 / 0.0123 / 8.33e-4.

    Not fixed decimals -- a repair mean of 0.0123 h under a failure mean of
    1,200 h has to fit the same two lines.
    """
    if value is None or not math.isfinite(value):
        return "—"
    a = abs(value)
    if a != 0.0 and (a < 1e-3 or a >= 1e6):
        return f"{value:.3g}"
    return f"{value:,.4g}"


def _mean_of(code: int, params: list[float]) -> float | None:
    from raptor.core.engine.dependency import distro_mean

    try:
        value = float(distro_mean(code, list(params)))
    except Exception:                                  # noqa: BLE001
        return None
    # 12345.678x are the engine's "Murphy" sentinels for a shape that has no
    # mean (see `distro_mean`); printing them would be nonsense.
    if not math.isfinite(value) or 12345.0 < value < 12346.0:
        return None
    return value


def _sigma_of(code: int, params: list[float]) -> float | None:
    from raptor.core.distro_rules import distro_sigma

    try:
        value = distro_sigma(code, list(params))
    except Exception:                                  # noqa: BLE001
        return None
    return value if value is not None and math.isfinite(value) else None


def _phase_entry(block, phase: int) -> tuple[str, float] | None:
    """`(type, stress)` of a phased block's entry for `phase`, or None."""
    if not getattr(block, "uses_phasing", False) or phase < 0:
        return None
    types = list(getattr(block, "phase_type", []) or [])
    values = list(getattr(block, "phase_value", []) or [])
    if phase >= len(types):
        return None
    stress = values[phase] if phase < len(values) else 1.0
    return str(types[phase] or "A"), float(stress)


def sublabel(block, *, mode: str = DEFAULT_MODE, phase: int = -1,
             units: str = "") -> list[str]:
    """The lines to draw under *block*'s name (empty for mode 'off').

    At most two short lines, because the block is small: the failure side
    first, the repair side second, in the order the `F`/`R` pair used.
    """
    if mode not in dict(MODES) or mode == "off":
        return []
    unit = f" {units}" if units else ""
    entry = _phase_entry(block, phase)
    if entry is not None and entry[0] == _CUT:
        return ["cut in this phase"]

    fail_mean = _mean_of(block.fail_distro, block.fail_values)
    repair_mean = _mean_of(block.repair_distro, block.repair_values)
    if entry is not None and fail_mean is not None and entry[1] > 0.0:
        # Higher stress shortens the life: the engine divides by it.
        fail_mean = fail_mean / entry[1]

    if mode == "mean":
        return _mean_lines(fail_mean, repair_mean, unit)
    if mode == "spread":
        fail_sigma = _sigma_of(block.fail_distro, block.fail_values)
        repair_sigma = _sigma_of(block.repair_distro, block.repair_values)
        if entry is not None and fail_sigma is not None and entry[1] > 0.0:
            fail_sigma = fail_sigma / entry[1]
        return _spread_lines(fail_mean, fail_sigma, repair_mean,
                             repair_sigma, unit)
    if mode == "rate":
        return _rate_lines(fail_mean, repair_mean)
    return []


def _mean_lines(fail_mean, repair_mean, unit: str) -> list[str]:
    lines: list[str] = []
    if fail_mean is not None:
        lines.append(f"MTTF {engineering(fail_mean)}{unit}")
    if repair_mean is not None:
        lines.append(f"MTTR {engineering(repair_mean)}{unit}")
    return lines


def _spread_lines(fail_mean, fail_sigma, repair_mean, repair_sigma,
                  unit: str) -> list[str]:
    lines: list[str] = []
    if fail_mean is not None:
        lines.append(
            f"{engineering(fail_mean)} ± {engineering(fail_sigma)}{unit}"
            if fail_sigma is not None else
            f"{engineering(fail_mean)}{unit}")
    if repair_mean is not None:
        lines.append(
            f"{engineering(repair_mean)} ± {engineering(repair_sigma)}{unit}"
            if repair_sigma is not None else
            f"{engineering(repair_mean)}{unit}")
    return lines


def _rate_lines(fail_mean, repair_mean) -> list[str]:
    lines: list[str] = []
    if fail_mean is not None and fail_mean > 0.0:
        lines.append(f"λ {engineering(1.0 / fail_mean)}/h")
    if repair_mean is not None and repair_mean > 0.0:
        lines.append(f"μ {engineering(1.0 / repair_mean)}/h")
    return lines


def tooltip(block, *, mode: str = DEFAULT_MODE, phase: int = -1,
            units: str = "") -> str:
    """The full story for the block's own hover text.

    Independent of `mode`, so choosing Off loses nothing: the numbers a
    label would show, the spread behind them, the rates, and any per-phase
    entry.
    """
    unit = f" {units}" if units else ""
    fail_mean = _mean_of(block.fail_distro, block.fail_values)
    repair_mean = _mean_of(block.repair_distro, block.repair_values)
    fail_sigma = _sigma_of(block.fail_distro, block.fail_values)
    repair_sigma = _sigma_of(block.repair_distro, block.repair_values)
    out = [f"Failure mean {engineering(fail_mean)}{unit}"
           + (f" ± {engineering(fail_sigma)}{unit}"
              if fail_sigma is not None else ""),
           f"Repair mean {engineering(repair_mean)}{unit}"
           + (f" ± {engineering(repair_sigma)}{unit}"
              if repair_sigma is not None else "")]
    if fail_mean:
        out.append(f"Failure rate {engineering(1.0 / fail_mean)}/h")
    if repair_mean:
        out.append(f"Repair rate {engineering(1.0 / repair_mean)}/h")
    if getattr(block, "uses_phasing", False):
        types = list(getattr(block, "phase_type", []) or [])
        values = list(getattr(block, "phase_value", []) or [])
        if types:
            shown = ", ".join(
                f"P{i + 1} {t}"
                + (f" stress {engineering(v)}" if t != _CUT else " (cut)")
                for i, (t, v) in enumerate(zip(types, values)))
            out.append(f"Per-phase: {shown}")
            if phase >= 0 and phase < len(types):
                out.append(f"Canvas is showing phase {phase + 1}")
    return "\n".join(out)


def sublabel_html(lines: list[str]) -> str:
    """The two lines as the HTML fragment the item renders."""
    if not lines:
        return ""
    body = "<br>".join(html.escape(line) for line in lines)
    return f"<div style='font-size:7pt'>{body}</div>"

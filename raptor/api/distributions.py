"""Distribution reference data shared by the API and the MCP tools.

Parameter names and valid ranges come from the RAPTOR 7 tutorial workbook
and the engine's own parameter validation.  Kept beside the code table so
callers use the correct parameter order (for example Weibull is
``(shape, scale)``).
"""
from __future__ import annotations

from typing import Any

#: code -> parameter names (in slot order) and valid-range guidance.
PARAM_INFO: dict[int, dict[str, Any]] = {
    1: {"params": ["alpha", "beta"], "ranges": {"alpha": "> 0", "beta": "> 0"}},
    2: {"params": ["degrees_of_freedom"], "ranges": {"degrees_of_freedom": "> 0"}},
    3: {"params": ["trials", "probability"], "ranges": {"trials": ">= 1 integer", "probability": "0-1"}},
    4: {"params": ["mean", "location"], "ranges": {"mean": "> 0", "location": ">= 0"}},
    5: {"params": ["mean", "k"], "ranges": {"mean": "> 0", "k": ">= 1 integer"}},
    6: {"params": ["mean", "k", "location"], "ranges": {"mean": "> 0", "k": "> 0", "location": ">= 0"}},
    7: {"params": ["mean", "stdev"], "ranges": {"mean": "> 0", "stdev": "> 0"}},
    8: {"params": ["mean", "stdev"], "ranges": {"mean": ">= 0", "stdev": "> 0"}},
    9: {"params": ["lower", "upper"], "ranges": {"lower": "integer", "upper": ">= lower"}},
    10: {"params": ["lower", "upper"], "ranges": {"lower": "any", "upper": "> lower"}},
    11: {"params": ["shape", "scale", "location"], "ranges": {"shape": "> 1", "scale": "> 0", "location": ">= 0"}},
    12: {"params": ["shape1", "shape2", "scale"], "ranges": {"shape1": "> 0", "shape2": "> 1", "scale": "> 0"}},
    13: {"params": ["mean"], "ranges": {"mean": "> 0"}},
    14: {"params": ["lower", "mode", "upper"], "ranges": {"lower": "any", "mode": "between lower and upper", "upper": "> lower"}},
    15: {"params": ["shape", "scale", "location"], "ranges": {"shape": "> 0 (shape=1 -> exponential)", "scale": "> 0", "location": ">= 0"}},
    16: {"params": ["value_1", "value_2", "..."], "ranges": {}},
    17: {"params": ["value"], "ranges": {"value": "any"}},
    18: {"params": [], "ranges": {}},
    19: {"params": ["value"], "ranges": {"value": "any"}},
    20: {"params": ["shape", "scale", "location"], "ranges": {"shape": "> 0", "scale": "> 0", "location": ">= 0"}},
    21: {"params": ["scale", "location"], "ranges": {"scale": "> 0", "location": "any"}},
    22: {"params": ["scale", "location"], "ranges": {"scale": "> 0", "location": "any"}},
}


def dist_name(code: int) -> str:
    """Human name for a distribution code (e.g. ``15`` -> ``"Weibull"``)."""
    from raptor.core.rng import Distribution

    if code == 17:
        return "Bernoulli (Point Estimate)"
    if code == 20:
        return "Exponential Power / Error"
    try:
        return Distribution(code).name.lower().replace("_", " ").title()
    except ValueError:
        return f"Unknown ({code})"


def _num(value: Any) -> float | None:
    """A finite float, or None for the engine's 'Undefined'/'N/A' sentinels."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value == 12345.6789:  # the engine's "not computable here" placeholder
        return None
    return float(value)


def describe_dist(code: int, params: list[float]) -> dict[str, Any]:
    """Name, named parameters and (where defined) mean/stdev for one draw rule.

    ``mean``/``stdev`` are ``None`` when the distribution has no finite value
    for those parameters (for example a Pearson 5 with shape <= 2 has an
    infinite variance); ``notes`` says so rather than leaving a bare null.
    """
    from raptor.core.engine.diststats import compute_stats

    names = PARAM_INFO.get(code, {}).get("params", [])
    named = {n: p for n, p in zip(names, params)} if names else {}
    mean = stdev = None
    notes: list[str] = []
    try:
        stats = compute_stats(code, list(params))
    except Exception:  # noqa: BLE001 - a description must never crash
        stats = None
    if stats:
        mean, stdev = _num(stats[0]), _num(stats[1])
        if mean is None and stats[0] not in (None, 12345.6789):
            notes.append(f"mean is {stats[0]}")
        if stdev is None and stats[1] not in (None, 12345.6789):
            notes.append(f"stdev is {stats[1]} (no finite value for these parameters)")
    out: dict[str, Any] = {
        "code": code,
        "name": dist_name(code),
        "params": named or list(params),
        "mean": mean,
        "stdev": stdev,
    }
    if notes:
        out["notes"] = notes
    return out

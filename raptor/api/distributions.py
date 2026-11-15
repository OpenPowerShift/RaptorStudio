"""Distribution reference data shared by the API and the MCP tools.

Parameter names and valid ranges come from the RAPTOR 7 tutorial workbook
and the engine's own parameter validation.  Kept beside the code table so
callers use the correct parameter order (for example Weibull is
``(shape, scale)``).
"""
from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

#: code -> parameter names (in slot order) and valid-range guidance.
PARAM_INFO: dict[int, dict[str, Any]] = {
    1: {"params": ["alpha", "beta"], "ranges": {"alpha": "> 0", "beta": "> 0"}},
    2: {"params": ["degrees_of_freedom"], "ranges": {"degrees_of_freedom": "> 0"}},
    3: {"params": ["trials", "probability"], "ranges": {"trials": ">= 1 integer", "probability": "0-1"}},
    4: {"params": ["mean", "location"], "ranges": {"mean": "> 0", "location": ">= 0"}},
    # Erlang and Gamma: the file's first slot is the shape and the second the
    # scale (`rng._erlang(p[0] * p[1], round(p[0]))`); the mean is their product
    5: {"params": ["k", "scale"],
        "ranges": {"k": ">= 1 integer (stages)", "scale": "> 0 (each stage's mean; mean = k x scale)"}},
    6: {"params": ["shape", "scale", "location"],
        "ranges": {"shape": "> 0", "scale": "> 0 (mean = shape x scale + location)",
                   "location": ">= 0"}},
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


#: Which parameter slots scale when the mean is multiplied, per distribution
#: code.  `("mean",)` scales one slot; `("mean", "stdev")` keeps the shape
#: (CV) by scaling spread too; The fixed and uniform laws scale their values.
_SCALE_SLOTS: dict[int, tuple[int, ...]] = {
    4: (0, 1),        # Exponential: mean and location shift together
    5: (1,),          # Erlang: the scale (k x scale is the mean)
    6: (1, 2),        # Gamma: scale and location
    7: (0, 1),        # Lognormal: mean and stdev keep sigma
    8: (0, 1),        # Normal: mean and stdev keep the CV
    9: (0, 1),        # Uniform integer: lower and upper
    10: (0, 1),       # Uniform real: lower and upper
    11: (1, 2),       # Pearson 5: scale and location
    12: (2,),         # Pearson 6: scale
    13: (0,),         # Poisson: mean
    14: (0, 1, 2),    # Triangular: lower, mode, upper
    15: (1, 2),       # Weibull: scale and location
    19: (0,),         # Fixed: value
    21: (0, 1),       # Extreme value: scale and location
    22: (0, 1),       # Laplace: scale and location
}


def scales_with_mean(code: int) -> bool:
    """Whether `scale_mean` can move this law's mean by a factor."""
    return code in _SCALE_SLOTS


def scale_mean(code: int, params: list[float], factor: float) -> list[float]:
    """The parameters with the distribution's mean multiplied by `factor`.

    Shape parameters are untouched: Weibull's shape, Gamma's k, Lognormal's
    sigma (mean and stdev scale together), so the law keeps its shape and
    only its time scale moves.  Laws without a scale slot (Beta, Binomial,
    Chi-square, empirical) raise ValueError rather than guessing.

    `factor` must be positive.  The result is a new list; `params` is not
    changed.
    """
    if factor <= 0:
        raise ValueError("factor must be positive")
    slots = _SCALE_SLOTS.get(code)
    if slots is None:
        raise ValueError(
            f"{dist_name(code)} has no scale parameter to move; use its own "
            "parameters")
    out = [float(value) for value in params]
    for slot in slots:
        if slot >= len(out):
            continue
        out[slot] *= factor
    return out


def from_quantiles(  # noqa: C901, PLR0912, PLR0915
        law: str, quantiles: dict[float, float]) -> dict[str, Any]:
    """Fit a two-parameter life law from experience: shares failed by given ages (WP-4.4).

    `quantiles` maps probability to age, e.g. `{0.1: 100, 0.5: 300}` for
    "one in ten fail by 100, half by 300".  Two points solve the law in
    closed form; three or more use a least-squares fit of the linearised
    quantile function.  Returns the parameters in slot order plus the
    implied mean, the fitted quantiles and a plain reading of the shape.

    Laws: `weibull`, `lognormal` and `exponential`.  Inconsistent answers
    (a higher probability at an earlier age) are refused by name.
    """
    from statistics import NormalDist

    if law not in ("weibull", "lognormal", "exponential"):
        raise ValueError(
            "law must be one of weibull, lognormal or exponential")
    points = sorted((float(p), float(t)) for p, t in quantiles.items())
    if len(points) < 2:
        raise ValueError("give at least two quantiles")
    for probability, age in points:
        if not 0 < probability < 1:
            raise ValueError(f"probability {probability!r} must be in (0, 1)")
        if age <= 0:
            raise ValueError(f"age {age!r} must be positive")
    ages = [age for _p, age in points]
    if any(after <= before for before, after in pairwise(ages)):
        raise ValueError(
            "the answers are inconsistent: a higher probability cannot "
            "have an earlier age")

    def implied(params: dict[str, float], probability: float) -> float:
        if law == "weibull":
            return params["scale"] * (
                -math.log(1 - probability)) ** (1 / params["shape"])
        if law == "lognormal":
            z = NormalDist().inv_cdf(probability)
            return math.exp(params["mu"] + params["sigma"] * z)
        return -params["mean"] * math.log(1 - probability)

    if law == "exponential":
        ys = [-math.log(1 - p) for p, _t in points]
        ts = [t for _p, t in points]
        mean = sum(y * t for y, t in zip(ys, ts, strict=True)) / sum(
            y * y for y in ys)
        params = {"mean": mean}
        slots = [mean]
        note = "random failures: planned maintenance cannot reduce them"
    elif law == "weibull":
        xs = [math.log(t) for _p, t in points]
        ys = [math.log(-math.log(1 - p)) for p, _t in points]
        if len(points) == 2:
            beta = (ys[1] - ys[0]) / (xs[1] - xs[0])
            intercept = ys[0] - beta * xs[0]
        else:
            mean_x = sum(xs) / len(xs)
            mean_y = sum(ys) / len(ys)
            beta = (sum((x - mean_x) * (y - mean_y)
                        for x, y in zip(xs, ys, strict=True))
                    / sum((x - mean_x) ** 2 for x in xs))
            intercept = mean_y - beta * mean_x
        if beta <= 0:
            raise ValueError("the answers imply a decreasing failure rate; "
                             "use exponential or check the order")
        scale = math.exp(-intercept / beta)
        params = {"shape": beta, "scale": scale}
        slots = [beta, scale, 0.0]
        note = ("wears out: planned maintenance can help" if beta > 1
                else "random failures: planned maintenance cannot help")
    else:
        zs = [NormalDist().inv_cdf(p) for p, _t in points]
        xs = [math.log(t) for _p, t in points]
        if len(points) == 2:
            sigma = (xs[1] - xs[0]) / (zs[1] - zs[0])
            mu = xs[0] - sigma * zs[0]
        else:
            mean_z = sum(zs) / len(zs)
            mean_x = sum(xs) / len(xs)
            sigma = (sum((z - mean_z) * (x - mean_x)
                         for z, x in zip(zs, xs, strict=True))
                     / sum((z - mean_z) ** 2 for z in zs))
            mu = mean_x - sigma * mean_z
        if sigma <= 0:
            raise ValueError("the answers imply a negative spread; check "
                             "the order")
        params = {"mu": mu, "sigma": sigma}
        slots = [math.exp(mu + sigma * sigma / 2),
                 math.sqrt((math.exp(sigma * sigma) - 1)
                           * math.exp(2 * mu + sigma * sigma))]
        note = ("repair times: the longer tail is why a mean alone "
                "understates the wait" if law == "lognormal" else "")
    if law == "lognormal":
        params_out = {"mu": params["mu"], "sigma": params["sigma"]}
        mean = slots[0]
    elif law == "weibull":
        params_out = {"shape": params["shape"], "scale": params["scale"]}
        mean = params["scale"] * math.gamma(1 + 1 / params["shape"])
    else:
        params_out = {"mean": params["mean"]}
        mean = params["mean"]
    return {
        "law": law,
        "params": params_out,
        "slots": slots,
        "mean": mean,
        "fitted": {round(p, 6): implied(params, p) for p in (0.01, 0.1, 0.5,
                                                              0.9, 0.99)},
        "stated": {p: t for p, t in points},
        "shape_note": note,
    }


def experience_changes(fit: dict[str, Any], block: str,
                      use: str = "fail") -> list[str]:
    """PATH=VALUE edits that give `block` the law `fit` (from
    `from_quantiles`) as its failure (`use="fail"`) or repair
    (`use="repair"`) distribution, every parameter slot written."""
    if use not in ("fail", "repair"):
        raise ValueError("use must be 'fail' or 'repair'")
    slots = list(fit["slots"]) + [0.0] * (3 - len(fit["slots"]))
    params = ",".join(f"{value:.10g}" for value in slots)
    return [f"block:{block}.{use}_dist={fit['law']}",
            f"block:{block}.{use}_params={params}"]

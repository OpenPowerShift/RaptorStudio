"""Distribution parameter rules: the one owner, with no Qt and no I/O.

Raptor 7's ``GetParameters`` supplies the per-distribution
parameter labels, and its ``CheckValidInput`` the validation
rules. Both were ported twice -- once into `gui/ui/inspector.py` and once
into `api/validate.py` -- and the two copies turned out to be
**byte-identical in code**, differing only in comments. That is worse than
divergence: nothing flags it, and the API copy had already lost the NaN
guard's rationale, the Chi-square/Erlang integer-shape note, and the
Triangular legacy-comparison note.

This module is the canonical copy, promoted out of the widget because the
rules are pure model rules -- no Qt, no I/O, no model access. It was the
*widget* copy that was canonical: `api/validate.py` carried the bare
comment ``# Port of inspector.py's validate_distro_params``.

Living here also ends the GUI reaching into a widget module for a model
rule. Before the promotion, `inspector.py`, `data_tables.py`,
`distro_editor.py` and `dialogs.py` all imported the tables and the
validator by private name from `inspector`, so a validation rule lived in
a widget and five other modules depended on it there.

Rules key off each parameter's LABEL rather than its index, exactly as the
source does (the legacy code keys off the same per-distribution tag strings
``GetParameters`` produces). So a rule written against "Shape" applies to
every distribution whose parameter carries that label.
"""
from __future__ import annotations

import math

#: Per-distribution display names, keyed by the file format's code.
#: Matches `raptor.core.rng.Distribution` and the .rbd FDist/RDist/Dist
#: column codes (Raptor 7's `ConvertToString`).
DISTRO_NAMES = {
    1: "Beta", 2: "Chi-square", 3: "Binomial", 4: "Exponential",
    5: "Erlang", 6: "Gamma", 7: "Lognormal", 8: "Normal",
    9: "Uniform Integer", 10: "Uniform Real", 11: "Pearson 5",
    12: "Pearson 6", 13: "Poisson", 14: "Triangular", 15: "Weibull",
    16: "Empirical", 17: "Point Estimate", 18: "None", 19: "Fixed",
    20: "Exponential Power", 21: "Extreme Value", 22: "Laplace",
}


#: Per-distribution parameter labels -- port of Raptor 7's
#: GetParameters in its default display mode (lambdaMode /
#: muSigmaMode are preferences the legacy app toggles; Raptor's own
#: defaults use Mean / Standard Deviation wording). The empty string
#: marks a slot the legacy dialog hides entirely.
DISTRO_PARAM_LABELS = {
    1: ("Shape", "Shape2", ""),
    2: ("Shape", "", ""),
    3: ("N Trials", "Probability", ""),
    4: ("Mean", "Location", ""),
    5: ("Shape", "Scale", ""),
    6: ("Shape", "Scale", "Location"),
    7: ("Mean", "Standard Dev", ""),
    8: ("Mean", "Standard Dev", ""),
    9: ("Min", "Max", ""),
    10: ("Min", "Max", ""),
    11: ("Shape", "Scale", "Location"),
    12: ("Shape", "Shape2", "Scale"),
    13: ("Mean", "", ""),
    14: ("Min", "Mode", "Max"),
    15: ("Shape", "Scale", "Location"),
    16: ("", "", ""),
    17: ("P Success", "", ""),
    18: ("", "", ""),
    19: ("Constant", "", ""),
    20: ("Shape", "Scale", "Location"),
    21: ("Scale", "Location", ""),
    22: ("Scale", "Location", ""),
}


_LIMIT = 999999999.999999


def param_error(distro: int, idx: int, v: float, use: str) -> str | None:
    """The rules `validate_distro_params` applies to ONE parameter on its
    own (finite, in range, an integer where it must be), so a field can be
    checked when it is committed without the other fields' values. The
    rules that tie parameters together (Uniform min <= max, Triangular
    ordering) are whole-set rules: `validate_distro_params` only."""
    labels = DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
    label = labels[idx] if idx < len(labels) else ""
    if not label:
        return None
    # NaN/inf must be rejected before ANY comparison: every comparison
    # against NaN is False, so a NaN would otherwise slip past every rule
    # below and into the engine.
    if not math.isfinite(v):
        return f"{use} {label} must be a finite number"
    if v > _LIMIT:
        return f"{use} {label} must be less than 999,999,999.999999"
    key = label.lower()
    if key in ("mean", "scale", "n trials", "standard dev", "shape", "shape2"):
        if v < 0.000001:
            return f"{use} {label} must be greater than or equal to 0.000001"
        if key == "n trials" and float(round(v)) != v:
            return f"{use} N Trials must be an integer"
        # Chi-square and Erlang additionally demand an
        # integer shape -- their CASE branches run on top of the generic
        # shape rule, exactly mirrored here.
        if distro in (2, 5) and idx == 0:
            if float(round(v)) != v:
                return f"{use} shape parameter must be an integer"
    elif key in ("probability", "p success"):
        if v < 0.000001 or v > 1.0:
            return f"{use} {label} must be between 0.000001 and 1"
    elif key == "location":
        if v < 0.0:
            return f"{use} Location must be greater than or equal to 0"
    return None


def validate_distro_params(distro: int, values: list[float], use: str) -> str | None:
    """Port of Raptor 7's CheckValidInput, DEFAULT display
    mode. Returns an error message for the first violated rule, or None.

    Rules key off each parameter's label (the legacy code keys off the same
    per-distribution tag strings GetParameters produces), so a rule applies
    to every distribution whose parameter carries that label -- exactly as
    in the source. Cross-parameter rules (Triangular ordering, Uniform
    min/max) check the full value triple.
    """
    labels = DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
    for i, v in enumerate(values[: len(labels)]):
        err = param_error(distro, i, v, use)
        if err is not None:
            return err

    if distro == 8:  # Normal mean >= 0
        if values[0] < 0.0:
            return f"{use} Mean must be greater than or equal to 0"
    elif distro == 19:  # Fixed : the source's own text has no `use` prefix
        if values and values[0] < 0.0:
            return "The constant value must be greater than or equal to 0"
    elif distro == 9:  # Uniform Integer: min<=max, integers, not both 0
        lo, hi = values[0], values[1]
        if lo < 0.0 or lo > hi:
            return f"{use} Min must be less than or equal to Max and greater than or equal to 0"
        if lo > _LIMIT or hi > _LIMIT:
            return f"{use} Min and Max must be less than 999,999,999.999999"
        if float(round(lo)) != lo or float(round(hi)) != hi:
            return f"{use} Min and Max must be integers"
        if int(lo) == 0 and int(hi) == 0:
            return f"{use} Min and Max cannot both be equal to 0"
    elif distro == 10:  # Uniform Real
        lo, hi = values[0], values[1]
        if lo < 0.0 or lo > hi:
            return f"{use} Min must be less than or equal to Max and greater than or equal to 0"
        if 0.0 <= lo < 0.000001 and 0.0 <= hi < 0.000001:
            return f"{use} Min and Max cannot both be equal to 0"
    elif distro == 14:  # Triangular: Min < Mode <= Max... legacy: min<mode, min<max, mode<=max
        mn, mode, mx = values[0], values[1], values[2]
        if mn >= mode or mn >= mx or mode > mx:
            return f"{use} Min < Mode < Max must be observed"
        if mn < 0.0:
            return f"{use} Min must be greater than or equal to 0"
    return None


def params_for_mean(code: int, mean: float) -> list[float] | None:
    """Valid parameters for distribution `code`, with mean `mean` where the
    distribution has one to set, for a distribution change. Every
    distribution with parameters has an entry: Binomial had none, so a
    change from an Empirical with no points kept an empty list and the
    Inspector crashed indexing it. None only for Empirical (its points come
    from the data dialog) and None (no parameters)."""
    m = float(mean)
    n = max(1, round(m))
    # Binomial: N about twice the mean, p = mean / N (so 0.25 is N 1, p 0.25)
    bn = max(1, round(2 * m))
    return {
        1: [2.0, 2.0, 0.0],                  # Beta: two shapes
        2: [float(n), 0.0, 0.0],             # Chi-square: degrees of freedom
        3: [float(bn), min(1.0, max(m / bn, 0.000001)), 0.0],
        4: [m, 0.0, 0.0],                    # Exponential: mean
        5: [1.0, m, 0.0],                    # Erlang: shape 1, scale = mean
        6: [1.0, m, 0.0],                    # Gamma: shape 1, scale = mean
        7: [m, 0.5 * m, 0.0],                # Lognormal: mean, SD 50 %
        8: [m, 0.1 * m, 0.0],                # Normal: mean, SD 10 %
        9: [float(math.floor(0.5 * m)), float(math.ceil(1.5 * m)), 0.0],
        10: [0.5 * m, 1.5 * m, 0.0],         # Uniform: min, max
        11: [3.0, 2.0 * m, 0.0],             # Pearson 5: mean = scale/2
        12: [2.0, 3.0, m],                   # Pearson 6: mean = scale
        13: [m, 0.0, 0.0],                   # Poisson: mean
        14: [0.5 * m, m, 1.5 * m],           # Triangular: min, mode, max
        15: [1.0, m, 0.0],                   # Weibull: shape 1, scale = mean
        17: [0.5, 0.0, 0.0],                 # Point estimate: P success
        19: [m, 0.0, 0.0],                   # Fixed
        20: [2.0, 0.1 * m, m],               # Exponential power: mean = location
        # Extreme value: mean = location + 0.57721 * scale
        21: [0.1 * m, m - 0.057721 * m, 0.0],
        22: [0.1 * m, m, 0.0],               # Laplace: scale, location
    }.get(code)


def params_after_change(old_code: int, old_vals: list[float], new_code: int,
                        use: str, default_mean: float = 1.0
                        ) -> list[float] | None:
    """The parameters a distribution change should leave: the old ones
    where they are valid for the new distribution and give it the same
    mean, else new ones with the old distribution's mean (`default_mean`
    when it has none).

    Keeping the old values blindly was the trap: a phase of Fixed 0.25
    changed to Binomial read 0.25 as N Trials, and every later edit was
    refused because that field was not an integer. None when the new
    distribution takes no typed parameters (Empirical, None)."""
    from raptor.core.engine.dependency import distro_mean

    def mean_of(code: int, params: list[float]) -> float | None:
        try:
            m = float(distro_mean(code, params))
        except Exception:                                # noqa: BLE001
            return None
        return m if math.isfinite(m) else None

    vals = [float(v) for v in old_vals][:3]
    vals += [0.0] * (3 - len(vals))
    mean = mean_of(old_code, vals)
    if len(old_vals) >= _param_count(new_code) and \
            validate_distro_params(new_code, vals, use) is None:
        new_mean = mean_of(new_code, vals)
        # Kept only when they mean the same thing: Exponential(100) to
        # Poisson keeps its mean of 100, but Triangular(1, 2, 3) read as
        # Uniform(1, 2) is valid with a different mean.
        if mean is None or new_mean is None or \
                math.isclose(mean, new_mean, rel_tol=1e-9):
            return vals
    if mean is None or mean <= 0:
        mean = vals[0] if vals[0] > 0 else default_mean
    return params_for_mean(new_code, mean)


def distro_sigma(code: int, params: list[float]) -> float | None:
    """Standard deviation of a distribution, or None where it is undefined.

    The companion to the engine's `distro_mean` (`engine/dependency.py`, a
    port of `SetStats`), and written against the SAME parameterisation --
    the one `RandomGen._dispatch_draw` actually reads, not the textbook
    one:

    ===========  ===========================================  ==============
    code         parameters                                   variance
    ===========  ===========================================  ==============
    Exponential  ``(mean, shift)``                            mean**2
    Erlang       ``(shape, per-shape mean)``: mean=shape*2nd  mean**2/shape
    Gamma        ``(shape, scale, shift)``                    shape*scale**2
    Weibull      ``(shape, scale, shift)``                    scale**2*(G(1+2/k)-G(1+1/k)**2)
    Lognormal    ``(mean, stdev)`` -- already its own         stdev**2
    Normal       ``(mean, stdev)``                            stdev**2
    Triangular   ``(min, mode, max)``                         (a**2+b**2+c**2-ab-ac-bc)/18
    Pearson5/6   as `SetStats`                                see below
    ===========  ===========================================  ==============

    None for the laws the engine itself gives no mean (the 12345.6789
    "Murphy" shapes, `None`, Bernoulli's point estimate, and the
    Exponential-Power stub), and for shapes where a variance does not
    exist (Pearson 5 with shape <= 2, Pearson 6 with shape2 <= 2).
    """
    from raptor.core.engine.diststats import gamma_function
    from raptor.core.rng import Distribution

    p = list(params) + [0.0, 0.0, 0.0]
    d = int(code)
    if d == int(Distribution.EXPONENTIAL):
        return abs(p[0])
    if d == int(Distribution.ERLANG):
        k = round(p[0])
        erlang_mean = p[0] * p[1]
        return abs(erlang_mean) / math.sqrt(k) if k > 0 else None
    if d == int(Distribution.GAMMA):
        return math.sqrt(abs(p[0]) * p[1] * p[1]) if p[0] > 0 else None
    if d == int(Distribution.WEIBULL):
        weibull_shape, scale = p[0], p[1]
        if weibull_shape < 0.05:            # `SetStats`'s sentinel shapes
            return None
        if weibull_shape == 1.0 or weibull_shape > 10_000_000.0:
            return abs(scale)
        g1 = gamma_function(1.0 + 1.0 / k)
        g2 = gamma_function(1.0 + 2.0 / k)
        return abs(scale) * math.sqrt(max(0.0, g2 - g1 * g1))
    if d in (int(Distribution.LOGNORMAL), int(Distribution.NORMAL)):
        return abs(p[1])
    if d == int(Distribution.UNIFORM_REAL):
        return abs(p[1] - p[0]) / math.sqrt(12.0)
    if d == int(Distribution.UNIFORM_INTEGER):
        n = (round(p[1]) - round(p[0]) + 1)
        return math.sqrt(max(0.0, (n * n - 1) / 12.0))
    if d == int(Distribution.TRIANGULAR):
        a, b, c = p[0], p[1], p[2]
        var = (a * a + b * b + c * c - a * b - a * c - b * c) / 18.0
        return math.sqrt(max(0.0, var))
    if d == int(Distribution.PEARSON5):
        shape, scale = p[0], p[1]
        if shape <= 2.0:                    # no finite variance
            return None
        return abs(scale) / ((shape - 1.0) * math.sqrt(shape - 2.0))
    if d == int(Distribution.PEARSON6):
        k1, k2, scale = p[0], p[1], p[2]
        if k1 <= 0.0 or k2 <= 2.0:          # no finite variance
            return None
        # x = scale * (G1/G2) with G_i ~ Gamma(shape=k_i, rate=1), i.e.
        # (k1/k2) * F(2k1, 2k2) in scale.
        d1, d2 = 2.0 * k1, 2.0 * k2
        var_f = (2.0 * d2 * d2 * (d1 + d2 - 2.0)
                 / (d1 * (d2 - 2.0) ** 2 * (d2 - 4.0)))
        return abs(scale) * (k1 / k2) * math.sqrt(max(0.0, var_f))
    if d == int(Distribution.BETA):
        a, b = p[0], p[1]
        beta_total = a + b
        if a <= 0.0 or b <= 0.0:
            return None
        return math.sqrt(
            a * b / (beta_total * beta_total * (beta_total + 1.0)))
    if d == int(Distribution.BINOMIAL):
        binom_n, prob = p[0], p[1]
        return math.sqrt(max(0.0, binom_n * prob * (1.0 - prob)))
    if d == int(Distribution.CHI_SQUARE):
        return math.sqrt(2.0 * max(0, round(p[0])))
    if d == int(Distribution.POISSON):
        return math.sqrt(max(0.0, p[0]))
    if d == int(Distribution.FIXED):
        return 0.0
    if d == int(Distribution.EMPIRICAL):
        vals = [v for v in params if v == v]
        if not vals:
            return 0.0
        mean = sum(vals) / len(vals)
        return math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    return None


def _param_count(code: int) -> int:
    return sum(1 for lab in DISTRO_PARAM_LABELS.get(code, ("", "", ""))
               if lab)


#: The distributions Raptor 7 offers, by picker, in its own (alphabetical)
#: order: the item lists of its `FailCombo`, `RepairCombo` and `DistComboBox`
#: dialog controls.
#: `DistBox` is the generic distribution dialog `ShowDistBox` opens for the
#: pre- and post-repair delays, the PM duration, phase lengths and triggers
#: Only a block's failure and
#: repair laws offer Empirical; an event takes a success probability only
#: (code 17; the `EventsBox` dialog has no distribution choice).
RAPTOR7_LISTS: dict[str, tuple[int, ...]] = {
    "failure": (1, 3, 2, 16, 5, 4, 21, 6, 22, 7, 8, 11, 12, 13, 14, 9, 10, 15),
    "repair": (1, 3, 2, 16, 5, 4, 21, 19, 6, 22, 7, 18, 8, 11, 12, 13, 14, 9, 10, 15),
    "delay": (1, 3, 2, 5, 4, 21, 19, 6, 22, 7, 8, 11, 12, 13, 14, 9, 10, 15),
    "event": (17,),
}

#: What this port's pickers offer: Raptor 7's lists, plus Fixed (19) for a
#: failure law. Raptor 7's failure combo leaves Fixed out, but its engine
#: runs a block that fails by Fixed (files written by other tools carry it),
#: and the API has always accepted it, so the GUI offers it too. Fixed sits
#: where Raptor 7's repair list puts it, after Gumbel.
OFFERED: dict[str, tuple[int, ...]] = {
    **RAPTOR7_LISTS,
    "failure": (1, 3, 2, 16, 5, 4, 21, 19, 6, 22, 7, 8, 11, 12, 13, 14, 9, 10, 15),
}

"""Raptor's parameter-entry modes: lambda vs mean, and mu/sigma vs mean/sd.

The file always stores canonical values (Exponential = mean; Lognormal =
mean + standard deviation). Two Advanced preferences change only how those
numbers are DISPLAYED and TYPED:

* **lambda mode** (`editing/exp_mode == "rate"`): an Exponential's first
  parameter is entered as a failure/repair rate, 1/mean. Raptor applies
  this to block failure and repair laws (`GetParameters`,
  `typeOfDistro <> "other"`), not to the generic distribution boxes used
  for phase lengths, pool rows and triggers.
* **mu-sigma mode** (`editing/ln_mode == "mu_sigma"`): a Lognormal's two
  parameters are entered in normal (log) space. Raptor applies this to
  every distribution box, including the generic ones.

Numbers are converted only when the user changes a field, matching the
legacy `startfVal` comparisons: re-deriving an untouched value from a
rounded display string would quietly move the model's mean.
"""
from __future__ import annotations

import math

from raptor.core.distro_rules import DISTRO_PARAM_LABELS

EXPONENTIAL = 4
LOGNORMAL = 7


def exponential_is_rate() -> bool:
    """Whether Exponential parameters are typed as rates, per the preference."""
    from raptor.gui.ui import settings
    return str(settings.get("editing/exp_mode", "mean")) == "rate"


def lognormal_is_mu_sigma() -> bool:
    """Whether Lognormal parameters are typed in normal (log) space."""
    from raptor.gui.ui import settings
    return str(settings.get("editing/ln_mode", "mean_sd")) == "mu_sigma"


def labels(distro: int, *, lambda_mode: bool | None = None
           ) -> tuple[str, str, str]:
    """Display labels for a distribution's three slots.

    `lambda_mode=False` asks for canonical wording even when the
    preference is on -- the generic "other" boxes, where Raptor does not
    apply lambda mode."""
    try:
        code = int(distro)
    except (TypeError, ValueError):
        return DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
    base = DISTRO_PARAM_LABELS.get(code, ("", "", ""))
    if code == EXPONENTIAL:
        enabled = exponential_is_rate() if lambda_mode is None else lambda_mode
        if enabled:
            return ("Rate", base[1], base[2])
    elif code == LOGNORMAL and lognormal_is_mu_sigma():
        return ("Mu", "Sigma", base[2])
    return base


def is_rate_field(distro: int, idx: int, *,
                  lambda_mode: bool | None = None) -> bool:
    enabled = exponential_is_rate() if lambda_mode is None else lambda_mode
    return bool(enabled and int(distro) == EXPONENTIAL and idx == 0)


def is_mu_sigma(distro: int) -> bool:
    return int(distro) == LOGNORMAL and lognormal_is_mu_sigma()


def to_entry(distro: int, values: list[float], *,
             lambda_mode: bool | None = None) -> list[float]:
    """Canonical stored values -> the numbers the entry form shows."""
    out = list(values)
    if is_rate_field(distro, 0, lambda_mode=lambda_mode):
        # A zero or negative mean has no meaningful rate; leave it visible
        # so the field shows the problem instead of a fabricated number.
        out[0] = 1.0 / out[0] if out[0] > 0.0 else out[0]
    elif is_mu_sigma(distro):
        mean, sd = out[0], out[1]
        if mean > 0.0:
            cv2 = (sd / mean) ** 2
            out[0] = math.log(mean) - 0.5 * math.log1p(cv2)
            out[1] = math.sqrt(math.log1p(cv2))
        else:
            out[0] = out[1] = 0.0
    return out


def from_entry(distro: int, values: list[float],
               old: list[float] | None = None, *,
               lambda_mode: bool | None = None) -> list[float]:
    """Entry-form numbers -> canonical stored values.

    `old` is the currently stored canonical set. A field still holding the
    value `old` displays as (within display rounding) is kept verbatim, so
    committing one field never re-derives the others from a rounded
    string. Without `old`, every value is converted -- correct for a
    brand-new set, e.g. a dialog's factory defaults.
    """
    out = list(values)
    if is_rate_field(distro, 0, lambda_mode=lambda_mode):
        unchanged = (old is not None and old[0] > 0.0
                     and math.isclose(out[0], 1.0 / old[0],
                                      rel_tol=1e-5, abs_tol=0.0))
        if unchanged:
            out[0] = old[0]
        elif out[0] > 0.0:
            out[0] = 1.0 / out[0]
        elif old is not None:
            out[0] = old[0]
    elif is_mu_sigma(distro):
        unchanged = False
        if old is not None:
            shown = to_entry(distro, old)
            unchanged = (math.isclose(out[0], shown[0], rel_tol=1e-5,
                                      abs_tol=1e-9)
                         and math.isclose(out[1], shown[1], rel_tol=1e-5,
                                          abs_tol=1e-9))
        if unchanged:
            out[0], out[1] = old[0], old[1]
        else:
            mu, sigma = out[0], out[1]
            if sigma > 0.0:
                out[0] = math.exp(mu + sigma * sigma / 2.0)
                out[1] = math.sqrt(math.exp(2.0 * mu + sigma * sigma)
                                   * math.expm1(sigma * sigma))
            else:
                out[0] = math.exp(mu)
                out[1] = 0.0
    return out


def unit_for(distro: int, idx: int, units: str, *,
             lambda_mode: bool | None = None) -> str:
    """Unit text for one parameter slot, or "" when it has no time unit.

    A rate is per time (`per hours`); mu/sigma live in log space
    (`ln(hours)`); every other time-dimensioned slot is plain `hours`."""
    if not units:
        return ""
    if is_rate_field(distro, idx, lambda_mode=lambda_mode):
        return f"per {units}"
    if is_mu_sigma(distro) and idx in (0, 1):
        return f"ln({units})"
    return units

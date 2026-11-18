"""Distribution reference data: re-exported from `raptor.core.distributions`.

The code lives in core so lower layers (for example the AWB reader) can use
it without importing upward; this module keeps the API's import path.
"""
from __future__ import annotations

from raptor.core.distributions import (
    LOOSE_FIT,
    PARAM_INFO,
    describe_dist,
    dist_name,
    experience_changes,
    fit_document,
    from_quantiles,
    scale_mean,
    scales_with_mean,
)

__all__ = [
    "LOOSE_FIT",
    "PARAM_INFO",
    "describe_dist",
    "dist_name",
    "experience_changes",
    "fit_document",
    "from_quantiles",
    "scale_mean",
    "scales_with_mean",
]

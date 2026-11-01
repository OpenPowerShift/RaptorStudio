"""Analytical (static) solver for coherent RBDs.

Public surface:

    result = build(model, mode="reliability")
    reliability.evaluate.system_probability(result, t)

See `docs/design/analytic-solver.md` for the scope and assumptions.
"""
from __future__ import annotations

from .bdd import BDD, recursion_headroom
from .errors import AnalyticalError, BddTooLarge, NotApplicable, UnsupportedDistribution
from .network import BlockInfo, BuildResult, build
from . import evaluate, survival

__all__ = [
    "BDD",
    "AnalyticalError",
    "BddTooLarge",
    "BlockInfo",
    "BuildResult",
    "NotApplicable",
    "UnsupportedDistribution",
    "build",
    "evaluate",
    "recursion_headroom",
    "survival",
]

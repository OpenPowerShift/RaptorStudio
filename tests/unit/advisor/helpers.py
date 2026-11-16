"""Small models for advisor tests, built from specs (never binary files)."""
from __future__ import annotations

import copy
from itertools import pairwise

from raptor.api import build
from raptor.api.advisor import advise_model

WEAROUT = {"fail_dist": "Weibull", "fail_params": [2.5, 4000, 0],
           "repair_dist": "Lognormal", "repair_params": [12, 4, 0]}
RANDOM = {"fail_dist": "Exponential", "fail_params": [5000, 0, 0],
          "repair_dist": "Lognormal", "repair_params": [12, 4, 0]}
RUN = {"trials": 200, "termination_type": "TimeSim", "term_condition": 8760}


def series_spec(*blocks: dict, **extra) -> dict:
    names = [b["name"] for b in blocks]
    chain = ["Start", *names, "End"]
    spec = {"blocks": [copy.deepcopy(b) for b in blocks],
            "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
            "links": [{"from": a, "to": b} for a, b in pairwise(chain)],
            "experiment": dict(RUN)}
    spec.update(copy.deepcopy(extra))
    return spec


def parallel_spec(*blocks: dict, k: int = 1, **extra) -> dict:
    links = [{"from": "Start", "to": "Split"}, {"from": "Join", "to": "End"}]
    for b in blocks:
        links += [{"from": "Split", "to": b["name"]}, {"from": b["name"], "to": "Join"}]
    spec = {"blocks": [copy.deepcopy(b) for b in blocks],
            "nodes": [{"name": "Start", "type": 1}, {"name": "Split", "type": 2, "k": 1},
                      {"name": "Join", "type": 2, "k": k}, {"name": "End", "type": 3}],
            "links": links, "experiment": dict(RUN)}
    spec.update(copy.deepcopy(extra))
    return spec


def block(name: str, base: dict = RANDOM, **fields) -> dict:
    return {"name": name, **copy.deepcopy(base), **fields}


def findings(spec: dict, rule: str | None = None):
    out = advise_model(build.build_from_spec(spec))
    return [f for f in out if rule is None or f.rule == rule]


def rules_fired(spec: dict) -> set[str]:
    return {f.rule for f in findings(spec)}

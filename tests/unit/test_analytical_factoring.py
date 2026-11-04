"""Dominator factoring, the backward variable order and the size outlook.

Both are exact rewrites of the same Boolean function, so every result must
match the original build (no factoring, forward order) to rounding.
"""
from __future__ import annotations

import math

import pytest

from raptor.api.analytical import analyse_rbd
from raptor.core.analytical import network
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import load_rbds

_FIXTURES = ["tests/golden/LadL1Parallel/input.rbd",
             "tests/golden/PreventMxDemo/input.rbd",
             "tests/golden/TwoBlockRedundancy/input.rbd",
             "new_docs/models/WaterTreatment.rbds",
             "new_docs/models/HospitalPower.rbds",
             "new_docs/models/DC2.rbds"]


def _load(path):
    return load_rbds(path) if path.endswith(".rbds") else load_rbd(path)


def _solve(rbd, original: bool, monkeypatch, **kw):
    with monkeypatch.context() as patch:
        if original:
            patch.setattr(network, "_immediate_dominators",
                          lambda *a, **k: {})
            patch.setattr(network, "VARIABLE_ORDER", "forward")
        return analyse_rbd(rbd, "m", **kw)


@pytest.mark.parametrize("path", _FIXTURES)
@pytest.mark.parametrize("mode", ["reliability", "availability"])
def test_factored_build_matches_the_original(path, mode, monkeypatch):
    rbd = _load(path)
    kw = dict(mode=mode, ignore=["pm", "spares", "resources"],
              standby="active", dependencies="up")
    if mode == "reliability":
        kw.update(ignore_repairs=True, at=100.0)
    new = _solve(rbd, False, monkeypatch, **kw)
    old = _solve(rbd, True, monkeypatch, **kw)
    key = "reliability" if mode == "reliability" else "availability"
    assert math.isclose(new[key], old[key], rel_tol=1e-10, abs_tol=1e-13)
    by_id = {(r["kind"], r["id"]): r["birnbaum"] for r in old["importance"]}
    for row in new["importance"]:
        assert math.isclose(row["birnbaum"], by_id[(row["kind"], row["id"])],
                            rel_tol=1e-9, abs_tol=1e-12)


def test_hierarchy_1_7_fits_the_default_limit_now():
    """Over 2,000,000 nodes before factoring and the backward order;
    about 78,000 now."""
    result = analyse_rbd(load_rbd("tests/golden/Hierarchy1-7/input.rbd"), "m",
                         mode="availability", importance=False,
                         ignore=["pm", "spares", "resources"])
    assert result["bdd_nodes"] < 200_000


def _outlook(path):
    stages: list[str] = []
    analyse_rbd(_load(path), "m", mode="availability", importance=False,
                ignore=["pm", "spares", "resources"],
                progress=lambda s, d, t: stages.append(s))
    return next(s for s in stages if s.startswith("Outlook: "))


def test_outlook_calls_a_shared_feeder_harmless():
    """WaterTreatment's k-of-n stages are fed through common elements,
    which the build factors out: not a size risk."""
    assert "not exponentially" in _outlook("new_docs/models/WaterTreatment.rbds")


def test_outlook_names_a_junction_whose_inputs_share_blocks():
    """A bridge: End's two inputs both depend on block A without one common
    element ahead of both, so End is named."""
    from raptor.api.build import build_from_spec
    b = lambda n: {"name": n, "fail_dist": 4, "fail_params": [100.0, 0.0],
                   "repair_dist": 4, "repair_params": [5.0, 0.0]}
    spec = {"system": {"run_length": 100.0, "num_runs": 1,
                       "termination": "TimeSim"},
            "blocks": [b(n) for n in "ABCDE"],
            "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 2},
                      {"name": "L", "type": 2, "n": 1, "out": 2, "k": 1},
                      {"name": "R", "type": 2, "n": 2, "out": 1, "k": 1},
                      {"name": "End", "type": 3, "n": 2, "out": 0}],
            "links": [{"from": f, "to": t} for f, t in (
                ("Start", "A"), ("Start", "B"), ("A", "L"), ("B", "R"),
                ("L", "C"), ("L", "E"), ("E", "R"), ("R", "D"),
                ("C", "End"), ("D", "End"))]}
    rbd = build_from_spec(spec, auto_layout=False)
    stages: list[str] = []
    analyse_rbd(rbd, "bridge", mode="availability", importance=False,
                progress=lambda s, d, t: stages.append(s))
    outlook = next(s for s in stages if s.startswith("Outlook: "))
    assert "may grow large" in outlook and "End" in outlook


def test_evaluation_reports_progress_and_can_be_cancelled():
    """After the build, MTTF and the curve walk the diagram many times;
    progress keeps coming, and raising from it stops the solve."""
    import time as _time
    rbd = _load("tests/golden/Hierarchy1-7/input.rbd")
    stages: list[str] = []
    started = _time.monotonic()

    def progress(stage, done, total):
        stages.append(stage)
        if " of about " in stage:
            raise KeyboardInterrupt   # stands in for the GUI's cancel
    with pytest.raises(KeyboardInterrupt):
        analyse_rbd(rbd, "m", mode="reliability", ignore_repairs=True,
                    ignore=["pm", "spares", "resources"], at=100.0,
                    importance=False, progress=progress)
    assert any(s.startswith("Working out") for s in stages)
    assert _time.monotonic() - started < 10.0

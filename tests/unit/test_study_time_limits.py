"""A study (what-if, sensitivity, the optimisers) holds to one time limit
over all its runs and says when it stopped early (round-2 testing F5, F6,
F8, F15). Each variant used to get the whole `timeout_s` afresh, and a run
that hit it set the caller's abort, so the study stopped silently."""
from __future__ import annotations

import tempfile
import threading
import time

import pytest


def _model():
    from raptor.api.build import build_from_spec, series
    from raptor.api.workspace import Workspace
    ws = Workspace(tempfile.mkdtemp())
    info = ws.register_model(build_from_spec(series(3)), name="s")
    return ws, info["model_id"]


def test_deadline_tells_time_from_cancel():
    from raptor.api.batch import Deadline
    parent = threading.Event()
    limit = Deadline(parent, 0.05)
    assert not limit.is_set()
    time.sleep(0.08)
    assert limit.is_set() and limit.expired() and not limit.cancelled()
    child = Deadline(limit)
    assert child.is_set() and not child.cancelled()
    parent.set()
    assert Deadline(parent).cancelled()


def test_variants_share_one_time_limit_and_say_so():
    from raptor.api.batch import cut_short, run_variants
    ws, mid = _model()
    variants = [{"label": f"v{i}", "assignments": []} for i in range(5)]
    results = run_variants(ws, mid, variants, num_runs=5, timeout_s=1e-4)
    assert len(results) == 5
    assert all(row.get("skipped") == "time" for row in results[1:])
    note = cut_short(results, 1e-4)
    assert note and "time limit" in note and "did not run" in note


def test_a_cancel_is_not_called_a_time_limit():
    from raptor.api.batch import cut_short, run_variants
    ws, mid = _model()
    abort = threading.Event()
    abort.set()
    results = run_variants(ws, mid, [{"label": "a", "assignments": []}] * 2,
                           num_runs=5, timeout_s=600, abort=abort)
    assert {row["skipped"] for row in results} == {"cancelled"}
    assert "cancelled" in cut_short(results)


def test_a_complete_study_is_not_cut_short():
    from raptor.api.batch import cut_short, run_variants
    ws, mid = _model()
    results = run_variants(ws, mid, [{"label": "a", "assignments": []}],
                           num_runs=2, timeout_s=600)
    assert results[0]["requested_runs"] == 2
    assert cut_short(results) is None


def test_sensitivity_with_nothing_measured_is_an_error():
    from raptor.api.run import StudyTimeoutError
    from raptor.api.scenarios.sensitivity import default_inputs, tornado
    ws, mid = _model()
    inputs = default_inputs(ws.get(mid).rbd, limit=2)
    with pytest.raises(StudyTimeoutError, match="Nothing was measured"):
        tornado(ws, mid, inputs, trials=5, timeout_s=1e-4)


def test_a_cut_short_run_is_marked_partial():
    from raptor.api.run import run, run_dir
    from raptor.api.run_tables import partial_note, read_summary
    ws, mid = _model()
    abort = threading.Event()
    done = []

    def progress(n, total):
        done.append(n)
        if n >= 2:
            abort.set()

    result = run(ws, mid, num_runs=200, abort=abort, progress=progress,
                 confirm_long_run=True)
    summary = read_summary(run_dir(ws, result.run_id), result.run_id)
    assert summary["requested_runs"] == 200
    assert summary["truncated"] == "cancelled"
    assert "of 200" in partial_note(summary)
    full = run(ws, mid, num_runs=2, confirm_long_run=True)
    assert partial_note(read_summary(run_dir(ws, full.run_id),
                                     full.run_id)) is None


# --- the analytical solver on a chain of parallel pairs (round-2 F1) -------

def _pairs(groups: int) -> dict:
    blocks, links = [], []
    nodes = [{"name": "Start", "type": 1, "n": 0, "out": 1},
             {"name": "End", "type": 3, "n": 1, "out": 0}]
    prev = "Start"
    for i in range(groups):
        nodes += [{"name": f"S{i}", "type": 2, "n": 1, "out": 2, "k": 1},
                  {"name": f"J{i}", "type": 2, "n": 2, "out": 1, "k": 1}]
        links.append({"from": prev, "to": f"S{i}"})
        for j in range(2):
            blocks.append({"name": f"G{i}_{j}", "fail_dist": 4,
                           "fail_params": [700, 0]})
            links += [{"from": f"S{i}", "to": f"G{i}_{j}"},
                      {"from": f"G{i}_{j}", "to": f"J{i}"}]
        prev = f"J{i}"
    links.append({"from": prev, "to": "End"})
    return {"blocks": blocks, "nodes": nodes, "links": links}


def test_a_chain_of_parallel_pairs_solves_in_linear_size():
    """The walk back from End took each join's first branch through its
    split and on to Start before the second: the diagram doubled with each
    pair and 40 blocks reached the 2,000,000-node limit."""
    import math

    from raptor.api.analytical import analyse
    from raptor.api.build import build_from_spec
    from raptor.api.workspace import Workspace
    ws = Workspace(tempfile.mkdtemp())
    mid = ws.register_model(build_from_spec(_pairs(30)), name="p")["model_id"]
    result = analyse(ws, mid, mode="reliability", at=100.0,
                     ignore_repairs=True, max_bdd_nodes=5000)
    q = 1 - math.exp(-100 / 700)
    assert result["reliability"] == pytest.approx((1 - q * q) ** 30, rel=1e-9)
    assert result["bdd_nodes"] < 400


def test_a_too_large_diagram_is_not_called_outside_the_subset():
    from raptor.core.analytical.errors import BddTooLarge
    from raptor.mcp.result import error_result
    hint = error_result(BddTooLarge("the solver's diagram reached the limit"),
                        "analytical_analysis")
    text = " ".join(c.text for c in hint.content)
    assert "outside" not in text and "too large" in text

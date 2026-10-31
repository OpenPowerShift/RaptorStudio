"""A pool worker loads the model once and runs every chunk on a copy (#28).

A multi-core batch is split into up to 64 chunks and each chunk used to parse
and load the model again. The worker now keeps one unrun model and every chunk
runs on a deep copy of it. A chunk must give exactly what a freshly loaded
model gives -- including after an earlier chunk has already run in the same
worker, which is the case that would show leaked state.
"""
from __future__ import annotations

import glob
import os
import pathlib

import pytest

from raptor.core.engine import runner
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import render_rbds
from raptor.core.rng import _M as RNG_M

GOLD = pathlib.Path(__file__).resolve().parents[1] / "golden"
FIXTURES = sorted(os.path.basename(os.path.dirname(p))
                  for p in glob.glob(str(GOLD / "*" / "input.rbd")))
STRIDE = max(RNG_M - 1, 1) // 64


def _payload(text, runs, chunk):
    return (text, runs, (chunk + 1) * STRIDE, 10.0, 720.0, True, 0, "")


def _comparable(res):
    # The interim-cost header carries the wall-clock time ("Interim Cost
    # Report for model.rbds  Fri Oct 30 19:31:48 2026"), which differs from
    # one second to the next between any two runs, copy or not.
    cost = [ln for ln in res["cost"] if not ln.startswith("Interim Cost Report")]
    return (res["logs"], res["done"], res["negative_draws"], res["max_draws"],
            len(res["reports"]), res["cap"], res["ao"], cost)


@pytest.fixture(autouse=True)
def _clean_template():
    runner._TEMPLATE = None
    yield
    runner._TEMPLATE = None


@pytest.mark.parametrize("fixture", FIXTURES)
def test_a_chunk_on_a_copy_matches_a_chunk_on_a_fresh_load(fixture, monkeypatch):
    text = render_rbds(load_rbd(str(GOLD / fixture / "input.rbd")))
    runner._run_chunk_worker(_payload(text, 1, 0))        # primes the template
    assert runner._TEMPLATE is not None
    on_copy = runner._run_chunk_worker(_payload(text, 2, 1))

    monkeypatch.setattr(runner, "_model_template", lambda *a: None)
    fresh = runner._run_chunk_worker(_payload(text, 2, 1))
    assert _comparable(on_copy) == _comparable(fresh)


def test_the_template_is_loaded_once_per_model(monkeypatch):
    text = render_rbds(load_rbd(str(GOLD / "MotorDemo" / "input.rbd")))
    loads = []
    real = runner.load_model
    monkeypatch.setattr(runner, "load_model", lambda *a, **k: (loads.append(1), real(*a, **k))[1])
    for chunk in range(3):
        runner._run_chunk_worker(_payload(text, 1, chunk))
    assert len(loads) == 1


def test_a_different_model_replaces_the_template():
    a = render_rbds(load_rbd(str(GOLD / "MotorDemo" / "input.rbd")))
    b = render_rbds(load_rbd(str(GOLD / "Cascade" / "input.rbd")))
    runner._run_chunk_worker(_payload(a, 1, 0))
    first = runner._TEMPLATE
    runner._run_chunk_worker(_payload(b, 1, 0))
    assert runner._TEMPLATE is not first and runner._TEMPLATE[0] != first[0]


def test_the_template_itself_is_never_run():
    text = render_rbds(load_rbd(str(GOLD / "MotorDemo" / "input.rbd")))
    runner._run_chunk_worker(_payload(text, 1, 0))
    template = runner._TEMPLATE[1]
    assert all(b.op_status.value == "Running" for b in template.blocks_by_id.values())

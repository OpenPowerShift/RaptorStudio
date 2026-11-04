"""The numpy evaluation must give the same numbers as the plain loop.

`probability` (vectorised above `_VECTOR_FROM` nodes) and `probability_batch`
use the same expression in the same order as the Python loop, so they agree
to the last bit. Batched MTTF and BX search differ only in summation order
and bracket steps, within their own tolerances.
"""
from __future__ import annotations

import math
import random

import numpy as np
import pytest

from raptor.api.analytical import analyse_rbd
from raptor.core.analytical import bdd as bdd_module
from raptor.core.analytical import evaluate
from raptor.core.analytical.bdd import BDD
from raptor.core.fileio.rbd import load_rbd


def _diagram(variables: int = 40, seed: int = 3) -> tuple[BDD, int]:
    rng = random.Random(seed)
    bdd = BDD()
    vs = [bdd.variable(i) for i in range(variables)]
    root = 0
    for _ in range(20):
        picks = rng.sample(range(variables), 3)
        root = bdd.Or(root, bdd.And(*(vs[i] for i in picks)))
    return bdd, root


def test_vectorised_pass_matches_the_loop_to_the_bit(monkeypatch):
    bdd, root = _diagram()
    assert len(bdd._reachable(root)) >= 500
    rng = random.Random(1)
    for _ in range(20):
        probs = [rng.random() for _ in range(40)]
        monkeypatch.setattr(bdd_module, "_VECTOR_FROM", 10 ** 9)
        loop = bdd.probability(root, probs)
        monkeypatch.setattr(bdd_module, "_VECTOR_FROM", 0)
        vector = bdd.probability(root, probs)
        assert vector == loop


def test_batch_matches_single_passes_and_chunks(monkeypatch):
    bdd, root = _diagram()
    rng = np.random.default_rng(7)
    table = rng.random((40, 37))
    one_by_one = [bdd.probability(root, table[:, j].tolist())
                  for j in range(37)]
    assert bdd.probability_batch(root, table).tolist() == one_by_one
    # A tiny memory budget forces one-column chunks: same answer.
    monkeypatch.setattr(bdd_module, "_BATCH_BYTES", 1)
    assert bdd.probability_batch(root, table).tolist() == one_by_one


def test_batch_counts_each_chunk_once_it_is_done(monkeypatch):
    """A big batch (importance on a large diagram is thousands of cases and
    many seconds) must report as it goes. Counting the whole batch before
    the work left the progress at "the last pass" for all of that time."""
    bdd, root = _diagram()
    table = np.random.default_rng(2).random((40, 5))
    monkeypatch.setattr(bdd_module, "_BATCH_BYTES", 1)     # 1-column chunks
    seen: list[int] = []
    real = bdd_module._Plan.evaluate

    def evaluate_chunk(plan, chunk):
        seen.append(-chunk.shape[1])       # work starts...
        return real(plan, chunk)

    monkeypatch.setattr(bdd_module._Plan, "evaluate", evaluate_chunk)
    bdd.on_pass = seen.append              # ...and is counted after
    bdd.probability_batch(root, table)
    assert seen == [-1, 1] * 5


def test_batch_rejects_a_probability_outside_0_1():
    bdd, root = _diagram()
    table = np.full((40, 3), 0.5)
    table[5, 1] = 1.5
    with pytest.raises(ValueError, match="not in"):
        bdd.probability_batch(root, table)


@pytest.mark.parametrize("f", [
    lambda x: math.exp(-x / 7.0),
    lambda x: 1.0 if x < 3.3 else 0.0,           # a step, like a Fixed life
    lambda x: 1.0 / (1.0 + x * x),
])
def test_batched_simpson_matches_the_recursive_one(f):
    plain = evaluate._adaptive_simpson(f, 0.0, 20.0, tol=1e-9, max_depth=24)
    batched = evaluate._adaptive_simpson_batched(
        lambda xs: [f(x) for x in xs], 0.0, 20.0, tol=1e-9, max_depth=24)
    assert batched == pytest.approx(plain, rel=1e-12, abs=1e-15)


def test_bx_and_mttf_on_an_exponential_block():
    """One exponential block, mean 1,000: MTTF = 1,000 and
    B10 = -1,000 ln 0.9 = 105.36."""
    result = analyse_rbd(load_rbd("tests/golden/TwoBlockRedundancy/input.rbd"),
                         "m", mode="reliability", ignore_repairs=True,
                         at=100.0, bx=[10.0, 50.0], importance=False)
    assert result["bx"]["10"] < result["bx"]["50"]
    assert result["mttf"] > 0.0
    # Reliability at the reported B10 is 0.9 to the search tolerance.
    from raptor.core.analytical import build as build_analytical
    from raptor.core.engine.load import load_model
    model = load_model(load_rbd("tests/golden/TwoBlockRedundancy/input.rbd"))
    built = build_analytical(model, mode="reliability", ignore_repairs=True)
    for key, target in (("10", 0.9), ("50", 0.5)):
        assert evaluate.system_probability(built, result["bx"][key]) == \
            pytest.approx(target, abs=1e-9)


def test_importance_reports_progress_when_asked():
    from raptor.core.analytical import build as build_analytical
    from raptor.core.engine.load import load_model
    model = load_model(load_rbd("tests/golden/TwoBlockRedundancy/input.rbd"))
    built = build_analytical(model, mode="availability")
    seen: list[tuple[str, int, int]] = []
    rows = evaluate.importance(built, progress=lambda *a: seen.append(a))
    assert rows and seen[0][1] == 0 and seen[-1][1] == seen[-1][2] == len(rows)

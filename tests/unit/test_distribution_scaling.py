"""`scale_mean` keeps a law's shape and moves its mean by the factor."""
from __future__ import annotations

import pytest

from raptor.api.distributions import scale_mean, scales_with_mean
from raptor.core.engine.diststats import compute_stats

CASES = [
    (4, [100.0, 0.0]),
    (5, [100.0, 3.0, 0.0]),
    (6, [100.0, 2.0, 0.0]),
    (7, [100.0, 20.0]),
    (8, [100.0, 20.0]),
    (13, [100.0]),
    (15, [2.0, 100.0, 0.0]),
    (15, [2.0, 100.0, 25.0]),      # a failure-free period scales too
    (4, [100.0, 30.0]),
    (6, [100.0, 2.0, 40.0]),
    (14, [10.0, 20.0, 60.0]),
    (19, [100.0]),
    (21, [100.0, 0.0]),
    (22, [100.0, 50.0]),
]


@pytest.mark.parametrize("code,params", CASES)
def test_the_mean_scales_and_the_shape_does_not(code, params):
    before = compute_stats(code, list(params))
    after = compute_stats(code, scale_mean(code, params, 2.0))
    assert after[0] == pytest.approx(2.0 * before[0], rel=2e-6)
    if code != 13 and before[1] and after[1] is not None:
        assert after[1] / after[0] == pytest.approx(before[1] / before[0],
                                                    rel=2e-5)


def test_a_law_without_a_scale_slot_says_so():
    assert not scales_with_mean(1)
    with pytest.raises(ValueError, match="scale"):
        scale_mean(1, [2.0, 3.0], 2.0)


def test_the_original_parameters_are_not_changed():
    params = [2.0, 100.0, 0.0]
    scale_mean(15, params, 4.0)
    assert params == [2.0, 100.0, 0.0]


def test_erlang_and_gamma_name_their_slots_as_the_engine_reads_them():
    """The engine draws Erlang(p1 * p2, round(p1)): slot 1 is k, slot 2 the
    scale. They were labelled the other way round ("mean", "k")."""
    from raptor.api.distributions import describe_dist
    erlang = describe_dist(5, [3, 10])
    assert erlang["params"] == {"k": 3, "scale": 10}
    assert erlang["mean"] == 30.0
    gamma = describe_dist(6, [2.0, 5.0, 1.0])
    assert gamma["params"] == {"shape": 2.0, "scale": 5.0, "location": 1.0}
    assert gamma["mean"] == 11.0

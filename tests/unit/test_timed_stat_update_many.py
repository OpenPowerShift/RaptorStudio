"""`update_many` is `update` over a dict, bit for bit.

Capacity analysis touches every node's flow and capacity monitor at the end of
every state-changing instant, even when the value did not change (that split of
the integral is deliberate: see `capacity.touch_capacity_monitors`). On a
3,470-block model that was about 10 million `update` calls over five simulated
years, half the run. `update_many` does the same arithmetic in the same order
with the rounding done once; these tests require identical floats, not close
ones.
"""
from __future__ import annotations

import random

import pytest

from raptor.core.engine.stats import TimedStat, update_many


def _state(m: TimedStat):
    return (m.value, m.start_time, m._epoch, m._area, m.minimum, m.maximum)


def _fresh(n, rng):
    out = {}
    for k in range(n):
        m = TimedStat()
        m.reset(rng.choice([0, 0, 3, 100]), 0.0)
        out[k] = m
    return out


@pytest.mark.parametrize("seed", range(8))
def test_a_random_sequence_gives_the_same_floats_as_update(seed):
    rng = random.Random(seed)
    a, b = _fresh(40, rng), None
    rng2 = random.Random(seed)
    b = _fresh(40, rng2)
    t = 0.0
    for _ in range(300):
        t += rng.choice([0.0, 0.0, 1e-10, 0.37, 1.0 / 3.0, 12.5, 1e4 / 7.0])
        values = {k: rng.choice([0, 1, 5, 5, 100, 26880]) for k in a}
        for k, v in values.items():
            a[k].update(v, t)
        update_many(b, values, t)
    for k in a:
        assert _state(a[k]) == _state(b[k]), k
    assert [a[k].mean_at(t + 5.0) for k in a] == [b[k].mean_at(t + 5.0) for k in b]


def test_time_going_backwards_adds_nothing_exactly_as_update_does():
    m1, m2 = TimedStat(), TimedStat()
    for m in (m1, m2):
        m.reset(7, 10.0)
    m1.update(9, 5.0)
    update_many({0: m2}, {0: 9}, 5.0)
    assert _state(m1) == _state(m2)


def test_the_time_is_rounded_the_same_way():
    m1, m2 = TimedStat(), TimedStat()
    for m in (m1, m2):
        m.reset(2, 0.0)
    m1.update(4, 1.23456789012345)
    update_many({0: m2}, {0: 4}, 1.23456789012345)
    assert _state(m1) == _state(m2) and m2._epoch == round(1.23456789012345, 9)


def test_the_first_assignment_sets_both_extrema():
    m = TimedStat()
    m.reset(0, 0.0)
    assert m.minimum == float("inf")
    update_many({0: m}, {0: 7}, 1.0)
    assert (m.minimum, m.maximum) == (7, 7)


def test_only_the_keys_given_are_touched():
    a, b = TimedStat(), TimedStat()
    a.reset(1, 0.0)
    b.reset(1, 0.0)
    update_many({0: a, 1: b}, {0: 5}, 2.0)
    assert a.value == 5 and b.value == 1 and b._epoch == 0.0

"""Metric definitions: the notes that travel with a run's figures."""
from __future__ import annotations

from raptor.api.metric_definitions import from_summary


def _summary(mean_capacity_max):
    return {"Mean_Capacity": {"min": 10.0, "mean": 5e8, "max": mean_capacity_max},
            "Mean_Flow": {"min": 1.0, "mean": 2.0, "max": 3.0}}


def test_a_mean_capacity_holding_the_no_limit_value_is_flagged():
    notes = from_summary(_summary(999_999_999.0))["notes"]
    assert any("no limit" in note for note in notes)


def test_an_ordinary_mean_capacity_has_no_such_note():
    notes = from_summary(_summary(2050.0))["notes"]
    assert not any("no limit" in note for note in notes)


def test_the_no_limit_capacity_is_null_in_a_series():
    from raptor.api.run import _without_no_limit
    values, found = _without_no_limit([10.0, 999_999_999.0, 12.0], 1)
    assert values == [10.0, None, 12.0] and found
    mean, found = _without_no_limit([10.0, 999_999_999.0 / 4 + 5], 4)
    assert mean == [10.0, None] and found
    runs, found = _without_no_limit({"run_1": [5.0, 6.0]}, 1)
    assert runs == {"run_1": [5.0, 6.0]} and not found

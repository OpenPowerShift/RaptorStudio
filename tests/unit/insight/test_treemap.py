"""Squarified treemap: areas, aspect ratios and order."""
from __future__ import annotations

import random

import pytest

from raptor.api.insight import treemap


def test_areas_are_proportional_to_the_weights():
    items = [40.0, 30.0, 20.0, 10.0]
    rects = treemap.layout(items, 100.0, 50.0)
    total_area = sum(rect.area for rect in rects)
    assert total_area == pytest.approx(100.0 * 50.0)
    for item, rect in zip(items, rects, strict=True):
        assert rect.area == pytest.approx(100.0 * 50.0 * item / sum(items))


def test_the_result_keeps_the_callers_order():
    items = [{"name": "small", "value": 1.0},
             {"name": "large", "value": 9.0}]
    rects = treemap.layout(items, 100.0, 100.0)
    assert rects[1].area == pytest.approx(9000.0)
    assert rects[0].area == pytest.approx(1000.0)


def test_rectangles_stay_reasonably_square():
    rng = random.Random(7)
    items = [rng.uniform(0.1, 100.0) for _ in range(200)]
    rects = treemap.layout(items, 1200.0, 800.0)
    assert max(rect.aspect for rect in rects if rect.area > 0) < 6.0


def test_zero_and_empty_inputs_do_not_divide_by_zero():
    assert treemap.layout([], 100.0, 100.0) == []
    rects = treemap.layout([0.0, 0.0], 100.0, 100.0)
    assert all(rect.area == 0 for rect in rects)
    assert all(rect.area == 0 for rect in treemap.layout([1.0], 0.0, 100.0))


def test_group_sums_by_key_and_keeps_children():
    items = [{"name": "a", "group": "Pump", "value": 2.0},
             {"name": "b", "group": "Pump", "value": 3.0},
             {"name": "c", "group": "Valve", "value": 1.0}]
    groups = treemap.group(items)
    assert groups[0]["value"] == pytest.approx(5.0)
    assert [child["name"] for child in groups[0]["children"]] == ["a", "b"]

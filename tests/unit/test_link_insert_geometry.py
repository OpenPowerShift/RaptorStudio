"""Pure geometry for insert-on-link: flow inference and spacing."""
from __future__ import annotations

import pytest

from raptor.gui.link_insert import (
    FlowAxis,
    available_gap,
    choose_flow_axis,
    dominant_flow,
    pick_hover,
    plan_equal_spacing,
    plan_minimum_spacing,
    span,
)


def test_dominant_flow_uses_explicit_direction() -> None:
    assert dominant_flow([(1.0, 0.0)], configured="RL") == "RL"
    assert dominant_flow([], configured="TB") == "TB"


def test_dominant_flow_infers_from_weighted_link_vectors() -> None:
    assert dominant_flow([(10.0, 1.0), (8.0, -1.0)]) == "LR"
    assert dominant_flow([(-10.0, 1.0), (-8.0, -1.0)]) == "RL"
    assert dominant_flow([(1.0, 10.0), (-1.0, 8.0)]) == "TB"
    assert dominant_flow([(1.0, -10.0), (-1.0, -8.0)]) == "BT"


def test_dominant_flow_empty_falls_back_to_lr() -> None:
    assert dominant_flow([]) == "LR"
    assert dominant_flow([(0.0, 0.0)]) == "LR"


def test_choose_axis_uses_page_axis_and_link_sign() -> None:
    # A back-edge in an LR page still pushes its own target left.
    assert choose_flow_axis(-100.0, 0.0, "LR") == FlowAxis("x", -1)
    # A diagonal keeps the page axis but the link's sign.
    assert choose_flow_axis(-100.0, 100.0, "LR") == FlowAxis("x", -1)
    assert choose_flow_axis(100.0, 100.0, "RL") == FlowAxis("x", 1)


def test_choose_axis_follows_a_perpendicular_link() -> None:
    # A vertical hand-over in an LR diagram must push down, not sideways.
    assert choose_flow_axis(0.0, 100.0, "LR") == FlowAxis("y", 1)
    assert choose_flow_axis(0.0, -100.0, "LR") == FlowAxis("y", -1)
    assert choose_flow_axis(100.0, 0.0, "TB") == FlowAxis("x", 1)


def test_equal_spacing_centres_when_room_is_plentiful() -> None:
    flow = FlowAxis("x", 1)
    plan = plan_equal_spacing((0.0, 108.0), (408.0, 516.0), 108.0,
                              flow, gap=52.0)
    assert plan.center == pytest.approx(258.0)
    assert plan.shift == 0.0
    assert plan.gap_upstream == pytest.approx(96.0)
    assert plan.gap_downstream == pytest.approx(96.0)


def test_equal_spacing_shifts_target_when_link_is_too_short() -> None:
    flow = FlowAxis("x", 1)
    plan = plan_equal_spacing((0.0, 108.0), (160.0, 268.0), 108.0,
                              flow, gap=52.0)
    # Available = 52, required = 212, so the target moves 160.
    assert plan.shift == pytest.approx(160.0)
    assert plan.center == pytest.approx(214.0)
    assert plan.gap_upstream == pytest.approx(52.0)
    assert plan.gap_downstream == pytest.approx(52.0)


@pytest.mark.parametrize("flow,source,target,expected_center,expected_shift", [
    (FlowAxis("x", -1), (408.0, 516.0), (0.0, 108.0), 258.0, 0.0),
    (FlowAxis("x", -1), (160.0, 268.0), (0.0, 108.0), 85.0, 98.0),
    (FlowAxis("y", 1), (0.0, 46.0), (400.0, 446.0), 223.0, 0.0),
    (FlowAxis("y", -1), (400.0, 446.0), (0.0, 46.0), 223.0, 0.0),
])
def test_equal_spacing_is_mirrored_for_all_directions(
        flow, source, target, expected_center, expected_shift) -> None:
    plan = plan_equal_spacing(source, target, 46.0, flow, gap=52.0)
    assert plan.center == pytest.approx(expected_center)
    assert plan.shift == pytest.approx(expected_shift)


def test_minimum_spacing_slides_the_element_and_target() -> None:
    flow = FlowAxis("x", 1)
    # Element centred where there is no upstream gap at all.
    plan = plan_minimum_spacing((0.0, 108.0), (160.0, 268.0),
                                (108.0, 162.0), flow, gap=52.0)
    assert plan.center == pytest.approx(187.0)
    assert plan.shift == pytest.approx(106.0)
    assert plan.gap_upstream == pytest.approx(52.0)
    assert plan.gap_downstream == pytest.approx(52.0)


def test_available_gap_is_positive_downstream() -> None:
    assert available_gap((0.0, 10.0), (20.0, 30.0),
                         FlowAxis("x", 1)) == pytest.approx(10.0)
    assert available_gap((20.0, 30.0), (0.0, 10.0),
                         FlowAxis("x", -1)) == pytest.approx(10.0)


def test_pick_hover_allows_a_single_clear_link() -> None:
    pick = pick_hover([3.0, 20.0], pick_radius=10.0, ambiguity_margin=5.0)
    assert pick.index == 0
    assert pick.state == "allowed"
    assert pick.reason == ""


def test_pick_hover_blocks_an_ambiguous_overlap() -> None:
    pick = pick_hover([2.0, 4.0], pick_radius=10.0, ambiguity_margin=5.0)
    assert pick.index == 0
    assert pick.state == "blocked"
    assert "overlap" in pick.reason


def test_pick_hover_ignores_links_outside_the_radius() -> None:
    pick = pick_hover([20.0, 30.0], pick_radius=10.0, ambiguity_margin=5.0)
    assert pick.index is None
    assert pick.state == "none"


def test_span_helper() -> None:
    assert span(10.0, 5.0) == (5.0, 15.0)

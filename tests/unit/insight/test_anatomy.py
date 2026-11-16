"""The outage-anatomy layout."""
from __future__ import annotations

import pytest

from raptor.api.insight.anatomy import icicle


def seq(share, outages, *steps):
    return {"share": share, "outages": outages,
            "steps": [{"element": e, "cause": c} for e, c in steps]}


SEQUENCES = [
    seq(0.5, 10, ("A", "wait_spare"), ("A", "repair")),
    seq(0.2, 4, ("A", "wait_spare"), ("B", "repair")),
    seq(0.2, 8, ("B", "repair")),
    seq(0.1, 2, ("A", "repair")),
]


def test_columns_are_steps_and_heights_are_shares():
    cells = icicle(SEQUENCES)
    first = [c for c in cells if c.depth == 0]
    assert [(c.element, c.cause) for c in first] == [("A", "wait_spare"), ("B", "repair"),
                                                    ("A", "repair")]
    assert [c.height for c in first] == pytest.approx([0.7, 0.2, 0.1])
    assert [c.top for c in first] == pytest.approx([0.0, 0.7, 0.9])
    assert sum(c.height for c in first) == pytest.approx(1.0)
    assert first[0].outages == 14


def test_children_sit_inside_their_parent():
    cells = icicle(SEQUENCES)
    second = [c for c in cells if c.depth == 1]
    assert [(c.element, c.height) for c in second] == [("A", pytest.approx(0.5)),
                                                      ("B", pytest.approx(0.2))]
    assert second[0].top == pytest.approx(0.0) and second[1].top == pytest.approx(0.5)
    assert second[0].ends_here == pytest.approx(0.5)


def test_depth_limit_and_small_cells():
    assert max(c.depth for c in icicle(SEQUENCES, max_depth=1)) == 0
    big = icicle(SEQUENCES, min_share=0.15)
    assert ("A", "repair", 0) not in {(c.element, c.cause, c.depth) for c in big}
    assert icicle([]) == []

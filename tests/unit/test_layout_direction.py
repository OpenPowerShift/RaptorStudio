"""Layout direction and maximum-column wrapping."""
from __future__ import annotations

from raptor.core.fileio.legacy_layout import layout_container_positions


def _chain(n: int):
    members = [("node", i) for i in range(n)]
    edges = [((("node", i)), ("node", i + 1)) for i in range(n - 1)]
    return members, edges


def test_top_to_bottom_runs_down_the_page():
    members, edges = _chain(6)
    pos = layout_container_positions(members, edges, [("node", 0)],
                                     pins={("node", 5)}, direction="TB")
    ys = [pos[k][1] for k in members]
    xs = [pos[k][0] for k in members]
    assert ys[0] > ys[-1]
    assert max(xs) - min(xs) < 1e-6


def test_right_to_left_reverses_the_flow():
    members, edges = _chain(5)
    lr = layout_container_positions(members, edges, [("node", 0)],
                                    pins={("node", 4)}, direction="LR")
    rl = layout_container_positions(members, edges, [("node", 0)],
                                    pins={("node", 4)}, direction="RL")
    assert lr[("node", 0)][0] < lr[("node", 4)][0]
    assert rl[("node", 0)][0] > rl[("node", 4)][0]


def test_max_columns_wraps_into_another_band():
    members, edges = _chain(12)
    pos = layout_container_positions(members, edges, [("node", 0)],
                                     pins={("node", 11)},
                                     max_columns=3)
    ys = {round(pos[k][1], 6) for k in members}
    assert len(ys) > 1
    # The first three layers are one band; the fourth starts a new one.
    assert pos[("node", 0)][1] != pos[("node", 4)][1]


def test_unknown_direction_is_refused():
    import pytest
    with pytest.raises(ValueError):
        layout_container_positions([("node", 0)], [], [("node", 0)],
                                   direction="sideways")

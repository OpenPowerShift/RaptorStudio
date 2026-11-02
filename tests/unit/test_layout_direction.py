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


# -- wrapping and orientation regressions --------------------------------
# A top-to-bottom layout used to be produced by laying the model out
# left-to-right and then transposing the finished coordinates. With
# `max_columns` the band resets were in the transposed flow axis, so every
# band's layers collapsed onto the same few y values: nodes 1 and 2 in a
# five-node chain shared a coordinate. These pin the fix.

from raptor.core.fileio.legacy_layout import (  # noqa: E402
    FIRST_X, X_MAX, Y_MAX, Y_MIN,
)


def _on_canvas(pos, direction):
    if direction in ("LR", "RL"):
        xlo, xhi, ylo, yhi = FIRST_X, X_MAX, Y_MIN, Y_MAX
    else:
        xlo, xhi = FIRST_X, X_MAX
        ylo, yhi = Y_MIN + FIRST_X, Y_MAX - FIRST_X
    return all(xlo - 1e-9 <= x <= xhi + 1e-9
               and ylo - 1e-9 <= y <= yhi + 1e-9
               for x, y in pos.values())


def test_vertical_wrapping_keeps_every_element_distinct_and_on_canvas():
    members, edges = _chain(10)
    for direction in ("TB", "BT"):
        pos = layout_container_positions(
            members, edges, [("node", 0)], pins={("node", 9)},
            direction=direction, max_columns=3)
        assert len(set(pos.values())) == len(members), (
            f"{direction} wrapping produced duplicate coordinates")
        assert _on_canvas(pos, direction), (
            f"{direction} wrapping left the canvas: {pos}")


def test_vertical_flow_is_monotonic_within_each_band():
    members, edges = _chain(10)
    pos = layout_container_positions(members, edges, [("node", 0)],
                                     pins={("node", 9)}, direction="TB",
                                     max_columns=4)
    # With an explicit column limit every band keeps the requested flow
    # direction, so layers 0..3 and 4..7 both run down the page; only the
    # cross position changes at the band hand-over.
    band_one = [pos[("node", i)][1] for i in range(4)]
    band_two = [pos[("node", i)][1] for i in range(4, 8)]
    assert band_one == sorted(band_one, reverse=True)
    assert band_two == sorted(band_two, reverse=True)
    assert pos[("node", 3)][0] != pos[("node", 4)][0]
    # The hand-over returns to the top of the next band, one row away.
    assert abs(pos[("node", 3)][1] - pos[("node", 4)][1]) <= 5.0


def test_a_narrow_node_chain_still_wraps_without_duplicates():
    members, edges = _chain(200)
    pos = layout_container_positions(members, edges, [("node", 0)],
                                     pins={("node", 199)}, max_columns=7)
    assert len(set(pos.values())) == len(members)
    assert _on_canvas(pos, "LR")


def test_many_wrapped_single_element_bands_fit_the_canvas():
    # 30 one-column bands used to run off the bottom of the canvas; the
    # band gap tightens to the one-grid minimum when it has to.
    members, edges = _chain(30)
    pos = layout_container_positions(members, edges, [("node", 0)],
                                     pins={("node", 29)}, max_columns=1)
    assert _on_canvas(pos, "LR")
    assert len(set(pos.values())) == len(members)


def test_one_element_in_every_direction_is_on_canvas():
    for direction in ("LR", "RL", "TB", "BT"):
        pos = layout_container_positions([("node", 0)], [],
                                         [("node", 0)], direction=direction)
        assert len(pos) == 1
        assert _on_canvas(pos, direction)


def test_cyclic_flow_terminates_and_cannot_create_duplicates():
    members = [("block", i) for i in range(6)]
    edges = [(members[i], members[(i + 1) % 6]) for i in range(6)]
    pos = layout_container_positions(members, edges, [members[0]],
                                     direction="TB", max_columns=2)
    assert len(pos) == len(members)
    assert len(set(pos.values())) == len(members)


def test_max_columns_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        layout_container_positions([("node", 0)], [], [("node", 0)],
                                   max_columns=0)

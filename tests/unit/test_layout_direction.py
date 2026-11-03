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
    # A short vertical band keeps the requested flow direction; the
    # horizontal case is the one that snakes into a Z/S (see the LR test).
    band_one = [pos[("node", i)][1] for i in range(5)]
    band_two = [pos[("node", i)][1] for i in range(5, 10)]
    assert band_one == sorted(band_one, reverse=True)
    assert band_two == sorted(band_two, reverse=True)
    assert pos[("node", 4)][0] != pos[("node", 5)][0]
    # The hand-over returns to the top of the next band, one row away.
    assert abs(pos[("node", 4)][1] - pos[("node", 5)][1]) <= 5.0


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


def test_max_columns_counts_series_elements_not_parallel_branches():
    """Parallel branches are one series position, not two counted columns."""
    members = [("node", 1), ("block", 2), ("block", 3), ("block", 4),
               ("block", 5), ("node", 6)]
    edges = [(("node", 1), ("block", 2)),
             (("block", 2), ("block", 3)),
             (("block", 2), ("block", 4)),
             (("block", 3), ("block", 5)),
             (("block", 4), ("block", 5)),
             (("block", 5), ("node", 6))]
    pos = layout_container_positions(members, edges, [("node", 1)],
                                     pins={("node", 6)}, direction="LR",
                                     max_columns=2)
    # B and C are the same series position and share a column.
    assert pos[("block", 3)][0] == pos[("block", 4)][0]
    # A and the B/C pair fill the two counted positions; D wraps to the
    # next band's leading edge.
    assert pos[("block", 5)][1] != pos[("block", 3)][1]
    assert pos[("block", 5)][0] < pos[("block", 2)][0]


def test_spacing_factor_scales_gaps_without_reordering():
    members, edges = _chain(6)
    tight = layout_container_positions(members, edges, [("node", 0)],
                                       pins={("node", 5)}, direction="LR",
                                       spacing=0.5)
    nominal = layout_container_positions(members, edges, [("node", 0)],
                                         pins={("node", 5)}, direction="LR",
                                         spacing=1.0)
    open_ = layout_container_positions(members, edges, [("node", 0)],
                                       pins={("node", 5)}, direction="LR",
                                       spacing=2.0)
    tight_span = tight[("node", 5)][0] - tight[("node", 0)][0]
    nominal_span = nominal[("node", 5)][0] - nominal[("node", 0)][0]
    open_span = open_[("node", 5)][0] - open_[("node", 0)][0]
    assert tight_span <= nominal_span < open_span


def test_spacing_must_be_positive():
    import pytest
    with pytest.raises(ValueError, match="spacing"):
        layout_container_positions([("node", 0)], [], [("node", 0)],
                                   spacing=0)


def test_max_columns_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        layout_container_positions([("node", 0)], [], [("node", 0)],
                                   max_columns=0)


def test_top_to_bottom_layout_routes_ports_vertically():
    """A TB layout must use top/bottom ports, not the LR left/right ports."""
    from PySide6.QtWidgets import QApplication
    from raptor.api.build import build_from_spec
    from raptor.gui.ui.diagram import DiagramView
    from raptor.gui.ui.diagram_items import (
        BLOCK_H, BLOCK_W, flow_direction, set_flow_direction)
    QApplication.instance() or QApplication([])
    rbd = build_from_spec({
        "name": "TB", "blocks": [{"name": "A"}, {"name": "B"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "B"},
                  {"from": "B", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    view = DiagramView()
    view.load_rbd(rbd)
    try:
        view.auto_layout(direction="TB")
        assert flow_direction() == "TB"
        blocks = set(id(it) for it in view._block_items.values())
        for link in view._links:
            if id(link._from) not in blocks or id(link._to) not in blocks:
                continue
            start = link._from.mapFromScene(link._p0)
            assert abs(start.x() - BLOCK_W / 2) < 1.0, start
            assert start.y() > BLOCK_H * 0.5, start
            end = link._to.mapFromScene(link._p3)
            assert abs(end.x() - BLOCK_W / 2) < 1.0, end
            assert abs(end.y()) < 1.0, end
    finally:
        set_flow_direction("auto")


def test_left_to_right_layout_routes_ports_horizontally():
    """An LR layout must enter left and leave right, not top/bottom."""
    from PySide6.QtWidgets import QApplication
    from raptor.api.build import build_from_spec
    from raptor.gui.ui.diagram import DiagramView
    from raptor.gui.ui.diagram_items import (
        BLOCK_H, BLOCK_W, set_flow_direction)
    QApplication.instance() or QApplication([])
    rbd = build_from_spec({
        "name": "LR", "blocks": [{"name": "A"}, {"name": "B"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "B"},
                  {"from": "B", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    view = DiagramView()
    view.load_rbd(rbd)
    try:
        view.auto_layout(direction="LR")
        blocks = {id(it) for it in view._block_items.values()}
        for link in view._links:
            if id(link._from) not in blocks or id(link._to) not in blocks:
                continue
            start = link._from.mapFromScene(link._p0)
            assert abs(start.y() - BLOCK_H / 2) < 1.0, start
            assert abs(start.x() - BLOCK_W) < 1.0, start
            end = link._to.mapFromScene(link._p3)
            assert abs(end.y() - BLOCK_H / 2) < 1.0, end
            assert abs(end.x()) < 1.0, end
    finally:
        set_flow_direction("auto")


def _chain_view(name, n, direction, max_columns):
    from itertools import pairwise
    from PySide6.QtWidgets import QApplication
    from raptor.api.build import build_from_spec
    from raptor.gui.ui.diagram import DiagramView
    QApplication.instance() or QApplication([])
    seq = ["Start"] + [f"B{i}" for i in range(n)] + ["End"]
    rbd = build_from_spec({
        "name": name,
        "blocks": [{"name": f"B{i}"} for i in range(n)],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": a, "to": b} for a, b in pairwise(seq)],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    view = DiagramView()
    view.load_rbd(rbd)
    view.auto_layout(direction=direction, max_columns=max_columns)
    return view


def _block_links(view):
    blocks = {id(it) for it in view._block_items.values()}
    return [link for link in view._links
            if id(link._from) in blocks and id(link._to) in blocks]


def _reset_flow():
    from raptor.gui.ui.diagram_items import (
        set_explicit_bands, set_flow_direction)
    set_flow_direction("auto")
    set_explicit_bands(False)


def test_lr_band_handover_is_one_straight_line_bottom_to_top():
    """A wrapped LR band returns to the next band as a single straight
    line, leaving the bottom of the last block and entering the top of the
    first, so it never crosses a block."""
    from raptor.gui.ui.diagram_items import BLOCK_H, BLOCK_W
    view = _chain_view("LRBand", 8, "LR", 4)
    try:
        links = _block_links(view)
        handovers = [lk for lk in links if lk._p3.x() < lk._p0.x() - 1.0]
        assert len(handovers) == 1, "one hand-over between two bands"
        for link in links:
            assert link.path().elementCount() == 2, "straight line"
        link = handovers[0]
        start = link._from.mapFromScene(link._p0)
        end = link._to.mapFromScene(link._p3)
        assert abs(start.y() - BLOCK_H) < 1.0
        assert abs(start.x() - BLOCK_W / 2) < 1.0
        assert abs(end.y()) < 1.0 and abs(end.x() - BLOCK_W / 2) < 1.0
        for item in view._block_items.values():
            if item in (link._from, link._to):
                continue
            assert not link.collidesWithItem(item), item
    finally:
        _reset_flow()


def test_vertical_band_handover_is_one_straight_line_beside_the_bands():
    """A wrapped TB band leaves by the right side and enters the next
    band's first block by its left side: one straight line in the gap."""
    from raptor.gui.ui.diagram_items import BLOCK_H, BLOCK_W
    view = _chain_view("TBBand", 6, "TB", 3)
    try:
        links = _block_links(view)
        handovers = [lk for lk in links if lk._p3.y() < lk._p0.y() - 1.0]
        assert handovers, "wrapped bands should draw a hand-over"
        for link in links:
            assert link.path().elementCount() == 2
        for link in handovers:
            start = link._from.mapFromScene(link._p0)
            end = link._to.mapFromScene(link._p3)
            assert abs(start.x() - BLOCK_W) < 1.0
            assert abs(start.y() - BLOCK_H / 2) < 1.0
            assert abs(end.x()) < 1.0 and abs(end.y() - BLOCK_H / 2) < 1.0
            for item in view._block_items.values():
                if item in (link._from, link._to):
                    continue
                assert not link.collidesWithItem(item), item
    finally:
        _reset_flow()


def test_hand_over_ports_apply_only_to_an_explicit_band_wrap():
    """The automatic wrap snakes (alternating bands), so a link that runs
    against the flow there is an ordinary link. Only an explicit
    `max_columns` wrap, where every band keeps the flow direction, uses the
    straight hand-over ports."""
    from PySide6.QtCore import QPointF
    from raptor.gui.ui.diagram_items import (
        _LinkItem, set_explicit_bands, set_flow_direction)
    behind_lr = (QPointF(500.0, 0.0), QPointF(0.0, 120.0))
    try:
        set_flow_direction("LR")
        set_explicit_bands(False)
        assert _LinkItem._handover_sides(*behind_lr) is None
        set_explicit_bands(True)
        assert _LinkItem._handover_sides(*behind_lr) == ("bottom", "top")
        # forward links are never hand-overs
        assert _LinkItem._handover_sides(
            QPointF(0.0, 0.0), QPointF(80.0, 0.0)) is None
        set_flow_direction("TB")
        assert _LinkItem._handover_sides(
            QPointF(0.0, 500.0), QPointF(150.0, 0.0)) == ("right", "left")
        set_flow_direction("auto")
        assert _LinkItem._handover_sides(*behind_lr) is None
    finally:
        _reset_flow()


def test_flow_pitch_is_a_whole_number_of_grid_steps():
    """A pitch that is not a multiple of the grid made the gaps alternate
    (3.75, 3.75, 5.0 ...) once positions were snapped."""
    from raptor.api.build import build_from_spec
    from itertools import pairwise
    from raptor.core.fileio.legacy_layout import GRID, normalize_legacy_layout
    blocks = []
    nodes = [{"name": "Start", "type": 1, "n": 0, "out": 1},
             {"name": "End", "type": 3, "n": 1, "out": 0}]
    links = [{"from": "Start", "to": "S1"}]
    prev = "Start"
    for s in range(1, 7):
        nodes += [{"name": f"S{s}", "type": 2, "n": 1, "out": 3, "k": 1},
                  {"name": f"J{s}", "type": 2, "n": 3, "out": 1, "k": 2}]
        if s > 1:
            links.append({"from": prev, "to": f"S{s}"})
        for i in range(3):
            blocks.append({"name": f"S{s}_B{i}"})
            links += [{"from": f"S{s}", "to": f"S{s}_B{i}"},
                      {"from": f"S{s}_B{i}", "to": f"J{s}"}]
        prev = f"J{s}"
    links.append({"from": prev, "to": "End"})
    rbd = build_from_spec({"blocks": blocks, "nodes": nodes, "links": links},
                          auto_layout=False)
    normalize_legacy_layout(rbd, force=True, direction="TB")
    rows = {r.get("Name") or r.get("blockName"): r
            for r in rbd.section("BLOCK_DATA").rows
            if str(r.get("ID")) != "0"}
    ys = [float(rows[f"S{s}_B1"]["yPos"]) for s in range(1, 7)]
    gaps = {round(abs(b - a), 4) for a, b in pairwise(ys)}
    assert len(gaps) == 1, f"uneven stage pitch: {sorted(gaps)}"
    assert all(abs(g / GRID - round(g / GRID)) < 1e-6 for g in gaps)

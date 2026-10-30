"""Auto layout keeps every level inside Raptor 7's canvas.

Raptor's canvas is 120 x 80 display units, 150 x 100 in the file's stored
units (IDisplay.mod GridObj; IFileFx.mod stores display x 1.25). OpenFile
does not clamp, so an element laid out beyond it opens off the grid. A
60-block series used to reach x = 153.75 and a 12-branch parallel y =
106.25.
"""
from __future__ import annotations

import itertools

import pytest

from raptor.core.fileio.legacy_layout import (
    X_MAX, Y_MAX, Y_MIN, FIRST_X, ROW_CENTRE, _level_zoom,
    layout_container_positions)


def _series(n):
    ks = [("node", "in")] + [("block", i) for i in range(n)] + [("node", "out")]
    return ks, list(itertools.pairwise(ks)), [ks[0]]


def _parallel(n):
    ks = [("node", "in"), ("node", "out")] + [("block", i) for i in range(n)]
    edges = ([(("node", "in"), ("block", i)) for i in range(n)]
             + [(("block", i), ("node", "out")) for i in range(n)])
    return ks, edges, [ks[0]]


def _inside(pos):
    return all(FIRST_X <= x <= X_MAX and Y_MIN <= y <= Y_MAX
               for x, y in pos.values())


@pytest.mark.parametrize("shape,n", [
    (_series, 20), (_series, 60), (_series, 100), (_series, 300),
    (_parallel, 7), (_parallel, 12), (_parallel, 24), (_parallel, 48),
])
def test_layout_stays_inside_raptors_canvas(shape, n):
    ks, edges, entries = shape(n)
    report: dict = {}
    pos = layout_container_positions(ks, edges, entries, report=report)
    assert _inside(pos), pos
    assert len(set(pos.values())) == len(pos), "two elements stacked"
    assert "outside_world" not in report


def test_a_small_parallel_lays_out_as_before():
    # Up to 6 rows fit around the usual centre line, which does not move.
    pos = layout_container_positions(*_parallel(6))
    assert pos[("node", "in")][1] == ROW_CENTRE
    assert sorted({y for _x, y in pos.values()}) == [
        86.25, 88.75, 91.25, 92.5, 93.75, 96.25, 98.75]


def test_a_wide_layer_gets_its_own_chunk_columns():
    # 24 branches wrap into two 12-row columns; the out node takes the
    # next column instead of sharing the second chunk's.
    pos = layout_container_positions(*_parallel(24))
    blocks_x = {pos[("block", i)][0] for i in range(24)}
    assert len(blocks_x) == 2
    assert pos[("node", "out")][0] > max(blocks_x)


def test_a_long_series_wraps_back_and_forth():
    pos = layout_container_positions(*_series(100))
    # The first band runs left to right, the second right to left, so the
    # link between them is short.
    last_of_band0 = max((p for p in pos.values() if p[1] == ROW_CENTRE),
                        key=lambda p: p[0])
    band1 = [p for p in pos.values() if p[1] < ROW_CENTRE]
    first_of_band1 = max(band1, key=lambda p: p[0])
    assert abs(first_of_band1[0] - last_of_band0[0]) <= 2.5


def test_a_level_too_big_for_the_canvas_is_reported():
    report: dict = {}
    layout_container_positions(*_series(3000), report=report)
    assert report.get("outside_world") == 1


def test_the_saved_zoom_shows_every_row():
    # The view is pinned at the canvas top-left (display 0/80) and shows
    # 12.85/20 of the zoom vertically, as Raptor's SetView does.
    pos = layout_container_positions(*_parallel(12))
    zoom = _level_zoom(list(pos.values()))
    lowest_display_y = min(y for _x, y in pos.values()) / 1.25
    assert 80.0 - zoom * 12.85 / 20.0 <= lowest_display_y


def test_a_hierarchy_level_stays_inside_the_canvas(tmp_path):
    from raptor.api.build import build_from_spec, collapse_selection
    from raptor.api.workspace import Workspace
    from raptor.core.fileio.legacy_layout import normalize_legacy_layout
    from raptor.core.fileio.view_transform import collect_levels

    n = 60
    spec = {
        "blocks": [{"name": f"B{i}", "fail_dist": 4, "fail_params": [1000, 0],
                    "repair_dist": 7, "repair_params": [8, 2]}
                   for i in range(n)],
        "nodes": [{"name": "Start", "type": "start"},
                  {"name": "End", "type": "end"}],
        "links": ([{"from": "Start", "to": "B0"}]
                  + [{"from": f"B{i}", "to": f"B{i + 1}"} for i in range(n - 1)]
                  + [{"from": f"B{n - 1}", "to": "End"}]),
        "system": {"term_condition": 8760, "num_runs": 1},
    }
    ws = Workspace(tmp_path)
    mid = ws.register_model(build_from_spec(spec), name="h")["model_id"]
    collapse_selection(ws, mid, [f"B{i}" for i in range(n)], "H")
    rbd = ws.get(mid).rbd
    normalize_legacy_layout(rbd, force=True)
    levels = collect_levels(rbd)
    assert len(levels) == 2
    for xs, ys in levels.values():
        assert all(FIRST_X <= x <= X_MAX for x in xs)
        assert all(Y_MIN <= y <= Y_MAX for y in ys)

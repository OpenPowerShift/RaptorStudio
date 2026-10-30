"""Scale guard for the shared legacy-grid layout.

`layout_container_positions` runs on every `build_model` (auto-layout is
the default) and on Auto Layout / legacy save. Its greedy collision pass
used to score every candidate by scanning every element and every
edge-edge pair, so a 210-element fixture took minutes and a 500-block
series was unusable. The scoring is now spatially filtered and the pass
is skipped when the layout is already clean or cannot fit.

The bound is deliberately loose: this is a guard against the old
cubic blow-up, not a benchmark.
"""
from __future__ import annotations

import itertools
import time


def _series_spec(n: int) -> dict:
    return {
        "blocks": [
            {"name": f"B{i}", "fail_dist": 4, "fail_params": [1000, 0],
             "repair_dist": 7, "repair_params": [8, 2]}
            for i in range(n)
        ],
        "nodes": [{"name": "Start", "type": "start"},
                  {"name": "End", "type": "end"}],
        "links": ([{"from": "Start", "to": "B0"}]
                  + [{"from": f"B{i}", "to": f"B{i + 1}"}
                     for i in range(n - 1)]
                  + [{"from": f"B{n - 1}", "to": "End"}]),
        "system": {"term_condition": 8760, "num_runs": 1},
    }


def test_a_500_block_series_lays_out_in_seconds():
    from raptor.api.build import build_from_spec

    t0 = time.monotonic()
    rbd = build_from_spec(_series_spec(500), auto_layout=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 20.0, f"auto-layout of a 500-block series took {elapsed:.1f}s"

    rows = [r for r in rbd.sections["BLOCK_DATA"].rows if r.get("ID") != "0"]
    xs = [float(r["xPos"]) for r in rows]
    ys = [float(r["yPos"]) for r in rows]
    assert len(rows) == 500
    # Inside Raptor's 150 x 100 canvas: the series wraps into bands
    # rather than running 1,250 units to the right.
    assert all(1.25 <= x <= 148.75 for x in xs)
    assert all(1.25 <= y <= 98.75 for y in ys)
    assert len(set(ys)) > 1
    # Layering preserves the series order: each block sits next to the
    # one before it, along a band or down to the next band.
    order = sorted(rows, key=lambda r: int(r["ID"]))
    pts = [(float(r["xPos"]), float(r["yPos"])) for r in order]
    for (x1, y1), (x2, y2) in itertools.pairwise(pts):
        assert (abs(x2 - x1) <= 2.5 and y1 == y2) or \
            (abs(x2 - x1) <= 2.5 and 0 < y1 - y2 <= 5.0), ((x1, y1), (x2, y2))

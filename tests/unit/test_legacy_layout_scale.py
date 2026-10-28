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
    assert all(x > 0 for x in xs)
    assert all(81.0 < y < 99.0 for y in ys)
    # Layering preserves the series order left to right.
    assert xs == sorted(xs)

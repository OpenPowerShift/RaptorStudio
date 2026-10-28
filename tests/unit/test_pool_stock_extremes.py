"""Pool stock statistics at the run-end instant (verification ladder L2, 2026-10-01)."""
from __future__ import annotations


def test_pool_max_stock_counts_the_level_reached_at_the_run_end_instant() -> None:
    """`LadL2Pool` (real 2026-10-01): a restock of 2 landing at exactly the run length (stock 10 -> 12) gives
    MaxStock 12. A level set and replaced within one instant mid-run is still never recorded."""
    from raptor.core.engine.pool import ResourceQueue

    clock = {"t": 0.0}
    q = ResourceQueue(resources=10, time_fn=lambda: clock["t"])
    q.begin_run(10)
    clock["t"] = 50.0
    q.release_kinds(2)                       # 10 -> 12 at t=50, the run end
    assert q.stock_extremes(50.0) == (10, 12)

    clock["t"] = 0.0
    q2 = ResourceQueue(resources=10, time_fn=lambda: clock["t"])
    q2.begin_run(10)
    clock["t"] = 20.0
    q2.release_kinds(5)                      # 10 -> 15 ...
    q2.request(1, 5)                         # ... and straight back to 10 in the same instant
    clock["t"] = 50.0
    assert q2.stock_extremes(50.0)[1] == 10  # the 15 was held for zero time and never counts

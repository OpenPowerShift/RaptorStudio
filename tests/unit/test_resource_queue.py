"""`ResourceQueue`'s own contract: FIFO grants, kinds, waiting counts, withdrawal, release timing, stock statistics.

These are direct tests of the queue (the engine fixtures reach it only through whole simulations), so a change to its
branches is caught here, not just by a golden byte difference.
"""
from __future__ import annotations

from raptor.core.engine.pool import ResourceQueue


def _queue(resources: int = 2, *, resource_pool: bool = False) -> tuple[ResourceQueue, list[float]]:
    clock = [0.0]
    q = ResourceQueue(resources, time_fn=lambda: clock[0], is_resource_pool=resource_pool)
    q.begin_run(resources, 0.0)
    return q, clock


def test_request_grants_immediately_when_stock_covers_it():
    q, _ = _queue(2)
    assert q.request(1, 1) is True
    assert q.resources == 1
    assert not q.is_waiting(1)


def test_request_queues_in_fifo_order_even_when_stock_could_cover_a_later_one():
    q, _ = _queue(1)
    assert q.request(1, 2) is False          # needs 2, has 1: queued
    assert q.request(2, 1) is False          # stock covers it, but the queue is not empty: FIFO
    assert q.is_waiting(1) and q.is_waiting(2)
    assert q.count_waiting(1) == 1 and q.count_waiting(3) == 0


def test_release_grants_head_of_line_and_stops_at_the_first_unsatisfiable_waiter():
    q, _ = _queue(0)
    q.request(1, 2)
    q.request(2, 1)
    assert q.release(1) == []                # head needs 2: nobody is served, the later waiter does not jump it
    assert q.release(1) == [1]               # now the head is served (2 units) and the next waits
    assert q.is_waiting(2) and not q.is_waiting(1)
    assert q.release(1) == [2]


def test_release_kinds_carries_the_request_kind_through():
    q, _ = _queue(0)
    q.request(7, 1, kind="pm")
    q.request(8, 1, kind="repair")
    assert q.release_kinds(2) == [(7, "pm"), (8, "repair")]


def test_resource_pool_pm_wait_counts_twice_other_waits_once():
    pool, _ = _queue(0, resource_pool=True)
    pool.request(1, 1, kind="pm")
    pool.request(2, 1, kind="repair")
    assert pool.waits == 3                   # source double-counts a PM-driven resource wait
    spare, _ = _queue(0)
    spare.request(1, 1, kind="pm")
    assert spare.waits == 1                  # a spare pool does not


def test_withdraw_removes_the_earliest_matching_entry_only():
    q, _ = _queue(0)
    q.request(1, 1, kind="pm")
    q.request(1, 1, kind="pm")
    q.request(1, 1, kind="repair")
    assert q.withdraw(1, "pm") is True
    assert q.count_waiting(1) == 2
    assert q.withdraw(1, "pm") is True
    assert q.withdraw(1, "pm") is False      # none left of that kind
    assert q.withdraw(9, "repair") is False  # no such block
    assert q.is_waiting(1)                   # the repair request stays


def test_release_due_at_is_non_destructive_and_prunes_stale_entries():
    q, _ = _queue(1)
    q.note_release_at(5.0)
    q.note_release_at(9.0)
    assert q.release_due_at(5.0) and q.release_due_at(5.0)   # several waiters see the same release
    assert not q.release_due_at(6.0)
    assert q._release_times == [9.0]         # 5.0 is in the past now: pruned
    assert not q.release_due_at(7.0)


def test_stock_statistics_are_time_weighted_and_include_the_run_end_level():
    q, clock = _queue(4)
    clock[0] = 10.0
    q.request(1, 3)                          # 4 held 0..10, then 1
    clock[0] = 30.0
    q.release(2)                             # 1 held 10..30, then 3
    assert q.mean_stock(40.0) == (4 * 10 + 1 * 20 + 3 * 10) / 40.0
    assert q.stock_extremes(40.0) == (1, 4)
    assert (q.observed_min_stock, q.observed_max_stock) == (1, 4)


def test_a_level_replaced_within_an_instant_is_not_recorded_but_the_end_level_is():
    q, _ = _queue(5)
    q.request(1, 3)                          # t=0: 5 -> 2 at the same instant, 5 never held
    assert q.stock_extremes(10.0) == (2, 2)
    assert (q.observed_min_stock, q.observed_max_stock) == (2, 2)


def test_statistics_without_a_clock_observe_every_mutation():
    q = ResourceQueue(3)
    q.begin_run(3)
    q.request(1, 2)
    q.release(2)
    assert (q.observed_min_stock, q.observed_max_stock) == (1, 3)
    assert q.mean_stock(0.0) == 0.0


def test_reset_statistics_keeps_the_physical_allocation():
    q, clock = _queue(2)
    q.request(1, 1)
    q.request(2, 5)                          # queued
    clock[0] = 7.0
    q.reset_statistics(7.0)
    assert q.resources == 1 and q.is_waiting(2)
    assert q.arrivals == 0 and q.waits == 0

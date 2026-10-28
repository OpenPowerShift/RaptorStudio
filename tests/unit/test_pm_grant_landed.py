"""`_pm_grant_landed`: one owner for the "has an async PM grant landed?" rule.

The rule existed twice in `block_lifecycle.py`. `_claim_pm_grant` had it
correctly -- including a `custom_q` term that reconciles a Custom block's
deferred PM spare against its own private queue. `_idle_wait` had a
hand-inlined copy that read the resource and shared-spare queues but
**omitted `custom_spares` entirely**.

The consequence: for a Custom-sparing block, a deferred PM spare request
granted out of the private queue read as "nothing landed". `_idle_wait`
gates its reconciliation on `pm_grant_landed`, so the grant was never
claimed -- and the inline copy's own comment explains at length that a
ground-truth queue read is needed precisely for the same-instant
`Dependence_All_Up` case that stales the `Pool_Available` wake. The weaker
copy was in the code path that most needed the stronger one.

These tests pin the rule at both kinds of queue, and pin that the pure
predicate does not mutate -- which is the whole reason it is now separate.
"""
from __future__ import annotations

from raptor.core.engine.block_env import BlockEnv, BlockPools
from raptor.core.engine.block_lifecycle import BlockProcess
from raptor.core.engine.blocks import BlockStatistics
from raptor.core.engine.pool import ResourceQueue
from raptor.core.model import RBDBlock, SparePool, SparingType
from raptor.core.rng import RandomGen, RaptorStream, fetch_raptor_seed
from raptor.core.scheduler import Scheduler

FIXED = 19


#: The rule counts *this block's own* requests, and `RBDBlock`'s default
#: id is not 1 -- so every queue in these tests keys on this.
BLOCK_ID = 1


def _block(**fields) -> RBDBlock:
    base = dict(
        id=BLOCK_ID,
        name="Pump", fail_distro=FIXED, fail_values=[100.0], repair_distro=FIXED,
        repair_values=[3.0], sparing_type=SparingType.INFINITE, uses_pm=True,
        pm_freq=30.0, pm_dist=FIXED, pm_params=[2.0, 0.0, 0.0], pm_refresh=True,
        pm_mis_defer=True, spare_cost=50.0,
    )
    base.update(fields)
    return RBDBlock(**base)


def _queue(stock: int) -> ResourceQueue:
    queue = ResourceQueue(resources=stock, time_fn=lambda: 0.0)
    queue.begin_run(stock, 0.0)
    return queue


def _queued(queue: ResourceQueue, block_id: int, amount: int = 1,
            kind: str = "pm") -> ResourceQueue:
    """Force a request onto the FIFO rather than being granted immediately.

    `request()` grants straight away when stock is available, so a queue
    that starts empty is the only way to get a genuinely *outstanding*
    request. Then `release()` is what makes it *landed* -- which is the
    transition `count_waiting()` is being asked about.
    """
    assert queue.request(block_id, amount, kind=kind) is False, \
        "request was granted immediately; start the queue at 0 stock"
    return queue


def _released(queue: ResourceQueue, block_id: int, amount: int = 1) -> None:
    """Grant the queued request: `resources` is restored and the entry
    leaves the FIFO, so `count_waiting` drops while the grant stands."""
    granted = queue.release(amount)
    assert block_id in granted, \
        f"release did not reach block {block_id} (got {granted})"


def _process(block: RBDBlock, *, resource_pool=None, spare_pool=None,
             custom_spares=None) -> BlockProcess:
    scheduler = Scheduler()
    env = BlockEnv(scheduler, 200.0, [], cost_analysis=False, events_file=True)
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))
    pools = BlockPools(
        resource_pool=resource_pool,
        spare_pool=spare_pool,
        spare_pool_config=SparePool(pool_name="P1", sparing_type=SparingType.SPARE_POOL)
        if spare_pool is not None else None,
    )
    proc = BlockProcess(block, BlockStatistics(), rng_fail, rng_repair, env, pools=pools)
    proc.clock.race_raw_life = None
    # `custom_spares` is set per-process (it is the block's own private
    # queue, built by `_start_custom_spares`), not through BlockPools.
    proc.custom_spares = custom_spares
    return proc


class TestNothingPending:
    def test_no_queues_and_no_pending_reports_nothing(self):
        proc = _process(_block())
        assert proc._pm_grant_landed() is None

    def test_a_pending_count_equal_to_the_queue_does_not_count_as_landed(self):
        """1 request outstanding, 1 still waiting -> nothing has landed yet.

        This is why the rule counts rather than testing presence: the
        binary `is_waiting()` check cannot tell "still queued" from
        "granted", and more than one request can be outstanding.
        """
        pool = _queued(_queue(0), BLOCK_ID)
        proc = _process(_block(), resource_pool=pool)
        proc.pm.resource_pending = 1
        assert proc._pm_grant_landed() is None


class TestResourceGrants:
    def test_a_grant_out_of_the_resource_queue_is_seen(self):
        pool = _queued(_queue(0), BLOCK_ID)
        proc = _process(_block(), resource_pool=pool)
        proc.pm.resource_pending = 1
        _released(pool, BLOCK_ID)
        assert proc._pm_grant_landed() == "resource"

    def test_resource_wins_over_spare_when_both_landed(self):
        """Ordering matters: the source claims resource first, and so must
        the shared predicate, or the two callers would claim different ones."""
        rpool = _queued(_queue(0), BLOCK_ID)
        spool = _queued(_queue(0), BLOCK_ID)
        proc = _process(_block(), resource_pool=rpool, spare_pool=spool)
        proc.pm.resource_pending = 1
        proc.pm.spare_pending = 1
        _released(rpool, BLOCK_ID)
        _released(spool, BLOCK_ID)
        assert proc._pm_grant_landed() == "resource"


class TestSpareGrants:
    def test_a_grant_out_of_the_shared_spare_queue_is_seen(self):
        pool = _queued(_queue(0), BLOCK_ID)
        proc = _process(_block(sparing_type=SparingType.SPARE_POOL), spare_pool=pool)
        proc.pm.spare_pending = 1
        _released(pool, BLOCK_ID)
        assert proc._pm_grant_landed() == "spare"

    def test_a_grant_out_of_the_custom_queue_is_seen(self):
        """THE REGRESSION. `_idle_wait`'s inlined copy had no `custom_q`
        term, so this returned False while `_claim_pm_grant` returned
        "spare" -- two answers to one question."""
        custom = _queued(_queue(0), BLOCK_ID)
        proc = _process(_block(sparing_type=SparingType.CUSTOM),
                        custom_spares=custom)
        proc.pm.spare_pending = 1
        _released(custom, BLOCK_ID)
        assert proc._pm_grant_landed() == "spare"

    def test_a_custom_request_still_queued_is_not_a_landed_grant(self):
        """THE REGRESSION, and it errs in the OPPOSITE direction to the one
        the review predicted.

        With a private queue and no shared pool, the old inlined copy read
        `spare_pending > (spare_pool.count_waiting(...) if spare_pool else
        0)` -- i.e. against ZERO, since a Custom block has no `spare_pool`.
        So it reported a landed grant for *any* non-zero `spare_pending`,
        including one that was still sitting in the private queue.

        The review claimed the omission made the predicate read False and
        skip reconciliation. It reads True too eagerly instead. Same root
        cause, opposite symptom: the predicate is a gate on `_idle_wait`'s
        reconciliation branch, so too-eager entry runs the branch early
        rather than skipping it. Worth stating precisely, because "we
        reconciled too late" and "we reconciled too early" are different
        bugs and only one of them is this.
        """
        custom = _queued(_queue(0), BLOCK_ID)      # requested, NOT granted
        proc = _process(_block(sparing_type=SparingType.CUSTOM),
                        custom_spares=custom)
        proc.pm.spare_pending = 1                 # one request outstanding

        assert custom.count_waiting(BLOCK_ID) == 1, "premise: still queued"
        assert proc._pm_grant_landed() is None, (
            "a request still sitting in the private queue was reported as a "
            "landed grant"
        )

    def test_the_custom_queue_is_counted_not_assumed_empty(self):
        """The positive half of the same case, so the test above cannot pass
        by the predicate being uniformly False."""
        custom = _queued(_queue(0), BLOCK_ID)
        proc = _process(_block(sparing_type=SparingType.CUSTOM),
                        custom_spares=custom)
        proc.pm.spare_pending = 1
        _released(custom, BLOCK_ID)
        assert proc._pm_grant_landed() == "spare"


class TestPurity:
    def test_the_predicate_does_not_decrement(self):
        """Separated from `_claim_pm_grant` precisely so a caller that only
        needs to know WHETHER a grant landed cannot consume it."""
        pool = _queued(_queue(0), BLOCK_ID)
        proc = _process(_block(), resource_pool=pool)
        proc.pm.resource_pending = 1
        _released(pool, BLOCK_ID)
        for _ in range(3):
            assert proc._pm_grant_landed() == "resource"
        assert proc.pm.resource_pending == 1, "the pure predicate consumed the grant"

    def test_claiming_decrements_exactly_once_per_call(self):
        pool = _queued(_queue(0), BLOCK_ID)
        proc = _process(_block(), resource_pool=pool)
        proc.pm.resource_pending = 1
        _released(pool, BLOCK_ID)
        assert proc._claim_pm_grant() == "resource"
        assert proc.pm.resource_pending == 0
        assert proc._claim_pm_grant() is None

    def test_predicate_and_claim_never_disagree_on_the_kind(self):
        """`claim` is defined as `landed` + decrement, so the two cannot
        name different things even as the rule evolves. The D-11 bug was
        exactly two copies of one rule answering differently."""
        for pools in ({"resource_pool": _queue(0)},
                      {"spare_pool": _queue(0)},
                      {"custom_spares": _queue(0)},
                      {"resource_pool": _queue(0), "spare_pool": _queue(0)},
                      {"custom_spares": _queue(0), "spare_pool": _queue(0)}):
            proc = _process(_block(), **pools)
            for q in pools.values():
                _queued(q, 1)
            proc.pm.resource_pending = 1
            proc.pm.spare_pending = 1
            for q in pools.values():
                q.release(1)
            assert proc._pm_grant_landed() == proc._claim_pm_grant()

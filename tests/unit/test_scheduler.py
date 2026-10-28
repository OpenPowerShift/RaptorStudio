"""Tests for `raptor.core.scheduler` -- domain-independent (no Raptor
model objects), since the module's job is generic event-calendar
mechanics. `test_block_lifecycle.py` covers the Raptor-specific proof that
routing the block scenario through this scheduler matches the direct
(non-scheduler) simulation.
"""
from __future__ import annotations

import pytest

from raptor.core.scheduler import (
    ActivityIdentity,
    Owner,
    ProcessHandle,
    Scheduler,
    Wait,
    Wake,
    _Entry,
    choose_next,
)


def _e(handle_id: int, seq: int = 0) -> _Entry:
    return _Entry(time=0.0, seq=seq, handle_id=handle_id, generation=0)


def _id(mapping: dict[int, ActivityIdentity]):
    return lambda e: mapping[e.handle_id]


def test_choose_next_first_non_block_wins_immediately() -> None:
    group = [_e(1, 0), _e(2, 1), _e(3, 2)]
    ident = _id({
        1: ActivityIdentity("block", 1),
        2: ActivityIdentity("front"),
        3: ActivityIdentity("block", 0),
    })
    assert choose_next(group, ident).handle_id == 2


def test_choose_next_lowest_block_id_wins_among_blocks() -> None:
    group = [_e(1, 0), _e(2, 1), _e(3, 2)]
    ident = _id({
        1: ActivityIdentity("block", 30),
        2: ActivityIdentity("block", 10),
        3: ActivityIdentity("block", 20),
    })
    assert choose_next(group, ident).handle_id == 2


# `choose_next` reads `group.Last` as the LAST real entry, which the WHILE
# exits on without comparing -- confirmed 2026-09-24 by
# the run-end instants of MultiOutLink/TriggerWakeOrder/WakeOrderInduced and
# the PooledGraceRepairProbe gdb trace. So the final entry of a group is never
# examined, and a two-entry group returns its first entry unless that entry
# is a non-block owner.

def test_choose_next_endsimulation_is_skipped() -> None:
    # A leading EndSimulation lets the next entry decide: a block there is
    # (provisionally) chosen, and the trailing entry is never examined.
    group = [_e(1, 0), _e(2, 1), _e(3, 2)]
    ident = _id({
        1: ActivityIdentity("endsim", name="EndSimulation"),
        2: ActivityIdentity("block", 9),
        3: ActivityIdentity("front"),
    })
    assert choose_next(group, ident).handle_id == 2


def test_choose_next_endsimulation_then_last_block_is_chosen() -> None:
    # Source reads `group.Next`'s owner even when it IS `group.Last`
    # (the WHILE test only stops the loop afterwards), so [EndSimulation,
    # block] returns the block, not EndSimulation.
    group = [_e(1, 0), _e(2, 1)]
    ident = _id({
        1: ActivityIdentity("endsim", name="EndSimulation"),
        2: ActivityIdentity("block", 4),
    })
    assert choose_next(group, ident).handle_id == 2


def test_choose_next_id_less_helper_loses_to_real_block() -> None:
    group = [_e(1, 0), _e(2, 1), _e(3, 2)]
    ident = _id({
        1: ActivityIdentity("block", None),
        2: ActivityIdentity("block", 5),
        3: ActivityIdentity("block", 1),   # last entry: never examined
    })
    assert choose_next(group, ident).handle_id == 2


def test_choose_next_single_entry() -> None:
    group = [_e(7)]
    ident = _id({7: ActivityIdentity("block", 3)})
    assert choose_next(group, ident).handle_id == 7


def test_choose_next_applies_only_at_the_end_simulation_instant() -> None:
    order: list[str] = []

    def proc(name: str):
        yield 5.0
        order.append(name)

    sched = Scheduler()
    assert sched.cn_active_time is None
    sched.schedule_process(proc("high"), block_id=9)
    sched.schedule_process(proc("low"), block_id=3)
    sched.run_until(10.0)
    assert order == ["high", "low"]  # tuned sort: insertion FIFO

    order.clear()
    sched = Scheduler()
    sched.cn_active_time = 5.0  # as CallEndSimulation's selection sets it
    sched.schedule_process(proc("high"), block_id=9)
    sched.schedule_process(proc("low"), block_id=3)
    sched.schedule_process(proc("last"), block_id=1)  # never examined
    sched.run_until(10.0)
    assert order[0] == "low"  # ChooseNext: lowest block Id among the examined


def test_switching_tie_breaking_off_leaves_end_simulation_in_calendar_order() -> None:
    """`ChangeStatus` turning the system Red calls `SetTieBreaking(FALSE)`: the
    rest of the instant is plain calendar order, where `EndSimulation` is no longer forced last
    (`DepCycleMixedProbe`, real gdb trace 2026-10-01)."""

    def run(switch_off: bool) -> list[str]:
        order: list[str] = []

        def proc(name: str):
            order.append(name)
            return
            yield  # generator

        sched = Scheduler()
        sched.cn_active_time = 5.0
        sched.schedule_process(proc("end"), at=5.0, endsim=True)
        sched.schedule_process(proc("blk"), at=5.0, block_id=1)
        if switch_off:
            sched.switch_off_tie_breaking()
        sched.run_until(10.0)
        return order

    assert run(switch_off=False) == ["blk", "end"]
    assert run(switch_off=True) == ["end", "blk"]


def test_choose_next_front_tier_beats_lower_block_id() -> None:
    order: list[str] = []

    def proc(name: str):
        yield 5.0
        order.append(name)

    sched = Scheduler()
    sched.cn_active_time = 5.0
    sched.schedule_process(proc("blk"), block_id=1)
    sched.schedule_process(proc("pool"), front_tier=True)
    sched.schedule_process(proc("blk2"), block_id=2)  # last: never examined
    sched.run_until(10.0)
    assert order[0] == "pool"


def test_single_process_runs_to_completion() -> None:
    log: list[float] = []

    def proc():
        log.append(0.0)
        yield 5.0
        log.append(5.0)
        yield 3.0
        log.append(8.0)

    sched = Scheduler()
    sched.schedule_process(proc())
    sched.run_until(100.0)

    assert log == [0.0, 5.0, 8.0]
    assert sched.time == 100.0


def test_two_processes_interleave_in_time_order() -> None:
    # Process A fires at t=1,3,5,7,9; process B fires at t=2,4,6,8,10.
    # The recorded order must be strictly time-ordered across both
    # processes, not grouped by which was scheduled first.
    events: list[tuple[float, str]] = []

    def make_proc(name: str, period: float, sched: Scheduler):
        def proc():
            for _ in range(5):
                yield period
                events.append((sched.time, name))
        return proc()

    sched = Scheduler()
    sched.schedule_process(make_proc("A", 2.0, sched), at=1.0)
    sched.schedule_process(make_proc("B", 2.0, sched), at=2.0)
    sched.run_until(20.0)

    times = [t for t, _ in events]
    assert times == sorted(times), "events must be strictly time-ordered"
    assert [name for _, name in events] == ["A", "B", "A", "B", "A", "B", "A", "B", "A", "B"]


def test_same_time_events_resolve_fifo_by_schedule_order() -> None:
    # Tie-break policy is FIFO (insertion order) -- see scheduler.py's
    # module docstring for why this is a documented default rather than a
    # match to the real engine's ChooseNext.
    order: list[str] = []

    def proc(name: str):
        yield 5.0
        order.append(name)

    sched = Scheduler()
    sched.schedule_process(proc("first"))
    sched.schedule_process(proc("second"))
    sched.schedule_process(proc("third"))
    sched.run_until(10.0)

    assert order == ["first", "second", "third"]


def test_run_until_advances_time_even_with_no_events() -> None:
    sched = Scheduler()
    sched.run_until(50.0)
    assert sched.time == 50.0


def test_process_not_rescheduled_past_run_until_boundary() -> None:
    # A process with more waits left than the run_until horizon allows
    # should simply not be resumed again — no error, no processing past
    # the boundary (mirrors EndSimulation's forced cutoff).
    calls: list[float] = []

    def proc(sched: Scheduler):
        while True:
            yield 10.0
            calls.append(sched.time)

    sched = Scheduler()
    sched.schedule_process(proc(sched))
    sched.run_until(35.0)

    assert calls == [10.0, 20.0, 30.0]
    assert sched.time == 35.0


# --- Time-domain validation + keep-seq leak + exception safety ---

def test_run_until_rejects_nan_horizon() -> None:
    with pytest.raises(ValueError, match="NaN"):
        Scheduler().run_until(float("nan"))


def test_run_until_rejects_negative_horizon() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Scheduler().run_until(-1.0)


def test_step_rejects_nan_horizon() -> None:
    with pytest.raises(ValueError, match="NaN"):
        Scheduler().step(float("nan"))


def test_schedule_process_rejects_negative_at() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Scheduler().schedule_process((x for x in ()), at=-1.0)


def _order_after_first_yield(first_yield) -> list[str]:
    """A and B both belong to block 1; A runs first and yields `first_yield`."""
    order: list[str] = []

    def a():
        order.append("A1")
        yield first_yield
        order.append("A2")

    def b():
        order.append("B1")
        yield 0.0
        order.append("B2")

    sched = Scheduler()
    sched.schedule_process(a(), block_id=1)
    sched.schedule_process(b(), block_id=1)
    sched.run_until(1.0)
    return order


def test_yielding_wait_is_yielding_its_duration() -> None:
    assert _order_after_first_yield(Wait(0.0)) == _order_after_first_yield(0.0) == ["A1", "B1", "A2", "B2"]


def test_wait_group_head_keeps_the_head_of_the_owner_group() -> None:
    # The kickoff barrier's hop: the continuation stays ahead of the
    # owner's other same-instant records instead of queueing behind them.
    assert _order_after_first_yield(Wait(group_head=True)) == ["A1", "A2", "B1", "B2"]


def test_wait_replaces_retires_the_slot_placeholder() -> None:
    ran: list[str] = []

    def placeholder():
        yield 1.0e9
        ran.append("placeholder")

    def proc():
        yield Wait(owner=Owner.block_trigger(1), replaces=slot.id)
        ran.append("proc")

    sched = Scheduler()
    slot = sched.schedule_process(placeholder(), owner_key=Owner.block_trigger(1))
    sched.schedule_process(proc(), block_id=1)
    sched.run_until(2.0e9)
    assert ran == ["proc"]


def test_wait_applies_to_a_first_yield_too() -> None:
    # A told process's first yield is received by `tell`, not `step`.
    times: list[float] = []

    def proc():
        yield Wait(2.5)
        times.append(sched.time)

    sched = Scheduler()
    sched.tell(proc())
    sched.run_until(10.0)
    assert times == [2.5]


def test_invalid_wait_duration_is_rejected() -> None:
    def bad():
        yield Wait(-1.0)

    sched = Scheduler()
    sched.schedule_process(bad())
    with pytest.raises(ValueError, match="invalid wait"):
        sched.run_until(10.0)


def test_executing_handle_reset_after_exception() -> None:
    def boom():
        raise RuntimeError("boom")
        yield  # pragma: no cover

    sched = Scheduler()
    sched.schedule_process(boom())
    with pytest.raises(RuntimeError, match="boom"):
        sched.run_until(1.0)
    assert sched.executing_handle_id is None


def test_process_yielding_negative_wait_is_rejected() -> None:
    def bad():
        yield -1.0

    sched = Scheduler()
    sched.schedule_process(bad())
    with pytest.raises(ValueError, match="invalid wait"):
        sched.run_until(10.0)


def test_process_yielding_nan_wait_is_rejected() -> None:
    def bad():
        yield float("nan")

    sched = Scheduler()
    sched.schedule_process(bad())
    with pytest.raises(ValueError, match="invalid wait"):
        sched.run_until(10.0)


def test_tell_process_is_executing_alongside_its_caller() -> None:
    """A `tell`-spawned process runs nested inside its caller, so
    both are on the execution stack -- `is_executing` reports membership,
    protecting the in-flight outer turn from a self-switch."""
    sched = Scheduler()
    observed: dict[str, bool] = {}
    outer_box: list = []

    def told():
        # `executing_handle_id` is the told process (stack top); the outer
        # caller is below it on the stack.
        observed["told_exec"] = sched.is_executing(sched.executing_handle_id)
        observed["outer_exec"] = sched.is_executing(outer_box[0].id)
        yield 1.0

    def outer():
        sched.tell(told(), block_id=2)
        yield 5.0

    outer_box.append(sched.schedule_process(outer(), block_id=1))
    sched.run_until(10.0)

    assert observed == {"told_exec": True, "outer_exec": True}
    assert sched.executing_handle_id is None


# --- Deferred `tell`: a TELL body starts as its own selection at the tail of the instant ---

def _run_log(sch: Scheduler, until: float = 10.0) -> None:
    sch.run_until(until)


def test_deferred_tell_starts_after_the_callers_turn_and_pending_entries() -> None:
    """`deferred=True` runs the body after the spawning turn ends and
    after every entry already pending at that instant, rather than inline.
    The synchronous default still runs it inline."""
    sch = Scheduler()
    log: list[str] = []

    def spawned(tag: str):
        log.append(f"{tag}-start")
        return
        yield  # pragma: no cover

    def other():                                   # pending at t=0 BEFORE the spawner's turn
        log.append("other")
        return
        yield  # pragma: no cover

    def spawner():
        log.append("spawner-begin")
        sch.tell(spawned("deferred"), deferred=True)
        sch.tell(spawned("sync"))
        log.append("spawner-end")
        return
        yield  # pragma: no cover

    sch.schedule_process(spawner())
    sch.schedule_process(other())
    _run_log(sch)
    assert log == ["spawner-begin", "sync-start", "spawner-end", "other", "deferred-start"]


def test_deferred_tell_bodies_start_in_creation_order() -> None:
    sch = Scheduler()
    log: list[int] = []

    def body(i: int):
        log.append(i)
        return
        yield  # pragma: no cover

    def spawner():
        for i in (1, 2, 3):
            sch.tell(body(i), deferred=True)
        return
        yield  # pragma: no cover

    sch.schedule_process(spawner())
    _run_log(sch)
    assert log == [1, 2, 3]


def test_interrupt_before_a_deferred_tell_has_started_starts_it_first() -> None:
    """Interrupting a pending-start TELL starts the body up to its first
    wait and then delivers the interrupt, rather than crashing with
    "can't send non-None value to a just-started generator"."""
    sch = Scheduler()
    log: list[object] = []

    def switch():
        log.append("startup")
        resume = yield 100.0                       # would wait 100 units
        log.append(("woke", resume))
        return

    def caller():
        h = sch.tell(switch(), deferred=True)
        sch.interrupt(h, "Switched_Off")           # BEFORE the body's own start entry is selected
        return
        yield  # pragma: no cover

    sch.schedule_process(caller())
    _run_log(sch)
    assert log == ["startup", ("woke", Wake("Switched_Off"))]
    assert sch.time < 100.0                        # woken at t=0, not after the 100-unit wait


def test_interrupt_to_a_deferred_tell_that_finishes_during_startup_is_a_noop() -> None:
    sch = Scheduler()
    log: list[str] = []

    def short():
        log.append("ran")
        return
        yield  # pragma: no cover

    def caller():
        h = sch.tell(short(), deferred=True)
        sch.interrupt(h, "x")
        return
        yield  # pragma: no cover

    sch.schedule_process(caller())
    _run_log(sch)
    assert log == ["ran"]


def test_owner_group_calendar_round_robin_and_tail_readd() -> None:
    """The real engine keeps one calendar group per owner
    (`exe 0x5e47c0`): at a tied instant owners take turns, and an owner's
    second activity is served after every other owner's first."""
    from raptor.core.scheduler import Scheduler

    sched = Scheduler()
    seen: list[str] = []

    def proc(name: str):
        def body():
            seen.append(name)
            return
            yield  # pragma: no cover
        return body()

    sched.schedule_process(proc("a1"), at=0.0, block_id=1)
    sched.schedule_process(proc("a2"), at=0.0, block_id=1)
    sched.schedule_process(proc("b1"), at=0.0, block_id=2)
    sched.run_until(1.0)
    assert seen == ["a1", "b1", "a2"]


def test_cancel_drops_a_pending_process_and_ignores_unknown_handles() -> None:
    # `cancel` is idempotent: cancelling a process twice, or a handle this
    # scheduler never issued, changes nothing. (A block's `BlockTrigger`
    # slot placeholder is retired through `cancel`; before per-run state
    # moved to `BlockRunState`, a placeholder id left over from the previous
    # run reached this path in ReleaseProbe32.)
    ran: list[str] = []

    def proc(name: str):
        yield 1.0
        ran.append(name)

    sched = Scheduler()
    dropped = sched.schedule_process(proc("dropped"))
    sched.schedule_process(proc("kept"))
    sched.cancel(dropped)
    sched.cancel(dropped)
    sched.cancel(ProcessHandle(id=999))
    sched.run_until(10.0)
    assert ran == ["kept"]

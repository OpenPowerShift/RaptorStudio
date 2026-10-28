"""A minimal discrete-event scheduler — the Python analog of Modsim II's
process-based `WAIT`/event-calendar semantics.

Each simulated "process" (a block's `Run`, an event's lifecycle, etc.) is a
plain Python generator. `yield <duration>` is the analog of
`WAIT DURATION <duration>` — it suspends the process and hands control back
to the scheduler, which resumes it once `duration` simulated time units have
passed (and every other process due at or before that time has also run).
This keeps a ported `Run`-style method readable next to its Modsim source
almost line-for-line (a `WAIT DURATION x; END WAIT;` becomes `yield x`).

`Scheduler.interrupt` is the analog of Modsim's `Interrupt(process, "Run")`:
it cuts a suspended `yield` short and hands the process a `Wake(reason)`
instead of the `None` an ordinary timeout wakeup would give it,
mirroring the source's `ON INTERRUPT` `WAIT` clause. It is used by
`BackFlowDependencies` (see `dependency.py`) to pause/resume a dependent
block's own failure countdown mid-wait — see `tests/unit/test_dependency.py`.

Tie-breaking for simultaneous events is FIFO by `seq` (insertion order), with
one refinement: a handle's whole causal "turn" at a given instant stays
pinned at the position where it was first disturbed. `Scheduler._first_pushed`
records the `seq` of a handle's first calendar entry at an instant, and a
later `interrupt()` for the same handle inherits it, so a re-interrupt
replaces the pending wait rather than moving the handle to the back of the
same-instant queue. An ordinary self-continuation pushed after a resume still
takes a fresh `seq`. `step` additionally sorts pool-owned processes
(`front_tier`) ahead of everything else, and orders a tied group by the
recovered owner-group calendar (see `Scheduler.__init__`). `choose_next`
ports `RBDSimControlObj.ChooseNext`, which source
applies only from `CallEndSimulation`'s selection to the end of that instant
(`cn_active_time`).
"""
from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field
from typing import NamedTuple
from collections.abc import Callable, Generator

#: The calendar is a real-time domain -- a NaN horizon makes
#: `run_until`'s `entry.time > end_time` comparisons all False (livelock),
#: and a negative horizon silently walks time backwards. Reject both.
def _validate_time(value: float, name: str) -> float:
    if isinstance(value, float) and math.isnan(value):
        raise ValueError(f"{name} must not be NaN")
    if value < 0.0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return value

class Owner(NamedTuple):
    """The runtime object that owns an activity (MODSIM's `ActivityOwner`).
    The owner-group calendar keeps one group per owner, and `choose_next`
    treats block owners and non-block owners differently."""

    kind: str
    ident: object = None

    @classmethod
    def block(cls, block_id: int) -> Owner:
        """An `RBDBlockObj` (joins that block's own owner group)."""
        return cls("block", block_id)

    @classmethod
    def system(cls) -> Owner:
        """The `System` object (its tickers and `CallEndSimulation`)."""
        return cls("system")

    @classmethod
    def trigger(cls, name: str) -> Owner:
        return cls("trigger", name)

    @classmethod
    def pool(cls, name: str) -> Owner:
        """A `PoolObj` (its `GenerateSpares`/`OrderASpare`/`OrderStock`)."""
        return cls("pool", name)

    @classmethod
    def block_trigger(cls, block_id: int) -> Owner:
        """A block's `BlockTrigger` (its `Release` records)."""
        return cls("block_trigger", block_id)

    @classmethod
    def give(cls, seq: int) -> Owner:
        """One pool `Give` resume (each request is its own record owner)."""
        return cls("give", seq)

    @classmethod
    def handle(cls, handle_id: int) -> Owner:
        """A port-only process with no source-side owner: alone in its group."""
        return cls("handle", handle_id)


#: Owner kinds `choose_next` treats as non-block owners.
_NON_BLOCK_OWNERS = frozenset({"system", "trigger", "pool", "block_trigger", "give"})


@dataclass(frozen=True, slots=True)
class Wake:
    """What an interrupted wait receives: MODSIM's `ON INTERRUPT` with the
    object's `InterruptReason`. `token` identifies which pool grant a
    `"Pool_Available"` wake announces ("pm"/"repair"), else None."""

    reason: str
    token: object | None = None


@dataclass(frozen=True)
class Wait:
    """A wait that also says where the process's continuation goes in the
    owner-group calendar. `yield Wait(d)` is `yield d`; the other fields
    shape that one requeue, and apply to the yielding process only.

    They port placements that Raptor 7 gets from being a different MODSIM
    activity than the port's one generator: the kickoff barrier is a hop
    inside Raptor 7's single `Run` TELL start (`group_head`), and the PM
    hand-back's Run wake is a `BlockTrigger.Release` record or an
    `Interrupt(SELF, "Run")` re-stamp (`owner`, `retail`, `replaces`).
    """

    duration: float = 0.0
    #: Keep the head of the process's owner group instead of queueing
    #: behind the group's other same-instant records. Applies only when the
    #: requeue is not later than the group's key.
    group_head: bool = False
    #: Requeue as a record of this owner instead of the process's own
    #: (a block's `Owner.block_trigger` group).
    owner: Owner | None = None
    #: Interrupt semantics: the owner's group goes to the calendar tail, as
    #: an `Interrupt(block, "Run")` re-stamp does.
    retail: bool = False
    #: A slot placeholder this requeue takes over; it is retired after the
    #: requeue has joined its group, so the group keeps its position.
    replaces: int | None = None


#: A process is a generator that yields the durations it wants to wait
#: (a `float`, or a `Wait` when the requeue needs shaping).
#: `yield duration` suspends until `duration` sim-time units have passed
#: on an ordinary timeout wakeup, at which point it receives `None` back
#: (the resume value of the `yield` expression) -- unless the wait was
#: cut short by `Scheduler.interrupt`, in which case it receives a `Wake`
#: instead. A process that never calls `Scheduler.interrupt` can ignore
#: the resume value entirely.
Process = Generator[float | Wait, Wake | None, None]


#: Same-instant tier by owner kind: pool processes first, EndSimulation
#: last, everything else (1) in between.
_TIER: dict[str | None, int] = {"front": 0, "endsim": 2}
#: After `SetTieBreaking(FALSE)` mid-instant `EndSimulation` keeps the calendar position it has.
_TIER_CN_OFF: dict[str | None, int] = {"front": 0}


@dataclass(frozen=True)
class ProcessHandle:
    """Opaque identity returned by `schedule_process`, needed to target a
    specific process with `interrupt` later on."""

    id: int


@dataclass
class _Entry:
    """One calendar entry. Deliberately NOT `@dataclass(order=True)` —
    that would compare every field including `handle_id`/`generation`
    against another entry's in ways that don't matter for ordering, so
    `__lt__` is defined explicitly on `(time, seq)` instead (the tie-break
    key). `generation` is the lazy-deletion token: if it no longer matches
    `Scheduler._generations[handle_id]` by the time this entry is popped,
    the process was `interrupt`-ed out from under this particular wait and
    the entry is silently discarded instead of resumed (see `interrupt`'s
    docstring). `interrupt_reason`: `None` for an ordinary timeout wakeup
    (resumed with `None`); otherwise this entry represents a PENDING
    interrupt (resumed with `Wake(interrupt_reason, token)`) -- a queued
    calendar entry, not an immediate nested `send()`."""

    time: float
    seq: int
    handle_id: int
    generation: int
    interrupt_reason: str | None = None
    #: `Wait(group_head=True)`: this continuation goes to the HEAD of its
    #: owner's same-instant records (the kickoff barrier's hop). `step`
    #: gets that from `_gc_insert`'s `_gc_head_handle`; the batch tie
    #: grouping has to carry the flag explicitly.
    group_head: bool = False
    #: Optional identity carried with a `"Pool_Available"` interrupt (see
    #: `interrupt`'s own docstring): which KIND of pool grant this wake
    #: announces, so the resumed wait can tell whether the grant belongs
    #: to it or to a DIFFERENT outstanding request of the same block.
    #: Delivered as the 3rd element of the resume tuple when set.
    token: object | None = None
    #: Per-record owner (see `interrupt`'s `gc_owner`), for records whose
    #: MODSIM owner is not the process's own object.
    gc_owner: Owner | None = None

    def __lt__(self, other: _Entry) -> bool:  # pragma: no cover - exercised via heapq
        if self.time == other.time:
            return self.seq < other.seq
        return self.time < other.time


@dataclass(eq=False)
class OwnerGroup:
    """One owner's pending records, in wake-time order (Raptor 7's per-owner
    group, cached at `[owner+0xc]`).

    `eq=False`: every `_gc_cal` membership test and `_gc_rank` lookup used to
    compare groups by their whole `recs` list, recursively, because a plain
    dataclass generates `__eq__`. Identity is the right comparison here, and
    it keeps the same-instant tie sort O(groups) instead of O(groups x recs).
    """

    recs: list[_Entry] = field(default_factory=list)
    #: Tail-stamp of this group in the recovered owner-group calendar,
    #: assigned whenever the group is (re)appended. `_gc_rank` uses it
    #: instead of `_gc_cal.index`.
    order: int = 0

    @property
    def head_time(self) -> float:
        """The group's calendar key: its earliest record's wake time."""
        return self.recs[0].time


@dataclass(frozen=True)
class ActivityIdentity:
    """Port of Modsim's `ActivityOwner` + `ActivityName` pair, as far as
    this port can supply it. `ChooseNext` reads
    exactly two things off an activity: whether its owner's runtime type
    is `"RBDBlockObj"`, and — for the `EndSimulation` special case — the
    activity NAME. Everything else it decides from the block owner's
    `Id`.

    `owner_kind` is this port's classification of the owning handle:
    `"block"` (an `RBDBlockObj`-owned activity, carries `block_id`),
    `"front"` (a PoolObj-owned TELL METHOD — non-block, and the only
    non-block owner the Raptor 7 captures actually exercise), `"endsim"`
    (the terminal `EndSimulation` activity), or `None` (a port-only
    helper process with no source-side activity behind it; these must
    NOT be treated as ordinary non-block owners or every kickoff
    reorders). `name` is only ever consulted for the `"EndSimulation"`
    literal.
    """

    owner_kind: str | None
    block_id: int | None = None
    name: str | None = None


def choose_next(group: list[_Entry], identity) -> _Entry:
    """Faithful port of `RBDSimControlObj.ChooseNext`
   over an already
    insertion-ordered same-instant group.

    `identity(entry) -> ActivityIdentity` supplies the owner/name pair.
    The source algorithm, verbatim in spirit:

    * scan the group in its own (insertion) order;
    * the FIRST non-block owner wins immediately (returns that slot) —
      EXCEPT `EndSimulation`, which is skipped so the next entry decides
      (a block there only wins if no block was seen earlier; a non-block
      there wins outright);
    * among `RBDBlockObj` owners, the LOWEST `Id` wins;
    * `doFirst` defaults to the group's first slot.

    This is deliberately a pure function so it can be unit-tested against
    the source semantics on its own.

    The source gates this selector: `SetTieBreaking(FALSE)` at run start,
   re-enabled only in `CallEndSimulation`,
    and `TimeAdvance` disables it once time advances. So the bulk
    of a run uses plain insertion order -- which the port's tuned
    `seq`/tier sort reproduces -- and `ChooseNext` governs only the rest of
    the instant `CallEndSimulation` is selected at (`Scheduler.cn_active_time`).

    `group.Last` is read as a ONE-PAST-END sentinel: with it as the last
    real index the final entry could never be selected by the block
    branch, which is not a usable selector. `group.Next` is the only
    traversal method `ActivityGroup` exposes, so a cursor-walks-until-
    sentinel shape is the only reading that makes sense.
    """
    # `group.Last` is the LAST real entry, which the WHILE exits on without
    # comparing, as a gdb trace of Raptor 7 showed. Confirmed 2026-09-24: with ChooseNext live
    # only from `CallEndSimulation`'s selection, this reading makes
    # MultiOutLink/TriggerWakeOrder/WakeOrderInduced's run-end instants
    # (trigger fire + two PMs) and the pool-order run ends exact.
    last = len(group) - 1
    act = 0
    do_first = 0
    owner = identity(group[0])
    temp1 = owner if owner.owner_kind == "block" else None
    temp1set = temp1 is not None
    while act != last:
        owner = identity(group[act])
        if owner.owner_kind != "block":
            if owner.name == "EndSimulation":
                act += 1
                # Source reads `group.Next`'s owner even when it is
                # `group.Last` (it only stops at the WHILE test); only a step
                # past the real entries ends the scan here.
                if act >= len(group):
                    break
                owner = identity(group[act])
                if owner.owner_kind == "block":
                    if not temp1set:
                        temp1 = owner
                        do_first = act
                        temp1set = True
                else:
                    return group[act]
            else:
                return group[act]
        else:
            if temp1 is None or (owner.block_id is not None
                                 and (temp1.block_id is None
                                      or owner.block_id < temp1.block_id)):
                temp1 = owner
                do_first = act
            act += 1
    return group[do_first]


class Scheduler:
    """A minimal discrete-event calendar. Not thread-based, not async —
    just a heap of `(time, insertion_order)` -> generator, popped in
    time order (see module docstring for the tie-break caveat)."""

    def __init__(self) -> None:
        self.time: float = 0.0
        self._calendar: list[_Entry] = []
        self._counter = itertools.count()
        self._handle_counter = itertools.count()
        self._processes: dict[int, Process] = {}
        self._generations: dict[int, int] = {}
        #: Handle id of the process currently inside its own `step`
        #: turn (`process.send` in flight), else None. Lets interrupt
        #: senders distinguish "wake a parked process" from "flag the
        #: turn that's already running" -- see
        #: orchestrator._Cascade._switch_interrupt's cooperative branch.
        #: This is the TOP of `_exec_stack`; a `tell`-spawned process runs
        #: nested inside its caller, so both are "executing" -- use
        #: `is_executing` for a membership test.
        self.executing_handle_id: int | None = None
        #: Nested execution stack (outer `step` turn, then any `tell`-spawned
        #: process it runs synchronously). See `is_executing`.
        self._exec_stack: list[int] = []
        #: GUI intra-run pause hook: when set, called before EVERY
        #: processed event -- a BLOCKING callable freezes sim time mid-run
        #: (the calendar is untouched; resume simply returns). Default
        #: None = zero overhead for batch runs.
        self.block_hook: Callable[[], None] | None = None
        #: handle_id -> "block" | "front" | "endsim" | None; see
        #: `schedule_process`'s docstring for the ChooseNext tie-break.
        self._owners: dict[int, str | None] = {}
        #: handle_id -> owning `RBDBlockObj.Id` for `"block"` owners, so
        #: `choose_next` can apply the source's lowest-Id tie-break.
        self._owner_ids: dict[int, int | None] = {}
        #: handle_id -> (time, seq) of that handle's own FIRST calendar
        #: entry pushed at that time -- every LATER push for the same
        #: handle at the SAME instant (an ordinary self-continuation, a
        #: re-interrupt, or both) reuses this seq instead of getting a
        #: fresh one, so a handle's whole causal "turn" this instant stays
        #: pinned at the position it was first disturbed, no matter how
        #: many times it's re-touched afterward. See `_push`.
        self._first_pushed: dict[int, tuple[float, int]] = {}
        #: Handles created by a DEFERRED `tell` whose body has not yet been selected. An interrupt
        #: reaching one must START it first (see `_start_unstarted`), never send it an interrupt value.
        self._unstarted: set[int] = set()
        #: The recovered owner-group calendar. Raptor 7 keeps ONE group per owner
        #: (its cached `[owner+0xc]`), holding that owner's pending
        #: activities sorted by wake time; the calendar orders GROUPS by
        #: their earliest wake, ties by the order the group was (re)added.
        #: A group is re-added at the calendar TAIL whenever its head
        #: changes (a pop with records left, or an earlier-time insertion).
        #: Used ONLY to order a tied group in `step` (tier, then this rank,
        #: then seq).
        self._gc_groups: dict[Owner, OwnerGroup] = {}
        self._gc_cal: list[OwnerGroup] = []
        self._gc_order = itertools.count()
        self._gc_live: dict[int, tuple[OwnerGroup, _Entry]] = {}
        #: handle id -> explicit owner key for a non-block owner (a spare
        #: pool's `OrderASpare` TELLs all share their pool). Without it a
        #: non-block handle is its own one-activity owner.
        self._gc_owner_override: dict[int, Owner] = {}
        #: The instant a batch tie-group is being processed for. Its
        #: remaining entries live in `_run_tie_group`'s buckets rather than
        #: the heap; `next_time` must still report that instant so per-event
        #: observers see "more at this instant" exactly as `step` did.
        self._tie_in_flight: float | None = None
        #: Entries of that instant still waiting in the fast-path buckets.
        self._tie_pending: int = 0
        #: Set by `interrupt` while a batch tie group is in flight. The
        #: interrupt protocol mutates/replaces records in ways that depend
        #: on which record is still first; rather than re-derive that
        #: ordering inside the fast path, hand the REST of the instant back
        #: to `step` (which is the authority) and keep already-processed
        #: entries -- their order matched it up to this point.
        self._tie_interrupted = False
        #: Once any interrupt has fired inside a fast tie group, this
        #: run's same-instant ordering is left to `step` for the rest of
        #: the run. MODSIM's "a second interrupt mutates the first record"
        #: rule makes the fast path's bucket order depend on cascade order
        #: in a way only `step` reproduces exactly.
        self._fast_ties_disabled = False
        #: The instant at which `CallEndSimulation` was selected: ChooseNext
        #: governs the rest of that instant (`SetTieBreaking(TRUE)`,
        #: Raptor 7; `TimeAdvance` turns it off).
        self.cn_active_time: float | None = None
        #: The instant whose `ChooseNext` phase `switch_off_tie_breaking` cut short.
        self.cn_off_time: float | None = None
        #: Handles of pure bookkeeping tickers (the System's
        #: UpdateSimStatus/UpdateCumulativeValues) whose turns change no model
        #: state; `last_active_time` records the last instant any OTHER
        #: process ran, for callers that act "at the end of every instant
        #: something ran" (capacity monitors).
        self.passive_handles: set[int] = set()
        self.last_active_time: float | None = None
        #: The handle whose next group insertion takes the head of its
        #: owner group (`Wait.group_head`, `schedule_process(at_group_head=)`).
        #: Set and consumed within one push.
        self._gc_head_handle: int | None = None

    def schedule_process(self, process: Process, *, at: float | None = None,
                         block_id: int | None = None, front_tier: bool = False,
                         endsim: bool = False, owner_key: Owner | None = None,
                         at_group_head: bool = False) -> ProcessHandle:
        """Register a new process. `at` defaults to the scheduler's
        current time (i.e. it becomes runnable immediately, on the next
        `run_until` step). Returns a `ProcessHandle` -- keep it if this
        process may need `interrupt`-ing later; otherwise it can be
        discarded, same as ignoring this return value entirely.

        Deferred, not synchronous: the process is registered and a
        zero-delay entry pushed, resolved via `run_until`'s
        insertion-order tie-break. For a `TELL METHOD`-style spawn from
        INSIDE another process's own currently-running turn (Modsim's
        `TELL x TO method(...)`, which transfers control to the new
        process's body IMMEDIATELY, up to ITS OWN first `WAIT`, before the
        caller's own remaining code continues), use `tell` instead -- see
        that method's docstring for why the two are genuinely different,
        not just a style choice.

        `block_id`: pass the owning RBDBlockObj's Id for processes that
        ARE one of an RBDBlockObj's own TELL METHODs in the source (`Run`,
        `Repair`, `GenerateSpares`, `OrderASpare`, ... -- anything the
        source declares on RBDBlockObj). `front_tier`: likewise for
        PoolObj's own TELL METHODs (`GenerateSpares`/`OrderASpare`/
        `OrderStock`,+). Same-time tie-breaking
        (`step`'s docstring) sorts ONLY pool-owned processes ahead of
        everything else, porting the ONE part of `RBDSimControlObj.
        ChooseNext` that the Raptor 7 captures
        actually exercise: non-block owners beat block owners. Untagged
        handles keep the plain seq-FIFO behavior mixed with blocks --
        deliberately, because most of this port's helper processes
        (cold-standby kickoff redistribution, deferred system-status
        checks, ...) have NO separate source-side process behind them, and
        putting those ahead of blocks at t=0 catastrophically reorders
        kickoff. `choose_next`'s block-Id ordering applies only at the
        end-of-run instant (see `step`).

        `at_group_head`: the new process takes the head of its owner group,
        for a process source TELLs before the spawning activity (a block's
        own custom `GenerateSpares`)."""
        handle_id = next(self._handle_counter)
        self._processes[handle_id] = process
        self._generations[handle_id] = 0
        self._owners[handle_id] = (
            "block" if block_id is not None
            else ("endsim" if endsim else ("front" if front_tier else None))
        )
        self._owner_ids[handle_id] = block_id
        if owner_key is not None:
            self._gc_owner_override[handle_id] = owner_key
        start_time = self.time if at is None else at
        _validate_time(start_time, "schedule_process at")
        if at_group_head:
            self._gc_head_handle = handle_id
        self._push(handle_id, start_time, 0)
        return ProcessHandle(id=handle_id)

    def tell(self, process: Process, *, block_id: int | None = None,
             front_tier: bool = False, deferred: bool = False,
             owner_key: Owner | None = None) -> ProcessHandle:
        """Spawn `process` and run it SYNCHRONOUSLY, right now, up to its
        own first `yield` -- the Python analog of Modsim's `TELL x TO
        method(...)`, which immediately transfers control into the new
        process's body (unlike `ASK`/an ordinary call, `TELL` doesn't
        wait for the WHOLE method to finish -- just runs it up to its own
        first `WAIT`, then hands control back to the caller).

        Differs from `schedule_process` (which only REGISTERS a process,
        deferred to whenever `run_until`'s own popping loop naturally
        reaches it) in exactly the same way `interrupt` differs from
        pushing a plain calendar entry: some callers need the spawned
        process's own STARTUP code (any log lines/side effects before its
        first `WAIT`) to happen as part of THEIR OWN current turn, not
        deferred to later.

        `deferred=True` is what the Raptor 7 runtime does for every TELL: the
        body starts as its OWN selection at the TAIL of the instant, after
        the caller's turn and every entry already pending. Used for
        `PerformSwitch` and the phasing statistics reset. An interrupt
        reaching a deferred TELL BEFORE its start entry is selected starts
        it first (`_start_unstarted`) -- a defined convention that keeps
        the cold-standby fixtures exact.

        The ESO `OrderASpare` spawns on the FAILURE path of
        `block_run_process` are deferred too. In source `CheckForSpare`
        (which spawns `OrderASpare`) runs inside `DetermineNewState`,
        BEFORE the `Repair;` TELL spawn, so `OrderASpare` is created --
        and starts -- first (see `_repair_seam` in `block_lifecycle.py`),
        so a deferred start naturally precedes Repair's own work.

        Still SYNCHRONOUS, because it has no such seam to sit behind: the
        ESO spawn in the PM path (`ObtainPMprereqs`).
        """
        handle_id = next(self._handle_counter)
        self._processes[handle_id] = process
        self._generations[handle_id] = 0
        self._owners[handle_id] = (
            "block" if block_id is not None else ("front" if front_tier else None)
        )
        self._owner_ids[handle_id] = block_id
        if owner_key is not None:
            # A non-block runtime object that owns this activity (e.g. a
            # spare pool): the owner-group calendar groups every activity of
            # that one object together, so its second tied activity waits
            # for all other owners' first -- see `_gc_owner_key`.
            self._gc_owner_override[handle_id] = owner_key
        # `tell` runs the spawned process SYNCHRONOUSLY. Push the told
        # handle onto the execution stack so it is identifiable as
        # executing (alongside the enclosing caller) -- see `is_executing`.
        if deferred:
            # The body starts as its OWN scheduler selection at the TAIL of
            # the instant, after the caller's turn and every already-pending
            # entry -- what the Raptor 7 runtime does for every TELL. Opt-in
            # per call site.
            self._unstarted.add(handle_id)
            self._push(handle_id, self.time, 0)
            return ProcessHandle(id=handle_id)
        self._push_exec(handle_id)
        try:
            yielded = process.send(None)
        except StopIteration:
            self._retire(handle_id)
            return ProcessHandle(id=handle_id)
        finally:
            self._pop_exec(handle_id)
        self._requeue(handle_id, yielded, 0)
        return ProcessHandle(id=handle_id)

    def _start_unstarted(self, handle_id: int) -> None:
        """Run a deferred TELL's body up to its first `yield` NOW, because
        an interrupt has reached it before its own start entry was
        selected. Its pending start entry goes stale (generation bump)
        and its first wait is pushed instead, so the caller's `interrupt`
        proceeds against a SUSPENDED process exactly as it does for any
        other. The process is treated as having started."""
        self._unstarted.discard(handle_id)
        self._generations[handle_id] += 1
        self._push_exec(handle_id)
        try:
            yielded = self._processes[handle_id].send(None)
        except StopIteration:
            self._retire(handle_id)
            return
        finally:
            self._pop_exec(handle_id)
        self._requeue(handle_id, yielded, self._generations[handle_id])

    def _push(
        self, handle_id: int, time: float, generation: int, *,
        interrupt_reason: str | None = None, seq: int | None = None,
        token: object | None = None, gc_owner: Owner | None = None, gc_retail: bool = False,
        group_head: bool = False,
    ) -> None:
        if seq is None:
            seq = next(self._counter)
        first = self._first_pushed.get(handle_id)
        if first is None or first[0] != time:
            self._first_pushed[handle_id] = (time, seq)
        entry = _Entry(
            time=time, seq=seq, handle_id=handle_id, generation=generation,
            interrupt_reason=interrupt_reason, token=token, gc_owner=gc_owner,
            group_head=group_head,
        )
        heapq.heappush(self._calendar, entry)
        self._gc_insert(entry, interrupt=interrupt_reason is not None or gc_retail, gc_owner=gc_owner)

    def switch_off_tie_breaking(self) -> None:
        """`SetTieBreaking(FALSE)` (the system turning Red inside `ChangeStatus`).
        The rest of the `CallEndSimulation` instant is then plain calendar order, in which
        `EndSimulation` is an ordinary entry rather than last."""
        if self.cn_active_time is not None:
            self.cn_off_time = self.cn_active_time
            self.cn_active_time = None

    def _gc_owner_key(self, handle_id: int) -> Owner:
        """The owner whose group `handle_id`'s records join: an explicit
        `owner_key`, else its block, else the handle alone. (An explicit
        `Owner.block(id)` joins that block's group without the rest of
        `block_id`'s meaning, the identity used for block tie rules.)"""
        override = self._gc_owner_override.get(handle_id)
        if override is not None:
            return override
        bid = self._owner_ids.get(handle_id)
        return Owner.block(bid) if bid is not None else Owner.handle(handle_id)

    def _gc_remove(self, handle_id: int) -> None:
        """Drop `handle_id`'s live record from the owner-group model (a real record is one per
        activity: re-scheduling, interrupting or selecting it removes the old entry)."""
        live = self._gc_live.pop(handle_id, None)
        if live is None:
            return
        g, e = live
        g.recs.remove(e)
        if not g.recs and g in self._gc_cal:
            self._gc_cal.remove(g)

    def _gc_tail(self, g: OwnerGroup) -> None:
        """Move `g` to the recovered calendar tail and stamp its order."""
        if g in self._gc_cal:
            self._gc_cal.remove(g)
        g.order = next(self._gc_order)
        self._gc_cal.append(g)

    def _gc_insert(self, entry: _Entry, *, interrupt: bool = False, gc_owner: Owner | None = None) -> None:
        self._gc_remove(entry.handle_id)
        g = self._gc_groups.setdefault(
            gc_owner if gc_owner is not None else self._gc_owner_key(entry.handle_id), OwnerGroup())
        recs = g.recs
        if self._gc_head_handle == entry.handle_id:
            self._gc_head_handle = None
            if recs and entry.time <= g.head_time:
                recs.insert(0, entry)
                self._gc_live[entry.handle_id] = (g, entry)
                return
        if not recs:
            recs.append(entry)
            self._gc_tail(g)
        elif entry.time < g.head_time:
            recs.insert(0, entry)
            self._gc_tail(g)
        else:
            i = len(recs)
            while i > 0 and recs[i - 1].time > entry.time:
                i -= 1
            recs.insert(i, entry)
            if interrupt:
                # An interrupt re-stamps the owner's record and sends its
                # whole group to the calendar tail, even at an equal time
                # (gdb trace, Phasing1 run 6 t=0: SolarP_500W's group,
                # interrupted by an induced failure, was served after every
                # other pending block group; the re-stamped Run record
                # itself sat behind the group's other t=0 records).
                self._gc_tail(g)
        self._gc_live[entry.handle_id] = (g, entry)

    def _gc_select(self, e: _Entry) -> None:
        """`e` was chosen: pop it; a group with records left goes to the calendar tail."""
        live = self._gc_live.get(e.handle_id)
        if live is None or live[1] is not e:
            return
        g = live[0]
        self._gc_remove(e.handle_id)
        if g.recs:
            self._gc_tail(g)

    def _gc_rank(self, e: _Entry) -> tuple[int, int]:
        live = self._gc_live.get(e.handle_id)
        if live is None or live[1] is not e:
            return (1 << 30, e.seq)
        g = live[0]
        for i, candidate in enumerate(g.recs):
            if candidate is e:
                return (g.order, i)
        return (g.order, len(g.recs))

    def _retire(self, handle_id: int) -> None:
        self._processes.pop(handle_id, None)
        self._generations.pop(handle_id, None)
        self._owners.pop(handle_id, None)
        self._owner_ids.pop(handle_id, None)

    def _push_exec(self, handle_id: int) -> None:
        self._exec_stack.append(handle_id)
        self.executing_handle_id = handle_id

    def _pop_exec(self, handle_id: int) -> None:
        if self._exec_stack and self._exec_stack[-1] == handle_id:
            self._exec_stack.pop()
        elif handle_id in self._exec_stack:
            self._exec_stack.remove(handle_id)
        self.executing_handle_id = self._exec_stack[-1] if self._exec_stack else None

    def is_executing(self, handle_id: int) -> bool:
        """True if `handle_id`'s process is currently on the execution
        stack -- either the innermost `step` turn or ANY enclosing
        `tell`-spawned process still running synchronously inside it. The
        switch guard needs membership, not top-of-stack equality: an
        in-flight outer turn must still be protected while a process it
        `tell`ed runs."""
        return handle_id in self._exec_stack

    def _identity(self, entry: _Entry) -> ActivityIdentity:
        """Best-effort `ActivityOwner`/`ActivityName` for a calendar entry
        (see `choose_next`). `"block"` owners carry their `RBDBlockObj.Id`;
        `"front"` is a PoolObj-owned TELL METHOD; `"endsim"` is the
        terminal activity. A port-only helper (no source-side process
        behind it) is reported as a block with NO `Id`: it must not win
        as a non-block owner (that reorders kickoff catastrophically, see
        `schedule_process`'s docstring), but a real block with an `Id`
        beats it."""
        own = entry.gc_owner if entry.gc_owner is not None else self._gc_owner_override.get(entry.handle_id)
        if own is not None and own.kind in _NON_BLOCK_OWNERS:
            if self._owners.get(entry.handle_id) == "endsim":
                return ActivityIdentity("endsim", name="EndSimulation")
            return ActivityIdentity("front")
        if own is not None and own.kind == "block":
            return ActivityIdentity("block", own.ident if isinstance(own.ident, int) else None)
        kind = self._owners.get(entry.handle_id)
        if kind == "block":
            return ActivityIdentity("block", self._owner_ids.get(entry.handle_id))
        if kind == "front":
            return ActivityIdentity("front")
        if kind == "endsim":
            return ActivityIdentity("endsim", name="EndSimulation")
        return ActivityIdentity("block", None)

    def step(self, end_time: float) -> bool:
        """Pop and process ONE calendar entry (if one is due at or before
        `end_time`); return whether an entry was processed. `run_until` is
        now just `while self.step(end_time): pass`. Enables
        interactive/live-sim drivers that want to observe model state
        between events.

        A same-time tie group is ordered by `(tier, owner-group calendar
        rank, seq)`: PoolObj-owned processes (`front_tier`) first, then the
        owner-group calendar, then insertion order. Untagged helper processes do not
        jump ahead of blocks (most are port structural artifacts with no
        source-side process behind them; giving them the non-block tier
        reorders kickoff). From `CallEndSimulation`'s selection to the end
        of that instant (`cn_active_time`), `choose_next` decides instead,
        as source's `SetTieBreaking(TRUE)` does."""
        _validate_time(end_time, "step end_time")
        if not self._calendar or self._calendar[0].time > end_time:
            return False
        tie_time = self._calendar[0].time
        group: list[_Entry] = []
        while self._calendar and self._calendar[0].time == tie_time:
            entry = heapq.heappop(self._calendar)
            if self._generations.get(entry.handle_id) != entry.generation:
                continue  # stale -- this wait was interrupt-ed away already
            group.append(entry)
        if not group:
            return True
        if self.cn_active_time == tie_time:
            # The rest of the `CallEndSimulation` instant: calendar order,
            # then the faithful ChooseNext port.
            group.sort(key=lambda e: (self._gc_rank(e), e.seq))
            chosen = choose_next(group, self._identity)
            entry = group.pop(next(i for i, e in enumerate(group) if e is chosen))
        else:
            tiers = _TIER_CN_OFF if self.cn_off_time == tie_time else _TIER
            group.sort(
                key=lambda e: (
                    tiers.get(self._owners.get(e.handle_id), 1),
                    self._gc_rank(e),
                    e.seq,
                )
            )
            entry = group.pop(0)
        self._gc_select(entry)
        for e in group:
            heapq.heappush(self._calendar, e)
        self.time = tie_time
        self._process(entry)
        return True

    def _process(self, entry: _Entry) -> None:
        """Resume the process behind `entry` once (see `step`'s docstring).
        `self.time` must already be set to the entry's instant."""
        if entry.handle_id not in self.passive_handles:
            self.last_active_time = self.time
        process = self._processes[entry.handle_id]
        self._unstarted.discard(entry.handle_id)
        resume = None if entry.interrupt_reason is None else Wake(entry.interrupt_reason, entry.token)
        self._push_exec(entry.handle_id)
        try:
            yielded = process.send(resume)
        except StopIteration:
            self._retire(entry.handle_id)
            return
        finally:
            # Exception-safe -- the execution frame must not stay set to a
            # dead/raising process and mislead interrupt senders.
            self._pop_exec(entry.handle_id)
        self._requeue(entry.handle_id, yielded, entry.generation)

    def _requeue(self, handle_id: int, yielded: float | Wait, generation: int) -> None:
        """Push a process's continuation for what it just yielded."""
        request = yielded if isinstance(yielded, Wait) else Wait(yielded)
        wait = request.duration
        # A NaN/negative wait would corrupt the calendar (NaN time
        # livelocks; negative walks time backwards). Reject at the source.
        if not (wait >= 0.0):
            raise ValueError(
                f"process handle {handle_id} yielded an invalid wait "
                f"{wait!r} at t={self.time!r}")
        if request.group_head:
            self._gc_head_handle = handle_id
        self._push(handle_id, self.time + wait, generation,
                   gc_owner=request.owner, gc_retail=request.retail,
                   group_head=request.group_head)
        if request.replaces is not None:
            # Retire the slot placeholder AFTER the requeue joined its group,
            # so the group keeps its calendar position.
            self.cancel(ProcessHandle(id=request.replaces))

    def cancel(self, handle: ProcessHandle) -> None:
        """Drop a pending process entirely (its calendar entry goes stale and
        its owner-group record is removed)."""
        if handle.id not in self._processes:
            return
        self._generations[handle.id] = self._generations.get(handle.id, 0) + 1
        self._gc_remove(handle.id)
        self._retire(handle.id)

    #: Optional per-event observer (GUI live view): called after each
    #: processed entry with the scheduler itself; the driver closes over
    #: whatever live model objects it wants to read.
    on_step: Callable[[Scheduler], None] | None = None

    def run_until(self, end_time: float) -> None:
        """Advance the simulation, resuming processes in time order, until
        no scheduled event remains at or before `end_time` — then sets
        `self.time = end_time` (matching the source's behavior of forcing
        `SimTime` to exactly the configured run length even if the last
        real event was earlier, see `block_lifecycle.py`'s docstring on
        `EndSimulation`'s forced termination)."""
        _validate_time(end_time, "run_until end_time")
        while True:
            if not self._calendar or self._calendar[0].time > end_time:
                break
            # Batch runs have no per-event observer: process the whole
            # same-instant group in one pass. The old loop re-popped and
            # re-sorted every remaining entry of the group once per entry
            # (O(k^2 log k)); on a 3,470-block model's ~6,900-entry t=0
            # group that was ~30 s per replication. None order changes.
            self._tie_in_flight = self._calendar[0].time
            try:
                fast = self._run_tie_group(self._tie_in_flight)
            finally:
                self._tie_in_flight = None
            if fast is not None:
                # False means the instant held only stale entries; they
                # are consumed, so continue with the next time.
                continue
            # ChooseNext/mixed-tier instant: the one-at-a-time path.
            if not self.step(end_time):
                break
            if self.block_hook is not None:
                # May block indefinitely (GUI Pause): sim time is frozen
                # because this loop simply doesn't advance while blocked.
                self.block_hook()
            if self.on_step is not None:
                self.on_step(self)
        self.time = end_time

    def _run_tie_group(self, tie_time: float) -> bool | None:  # noqa: C901, PLR0912, PLR0915
        """Process every valid entry at `tie_time` in the same order the
        one-at-a-time `step` path would, without the repeated full sort.

        Returns True when the instant is finished, False when nothing was
        left, or None when this instant needs the slow path (ChooseNext
        governs it, or an owner group mixes tiers). The caller then falls
        back to `step`, so the faithful tie rules stay in one place.
        """
        self._tie_pending = 0
        if self.cn_active_time == tie_time or self._fast_ties_disabled:
            return None
        group: list[_Entry] = []
        while self._calendar and self._calendar[0].time == tie_time:
            entry = heapq.heappop(self._calendar)
            if self._generations.get(entry.handle_id) == entry.generation:
                group.append(entry)
        if not group:
            return False
        tiers = _TIER_CN_OFF if self.cn_off_time == tie_time else _TIER

        # id(owner group) -> [owner group, tier, entries still pending]
        buckets: dict[int, list] = {}
        for e in group:
            live = self._gc_live.get(e.handle_id)
            if live is None or live[1] is not e:
                continue
            g = live[0]
            tier = tiers.get(self._owners.get(e.handle_id), 1)
            bucket = buckets.get(id(g))
            if bucket is None:
                buckets[id(g)] = [g, tier, [e]]
            elif bucket[1] != tier:
                # Same owner, different tiers: the global key
                # (tier, group rank, seq) interleaves them; let `step`
                # handle this rare instant exactly.
                for e2 in group:
                    heapq.heappush(self._calendar, e2)
                return None
            else:
                bucket[2].append(e)
        if not buckets:
            return False
        if any(len(bucket[2]) > 1 for bucket in buckets.values()):
            # One owner with several records at this instant: MODSIM's
            # interrupt rule (a second interrupt mutates the FIRST record's
            # reason and keeps its position) makes within-group order
            # dependent on the exact sequence of cascades. That is what
            # `step` implements; let it process this instant.
            for e2 in group:
                heapq.heappush(self._calendar, e2)
            return None
        self._tie_pending = sum(len(b[2]) for b in buckets.values())

        # Within an owner group the order is (position in g.recs, seq).
        heap: list[tuple[int, int, int]] = []
        for key, (g, tier, entries) in buckets.items():
            index = {id(r): i for i, r in enumerate(g.recs)}
            entries.sort(
                key=lambda e: (index.get(id(e), len(g.recs)), e.seq))
            heapq.heappush(heap, (tier, g.order, key))

        return self._run_tie_buckets(tie_time, buckets, heap, tiers)

    def _run_tie_buckets(  # noqa: C901, PLR0912, PLR0915
        self, tie_time: float, buckets: dict[int, list],
        heap: list[tuple[int, int, int]], tiers: dict,
    ) -> bool | None:
        """Drain one instant's `(tier, owner-group rank, seq)` buckets."""
        while heap:
            _tier, _order, key = heapq.heappop(heap)
            bucket = buckets.get(key)
            if bucket is None:
                continue
            g, g_tier, entries = bucket
            while entries:
                e = entries[0]
                if self._generations.get(e.handle_id) == e.generation:
                    break
                entries.pop(0)
            if not entries:
                buckets.pop(key, None)
                continue
            entry = entries.pop(0)
            self._gc_select(entry)
            self.time = tie_time
            self._process(entry)
            if self._tie_interrupted:
                # An interrupt landed during this turn. The remaining
                # records' order depends on MODSIM's "second interrupt
                # keeps the first record's position" rule, which `step`
                # implements; let it sort the rest of the instant.
                self._tie_interrupted = False
                for _k, (_g2, _t, es) in list(buckets.items()):
                    for e2 in es:
                        heapq.heappush(self._calendar, e2)
                buckets.clear()
                self._tie_pending = 0
                return None
            self._tie_pending = sum(len(b[2]) for b in buckets.values())
            if self.block_hook is not None:
                # GUI Pause / Stop / Skip between events, exactly as the
                # one-at-a-time loop does after `step`.
                self.block_hook()
            if self.on_step is not None:
                self.on_step(self)
            # `_process` may have queued more work at this same instant.
            while self._calendar and self._calendar[0].time == tie_time:
                new = heapq.heappop(self._calendar)
                if self._generations.get(new.handle_id) != new.generation:
                    continue
                live = self._gc_live.get(new.handle_id)
                if live is None or live[1] is not new:
                    continue
                new_g = live[0]
                new_tier = tiers.get(self._owners.get(new.handle_id), 1)
                new_key = id(new_g)
                new_bucket = buckets.get(new_key)
                if new_bucket is None:
                    new_bucket = [new_g, new_tier, []]
                    buckets[new_key] = new_bucket
                    heapq.heappush(heap, (new_tier, new_g.order, new_key))
                elif new_bucket[1] != new_tier:
                    # Rare mixed-tier instant: hand the rest to `step`.
                    heapq.heappush(self._calendar, new)
                    for _k, (_g2, _t, es) in list(buckets.items()):
                        for e2 in es:
                            heapq.heappush(self._calendar, e2)
                    return None
                if new.group_head:
                    new_bucket[2].insert(0, new)
                else:
                    new_bucket[2].append(new)
            if entries:
                heapq.heappush(heap, (g_tier, g.order, key))
            else:
                buckets.pop(key, None)
            self._tie_pending = sum(len(b[2]) for b in buckets.values())
        self._tie_pending = 0
        return True

    def interrupt(self, handle: ProcessHandle, reason: str, *,
                  token: object | None = None, gc_owner: Owner | None = None) -> None:
        """Queue an interrupt for `handle`'s process at the scheduler's
        current time, discarding whatever timed (or unbounded) wait it
        was suspended on — the Python analog of Modsim's `Interrupt(
        process, "Run")` (see `dependency.py`'s module docstring and
         around `Interrupt(tempBlock,"Run")`/`SetInterrupt
        Reason`). The process receives `Wake(reason, token)` as the
        resume value of its currently suspended `yield`, the next time
        `run_until`'s own popping loop reaches it, and must `yield` its
        own next wait duration in response, exactly like an ordinary
        timeout resume.

        Queued, not run immediately — a zero-delay calendar entry
        (`self.time + 0`), NOT a synchronous nested `process.send()`. This
        matters whenever `interrupt` is itself called from INSIDE some
        other process's own currently-running turn (e.g. `_Cascade.apply`'s
        `wake_ids` handling, fired mid-cascade out of a block's own
        repair-completion transition): the CALLER's own generator hasn't
        reached ITS next `yield` yet, so it has MORE of its own code left
        to run (its own next `WAIT`/RNG draw) before truly suspending —
        and that remaining code must run to completion FIRST. Queuing
        here lets the caller's generator continue uninterrupted; once it
        finally does yield, `run_until`'s heap naturally pops this
        zero-delay entry next (same timestamp, earlier insertion time
        than whatever the caller just pushed for its own future
        continuation), with NO special-casing needed for the "many
        zero-delay hops in a row" case either (a resumed process's own
        `WAIT DURATION 0.0`, `pool.py`'s module docstring, is just
        another ordinary zero-delay entry, handled by ordinary
        iteration). This also keeps a repair-completing block's OWN next
        fail-time draw consuming the shared RNG stream BEFORE a
        resource/spare-pool release it triggers resumes some OTHER waiting
        block.

        Only meaningful while called from within an active `run_until`
        step (uses `self.time` as the interrupt's simulated timestamp,
        matching that this only ever happens as a synchronous side effect
        of some OTHER process's own event, e.g. `BackFlowDependencies`
        firing out of a depended-upon block's own state change). A no-op
        if `handle`'s process has already run to completion.
"""
        if handle.id not in self._processes:
            return
        if self._tie_in_flight is not None:
            self._tie_interrupted = True
            self._fast_ties_disabled = True
        if handle.id in self._unstarted:
            self._start_unstarted(handle.id)
            if handle.id not in self._processes:
                return  # the body finished during its startup
        # MODSIM's Interrupt only acts on a record that is still waiting
        # (`0x5e13b0`: `cmpb $3,0xc(%edi)`). A second Interrupt of a record
        # already interrupted this instant is a no-op: the record keeps the
        # FIRST interrupt's calendar position, and only the object's
        # InterruptReason field (set by the caller) changes. gdb traces
        # `events35`/`events36` (`trace_interrupt.gdb`: a `K` with no `KR`).
        # Pool grants carry a token and are separate Give records, so
        # they are left out.
        live = self._gc_live.get(handle.id)
        if live is not None:
            e = live[1]
            if (e.interrupt_reason is not None and e.time == self.time
                    and e.generation == self._generations[handle.id]
                    and e.token is None and token is None
                    and e.interrupt_reason != "Pool_Available" and reason != "Pool_Available"):
                e.interrupt_reason = reason
                return
        if (live is not None and reason in ("Switched_On", "Switched_Off") and token is None):
            e = live[1]
            if (e.interrupt_reason == "Pool_Available" and e.time == self.time
                    and e.generation == self._generations[handle.id]):
                # A pool `Give` is a record of its own in MODSIM; a later cold-switch `Interrupt` of the block's
                # `Run` does not displace it. The single generator takes the grant at the grant's own place in
                # the FIFO and sees the switched state on its next pass (`LadL7CapMix` run 23 t=1989.192196).
                return
        generation = self._generations[handle.id] + 1
        self._generations[handle.id] = generation
        # Inherit the seq of this SAME handle's own FIRST calendar entry
        # pushed at this SAME instant (whether that first push was itself
        # an interrupt or an ordinary self-continuation) -- NOT its most
        # recently pushed one. A handle's whole causal "turn" this instant
        # (an interrupt, its own resulting zero-duration hops, a LATER re-
        # interrupt replacing part of that turn, ...) stays pinned at
        # wherever it was FIRST disturbed, relative to every OTHER handle's
        # own entries -- it doesn't advance to the back of the queue just
        # because it's been re-touched since. Deliberately NOT applied to
        # `run_until`'s own ordinary re-push after an ordinary resume (that
        # one always gets a genuinely fresh seq, `_push`'s default) -- only
        # an interrupt re-push reaches back for this handle's earlier
        # position. A handle with no earlier entry this instant has nothing
        # to inherit and simply gets a fresh, later seq.
        first = self._first_pushed.get(handle.id)
        seq = first[1] if first is not None and first[0] == self.time else None
        self._push(handle.id, self.time, generation, interrupt_reason=reason,
                   seq=seq, token=token, gc_owner=gc_owner)

    def pending_count(self) -> int:
        return len(self._calendar)

    def next_time(self) -> float | None:
        """Time of the calendar's head entry, or None when it is empty. The
        head can be a stale (interrupted) entry that `step` will skip, so this
        is a lower bound on the next instant, not necessarily a live event."""
        if self._tie_in_flight is not None and self._tie_pending > 0:
            if not self._calendar or self._calendar[0].time >= self._tie_in_flight:
                return self._tie_in_flight
        return self._calendar[0].time if self._calendar else None

"""One replication: build the run context and drive a run from kickoff to its report.

`build_engine_ctx` builds the per-batch `EngineCtx` (model, streams, stats
and sinks) once; `SingleRunDriver` runs one replication with it -- kickoff,
advance to the end, finalize -- over a fresh `orchestrator._Cascade`.
`runner.run_simulation` and `step_session.StepSession` drive it.

!! SURPRISING REAL-RAPTOR BEHAVIOUR: AN OUTPUT OPTION CHANGES THE RESULTS !!
------------------------------------------------------------------------------
In Raptor 7, ticking the **Interim Costs** output in the Simulation dialog
changes the simulated trajectory itself -- not just what gets written -- for
a phased CycleSim/FailSim model whose triggers share a system random stream
with the phase schedule (they do in every Phasing-family model: stream 101).
With the option off, the phase-duration draw happens first at t=0; with it
on, the trigger draws happen first, so every draw after that differs (every
run's first `Begin_phase` time changes, e.g. 4.940641 -> 4.980551).

Proven 2026-09-23 by a one-option-at-a-time experiment on one unchanged file
(`tests/golden/CycleSimStartStatsCapacityProbe/legacy/
output_option_experiment/`; Final Results, End-of-Run and capacity plot do
NOT flip it; Interim Costs does, deterministically), and confirmed for FailSim
on `PhasedFailSimOrderProbe`. Source explains it: kickoff queues `TELL System
TO CalculateInterimCost` (only when costAnalysis AND
the cost file is requested) ahead of `TELL System TO ControlPhaseChanges()`
 -- and TimeSim always queues `CallEndSimulation IN StopCriteria` there,
which is why every TimeSim model is always "triggers first". Why an
earlier-queued TELL reorders two LATER equal-time activities is not yet
understood (consistent with a non-stable heap calendar).

Consequences for this port (implemented at the kickoff's `_phase_first`):
- the order follows whether a cost sink (`_cost_sink` / `cost_sink=`) was
  requested -- the port's stand-in for the dialog's Interim Costs box;
- `raptor.api.run` (MCP `simulate`, the GUI) ALWAYS requests the cost
  stream, so its results correspond to Raptor 7 runs with Interim Costs
  ticked; the golden-test harness requests none;
- when comparing against a Raptor 7 capture, run the port with the same Interim
  Costs setting the capture used, or the two will disagree from t=0.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass

from raptor.core.engine import kickoff as kickoff_phases
from raptor.core.engine.blocks import BlockStatistics, change_block_state
from raptor.core.engine.capacity import CapacityState, build_capacity_run_report
from raptor.core.engine.costs import build_cost_run_report
from raptor.core.engine.errors import EngineInvariantError, StopRun, require
from raptor.core.engine.eventlog import EventLog, format_event_log_line
from raptor.core.engine.events import EventStatistics, change_event_state
from raptor.core.engine.load import load_model, resolve_defer_trigger
from raptor.core.engine.orchestrator import _Cascade
from raptor.core.engine.pool import PoolStats, ResourceQueue
from raptor.core.engine.report_format import ao_time_column, ao_value
from raptor.core.engine.reporting import StockSnapshot, build_run_report_data
from raptor.core.engine.system_status import MissionStatus, SystemColor, change_system_status
from raptor.core.fileio.rbd import RbdFile
from raptor.core.model import BlockStatus, EventStatus, RBDBlock
from raptor.core.rng import RandomGen, RandomSource, RaptorStream, fetch_raptor_seed
from raptor.core.scheduler import ProcessHandle, Scheduler


def make_shared_streams(sys_streams_config: list[int],
                        seed: int = 0) -> dict[int, RandomSource]:
    """Raptor 7 -- the 11 streams shared by every block or
    event whose `failStream`/`repairStream` is `> 200` (`GetStream`'s
    shared branch). Built once per `run_simulation`
    call and persisted across runs.

    `seed` (default 0) salts every base seed, so a caller-supplied seed
    re-rolls the whole batch reproducibly; 0 leaves Raptor 7
    seed table untouched."""
    return {
        i + 1: RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(abs(v)) + seed, antithetic=v < 0))
        for i, v in enumerate(sys_streams_config)
    }


def make_rng(stream_num: int, sys_streams: dict[int, RandomSource],
             seed: int = 0) -> RandomSource:
    """`GetStream`'s dispatch: a stream number
    `> 200` selects a shared `SysStream[stream_num-200]` (one generator that
    multiple blocks/events draw from, interleaved by simulation event
    order); anything else is a dedicated stream for this object.

    `seed` salts this stream's base seed (see `make_shared_streams`)."""
    if stream_num > 200:
        index = stream_num - 200
        if index not in sys_streams:
            raise ValueError(f"stream {stream_num} references SysStream[{index}], out of range 1..{len(sys_streams)}")
        return sys_streams[index]
    return RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(abs(stream_num)) + seed, antithetic=stream_num < 0))


class EngineCtx:
    """Cross-run-persistent engine objects, shared by every per-run
    `SingleRunDriver`."""

    def __init__(self, *, model, rbd_file, on_event, sim_details_recorder,
                 current_scheduler, sys_streams, rng_fail_by_block,
                 rng_repair_by_block, rng_by_event, stats_by_block,
                 stats_by_event, end_node, original_good_paths, ao_sink,
                 cost_sink, ao_plot_interval, cost_plot_interval,
                 report_sink, runs_total, draw_tag=None):
        self.model = model
        self.rbd_file = rbd_file
        self.on_event = on_event
        self.sim_details_recorder = sim_details_recorder
        #: {{"name": <phase name>}} while a phase duration is being drawn
        #: from stream 11 -- consumed by that stream's NamedRandomGen name
        #: callback and by `set_draw_tag`.
        self.draw_tag: dict[str, str] = draw_tag if draw_tag is not None else {}
        self.current_scheduler = current_scheduler
        self.sys_streams = sys_streams
        self.rng_fail_by_block = rng_fail_by_block
        self.rng_repair_by_block = rng_repair_by_block
        self.rng_by_event = rng_by_event
        self.stats_by_block = stats_by_block
        self.stats_by_event = stats_by_event
        self.end_node = end_node
        self.original_good_paths = original_good_paths
        self.ao_sink = ao_sink
        self.cost_sink = cost_sink
        self.ao_plot_interval = ao_plot_interval
        self.cost_plot_interval = cost_plot_interval
        self.report_sink = report_sink
        self.runs_total = runs_total
        #: The `/C130` mission file's rows, or None when off.
        self.mission_lines: list[str] | None = None
        #: The "Cumulative By Time" file's collector (`cumulative.CumulativeByTime`), or None.
        self.cum_time = None


def count_stream_draws(ctx: EngineCtx) -> list:
    """Swap every distinct underlying stream of `ctx` for a `CountingStream`
    (same state) and return them. Multi-worker chunks call this so the parent
    can check each stayed inside its segment; a wrapped stream (the `/sd`
    recorder) is left alone."""
    
    # `RunStreams.distinct_streams()` is the ONE enumeration (review E-02).
    # This used to re-list the same four collections inline, making it the
    # FOURTH place a new stream category had to be remembered: construction
    # (twice), `stream_jump`, the `/sd` wrapping, and this counter. Miss this
    # one and a chunk's draw budget stops covering the new stream.
    # Non-`RandomGen` entries (the `/sd` recorder's wrappers) are FILTERED
    # OUT, not skipped-with-a-None: the caller receives only the streams it
    # must check, and `[]` is the right answer for a fully-wrapped context.
    return [w for w in (swap_in_counting(gen)
                        for gen in _streams_of(ctx).distinct_streams())
            if w is not None]


def _streams_of(ctx: EngineCtx) -> RunStreams:
    """The `RunStreams` view of an `EngineCtx`.

    `EngineCtx` stores the six collections as loose attributes because that is
    its public shape, but everything downstream wants them as one bundle.
    This is the adapter rather than a rebuild -- no new dicts, no re-seeding,
    just a view -- so wrapping or counting through it mutates `ctx`'s own
    objects.

    `stats_by_block`/`stats_by_event` are read with `getattr` defaults: the
    two stats collections are not RNGs, so no consumer of this view touches
    them, and a caller that only wants the streams (as `count_stream_draws`
    does) should not be forced to supply them.
    """
    return RunStreams(
        sys_streams=ctx.sys_streams,
        rng_fail_by_block=ctx.rng_fail_by_block,
        rng_repair_by_block=ctx.rng_repair_by_block,
        rng_by_event=ctx.rng_by_event,
        stats_by_block=getattr(ctx, "stats_by_block", {}),
        stats_by_event=getattr(ctx, "stats_by_event", {}),
    )


def swap_in_counting(gen):
    """Swap one generator's stream for a `CountingStream` over the same
    state, returning the wrapper, or `None` for an already-wrapped stream.

    Callers must pass each generator at most once; `distinct_streams()`
    already de-duplicates by identity, because `make_rng` hands the *same*
    `RandomGen` to every consumer of a shared stream and counting one twice
    would replace its stream with two wrappers over the same state.
    """
    from raptor.core.rng import CountingStream

    if not isinstance(gen, RandomGen):
        return None       # already wrapped (the `/sd` recorder)
    wrapper = CountingStream(gen.stream._x, gen.stream._antithetic)
    gen.stream = wrapper
    return wrapper


#: Anti-runaway horizon for FailSim/CycleSim runs, whose termination is a count rather than a time.
#: `RAPTOR_SAFETY_BOUND` overrides it for test harnesses: a parallel batch's worker processes inherit
#: the environment, not a monkeypatched module attribute.
SAFETY_BOUND = float(os.environ.get("RAPTOR_SAFETY_BOUND", "1.0e7"))


class SingleRunDriver:
    """Exactly one replication of `run_simulation`'s former per-run loop
    body, split into three phases so an interactive driver (GUI step-through)
    can observe state between events:

      kickoff()       -- in source's kickoff order: `_reset_run`,
                         `_initialize_blocks_and_events`, `_build_pools`,
                         `_start_processes`, `_start_triggers`,
                         `_start_system_processes`
      advance_to_end()/step_once()
                      -- the former `run_until` (+ `StopRun` catch);
                         `step_once` processes one calendar entry instead
      finalize()      -- the former closing passes (End_sim lines, system
                         DDone flush, terminate marker, Ao/cost separators,
                         RunReportData construction)

    Attribute writes use `self.x = x = ...` so the moved body keeps its
    original bare-name style; later phases re-alias from `self`.
    """

    def __init__(self, ctx: EngineCtx, run_index: int):
        self.ctx = ctx
        # Defaults so a driver whose `kickoff()` never ran (or a
        # finalize-only path) still reports empty stock tables rather than
        # raising.
        self._custom_spares_registry: dict[int, ResourceQueue] = {}
        self._resource_queues: dict[str, ResourceQueue] = {}
        self._spare_queues: dict[str, ResourceQueue] = {}
        self.run_index = run_index
        #: This run's event log; subscribe with `self.logs.subscribe(...)`
        #: before `kickoff()` to follow the run line by line.
        self.logs: list[str] = EventLog()
        self._scheduler: Scheduler | None = None
        self._cascade: _Cascade | None = None
        self.capacity_state: CapacityState | None = None
        self.run_pool_stats: dict[str, PoolStats] = {}
        self.all_blocks_list: list[RBDBlock] = []
        self.pre_force_sim_time = 0.0
        self.stop_requested = False
        self.finalized = False
        #: True when a FailSim/CycleSim run exhausted the anti-runaway bound
        #: without meeting its termination condition (see `advance_to_end`).
        self.hit_safety_bound = False
        #: Per-trigger waiter list: the order the subscribers' `ControlPMtriggers`
        #: processes entered `WAIT FOR myTrig TO Fire` (`_make_pm_on_wait`).
        #: Each fire releases the list in order and empties it; each released
        #: process re-joins at the back once it has handled the fire.
        self._pm_waiters: dict[str, list[int]] = {}
        self._pm_sync_waiters: list[int] = []
        #: One `ControlPMtriggers` process per subscribing block, handle kept
        #: so the trigger fire can release them.
        self._pm_trigger_handles: dict[int, ProcessHandle] = {}

    @property
    def started(self) -> bool:
        """True once `kickoff()` has built this run's scheduler and cascade."""
        return self._scheduler is not None and self._cascade is not None

    @property
    def scheduler(self) -> Scheduler:
        """This run's scheduler; available from `kickoff()` on."""
        return require(self._scheduler, "the run's scheduler (call kickoff() first)")

    @property
    def cascade(self) -> _Cascade:
        """This run's cascade; available from `kickoff()` on."""
        return require(self._cascade, "the run's cascade (call kickoff() first)")

    def _set_draw_tag(self, name: str | None) -> None:
        """`/sd` call-site name for the shared stream 11's next draw.

        Raptor 7's `gettingDraw` is an argument of the `DrawNumber` call, so it
        names the call site, not the stream. `ControlPhaseChanges` sets it
        around its own phase-duration draw and nothing else on stream 11
        inherits it -- hence the `None` (clear) side: without it the last
        phase's name would leak onto later stream-11 draws from other
        callers."""
        if name is None:
            self.ctx.draw_tag.pop("name", None)
        else:
            self.ctx.draw_tag["name"] = name

    def kickoff(self):
        """Set up this run and schedule everything that starts at t=0,
        without advancing the clock: source's kickoff sequence,
       in its order."""
        self._reset_run()
        start_cond_by_block, initial_fail_time_left_by_block = self._initialize_blocks_and_events()
        self._build_pools()

        self._start_processes(start_cond_by_block, initial_fail_time_left_by_block)

        _phase_first = self._start_triggers()

        self._start_system_processes(_phase_first)

    # Kickoff phases and the callbacks they hand out live in kickoff.py as
    # functions taking the driver as `self`, bound here as methods.
    _reset_run = kickoff_phases._reset_run
    _initialize_blocks_and_events = kickoff_phases._initialize_blocks_and_events
    _build_pools = kickoff_phases._build_pools
    _start_processes = kickoff_phases._start_processes
    _start_triggers = kickoff_phases._start_triggers
    _start_system_processes = kickoff_phases._start_system_processes
    _make_pm_trigger_eligible = kickoff_phases._make_pm_trigger_eligible
    _make_pm_phase_defer_wait = kickoff_phases._make_pm_phase_defer_wait
    _make_pm_sync_wait = kickoff_phases._make_pm_sync_wait
    _make_pm_on_wait = kickoff_phases._make_pm_on_wait
    _make_pm_trigger_wake = kickoff_phases._make_pm_trigger_wake
    _new_queue = kickoff_phases._new_queue
    _reset_all_statistics = kickoff_phases._reset_all_statistics
    _wake_block = kickoff_phases._wake_block
    _make_spawn_order_a_spare = kickoff_phases._make_spawn_order_a_spare
    _make_spawn_order_stock = kickoff_phases._make_spawn_order_stock

        # Stop strictly before run_length, not at-or-before: `BlockProcess`
        # self-schedules a Done/End_sim
        # wake-up landing exactly at run_length. With N blocks sharing one
        # scheduler those wake-ups tie at the same timestamp, and the
        # scheduler's insertion-sequence tie-break does not match real here --
        # real finalizes still-Running blocks in one fixed end-of-run pass
        # ordered by block creation ID. Halting just short of run_length
        # prevents any of those self-terminations from firing, so the closing
        # loop below (already in blocks_by_id order) is the only place that
        # logs them. `pre_force_sim_time` is captured before `run_until`
        # forces the clock forward, for the report's EndingSimTime on a model
        # with nothing scheduled.

    def advance_to_end(self):
        """Former `try: run_until(...)/except StopRun: pass` block.

        Sets `self.hit_safety_bound` when a FailSim/CycleSim run ends on the
        anti-runaway bound instead of its termination condition, so the GUI
        can surface it instead of looking hung."""
        model = self.ctx.model
        scheduler = self.scheduler

        self.pre_force_sim_time = _pre_force_sim_time = scheduler.time
        self.hit_safety_bound = False
        try:
            if model.termination_type in ("FailSim", "CycleSim"):
                # termType=2/3: TermCondition is a count, not a time -- there
                # is no scheduled end; the run ends
                # only when the termination callback raises StopRun. The huge
                # bound is an anti-runaway guard, not semantics.
                scheduler.run_until(SAFETY_BOUND)
                # Normal exit means the condition was never met. It is the bound only if events are still
                # waiting beyond it; a calendar that simply ran dry (`Events`: nothing is ever scheduled, the
                # run ends at t = 0) is a natural end, not a runaway. `pre_force_sim_time` stays at its
                # natural capture above, since reports need the clock's resting point from real events, not
                # the forced bound.
                self.hit_safety_bound = scheduler.next_time() is not None
            else:
                # Inclusive boundary: the scheduled `_end_sim_process` entry
                # at run_length must pop -- Raptor 7 fires same-instant events
                # there before terminating.
                scheduler.run_until(model.run_length)
        except StopRun:
            pass

        # FailSim early end: EndSimulation already logged every block's
        # Done/End_sim, reset the nodes, and armed every event -- the ordinary
        # closing passes below must not repeat any of it.

    def step_once(self) -> bool:
        """Process exactly one calendar entry (GUI Step button); False when
        nothing is scheduled within the run's horizon. A `StopRun` raised
        mid-event marks the run stopped and reports True."""
        if self.stop_requested or self.finalized:
            return False
        model = self.ctx.model
        horizon = (
            1.0e7 if model.termination_type in ("FailSim", "CycleSim")
            else model.run_length)
        try:
            advanced = self.scheduler.step(horizon)
        except StopRun:
            self.stop_requested = True
            return True
        if advanced and self.scheduler.on_step is not None:
            self.scheduler.on_step(self.scheduler)
        return advanced

    def finalize(self, terminate_at: float | None = None):
        """Closing passes; returns this run's `RunReportData` (or None).
        `self.logs` holds the finalized event-log lines.

        `terminate_at`: Early Terminate (manual B-11) -- finalize at the
        current sim time instead of run_length, so partial-run results are
        usable."""
        model = self.ctx.model
        logs = self.logs
        _scheduler = self.scheduler
        cascade = self.cascade
        capacity_state = self.capacity_state
        run_pool_stats = self.run_pool_stats
        pre_force_sim_time = self.pre_force_sim_time
        _ao_sink = self.ctx.ao_sink
        _cost_sink = self.ctx.cost_sink
        all_blocks_list = self.all_blocks_list
        # Early Terminate (manual B-11): close the run at the current sim
        # time instead of run_length.
        end_len = (model.run_length if terminate_at is None
                   else float(terminate_at))
        stats_by_block = self.ctx.stats_by_block
        stats_by_event = self.ctx.stats_by_event

        ended_early = cascade.end_sim_time is not None

        # Unconditional, not `if block.op_status != DONE`: Raptor 7 logs a final
        # Done/End_sim line for every block at run_length, even one that
        # already reached terminal Done earlier via its own event.
        if not ended_early:
            for block in model.blocks_by_id.values():
                stats = stats_by_block[block.id]
                change_logs, *_ = change_block_state(
                    block, stats, BlockStatus.DONE, block.active_status, "End_sim",
                    end_len, events_file=True,
                    cost_analysis=model.cost_analysis, weak_analysis=model.weak_analysis,
                    pool_stats=run_pool_stats.get(block.resource_pool_name),
                    run_completed=True,
                )
                logs.extend(change_logs)

        # Port of `EndSimulation`'s event-closing half
        #  reached via ordinary run-length completion, not just the early
        # `CallEndSimulation` path. Unconditional, in `model.events_by_id`
        # order, after every block's own closing Done/End_sim line.
        #
        # Gated on `model.blocks_by_id` being non-empty: an events-only model
        # never reaches this closing pass in source, since nothing keeps the
        # run alive until `run_length` the way a block's own wait does.
        if model.blocks_by_id and not ended_early:
            for event in model.events_by_id.values():
                stats = stats_by_event[event.id]
                end_logs, *_ = change_event_state(
                    event, stats, EventStatus.ARMED, event.active_status,
                    "End_sim", end_len, weak_analysis=model.weak_analysis, events_file=True,
                )
                logs.extend(end_logs)

        # Port of `ASK System TO ChangeStatus(DDone,NonMission)`
        #: flushes the last color segment's duration into
        # the monitors (report bookkeeping only, no State_change line) and
        # completes the run's mission, producing the real
        # `Mission_success`/`Mission_failure`/`Finished_a_mission` line
        # whenever a mission was in progress. Always runs, since it has a real
        # main-event-log side effect.
        final_sim_time = (
            cascade.end_sim_time if cascade.end_sim_time is not None
            else (end_len if all_blocks_list else pre_force_sim_time)
        )
        logs.extend(change_system_status(
            cascade.system_state, SystemColor.DONE, final_sim_time, all_blocks_list,
            events_file=True, new_mission_status=MissionStatus.NON_MISSION,
            system_red_cost=model.system_red_cost if model.cost_analysis else 0.0,
            # Weak-link per-element mission credit:
            # this run-end close is the mission end for a non-phased model
            # (source's `ChangeStatus(DDone, NonMission)`).
            block_stats=cascade.stats_by_block if model.weak_analysis else None,
            node_stats=cascade.node_stats if model.weak_analysis else None,
            nodes=list(model.nodes_by_id.values()) if model.weak_analysis else None,
            event_stats=cascade.stats_by_event if model.weak_analysis else None,
            events=list(model.events_by_id.values()) if model.weak_analysis else None,
        ))
        if self.ctx.mission_lines is not None:
            self.ctx.mission_lines.append("-1 -1 -1")   # `EndOfRun`, Raptor 7
        # Port of the Raptor 7 engine's own unconditional run-end marker --
        # `"System"/"Simulation_Terminate"/"--"/"--"`, always the last line of
        # a run.
        logs.append(format_event_log_line(final_sim_time, "System", "Simulation_Terminate", "--", "--"))

        # Ao plot run-end row: Availability plus the
        # three cumulative percents -- four numeric columns, no Interval
        # column (unlike the interval process's five).
        if _ao_sink is not None:
            st = cascade.system_state
            g, y, r = st.green_stat.sum, st.yellow_stat.sum, st.red_stat.sum
            total = g + y + r
            ao_v = (g + y) / total if total > 0 else 1.0
            _ao_sink.append(
                ao_time_column(final_sim_time)
                + f"{ao_value(ao_v)}  {ao_value(g / total)}  {ao_value(y / total)}  {ao_value(r / total)}"
                if total > 0 else
                ao_time_column(final_sim_time) + "1.000000  " * 3 + "1.000000"
            )
            # -- per-run separator.
            _ao_sink.append("")
            _ao_sink.append("*" * 30 + f"End_of_Run_{self.run_index + 1}" + "*" * 30)
            _ao_sink.append("")

        # The interim cost report tick is handled by its own scheduled
        # process; here only the per-run cost-file separator is appended.
        if _cost_sink is not None and model.cost_analysis:
            _cost_sink.append("")
            _cost_sink.append("*" * 40 + f"End_of_Run_{self.run_index + 1}" + "*" * 40)
            _cost_sink.append("")

        _report_sink = self.ctx.report_sink
        report_row = None
        if _report_sink is not None:
            capacity_report = None
            end_t = end_len if all_blocks_list else pre_force_sim_time
            # Hoisted above the capacity/cost reports, which need it too.
            effective_end = (
                cascade.end_sim_time if cascade.end_sim_time is not None
                else (end_len if all_blocks_list else pre_force_sim_time)
            )
            if capacity_state is not None:
                # DoEndOfRunUpdate's per-run stat extraction
                # reads the timed monitors at the
                # final sim time, which is `effective_end`, the run's actual
                # elapsed time -- not `end_t`, which falls back to
                # `model.run_length`. For a CycleSim or FailSim model
                # `run_length` carries `TermCondition`, a count, and feeding it
                # to a time-weighted mean divides the accumulated area by the
                # wrong denominator. A CycleSim with
                # TermCondition=100 whose runs span ~2398.85 time units is the
                # case that exposes it.
                capacity_report = build_capacity_run_report(
                    capacity_state, model.cap_node_group, effective_end,
                    model.flow_generated, model.stats_start_time,
                )
            cost_report = None
            if model.cost_analysis:
                cost_report = build_cost_run_report(
                    model, stats_by_block, stats_by_event, run_pool_stats,
                    cascade.system_state, capacity_report, effective_end,
                )
            weak_snapshot = None
            weak_nodes = None
            weak_events = None
            weak_hiers = None
            if model.weak_analysis:
                # Per-block state times for the manual's weak-link tables.
                weak_snapshot = {}
                for b in all_blocks_list:
                    bs = stats_by_block[b.id]
                    weak_snapshot[b.id] = {
                        "name": b.name,
                        "run": bs.run_time,
                        "repair": bs.repair_time,
                        "rep_hold": bs.rep_hold_time,
                        "pm": bs.pm_time,
                        "pm_hold": bs.pm_hold_time,
                        "idle": bs.idle_time,
                        "standby": bs.standby_time,
                        "done": bs.done_time,
                # Per-node G/Y/R/B/Brown/Orange times (nodes.py's own weak
                # accumulators; `cascade.node_stats` persists per run).
                        "m_completed": bs.m_completed,
                    }
                # Per-node G/Y/R/B/Brown/Orange times (nodes.py's own
                # weak accumulators; `cascade.node_stats` persists per
                # run).
                weak_nodes = {}
                for nid, ns in cascade.node_stats.items():
                    node = self.ctx.model.nodes_by_id.get(nid)
                    if node is None or getattr(node, "type_node", 0) \
                            in (4, 5):
                        continue
                    weak_nodes[nid] = {
                        "name": node.report_name,
                        "green": ns.green_time,
                        "yellow": ns.yellow_time,
                        "red": ns.red_time,
                        "blue": ns.blue_time,
                        "m_completed": ns.m_completed,
                        "type_node": getattr(node, "type_node", 0),
                        "report_anal": bool(getattr(node, "report_node_anal", False)),
                        "brown": ns.brown_time,
                        "orange": ns.orange_time,
                    }
                # EVENT_RESULTS raw material --
                # event state times.
                weak_events = {}
                for e in model.events_by_id.values():
                    es = stats_by_event[e.id]
                    weak_events[e.id] = {
                        "name": e.name, "run": es.run_time,
                        "standby": es.standby_time, "repair": es.repair_time,
                        "m_completed": es.m_completed,
                    }
                # HIERARCHY_RESULTS raw material -- each
                # hierarchy's OUT node state times plus its `hierDE` count.
                weak_hiers = {}
                for hid, h in model.hiers_by_id.items():
                    ons = cascade.node_stats.get(h.out_id)
                    if ons is None:
                        continue
                    weak_hiers[hid] = {
                        "name": h.name, "out_node_id": h.out_id,
                        "green": ons.green_time, "yellow": ons.yellow_time,
                        "red": ons.red_time, "blue": ons.blue_time,
                        "brown": ons.brown_time, "orange": ons.orange_time,
                        "down_event_count": h.down_event_count, "m_completed": ons.m_completed,
                    }
            report_row = build_run_report_data(
                self.run_index + 1, end_len, effective_end,
                cascade.system_state, all_blocks_list,
                has_phases=bool(model.phases), capacity=capacity_report,
                cost=cost_report, weak_stats=weak_snapshot,
                weak_nodes=weak_nodes,
                weak_events=weak_events, weak_hiers=weak_hiers,
                hier_dsi=_hier_dsi_rollup(self.ctx.model, end_t),
                termination_type=self.ctx.model.termination_type,
                auto_stopped=cascade.auto_stopped,
                out_of_spares=cascade.out_of_spares,
                # Stock tables. The time-weighted
                # mean's denominator is `effective_end`, the run's actual
                # elapsed sim time -- source reads the mean off a monitor
                # started at the run's t=0, so a run that stops early
                # integrates over less than `model.run_length`.
                missions_attempted=cascade.system_state.missions_attempted,
                # Port of `EndSimulation`'s own `cyclesCount:=cyclesCount+
                # (FLOAT(currentPhase)/FLOAT(activePhases))`
                #  -- the run's last, part-finished phase cycle, added on
                # top of the whole ones `ControlPhaseChanges` already counted.
                # Computed here rather than mutated onto `system_state` so
                # re-reading a finished run's state stays idempotent.
                cycles_completed=(
                    cascade.system_state.cycles_count
                    + cascade.system_state.current_phase / len(model.phases)
                    if model.phases else None),
                # Keyed by block ID, not name: a model may carry two blocks
                # with the same name and Raptor 7 prints a row for each.
                custom_stock={
                    bid: _stock_snapshot(model.blocks_by_id[bid].name, q, effective_end)
                    for bid, q in sorted(
                        self._custom_spares_registry.items(),
                        key=lambda kv: model.blocks_by_id[kv[0]].name.lower())
                },
                spare_pool_stock={
                    name: _stock_snapshot(name, q, effective_end)
                    for name, q in self._spare_queues.items()
                },
                resource_stock={
                    name: _stock_snapshot(name, q, effective_end)
                    for name, q in self._resource_queues.items()
                },
            )
            _report_sink.append(report_row)
        self.finalized = True
        return report_row


def _stock_snapshot(name: str, q: ResourceQueue, run_length: float) -> StockSnapshot:
    """One `StockSnapshot` from a live `ResourceQueue` -- source's own
    run-end sampling of its monitors into the `...NumSpares` arrays
    (for Custom for pools)."""
    lo, hi = q.stock_extremes(run_length)
    return StockSnapshot(
        name=name,
        init=q.init_stock,
        arrivals=q.arrivals,
        minimum=lo,
        mean=q.mean_stock(run_length),
        maximum=hi,
        ending=q.resources,
        waits=q.waits,
    )


def _hier_dsi_rollup(model, end_t: float) -> dict[int, dict]:
    """End-of-run Descendant Status Indicator per hierarchy (manual
    Raptor 7). For each hierarchy, look at the REGULAR + End nodes whose
    parent chain lands inside it and aggregate their final `status`:
    any Down -> Down; else any Degraded/PM/Standby/Idle -> Degraded;
    else AllUp. The legacy DSI counts the same colour classes live via
    ChangeNodeState's descendant rollup (not ported); this is the
    end-of-run equivalent, computed from state the engine already
    maintains on `node.status`."""
    nodes = model.nodes_by_id
    hiers = model.hiers_by_id

    # parent-of map over ELEMENT levels (blocks/events/hiers use
    # ParentId; nodes use `parent`). A node belongs to hier H when H
    # appears in its ancestor chain.
    parent_of: dict[tuple[str, int], int] = {}
    for hid, h in hiers.items():
        parent_of[("H", hid)] = h.parent_id or 0
    for bid, b in model.blocks_by_id.items():
        parent_of[("B", bid)] = b.parent_id or 0
    for nid, n in nodes.items():
        parent_of[("N", nid)] = getattr(n, "parent_id", 0) or 0

    def ancestors(kind: str, oid: int):
        cur = (kind, oid)
        seen = set()
        while cur in parent_of:
            cur = ("H", parent_of[cur])
            if cur in seen:
                break
            seen.add(cur)
            yield cur[1]

    membership: dict[int, set[int]] = {hid: set() for hid in hiers}
    for nid, n in nodes.items():
        if getattr(n, "type_node", 0) in (4, 5):
            continue  # markers are boundaries, not descendants
        for hid in ancestors("N", nid):
            if hid in membership:
                membership[hid].add(nid)

    def classify(status) -> str:
        name = getattr(status, "name", "")
        return {"DOWN": "Down", "DEGRADED": "Degraded",
                "PM": "Degraded"}.get(name, "")

    result: dict[int, dict] = {}
    for hid, nids in membership.items():
        down = sum(1 for nid in nids
                   if getattr(nodes[nid].status, "name", "") == "DOWN")
        degraded_count = sum(1 for nid in nids if classify(nodes[nid].status))
        state = ("Down" if down else
                 "Degraded" if degraded_count else "AllUp")
        result[hid] = {
            "state": state,
            "nodes": len(nids),
            "down": down,
            "degraded": degraded_count,
        }
    return result


@dataclass(frozen=True)
class RunStreams:
    """The RNG streams and stats objects one engine batch runs on.

    Built in one place (review E-02). This construction was written out
    **twice** — here and inline in `runner.run_simulation` — six statements
    apart, and the six enumerate **the set of streams in the model**:

        sys_streams, rng_fail_by_block, rng_repair_by_block,
        rng_by_event, stats_by_block, stats_by_event

    That set is the fragile part, not the statements. `stream_jump` exists
    precisely because a multi-worker chunk must advance *every distinct
    stream* to its own disjoint segment, and it does so by walking the same
    six collections (`build_engine_ctx`, below). The `/sd` `NamedRandomGen`
    wrapping walks them a third time (`runner.py`), and `jump_ahead` a
    fourth. **Add one stream category and there are four places to remember** —
    miss the `jump_ahead` one and multi-worker chunks silently share stream
    segments; miss the `/sd` one and the segment check stops covering it.

    So the *enumeration* is what belongs here, not just the six statements:
    `distinct_streams()` is the single list every consumer walks.

    Frozen because these are shared across every run in a batch and across
    the `/sd` wrappers; nothing may rebind a collection after construction.
    """

    sys_streams: dict
    rng_fail_by_block: dict
    rng_repair_by_block: dict
    rng_by_event: dict
    stats_by_block: dict
    stats_by_event: dict

    def distinct_streams(self) -> list:
        """Every RNG reachable from this bundle, **de-duplicated by identity**.

        `make_rng` returns the *same* `RandomGen` object for every consumer of
        a shared stream, so a block's failure and repair generators can be one
        object. Jumping or counting such a generator twice would advance it
        twice — which is why this de-duplicates rather than concatenating the
        collections and hoping.
        """
        seen: dict[int, object] = {}
        for gen in self.sys_streams.values():
            seen[id(gen)] = gen
        for coll in (self.rng_fail_by_block, self.rng_repair_by_block,
                     self.rng_by_event):
            for gen in coll.values():
                seen[id(gen)] = gen
        return list(seen.values())

    def wrap_per_element(self, wrap, name_of) -> None:
        """Wrap every per-element generator, **rebinding in place**.

        This is the third consumer of the stream set (with `stream_jump` and
        `count_stream_draws`), and it existed as two hand-written loops over
        `model.blocks_by_id` / `model.events_by_id` that read each collection
        by key. A new per-element collection would therefore have been
        silently left unwrapped -- and so unreported in the `/sd` capture --
        while still being jumped. Naming the set once is what keeps those
        consumers in step.

        `wrap(gen, name) -> wrapped` and `name_of(element_id) -> str` are
        supplied by the caller because only the `/sd` path wraps: a
        sequential run with no `simDetails` capture must leave the raw
        `RandomGen` objects alone.

        Rebinding is in place rather than returning new collections because
        `run_simulation` has already bound the dicts into locals, the
        scheduler processes, and `EngineCtx`.
        """
        for eid, gen in self.rng_fail_by_block.items():
            self.rng_fail_by_block[eid] = wrap(gen, name_of(eid))
        for eid, gen in self.rng_repair_by_block.items():
            self.rng_repair_by_block[eid] = wrap(gen, name_of(eid))
        for eid, gen in self.rng_by_event.items():
            self.rng_by_event[eid] = wrap(gen, name_of(eid))


def build_run_streams(model, *, stream_seed: int = 0) -> RunStreams:
    """Construct the batch's streams and stats objects. The one owner.

    `stream_seed` salts every base seed (`0` = Raptor 7 seeds); it
    is how a user-supplied `seed` re-rolls a batch reproducibly.
    """
    sys_streams = make_shared_streams(model.sys_streams, seed=stream_seed)
    return RunStreams(
        sys_streams=sys_streams,
        rng_fail_by_block={
            b.id: make_rng(b.fail_stream, sys_streams, seed=stream_seed)
            for b in model.blocks_by_id.values()},
        rng_repair_by_block={
            b.id: make_rng(b.repair_stream, sys_streams, seed=stream_seed)
            for b in model.blocks_by_id.values()},
        stats_by_block={b.id: BlockStatistics()
                        for b in model.blocks_by_id.values()},
        rng_by_event={e.id: make_rng(e.fail_stream, sys_streams, seed=stream_seed)
                      for e in model.events_by_id.values()},
        stats_by_event={e.id: EventStatistics()
                        for e in model.events_by_id.values()},
    )


def _model_from(rbd_file: RbdFile, defer_trigger: str, template):
    """A fresh model: a copy of `template`, else a load of `rbd_file`."""
    if template is not None:
        try:
            return copy.deepcopy(template)
        except RecursionError:      # a very deep model: load it the long way
            pass
    return load_model(rbd_file, defer_trigger=resolve_defer_trigger(rbd_file, defer_trigger))


def build_engine_ctx(rbd_file: RbdFile, *, num_runs: int | None = None,
                      on_event=None, report_sink=None, ao_sink=None,
                      ao_plot_interval: float = 10.0, cost_sink=None,
                      cost_plot_interval: float = 720.0,
                      stream_jump: int = 0, stream_seed: int = 0, defer_trigger: str = "",
                      model_template=None):
    """Shared engine-state construction for `run_simulation` and the GUI's
    `StepSession` (same statements, same order, as run_simulation's former
    inline setup). Returns `(model, ctx)`.

    `stream_seed` salts every base seed (0 = Raptor 7 seeds); it is
    how a user-supplied `seed` re-rolls a batch reproducibly.

    `model_template` is a model that `load_model` returned and nothing has
    run: a copy of it stands in for loading the file again, which on a large
    model is most of the cost of building a context. The caller must never
    run the template itself, or the copies would start from its state."""
    model = _model_from(rbd_file, defer_trigger, model_template)
    runs = model.num_runs if num_runs is None else num_runs
    streams = build_run_streams(model, stream_seed=stream_seed)
    sys_streams = streams.sys_streams
    rng_fail_by_block = streams.rng_fail_by_block
    rng_repair_by_block = streams.rng_repair_by_block
    stats_by_block = streams.stats_by_block
    rng_by_event = streams.rng_by_event
    stats_by_event = streams.stats_by_event
    if stream_jump:
        # Multi-worker mode: give this worker a disjoint segment of every
        # distinct stream (`rng.jump_ahead` explains why segments are safe).
        # Sequential runs keep jump=0 and full draw-for-draw compatibility.
        #
        # `make_rng` returns the same `RandomGen` object for every consumer
        # of a shared stream, so jump each distinct underlying stream exactly
        # once.
        from raptor.core.rng import jump_ahead
        _jumped: set[int] = set()

        def _jump_once(gen: RandomSource) -> None:
            if not isinstance(gen, RandomGen):
                raise EngineInvariantError(f"cannot jump a wrapped stream ({type(gen).__name__})")
            if id(gen.stream) in _jumped:
                return
            _jumped.add(id(gen.stream))
            jump_ahead(gen.stream, stream_jump)

        # The enumeration is `RunStreams.distinct_streams()` -- the ONE list
        # every stream-consuming pass walks (this jump, the `/sd` wrapping,
        # and the draw counter). It was previously spelled out here in two
        # loops, which is how a new stream category could be added to the
        # bundle and missed by exactly one of the consumers.
        for rng in streams.distinct_streams():
            _jump_once(rng)
    end_node = next((n for n in model.nodes_by_id.values() if n.type_node == 3), None)
    original_good_paths = {n.id: n.good_paths for n in model.nodes_by_id.values()}
    ctx = EngineCtx(
        model=model, rbd_file=rbd_file, on_event=on_event,
        sim_details_recorder=None, current_scheduler=None,
        sys_streams=sys_streams, rng_fail_by_block=rng_fail_by_block,
        rng_repair_by_block=rng_repair_by_block, rng_by_event=rng_by_event,
        stats_by_block=stats_by_block, stats_by_event=stats_by_event,
        end_node=end_node, original_good_paths=original_good_paths,
        ao_sink=ao_sink, cost_sink=cost_sink,
        ao_plot_interval=ao_plot_interval,
        cost_plot_interval=cost_plot_interval,
        report_sink=report_sink, runs_total=runs,
    )
    return model, ctx

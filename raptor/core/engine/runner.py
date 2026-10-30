"""Run a model: the multi-run loop, the parallel batch and the report run.

`run_simulation` ports `StartEngine`'s main run loop: it loads the model once and
drives one `driver.SingleRunDriver` per run, or splits the batch
across worker processes (`_run_chunk_worker`). `run_simulation_with_reports`
runs it and builds the Final Results and End-of-Run reports.
"""
from __future__ import annotations

import os as _os
import time

from raptor.core.engine.load import load_model, resolve_defer_trigger
from raptor.core.engine.driver import (
    EngineCtx, SingleRunDriver, _streams_of, build_engine_ctx, build_run_streams, count_stream_draws,
)
from raptor.core.engine.errors import RunAborted, SkipRun, StreamBudgetExceeded
from raptor.core.engine.reporting import RunReportData
from raptor.core.engine.reports import build_end_of_run_report, build_final_results_report
from raptor.core.engine.sim_details import NamedRandomGen, SimDetailsRecorder
from raptor.core.fileio.rbd import RbdFile
from raptor.core.scheduler import Scheduler

#: Multiprocessing start method for parallel batches (`num_workers > 1`).
#: "spawn" is required because the GUI starts batches from a live Qt event
#: loop and forking a process that owns a QApplication kills the workers.
#: Overridable via `RAPTOR_MP_CONTEXT` for test harnesses.
MP_CONTEXT = _os.environ.get("RAPTOR_MP_CONTEXT", "spawn")


#: Work chunks in a parallel batch, whatever the number of workers. Each
#: chunk has its own random-stream segment, so the split decides the
#: numbers: it used to be 8 per worker, so the same model gave different
#: answers on a 4-core and an 8-core machine, and changed when the GUI's
#: Cores box did. A fixed count makes every parallel run of a model agree
#: (one worker still runs the sequential path, which matches Raptor 7).
#: More chunks also mean steadier progress and load balancing, at the cost
#: of one engine-context rebuild (parse the .rbd, build the model) each.
_PARALLEL_CHUNKS = max(1, int(
    _os.environ.get("RAPTOR_PARALLEL_CHUNKS", "64")))


def check_chunk_draw_budget(max_draws: int, stride: int) -> None:
    """Raise `StreamBudgetExceeded` when a chunk drew `max_draws` from one
    stream and its segment (the `stride` to the next chunk) holds fewer."""
    if max_draws > stride:
        raise StreamBudgetExceeded(
            f"a parallel chunk drew {max_draws:,} numbers from one random stream but its segment holds "
            f"{stride:,}, so it overlapped the next chunk and the runs are not independent. "
            "Run fewer replications at once, or run sequentially (one worker).")


def negative_draw_total(streams) -> int:
    """Draws clamped to 0.000001 so far, over every distinct underlying generator (the `/sd` and
    counting wrappers hold the real one in `.rng`)."""
    seen: dict[int, object] = {}
    for g in streams:
        while hasattr(g, "rng"):
            g = g.rng
        seen[id(g)] = g
    return sum(getattr(g, "negative_draws", 0) for g in seen.values())


#: Set in each pool worker (`_init_chunk_worker`): a shared flag a worker
#: raises when one of its runs hits the anti-runaway bound.
_RUNAWAY_FLAG = None
#: Set in each pool worker: a shared count of finished runs, bumped after
#: every run so the parent can show progress between chunk merges.
_RUNS_DONE = None
#: Set in each pool worker: raised by the parent on Stop. A worker checks it
#: between runs and returns the runs it has finished instead of the whole chunk.
_STOP_FLAG = None


#: The one unrun model this worker process has loaded, with the key it was
#: loaded for. A batch is split into up to 64 chunks and each chunk used to
#: parse and load the model again; now the first chunk loads it and every
#: chunk (that one included) runs on a copy, so set-up is paid once per
#: worker. The template itself is never run.
_TEMPLATE: tuple[tuple[str, str], object] | None = None


def _model_template(rbd, rbd_text: str, defer_trigger: str):
    """This worker's unrun model for `rbd_text` (loaded on first use)."""
    global _TEMPLATE
    import hashlib
    key = (hashlib.sha1(rbd_text.encode("utf-8")).hexdigest(), defer_trigger)
    if _TEMPLATE is None or _TEMPLATE[0] != key:
        _TEMPLATE = (key, load_model(
            rbd, defer_trigger=resolve_defer_trigger(rbd, defer_trigger)))
    return _TEMPLATE[1]


def _init_chunk_worker(flag, runs_done=None, stop=None) -> None:
    global _RUNAWAY_FLAG, _RUNS_DONE, _STOP_FLAG
    _RUNAWAY_FLAG = flag
    _RUNS_DONE = runs_done
    _STOP_FLAG = stop
    # A worker outlives a parent that dies without cleaning up (a crash, or
    # `os._exit`) and would run its chunk to the end on a core nobody is
    # using; it checks on the parent and leaves with it.
    import threading
    parent = _os.getppid()

    def _watch() -> None:
        while _os.getppid() == parent:
            time.sleep(1.0)
        _os._exit(0)
    threading.Thread(target=_watch, daemon=True).start()


#: How long Stop waits for in-flight chunks to hand back their finished runs
#: (a worker answers after the run it is in the middle of).
_STOP_GRACE_SECONDS = 15.0


def _run_chunks_in_pool(mp_ctx, payloads, workers, merge, runs, *,
                        progress=None, stop_check=None, pause_check=None,
                        metadata_sink=None) -> None:
    """Run the chunks on `workers` processes, merging results in chunk
    order (the order decides the numbers, so it never depends on which
    worker finished first).

    At most `workers` chunks are in flight, so the batch can be steered as
    it goes: Stop (`stop_check`, or `progress` raising `RunAborted`) kills
    the workers and keeps every run already finished, including the runs of a
    chunk still in flight (each worker hands back what it has done); Pause
    (`pause_check`, or a `stop_check` that blocks, as the GUI's does) hands
    out no new chunks until resumed; a run that hits the anti-runaway bound
    in any worker sets `metadata_sink["runaway_runs"]` straight away and
    calls `progress`, so a caller can ask whether to go on (the sequential
    path does the same after the run). It used to be one `ex.map` over every
    chunk: none of those hooks worked, and Stop waited for the whole batch.
    """
    flag = mp_ctx.Event()
    # Runs finished in any worker, for progress. Results are only MERGED in
    # chunk order (that decides the numbers), so reporting merges alone
    # left a big run's progress still for minutes.
    runs_done = mp_ctx.Value("i", 0)
    stop_flag = mp_ctx.Event()
    pool = mp_ctx.Pool(workers, initializer=_init_chunk_worker,
                       initargs=(flag, runs_done, stop_flag))
    shown = 0
    pending: dict = {}
    finished: dict = {}
    nxt = merged = 0
    done = 0
    stopped = False
    flagged = False

    def _report(n: int) -> None:
        nonlocal stopped
        if progress is None:
            return
        try:
            progress(n, runs)
        except RunAborted:
            stopped = True

    try:
        while merged < len(payloads) and not stopped:
            if stop_check is not None and stop_check():
                stopped = True
                break
            if pause_check is not None:
                pause_check()
            while nxt < len(payloads) and len(pending) < workers:
                pending[nxt] = pool.apply_async(_run_chunk_worker,
                                                (payloads[nxt],))
                nxt += 1
            pending[min(pending)].wait(0.1)
            for i in [i for i, r in pending.items() if r.ready()]:
                finished[i] = pending.pop(i).get()
            while merged in finished:
                done = merge(finished.pop(merged))
                merged += 1
            live = max(done, min(runs_done.value, runs))
            if live != shown:
                shown = live
                _report(live)
            if flag.is_set() and not flagged:
                flagged = True
                if metadata_sink is not None:
                    metadata_sink["runaway_runs"] = max(
                        1, metadata_sink.get("runaway_runs", 0))
                _report(max(done, shown))
        # Stopped: keep what finished, in chunk order. A chunk in flight is
        # asked to return the runs it has done (it was a big batch's whole
        # progress otherwise: 70,000 runs counted, none kept, because no
        # chunk of ~70,000 had finished); one that does not answer in time
        # is dropped when the pool is terminated.
        if stopped:
            stop_flag.set()
        deadline = time.monotonic() + _STOP_GRACE_SECONDS
        for i in sorted(pending):
            r = pending[i]
            if stopped:
                r.wait(max(0.0, deadline - time.monotonic()))
            if r.ready():
                finished[i] = pending.pop(i).get()
        for i in sorted(finished):
            done = merge(finished.pop(i))
    finally:
        if pending or stopped:
            pool.terminate()
        else:
            pool.close()
        pool.join()


def _run_chunk_worker(payload):
    """Subprocess entry for num_workers>1 batches (see run_simulation).
    Runs its chunk sequentially with this worker's disjoint stream
    segment; returns picklable artifacts for ordered merging.

    The payload is `.rbds` JSON (not legacy `.rbd` text): the legacy writer
    drops phase columns, `rates`, `formulas` and `annotations`, which used
    to corrupt any phased/annotated model under `num_workers > 1`.
    """
    (rbd_text, chunk_runs, stream_jump, ao_interval, cost_interval,
     collect_logs, stream_seed, defer_trigger) = payload
    from raptor.core.fileio.rbds import parse_rbds
    rbd = parse_rbds(rbd_text)
    template = _model_template(rbd, rbd_text, defer_trigger)
    report_rows: list[RunReportData] = []
    ao_lines: list[str] = []
    cost_lines: list[str] = []
    _model, ctx = build_engine_ctx(
        rbd, num_runs=chunk_runs, report_sink=report_rows,
        ao_sink=ao_lines, cost_sink=cost_lines,
        ao_plot_interval=ao_interval, cost_plot_interval=cost_interval,
        stream_jump=stream_jump, stream_seed=stream_seed, defer_trigger=defer_trigger,
        model_template=template)
    counting = count_stream_draws(ctx)
    neg_runs: list[int] = []
    neg_before = 0
    logs_list: list[list[str]] = []
    cap_lines: list[str] = []
    done = runaway = 0
    for i in range(chunk_runs):
        if _STOP_FLAG is not None and _STOP_FLAG.is_set():
            break
        driver = SingleRunDriver(ctx, i)
        driver.kickoff()
        driver.advance_to_end()
        driver.finalize()
        logs_list.append(list(driver.logs) if collect_logs else [])
        neg_total = negative_draw_total(_streams_of(ctx).distinct_streams())
        neg_runs.append(neg_total - neg_before)
        neg_before = neg_total
        if driver.capacity_state is not None:
            cap_lines.extend(driver.capacity_state.cap_file_lines)
        if _RUNS_DONE is not None:
            with _RUNS_DONE.get_lock():
                _RUNS_DONE.value += 1
        if driver.hit_safety_bound:
            runaway += 1
            if _RUNAWAY_FLAG is not None:
                _RUNAWAY_FLAG.set()      # the parent asks now, not at the end
        done += 1
    return {"logs": logs_list, "reports": report_rows, "ao": ao_lines,
            "cost": cost_lines, "cap": cap_lines, "runaway": runaway,
            "done": done, "max_draws": max((c.draws for c in counting), default=0),
            "negative_draws": neg_runs}


def run_simulation(
    rbd_file: RbdFile, num_runs: int | None = None, *, _report_sink: list[RunReportData] | None = None,
    sim_details_path: str | None = None, _capacity_sink: list[str] | None = None,
    progress=None, logs_sink: list[list[str]] | None = None,
    on_event=None,
    _cost_sink: list[str] | None = None, cost_plot_interval: float = 720.0,
    _ao_sink: list[str] | None = None, ao_plot_interval: float = 10.0,
    stop_check=None, pause_check=None, skip_check=None,
    num_workers: int = 1,
    metadata_sink: dict | None = None,
    collect_logs: bool = True,
    seed: int | None = None,
    count_draws: bool = False,
    sim_details_append: bool = False,
    mission_path: str | None = None,
    defer_trigger: str = "",
    cumulative_trial_path: str | None = None,
    cumulative_trial_interval: int = 0,
    cumulative_time_path: str | None = None,
    cumulative_time_mesh: float = 0.0,
    keep_partial_logs: bool = False,
    setup_progress=None,
) -> list[list[str]]:
    """Loads `rbd_file` (via `load_model`) and runs it for `num_runs`
    (defaulting to the file's own `SYSTEM_DATA.NumRuns`), returning one
    event-log line list per run. Each block gets its own dedicated
    fail/repair stream pair seeded from `fetch_raptor_seed`, matching
    Raptor 7; those streams persist across runs. Events use
    the same per-object dedicated-or-shared stream lookup with a single fail
    stream each (see `events.py`).

    `num_workers > 1` splits the batch across that many processes, each with
    a disjoint segment of every random stream (`rng.jump_ahead`). Replications
    are then statistically independent but not draw-for-draw identical to a
    sequential batch; sequential mode stays the default and remains bit-exact
    with the golden captures. The batch is split into a fixed number of
    chunks, so every `num_workers > 1` gives the same numbers. In parallel
    mode stop and pause act between chunks (see `_run_chunks_in_pool`);
    skip (one run) and `on_event` are not available.

    `_report_sink`: private, additive hook -- when given a list, one
    `RunReportData` snapshot (see `reporting.py`) is appended per run, right
    after that run's event log is finalized. `None` is a complete no-op.

    `sim_details_path`: this project's own `/sd` equivalent (see
    `sim_details.py`) -- when given a path, every RNG draw across every run
    is recorded and written there in the real `simDetails.TXT` format,
    diffable against a Raptor 7 capture. Per-block/per-event streams are tagged
    with the object's name; a shared stream is tagged generically as
    `"SysStreamN"`, since several callers do not expose enough context to
    name the specific draw.

    `count_draws`: count every distribution draw (Raptor 7's `randNumCount`) into
    `metadata_sink["random_draws"]`. It runs the batch in one process whatever `num_workers`
    says, because the count is one number for the batch.

    `cumulative_trial_path`, `cumulative_trial_interval`: Raptor 7's "Cumulative By Trial File"
    (see `cumulative.py`): every `interval` runs a row of the statistics so far. Written with CRLF.

    `cumulative_time_path`, `cumulative_time_mesh`: the "Cumulative By Time" file (see
    `cumulative.CumulativeByTime`): a row per mesh point, written after the last run. For a
    time-terminated run only (the source's own limit); runs in one process.

    `defer_trigger`: the `deferTrig` choice (see `load.resolve_defer_trigger`): "" off, `file`, or a
    trigger name. Blocks commented `maintDefer` then hold their repair until that trigger fires.

    `mission_path`: Raptor 7's `/C130` Mission File: a row per block
    going down, `-1 -1 -1` at each run's end and `-2 -2 -2` at the end of the batch, written
    there with CRLF line ends.

    `_cost_sink`: NOT just an output switch. Requesting it (any list) stands
    in for Raptor 7's Interim Costs output option, which CHANGES THE
    SIMULATION for a phased CycleSim/FailSim model whose triggers share a
    system stream with the phase schedule (t=0 draw order flips) -- see this
    module's docstring, "SURPRISING REAL-RAPTOR BEHAVIOUR". Pass it exactly
    when the Raptor 7 capture you compare against had Interim Costs ticked.
    """
    model = load_model(rbd_file, defer_trigger=resolve_defer_trigger(rbd_file, defer_trigger),
                       progress=setup_progress)
    runs = model.num_runs if num_runs is None else num_runs
    stream_seed = int(seed or 0)
    if cumulative_trial_path is not None and cumulative_trial_interval > 0 and _report_sink is None:
        _report_sink = []

    def _write_cumulative_by_trial() -> None:
        if cumulative_trial_path is not None and cumulative_trial_interval > 0 and _report_sink is not None:
            from raptor.core.engine.cumulative import by_trial_lines
            with open(cumulative_trial_path, "wb") as f:
                f.write("".join(f"{ln}\r\n" for ln in by_trial_lines(
                    _report_sink, cumulative_trial_interval)).encode("latin-1"))

    if num_workers > 1 and runs > 1 and not count_draws and not (cumulative_time_path and cumulative_time_mesh > 0):
        # ---- parallel path (see docstring) -------------------------
        import multiprocessing as _mp
        from raptor.core.fileio.rbds import render_rbds
        from raptor.core.rng import _M as RNG_M
        # `.rbds`, not legacy `.rbd`: the legacy writer drops phase columns,
        # rates, formulas and annotations, corrupting phased/annotated models.
        rbd_text = render_rbds(rbd_file)
        # Split into several chunks per worker: each finished chunk calls
        # `progress(done_runs, runs)` once, so the GUI's run-count readout
        # updates regularly instead of only once or twice per batch. Smaller
        # chunks also load-balance better. Each chunk gets its own disjoint
        # RNG stream segment, so results stay statistically independent.
        n_chunks = min(runs, _PARALLEL_CHUNKS)
        stride = max(RNG_M - 1, 1) // max(n_chunks, 1)
        base, extra = divmod(runs, n_chunks)
        payloads = []
        for w in range(n_chunks):
            chunk = base + (1 if w < extra else 0)
            if chunk:
                payloads.append((rbd_text, chunk,
                                 (w + 1) * stride, ao_plot_interval,
                                 cost_plot_interval, collect_logs,
                                 stream_seed, defer_trigger))
        all_logs_p: list[list[str]] = []
        neg_all: list[int] = []
        done_runs = 0
        # Spawn, not the default fork: the GUI calls this from inside a live
        # Qt event loop, and forking a process that owns a QApplication and
        # its threads leaves the children dead on arrival.
        mp_ctx = _mp.get_context(MP_CONTEXT)
        runaway_total = [0]

        def _merge(res):
            nonlocal done_runs
            check_chunk_draw_budget(res["max_draws"], stride)
            all_logs_p.extend(res["logs"])
            if _report_sink is not None:
                _report_sink.extend(res["reports"])
            if _capacity_sink is not None:
                _capacity_sink.extend(res["cap"])
            if _ao_sink is not None and res["ao"]:
                _ao_sink.extend(res["ao"][1:])   # skip dup header
            if _cost_sink is not None and res["cost"]:
                _cost_sink.extend(res["cost"])
            done_runs += res["done"]
            neg_all.extend(res["negative_draws"])
            runaway_total[0] += res.get("runaway", 0)
            return done_runs

        _run_chunks_in_pool(
            mp_ctx, payloads, min(num_workers, n_chunks), _merge, runs,
            progress=progress, stop_check=stop_check,
            pause_check=pause_check, metadata_sink=metadata_sink)
        if _report_sink is not None:
            # chunks each numbered from 1 -- renumber sequentially
            for i, row in enumerate(_report_sink):
                row.run_number = i + 1
        _write_cumulative_by_trial()
        if metadata_sink is not None:
            metadata_sink["negative_draws"] = neg_all
            if runaway_total[0]:
                metadata_sink["runaway_runs"] = runaway_total[0]
        return all_logs_p

    # DELEGATED to `driver.build_run_streams` (review E-02). These six
    # statements were written out here as well as in `build_engine_ctx`, and
    # what they ENUMERATE -- the model's stream set -- is walked by three
    # other consumers (`stream_jump`, the `/sd` wrapping below, and
    # `count_stream_draws`). `RunStreams` is the one owner of that set.
    streams = build_run_streams(model, stream_seed=stream_seed)
    sys_streams = streams.sys_streams
    rng_fail_by_block = streams.rng_fail_by_block
    rng_repair_by_block = streams.rng_repair_by_block
    stats_by_block = streams.stats_by_block
    rng_by_event = streams.rng_by_event
    stats_by_event = streams.stats_by_event

    sim_details_recorder = None
    # Shared holders live at FUNCTION scope: run_simulation passes them into
    # EngineCtx and the stream wrappers reference them on every draw.
    _current_scheduler: dict[str, Scheduler] = {}
    _draw_tag: dict[str, str] = {}
    if sim_details_path is not None or count_draws:
        sim_details_recorder = SimDetailsRecorder(keep_lines=sim_details_path is not None)
        # One mutable holder for "the currently-active scheduler's own
        # `.time`" -- `scheduler` is rebuilt every run, but these `RandomGen`
        # wrappers are built once, up front.
        #: Name of the phase whose duration is currently being drawn from
        #: stream 11 -- real tags each such draw with the phase's own name
        #: (`gettingDraw` at ControlPhaseChanges' DrawNumber call site). Falls
        #: back to the generic "SysStream11" tag when nothing set it.

        def _sim_time() -> float:
            return _current_scheduler["s"].time

        # The stream set is enumerated by `RunStreams.wrap_per_element`
        # (review E-02) rather than by two hand-written loops over
        # `model.blocks_by_id` / `model.events_by_id`. Those loops read each
        # collection by key, so a new per-element stream collection would have
        # been silently left unwrapped -- unreported in the `/sd` capture --
        # while still being advanced by `stream_jump`.
        def _name_of(eid: int) -> str:
            block = model.blocks_by_id.get(eid)
            return (block.name if block is not None
                    else model.events_by_id[eid].name)

        streams.wrap_per_element(
            lambda gen, name: NamedRandomGen(
                gen, sim_details_recorder, name, _sim_time),
            _name_of,
        )
        #: Raw (pre-wrap) stream-11 generator, kept for the per-trigger
        #: wrappers built at scheduling time -- real records each trigger's
        #: schedule draws under the trigger's own name (as recorded in the
        #: `/sd` capture), while other stream-11 draws keep the generic
        #: "SysStream11" tag.
        for stream_num in list(sys_streams.keys()):
            if stream_num == 11:
                sys_streams[stream_num] = NamedRandomGen(
                    sys_streams[stream_num], sim_details_recorder,
                    (lambda: _draw_tag.get("name", "SysStream11")), _sim_time,
                )
            else:
                sys_streams[stream_num] = NamedRandomGen(
                    sys_streams[stream_num], sim_details_recorder, f"SysStream{stream_num}", _sim_time,
                )

    # The model's End node, needed by repair_completion_status/
    # back_flow_dependencies_from_node for any node-based dependency. Every
    # fixture has exactly one; found once here (model-invariant).
    end_node = next((n for n in model.nodes_by_id.values() if n.type_node == 3), None)

    # `node.good_paths` (the file-parsed static "k") is mutated in place by
    # `conduct_phase_change`'s `apply_node`, and `model.nodes_by_id` is
    # shared across runs, so the original static value is captured once here
    # and re-applied at each run's kickoff (matching `RBDNodeObj.
    # SetToInitValues`'s unconditional `GoodPathsRequired:=goodPaths`).
    original_good_paths = {n.id: n.good_paths for n in model.nodes_by_id.values()}

    all_logs: list[list[str]] = []
    all_capacity_lines: list[str] = []
    if _ao_sink is not None and not _ao_sink:
        # -- file header, once.
        _ao_sink.append("SimulationTime        Ao        Green     Yellow    Red       Interval")
    # Cost-analysis accumulators, one per RESOURCE pool (spare pools keep a
    # zeroed entry too so lookups always succeed). Reset every run below;
    # source resets PoolAddCost/PoolESCost per run.
    ctx = EngineCtx(
        model=model, rbd_file=rbd_file, on_event=on_event,
        sim_details_recorder=sim_details_recorder,
        draw_tag=_draw_tag,
        current_scheduler=_current_scheduler,
        sys_streams=sys_streams, rng_fail_by_block=rng_fail_by_block,
        rng_repair_by_block=rng_repair_by_block, rng_by_event=rng_by_event,
        stats_by_block=stats_by_block, stats_by_event=stats_by_event,
        end_node=end_node, original_good_paths=original_good_paths,
        ao_sink=_ao_sink, cost_sink=_cost_sink,
        ao_plot_interval=ao_plot_interval,
        cost_plot_interval=cost_plot_interval,
        report_sink=_report_sink, runs_total=runs,
    )
    if mission_path is not None:
        ctx.mission_lines = []
    if cumulative_time_path is not None and cumulative_time_mesh > 0.0:
        if model.termination_type != "TimeSim":
            raise ValueError("the cumulative-by-time file needs a time-terminated run (Raptor 7 "
                             "warns 'You are attempting to write a reliability file on a non-time "
                             "terminated sim')")
        from raptor.core.engine.cumulative import CumulativeByTime
        ctx.cum_time = CumulativeByTime(model.run_length, cumulative_time_mesh, runs)
    drivers: list[SingleRunDriver] = []
    negative_by_run: list[int] = []
    _neg_before = [0]
    for run_index in range(runs):
        # GUI hooks: between-runs Stop breaks cleanly here; mid-run the
        # block hook (wired below) raises RunAborted instead.
        if stop_check is not None and stop_check():
            break
        driver = SingleRunDriver(ctx, run_index)
        drivers.append(driver)
        try:
            driver.kickoff()
            if pause_check is not None or stop_check is not None:
                # Scheduler exists only after kickoff; the hook matters
                # exclusively during advance (mid-run).
                def _block_hook():
                    if skip_check is not None and skip_check():
                        raise SkipRun()
                    if stop_check is not None and stop_check():
                        raise RunAborted()
                    if pause_check is not None:
                        pause_check()
                driver.scheduler.block_hook = _block_hook
            driver.advance_to_end()
            driver.finalize()
        except SkipRun:
            # GUI Jump: abandon this run, keep going with the next one.
            continue
        except RunAborted:
            # Hard abandon: discard this run's report/sinks contribution;
            # RNG streams stay advanced -- fine, the batch stops on abort.
            # GUI callers can keep the interrupted run's event log so a
            # stop after a few events still shows what happened.
            if keep_partial_logs and collect_logs and driver.logs:
                all_logs.append(list(driver.logs))
            break
        logs = driver.logs
        _neg_total = negative_draw_total(_streams_of(ctx).distinct_streams())
        negative_by_run.append(_neg_total - _neg_before[0])
        _neg_before[0] = _neg_total
        # Files & Reports opt-in: the GUI's Event Log checkbox OFF frees
        # each run's log as soon as the run ends, so a huge batch cannot
        # accumulate every run's lines in RAM. The returned outer list keeps
        # one (empty) entry per run so callers' run-index alignment is
        # preserved.
        if not collect_logs:
            driver.logs = []
            logs = []
        all_logs.append(logs)
        # Per-run event logs become available the moment each run finishes,
        # so a live view can show progress run-by-run. Purely observational.
        if logs_sink is not None:
            logs_sink.append(logs)
        if driver.capacity_state is not None:
            if not all_capacity_lines:
                # Raptor 7 -- file header, once.
                all_capacity_lines.append("SimulationTime        Flow       Capacity")
            all_capacity_lines.extend(driver.capacity_state.cap_file_lines)
            # Raptor 7 -- per-run separator: WriteLn, the
            # marker, WriteLn, WriteLn (one blank line either side).
            all_capacity_lines.extend([
                "", "*" * 30 + f"End_of_Run_{run_index + 1}" + "*" * 30, ""])
        # Additive progress hook: called after each completed run with
        # (completed, total). Purely observational.
        if driver.hit_safety_bound and metadata_sink is not None:
            # This run met neither its termination condition nor an empty calendar: tell the caller before it
            # goes on to spend as long on every remaining run.
            metadata_sink["runaway_runs"] = metadata_sink.get("runaway_runs", 0) + 1
        if progress is not None:
            # A GUI may abort from this callback (Stop button between runs)
            # by raising RunAborted -- honor it exactly like the mid-run
            # block hook: keep completed results, stop cleanly.
            try:
                progress(run_index + 1, runs)
            except RunAborted:
                break

    if _capacity_sink is not None:
        _capacity_sink.extend(all_capacity_lines)
    _write_cumulative_by_trial()
    if ctx.cum_time is not None and cumulative_time_path is not None:
        with open(cumulative_time_path, "wb") as f:
            f.write("".join(f"{ln}\r\n" for ln in ctx.cum_time.lines()).encode("latin-1"))
    if mission_path is not None and ctx.mission_lines is not None:
        with open(mission_path, "wb") as f:
            f.write("".join(f"{ln}\r\n" for ln in [*ctx.mission_lines, "-2 -2 -2"]).encode("latin-1"))
    if sim_details_recorder is not None and sim_details_path is not None:
        sim_details_recorder.write(sim_details_path, append=sim_details_append)
    if metadata_sink is not None and sim_details_recorder is not None:
        metadata_sink["random_draws"] = sim_details_recorder.count
    if metadata_sink is not None:
        # FailSim/CycleSim runs that exhausted the anti-runaway bound without
        # meeting their termination condition. Aborted batches cannot tell
        # "bound" from "stopped", so the flag reflects completed runs only.
        metadata_sink["negative_draws"] = negative_by_run
        metadata_sink["hit_safety_bound"] = any(
            getattr(d, "hit_safety_bound", False) for d in drivers)
        metadata_sink["runs_completed"] = len(all_logs)

    return all_logs


def run_simulation_with_reports(
    rbd_file: RbdFile, num_runs: int | None = None, *, date: str | None = None,
    report_sink: list[RunReportData] | None = None,
    capacity_sink: list[str] | None = None,
    progress=None,
    notes_sink: list[str] | None = None,
    on_event=None,
    ao_sink: list[str] | None = None,
    cost_sink: list[str] | None = None,
    num_workers: int = 1,
    stop_check=None,
    pause_check=None,
    skip_check=None,
    metadata_sink: dict | None = None,
    collect_logs: bool = True,
    ao_plot_interval: float = 10.0,
    cost_plot_interval: float = 720.0,
    sim_details_path: str | None = None,
    seed: int | None = None,
    count_draws: bool = False,
    sim_details_append: bool = False,
    mission_path: str | None = None,
    defer_trigger: str = "",
    cumulative_trial_path: str | None = None,
    cumulative_trial_interval: int = 0,
    cumulative_time_path: str | None = None,
    cumulative_time_mesh: float = 0.0,
    keep_partial_logs: bool = False,
    setup_progress=None,
) -> tuple[list[list[str]], list[str], list[str]]:
    """Public entry point: same event logs as `run_simulation`, plus
    the `End_Of_Run`/`Final_Results_Report` text (`reports.
    build_end_of_run_report`/`build_final_results_report`). `date` defaults to
    `time.ctime()`; pass a fixed string for reproducible output.

    `report_sink`: additive, GUI-facing hook -- when given a list, the per-run
    `RunReportData` objects are appended as each run finishes (not batched at
    the end), so a caller polling it from another thread sees each run's data
    as it lands, matching the live Ao/G-Y-R status-bar readouts.

    `sim_details_path`: forwarded to `run_simulation` (the `/sd` draw log).

    `count_draws`: the Final Results report gets Raptor 7's `Random draws: N` line
    (printed there only under the master switch; see `dev_options.random_draw_count`)."""
    if count_draws and metadata_sink is None:
        metadata_sink = {}
    report_data: list[RunReportData] = report_sink if report_sink is not None else []
    capacity_lines: list[str] = []
    live_logs: list[list[str]] = []
    all_logs = run_simulation(rbd_file, num_runs, _report_sink=report_data,
                              num_workers=num_workers,
                              _capacity_sink=capacity_lines, progress=progress,
                              setup_progress=setup_progress,
                              logs_sink=live_logs, on_event=on_event,
                              _ao_sink=ao_sink, _cost_sink=cost_sink,
                              stop_check=stop_check, pause_check=pause_check,
                              skip_check=skip_check,
                              metadata_sink=metadata_sink,
                              collect_logs=collect_logs,
                              ao_plot_interval=ao_plot_interval,
                              cost_plot_interval=cost_plot_interval,
                              sim_details_path=sim_details_path,
                              seed=seed, count_draws=count_draws,
                              sim_details_append=sim_details_append,
                              mission_path=mission_path, defer_trigger=defer_trigger,
                              cumulative_trial_path=cumulative_trial_path,
                              cumulative_trial_interval=cumulative_trial_interval,
                              cumulative_time_path=cumulative_time_path,
                              cumulative_time_mesh=cumulative_time_mesh,
                              keep_partial_logs=keep_partial_logs)
    if capacity_sink is not None:
        capacity_sink.extend(capacity_lines)

    model = load_model(rbd_file)
    report_date = date if date is not None else time.ctime()
    # Port of Raptor 7's own "End_Of_Run Report for "+header+
    # "  "+Date header line, written once before the per-run blocks (unlike
    # Final_Results, which gets its own header from
    # `build_final_results_report`).
    end_of_run_lines: list[str] = [f"End_Of_Run Report for {rbd_file.file_name}  {report_date}", ""]
    for run_data in report_data:
        end_of_run_lines.extend(build_end_of_run_report(run_data, model.blocks_by_id))
    final_results_lines = build_final_results_report(
        report_data, model.blocks_by_id, rbd_file.file_name, model.run_length, report_date,
        termination_type=model.termination_type, notes_sink=notes_sink,
        # Source's `StatsReset` global (Raptor 7, sticky once
        # ResetAllStatistics ever fired): a run whose StatsStartTime is
        # non-zero had an in-run reset. Relabels Term1/Term2 rows and adds the
        # footer.
        stats_reset=any(rd.stats_start_time > 0.0 for rd in report_data),
        start_stats=model.stats_start_time,
        random_draws=(metadata_sink or {}).get("random_draws") if count_draws else None,
    )
    # No `report_sink.extend(report_data)` here -- `report_data` IS
    # `report_sink` itself (see the assignment above), already populated
    # incrementally by the inner `run_simulation` call.
    return all_logs, end_of_run_lines, final_results_lines

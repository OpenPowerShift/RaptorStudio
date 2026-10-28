"""Run simulation and return structured results.

Provides :func:`run` (execute a simulation, persist artefacts, return a
:class:`RunResult`), :func:`get_report` (paginated access to stored report
text) and :func:`get_timeseries` (decimated series for the client to
plot).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable

from raptor.api.results import aggregate_metrics, metrics_to_dict, Metric
from raptor.api.workspace import Workspace
from raptor.core.fileio.rbd import RbdFile
from raptor.core.engine.reporting import RunReportData


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RunError(Exception):
    """Base error for run operations."""


class RunCancelledError(RunError):
    """The caller aborted the operation before it finished."""


class RunTimeoutError(RunError):
    """Simulation exceeded its timeout budget."""


class RunNotFoundError(RunError):
    """Referenced run_id does not exist in the workspace."""


#: Explicit horizons at or below this many hours skip the pre-run pilot.
#: The pilot is a REAL replication, so on a large model it can cost as much
#: as the short run it is gating; the run's own `timeout_s` still protects
#: the caller if the model turns out to be pathological.
_SHORT_RUN_SKIP_ESTIMATE_S = 8_760.0


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    """Structured output of a completed simulation."""
    run_id: str
    model_id: str
    num_runs: int
    model_name: str = ""
    metrics: list[Metric] = field(default_factory=list)
    per_run: list[dict[str, float | None]] = field(default_factory=list)
    end_of_run: str = ""
    final_results: str = ""
    wall_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    model_hash: str = ""
    #: Set for an isolated-hierarchy run (a port extension): the
    #: `raptor.api.isolate.IsolationRecord` as a dict. Every output of such a run
    #: carries it.
    isolation: dict[str, Any] | None = None
    #: Set when the experiment asked for importance measures: the
    #: `raptor.api.importance.analyse` result, so the GUI, CLI and MCP all read
    #: one object rather than re-deriving it from the report text.
    importance: dict[str, Any] | None = None
    #: Wall time the pre-run pilot cost (0 when `confirm_long_run` skipped
    #: it, or when the estimate came from cache). Kept out of
    #: `wall_seconds`, which is the simulation batch itself; callers that
    #: time the whole tool call need both.
    estimate_seconds: float = 0.0
    #: The pre-run predicted batch wall time, when one was computed.
    estimated_wall_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = self._base_dict()
        if self.isolation is not None:
            d["isolation"] = self.isolation
        if self.importance is not None:
            d["importance"] = self.importance
        return d

    def _base_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "model_id": self.model_id,
            "model_name": self.model_name,
            "num_runs": self.num_runs,
            "model_hash": self.model_hash,
            "metrics": metrics_to_dict(self.metrics),
            "per_run": self.per_run,
            "wall_seconds": round(self.wall_seconds, 3),
            "estimate_seconds": round(self.estimate_seconds, 3),
            "estimated_wall_seconds": (None
                                       if self.estimated_wall_seconds is None
                                       else round(self.estimated_wall_seconds, 3)),
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9_\-]+")


def run_dir(workspace: Workspace, run_id: str) -> Path:
    """The run's directory, refusing ids that escape ``runs/``.

    Run ids are server-generated identifiers.  Treating one as a path
    allowed ``get_report(run_id="../outside")`` to read known artefact
    filenames outside the workspace.
    """
    if not _SAFE_RUN_ID.fullmatch(str(run_id)):
        raise RunNotFoundError(f"Run {run_id!r} not found")
    return workspace.root / "runs" / run_id


def _new_run_id() -> str:
    h = hashlib.sha256(
        f"{time.time_ns()}-run-{os.getpid()}".encode()
    ).hexdigest()[:8]
    return f"r_{h}"


def model_hash(rbd: RbdFile) -> str:
    """Deterministic content hash of a model."""
    from raptor.core.fileio.rbds import render_rbds
    return hashlib.sha256(render_rbds(rbd).encode("utf-8")).hexdigest()[:16]


def _rd_to_dict(rd: RunReportData) -> dict[str, float | None]:
    """Single RunReportData → flat dict (one per run)."""
    return {
        "availability": rd.availability,
        "reliability": rd.reliability,
        "conditional_reliability": rd.conditional_reliability,
        "dependability": rd.dependability,
        "mtbde": rd.mtbde,
        "mdt": rd.mdt,
        "mtbm": rd.mtbm,
        "mtbmu": rd.mtbmu,
        "mtbms": rd.mtbms,
        "mmt": rd.mmt,
        "mmtu": rd.mmtu,
        "mmts": rd.mmts,
        "mtbde_censored": rd.num_system_failures == 0,
        "mtbm_censored": bool(
            rd.mtbm is not None and rd.ending_sim_time
            and abs(rd.mtbm - rd.ending_sim_time) < 1e-9),
        "mtbmu_censored": bool(
            rd.mtbmu is not None and rd.ending_sim_time
            and abs(rd.mtbmu - rd.ending_sim_time) < 1e-9),
        "num_system_failures": rd.num_system_failures,
        "num_component_failures": rd.num_component_failures,
        "green_percent": rd.green_percent,
        "yellow_percent": rd.yellow_percent,
        "red_percent": rd.red_percent,
    }


def model_units(rbd: RbdFile) -> str:
    """The model's time units ('hours', 'miles', ...), or ''.

    One owner for the SYSTEM_DATA accessor: the API run writer, the MCP
    run/timeseries tools and the GUI all show the model's native units,
    so a missing or whitespace-padded column must read the same at every
    door.
    """
    sec = rbd.sections.get("SYSTEM_DATA")
    return str(sec.rows[0].get("units", "")).strip() if sec and sec.rows \
        else ""


def _set_run_length(rbd: RbdFile, run_length: float) -> None:
    """Set SYSTEM_DATA's TermCondition on *rbd* (already a private copy)."""
    sec = rbd.sections.get("SYSTEM_DATA")
    if sec and sec.rows:
        sec.rows[0]["TermCondition"] = f"{float(run_length):.6f}"


def model_num_runs(rbd: RbdFile, *, strict: bool = False) -> int | None:
    """The model's own SYSTEM_DATA.NumRuns, or None if unset.

    ``strict`` is for the MCP boundary: there an unparsable value is the
    caller's problem and gets a message naming the setting, rather than
    silently falling back to a default count. The API keeps the lenient
    form because a run may pass ``num_runs`` explicitly.
    """
    sec = rbd.sections.get("SYSTEM_DATA")
    if not (sec and sec.rows):
        return None
    raw = sec.rows[0].get("NumRuns")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        if strict:
            raise ValueError(
                "SYSTEM_DATA.NumRuns is not a valid run count; set num_runs "
                "explicitly or fix Project Settings") from None
        return None


# ---------------------------------------------------------------------------
# Public API — run
# ---------------------------------------------------------------------------

def run(
    workspace: Workspace,
    model_id: str,
    *,
    num_runs: int | None = None,
    run_length: float | None = None,
    workers: int = 1,
    timeout_s: float = 1800.0,
    collect_events: bool = False,
    confirm_long_run: bool = False,
    date: str | None = None,
    abort: threading.Event | None = None,
    progress: Callable[[int, int | None], None] | None = None,
    seed: int | None = None,
    developer: Any = None,
    importance_progress: Callable[[int, int, str], None] | None = None,
) -> RunResult:
    """Execute a simulation and persist artefacts to ``runs/<run_id>/``.

    ``developer`` (a `raptor.core.dev_options.DeveloperOptions`; default: from
    ``RAPTOR_DEV_FEATURES``, which is what ``--dev-features`` sets) switches on the
    original's hidden options: today the event-log category filter and the one-component
    detail (the Avant-garde tab's A-D boxes and "detailed events for this component").

    ``abort`` lets the caller cancel a run in flight (partial results are
    kept, as for a timeout); ``progress(done, total)`` is called as
    replications complete (best-effort: the parallel path reports less
    often).

    ``run_length`` is applied to a private copy of the model, so the live
    model is never mutated.  Unless ``confirm_long_run`` is set, the call
    estimates first and refuses work predicted to exceed ``timeout_s``.

    ``seed`` (optional, non-negative) salts every random stream so a batch
    can be re-rolled reproducibly; ``None`` (default) keeps the engine's
    fixed RAPTOR seed table, under which the same model and run count
    always returns the same numbers.
    """
    if seed is not None:
        seed = int(seed)
        if seed < 0:
            # caller-input error -> MCP "invalid_argument"
            raise ValueError(f"seed must be a non-negative integer, got {seed}")
    if run_length is not None and not (
            1e-6 <= float(run_length) <= 999_999_999.999999):
        raise ValueError(
            "Stop simulation time must be between 0.000001 and "
            "999,999,999.999999!")
    handle = workspace.get(model_id)

    # Work on a copy only when overriding, so the live model is untouched.
    sim_rbd = copy.deepcopy(handle.rbd) if run_length is not None else handle.rbd
    if run_length is not None:
        _set_run_length(sim_rbd, run_length)

    # The model's own Experiment recording intervals govern sampling, as
    # they do in the GUI. Ignoring them wrote availability at 10 and cost
    # at 720 regardless of what the model asked for (and made a short run
    # look as if cost was never enabled).
    record: dict[str, Any] = {}
    experiment = getattr(sim_rbd, "experiment", None)
    if isinstance(experiment, dict):
        record = experiment.get("recording") or {}

    def _interval(key: str, default: float) -> float:
        try:
            return max(1e-6, float(record.get(key, default)))
        except (TypeError, ValueError):
            return default

    ao_interval = _interval("ao_interval", 10.0)
    cost_interval = _interval("cost_interval", 720.0)

    effective_runs = num_runs if num_runs is not None else model_num_runs(sim_rbd)
    importance_mode = (str(experiment.get("importance", "none"))
                       if isinstance(experiment, dict) else "none")

    # --- estimate: long-run gate + post-run sanity warning ---
    # Computed for the gate (i.e. unless the caller has already opted into
    # a long run) and kept so the actual wall time can be compared against
    # the pre-run prediction afterwards.  Best-effort: a failed pilot
    # never blocks the run.
    est_wall: float | None = None
    estimate_seconds = 0.0
    # A short explicit horizon does not need a pilot to know the batch is
    # affordable: the run's own timeout still aborts it if the model is
    # pathological. This is the common "try 1,000 h" case, where the pilot
    # (a real replication) cost as much as the run it was gating.
    short_explicit = (run_length is not None
                      and float(run_length) <= _SHORT_RUN_SKIP_ESTIMATE_S
                      and importance_mode in ("", "none"))
    if not confirm_long_run and effective_runs and not short_explicit:
        try:
            from raptor.api.estimate import estimate as est_fn
            est = est_fn(workspace, model_id, num_runs=effective_runs,
                         run_length=run_length, workers=workers,
                         importance=importance_mode)
            est_wall = est.wall_seconds_est
            estimate_seconds = float(
                getattr(est, "estimate_seconds", 0.0) or 0.0)
        except Exception:  # noqa: BLE001 - estimate is best-effort
            est_wall = None
        if est_wall is not None and est_wall > timeout_s:
            raise RunTimeoutError(
                f"Estimated {est_wall:.0f}s for "
                f"{effective_runs} runs exceeds timeout_s={timeout_s:.0f}s. "
                "Use fewer runs, a shorter run_length, or "
                "confirm_long_run=True."
            )

    # --- run the simulation ---
    report_data: list[RunReportData] = []
    ao_lines: list[str] = []
    cap_lines: list[str] = []
    cost_lines: list[str] = []
    t0 = time.monotonic()
    run_started = time.ctime()
    from raptor.core.dev_options import from_environment
    if developer is None:
        developer = from_environment()

    cancelled_by_caller = abort is not None
    if abort is None:
        abort = threading.Event()
    # Mark OUR timeout separately: the caller also supplies an abort
    # event, so `abort.is_set()` alone cannot say which one fired.
    timed_out = threading.Event()

    def _on_timeout() -> None:
        timed_out.set()
        abort.set()

    timer = threading.Timer(timeout_s, _on_timeout)
    timer.daemon = True
    timer.start()

    # The draw log is written as the batch runs; it moves into the run directory once that exists.
    sd_tmp = (workspace.root / f".simDetails-{os.getpid()}-{time.monotonic_ns()}"
              if developer.sim_details else None)
    mission_tmp = (workspace.root / f".mission-{os.getpid()}-{time.monotonic_ns()}"
                   if developer.mission_file else None)
    cumtrial_tmp = (workspace.root / f".cumtrial-{os.getpid()}-{time.monotonic_ns()}"
                    if developer.cumulative_by_trial > 0 else None)
    cumtime_tmp = (workspace.root / f".cumtime-{os.getpid()}-{time.monotonic_ns()}"
                   if developer.cumulative_by_time > 0 else None)
    run_metadata: dict = {}
    try:
        event_logs, eor_lines, final_lines = _run_simulation(
            sim_rbd, num_runs, report_data, workers, collect_events,
            abort, ao_lines, cap_lines, cost_lines, date=date,
            ao_plot_interval=ao_interval,
            cost_plot_interval=cost_interval,
            progress=progress, total=effective_runs, seed=seed,
            count_draws=developer.random_draw_count,
            sim_details_path=str(sd_tmp) if sd_tmp is not None else None,
            mission_path=str(mission_tmp) if mission_tmp is not None else None,
            cumulative_trial_path=str(cumtrial_tmp) if cumtrial_tmp is not None else None,
            cumulative_trial_interval=developer.cumulative_by_trial,
            cumulative_time_path=str(cumtime_tmp) if cumtime_tmp is not None else None,
            cumulative_time_mesh=developer.cumulative_by_time,
            defer_trigger=developer.defer_trigger,
            metadata_sink=run_metadata,
        )
    except BaseException:
        for tmp in (sd_tmp, mission_tmp, cumtrial_tmp, cumtime_tmp):
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        raise
    finally:
        timer.cancel()

    wall = time.monotonic() - t0

    # --- importance measures (opt-in) -----------------------------------
    # Runs AFTER the ordinary batch, which is its baseline: the user sees
    # their own results first, and the variants then cost the extra batches
    # the panel warned about. `importance_progress` reports those batches
    # separately so the run's own counter is not disturbed.
    importance_data: dict[str, Any] | None = None
    if importance_mode not in ("none", "") and report_data:
        from raptor.api import importance as _importance
        importance_data = _importance.analyse(
            sim_rbd, report_data, mode=importance_mode,
            runs=len(report_data), workers=max(1, int(workers)), seed=seed,
            progress=importance_progress, stop_check=abort.is_set)
        wall = time.monotonic() - t0

    if abort.is_set() and not report_data:
        for tmp in (sd_tmp, mission_tmp, cumtrial_tmp, cumtime_tmp):
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    if abort.is_set() and not report_data:
        if timed_out.is_set():
            raise RunTimeoutError(
                f"Simulation did not complete within {timeout_s}s")
        if cancelled_by_caller:
            raise RunCancelledError("Simulation was cancelled")
        raise RunTimeoutError(
            f"Simulation did not complete within {timeout_s}s")

    # --- persist artefacts ---
    run_id = _new_run_id()
    runs_dir = workspace.root / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    if sd_tmp is not None and sd_tmp.exists():
        os.replace(sd_tmp, runs_dir / SECTION_FILES["sim_details"])
    if mission_tmp is not None and mission_tmp.exists():
        os.replace(mission_tmp, runs_dir / SECTION_FILES["mission_file"])
    if cumtrial_tmp is not None and cumtrial_tmp.exists():
        os.replace(cumtrial_tmp, runs_dir / SECTION_FILES["cumulative_by_trial"])
    if cumtime_tmp is not None and cumtime_tmp.exists():
        os.replace(cumtime_tmp, runs_dir / SECTION_FILES["cumulative_by_time"])

    eor_text = "\n".join(eor_lines)
    final_text = "\n".join(final_lines)
    artefacts = {
        "end_of_run": eor_text,
        "final_results": final_text,
        "availability_plot": "\n".join(ao_lines),
        "capacity": "\n".join(cap_lines),
        "cost": "\n".join(cost_lines),
    }
    from raptor.core.engine.data_files import DATA_FILES
    for kind, build in DATA_FILES.items():
        artefacts[kind] = "\n".join(build(report_data))
    if collect_events:
        from raptor.core.engine.eventlog import format_event_log_file
        runs = [log if isinstance(log, list) else [log] for log in event_logs]
        cats = developer.event_log_filter
        artefacts["events"] = "\n".join(str(x) for x in format_event_log_file(
            runs, sim_rbd.file_name, time.ctime(),
            blocks="block" in cats, nodes="node" in cats, system="system" in cats,
            phases="phase" in cats,
            component=developer.event_log_component))
    if importance_data is not None:
        artefacts["importance"] = "\n".join(
            _importance.table_lines(importance_data))

    # Written through `section_path`, from the same map the readers use, so
    # the writer cannot spell a filename the reader will not look for.
    #
    # The guard is in the direction that matters: every artefact produced
    # must be a DECLARED section (otherwise the reader has no name for it),
    # and every declared section must be written unless it is one of the
    # conditionally-collected ones. A section added to SECTION_FILES without
    # a writer here would otherwise be readable-but-never-written, which is
    # how the copies drifted in the first place.
    optional = {"events"} if not collect_events else set()
    # Written by the engine itself, as it draws, when the `sim_details` option is on.
    optional.add("sim_details")
    # Only when the experiment asked for importance measures.
    optional.add("importance")
    optional.add("mission_file")
    optional.add("cumulative_by_trial")
    optional.add("cumulative_by_time")
    if developer.diagnostics_log:
        from raptor.core import diagnostics as _diag
        artefacts["diagnostics_log"] = "\n".join(
            [*_diag.startup_lines(time.ctime(), file_to_open=sim_rbd.file_name),
             *_diag.pre_sim_lines(), _diag.start_simulation_line(run_started), _diag.end_simulation_line()])
    optional.add("diagnostics_log")
    if developer.domain_tree or developer.core_file:
        from raptor.core.engine.domain_tree import domain_tree_lines
        today = time.ctime()
        if developer.domain_tree:
            artefacts["domain_tree"] = "\n".join(
                domain_tree_lines(sim_rbd, sim_rbd.file_name, today))
        if developer.core_file:
            artefacts["core_file"] = "\n".join(
                domain_tree_lines(sim_rbd, sim_rbd.file_name, today, core=True))
    optional |= {"domain_tree", "core_file"} - set(artefacts)
    undeclared = set(artefacts) - set(SECTION_FILES)
    unwritten = set(SECTION_FILES) - set(artefacts) - optional
    assert not undeclared and not unwritten, (
        f"the artefact layout and its writer disagree: undeclared="
        f"{sorted(undeclared)} never-written={sorted(unwritten)} "
        f"optional={sorted(optional)}"
    )
    for section, text in artefacts.items():
        path = section_path(runs_dir, section)
        assert path is not None      # guaranteed by the assertion above
        path.write_text(text)

    metrics = aggregate_metrics(report_data)
    per_run = [_rd_to_dict(rd) for rd in report_data]
    mhash = model_hash(sim_rbd)

    # The per-run figures and the time units travel with the run so its
    # result tables can be rebuilt later (`raptor.api.run_tables`).
    (runs_dir / "per_run.json").write_text(json.dumps(per_run))
    summary = {
        "run_id": run_id,
        "model_id": model_id,
        "model_name": handle.name,
        "model_hash": mhash,
        "num_runs": len(report_data),
        "units": model_units(sim_rbd),
        "metrics": metrics_to_dict(metrics),
        "wall_seconds": round(wall, 3),
        "negative_draws": sum(run_metadata.get("negative_draws", [])),
    }
    if importance_data is not None:
        summary["importance"] = importance_data
    (runs_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str)
    )

    warnings: list[str] = []
    from raptor.core.engine import negative_draws as _neg
    _negative = list(run_metadata.get("negative_draws", []))
    _negative_note = _neg.note(_negative)
    if _negative and not _neg.ignoring(sim_rbd) and _negative_note:
        warnings.append(_negative_note)
    if num_runs is not None and len(report_data) < num_runs:
        warnings.append(
            f"Completed {len(report_data)}/{num_runs} runs (timeout or abort)"
        )
    # Sanity-check the pre-run estimate against reality.  Models with
    # non-uniform per-run cost (capacity/events/growth) can beat a short
    # pilot badly; a silent 40x miss is worse than an explicit warning.
    # Below ~50ms the two-decimal diagnosis would read "0.00s" and the
    # ratio is dominated by process noise; don't warn on it.
    if est_wall is not None and est_wall >= 0.05 and wall > 3.0 * est_wall:
        warnings.append(
            f"Actual run took {wall:.1f}s, over 3x the pre-run estimate of "
            f"{est_wall:.2f}s. Per-run cost may be non-uniform "
            f"(capacity/events/growth), so treat timing estimates as a "
            f"lower bound."
        )

    try:
        workspace.enforce_size_cap()
    except OSError:
        pass

    return RunResult(
        run_id=run_id,
        model_id=model_id,
        num_runs=len(report_data),
        model_name=handle.name,
        metrics=metrics,
        per_run=per_run,
        end_of_run=eor_text,
        final_results=final_text,
        wall_seconds=wall,
        warnings=warnings,
        model_hash=mhash,
        importance=importance_data,
        estimate_seconds=estimate_seconds,
        estimated_wall_seconds=est_wall,
    )


def _run_simulation(
    rbd: RbdFile,
    num_runs: int | None,
    report_data: list[RunReportData],
    workers: int,
    collect_events: bool,
    abort: threading.Event,
    ao_lines: list[str],
    cap_lines: list[str],
    cost_lines: list[str],
    date: str | None = None,
    ao_plot_interval: float = 10.0,
    cost_plot_interval: float = 720.0,
    progress: Callable[[int, int | None], None] | None = None,
    total: int | None = None,
    seed: int | None = None,
    count_draws: bool = False,
    sim_details_path: str | None = None,
    mission_path: str | None = None,
    defer_trigger: str = "",
    cumulative_trial_path: str | None = None,
    cumulative_trial_interval: int = 0,
    cumulative_time_path: str | None = None,
    cumulative_time_mesh: float = 0.0,
    metadata_sink: dict | None = None,
) -> tuple[list[list[str]] | list[str], list[str], list[str]]:
    """Thin wrapper around the engine that honours an abort flag."""
    from raptor.core.engine.runner import run_simulation_with_reports as _run

    def _stop_check() -> bool:
        if progress is not None:
            progress(len(report_data), total)
        return abort.is_set()

    all_logs, eor_lines, final_lines = _run(
        rbd,
        num_runs=num_runs,
        report_sink=report_data,
        num_workers=workers,
        stop_check=_stop_check,
        collect_logs=collect_events,
        ao_sink=ao_lines,
        capacity_sink=cap_lines,
        # Always requested -- which, for a phased CycleSim/FailSim model with
        # stream-sharing triggers, CHANGES THE SIMULATION ITSELF, matching
        # Raptor 7 with its Interim Costs output ticked (see
        # raptor.core.engine.orchestrator's module docstring, "SURPRISING
        # REAL-RAPTOR BEHAVIOUR").
        cost_sink=cost_lines,
        date=date,
        ao_plot_interval=ao_plot_interval,
        cost_plot_interval=cost_plot_interval,
        seed=seed,
        count_draws=count_draws,
        sim_details_path=sim_details_path,
        mission_path=mission_path,
        defer_trigger=defer_trigger,
        cumulative_trial_path=cumulative_trial_path,
        cumulative_trial_interval=cumulative_trial_interval,
        cumulative_time_path=cumulative_time_path,
        cumulative_time_mesh=cumulative_time_mesh,
        metadata_sink=metadata_sink,
    )
    return all_logs, eor_lines, final_lines


def run_isolated(
    workspace: Workspace,
    model_id: str,
    hierarchy: str | int,
    *,
    treatments: dict[str, Any] | None = None,
    **run_kwargs: Any,
) -> RunResult:
    """Run one hierarchy of a model on its own (a port extension; see
    `raptor.api.isolate`). Extracts the hierarchy as a standalone model, runs
    that with the ordinary engine, and attaches the isolation record to the
    result, to `summary.json` and to the run directory (`isolation.json`,
    `isolation.txt`). The source model is not modified."""
    from raptor.api import isolate
    handle = workspace.get(model_id)
    extracted, record = isolate.extract(handle.rbd, hierarchy, treatments)
    # Validate the extract, not the source model: this is the model that
    # will run, and the source may be deliberately incomplete elsewhere.
    from raptor.api.validate import issues
    found = issues(extracted)
    errors = [e for e in found if e.severity == "error"]
    if errors:
        raise ValueError(
            "isolated hierarchy failed validation; fix these before "
            "running:\n" + "\n".join(str(e) for e in errors[:10]))
    info = workspace.register_model(
        extracted, name=f"{handle.name}__{record.hierarchy}",
        source=f"isolated hierarchy {record.hierarchy} of {handle.name}")
    try:
        result = run(workspace, info["model_id"], **run_kwargs)
    finally:
        workspace.close(info["model_id"], delete=True)
    result.model_id = model_id
    result.model_name = f"{handle.name} / {record.hierarchy} (isolated)"
    result.isolation = record.to_dict()
    result.warnings = [record.banner] + [
        f"{h.kind}: {', '.join(h.elements)}: {h.detail}"
        + (f" -> {h.treatment} ({h.effect})" if h.treatment else "")
        for h in record.hazards if h.effect != "information"] + result.warnings
    runs_dir = workspace.root / "runs" / result.run_id
    # Keep the model that actually ran. Without it, input tables for an
    # isolated run described the source model, so one exported run
    # package mixed two different models.
    from raptor.core.fileio.rbds import save_rbds
    save_rbds(extracted, str(runs_dir / "isolated_model.rbds"))
    (runs_dir / "isolation.json").write_text(json.dumps(record.to_dict(), indent=2))
    (runs_dir / "isolation.txt").write_text(record.text())
    summary_path = runs_dir / "summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text())
        summary["isolation"] = record.to_dict()
        summary["model_name"] = result.model_name
        summary["model_id"] = model_id
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
    return result


def isolation_metadata(workspace: Workspace, run_id: str) -> dict | None:
    """The isolation record stored with an isolated run, if any.

    Every reader (the run tools, the table tools, resources) used to open
    ``runs/<id>/summary.json`` itself and re-apply the same identity
    checks. A missing, unreadable or foreign summary simply has no
    metadata rather than raising: callers are reporting an optional
    property of the run, not validating it.
    """
    from raptor.api.run_tables import read_summary

    try:
        summary = read_summary(run_dir(workspace, run_id), run_id)
    except Exception:  # noqa: BLE001 - an unsafe/absent id has no metadata
        return None
    isolation = summary.get("isolation")
    return isolation if isinstance(isolation, dict) else None


# ---------------------------------------------------------------------------
# Public API — reports
# ---------------------------------------------------------------------------

#: The on-disk run-artefact layout. THIS MODULE IS THE WRITER, so it owns
#: the map. It used to be spelled out four times -- here as the writer's
#: inline literals, here again as `_SECTION_FILES` for the reader, a third
#: time inline in `api/run_tables.bundle_from_run`, and a fourth time in
#: `mcp/resources.py` -- and the copies had already drifted (see
#: DERIVED_SECTIONS). Every reader resolves names through
#: :func:`section_path` instead of restating a filename.
SECTION_FILES = {
    "end_of_run": "end_of_run.txt",
    "final_results": "final_results.txt",
    "availability_plot": "availability_plot.txt",
    "events": "events.txt",
    # Only when the `sim_details` developer option is on (Raptor 7's `/sd`).
    "sim_details": "simDetails.TXT",
    # Only with the `domain_tree` / `core_file` developer options (Avant-garde F and G).
    "mission_file": "mission_file.txt",
    "cumulative_by_trial": "cumulative_by_trial.txt",
    "cumulative_by_time": "cumulative_by_time.txt",
    "diagnostics_log": "diagnostics.txt",
    "domain_tree": "DomainTree.TXT",
    "core_file": "CoreFile.TXT",
    "capacity": "capacity.txt",
    "cost": "cost.txt",
    # Only when the experiment asked for importance measures (a port
    # extension; Raptor 7 has no such analysis).
    "importance": "importance.txt",
    # The four Files & Reports data files (`raptor.core.engine.data_files`),
    # stored with LF like every other artefact; exported with Raptor 7's CRLF.
    "key_parameters": "key_parameters.txt",
    "failure_times": "failure_times.txt",
    "down_times": "down_times.txt",
    "end_times": "end_times.txt",
}

#: Sections the reader SYNTHESISES rather than opening an artefact.
#: `weak_link` is computed from the run's block statistics, so there is no
#: file for it. It was handled as an inline special case in `get_report`
#: only, which meant `get_report(section="weak_link")` worked over MCP while
#: the `raptor://runs/{run_id}/weak_link` resource 404'd with "no section
#: 'weak_link'" -- the exact "added it to the tool, forgot the resource"
#: shape. Naming the set here is what lets both doors agree.
DERIVED_SECTIONS = frozenset({"weak_link"})

#: Every section name either door accepts, for error messages and docs.
ALL_SECTIONS: tuple[str, ...] = (
    *sorted(SECTION_FILES), *sorted(DERIVED_SECTIONS),
)


def section_path(runs_dir: Path, section: str) -> Path | None:
    """Path of *section*'s artefact within *runs_dir*, or ``None``.

    ``None`` means "there is no file to open" -- either an unknown section
    name or a member of :data:`DERIVED_SECTIONS`. A caller that wants a
    path must handle that rather than silently opening ``runs_dir/None``.
    """
    filename = SECTION_FILES.get(section)
    return None if filename is None else Path(runs_dir) / filename

_RUN_MARKER = re.compile(
    r"(?:Begin_Run_|End_of_Run_|End_Of_Run_|\*\*\*Run_)(\d+)")


def get_report(
    workspace: Workspace,
    run_id: str,
    *,
    section: str = "final_results",
    offset: int = 0,
    limit: int = 100,
    run_number: int | None = None,
) -> dict[str, Any]:
    """Paginated access to stored report text.

    ``section`` is any name in :data:`ALL_SECTIONS` (see the docstring of
    that module attribute for the two kinds).  ``run_number`` slices a
    per-run section when the report carries run markers.
    """
    runs_dir = run_dir(workspace, run_id)
    if not runs_dir.is_dir():
        raise RunNotFoundError(f"Run {run_id!r} not found")

    if section in DERIVED_SECTIONS:
        return _weak_link_section(runs_dir, offset=offset, limit=limit)

    path = section_path(runs_dir, section)
    if path is None:
        raise ValueError(
            f"Unknown section {section!r}; expected one of "
            f"{', '.join(ALL_SECTIONS)}"
        )

    if not path.is_file():
        from raptor.api.workspace import RunArtefactsError
        raise RunArtefactsError(
            f"Report section {section!r} was not recorded for run "
            f"{run_id!r}; re-run with that analysis/recording enabled")

    all_lines = path.read_text().splitlines()
    if not all_lines and section in ("capacity", "availability_plot", "cost"):
        from raptor.api.workspace import RunArtefactsError
        what, analyser = {
            "capacity": ("capacity", "Capacity"),
            "availability_plot": ("availability", "Availability plot"),
            "cost": ("interim cost", "Cost"),
        }[section]
        raise RunArtefactsError(
            f"no {what} samples were recorded for this run; enable "
            f"{analyser} and run again")
    if section == "cost" and not _parse_cost_runs("\n".join(all_lines)):
        from raptor.api.workspace import RunArtefactsError
        raise RunArtefactsError(
            "no interim cost samples were recorded for this run; enable "
            "Cost analysis and use a cost interval shorter than the run "
            "length")
    if run_number is not None:
        if section in ("capacity", "availability_plot", "cost"):
            all_lines = _slice_end_marker(all_lines, run_number)
        elif section == "final_results":
            raise ValueError(
                "final_results is an aggregate report; run_number does not "
                "apply (use end_of_run, events or capacity)")
        else:
            all_lines = _slice_run(all_lines, run_number)

    offset = max(0, int(offset))
    limit = max(1, int(limit))
    page = all_lines[offset: offset + limit]
    next_offset = offset + limit if offset + limit < len(all_lines) else None

    result: dict[str, Any] = {
        "section": section,
        "lines": page,
        "total": len(all_lines),
        "next_offset": next_offset,
    }
    if section == "capacity":
        # Structured view of the sink's change-point rows; `lines` stays
        # raw so format="raw" callers are unaffected.
        result["columns"] = ["time", "flow", "capacity"]
        result["rows"] = _capacity_rows(page)
    return result


def _capacity_rows(lines: list[str]) -> list[dict[str, float]]:
    """Flatten capacity-sink lines into ``{time, flow, capacity}`` rows.

    Run-boundary markers and any header are dropped; only data rows
    survive.  Order is preserved (run by run, then time).
    """
    from raptor.api import series as series_mod

    rows: list[dict[str, float]] = []
    for run in series_mod.parse_capacity_runs("\n".join(lines)):
        for t, flow, capacity in zip(
            run["t"], run["flow"], run["capacity"],
        ):
            rows.append({"time": t, "flow": flow, "capacity": capacity})
    return rows


def _slice_end_marker(lines: list[str], run_number: int) -> list[str]:
    """Slice a sink whose markers are run ENDs (capacity/Ao/cost).

    Treating the marker as a start returned the next run's samples under
    the requested run.
    """
    start = 0
    for i, line in enumerate(lines):
        m = _RUN_MARKER.search(line)
        if not m:
            continue
        if int(m.group(1)) == run_number:
            return lines[start:i + 1]
        start = i + 1
    raise ValueError(f"run_number {run_number} is out of range")


def _slice_run(lines: list[str], run_number: int) -> list[str]:
    """Return the block for one replication, or the whole list if the
    report has no run markers."""
    starts = [i for i, l in enumerate(lines)
              if (m := _RUN_MARKER.search(l)) and int(m.group(1)) == run_number]
    if not starts:
        # Do not return a whole multi-run file under a one-run label.
        raise ValueError(f"run_number {run_number} is out of range")
    start = starts[0]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _RUN_MARKER.search(lines[i]):
            end = i
            break
    return lines[start:end]


def _weak_link_section(runs_dir: Path, *, offset: int, limit: int) -> dict[str, Any]:
    """Extract the ``***WEAK_LINK_ANALYSIS***`` block from final results."""
    path = runs_dir / "final_results.txt"
    if not path.is_file():
        raise RunNotFoundError("final_results.txt missing; cannot extract weak link")
    lines = path.read_text().splitlines()
    banner = next((i for i, l in enumerate(lines)
                   if "WEAK_LINK_ANALYSIS" in l), None)
    if banner is None:
        from raptor.api.workspace import RunArtefactsError
        raise RunArtefactsError(
            "weak-link analysis was not recorded for this run; enable "
            "Weak Link and run again")
    end = len(lines)
    for i in range(banner + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("***") and "WEAK_LINK_ANALYSIS" not in stripped:
            end = i
            break
    block = lines[banner:end]
    page = block[offset: offset + limit]
    next_offset = offset + limit if offset + limit < len(block) else None
    return {"section": "weak_link", "lines": page, "total": len(block),
            "next_offset": next_offset}


# ---------------------------------------------------------------------------
# Public API — timeseries
# ---------------------------------------------------------------------------

def _parse_cost_runs(text: str) -> list[dict[str, list]]:
    """Interim cost report -> one ``{t, values}`` per run.

    The sink is a sequence of ``SimTime=`` stamps followed by their
    ``Grand_Total`` row, not a numeric table; the old generic parser saw
    block names in column 1 and returned nothing.
    """
    runs: list[dict[str, list]] = []
    current: dict[str, list] = {"t": [], "values": []}
    pending_t: float | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if "End_of_Run_" in stripped:
            if current["t"]:
                runs.append(current)
            current = {"t": [], "values": []}
            pending_t = None
            continue
        if stripped.startswith("SimTime="):
            try:
                pending_t = float(stripped.split("=", 1)[1].strip())
            except (ValueError, IndexError):
                pending_t = None
            continue
        if stripped.startswith("Grand_Total") and pending_t is not None:
            parts = stripped.split()
            try:
                current["t"].append(pending_t)
                current["values"].append(float(parts[1]))
            except (ValueError, IndexError):
                pass
            pending_t = None
    if current["t"]:
        runs.append(current)
    return runs


def _parse_runs_generic(text: str, *, value_col: int = 1) -> list[dict[str, list]]:
    """Parse a run-separated numeric report into one ``{t, values}`` per run.

    A line that is not numeric (a header, banner or blank) starts a new
    run when it follows data.  Used for capacity/cost series whose exact
    column layout is less stable than the Ao plot's.
    """
    runs: list[dict[str, list]] = []
    current: dict[str, list] = {"t": [], "values": []}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) <= value_col:
            if current["t"]:
                runs.append(current)
                current = {"t": [], "values": []}
            continue
        try:
            t = float(parts[0])
            v = float(parts[value_col])
        except ValueError:
            if current["t"]:
                runs.append(current)
                current = {"t": [], "values": []}
            continue
        current["t"].append(t)
        current["values"].append(v)
    if current["t"]:
        runs.append(current)
    return runs


def get_timeseries(
    workspace: Workspace,
    run_id: str,
    *,
    series: str = "availability",
    runs: str = "mean",
    points: int = 100,
) -> dict[str, Any]:
    """Return a decimated time series for the client to plot."""
    from raptor.api import series as series_mod

    runs_dir = run_dir(workspace, run_id)
    if not runs_dir.is_dir():
        raise RunNotFoundError(f"Run {run_id!r} not found")
    from raptor.api.limits import MAX_TIMESERIES_POINTS
    points = max(1, min(int(points), MAX_TIMESERIES_POINTS))

    if series == "availability":
        path = runs_dir / "availability_plot.txt"
        if not path.is_file():
            from raptor.api.workspace import RunArtefactsError
            raise RunArtefactsError(
                "availability plot was not recorded for this run; re-run "
                "with Availability plot enabled")
        parser = getattr(series_mod, "parse_ao_runs", None)
        if parser is None:
            raise ValueError("availability series parser unavailable")
        per_run = parser(path.read_text())
        label = "Ao"
        envelope_fn = series_mod.mean_envelope
    elif series == "capacity":
        path = runs_dir / "capacity.txt"
        raw_runs = series_mod.parse_capacity_runs(path.read_text())
        # `values` stays the flow column; the capacity column is returned
        # alongside it.
        per_run = [
            {"t": r["t"], "values": r["flow"], "capacity": r["capacity"]}
            for r in raw_runs
        ]
        label = "Capacity"
        envelope_fn = series_mod.capacity_envelope
    elif series == "cost":
        path = runs_dir / "cost.txt"
        per_run = _parse_cost_runs(path.read_text())
        label = "Cost"
        envelope_fn = series_mod.mean_envelope
    else:
        raise ValueError(f"Unknown series {series!r}; use availability/capacity/cost")

    per_run = [s for s in per_run if s.get("t")]
    if not per_run:
        # The run exists and was recorded, it just has no samples for
        # this series: that is a precondition, not a missing run (the
        # generic RunNotFound hint wrongly suggested retention purged it).
        from raptor.api.workspace import RunArtefactsError
        analyser = {"capacity": "Capacity", "cost": "Cost",
                    "availability": "Availability plot"}.get(series, series)
        raise RunArtefactsError(
            f"No {series} samples were recorded for run {run_id!r}; "
            f"enable {analyser} and run again")

    n_runs = len(per_run)
    values: Any
    envelope = None
    capacity_values: Any = None
    if runs == "all":
        # Runs have different change-point grids. Resample every run onto
        # the longest run's grid first, then decimate ONCE with shared
        # indices: `t`, each run's `values` and capacity must stay the
        # same length and refer to the same instants.
        base = max(per_run, key=lambda s: len(s["t"]))
        grid = list(base["t"])
        aligned = {
            f"run_{i + 1}": [series_mod._interp(s["t"], s["values"], x)
                             for x in grid]
            for i, s in enumerate(per_run)
        }
        aligned_cap = None
        if series == "capacity":
            aligned_cap = {
                f"run_{i + 1}": [series_mod._interp(
                    s["t"], s["capacity"], x) for x in grid]
                for i, s in enumerate(per_run)
            }
        keep = series_mod.decimate_indices(
            grid, aligned["run_1"], points)
        t = [grid[i] for i in keep]
        values = {k: [v[i] for i in keep] for k, v in aligned.items()}
        if aligned_cap is not None:
            capacity_values = {
                k: [v[i] for i in keep] for k, v in aligned_cap.items()}
        decimated_from = len(grid)
    elif runs in ("mean", ""):
        env = envelope_fn(per_run)
        t = env["t"]
        values = env["mean"]
        envelope = {"min": env["min"], "max": env["max"]}
        # Capacity's envelope is the union of every run's change-points;
        # the other series count the first run's raw samples.
        decimated_from = len(t) if series == "capacity" \
            else len(per_run[0]["t"])
        if series == "capacity":
            capacity_values = envelope_fn(
                _series_view(per_run, "capacity"))["mean"]
    else:
        try:
            idx = int(runs)
        except ValueError:
            raise ValueError(f"runs must be 'mean', 'all' or a run number, got {runs!r}") from None
        if not (1 <= idx <= n_runs):
            raise ValueError(f"run {idx} out of range (1..{n_runs})")
        picked = per_run[idx - 1]
        decimated = series_mod.decimate(picked, points)
        t = decimated["t"]
        values = decimated["values"]
        decimated_from = len(picked["t"])
        if series == "capacity":
            capacity_values = series_mod.decimate(
                {"t": picked["t"], "values": picked["capacity"]}, points,
            )["values"]

    # Cap the returned samples, keeping flow, capacity and the envelope
    # aligned.
    if runs != "all" and len(t) > points:
        keep = series_mod.decimate_indices(t, values, points)
        t = [t[i] for i in keep]
        values = [values[i] for i in keep]
        if capacity_values is not None:
            capacity_values = [capacity_values[i] for i in keep]
        if envelope is not None:
            envelope = {
                "min": [envelope["min"][i] for i in keep],
                "max": [envelope["max"][i] for i in keep],
            }

    preview = list(zip(t[:15], values[:15])) if runs != "all" else []
    if preview:
        from raptor.mcp.result import markdown_table
        markdown = markdown_table(["t", label], [list(p) for p in preview])
    else:
        markdown = f"{n_runs} run(s) included; {len(t)} sample(s)."

    result = {
        "t": t,
        "values": values,
        "series": series,
        "runs_included": n_runs,
        "decimated_from": decimated_from,
        "envelope": envelope,
        "markdown": markdown,
    }
    if capacity_values is not None:
        result["capacity"] = capacity_values
    return result


def _series_view(runs: list[dict], key: str) -> list[dict]:
    """Copy *runs* with ``values`` taken from *key* (for a second column)."""
    return [
        {"t": r["t"], "values": r.get(key, r["values"])}
        for r in runs
    ]

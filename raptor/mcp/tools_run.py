"""MCP tools, part 2: analysis, run and timeseries.

Registers the analysis, what-if and timeseries tools.  Every analysis tool
returns structured JSON (``structuredContent``) plus a Markdown body from
:mod:`raptor.api.markdown` — this layer formats no reliability numbers
itself.  Output discipline (row caps, ``resource_link``s, image caps) is
enforced here.
"""
from __future__ import annotations
from raptor.api.insight.axes import pct

import math
import os
from typing import Any, Literal

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.jobs import run_job
from raptor.mcp.result import ADDITIVE, READ_ONLY, guarded, markdown_table, ok
from raptor.mcp.server import mcp


def resolve_workers(workers: int | None, rbd, runs: int) -> tuple[int, bool]:
    """`(workers, chosen automatically)` for an MCP run.

    A caller's `workers` is used as given. When it is left out, a large model
    run many times gets several cores: half the machine's, at most
    `AUTO_WORKERS_MAX`, and never fewer than two so the answer does not depend
    on the machine (a multi-core batch is not bit-identical to a sequential
    one, but is the same for any worker count above one)."""
    from raptor.api.limits import (
        AUTO_WORKERS_MAX, AUTO_WORKERS_MIN_ELEMENTS, AUTO_WORKERS_MIN_RUNS)
    if workers is not None:
        return int(workers), False
    if runs < AUTO_WORKERS_MIN_RUNS:
        return 1, False
    size = sum(len(rbd.sections[s].rows) for s in
               ("BLOCK_DATA", "EVENT_DATA", "NODE_DATA") if s in rbd.sections)
    if size < AUTO_WORKERS_MIN_ELEMENTS:
        return 1, False
    return min(AUTO_WORKERS_MAX, max(2, (os.cpu_count() or 1) // 2)), True


def _validate_run_args(timeout_s: float | None, date: str | None,
                       workers: int | None = 1) -> None:
    """Refuse values that would otherwise fail silently or misleadingly."""
    from raptor.api.limits import MAX_TIMEOUT_S, MAX_WORKERS
    if timeout_s is None or float(timeout_s) <= 0:
        raise ValueError("timeout_s must be greater than 0")
    if not math.isfinite(float(timeout_s)) or float(timeout_s) > MAX_TIMEOUT_S:
        raise ValueError(
            f"timeout_s must be finite and at most {MAX_TIMEOUT_S:g}")
    if workers is not None:
        context.whole_number(workers, "workers", minimum=1, maximum=MAX_WORKERS)
    if not date:
        return
    from datetime import datetime
    from email.utils import parsedate_to_datetime
    try:
        datetime.fromisoformat(date)
    except ValueError:
        try:
            parsedate_to_datetime(date)
        except (TypeError, ValueError):
            raise ValueError(
                "date must be an ISO date/time or a valid date string"
            ) from None


# ══════════════════════════════════════════════════════════════════════
# Analysis
# ══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations=READ_ONLY("Validate model"))
@guarded
def validate_model(model_id: str) -> CallToolResult:
    """Validate a model for structural and parameter errors.

    Returns {errors, warnings, groups, runnable}. `groups` collapses the
    same rule firing on many elements into one row (rule, count, example
    elements), which is what makes a 700-error model readable; `errors`
    and `warnings` keep the verbatim per-element list. Always call this
    before a run.
    """
    ws = context.workspace()
    from raptor.api.validate import issues
    handle = ws.get(model_id)
    found = issues(handle.rbd)
    errors = [{"element": e.element, "message": e.message}
              for e in found if e.severity == "error"]
    warnings = [{"element": e.element, "message": e.message}
                for e in found if e.severity == "warning"]
    data: dict[str, Any] = {"errors": errors, "warnings": warnings,
                            "groups": _group_messages(errors),
                            "runnable": not errors}
    if errors:
        md = ("**Not runnable** — " + str(len(errors)) + " error(s) in "
              + str(len(data["groups"])) + " rule(s):\n\n"
              + "\n".join(
                  f"- `{g['rule']}` × {g['count']} (e.g. {', '.join(g['examples'][:3])})"
                  for g in data["groups"]))
        if len(errors) > 50:
            md += (f"\n\n{len(errors) - 50} further error(s) omitted; "
                   f"`errors` has them all.")
    elif warnings:
        md = "Runnable, with warnings:\n\n" + \
            "\n".join(f"- {w['element']}: {w['message']}" for w in warnings)
    else:
        md = "Model is valid and runnable."
    return ok(data, markdown=md)


def _group_messages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse per-element messages into (rule, count, examples) rows.

    The rule key strips the element name, the numbers and the trailing
    ``[source, line]`` tag, so "Block 12 'A': must connect from something"
    and "Block 40 'B': must connect from something" are one group.
    """
    import re

    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        message = str(item.get("message", ""))
        rule = re.sub(r"\[[^\]]*\]\s*$", "", message).strip()
        rule = re.sub(r"'[^']*'", "'…'", rule)
        rule = re.sub(r"\b\d+\b", "N", rule)
        entry = groups.setdefault(rule, {"rule": rule, "count": 0,
                                         "examples": []})
        entry["count"] += 1
        element = str(item.get("element", ""))
        if element and element not in entry["examples"] and \
                len(entry["examples"]) < 5:
            entry["examples"].append(element)
    return sorted(groups.values(), key=lambda g: -g["count"])


@mcp.tool(annotations=READ_ONLY("Estimate runtime"))
@guarded
def estimate_runtime(model_id: str, num_runs: int = 10,
                     run_length: float | None = None,
                     workers: int | None = None,
                     importance: Literal["none", "improvement", "risk",
                                         "both"] | None = None
                     ) -> CallToolResult:
    """Estimate wall-clock time for a simulation before running it.

    Times a real pilot and extrapolates.  `workers` is taken into account
    with a contention-aware speed-up: parallel replications on a large
    model scale at roughly ``w / (1 + 0.25 (w - 1))``, not ``w`` (8 workers
    measured 2.8x on an 8-core box).  Models of 1,500+ elements are piloted
    at an 8,760 h horizon and the fit scaled, so the call always returns.

    `importance` overrides the model experiment's importance setting for the
    estimate. When importance is on, `seconds_estimate` is the TOTAL time
    until the whole operation finishes: the baseline batch plus one batch
    per block per variant kind. `baseline_seconds` and
    `importance_seconds` show the split.

    Returns {seconds_estimate, baseline_seconds, importance_seconds,
    total_batches, importance_batches, importance_blocks, range,
    per_run_ms, pilot_runs, estimate_seconds, cached, advice,
    storage_bytes_estimate, event_log_bytes_estimate, storage_note}: the
    last three say how much the run will write to disk (the availability
    plot is sampled at the model's own interval on every run).
    """
    ws = context.workspace()
    from raptor.api.estimate import estimate
    from raptor.api.limits import MAX_WORKERS
    if workers is not None and not 1 <= int(workers) <= MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")
    workers, _auto = resolve_workers(workers, ws.get(model_id).rbd, num_runs)
    est = estimate(ws, model_id, num_runs=num_runs, run_length=run_length,
                   workers=workers, importance=importance)
    data: dict[str, Any] = {
        "seconds_estimate": round(est.wall_seconds_est, 2),
        "baseline_seconds": round(est.baseline_wall_seconds, 2),
        "importance_seconds": round(est.importance_wall_seconds, 2),
        "importance_mode": est.importance_mode,
        "importance_blocks": est.importance_blocks,
        "importance_batches": est.importance_batches,
        "total_batches": est.total_batches,
        "range": {"low": round(est.low, 2), "high": round(est.high, 2)},
        "per_run_ms": round(est.per_run * 1000, 1),
        "pilot_runs": len(est.pilot_times),
        "setup_s": round(est.setup, 3),
        "estimate_seconds": round(est.estimate_seconds, 3),
        "cached": est.cached,
        "advice": est.advice,
        "storage_bytes_estimate": (None if est.storage_bytes_est is None
                                   else round(est.storage_bytes_est)),
        "event_log_bytes_estimate": (None if est.event_log_bytes_est is None
                                     else round(est.event_log_bytes_est)),
        "storage_note": est.storage_note,
    }
    split = ""
    if est.importance_batches:
        split = (f"\n\nBaseline **{data['baseline_seconds']:.2f} s** + "
                 f"importance **{data['importance_seconds']:.2f} s** "
                 f"({est.importance_mode}: {est.importance_blocks} blocks, "
                 f"{est.importance_batches} variant batches; "
                 f"{est.total_batches} total).")
    md = (f"Estimated **{data['seconds_estimate']:.2f} s** total until "
          f"completion for {num_runs} runs (range "
          f"{data['range']['low']:.2f}–{data['range']['high']:.2f} s), "
          f"from {data['pilot_runs']} pilot run(s) at "
          f"{data['per_run_ms']:.1f} ms/replication.{split}\n\n{est.advice}"
          + (f"\n\n{est.storage_note}" if est.storage_note else ""))
    return ok(data, markdown=md)


@mcp.tool(annotations=ADDITIVE("Simulate"))
@guarded
async def simulate(  # noqa: PLR0912, PLR0915
    model_id: str,
    num_runs: int | None = None,
    run_length: float | None = None,
    collect_events: bool = False,
    workers: int | None = None,
    date: str | None = None,
    timeout_s: float = 1800.0,
    confirm_long_run: bool = False,
    seed: int | None = None,
    hierarchy: str | int | None = None,
    treatments: dict | None = None,
    importance: Literal["none", "improvement", "risk", "both"] | None = None,
    availability_plot: bool | None = None,
    interim_cost: bool | None = None,
    attribution: bool = False,
    capacity_rule: Literal["proportional", "marginal", "first_failed"] = "proportional",
    precision: dict | None = None,
    ctx: Context | None = None,
) -> CallToolResult:
    """Run a simulation and return structured results.

    `attribution=True` also works out which element caused each system
    outage and why (repair, waiting for a spare, waiting for a crew,
    logistics, maintenance); read it with `downtime_drivers(run_id)` and
    `advise(model_id, run_id)`. The results themselves are unchanged. It
    keeps the event log in memory for the batch, so leave it off for very
    large runs. Not available with `hierarchy`.

    `capacity_rule` (with attribution on a capacity-analysis model) shares
    the lost flow among the elements that were down: `proportional` sums to
    the engine's own lost flow, `marginal` answers "what would fixing this
    one buy?" (shares need not add up), `first_failed` is for comparison.

    `availability_plot` / `interim_cost`: record the availability-over-time
    plot (needed by `get_timeseries(series="availability")`) or the interim
    cost report for this run. Left out, the model's own Recording settings
    decide, as in the GUI; they are off in a new model, because each one is
    a sampling timer inside the simulation that costs run time.

    `hierarchy` (a name or id) runs just that hierarchy on its own, its input
    always available -- useful for checking part of a model. This is a port
    extension, not a Raptor feature: the result carries an `isolation` object
    (hazards at the boundary, the treatment applied to each) and must not be
    read as a whole-model figure. `treatments` is `{deps: up|system|
    refuse, pools: full|share|refuse, streams: keep|separate, items: {...}}`;
    call `extract_hierarchy` with `report_only` first to see the hazards.

    Long runs send MCP progress notifications about once a second and can
    be cancelled by the client (the engine stops between replications).

    Returns {run_id, model_id, model_hash, runs, requested_runs, truncated,
    wall_seconds, summary, warnings, caveats}; `summary` has each metric
    with min/mean/max/stdev/sem. With capacity analysis on, `summary` adds
    Units_Produced, Mean_Flow, Mean_Capacity, Capacity_Weighted_Availability
    (Mean_Flow / FlowGenerated: the delivered fraction, where Availability
    only means some flow got through) and Lost_Flow.  `caveats` is the model-validation subset
    of `warnings` (surfaced separately so a client can show them first).
    `outages` is {outages, runs, up_time, between, few}: working time per
    system outage pooled over every run. MTBDE averages each run's own
    figure, and a run with no outage gives its whole length, so with rare
    outages quote `between` (and say "rough" when `few`) rather than MTBDE.
    The Markdown body is a table with units in the headers.

    The model is **validated first**; structural errors are returned with
    the issue list instead of running.  The run is estimated first and
    refused with a clear message if the estimate exceeds `timeout_s`,
    unless `confirm_long_run=true`.  `workers > 1` is faster but not
    bit-identical to a sequential batch.  Leave `workers` out and a large
    model (500+ blocks, events and nodes) run 20+ times uses several cores
    (half the machine's, at most 8); the result reports the `workers` used
    and `workers_auto`. Pass `workers=1` for the sequential numbers. This is
    a server default only: the desktop app and the command line are unchanged.

    `seed` (optional, non-negative) re-rolls the batch reproducibly;
    leaving it unset keeps the engine's fixed seeds, under which the same
    model and run count always return byte-identical numbers.  If a run is
    cut short by `timeout_s`, `runs` is below the requested count and
    `truncated` is true -- do not read a truncated batch as complete.

    `precision` runs in growing batches until the watched metric is
    precise enough, instead of a fixed count: `{relative: 0.1, metric:
    "system.ao", on: "auto" | "unavailability" | "value", confidence:
    0.95, min_trials: 50, max_trials: 20000, batch: 50}`. `on` "auto" (the
    default) watches 1 - value for a probability (system.ao, system.r,
    system.capacity) and the value for a time (system.mtbde, system.mdt).
    The result carries a `precision` object with the reached interval
    (`watched` names what `mean` is; `value` is the metric itself), the
    target, the trial count and the pass history; `relative` is a fraction
    (0.1 = 10 %).

    Capacity: delivered flow is limited by link `nom_flow`/`max_flow`
    caps, and those caps only bind on links entering a node whose
    unrestricted flow is off (`full_flow: false`).  A model with no such
    boundary node reports no lost flow.
    """
    ws = context.workspace()
    _validate_run_args(timeout_s, date, workers)
    cap = context.max_runs()
    handle = ws.get(model_id)
    requested_workers = workers
    # The effective count is the explicit argument, else the model's own
    # NumRuns. Checking only the argument let a configured 6000-run model
    # bypass a 5000 cap; 0/negative also slipped through as a successful
    # empty batch.
    from raptor.api.run import (
        model_num_runs, model_units, run as api_run, run_isolated)
    from raptor.api.markdown import summary_table

    effective_runs = (num_runs if num_runs is not None
                      else model_num_runs(handle.rbd, strict=True))
    if effective_runs is None:
        raise ValueError(
            "the model has no valid NumRuns; pass num_runs explicitly")
    if effective_runs < 1:
        raise ValueError("num_runs must be at least 1")
    if effective_runs > cap:
        raise ValueError(
            f"num_runs={effective_runs} exceeds the server cap of "
            f"{cap} (--max-runs). Lower num_runs or raise the cap."
        )

    workers, workers_auto = resolve_workers(
        requested_workers, handle.rbd, effective_runs)

    # Validate the model that will actually run. For an isolated run that
    # is the extracted hierarchy, not the whole source: the rest of the
    # model may be mid-edit, and its parent-level wiring is never used.
    from raptor.api.validate import issues
    found = [] if hierarchy is not None else issues(handle.rbd)
    errors = [e for e in found if e.severity == "error"]
    if errors:
        raise ValueError(
            "model failed validation; fix these before running:\n"
            + "\n".join(str(e) for e in errors[:10])
        )
    validation_warnings = [str(e) for e in found if e.severity == "warning"]


    # Importance measures are an experiment setting, so they travel with the
    # model into `run()` -- including through `run_isolated`, which builds
    # its extract from this handle. Requested per call so an assistant can
    # ask for them without editing the model first.
    if importance is not None:
        if hierarchy is not None:
            raise ValueError(
                "importance measures are not available for an isolated "
                "hierarchy; run the whole model")
        from raptor.core import run_experiment as _rx
        experiment = dict(getattr(handle.rbd, "experiment", None) or {})
        experiment["importance"] = importance
        handle.rbd.experiment = _rx.normalise(experiment)

    def _go(abort, progress):
        if precision is not None:
            if hierarchy is not None:
                raise ValueError(
                    "precision and hierarchy cannot be combined yet")
            from raptor.api.scenarios import Precision, run_to_precision
            try:
                wanted = Precision(**precision)
                wanted.field()
            except TypeError as exc:
                raise ValueError(f"bad precision object: {exc}") from exc
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            if wanted.max_trials > cap:
                raise ValueError(
                    f"precision.max_trials={wanted.max_trials} exceeds the "
                    f"server cap of {cap}; lower it or raise the cap")
            outcome = run_to_precision(
                ws, model_id, wanted, workers=workers, seed=seed,
                progress=lambda done, _total, _text: progress(done, _total),
                abort=abort, timeout_s=timeout_s,
                confirm_long_run=confirm_long_run, collect_events=collect_events,
                availability_plot=availability_plot, interim_cost=interim_cost,
                attribution=attribution, capacity_rule=capacity_rule)
            result = outcome.result
            result.precision = outcome.to_dict()
            return result
        kwargs = dict(
            num_runs=num_runs, run_length=run_length, workers=workers,
            timeout_s=timeout_s, collect_events=collect_events,
            confirm_long_run=confirm_long_run, date=date, abort=abort,
            progress=progress, seed=seed,
            availability_plot=availability_plot, interim_cost=interim_cost)
        if hierarchy is not None:
            from raptor.api import isolate
            try:
                return run_isolated(ws, model_id, hierarchy,
                                    treatments=treatments, **kwargs)
            except isolate.IsolationError as exc:
                raise ValueError(str(exc)) from exc
        return api_run(ws, model_id, attribution=attribution,
                       capacity_rule=capacity_rule, **kwargs)

    result = await run_job(ctx, _go, label="Simulating")
    warnings = list(result.warnings) + validation_warnings
    # `requested` must come from the caller or the model's own configured
    # run count -- falling back to `result.num_runs` would make the
    # truncation check vacuous whenever `num_runs` is omitted.
    requested = (result.num_runs if precision is not None else effective_runs)
    truncated = (False if precision is not None
                 else requested is not None and result.num_runs < requested)
    data = {
        "run_id": result.run_id,
        "model_id": result.model_id,
        "model_hash": result.model_hash,
        "runs": result.num_runs,
        "requested_runs": requested,
        "truncated": truncated,
        "wall_seconds": round(result.wall_seconds, 3),
        "estimate_seconds": round(
            getattr(result, "estimate_seconds", 0.0) or 0.0, 3),
        "estimated_wall_seconds": getattr(
            result, "estimated_wall_seconds", None),
        "total_seconds": round(
            result.wall_seconds
            + (getattr(result, "estimate_seconds", 0.0) or 0.0), 3),
        "summary": result.to_dict()["metrics"],
        "units": model_units(handle.rbd) or "",
        "display_unit": (getattr(handle.rbd, "display_unit", None)
                         or model_units(handle.rbd) or ""),
        "warnings": warnings,
        # Model-level assumptions that can make the numbers misleading
        # (e.g. capacity analysis on but no boundary node).  Also in
        # `warnings`; separated so a client can surface them prominently.
        "caveats": validation_warnings,
        "workers": workers,
        "workers_auto": workers_auto,
    }
    if getattr(result, "isolation", None):
        data["isolation"] = result.isolation
    if getattr(result, "importance", None):
        data["importance"] = result.importance
    if getattr(result, "precision", None):
        data["precision"] = result.precision
    from raptor.api.presentation import pooled_outages
    rate = pooled_outages(result.per_run)
    if rate is not None:
        # MTBDE is a mean of per-run figures (a floor when outages are rare)
        data["outages"] = rate.to_dict()
    md = summary_table(
        result,
        display_unit=getattr(handle.rbd, "display_unit", None),
        native_unit=model_units(handle.rbd),
    )
    if getattr(result, "isolation", None):
        md = f"**{result.isolation['banner']}**\n\n" + md
    if rate is not None:
        md += "\n\n" + rate.sentence(data["units"])
    if "Capacity_Weighted_Availability" in data["summary"]:
        md += ("\n\nCapacity analysis: `Availability` counts any flow reaching "
               "the end; `Capacity_Weighted_Availability` (Mean_Flow / "
               "FlowGenerated) is the delivered fraction, and `Lost_Flow` the "
               "flow not delivered.")
    if workers_auto:
        md += (f"\n\nRan on {workers} cores (chosen for a large model; pass "
               "`workers=1` for the sequential numbers).")
    if truncated:
        md = (f"**Partial batch: only {result.num_runs} of {requested} runs "
              f"completed** (timeout or abort) -- these estimates are noisier "
              f"than requested. Re-run with a larger `timeout_s`, fewer runs, "
              f"or `confirm_long_run=true`.\n\n" + md)
    if validation_warnings:
        md = ("**Read these caveats before trusting the numbers:**\n"
              + "\n".join(f"- {w}" for w in validation_warnings)
              + "\n\n" + md)
    other = [w for w in warnings if w not in validation_warnings]
    if other:
        md += "\n\n" + "\n".join(f"- {w}" for w in other)
    if getattr(result, "importance", None):
        from raptor.api.importance import DEFAULT_METRIC, table_lines
        md += ("\n\n**Importance measures** (IEC 61078 Annex D, estimated by "
               "re-simulation):\n\n```\n"
               + "\n".join(table_lines(result.importance, DEFAULT_METRIC,
                                      limit=20))
               + "\n```")
    return ok(data, markdown=md)


@mcp.tool(annotations=ADDITIVE("Start run"))
@guarded
def start_run(
    model_id: str,
    num_runs: int | None = None,
    run_length: float | None = None,
    workers: int | None = None,
    timeout_s: float = 1800.0,
    collect_events: bool = False,
    seed: int | None = None,
    availability_plot: bool | None = None,
    interim_cost: bool | None = None,
    attribution: bool = False,
    capacity_rule: Literal["proportional", "marginal", "first_failed"] = "proportional",
) -> CallToolResult:
    """Start a simulation in the background and return a job id immediately.

    `attribution=True` also records what caused the downtime, as in
    `simulate`; read it with `downtime_drivers` once the job is done.
    `capacity_rule` chooses how the lost flow is shared, as in `simulate`.

    Same arguments as `simulate`, minus the hierarchy/isolation and
    importance options. Use this for batches that outlive one tool call (a
    100-replication, 20-year run is tens of minutes): the engine runs on a
    daemon thread, `run_status` reports progress, `cancel_run` stops it
    between replications, and the completed run is an ordinary workspace run
    (`run_id`) whose results `get_run_results`, `get_table` and `get_report`
    can read.

    The model is validated and the batch size checked *before* the job
    starts, so a structural error is reported here rather than asynchronously.

    `workers` defaults as it does for `simulate`: a large model run 20+ times
    uses several cores unless you pass `workers=1`.

    Returns {job_id, state, phase, setup, requested_runs, ...}. `run_status`
    shows `phase` (`setting_up` while a big model is being prepared, then
    `simulating`) and `setup` progress, so a long quiet start is not a hang.
    """
    _validate_run_args(timeout_s, None, workers)
    ws = context.workspace()
    handle = ws.get(model_id)
    cap = context.max_runs()
    from raptor.api.run import model_num_runs
    effective_runs = (num_runs if num_runs is not None
                      else model_num_runs(handle.rbd, strict=True))
    if effective_runs is None:
        raise ValueError("the model has no valid NumRuns; pass num_runs explicitly")
    if effective_runs < 1:
        raise ValueError("num_runs must be at least 1")
    if effective_runs > cap:
        raise ValueError(
            f"num_runs={effective_runs} exceeds the server cap of {cap} "
            f"(--max-runs). Lower num_runs or raise the cap.")

    from raptor.api.validate import issues
    errors = [e for e in issues(handle.rbd) if e.severity == "error"]
    if errors:
        raise ValueError(
            "model failed validation; fix these before running:\n"
            + "\n".join(str(e) for e in errors[:10]))

    from raptor.api.run import run as api_run
    from raptor.mcp import async_runs

    workers, workers_auto = resolve_workers(workers, handle.rbd, effective_runs)

    def work(abort, progress, setup_progress, insight_progress):
        return api_run(ws, model_id, num_runs=effective_runs,
                       run_length=run_length, workers=workers,
                       timeout_s=timeout_s, collect_events=collect_events,
                       confirm_long_run=True, abort=abort, progress=progress,
                       setup_progress=setup_progress, seed=seed,
                       insight_progress=insight_progress,
                       availability_plot=availability_plot,
                       attribution=attribution,
                       capacity_rule=capacity_rule,
                       interim_cost=interim_cost)

    job = async_runs.start(work, model_id=model_id, total=effective_runs)
    data = job.snapshot()
    data["workers"] = workers
    data["workers_auto"] = workers_auto
    md = (f"Started job `{job.job_id}` for {effective_runs} run(s) of "
          f"{run_length or 'the model'}'s run length"
          + (f" on {workers} cores" if workers_auto else "")
          + ". Poll `run_status` with that id.")
    return ok(data, markdown=md)


@mcp.tool(annotations=READ_ONLY("Run status"))
@guarded
def run_status(job_id: str) -> CallToolResult:
    """Progress of a job started with `start_run`.

    Returns {job_id, model_id, state, phase, setup, completed_runs,
    requested_runs, percent, elapsed_s, wall_seconds, run_id, summary,
    error}. `state` is queued, running, done, cancelled or failed. `phase`
    adds what a running job is doing: `setting_up` while a large model is
    prepared (`setup` then has `{stage, done, total, percent}`; it can take
    minutes before the first replication, and `completed_runs` stays 0), then
    `simulating`, then with attribution `attributing` (`attributing` has
    `{label, done, total}`). Small models skip straight to `simulating` and `setup` is
    null. `run_id` is set once the
    batch has produced a run, and `summary` then carries the mean of each
    metric. Jobs are in-process only -- after a server restart the run is
    still in the workspace, the job id is not.
    """
    from raptor.mcp import async_runs
    job = async_runs.get(job_id)
    if job is None:
        raise ValueError(
            f"unknown job {job_id!r}; jobs live in this server process only "
            f"(the run itself is in the workspace, use get_run_results)")
    data = job.snapshot()
    md = (f"Job `{job.job_id}`: **{data['state']}** — "
          f"{data['completed_runs']}/{data['requested_runs']} runs, "
          f"{data['elapsed_s']}s elapsed")
    if data["phase"] == "setting_up" and data["setup"]:
        s = data["setup"]
        md += f"; setting up: {s['stage']} {s['done']}/{s['total']}"
    if data["phase"] == "attributing" and data["attributing"]:
        a = data["attributing"]
        md += f"; {a['label'].lower()}: {a['done']}/{a['total']}"
    if data["run_id"]:
        md += f", run `{data['run_id']}`"
    if data["error"]:
        md += f"\n\n{data['error']}"
    return ok(data, markdown=md)


@mcp.tool(annotations=ADDITIVE("Cancel run"))
@guarded
def cancel_run(job_id: str) -> CallToolResult:
    """Ask a background job to stop (checked between replications, and
    while a large model is still being set up).

    The replications already completed are kept and become a run, exactly
    as when `simulate` is cut short: the result is marked truncated. Returns
    the job snapshot; poll `run_status` until `state` is `cancelled`.
    """
    from raptor.mcp import async_runs
    job = async_runs.cancel(job_id)
    if job is None:
        raise ValueError(f"unknown job {job_id!r}")
    return ok(job.snapshot(),
              markdown=f"Cancellation requested for `{job_id}`.")


@mcp.tool(annotations=READ_ONLY("Run results"))
@guarded
def get_run_results(run_id: str, top: int = 10,
                    per_run_detail: bool = False) -> CallToolResult:
    """One completed run as a single assessable bundle.

    Reads what the run wrote: the metric means and per-replication figures,
    the capacity summary, and the weakest elements (`weak_link`). Use it to
    judge a run without paging through `get_report`, and `get_table` /
    `export_tables` (with the same `run_id`) when you need a whole table or
    a workbook.

    `top` caps the weak-link list (1..100). `per_run` carries each
    replication's system figures; `per_run_detail=True` adds the nested
    per-element data, which on a large model is megabytes (use `get_table`
    with `results.per_run` to page it instead).
    """
    from raptor.api.run import get_report as _report, run_dir
    from raptor.api.run_tables import read_per_run, read_summary
    ws = context.workspace()
    runs_dir = run_dir(ws, run_id)
    if not runs_dir.is_dir():
        raise ValueError(f"run {run_id!r} not found")

    # The same validated readers the table and metric tools use: a summary
    # for another run, or corrupt per-run data, is metadata trouble and is
    # reported as such rather than silently producing a partial bundle.
    summary = read_summary(runs_dir, run_id)
    metrics = {
        name: values["mean"]
        for name, values in (summary.get("metrics") or {}).items()
        if isinstance(values, dict) and values.get("mean") is not None
    }
    per_run = read_per_run(runs_dir, run_id) or []
    if not per_run_detail:
        # the nested per-element lists made this 2 MB for a 500-block model
        # with 20 runs, beside a 700-character text answer
        per_run = [{k: v for k, v in row.items()
                    if not isinstance(v, (list, dict))} for row in per_run]

    capacity: dict[str, Any] = {}
    path = runs_dir / "final_results.txt"
    if path.is_file():
        for line in path.read_text().splitlines():
            parts = line.split()
            if parts and parts[0] in ("Mean_Flow", "Mean_Capacity",
                                      "System_Failures", "Availability",
                                      "Units_Produced") and len(parts) > 2:
                try:
                    capacity[parts[0]] = float(parts[1])
                except ValueError:
                    pass

    weak: list[str] = []
    _, limit = context.page_args(0, top, 100)
    try:
        section = _report(ws, run_id, section="weak_link", limit=limit)
        weak = list(section.get("lines") or [])[:limit]
    except Exception:  # noqa: BLE001 - weak link needs block statistics
        weak = []

    data = {
        "run_id": run_id,
        "model_id": summary.get("model_id"),
        "model_name": summary.get("model_name"),
        "runs": summary.get("num_runs"),
        "units": summary.get("units"),
        "metrics": metrics,
        "capacity": capacity,
        "per_run": per_run,
        "weak_link": weak,
        "files": sorted(p.name for p in runs_dir.iterdir() if p.is_file()),
    }
    md = (f"Run `{run_id}` — {data['runs']} replication(s) of "
          f"{data['model_name']}.\n\n")
    if metrics:
        md += "| metric | mean |\n|---|---|\n"
        for name, value in metrics.items():
            md += f"| {name.replace('_', ' ')} | {value:.6g} |\n"
    if "Mean_Capacity" in capacity:
        md += f"\nMean capacity: **{capacity['Mean_Capacity']:.2f}**"
    if weak:
        md += "\n\n**Weakest elements**\n\n```\n" + "\n".join(weak) + "\n```"
    return ok(data, markdown=md)


@mcp.tool(annotations=READ_ONLY("Get report"))
@guarded
def get_report(
    run_id: str,
    section: Literal["final_results", "end_of_run", "events", "capacity",
                     "cost", "weak_link", "importance",
                     "availability_plot"] = "final_results",
    run_number: int | None = None,
    format: Literal["markdown", "raw"] = "markdown",
    offset: int = 0,
    limit: int = 200,
) -> CallToolResult:
    """Get a paginated report section from a completed run.

    section: 'final_results', 'end_of_run', 'events', 'capacity', 'cost',
    'weak_link', 'importance' (only when the run asked for it) or
    'availability_plot'.  `run_number` selects one
    replication where the section is per-run.  format: 'markdown'
    (default) or 'raw'.  Returns {lines, total, next_offset, markdown};
    `lines` never exceeds `limit` (≤200).
    """
    ws = context.workspace()
    offset, limit = context.page_args(offset, limit, 200)
    from raptor.api.run import get_report as api_report, isolation_metadata
    report = api_report(
        ws, run_id, section=section, offset=offset, limit=limit,
        run_number=run_number,
    )
    raw = "\n".join(report["lines"])
    md = raw
    # A page is not a section: rendering Markdown from page 2 yields bare
    # rows with no header, and page 1 may end mid-table. Keep paged
    # responses raw; render only a complete section.
    if format == "markdown" and offset == 0 and report["next_offset"] is None:
        from raptor.api.markdown import report_markdown
        md = report_markdown(raw, section=section)
    data = {
        "section": section,
        "total": report["total"],
        "next_offset": report["next_offset"],
        "lines": report["lines"],
    }
    isolation = isolation_metadata(context.workspace(), run_id)
    if isolation:
        data["isolation"] = isolation
    for key in ("columns", "rows"):
        if key in report:
            data[key] = report[key]
    return ok(data, markdown=md)


@mcp.tool(annotations=READ_ONLY("Single point failures"))
@guarded
def single_point_failures(model_id: str, workers: int = 1,
                          limit: int = 100,
                          offset: int = 0) -> CallToolResult:
    """Find single-point failures in the model.

    A single point failure is an element whose failure alone takes the
    system down, found as Raptor does (by failing each element in Failure
    Effects View, so dependencies and cold standby count). Returns
    {spofs, count, total, offset, next_offset, elapsed_seconds, cached};
    each spof is {kind, id, name, crit} plus `phase` for a phased model.

    `limit` pages the response (default 100, use 0 for all); the full row
    set is still computed and cached by model content, so later pages and
    repeated calls are instant. `workers > 1` parallelises the candidate
    tests on an unphased model.
    """
    import time as _time

    from raptor.api.limits import MAX_WORKERS
    if not 1 <= int(workers) <= MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")
    if int(limit) < 0 or int(offset) < 0:
        raise ValueError("limit and offset must be non-negative")
    ws = context.workspace()
    from raptor.api.analyse import single_point_failures as spf
    from raptor.api.run import model_hash as _hash
    cache_path = (ws.root / ".spf_cache"
                  / f"{_hash(ws.get(model_id).rbd)}.json")
    cached = cache_path.is_file()
    started = _time.monotonic()
    rows = spf(ws, model_id, workers=workers)
    elapsed = _time.monotonic() - started
    total = len(rows)
    page = rows if int(limit) == 0 else rows[offset:offset + int(limit)]
    phased = any("phase" in r for r in rows)
    md = markdown_table((["phase"] if phased else []) + ["kind", "id", "name"],
                        [([r.get("phase", "")] if phased else [])
                         + [r["kind"], r["id"], r["name"]] for r in page]) \
        if page else "*No single points of failure.*"
    end = offset + len(page)
    next_offset = None if end >= total else end
    if len(page) < total:
        md += (f"\n\n_Showing {offset + 1}-{offset + len(page)} of {total}; "
               f"call again with offset={offset + len(page)}._")
    data: dict[str, Any] = {"spofs": page, "count": len(page), "total": total,
            "offset": offset, "next_offset": next_offset,
            "elapsed_seconds": round(elapsed, 3), "cached": cached}
    return ok(data, markdown=md)


@mcp.tool(annotations=READ_ONLY("Failure effects"))
@guarded
def failure_effects(
    model_id: str,
    fail: list[str | int] | None = None,
    repair: list[str | int] | None = None,
    phase: int | None = None,
) -> CallToolResult:
    """Run failure-effects analysis (Raptor workbook Chapter 10).

    Fails and/or repairs the listed blocks or events and reports the resulting
    system status and the Failure Effects text (failed, induced, idle and PM
    components, down and degraded nodes). A phased model starts in phase 1;
    `phase` moves it to another phase first. Returns {system_status, affected,
    log, effects, criticality, phase}, plus, for a model with capacity
    analysis, `capacity`: {flow_generated, delivered, lost,
    delivered_fraction, system_capacity, nodes: [{id, name, flow,
    capacity}]} -- the flow the state delivers, as capacity analysis would
    record it.

    Each call is an **independent scenario starting from the all-good
    state**: it does not accumulate failures across calls.  To test
    several simultaneous failures, name them all in one `fail` list; a
    `repair` can only undo a failure named in the same call.
    """
    ws = context.workspace()
    from raptor.api.analyse import failure_effects as fev
    result = fev(ws, model_id, fail=fail or [], repair=repair or [], phase=phase)
    md = f"System status: **{result['system_status']}**"
    if result.get("capacity"):
        flow = result["capacity"]
        md += (f"\n\nDelivered flow: **{flow['delivered']}** of "
               f"{flow['flow_generated']} (lost {flow['lost']})")
    if result.get("affected"):
        md += "\n\nAffected: " + ", ".join(result["affected"])
    if result.get("log"):
        md += "\n\n```\n" + "\n".join(result["log"][:80]) + "\n```"
    return ok(result, markdown=md)


@mcp.tool(annotations=READ_ONLY("Failure effects, many scenarios"))
@guarded
def failure_effects_batch(
    model_id: str,
    scenarios: list[dict[str, Any]],
    include_log: bool = False,
) -> CallToolResult:
    """Run many failure-effects scenarios in one call, on one model set-up.

    `scenarios` is a list of `{"fail": [...], "repair": [...], "phase": N}`
    (every key optional), up to 2000. Each scenario is independent and starts
    from the all-good state, exactly as a separate `failure_effects` call
    would; the model is only set up once, so hundreds of combinations on a
    large model take seconds, not one set-up each.

    Returns {model_id, count, failed, scenarios}. `scenarios[i]` has the
    scenario's `index`, `fail` and `repair`, then either its result
    ({system_status, affected, effects, criticality, phase}, `capacity` for
    a model with capacity analysis, plus the `log` text when `include_log`
    is true) or `{"error": ...}` when that scenario
    could not be applied (an unknown or already-failed element, a bad
    phase). A bad scenario does not stop the others. `failed` counts them.
    """
    ws = context.workspace()
    from raptor.api.analyse import AnalysisError, failure_effects_many
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenarios must be a non-empty list of "
                         "{fail, repair, phase} objects")
    for i, sc in enumerate(scenarios):
        if not isinstance(sc, dict):
            raise ValueError(f"scenarios[{i}] must be an object with fail / "
                             "repair / phase")
        unknown = sorted(set(sc) - {"fail", "repair", "phase"})
        if unknown:
            raise ValueError(f"scenarios[{i}] has unknown keys {unknown}; "
                             "use fail, repair and phase")
    try:
        rows = failure_effects_many(ws, model_id, scenarios,
                                    include_log=include_log)
    except AnalysisError as exc:
        raise ValueError(str(exc)) from exc
    failed = sum(1 for r in rows if "error" in r)
    data = {"model_id": model_id, "count": len(rows), "failed": failed,
            "scenarios": rows}
    flow = any("capacity" in r for r in rows)
    table = markdown_table(
        ["#", "fail", "repair", "system status"] + (["delivered"] if flow else []),
        [[r["index"], ", ".join(map(str, r["fail"])),
          ", ".join(map(str, r["repair"])),
          r.get("system_status") or f"error: {r['error']}"]
         + ([r["capacity"]["delivered"] if "capacity" in r else ""] if flow else [])
         for r in rows[:50]])
    md = (f"{len(rows)} scenario(s), {failed} that could not be applied.\n\n"
          + table + ("\n\n(first 50 shown)" if len(rows) > 50 else ""))
    return ok(data, markdown=md)


@mcp.tool(annotations=ADDITIVE("Compare models"))
@guarded
async def compare_models(
    models: list[str],
    num_runs: int = 5,
    metrics: list[str] | None = None,
    ctx: Context | None = None,
) -> CallToolResult:
    """Compare 2–5 models or runs side by side.

    Returns per-metric values (`metrics`, in `models`/`labels` order),
    deltas against the first entry, and which entry is best per metric,
    plus a Markdown comparison table. `labels` gives each entry's model
    name (a run's cached `model_name`), parallel to `models` -- `deltas`
    keys and `best` values use the label, not the raw id, unless two
    entries share a name, in which case that label gets its id appended
    to stay unique.
    """
    ws = context.workspace()
    from raptor.api.analyse import compare
    from raptor.api.markdown import comparison_table

    if len(models) > 5:
        raise ValueError("compare_models accepts at most 5 models or runs")
    cap = context.max_runs()
    if num_runs > cap:
        raise ValueError(f"num_runs={num_runs} exceeds the server cap of {cap}")
    result = await run_job(
        ctx,
        lambda abort, progress: compare(
            ws, models, num_runs=num_runs, metrics=metrics,
            abort=abort, progress=progress),
        label="Comparing",
    )
    result["markdown"] = comparison_table(result)
    return ok(result, markdown=result["markdown"])


# ══════════════════════════════════════════════════════════════════════
# What-if
# ══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations=ADDITIVE("Sweep parameter"))
@guarded
async def sweep_parameter(
    model_id: str,
    selector: str,
    field: str,
    values: list[Any],
    num_runs: int = 5,
    metrics: list[str] | None = None,
    ctx: Context | None = None,
) -> CallToolResult:
    """Sweep a parameter across values and measure the impact.

    Works on copies — the original model is untouched.  `field` may be a
    friendly field or an indexed form like `repair_params[0]`.  Each row
    carries the `run_id` and the `runs` count that produced it.  Returns
    {rows, monotonic, all_rows_identical, warnings} plus a Markdown table;
    `all_rows_identical` is true when every row's metrics are bit-identical
    (which cannot be told apart from "the value was never applied"), and
    `warnings` says so.  `monotonic` looks at the first swept metric and
    treats a flat or single-row series as monotonic; `all_rows_identical`
    needs at least two rows.  Concurrent sweeps are serialised, so two in
    one batch both succeed.
    """
    ws = context.workspace()
    from raptor.api.analyse import sweep
    from raptor.api.markdown import sweep_table

    if len(values) > 12:
        raise ValueError("a sweep accepts at most 12 values")
    cap = context.max_runs()
    if num_runs > cap:
        raise ValueError(f"num_runs={num_runs} exceeds the server cap of {cap}")
    result = await run_job(
        ctx,
        lambda abort, progress: sweep(
            ws, model_id, selector, field, values,
            num_runs=num_runs, metrics=metrics,
            abort=abort, progress=progress),
        label="Sweeping",
    )
    md = sweep_table(result)
    for w in result.get("warnings", []):
        md += f"\n\n**Note:** {w}"
    result["markdown"] = md
    return ok(result, markdown=md)


# ══════════════════════════════════════════════════════════════════════
# Timeseries
# ══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations=READ_ONLY("Get timeseries"))
@guarded
def get_timeseries(
    run_id: str,
    series: Literal["availability", "capacity", "cost"] = "availability",
    runs: str = "mean",
    points: int = 100,
) -> CallToolResult:
    """Get time-series data from a completed run, for the client to plot.

    series: 'availability', 'capacity' or 'cost'.  runs: 'mean' (default),
    'all', or a specific run number.  points: max samples after decimation
    (default 100).

    Returns {t, values, runs_included, decimated_from, envelope?} plus a
    short Markdown preview.  No image is ever returned.  For 'capacity',
    `values` is the flow and `capacity` the system capacity, null where
    nothing limits the flow (Raptor's 999,999,999), with `capacity_note`.
    """
    ws = context.workspace()
    from raptor.api.limits import MAX_TIMESERIES_POINTS
    from raptor.api.run import get_timeseries as api_series, isolation_metadata

    points = max(1, min(int(points), MAX_TIMESERIES_POINTS))
    data = api_series(ws, run_id, series=series, runs=runs, points=points)
    isolation = isolation_metadata(context.workspace(), run_id)
    if isolation:
        data["isolation"] = isolation
    md = data.pop("markdown", None)
    if isolation:
        md = ("**Isolated hierarchy** — " + isolation.get("hierarchy", "")
              + ". Not a whole-model result.\n\n" + (md or ""))
    if md is None:
        preview = list(zip(data["t"][:15], data["values"][:15]))
        md = markdown_table(["t", series], [list(p) for p in preview]) \
            if preview else "*No data.*"
    return ok(data, markdown=md)


@mcp.tool(annotations=ADDITIVE("Extract hierarchy"))
@guarded
def extract_hierarchy(
    model_id: str,
    hierarchy: str | int,
    treatments: dict | None = None,
    report_only: bool = False,
    name: str | None = None,
) -> CallToolResult:
    """Build a standalone model from one hierarchy (a port extension), or just
    list what crosses its boundary.

    The hierarchy's In node becomes Start and its Out node End, so its input is
    always available. Hazards at the boundary -- elements following something
    outside, spare and resource pools shared with outside blocks, random streams
    shared with outside elements -- each get a documented treatment; see
    `simulate`'s `treatments`. With `report_only` nothing is created and the
    hazards (with the treatment each would get) are returned. Otherwise the
    extract is registered as a new model: returns {model_id, name, isolation}.
    The source model is not changed.
    """
    from raptor.api import isolate
    if not report_only:
        context.require_write()
    ws = context.workspace()
    rbd = ws.get(model_id).rbd
    try:
        if report_only:
            hazards = isolate.scan(rbd, hierarchy, treatments)
            md = markdown_table(
                ["kind", "elements", "detail", "treatment", "effect"],
                [[h.kind, ", ".join(h.elements), h.detail, h.treatment, h.effect]
                 for h in hazards]) if hazards else "*Nothing crosses the hierarchy's boundary.*"
            return ok({"hazards": [h.to_dict() for h in hazards], "count": len(hazards)},
                      markdown=md)
        extracted, record = isolate.extract(rbd, hierarchy, treatments)
    except isolate.IsolationError as exc:
        raise ValueError(str(exc)) from exc
    created_name = name or f"{record.hierarchy}_isolated"
    info = ws.register_model(extracted, name=created_name,
                             source=f"isolated hierarchy {record.hierarchy}")
    return ok({"model_id": info["model_id"], "name": created_name,
               "isolation": record.to_dict()},
              markdown=f"**{record.banner}**\n\nNew model `{info['model_id']}`.\n\n"
                       + record.text())


@mcp.tool(annotations=ADDITIVE("What-if"))
@guarded
async def what_if(
    model_id: str,
    changes: list[str],
    name: str = "Scenario",
    trials: int | None = None,
    seed: int = 1,
    precision: dict | None = None,
    requirements: list[dict] | None = None,
    workers: int | None = None,
    timeout_s: float = 1800.0,
    ctx: Context | None = None,
) -> CallToolResult:
    """Compare a changed model with Base using paired trials.

    `changes` are PATH=VALUE edits (the grammar of `raptor set`), e.g.
    `["block:Pump.repair_params=8,0,0"]`. Base and the scenario run with the
    same seed, so trial *i* is paired; the result reports each metric's
    scenario value, the paired difference with its standard error, the
    chance the scenario is better, and how much variance pairing cut (often
    little: the random streams run on between trials, so trials stay in step
    only until the change first moves an event; the standard error is from
    the paired differences, so it is honest either way). The
    open model is never modified.

    `trials` sets the fixed count; `precision` (`{relative: 0.1,
    min_trials: 50, max_trials: 20000}`) sizes Base first with
    run-to-precision. `requirements` are `{metric, op, value, label}` rows
    judged for Base and the scenario. Long runs report progress and can be
    cancelled by the client.
    """
    ws = context.workspace()
    cap = context.max_runs()
    context.check_batch(changes, "changes")
    if precision is None:
        context.check_study(model_id, trials, timeout_s, workers=workers)
    else:
        _validate_run_args(timeout_s, None, workers)
    wanted = None
    if precision is not None:
        from raptor.api.scenarios.stopping import Precision
        try:
            wanted = Precision(**precision)
            wanted.field()
        except TypeError as exc:
            raise ValueError(f"bad precision object: {exc}") from exc
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if wanted.max_trials > cap:
            raise ValueError(
                f"precision.max_trials={wanted.max_trials} exceeds the server "
                f"cap of {cap}")
    from raptor.api.scenarios import Scenario, compare

    def work(abort, progress):
        return compare(
            ws, model_id, [Scenario(name=name, changes=tuple(changes))],
            trials=trials, precision=wanted, workers=max(1, workers or 1),
            seed=seed, timeout_s=timeout_s, requirements=requirements,
            progress=lambda done, total, label: progress(done, total, label),
            abort=abort)

    comparison = await run_job(ctx, work, label="Comparing scenario")
    failed = comparison.rows[0].get("error") if comparison.rows else None
    if failed:
        # an error, not a success whose text says nothing ran: a pipeline
        # checking is_error took a bad change for a finished comparison
        raise ValueError(f"The scenario did not run: {failed}")
    data = comparison.to_dict()
    data["caption"] = comparison.caption
    return ok(data, _what_if_markdown(comparison))


@mcp.tool(annotations=ADDITIVE("Recommend changes"))
@guarded
async def recommend_changes(
    model_id: str,
    run_id: str | None = None,
    top: int = 3,
    trials: int | None = None,
    seed: int = 1,
    workers: int | None = None,
    timeout_s: float = 1800.0,
    min_gain: float | None = None,
    ctx: Context | None = None,
) -> CallToolResult:
    """Propose and test one change for each top downtime driver.

    Finds the `top` blocks causing the most system downtime (from `run_id`,
    a stored run made with `attribution=True`, or else from a Base run with
    attribution made first), proposes a fixed-step lever from the cause that
    dominates each one's outages (halve the repair time or the logistics
    delays, one more spare, one more crew, maintenance half as often, a part
    that lasts twice as long), and tests every change against Base with
    paired trials. Each row gives the PATH=VALUE change (apply it with
    `update_element`/`manage_spare_pools`/`manage_resources`, or `raptor
    set`), the paired availability gain with its standard error and chance
    of being better, the downtime avoided per run, the unit cost the model
    holds (null when it holds none), what is left to weigh, and a verdict of
    what was MEASURED: "clearly helps" (a real gain, at a confidence shared
    across all the changes tested, that removes at least 5 % of Base's
    downtime or `min_gain` availability, a fraction), "helps a little" (real
    but smaller), "may help; run more trials", "no measurable gain" or
    "makes it worse". No verdict weighs cost: never present one as advice to
    spend money. `bars` gives the confidence and minimum gain used. The open
    model is never modified. Long runs report progress and can be cancelled.
    """
    ws = context.workspace()
    context.check_study(model_id, trials, timeout_s, workers=workers)
    if not 1 <= top <= 20:
        raise ValueError("top must be between 1 and 20")
    if min_gain is not None and not 0 <= min_gain <= 1:
        raise ValueError("min_gain is an availability fraction, 0 to 1 (0.001 = 0.1 points)")
    from raptor.api.scenarios.recommend import recommend, text_lines

    def work(abort, progress):
        return recommend(
            ws, model_id, run_id=run_id, top=top, trials=trials, seed=seed,
            workers=max(1, workers or 1), timeout_s=timeout_s, min_gain=min_gain,
            progress=lambda done, total, label: progress(done, total, label),
            abort=abort)

    result = await run_job(ctx, work, label="Testing changes")
    return ok(result.to_dict(), "\n".join(text_lines(result)))


def _what_if_markdown(comparison: Any) -> str:
    row = comparison.rows[0] if comparison.rows else {}
    if row.get("error"):
        return f"The scenario did not run: {row['error']}"
    lines = ["| Metric | Base | Scenario | Change | P(better) |",
             "|---|---|---|---|---|"]
    for key in ("availability", "reliability", "mtbde", "mdt",
                "capacity_weighted_availability", "lost_flow"):
        metric = (row.get("metrics") or {}).get(key)
        if not metric or (metric.get("value") or {}).get("mean") is None:
            continue
        fraction = metric.get("fraction")
        base = (metric.get("base") or {}).get("mean")
        value = (metric.get("value") or {}).get("mean")
        delta = (metric.get("delta") or {}).get("mean")
        sem = (metric.get("delta") or {}).get("sem")
        fmt = ((lambda v: f"{100 * v:.5g} %") if fraction
               else (lambda v: f"{v:,.6g}"))
        if delta is None:
            continue
        change = (f"{100 * delta:+.3g} pts" if fraction
                  else f"{delta:+,.6g}")
        if sem:
            change += (f" ± {100 * sem:.3g}" if fraction else f" ± {sem:.3g}")
        chance = metric.get("p_better")
        tail = ("—" if chance is None else f"{pct(chance)}")
        lines.append(f"| {metric['label']} | {fmt(base)} | {fmt(value)} | "
                     f"{change} | {tail} |")
    lines.append("")
    lines.append(comparison.caption)
    cut = (comparison.variance_reduction or {}).get("availability")
    if cut is not None:
        lines.append(f"Pairing cut the variance by {pct(cut)}.")
    return "\n".join(lines)


@mcp.tool(annotations=ADDITIVE("Sensitivity"))
@guarded
async def sensitivity(
    model_id: str,
    top: int = 10,
    trials: int | None = None,
    metric: str = "availability",
    seed: int = 1,
    workers: int | None = None,
    timeout_s: float = 1800.0,
    ctx: Context | None = None,
) -> CallToolResult:
    """One-at-a-time tornado: which inputs the answer is most sensitive to.

    Builds up to `top` candidate inputs from the model (block life and
    repair means, PM intervals, finite spare stock, crew counts), varies each
    at a low and a high value, and runs every variant with Base's seed. The
    result gives each input's paired effect at both ends with its standard
    error, the swing, a base-point elasticity where the input is continuous,
    and whether the swing is distinguishable from no effect at this run
    count. This is a screening view: it shows each input alone and cannot
    show interactions.
    """
    from raptor.api.scenarios.sensitivity import default_inputs, tornado

    ws = context.workspace()
    context.whole_number(top, "top", minimum=1, maximum=60)
    context.check_study(model_id, trials, timeout_s, workers=workers,
                        variants=2 * top + 1)
    inputs = default_inputs(ws.get(model_id).rbd, limit=top)
    if not inputs:
        raise ValueError("no input could be varied in this model")

    def work(abort, progress):
        return tornado(
            ws, model_id, inputs, trials=trials, metric=metric, seed=seed,
            workers=max(1, workers or 1), timeout_s=timeout_s,
            progress=lambda done, total, label: progress(done, total, label),
            abort=abort)

    data = await run_job(ctx, work, label="Screening inputs")
    lines = ["| Input | Swing | Low | High | Distinguishable |",
             "|---|---|---|---|---|"]
    for row in data["inputs"]:
        swing = ("—" if row["swing"] is None else f"{row['swing']:.4g}")
        low = ("—" if row["low"]["effect"] is None
               else f"{row['low']['effect']:+.4g}")
        high = ("—" if row["high"]["effect"] is None
                else f"{row['high']['effect']:+.4g}")
        flag = "yes" if row["distinguishable"] else "no"
        lines.append(f"| {row['input']['name']} | {swing} | {low} | "
                     f"{high} | {flag} |")
    lines.append("")
    lines.append(data["caption"])
    lines.append(data["note"])
    return ok(data, "\n".join(lines))

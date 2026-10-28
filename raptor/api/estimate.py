"""Estimate wall-clock time for a simulation run.

Implements an adaptive pilot (5-10 replications, depending on the
requested batch size) with a ``setup + n x per_run`` fit, a +/-30 %
range, a ``(model hash, run_length)`` cache, a single-replication
bail-out for very slow models, and advice strings.

The pilot is a real, cumulative measurement: one batch of replications is
timed, recording the elapsed wall-clock after each replication completes
(``t(1), t(2), ...``).  The cumulative points are least-squares fitted to
``setup + n x per_run``, which measures the per-run cost rather than
run-to-run noise.  The pilot runs several replications because a model
whose per-run cost is non-uniform (capacity/events/growth) can make a
short pilot look cheap.

``run_simulation_with_reports`` also does real work *after* the last
replication (it builds the per-run End_Of_Run blocks and the
Final_Results report).  That work is proportional to the replication
count, so a pilot that only timed the engine loop would systematically
under-estimate a larger batch.  A tiny ``num_runs=0`` call measures the
fixed call overhead, and the pilot batch's own post-loop time is used to
add a per-run report cost on top of the fitted engine slope.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raptor.api.workspace import Workspace
from raptor.core.fileio.rbd import RbdFile
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.engine.reporting import RunReportData

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class EstimateError(Exception):
    """Base error for estimate operations."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum number of replications in the pilot batch.  Sampling the
#: per-run cost at the real horizon captures some of the growth that makes
#: a short pilot under-estimate non-uniform models (capacity/events/growth).
_PILOT_RUNS = 5

#: Upper bound on the adaptive pilot: large batches get more samples
#: (``num_runs / 10``, capped here) so a 200-run request is not estimated
#: from the first handful of cheap replications.
_PILOT_MAX = 10

#: If a single pilot replication exceeds this, stop after one and report
#: a rough estimate (the model is too slow to afford a full pilot).
_SINGLE_RUN_BAIL_SECONDS = 10.0

#: Models at or above this many elements are piloted at :data:`_PILOT_HORIZON_S`
#: instead of the requested horizon, and the fit is scaled up.  One replication
#: of a multi-year horizon on a large model can take longer than any caller
#: will wait (the engine only checks for abort *between* replications), so an
#: unbounded pilot simply never returns.
_LARGE_MODEL_ELEMENTS = 1_500
_PILOT_HORIZON_S = 8_760.0

#: ±range around the point estimate.
_RANGE_FRACTION = 0.3

#: Parallel efficiency: ``speedup(w) = w / (1 + _CONTENTION * (w - 1))``.
#: Measured on a 3,470-block model: 8 workers on an 8-core box gave a 2.8x
#: speed-up, not 8x (each worker ran at ~0.9 core), which fits this curve
#: at ~2.9x.  Pure linear scaling over-promised the wall clock by ~3x.
_CONTENTION = 0.25


def _speedup(workers: int) -> float:
    """Effective speed-up of *workers* parallel replications."""
    import os
    w = max(1, min(int(workers or 1), os.cpu_count() or 1))
    return w / (1.0 + _CONTENTION * (w - 1))


def _pilot_count(num_runs: int) -> int:
    """Replications to time for a *num_runs* estimate.

    At least :data:`_PILOT_RUNS`, growing with the requested batch size
    (one extra sample per 10 runs) up to :data:`_PILOT_MAX`, and never
    more than the batch actually requested (a 1-run estimate does not
    need a 5-run pilot).
    """
    wanted = max(_PILOT_RUNS, min(_PILOT_MAX, math.ceil(num_runs / 10)))
    return max(1, min(num_runs, wanted))


# ---------------------------------------------------------------------------
# Estimate dataclass
# ---------------------------------------------------------------------------

@dataclass
class Estimate:
    """Timing prediction for a simulation run."""
    num_runs: int
    pilot_times: list[float] = field(default_factory=list)
    setup: float = 0.0
    per_run: float = 0.0
    #: TOTAL wall time until the operation completes: the ordinary batch
    #: PLUS every importance-measure variant batch (when the experiment
    #: asks for them). This is what a caller waiting on the tool needs.
    wall_seconds_est: float = 0.0
    #: The ordinary batch alone.
    baseline_wall_seconds: float = 0.0
    #: The extra importance-measure batches alone (0 when off).
    importance_wall_seconds: float = 0.0
    #: "none", "improvement", "risk" or "both".
    importance_mode: str = "none"
    #: Number of blocks measured and the variant batches they need.
    importance_blocks: int = 0
    importance_batches: int = 0
    #: Batches in the operation, baseline included (1 when importance is off).
    total_batches: int = 1
    low: float = 0.0
    high: float = 0.0
    advice: str = ""
    #: True when only one replication could be afforded (very slow model),
    #: so the fit is ``per_run = single replication`` and ``setup = 0``.
    rough: bool = False
    #: Wall time this estimate itself cost (the pilot plus fixed-overhead
    #: measurement). 0 on a cache hit. Callers that time a whole job need
    #: this to explain why a short run took longer than `wall_seconds_est`.
    estimate_seconds: float = 0.0
    #: True when the fit came from the on-disk cache (no pilot re-ran).
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_runs": self.num_runs,
            "pilot_times": [round(t, 4) for t in self.pilot_times],
            "setup": round(self.setup, 4),
            "per_run": round(self.per_run, 4),
            "wall_seconds_est": round(self.wall_seconds_est, 4),
            "baseline_wall_seconds": round(self.baseline_wall_seconds, 4),
            "importance_wall_seconds": round(self.importance_wall_seconds, 4),
            "importance_mode": self.importance_mode,
            "importance_blocks": self.importance_blocks,
            "importance_batches": self.importance_batches,
            "total_batches": self.total_batches,
            "low": round(self.low, 4),
            "high": round(self.high, 4),
            "advice": self.advice,
            "rough": self.rough,
            "estimate_seconds": round(self.estimate_seconds, 3),
            "cached": self.cached,
        }


# ---------------------------------------------------------------------------
# Model hashing for cache key
# ---------------------------------------------------------------------------

def _model_hash(rbd: RbdFile) -> str:
    """Deterministic hash of the model content (for cache key)."""
    # Use the serialised form so identical models produce the same hash
    from raptor.core.fileio.rbds import render_rbds
    text = render_rbds(rbd)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Cache (JSON file in workspace)
# ---------------------------------------------------------------------------

def _cache_path(workspace: Workspace, model_hash: str,
                run_length: float | None,
                pilot_horizon: float | None = None) -> Path:
    """Return the cache file path for a model/run_length/pilot horizon.

    The pilot COUNT is deliberately not part of the key: the cached value
    is the (num_runs-independent) ``setup + n x per_run`` fit, so a pilot
    from a larger batch can answer a later small batch and vice versa.
    A request that needs more samples than the cached pilot has reruns;
    see `_load_cache`.
    """
    cache_dir = workspace.root / ".estimate_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = (f"{model_hash}_{run_length}"
           f"_{pilot_horizon if pilot_horizon is not None else 'same'}")
    return cache_dir / f"{key}.json"


def _load_cache(workspace: Workspace, model_hash: str,
                run_length: float | None, needed_pilots: int,
                pilot_horizon: float | None = None) -> dict[str, Any] | None:
    """Load a cached fit, or return None.

    Reuses a saved pilot when it is at least as long as the caller needs
    (a longer pilot is a better fit, never a worse one). A shorter pilot
    is ignored so a 200-run estimate is not answered from a single-run
    sample.
    """
    path = _cache_path(workspace, model_hash, run_length, pilot_horizon)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    pilot_times = data.get("pilot_times") or []
    if len(pilot_times) < int(needed_pilots) and not data.get("rough"):
        return None
    return data


def _save_cache(workspace: Workspace, model_hash: str,
                run_length: float | None,
                est: Estimate, pilot_horizon: float | None = None,
                *, raw_setup: float | None = None,
                raw_per_run: float | None = None,
                scaled: bool = False, horizon: float | None = None) -> None:
    """Persist a fit to the cache (full precision).

    `raw_setup`/`raw_per_run` are the ONE-WORKER fit before the contention
    speed-up and the small-horizon scaling; those are the values a later
    call can reuse whatever `workers` it asks for. The public `Estimate`
    fields are already adjusted for this call's workers.
    """
    path = _cache_path(workspace, model_hash, run_length, pilot_horizon)
    data = {
        "num_runs": est.num_runs,
        "pilot_times": est.pilot_times,
        "setup": est.setup if raw_setup is None else raw_setup,
        "per_run": est.per_run if raw_per_run is None else raw_per_run,
        "wall_seconds_est": est.wall_seconds_est,
        "low": est.low,
        "high": est.high,
        "advice": est.advice,
        "rough": est.rough,
        "scaled": scaled,
        "pilot_horizon": pilot_horizon,
        "horizon": horizon,
    }
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

class _TimedSink(list):
    """A ``report_sink`` list that records the elapsed time of each
    appended run.

    ``run_simulation_with_reports`` appends one ``RunReportData`` per
    replication as it finishes, so ``times`` ends up holding the real
    cumulative wall-clock ``t(1), t(2), …`` for the batch — recorded
    inside one call, i.e. with exactly one model setup.
    """

    def __init__(self, bail_after: float | None = None) -> None:
        super().__init__()
        self._t0 = time.monotonic()
        self.times: list[float] = []
        self._bail_after = bail_after
        self.bail = False

    def append(self, item: RunReportData) -> None:  # type: ignore[override]
        elapsed = time.monotonic() - self._t0
        self.times.append(elapsed)
        # After the first replication, bail out if it was slow.
        if (self._bail_after is not None
                and len(self.times) == 1
                and elapsed > self._bail_after):
            self.bail = True
        super().append(item)


def _measure_fixed_overhead(rbd: RbdFile) -> float:
    """Time a full call that does no replications.

    This captures the fixed per-call cost (model load + report
    scaffolding) so the pilot's post-loop work can be split into fixed
    and per-run parts.  Failure is non-fatal — the caller treats 0.0 as
    "unknown".
    """
    try:
        t0 = time.monotonic()
        run_simulation_with_reports(
            rbd, num_runs=0, collect_logs=False, report_sink=[],
        )
        return max(time.monotonic() - t0, 0.0)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Fitting and advice
# ---------------------------------------------------------------------------

def _caveats() -> str:
    """The caveats the estimate cannot cover."""
    return (
        "Caveats: collect_events=true adds I/O roughly proportional to "
        "the event count; workers>1 is faster but the results are not "
        "bit-identical to a sequential batch; and a model whose failure "
        "rate changes over time (growth/decay, phases) can have "
        "non-uniform per-run cost, so the pilot may land off.  This is an "
        "estimate, not a promise."
    )


def _fit(pilot_times: list[float]) -> tuple[float, float]:
    """Least-squares fit ``t(n) = setup + n × per_run``.

    *pilot_times* are cumulative times for n = 1, 2, …  Returns
    ``(setup, per_run)``, both clamped at zero.
    """
    if not pilot_times:
        return 0.0, 0.0
    if len(pilot_times) == 1:
        return 0.0, max(pilot_times[0], 0.0)

    ns = list(range(1, len(pilot_times) + 1))
    mean_n = sum(ns) / len(ns)
    mean_t = sum(pilot_times) / len(pilot_times)
    denom = sum((n - mean_n) ** 2 for n in ns)
    if denom == 0.0:
        return 0.0, max(mean_t, 0.0)

    slope = sum((n - mean_n) * (t - mean_t)
                for n, t in zip(ns, pilot_times)) / denom
    if slope <= 0.0:
        # Non-monotonic / noisy pilot — fall back to the average per run.
        per_run = max(pilot_times[-1] / len(pilot_times), 0.0)
        return 0.0, per_run

    per_run = slope
    setup = max(mean_t - per_run * mean_n, 0.0)
    return setup, per_run


def _finalize(
    setup: float,
    per_run: float,
    pilot_times: list[float],
    num_runs: int,
    *,
    rough: bool = False,
    scaled: bool = False,
    pilot_horizon: float = 0.0,
    horizon: float = 0.0,
    workers: int = 1,
    estimate_seconds: float = 0.0,
    cached: bool = False,
    importance_mode: str = "none",
    importance_blocks: int = 0,
    importance_batches: int = 0,
) -> Estimate:
    """Build the public :class:`Estimate` from a one-worker fit.

    `per_run` must be the RAW one-worker marginal cost; the contention
    speed-up for `workers` is applied here, so the same cached fit answers
    a 1-worker and a 4-worker request correctly.
    """
    per_run = max(per_run, 0.0) / _speedup(workers)
    baseline_wall = max(setup + num_runs * per_run, 0.0)
    total_batches = 1 + max(0, int(importance_batches))
    importance_wall = baseline_wall * max(0, int(importance_batches))
    wall_est = baseline_wall * total_batches
    low = wall_est * (1.0 - _RANGE_FRACTION)
    high = wall_est * (1.0 + _RANGE_FRACTION)
    importance_note = ""
    if importance_batches:
        importance_note = (
            f" Including importance measures ({importance_mode}: "
            f"{importance_blocks} blocks, {importance_batches} extra "
            f"batch(es), {total_batches} batches total)"
            if importance_mode else
            f" Including {importance_batches} importance batch(es)")

    if rough:
        lead = (
            f"Rough estimate: a single replication took "
            f"{per_run:.1f}s — this is a very slow model, so the pilot "
            f"stopped after one.  Estimated total for {num_runs} runs: "
            f"{wall_est:.0f}s{importance_note} (do the arithmetic yourself "
            f"for more confidence)."
        )
    elif wall_est < 5.0:
        lead = (
            f"Fast model — estimated {wall_est:.2f}s for {num_runs} "
            f"runs{importance_note}; no action needed."
        )
    elif wall_est < 60.0:
        lead = (
            f"Estimated {wall_est:.1f}s for {num_runs} runs"
            f"{importance_note}.  Consider reducing num_runs if "
            f"interactive feedback is needed."
        )
    elif wall_est < 300.0:
        lead = (
            f"Estimated {wall_est:.0f}s for {num_runs} runs"
            f"{importance_note}.  Run in background with "
            f"timeout_s={max(int(wall_est * 1.5), 120)}."
        )
    else:
        lead = (
            f"Estimated {wall_est:.0f}s for {num_runs} runs"
            f"{importance_note} — very long run.  Consider fewer runs or a "
            f"shorter run_length."
        )

    if workers and int(workers) > 1:
        lead = (f"Assumes {int(workers)} parallel worker(s) "
                f"(~{_speedup(int(workers)):.1f}x speed-up, not linear).  "
                + lead)
    if scaled:
        lead = (
            f"Large model: piloted at a reduced horizon "
            f"({pilot_horizon:g} h) and scaled to {horizon:g} h, so treat "
            f"this as an order-of-magnitude answer.  " + lead
        )
    advice = f"{lead}  {_caveats()}"

    return Estimate(
        num_runs=num_runs,
        pilot_times=list(pilot_times),
        setup=setup,
        per_run=per_run,
        wall_seconds_est=wall_est,
        baseline_wall_seconds=baseline_wall,
        importance_wall_seconds=importance_wall,
        importance_mode=(importance_mode or "none") if importance_batches else "none",
        importance_blocks=int(importance_blocks if importance_batches else 0),
        importance_batches=int(importance_batches),
        total_batches=total_batches,
        low=low,
        high=high,
        advice=advice,
        rough=rough,
        estimate_seconds=estimate_seconds,
        cached=cached,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def estimate(  # noqa: C901, PLR0912, PLR0915
    workspace: Workspace,
    model_id: str,
    *,
    num_runs: int = 10,
    run_length: float | None = None,
    workers: int = 1,
    importance: str | None = None,
) -> Estimate:
    """Estimate wall-clock time for *num_runs* simulations.

    Runs a real, cumulative pilot (5 replications by default, up to 10
    for large batches; 1 for very slow models), fits
    ``setup + n × per_run``, and returns a ±30 % range with advice.

    The result is cached on ``(model_hash, run_length)`` so repeated
    estimates for the same model are instant — and because the cache
    stores the fit rather than a single ``num_runs`` answer, asking for
    a different ``num_runs`` is also free.

    Parameters
    ----------
    workspace : Workspace
        Active workspace that owns *model_id*.
    model_id : str
        Handle returned by :meth:`Workspace.create_model` / ``open_model``.
    num_runs : int
        Number of runs to estimate for (default 10).
    run_length : float | None
        If given, overrides the model's ``system_config.term_condition``.
        The override is applied to a deep copy, so the live model is
        never mutated.
    workers : int
        Replications to run in parallel.  The pilot measures one worker;
        the answer is divided by a contention-aware speed-up
        (:func:`_speedup`), because parallel workers do not scale linearly
        on a memory-bandwidth-bound model.
    importance : str | None
        Override the model experiment's importance mode for this estimate.
        ``None`` uses the model's own setting.  When on, the returned total
        includes the baseline batch AND the per-block variant batches, so
        it is the time until the whole operation finishes.
    """
    if num_runs < 1:
        raise ValueError("num_runs must be at least 1")
    if run_length is not None and not (
            1e-6 <= float(run_length) <= 999_999_999.999999):
        raise ValueError(
            "Stop simulation time must be between 0.000001 and "
            "999,999,999.999999!")
    handle = workspace.get(model_id)
    rbd = handle.rbd

    # A big model is piloted at a bounded horizon and the cost scaled up:
    # one replication over the real horizon can take so long that the pilot
    # never returns (the engine can only stop between replications).
    def _element_count(model: RbdFile) -> int:
        return sum(len(model.section(name).rows)
                   for name in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                                "HIERARCHY_DATA"))

    horizon = run_length
    if horizon is None:
        try:
            horizon = float(rbd.section("SYSTEM_DATA").rows[0]
                            .get("TermCondition", "1000"))
        except (IndexError, KeyError, TypeError, ValueError):
            horizon = 1000.0
    pilot_horizon = horizon
    scaled = False
    if _element_count(rbd) >= _LARGE_MODEL_ELEMENTS and horizon > _PILOT_HORIZON_S:
        pilot_horizon = _PILOT_HORIZON_S
        scaled = True

    # Optionally override run_length — on a copy, never the live model.
    # ``TermCondition`` is the SYSTEM_DATA column the engine reads
    # (``typed.system_config_from_row``).
    if run_length is not None or scaled:
        rbd = copy.deepcopy(rbd)
        sys_sec = rbd.sections.get("SYSTEM_DATA")
        if sys_sec and sys_sec.rows:
            sys_sec.rows[0]["TermCondition"] = str(pilot_horizon)

    # Importance measures run AFTER the baseline as one batch per block per
    # variant kind, sequentially. They are part of the operation's wall
    # time, so the estimate must include them -- not just the baseline.
    mode = importance
    if mode is None:
        experiment = getattr(rbd, "experiment", None)
        mode = str(experiment.get("importance", "none")) if isinstance(experiment, dict) else "none"
    mode = (mode or "none").strip()
    if mode in ("", "none"):
        mode = "none"
        importance_blocks = 0
        importance_batches = 0
    else:
        from raptor.api.importance import batch_count, block_rows, variants_for
        variants_for(mode)          # validate early, with the public message
        importance_blocks = len(block_rows(rbd))
        importance_batches = max(
            0, batch_count(importance_blocks, mode) - 1)

    mhash = _model_hash(rbd)
    started = time.monotonic()

    # Adaptive pilot length. The cache stores the FIT, not this batch's
    # answer, so a longer pilot can answer a smaller batch; a shorter one
    # may not answer a larger batch (see `_load_cache`).
    # Two replications when scaling: one cannot separate the fixed
    # setup cost from the marginal per-run cost, and that conflation made
    # the scaled estimate 12x too high on a large model.
    pilot_runs = 2 if scaled else _pilot_count(num_runs)

    # Check cache — the cached value holds the (num_runs-independent) fit.
    cached = _load_cache(workspace, mhash, run_length, pilot_runs,
                         pilot_horizon)
    if cached is not None:
        return _finalize(
            float(cached.get("setup", 0.0)),
            float(cached.get("per_run", 0.0)),
            list(cached.get("pilot_times") or []),
            num_runs,
            rough=bool(cached.get("rough", False)),
            scaled=bool(cached.get("scaled", scaled)),
            pilot_horizon=float(cached.get("pilot_horizon")
                                or pilot_horizon),
            horizon=float(cached.get("horizon") or horizon),
            workers=workers,
            estimate_seconds=time.monotonic() - started,
            cached=True,
            importance_mode=mode,
            importance_blocks=importance_blocks,
            importance_batches=importance_batches,
        )

    # Fixed per-call cost, so post-loop report work can be separated.
    fixed_overhead = _measure_fixed_overhead(rbd)

    # --- real cumulative pilot: one batch, timed per replication ---
    t_batch_start = time.monotonic()
    sink = _TimedSink(bail_after=(120.0 if scaled
                                  else _SINGLE_RUN_BAIL_SECONDS))

    def _stop_check() -> bool:
        return sink.bail

    try:
        run_simulation_with_reports(
            rbd,
            num_runs=pilot_runs,
            report_sink=sink,
            collect_logs=False,
            stop_check=_stop_check,
        )
    except Exception as exc:
        raise EstimateError(f"Pilot run failed: {exc}") from exc
    batch_total = time.monotonic() - t_batch_start

    pilot_times = sink.times
    if not pilot_times:
        raise EstimateError("Pilot produced no completed replications.")

    elapsed = time.monotonic() - started

    # Single-replication bail-out for very slow models.
    if sink.bail or len(pilot_times) == 1:
        single = pilot_times[0] * (horizon / pilot_horizon)
        est = _finalize(0.0, single, pilot_times,
                        num_runs, rough=True, scaled=scaled,
                        pilot_horizon=pilot_horizon, horizon=horizon,
                        workers=workers, estimate_seconds=elapsed,
                        importance_mode=mode,
                        importance_blocks=importance_blocks,
                        importance_batches=importance_batches)
        _save_cache(workspace, mhash, run_length, est,
                    pilot_horizon, raw_setup=0.0, raw_per_run=single,
                    scaled=scaled, horizon=horizon)
        return est

    # --- fit the engine cost, then add the per-run report cost ---
    setup, per_run = _fit(pilot_times)

    # Post-loop work for the pilot batch (End_Of_Run + Final_Results
    # report building) is not in `pilot_times`; subtract the fixed
    # overhead and spread the remainder over the pilot replications.
    post_loop = batch_total - pilot_times[-1]
    variable_report = max(post_loop - fixed_overhead, 0.0)
    report_per_run = variable_report / len(pilot_times)
    per_run += report_per_run

    if scaled:
        per_run *= horizon / pilot_horizon
    est = _finalize(setup, per_run, pilot_times,
                    num_runs, scaled=scaled, pilot_horizon=pilot_horizon,
                    horizon=horizon, workers=workers,
                    estimate_seconds=elapsed,
                    importance_mode=mode,
                    importance_blocks=importance_blocks,
                    importance_batches=importance_batches)
    _save_cache(workspace, mhash, run_length, est,
                pilot_horizon, raw_setup=setup, raw_per_run=per_run,
                scaled=scaled, horizon=horizon)
    return est

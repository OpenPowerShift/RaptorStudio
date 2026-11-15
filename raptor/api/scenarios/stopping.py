"""Run until the answer is precise enough (WP-3.3).

The user states a relative precision, not a run count.  Each pass runs a
larger batch with the same seed and reads the per-run values; because
trial *i* draws the same streams under the same seed, the final batch is
exactly the first *n* trials of a fixed *n*-trial run (asserted in the
tests).  Batches are re-run rather than appended, which keeps the engine
and its seed table untouched and costs at most about twice the final work.

The stopping rule is Law (2015)'s sequential relative-precision procedure:
run `min_trials`, then stop when `t * SEM / |mean| <= relative`, otherwise
run enough more trials for the estimated width to meet the target, growing
the batch so the loop takes about ten passes at most.
"""
from __future__ import annotations

import functools
from functools import lru_cache
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from raptor.api.run import RunResult

#: Metric aliases users write -> per-run field.
METRIC_FIELDS = {
    "system.ao": "availability",
    "system.availability": "availability",
    "availability": "availability",
    "system.r": "reliability",
    "system.reliability": "reliability",
    "reliability": "reliability",
    "system.mtbde": "mtbde",
    "mtbde": "mtbde",
    "system.mdt": "mdt",
    "mdt": "mdt",
    "system.capacity": "capacity_weighted_availability",
    "capacity": "capacity_weighted_availability",
}

#: Two-sided Student-t quantiles for the common confidence levels.
_T_TABLE = {
    0.90: (6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833,
           1.812, 1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734,
           1.729, 1.725, 1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703,
           1.701, 1.699, 1.697),
    0.95: (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262,
           2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
           2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052,
           2.048, 2.045, 2.042),
    0.99: (63.657, 9.925, 5.841, 4.604, 4.032, 3.707, 3.499, 3.355, 3.250,
           3.169, 3.106, 3.055, 3.012, 2.977, 2.947, 2.921, 2.898, 2.878,
           2.861, 2.845, 2.831, 2.819, 2.807, 2.797, 2.787, 2.779, 2.771,
           2.763, 2.756, 2.750),
}


def t_critical(df: int, confidence: float = 0.95) -> float:
    """Two-sided Student-t quantile for `confidence` with `df` degrees of
    freedom: the table for the common levels, otherwise computed exactly
    (an 80 % level was silently read as 90 %)."""
    if df <= 0:
        return float("inf")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    for level, row in _T_TABLE.items():
        if abs(level - confidence) < 1e-9 and df <= len(row):
            return row[df - 1]
    return _t_quantile_cached(round(confidence, 12), int(df))


@lru_cache(maxsize=512)
def _t_quantile_cached(confidence: float, df: int) -> float:
    from raptor.api.advisor.stats import t_quantile
    return t_quantile(0.5 + confidence / 2.0, df)


#: Fields that are probabilities: by default precision watches the part
#: that is lost (1 - value), since a 10 % precision on 99.9 % availability
#: should be a tenth of the 0.1 % lost, not of the 99.9 %
PROBABILITY_FIELDS = frozenset({"availability", "reliability",
                                "capacity_weighted_availability"})


@dataclass(frozen=True)
class Precision:
    """What run-to-precision watches and how closely.

    `on` is ``"unavailability"`` (watch 1 - value) or ``"value"``; left as
    ``"auto"`` it is the first for a probability (availability, reliability,
    delivered fraction) and the second for a time (MTBDE, MDT), where
    1 - value means nothing."""

    metric: str = "system.ao"
    on: Literal["unavailability", "value", "auto"] = "auto"
    relative: float = 0.10
    confidence: float = 0.95
    min_trials: int = 50
    max_trials: int = 20_000
    batch: int = 50

    def __post_init__(self) -> None:
        if self.on == "auto":
            field_name = METRIC_FIELDS.get(self.metric)
            object.__setattr__(self, "on", "unavailability"
                               if field_name in PROBABILITY_FIELDS or field_name is None
                               else "value")

    def field(self) -> str:
        field_name = METRIC_FIELDS.get(self.metric)
        if field_name is None:
            raise ValueError(
                f"unknown precision metric {self.metric!r}; use one of "
                f"{', '.join(sorted(METRIC_FIELDS))}")
        if self.on not in ("unavailability", "value"):
            raise ValueError(f"precision on must be 'unavailability' or 'value', "
                             f"not {self.on!r}")
        if self.on == "unavailability" and field_name not in PROBABILITY_FIELDS:
            raise ValueError(f"{self.metric} is a time, not a probability: watch "
                             "its value (on='value'), 1 - it means nothing")
        if not 0 < self.confidence < 1:
            raise ValueError("confidence must be between 0 and 1, e.g. 0.95")
        if not 0 < self.relative < 1:
            raise ValueError("relative precision must be between 0 and 1")
        if self.min_trials < 2 or self.max_trials < self.min_trials:
            raise ValueError("need 2 <= min_trials <= max_trials")
        if self.batch < 1:
            raise ValueError("batch must be at least 1")
        return field_name


@dataclass
class PrecisionRun:
    """The final run and the precision it reached."""

    result: RunResult
    precision: Precision
    field_name: str
    mean: float | None
    unavailability: float | None
    sem: float | None
    half_width: float | None
    relative_half_width: float | None
    target: float
    trials: int
    stopped: str
    history: list[dict[str, Any]] = field(default_factory=list)

    def watched(self) -> str:
        """What the interval is on, in words: 'unavailability',
        '1 - reliability' or the metric itself."""
        if self.precision.on != "unavailability":
            return self.field_name
        return ("unavailability" if self.field_name == "availability"
                else f"1 - {self.field_name.replace('_', ' ')}")

    def value(self) -> float | None:
        """The mean of the metric itself (availability, not unavailability)."""
        if self.mean is None:
            return None
        return 1.0 - self.mean if self.precision.on == "unavailability" else self.mean

    #: the reason a run stopped, for a person
    STOPPED = {"precision": "target reached", "max trials": "trial limit reached",
               "cancelled": "stopped", "no data": "no values to measure"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.result.run_id,
            "metric": self.precision.metric,
            "on": self.precision.on,
            "relative": self.precision.relative,
            "confidence": self.precision.confidence,
            "watched": self.watched(),
            "mean": self.mean,
            "value": self.value(),
            "unavailability": self.unavailability,
            "sem": self.sem,
            "half_width": self.half_width,
            "relative_half_width": self.relative_half_width,
            "target": self.target,
            "trials": self.trials,
            "stopped": self.stopped,
            "history": self.history,
        }


def _watch(row: dict, field_name: str, on: str) -> float | None:
    value = row.get(field_name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return 1.0 - number if on == "unavailability" else number


def _stats(values: list[float], confidence: float
           ) -> tuple[float | None, float | None, float | None, float | None]:
    n = len(values)
    if n < 2:
        return (values[0] if values else None), None, None, None
    mean = sum(values) / n
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    sem = math.sqrt(variance / n)
    half = t_critical(n - 1, confidence) * sem
    relative = half / abs(mean) if mean else None
    return mean, sem, half, relative


def _pass_label(number: int, trials: int, relative: float | None,
                target: float) -> str:
    """What one pass is doing, for a progress line: its number and size and
    how far the last pass was from the target."""
    from raptor.api.insight.axes import pct
    text = f"pass {number}: {trials:,} trials"
    if relative is not None:
        text += f"; last pass ±{pct(relative)} of the value, target ±{pct(target)}"
    return text


def _one_pass(run: Callable[[], RunResult], trials: int, abort: Any, *,
              first: bool) -> RunResult | None:
    """One pass, or None when it was stopped part-way (a fragment of a pass
    is not kept). Stopping the first pass, with nothing to keep, raises."""
    from raptor.api.run import RunCancelledError
    try:
        latest = run()
    except RunCancelledError:
        if first:
            raise
        return None
    stopping = abort is not None and getattr(abort, "is_set", lambda: False)()
    if stopping and not first and len(latest.per_run) < trials:
        return None
    return latest


def run_to_precision(
    workspace: Any,
    model_id: str,
    precision: Precision | None = None,
    *,
    workers: int = 1,
    seed: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    abort: Any = None,
    **run_options: Any,
) -> PrecisionRun:
    """Run the model in growing batches until the relative width is met.

    `progress(done, total, text)` reports each trial of each pass (the text
    names the pass and how far the last one was from the target); `abort` (a
    `threading.Event`) stops between passes, and the last completed batch
    is kept.  Extra keyword arguments go to `raptor.api.run.run`
    (`timeout_s`, `attribution`, ...).
    """
    from raptor.api.run import run as run_api

    precision = precision or Precision()
    field_name = precision.field()
    target = precision.relative / (1 + precision.relative)
    trials = max(2, precision.min_trials)
    history: list[dict[str, Any]] = []
    result: RunResult | None = None
    mean = sem = half = relative = None

    while True:
        if (result is not None and abort is not None
                and getattr(abort, "is_set", lambda: False)()):
            stopped = "cancelled"
            break
        options = dict(run_options)
        if progress:
            label = _pass_label(len(history) + 1, trials, relative, target)
            progress(0, trials, label)
            if "progress" not in options:
                # each trial of this pass, so a long pass is not a frozen bar
                options["progress"] = (
                    lambda done, _total, n=trials, text=label: progress(done, n, text))
        latest = _one_pass(functools.partial(
                               run_api, workspace, model_id, num_runs=trials,
                               workers=workers, seed=seed, abort=abort, **options),
                           trials, abort, first=result is None)
        if latest is None:
            # stopped part-way through a pass: the last whole pass stands
            stopped = "cancelled"
            break
        result = latest
        values = [value for row in result.per_run
                  if (value := _watch(row, field_name, precision.on))
                  is not None]
        mean, sem, half, relative = _stats(values, precision.confidence)
        # `trials` were run; `values` of them had the metric (a run with no
        # outage has no mean downtime)
        history.append({"trials": len(result.per_run), "values": len(values),
                        "mean": mean, "sem": sem, "half_width": half,
                        "relative": relative})
        if len(values) < 2:
            stopped = "no data"
            break
        if relative is not None and relative <= target:
            stopped = "precision"
            break
        # Count the trials RUN, not the values: a run with no outage has no
        # mean downtime, and counting values re-ran max_trials for ever.
        ran = len(result.per_run)
        if ran >= precision.max_trials or (
                abort is not None and getattr(abort, "is_set", lambda: False)()):
            stopped = "max trials" if ran >= precision.max_trials else "cancelled"
            break
        # Each pass re-runs from trial 1 with the same seed, so the count
        # must grow geometrically: with no estimate yet (no failure seen,
        # mean 0) double it rather than add one trial per pass. Values come
        # from len(values) of `ran` trials, so scale by that ratio.
        per_trial = len(values) / ran
        needed = max(ran + precision.batch, 2 * ran)
        if relative and half and mean:
            needed = max(ran + precision.batch,
                         math.ceil(len(values) * (relative / target) ** 2 / per_trial))
        trials = min(precision.max_trials, max(needed, ran + 1))

    assert result is not None
    return PrecisionRun(
        result=result, precision=precision, field_name=field_name, mean=mean,
        # the watched mean is already 1 - availability: it is the
        # unavailability (it used to say 1 - mean, the availability)
        unavailability=(mean if mean is not None and precision.on == "unavailability"
                        and field_name == "availability" else None),
        sem=sem, half_width=half, relative_half_width=relative,
        target=target, trials=len(result.per_run), stopped=stopped,
        history=history)

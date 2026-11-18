"""What-if scenarios compared on shared seeds (WP-3.1).

A scenario is the open model plus a short list of `PATH=VALUE` changes
(`raptor.api.paths`), never a second file.  Base and every scenario run with
the same seed, and the comparison is on paired differences.

The streams run on from one trial into the next (as in Raptor 7), so the
two runs stay in step only until the change first moves an event; after
that they draw different numbers. Measured on the book's models the pairing
removes a few per cent of the variance, often none. The comparison stays
unbiased and its standard error is from the paired differences, so it is
honest; `variance_reduction` reports what pairing actually bought. True
per-trial common random numbers would need the engine to re-seed each
trial, which Raptor 7 does not do.
"""
from __future__ import annotations
from raptor.api.insight.axes import pct

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from raptor.api.batch import Deadline, cut_short, run_variants
from raptor.api.run import run_dir
from raptor.api.run_tables import read_per_run

#: The metrics a comparison reports.
METRICS: tuple[str, ...] = (
    "availability", "reliability", "mtbde", "mdt",
    "capacity_weighted_availability", "lost_flow", "grand_total",
)
LABELS = {
    "availability": "Availability",
    "reliability": "Reliability",
    "mtbde": "Time between failures",
    "mdt": "Time down",
    "capacity_weighted_availability": "Delivered fraction",
    "lost_flow": "Lost flow",
    "grand_total": "Grand total",
}
UNITS = {"mtbde": "time", "mdt": "time", "lost_flow": "flow",
         "grand_total": "cost"}
FRACTIONS = {"availability", "reliability",
             "capacity_weighted_availability"}
#: Metrics where a smaller value is the better one.
LOWER_IS_BETTER = frozenset({"mdt", "lost_flow", "grand_total"})


@dataclass(frozen=True)
class Scenario:
    name: str
    changes: tuple[str, ...] = ()
    note: str = ""

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Scenario:
        return cls(str(row.get("name") or "Scenario"),
                   tuple(str(change) for change in row.get("changes") or ()),
                   str(row.get("note") or ""))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Comparison:
    trials: int
    seed: int | None
    base: dict[str, Any]
    rows: list[dict[str, Any]]
    variance_reduction: dict[str, float | None] = field(default_factory=dict)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    caption: str = ""
    note: str = ""
    #: Set when the time limit or a cancel stopped the study before every
    #: run finished: say so wherever the numbers are shown.
    cut_short: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _values(rows: list[dict], key: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if math.isfinite(number):
                out.append(number)
    return out


def _stat(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"mean": None, "sem": None, "n": 0}
    mean = sum(values) / n
    sem = (math.sqrt(sum((value - mean) ** 2 for value in values)
                     / (n - 1) / n) if n > 1 else None)
    return {"mean": mean, "sem": sem, "n": n}


def _paired(base: list[float], other: list[float], lower_better: bool = False
            ) -> tuple[dict[str, Any], float | None]:
    """Mean paired difference (other - base) and the chance it is better.

    "Better" is an increase, or a decrease for a metric in
    `LOWER_IS_BETTER` such as time down or cost.
    """
    n = min(len(base), len(other))
    if n < 2:
        return _stat([]), None
    deltas = [other[i] - base[i] for i in range(n)]
    stat = _stat(deltas)
    mean, sem = stat["mean"], stat["sem"]
    if mean is None:
        return stat, None
    gain = -mean if lower_better else mean
    if not sem:
        return stat, 1.0 if gain > 0 else 0.0 if gain < 0 else 0.5
    # Student's t on n - 1 degrees of freedom, not the normal: at the small
    # trial counts a what-if often has, the normal overstates the certainty
    from raptor.api.advisor.stats import t_cdf
    return stat, t_cdf(gain / sem, n - 1)


def _variance_reduction(base: list[float], other: list[float]) -> float | None:
    n = min(len(base), len(other))
    if n < 2:
        return None
    deltas = [other[i] - base[i] for i in range(n)]

    def variance(values: list[float]) -> float:
        mean = sum(values[:n]) / n
        return sum((value - mean) ** 2 for value in values[:n]) / (n - 1)

    denominator = variance(base) + variance(other)
    if denominator <= 0:
        return None
    return max(0.0, min(1.0, 1.0 - variance(deltas) / denominator))


def _field(requirement: dict[str, Any]) -> str:
    from raptor.api.advisor.context import REQUIREMENT_FIELDS
    return REQUIREMENT_FIELDS.get(str(requirement.get("metric") or ""),
                                  str(requirement.get("metric") or "")
                                  .split(".", 1)[-1])


def _verdict(value: float | None, requirement: dict[str, Any]) -> str:
    if value is None:
        return "no data"
    try:
        target = float(str(requirement.get("value")))
    except (TypeError, ValueError):
        return "not comparable"
    op = str(requirement.get("op") or ">=")
    ok = {">=": value >= target, ">": value > target,
          "<=": value <= target, "<": value < target,
          "==": abs(value - target) < 1e-12,
          "!=": abs(value - target) >= 1e-12}.get(op)
    return "met" if ok else "missed" if ok is False else "unknown"


def _per_run(workspace: Any, row: dict[str, Any]) -> list[dict]:
    run_id = row.get("run_id")
    if not run_id:
        return []
    try:
        return read_per_run(run_dir(workspace, run_id), run_id) or []
    except Exception:  # noqa: BLE001 - the variant row already carries an error
        return []


def compare(
    workspace: Any,
    model_id: str,
    scenarios: Sequence[Scenario | dict[str, Any]],
    *,
    trials: int | None = None,
    precision: Any = None,
    workers: int = 1,
    seed: int | None = None,
    timeout_s: float = 1800.0,
    requirements: list[dict[str, Any]] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    abort: Any = None,
) -> Comparison:
    """Run Base and each scenario with paired seeds and compare the metrics.

    `trials` sets the fixed count. `precision` (a
    `raptor.api.scenarios.stopping.Precision`) first sizes Base with
    `run_to_precision`, then runs everyone at that count. `progress` receives
    `(done, total, label)`.
    """
    parsed = [scenario if isinstance(scenario, Scenario)
              else Scenario.from_dict(scenario) for scenario in scenarios]
    started = time.monotonic()
    limit = Deadline(abort, timeout_s)
    if precision is not None:
        from raptor.api.scenarios.stopping import run_to_precision
        sized = run_to_precision(workspace, model_id, precision,
                                 workers=workers, seed=seed,
                                 progress=None, abort=limit,
                                 timeout_s=timeout_s)
        trials = sized.trials
    variants = [{"label": "Base", "assignments": []}]
    variants += [{"label": scenario.name,
                  "assignments": list(scenario.changes)}
                 for scenario in parsed]
    results = run_variants(
        workspace, model_id, variants, num_runs=trials, workers=workers,
        timeout_s=max(1e-3, timeout_s - (time.monotonic() - started)),
        seed=seed, progress=progress, abort=limit)
    short = cut_short(results, timeout_s)
    base_row = results[0] if results else {}
    base_data = _per_run(workspace, base_row)
    base_metrics = {key: _stat(_values(base_data, key)) for key in METRICS}
    rows: list[dict[str, Any]] = []
    variance: dict[str, float | None] = {}
    for scenario, row in zip(parsed, results[1:], strict=False):
        entry: dict[str, Any] = {
            "name": scenario.name, "changes": list(scenario.changes),
            "note": scenario.note, "run_id": row.get("run_id"),
            "error": row.get("error"), "skipped": row.get("skipped"),
            "truncated": bool(row.get("truncated")), "metrics": {},
        }
        if not entry["error"]:
            other = _per_run(workspace, row)
            for key in METRICS:
                base_values = _values(base_data, key)
                other_values = _values(other, key)
                delta, p_better = _paired(base_values, other_values,
                                          key in LOWER_IS_BETTER)
                entry["metrics"][key] = {
                    "label": LABELS.get(key, key),
                    "units": UNITS.get(key, ""),
                    "fraction": key in FRACTIONS,
                    "base": _stat(base_values),
                    "value": _stat(other_values),
                    "delta": delta,
                    "p_better": p_better,
                    "paired": len(base_values) == len(other_values)
                              and bool(base_values),
                }
            entry["variance_reduction"] = {
                key: _variance_reduction(_values(base_data, key),
                                         _values(other, key))
                for key in METRICS}
            if not variance:
                variance = entry["variance_reduction"]
        rows.append(entry)
    comparison = Comparison(
        trials=int(trials or len(base_data)), seed=seed,
        base={"run_id": base_row.get("run_id"), "metrics": base_metrics},
        rows=rows, variance_reduction=variance,
        requirements=_requirements(requirements or (), base_metrics, rows),
        caption=(short + " " if short else "") + _caption(rows),
        cut_short=short,
        note=("Each scenario is compared with Base using the same seed and "
              "paired trials. The two stay in step only until the change first "
              "moves an event, so pairing usually removes little noise; the "
              "standard error is from the paired differences, so it is honest."))
    return comparison


def _requirements(requirements: Any, base_metrics: dict[str, Any],
                  rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for requirement in requirements:
        field_name = _field(requirement)
        entry = {**requirement, "field": field_name,
                 "base": _verdict((base_metrics.get(field_name) or {})
                                  .get("mean"), requirement),
                 "scenarios": {}}
        for row in rows:
            if row.get("error"):
                continue
            metric = (row.get("metrics", {}).get(field_name) or {})
            entry["scenarios"][row["name"]] = _verdict(
                (metric.get("value") or {}).get("mean"), requirement)
        out.append(entry)
    return out


def _caption(rows: list[dict[str, Any]]) -> str:
    usable = [row for row in rows
              if not row.get("error") and row.get("metrics")]
    if not usable:
        failed = [row for row in rows if row.get("error")]
        if failed:
            first = failed[0]
            return (f"No scenario ran: {len(failed)} could not be applied or run "
                    f"(\"{first['name']}\": {first['error']}).")
        return "No scenario to compare: add one with a change to the model."
    best = max(usable, key=lambda row: (
        (row["metrics"]["availability"]["delta"] or {}).get("mean") or 0.0))
    delta = (best["metrics"]["availability"]["delta"] or {}).get("mean")
    if delta is None:
        return f"{len(usable)} scenario(s) compared with Base."
    note = (" With several scenarios, each is compared with Base; the "
            "largest is not a league table of noise."
            if len(usable) > 1 else "")
    if abs(delta) < 1e-12:
        lead = ("No scenario changes availability against Base"
                if len(usable) > 1 else f"{best['name']} does not change availability")
        return lead + " at this run count." + note
    if delta > 0:
        return (f"{best['name']} gives the largest gain in availability: "
                f"{100 * delta:+.3g} points." + note)
    return ("No scenario improves availability; the least harmful, "
            f"{best['name']}, changes it by {100 * delta:+.3g} points." + note)


#: (metric, label, is a fraction) in the order the text shows them.
TEXT_METRICS = (("availability", "Availability", True),
                ("reliability", "Reliability", True),
                ("mtbde", "MTBDE", False),
                ("mdt", "MDT", False),
                ("capacity_weighted_availability", "Delivered fraction", True),
                ("lost_flow", "Lost flow", False),
                ("grand_total", "Grand total", False))


def comparison_lines(comparison: Any) -> list[str]:
    """A comparison as text lines: each scenario's metrics with the paired
    change, its standard error and the chance it is better; the requirement
    verdicts; the caption. `raptor whatif` prints it; the GUI copies it."""
    lines: list[str] = []
    lines.append(f"Base: {comparison.trials} paired runs, " + (f"seed {comparison.seed}" if comparison.seed else "the default seed"))
    for row in comparison.rows:
        lines.append(f"\n{row['name']}  ({'; '.join(row['changes']) or 'no changes'})")
        if row.get("error"):
            lines.append(f"  error: {row['error']}")
            continue
        for key, label, fraction in TEXT_METRICS:
            metric = row["metrics"].get(key) or {}
            value = (metric.get("value") or {}).get("mean")
            delta = (metric.get("delta") or {}).get("mean")
            sem = (metric.get("delta") or {}).get("sem")
            if value is None:
                continue
            shown = f"{100 * value:.5g} %" if fraction else f"{value:,.6g}"
            change = (f"{100 * delta:+.3g} pts" if fraction and delta is not None
                      else f"{delta:+,.3g}" if delta is not None else "—")
            error = (f" ± {100 * sem:.3g}" if fraction and sem
                     else f" ± {sem:.3g}" if sem else "")
            chance = metric.get("p_better")
            chance_text = f"  P(better) {pct(chance)}" if chance is not None else ""
            lines.append(f"  {label:<22} {shown:>14}   {change}{error}{chance_text}")
    if comparison.variance_reduction:
        cut = comparison.variance_reduction.get("availability")
        if cut is not None:
            lines.append(f"\nPairing cut the variance by {pct(cut)}.")
    for requirement in comparison.requirements:
        lines.append(f"\nRequirement {requirement.get('label')}: "
              f"Base {requirement['base']}")
        for name, verdict in (requirement.get("scenarios") or {}).items():
            lines.append(f"  {name}: {verdict}")
    lines.append(f"\n{comparison.caption}")
    return lines

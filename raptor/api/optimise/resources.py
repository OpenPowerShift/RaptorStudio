"""How many crews or tools are needed (WP-4.5).

Each candidate size is simulated with Base's seed and compared by paired
trials.  The analytic seed is the offered load: failures per unit time times
the mean time each repair holds the resource, rounded up.  A size meets the
target only when its pessimistic confidence bound does (lower for
availability, upper for MDT); otherwise the best measured size is reported.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ResourceSpec:
    name: str
    units: int
    offered_load: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resources_from_model(rbd: Any, names: Sequence[str] | None = None
                         ) -> list[ResourceSpec]:
    """Each resource with its offered load (Erlangs of work).

    Load = sum over the blocks that use it of failure rate (duty applied) x
    mean repair x units needed, plus PM frequency x mean PM duration x units
    needed for PM when the block has planned maintenance.
    """
    from raptor.api.advisor.context import ModelContext, on

    ctx = ModelContext(rbd)
    wanted = set(names or ())
    members = ctx.resource_members
    out: list[ResourceSpec] = []
    for row in ctx.resources:
        name = str(row.get("name") or "")
        if not name or name.lower() == "none":
            continue
        if wanted and name not in wanted:
            continue
        try:
            units = int(float(row.get("num_in") or 1))
        except (TypeError, ValueError):
            units = 1
        load = 0.0
        for b in members.get(name, []):
            rate = ctx.failure_rate(b) or 0.0
            repair = ctx.mean_repair(b) or 0.0
            load += rate * repair * _int(b.get("repair_resources_needed"), 1)
            freq = _float(b.get("pm_freq"))
            if on(b.get("uses_pm")) and freq > 0:
                load += ((ctx.mean_pm(b) or 0.0) / freq
                         * _int(b.get("pm_resources_needed"), 1))
        out.append(ResourceSpec(name, units, load))
    return out


def _int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def optimise_resources(  # noqa: C901, PLR0912
    workspace: Any,
    model_id: str,
    *,
    resources: Sequence[str] | None = None,
    target: float | None = 0.995,
    metric: str = "availability",
    extra: int = 3,
    trials: int | None = None,
    seed: int | None = 1,
    workers: int = 1,
    timeout_s: float = 1800.0,
    confidence: float = 0.95,
    progress: Any = None,
    abort: Any = None,
) -> dict[str, Any]:
    """Evaluate crew sizes around the offered-load seed and recommend one."""
    from raptor.api.batch import run_variants
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run

    specs = resources_from_model(workspace.get(model_id).rbd, resources)
    if not specs:
        raise ValueError("no repair resource in this model")
    variants = [{"label": "Base", "assignments": []}]
    plan: list[tuple[str, int, str]] = []
    for spec in specs:
        start = max(1, math.ceil(spec.offered_load))
        low = max(1, min(spec.units, start) - 1)
        for units in range(low, max(spec.units, start) + extra + 1):
            variants.append({"label": f"{spec.name}: {units}",
                             "assignments": [
                                 f"resource:{spec.name}.units={units}"]})
            plan.append((spec.name, units, f"resource:{spec.name}.units={units}"))
    results = run_variants(
        workspace, model_id, variants, num_runs=trials, workers=workers,
        timeout_s=timeout_s, seed=seed, progress=progress, abort=abort)

    def rows_for(result: dict) -> list[dict]:
        run_id = result.get("run_id")
        if not run_id:
            return []
        try:
            return read_per_run(run_dir(workspace, run_id), run_id) or []
        except Exception:  # noqa: BLE001
            return []

    base_rows = rows_for(results[0]) if results else []
    base_values = [float(row[metric]) for row in base_rows
                   if isinstance(row.get(metric), (int, float))]
    rows: list[dict[str, Any]] = []
    for index, (name, units, _assignment) in enumerate(plan, start=1):
        result = results[index] if index < len(results) else {}
        values = [float(row[metric]) for row in rows_for(result)
                  if isinstance(row.get(metric), (int, float))]
        mean, sem, lower = _stats(values, confidence)
        rows.append({"resource": name, "units": units, "mean": mean,
                     "sem": sem, "lower": lower,
                     "delta": (sum(values[i] - base_values[i]
                                   for i in range(min(len(values),
                                                     len(base_values))))
                               / max(1, min(len(values), len(base_values)))
                               if len(values) == len(base_values) and values
                               else None),
                     "error": result.get("error")})
    recommendations = []
    for spec in specs:
        group = [row for row in rows if row["resource"] == spec.name
                 and row["mean"] is not None]
        if not group:
            continue
        sign = -1 if metric == "mdt" else 1
        meeting = [row for row in group
                   if row["lower"] is not None and target is not None
                   and (row["lower"] >= target if sign > 0
                        else 2 * row["mean"] - row["lower"] <= target)]
        if meeting:
            best = min(meeting, key=lambda row: row["units"])
            why = "smallest size whose lower bound meets the target"
        else:
            gains = sorted(group, key=lambda row: row["units"])
            best = max(gains, key=lambda row: sign * (row["mean"] or 0))
            why = ("no size met the target; the best measured size is "
                   "shown")
        recommendations.append({"resource": spec.name,
                                "units": best["units"],
                                "mean": best["mean"], "lower": best["lower"],
                                "why": why, "curve": sorted(
                                    group, key=lambda row: row["units"])})
    return {
        "metric": metric, "target": target, "confidence": confidence,
        "seed": seed, "trials": trials, "batches": len(results),
        "resources": [spec.to_dict() for spec in specs],
        "base": {"mean": (sum(base_values) / len(base_values)
                          if base_values else None)},
        "rows": rows, "recommendations": recommendations,
        "caption": _caption(recommendations),
        "note": ("Paired against Base with the same seed; a size meets the "
                 "target only when its lower confidence bound does."),
    }


def _stats(values: list[float], confidence: float
           ) -> tuple[float | None, float | None, float | None]:
    from raptor.api.scenarios.stopping import t_critical
    n = len(values)
    if n < 2:
        mean = values[0] if values else None
        return mean, None, mean
    mean = sum(values) / n
    sem = math.sqrt(sum((value - mean) ** 2 for value in values)
                    / (n - 1) / n)
    return mean, sem, mean - t_critical(n - 1, confidence) * sem


def _caption(recommendations: list[dict[str, Any]]) -> str:
    if not recommendations:
        return "No resource could be sized."
    first = recommendations[0]
    return (f"{first['resource']}: {first['units']} "
            f"({'meets the target' if 'meets' in first['why'] else 'best measured'}).")

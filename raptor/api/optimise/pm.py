"""Is planned maintenance worthwhile, and at what interval (WP-4.2).

The analytic screen is the age-replacement cost rate for a Weibull life:

    C(tau) = (Cp * R(tau) + Cf * F(tau)) / integral_0^tau R(u) du

No interior minimum means run to failure.  The analytic optimum ignores
redundancy, spares, crews and staggering, so it is only a seed: a handful of
intervals around it are then simulated with common random numbers, together
with "no PM", and the best measured interval is reported.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PmAdvice:
    block: str
    block_id: str
    shape: float | None
    scale: float | None
    worthwhile: bool | None
    interval: float | None
    reason: str
    cp: float
    cf: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _survival(t: float, shape: float, scale: float) -> float:
    return math.exp(-((t / scale) ** shape)) if t > 0 else 1.0


def _failure(t: float, shape: float, scale: float) -> float:
    return 1.0 - _survival(t, shape, scale)


def cost_rate(tau: float, shape: float, scale: float, cp: float,
              cf: float) -> float:
    """Age-replacement cost per unit time at interval `tau`."""
    if tau <= 0:
        return cf / max(scale * math.gamma(1 + 1 / shape), 1e-12)
    steps = 200
    integral = 0.0
    for i in range(steps):
        t = tau * (i + 0.5) / steps
        integral += _survival(t, shape, scale)
    integral *= tau / steps
    if integral <= 0:
        return float("inf")
    return (cp * _survival(tau, shape, scale)
            + cf * _failure(tau, shape, scale)) / integral


def weibull_optimum(shape: float, scale: float, cp: float,
                    cf: float) -> float | None:
    """The interval minimising the cost rate, or None for no interior one."""
    if shape <= 1.0 or scale <= 0 or cf <= 0:
        return None
    high = scale * max(1.0, 3.0 / max(shape - 1, 1e-9))
    best_t, best = None, float("inf")
    steps = 200
    for i in range(1, steps + 1):
        tau = high * i / steps
        value = cost_rate(tau, shape, scale, cp, cf)
        if value < best:
            best_t, best = tau, value
    if best_t is None:
        return None
    low = max(high / steps, best_t - high / steps)
    upper = min(high, best_t + high / steps)
    for _ in range(40):
        third = (upper - low) / 3
        left = low + third
        right = upper - third
        if cost_rate(left, shape, scale, cp, cf) < cost_rate(
                right, shape, scale, cp, cf):
            upper = right
        else:
            low = left
    return (low + upper) / 2


def _costs(row: dict[str, Any]) -> tuple[float, float]:
    try:
        cp = (float(row.get("pmFixedCost", 0) or 0)
              + float(row.get("pmCost", 0) or 0))
    except (TypeError, ValueError):
        cp = 0.0
    try:
        cf = (float(row.get("repFixedCost", 0) or 0)
              + float(row.get("repPerTime", 0) or 0)
              + float(row.get("perSpare", 0) or 0))
    except (TypeError, ValueError):
        cf = 0.0
    return max(cp, 1.0), max(cf, 1.0)


def screen(rbd: Any, blocks: Sequence[str] | None = None) -> list[PmAdvice]:
    """Analytic advice per block: worthwhile, first interval, and why."""
    from raptor.core.engine.diststats import compute_stats

    wanted = set(blocks or ())
    out: list[PmAdvice] = []
    for row in rbd.section("BLOCK_DATA").rows:
        if str(row.get("ID", "0")) == "0":
            continue
        name = str(row.get("blockName") or row.get("ID"))
        if wanted and name not in wanted:
            continue
        try:
            code = int(float(row.get("FDist", 18) or 18))
        except (TypeError, ValueError):
            code = 18
        cp, cf = _costs(row)
        shape = scale = None
        if code == 15:
            try:
                shape = float(row.get("FParam1", 0) or 0)
                scale = float(row.get("FParam2", 0) or 0)
            except (TypeError, ValueError):
                shape = scale = None
        stats = compute_stats(code, [
            float(row.get("FParam1", 0) or 0),
            float(row.get("FParam2", 0) or 0),
            float(row.get("FParam3", 0) or 0)])
        mean = float(stats[0]) if stats and isinstance(stats[0], (int, float)) \
            else None
        if code != 15 or shape is None or scale is None or scale <= 0:
            out.append(PmAdvice(
                name, str(row.get("ID")), shape, scale, None,
                mean if mean and mean > 0 else None,
                "simulation only: the life law has no closed-form age-"
                "replacement optimum here", cp, cf))
            continue
        if shape <= 1.0:
            out.append(PmAdvice(
                name, str(row.get("ID")), shape, scale, False, None,
                "not worthwhile: constant or falling failure rate", cp, cf))
            continue
        interval = weibull_optimum(shape, scale, cp, cf)
        if interval is None:
            out.append(PmAdvice(
                name, str(row.get("ID")), shape, scale, False, None,
                "run to failure: the cost rate has no interior minimum",
                cp, cf))
        else:
            out.append(PmAdvice(
                name, str(row.get("ID")), shape, scale, True, interval,
                f"analytic optimum about {interval:.4g}", cp, cf))
    return out


def optimise_pm(  # noqa: C901, PLR0912
    workspace: Any,
    model_id: str,
    *,
    blocks: Sequence[str] | None = None,
    objective: str = "availability",
    trials: int | None = None,
    seed: int | None = 1,
    workers: int = 1,
    timeout_s: float = 1800.0,
    progress: Any = None,
    abort: Any = None,
) -> dict[str, Any]:
    """Screen each block, then simulate around its analytic interval."""
    from raptor.api.batch import run_variants
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run

    if objective not in ("cost", "availability"):
        raise ValueError("objective must be 'cost' or 'availability'")
    rbd = workspace.get(model_id).rbd
    advice = screen(rbd, blocks)
    metric = "grand_total" if objective == "cost" else "availability"
    variants = [{"label": "Base", "assignments": []}]
    plan: list[tuple[str, str, list[str]]] = []
    for item in advice:
        intervals: list[tuple[str, float | None]] = [("no PM", None)]
        if item.interval:
            for factor in (0.5, 0.75, 1.0, 1.5, 2.0):
                intervals.append((f"{factor:.2f} x optimum",
                                  item.interval * factor))
        for label, interval in intervals:
            if interval is None:
                assignments = [
                    f"block:{item.block_id}.uses_pm=false"]
            else:
                assignments = [
                    f"block:{item.block_id}.uses_pm=true",
                    f"block:{item.block_id}.pm_freq={interval:.10g}"]
            variants.append({"label": f"{item.block}: {label}",
                             "assignments": assignments})
            plan.append((item.block, label, assignments))
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
    base_values = _values(base_rows, metric)
    rows: list[dict[str, Any]] = []
    for index, (block, label, _assignments) in enumerate(plan, start=1):
        result = results[index] if index < len(results) else {}
        values = _values(rows_for(result), metric)
        mean, sem = _stat(values)
        delta = _paired(base_values, values)
        row = {"block": block, "option": label,
               "mean": mean, "sem": sem, "delta": delta,
               "run_id": result.get("run_id"), "error": result.get("error")}
        rows.append(row)
    by_block: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_block.setdefault(row["block"], []).append(row)
    recommendations = []
    for block, group in by_block.items():
        usable = [row for row in group if row["mean"] is not None]
        if not usable:
            continue
        best = (min(usable, key=lambda row: row["mean"])
                if objective == "cost"
                else max(usable, key=lambda row: row["mean"]))
        no_pm = next((row for row in group if row["option"] == "no PM"), None)
        worth = ((best["mean"] < no_pm["mean"] if objective == "cost"
                  else best["mean"] > no_pm["mean"])
                 if no_pm is not None and best["mean"] is not None
                 and no_pm["mean"] is not None else False)
        worthwhile = best["option"] != "no PM" and worth
        recommendations.append({
            "block": block, "option": best["option"],
            "mean": best["mean"], "delta_vs_base": best["delta"],
            "worthwhile": worthwhile,
            "curve": sorted(group, key=lambda row: row["mean"]
                            if row["mean"] is not None else 0.0)})
    return {
        "objective": objective, "metric": metric, "trials": trials,
        "seed": seed, "batches": len(results), "advice": [
            item.to_dict() for item in advice],
        "base": {"mean": _stat(base_values)[0]},
        "rows": rows, "recommendations": recommendations,
        "caption": _caption(recommendations, objective),
        "note": ("The analytic screen assumes age replacement on one block; "
                 "the recommendation comes from the paired simulation, not "
                 "from the formula."),
    }


def _values(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows
            if isinstance(row.get(key), (int, float))
            and not isinstance(row.get(key), bool)]


def _stat(values: list[float]) -> tuple[float | None, float | None]:
    n = len(values)
    if n == 0:
        return None, None
    mean = sum(values) / n
    sem = (math.sqrt(sum((value - mean) ** 2 for value in values)
                     / (n - 1) / n) if n > 1 else None)
    return mean, sem


def _paired(base: list[float], other: list[float]) -> float | None:
    n = min(len(base), len(other))
    if n < 2:
        return None
    return sum(other[i] - base[i] for i in range(n)) / n


def _caption(recommendations: list[dict[str, Any]], objective: str) -> str:
    useful = [row for row in recommendations if row["worthwhile"]]
    if not recommendations:
        return "No block could be screened."
    if not useful:
        return ("No screened interval beat running to failure; keep the "
                "current plan.")
    best = useful[0]
    return (f"{best['block']}: {best['option']} is worthwhile on "
            f"{objective} ({best['mean']:.5g}).")

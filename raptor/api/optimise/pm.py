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


def _ratio_inputs(ctx: Any, b: dict[str, Any], objective: str
                  ) -> tuple[float, float]:
    """(cost of a planned renewal, cost of a failure) for the screen.

    For `availability` both are hours of downtime: the mean PM duration
    against the mean repair plus logistics delay.  For `cost` they are
    money: the fixed charge plus the hourly rate times the mean duration,
    and a spare per failure.  The per-hour rates are rates, so they are
    multiplied by a duration before being added to a fixed charge.
    """
    pm_time = ctx.mean_pm(b) or 0.0
    repair_time = (ctx.mean_repair(b) or 0.0) + ctx.mean_ldt(b)
    if objective == "availability":
        return max(pm_time, 1e-9), max(repair_time, 1e-9)

    def money(key: str) -> float:
        try:
            return float(b.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    cp = money("pm_fixed_cost") + money("pm_cost") * pm_time
    cf = (money("rep_fixed_cost") + money("rep_per_time_cost") * repair_time
          + money("per_spare_cost"))
    return cp, cf


def screen(rbd: Any, blocks: Sequence[str] | None = None,
           objective: str = "availability") -> list[PmAdvice]:
    """Analytic advice per block: worthwhile, first interval, and why.

    The ratio that decides the optimum is downtime per PM against
    downtime per failure for `availability`, money for `cost`.  A block
    whose failure costs no more than its PM has no interior optimum.
    """
    from raptor.api.advisor.context import ModelContext

    ctx = ModelContext(rbd)
    wanted = set(blocks or ())
    unknown = wanted - {str(b.get("name") or b.get("id")) for b in ctx.blocks}
    if unknown:
        # a misspelt name used to be dropped and the study ran on nothing
        raise ValueError(f"no block named {', '.join(sorted(map(repr, unknown)))}")
    out: list[PmAdvice] = []
    for b in ctx.blocks:
        name = str(b.get("name") or b.get("id"))
        block_id = str(b.get("id"))
        if wanted and name not in wanted:
            continue
        cp, cf = _ratio_inputs(ctx, b, objective)
        code = int(b.get("fail_dist") or 0)
        params = [float(v or 0) for v in (b.get("fail_params") or [])]
        mean = ctx.mean_life(b)
        if code == 4:
            # memoryless: a renewed part fails as soon as the old one would
            out.append(PmAdvice(
                name, block_id, 1.0, mean, False, None,
                "not worthwhile: an exponential life has a constant failure "
                "rate, so replacing early buys nothing", cp, cf))
            continue
        if code != 15 or len(params) < 2 or params[1] <= 0:
            out.append(PmAdvice(
                name, block_id, None, None, None,
                mean if mean and mean > 0 else None,
                "simulation only: the life law has no closed-form age-"
                "replacement optimum here", cp, cf))
            continue
        shape, scale = params[0], params[1]
        location = params[2] if len(params) > 2 else 0.0
        if shape <= 1.0:
            out.append(PmAdvice(
                name, block_id, shape, scale, False, None,
                "not worthwhile: constant or falling failure rate", cp, cf))
            continue
        if cp <= 0 and cf <= 0:
            out.append(PmAdvice(
                name, block_id, shape, scale, None, None,
                f"no {'durations' if objective == 'availability' else 'costs'}"
                " to weigh: simulation only", cp, cf))
            continue
        interval = weibull_optimum(shape, scale, max(cp, 1e-9), cf)
        if interval is None or cf <= cp:
            out.append(PmAdvice(
                name, block_id, shape, scale, False, None,
                "run to failure: a failure costs no more than a PM, so the "
                "cost rate has no interior minimum", cp, cf))
        else:
            interval += max(location, 0.0)
            out.append(PmAdvice(
                name, block_id, shape, scale, True, interval,
                f"analytic optimum about {interval:.4g}", cp, cf))
    return out


def optimise_pm(  # noqa: C901, PLR0912
    workspace: Any,
    model_id: str,
    *,
    blocks: Sequence[str] | None = None,
    objective: str = "availability",
    trials: int | None = None,
    seed: int | None = None,
    workers: int = 1,
    timeout_s: float = 1800.0,
    progress: Any = None,
    abort: Any = None,
) -> dict[str, Any]:
    """Screen each block, then simulate around its analytic interval."""
    from raptor.api.batch import cut_short, require_measured, run_variants
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run

    if objective not in ("cost", "availability"):
        raise ValueError("objective must be 'cost' or 'availability'")
    rbd = workspace.get(model_id).rbd
    if objective == "cost" and str(
            rbd.section("SYSTEM_DATA").rows[0].get("costAnalysis") or "0"
            ).strip().upper() in ("0", "F", "FALSE", ""):
        # without cost analysis a run has no Grand Total, so every option
        # used to come back empty and the study reported nothing screened
        raise ValueError("the cost objective needs cost analysis: turn it on "
                         "(raptor set MODEL system.cost=true) or use the "
                         "availability objective")
    advice = screen(rbd, blocks, objective)
    metric = "grand_total" if objective == "cost" else "availability"
    variants = [{"label": "Base", "assignments": []}]
    plan: list[tuple[str, str, list[str]]] = []
    for item in advice:
        intervals: list[tuple[str, float | None]] = [("no PM", None)]
        if item.interval:
            # around the formula's optimum, or (no closed form) the mean life
            anchor = "optimum" if item.worthwhile else "mean life"
            for factor in (0.5, 0.75, 1.0, 1.5, 2.0):
                intervals.append((f"{factor:.2f} x {anchor}",
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
    short = cut_short(results, timeout_s)

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
    for index, (block, label, assignments) in enumerate(plan, start=1):
        result = results[index] if index < len(results) else {}
        values = _values(rows_for(result), metric)
        mean, sem = _stat(values)
        rows.append({"block": block, "option": label, "changes": assignments,
                     "mean": mean, "sem": sem, "delta": _paired(base_values, values),
                     "run_id": result.get("run_id"), "error": result.get("error"),
                     "_values": values})
    require_measured(short, any(row["mean"] is not None for row in rows))
    by_block: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_block.setdefault(row["block"], []).append(row)
    recommendations = [rec for block, group in by_block.items()
                       if (rec := _recommend(block, group, objective)) is not None]
    for row in rows:
        row.pop("_values", None)
        row.pop("run_id", None)          # the study's own runs are discarded
    from raptor.api.batch import discard_runs
    discard_runs(workspace, results)
    return {
        "objective": objective, "metric": metric, "trials": trials,
        "seed": seed, "batches": len(results), "advice": [
            item.to_dict() for item in advice],
        "base": {"mean": _stat(base_values)[0]},
        "rows": rows, "recommendations": recommendations,
        # PATH=VALUE edits for the recommendations worth making
        "changes": [c for r in recommendations for c in r["changes"]],
        "caption": ((short + " " if short else "")
                    + _caption(recommendations, objective)),
        "cut_short": short,
        "note": ("The analytic screen assumes age replacement on one block; "
                 "the recommendation comes from the paired simulation, not "
                 "from the formula, and is worth making only when its paired "
                 "gain over no PM exceeds twice its standard error. The best "
                 "of several intervals is picked on the same runs that measure "
                 "it, so its gain is a little optimistic: confirm it with What "
                 "If on fresh runs before relying on its size."),
    }


def _recommend(block: str, group: list[dict[str, Any]],
               objective: str) -> dict[str, Any] | None:
    """The best option for one block, and whether it is worth making."""
    usable = [row for row in group if row["mean"] is not None]
    if not usable:
        return None
    best = (min(usable, key=lambda row: row["mean"]) if objective == "cost"
            else max(usable, key=lambda row: row["mean"]))
    no_pm = next((row for row in group if row["option"] == "no PM"), None)
    # worthwhile only when the paired gain over no PM is told from noise
    gain, gain_sem = (_paired_stat(no_pm["_values"], best["_values"])
                      if no_pm is not None else (None, None))
    sign = -1 if objective == "cost" else 1
    worthwhile = (best["option"] != "no PM" and gain is not None
                  and sign * gain > 2 * (gain_sem or 0.0) and sign * gain > 0)
    return {
        "block": block, "option": best["option"],
        "mean": best["mean"], "delta_vs_base": best["delta"],
        "worthwhile": worthwhile,
        # 0 when the formula ruled PM out and only "no PM" was run
        "options_simulated": sum(1 for row in usable if row["option"] != "no PM"),
        "gain_vs_no_pm": {"mean": gain, "sem": gain_sem},
        "changes": list(best["changes"]) if worthwhile else [],
        "curve": sorted(group, key=lambda row: row["mean"]
                        if row["mean"] is not None else 0.0)}


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


def _paired_stat(base: list[float], other: list[float]
                 ) -> tuple[float | None, float | None]:
    """Mean and standard error of the paired differences `other - base`."""
    n = min(len(base), len(other))
    if n < 2:
        return None, None
    return _stat([other[i] - base[i] for i in range(n)])


def _paired(base: list[float], other: list[float]) -> float | None:
    n = min(len(base), len(other))
    if n < 2:
        return None
    return sum(other[i] - base[i] for i in range(n)) / n


def _caption(recommendations: list[dict[str, Any]], objective: str) -> str:
    useful = [row for row in recommendations if row["worthwhile"]]
    if not recommendations:
        return "No block could be screened."
    tried = [row for row in recommendations if row.get("options_simulated")]
    if useful:
        best = useful[0]
        text = (f"{best['block']}: {best['option']} is worthwhile on "
                f"{objective} ({best['mean']:.5g}).")
    elif tried:
        text = ("No simulated interval beat running to failure; keep the "
                "current plan.")
    else:
        text = "No PM interval was simulated."
    # A block the formula rules out has only "no PM" run: say so, rather
    # than let "keep" read as a simulated verdict.
    ruled_out = [row["block"] for row in recommendations
                 if not row.get("options_simulated")]
    if ruled_out:
        names = ", ".join(ruled_out[:4]) + (f" and {len(ruled_out) - 4} more"
                                            if len(ruled_out) > 4 else "")
        text += (f" {names}: the formula rules PM out (a constant or falling "
                 "failure rate), so no interval was simulated.")
    return text

"""How many spares to hold (WP-4.1).

The analytic seed is a single-echelon Poisson backorder model: each pool's
demand rate comes from the blocks drawing on it, its lead time from the
ordering settings, and EBO(s) is the expected number of units short with
`s` on the shelf.  Greedy marginal analysis turns that into a path of
one-unit increments, cheapest expected backorder reduction first.  The path
is then simulated, every point with the same seed, and a point counts as
meeting the target only when its pessimistic 95 % confidence bound does
(the lower bound for availability or reliability, the upper one for MDT).
The lead time comes from the pool's own resupply rule; see `lead_time`.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from raptor.api.scenarios.stopping import t_critical

METRICS = ("availability", "reliability", "mdt")


@dataclass(frozen=True)
class PoolSpec:
    name: str
    stock: int
    cost: float
    demand: float            # failures per unit time among its users
    lead_time: float         # ordering lead time, same unit
    max_stock: int = 20

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def label(self) -> str:
        return f"pool:{self.name}.stock"

    def assignments(self, stock: int) -> list[str]:
        return [f"{self.label}={stock}"]


def poisson_ebo(demand_over_lead: float, stock: int) -> float:
    """Expected units short with `stock` on the shelf (Poisson demand)."""
    if demand_over_lead <= 0:
        return 0.0
    term = math.exp(-demand_over_lead)
    ebo = 0.0
    # Sum P(D=k) until the tail is negligible; the cut is far enough out
    # that its contribution is below the printed precision.
    for k in range(1, max(stock + 1, int(demand_over_lead * 3) + 20)):
        term *= demand_over_lead / k
        if k > stock:
            ebo += (k - stock) * term
        if k > stock and term < 1e-12 and k > demand_over_lead * 3:
            break
    return ebo


def lead_time(pool: dict[str, Any], mission: float | None) -> tuple[float, str]:
    """How long a used spare takes to come back, and which rule set it.

    Stock-level ordering and emergency orders replace a used spare after
    their own delay; routine ordering refills every `arrive_every`; a pool
    with no resupply loses each spare for the rest of the run, so its
    "lead time" is the run length.
    """
    from raptor.api.advisor.context import on

    def num(key: str) -> float:
        try:
            return float(pool.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    if on(pool.get("slo")) and num("slo_time") > 0:
        return num("slo_time"), "stock-level order"
    if on(pool.get("eso")) and num("emer_time") > 0:
        return num("emer_time"), "emergency order"
    if on(pool.get("rso")) and num("arrive_every") > 0:
        return num("arrive_every"), "routine restock interval"
    return (mission or 8760.0), "no resupply: the whole run"


def pools_from_model(rbd: Any, names: Sequence[str] | None = None
                     ) -> list[PoolSpec]:
    """Finite pools with their demand and lead time, or the named ones.

    Demand is the summed failure rate (duty cycle applied) of the blocks
    that draw on the pool.
    """
    from raptor.api.advisor.context import ModelContext

    ctx = ModelContext(rbd)
    wanted = set(names or ())
    unknown = wanted - {str(pool.get("name") or "") for pool in ctx.pools}
    if unknown:
        raise ValueError(f"no spare pool named {', '.join(sorted(map(repr, unknown)))}")
    members = ctx.pool_members
    out: list[PoolSpec] = []
    for pool in ctx.pools:
        name = str(pool.get("name") or "")
        if not name or (wanted and name not in wanted):
            continue
        demand = sum(ctx.failure_rate(b) or 0.0
                     for b in members.get(name, []))
        if demand <= 0:
            continue
        try:
            stock = int(float(pool.get("stock") or 0))
            cost = float(pool.get("spare_cost") or 1)
        except (TypeError, ValueError):
            continue
        lead, _rule = lead_time(pool, ctx.run_length)
        out.append(PoolSpec(name, stock, cost if cost > 0 else 1.0,
                            demand, lead, _stock_ceiling(demand * lead, stock)))
    return out


#: Expected units short below which one more spare cannot matter.
NEGLIGIBLE_EBO = 1e-6


def _stock_ceiling(demand_over_lead: float, stock: int) -> int:
    """Most stock the search considers for a pool: well past where Poisson
    demand over the lead time could reach, and past what it holds today."""
    mean = max(demand_over_lead, 0.0)
    return max(20, stock + 1, math.ceil(mean + 6 * math.sqrt(mean) + 3))


def analytic_path(pools: Sequence[PoolSpec],
                  max_add: int | None = None) -> list[dict[str, Any]]:
    """Greedy one-unit increments, largest EBO reduction per unit cost first,
    until every pool is at its ceiling or no unit can reduce the expected
    shortfall measurably (`max_add` caps the total units added)."""
    stocks = {pool.name: 0 for pool in pools}
    path: list[dict[str, Any]] = []
    budget = max_add if max_add is not None else sum(pool.max_stock for pool in pools)
    for _ in range(max(budget, 0)):
        best = None
        for pool in pools:
            current = stocks[pool.name]
            if current >= pool.max_stock:
                continue
            ebo = poisson_ebo(pool.demand * pool.lead_time, current)
            if ebo < NEGLIGIBLE_EBO:
                continue
            nxt = poisson_ebo(pool.demand * pool.lead_time, current + 1)
            gain = (ebo - nxt) / max(pool.cost, 1e-12)
            if best is None or gain > best[0]:
                best = (gain, pool)
        if best is None or best[0] <= 0:
            break
        pool = best[1]
        stocks[pool.name] += 1
        path.append({"stocks": dict(stocks), "gained": best[0],
                     "pool": pool.name})
    return path


def _cost(stocks: dict[str, int], pools: Sequence[PoolSpec]) -> float:
    return sum(stocks.get(pool.name, 0) * pool.cost for pool in pools)


def _assignments(stocks: dict[str, int], pools: Sequence[PoolSpec]) -> list[str]:
    out: list[str] = []
    for pool in pools:
        out.extend(pool.assignments(stocks.get(pool.name, 0)))
    return out


def _stat(rows: list[dict], metric: str, confidence: float
          ) -> tuple[float | None, float | None, float | None]:
    """Mean, standard error and the lower confidence bound."""
    values = [float(row[metric]) for row in rows
              if isinstance(row.get(metric), (int, float))
              and not isinstance(row.get(metric), bool)]
    n = len(values)
    if n < 2:
        mean = values[0] if values else None
        return mean, None, mean
    mean = sum(values) / n
    sem = math.sqrt(sum((value - mean) ** 2 for value in values)
                    / (n - 1) / n)
    return mean, sem, mean - t_critical(n - 1, confidence) * sem


def optimise_spares(  # noqa: C901, PLR0912
    workspace: Any,
    model_id: str,
    *,
    mode: str = "meet",
    target: float | None = 0.995,
    metric: str = "availability",
    pools: Sequence[str] | None = None,
    max_evaluations: int = 40,
    trials: int | None = None,
    seed: int | None = None,
    workers: int = 1,
    confidence: float = 0.95,
    timeout_s: float = 1800.0,
    progress: Any = None,
    abort: Any = None,
) -> dict[str, Any]:
    """Simulate the analytic stock path and recommend a point.

    `mode="meet"` returns the cheapest evaluated point whose lower
    confidence bound meets `target`.  `mode="curve"` returns the whole
    evaluated frontier and stops when three moves in a row cannot be told
    from no gain.  The model is never modified.
    """
    if mode not in ("meet", "curve"):
        raise ValueError("mode must be 'meet' or 'curve'")
    if mode == "meet" and target is None:
        raise ValueError("mode='meet' needs a target")
    selected = pools_from_model(workspace.get(model_id).rbd, pools)
    if not selected:
        raise ValueError("no finite spare pool with demand in this model")
    path = analytic_path(selected)
    base_rows = _evaluate(workspace, model_id, [],
                          trials=trials, seed=seed, workers=workers,
                          timeout_s=timeout_s, abort=abort)
    base_mean, base_sem, base_lower = _stat(base_rows, metric, confidence)
    zero = {pool.name: 0 for pool in selected}
    candidates = [zero] + [entry["stocks"] for entry in path]
    points, evaluations, recommended = _walk(
        workspace, model_id, selected, candidates,
        mode=mode, target=target, metric=metric, max_evaluations=max_evaluations,
        trials=trials, seed=seed, workers=workers, confidence=confidence,
        timeout_s=timeout_s, progress=progress, abort=abort)
    sign = _better(metric)
    met = recommended is not None
    if mode == "curve" and points:
        recommended = max(points,
                          key=lambda point: sign * (point["mean"] or 0))
    elif recommended is None and points:
        # Nothing met the target: report the best evaluated point, and say
        # so in the caption, rather than present it as meeting the target.
        recommended = max(
            points, key=lambda point: sign * (
                (point["lower"] if sign > 0 else point["upper"]) or 0))
    costs_default = all(abs(pool.cost - 1.0) < 1e-12 for pool in selected)
    stopped_by = _stopped_by(met, mode, abort, evaluations, max_evaluations,
                             candidates[-1], points)
    caption = _caption(recommended, target, metric, costs_default,
                       met or mode == "curve")
    held = sum(pool.stock for pool in selected)
    if recommended is not None and sum(recommended["stocks"].values()) < held:
        caption += (f" That is fewer than the {held} held today: the extra "
                    "stock buys no measurable availability at this run count.")
    if not met and mode == "meet":
        caption += {
            "search ceiling": (
                f" The search reached its ceiling ({sum(candidates[-1].values())} "
                "spares in all) without meeting the target: spares alone may "
                "not get there."),
            "evaluation limit": (
                f" It stopped at the limit of {max_evaluations} evaluations; "
                "allow more evaluations to search further."),
            "cancelled": " The search was stopped before it finished.",
        }.get(stopped_by, "")
    return {
        "mode": mode, "metric": metric, "target": target,
        "confidence": confidence,
        "pools": [pool.to_dict() for pool in selected],
        "base": {"stocks": {pool.name: pool.stock for pool in selected},
                 "mean": base_mean, "sem": base_sem, "lower": base_lower,
                 "cost": _cost({pool.name: pool.stock for pool in selected},
                               selected)},
        "points": points,
        "recommended": recommended,
        # PATH=VALUE edits that give the model the recommended stock; a pool
        # already at it is left out (it used to be "changed" to its own stock)
        "changes": _assignments(
            (recommended or {}).get("stocks") or {},
            [pool for pool in selected
             if (recommended or {}).get("stocks", {}).get(pool.name,
                                                          pool.stock)
             != pool.stock]),
        "evaluations": evaluations,
        "max_evaluations": max_evaluations,
        "costs_default": costs_default,
        "target_met": met if mode == "meet" else None,
        "lead_times": _lead_rules(workspace, model_id, selected),
        "stopped_by": stopped_by,
        "search_ceiling": {pool.name: pool.max_stock for pool in selected},
        "caption": caption,
        "note": ("A point meets the target only when its lower confidence "
                 "bound does; the analytic path is a seed, not a result."),
        "batches": evaluations,
    }


def _stopped_by(met: bool, mode: str, abort: Any, evaluations: int,
                max_evaluations: int, last: dict[str, int],
                points: list[dict[str, Any]]) -> str:
    """Why the walk ended, in words a caption can use."""
    if met and mode == "meet":
        return "target met"
    if abort is not None and getattr(abort, "is_set", lambda: False)():
        return "cancelled"
    if any(point["stocks"] == last for point in points):
        return "search ceiling"
    if evaluations >= max_evaluations:
        return "evaluation limit"
    return "no further gain"


def _measure(workspace: Any, model_id: str, stocks: dict[str, int],
             selected: Sequence[PoolSpec], *, trials: int | None,
             seed: int | None, workers: int, confidence: float,
             timeout_s: float, abort: Any,
             metric: str = "availability") -> dict[str, Any] | None:
    rows = _evaluate(workspace, model_id,
                     _assignments(stocks, selected), trials=trials,
                     seed=seed, workers=workers, timeout_s=timeout_s,
                     abort=abort)
    mean, sem, lower = _stat(rows, metric, confidence)
    upper = (None if mean is None or lower is None
             else mean + (mean - lower))
    return {"stocks": dict(stocks), "cost": _cost(stocks, selected),
            "changes": _assignments(stocks, selected),
            "mean": mean, "sem": sem, "lower": lower, "upper": upper,
            "metric": metric}


def _walk(  # noqa: C901, PLR0912
          workspace: Any, model_id: str, selected: Sequence[PoolSpec],
          candidates: list[dict[str, int]], *,
          mode: str, target: float | None, metric: str, max_evaluations: int,
          trials: int | None, seed: int | None, workers: int,
          confidence: float, timeout_s: float, progress: Any, abort: Any
          ) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    """Evaluate the geometrically spaced path until the stopping rule."""
    order: list[int] = []
    step, index = 1, 0
    while index < len(candidates):
        order.append(index)
        index += step
        step = min(step * 2, 8)
    if order[-1] != len(candidates) - 1:
        order.append(len(candidates) - 1)
    points: list[dict[str, Any]] = []
    evaluations = 1
    recommended = None
    for position, index in enumerate(order):
        if abort is not None and getattr(abort, "is_set", lambda: False)():
            break
        if evaluations >= max_evaluations:
            break
        point = _measure(workspace, model_id, candidates[index], selected,
                         trials=trials, seed=seed, workers=workers,
                         confidence=confidence, timeout_s=timeout_s,
                         abort=abort, metric=metric)
        evaluations += 1
        if point is None:
            continue
        point["meets"] = _meets(point, target, metric)
        points.append(point)
        if progress:
            total = sum(point["stocks"].values())
            progress(evaluations, max_evaluations,
                     f"{total} spare{'' if total == 1 else 's'}")
        if mode == "meet" and point["meets"]:
            # The path was sampled with growing steps: look back between the
            # last point that missed and this one for the least stock that
            # still meets (more stock never needs fewer spares on the path).
            below = order[position - 1] if position > 0 else -1
            recommended, evaluations = _bisect(
                workspace, model_id, selected, candidates, below, index, point,
                evaluations, points, target=target, metric=metric,
                max_evaluations=max_evaluations, trials=trials, seed=seed,
                workers=workers, confidence=confidence, timeout_s=timeout_s,
                abort=abort)
            break
        if mode == "curve" and position >= 3:
            gains = _gains(points[-4:])
            if gains is not None and all(gain is None or gain <= 0
                                          for gain in gains):
                break
    points.sort(key=lambda p: (sum(p["stocks"].values()), p["cost"]))
    return points, evaluations, recommended


def _bisect(workspace: Any, model_id: str, selected: Sequence[PoolSpec],
            candidates: list[dict[str, int]], below: int, above: int,
            found: dict[str, Any], evaluations: int, points: list[dict[str, Any]],
            *, target: float | None, metric: str, max_evaluations: int,
            trials: int | None, seed: int | None, workers: int,
            confidence: float, timeout_s: float, abort: Any
            ) -> tuple[dict[str, Any], int]:
    """Least path point in (below, above] that meets the target; `above`
    (measured as `found`) meets it and `below` (or the start) does not."""
    best = found
    while above - below > 1 and evaluations < max_evaluations:
        if abort is not None and getattr(abort, "is_set", lambda: False)():
            break
        middle = (below + above) // 2
        point = _measure(workspace, model_id, candidates[middle], selected,
                         trials=trials, seed=seed, workers=workers,
                         confidence=confidence, timeout_s=timeout_s,
                         abort=abort, metric=metric)
        evaluations += 1
        if point is None:
            break
        point["meets"] = _meets(point, target, metric)
        points.append(point)
        if point["meets"]:
            above, best = middle, point
        else:
            below = middle
    return best, evaluations


def _better(metric: str) -> int:
    """+1 when a larger value is better, -1 for a time such as MDT."""
    return -1 if metric == "mdt" else 1


def _meets(point: dict[str, Any], target: float | None, metric: str) -> bool:
    """The pessimistic bound meets the target in the metric's direction."""
    if target is None or point.get("mean") is None:
        return False
    if _better(metric) > 0:
        return point["lower"] is not None and point["lower"] >= target
    upper = point.get("upper")
    return upper is not None and upper <= target


def _gains(points: list[dict[str, Any]]) -> list[float | None] | None:
    """Paired-ish gains between consecutive points, or None if too short."""
    if len(points) < 2:
        return None
    sign = _better(points[0].get("metric", "availability"))
    return [sign * ((points[i]["mean"] or 0) - (points[i - 1]["mean"] or 0))
            for i in range(1, len(points))]


def _evaluate(workspace: Any, model_id: str,
              assignments: list[str], *,
              trials: int | None, seed: int | None, workers: int,
              timeout_s: float, abort: Any) -> list[dict]:
    from raptor.api.batch import run_variants
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run

    results = run_variants(
        workspace, model_id,
        [{"label": "point", "assignments": assignments}],
        num_runs=trials, workers=workers, timeout_s=timeout_s, seed=seed,
        abort=abort)
    from raptor.api.batch import discard_runs
    row = results[0] if results else {}
    if row.get("error") or not row.get("run_id"):
        return []
    try:
        return read_per_run(run_dir(workspace, row["run_id"]),
                            row["run_id"]) or []
    except Exception:  # noqa: BLE001 - no rows is reported as no data
        return []
    finally:
        discard_runs(workspace, results)


def _lead_rules(workspace: Any, model_id: str,
                selected: Sequence[PoolSpec]) -> dict[str, str]:
    """Which resupply rule set each selected pool's lead time."""
    from raptor.api.advisor.context import ModelContext
    ctx = ModelContext(workspace.get(model_id).rbd)
    names = {pool.name for pool in selected}
    return {str(row.get("name")): lead_time(row, ctx.run_length)[1]
            for row in ctx.pools if str(row.get("name")) in names}


def _caption(recommended: dict[str, Any] | None, target: float | None,
             metric: str, costs_default: bool, met: bool = True) -> str:
    if recommended is None:
        return "No point was evaluated."
    stocks = ", ".join(f"{name} {stock}"
                       for name, stock in recommended["stocks"].items())
    bound_name, bound = (("lower bound", recommended["lower"])
                         if _better(metric) > 0
                         else ("upper bound", recommended.get("upper")))
    lead = "Recommended" if met else (
        "No evaluated stock met the target; the best was")
    def show(value: float | None) -> str:
        if value is None:
            return "unknown"
        return f"{100 * value:.4g} %" if metric == "availability" else f"{value:.5g}"
    text = (f"{lead}: {stocks} — {metric} {show(recommended['mean'])} "
            f"({bound_name} {show(bound)})")
    if target is not None:
        text += f" against a target of {show(target)}"
    if costs_default:
        text += "; all spare costs are 1, so this minimises the number held"
    return text + "."

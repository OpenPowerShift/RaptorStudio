"""How many spares to hold (WP-4.1).

The analytic seed is a single-echelon Poisson backorder model: each pool's
demand rate comes from the blocks drawing on it, its lead time from the
ordering settings, and EBO(s) is the expected number of units short with
`s` on the shelf.  Greedy marginal analysis turns that into a path of
one-unit increments, cheapest expected backorder reduction first.  The path
is then simulated with common random numbers: each point is paired against
Base, and a point counts as meeting the target only when the lower 95 %
confidence bound does.
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


def pools_from_model(rbd: Any, names: Sequence[str] | None = None
                     ) -> list[PoolSpec]:
    """Finite pools with their demand and lead time, or the named ones."""
    rows = rbd.section("SPARE_POOL_DATA").rows
    wanted = set(names or ())
    out: list[PoolSpec] = []
    for row in rows:
        name = str(row.get("PoolName") or "")
        if not name or (wanted and name not in wanted):
            continue
        try:
            stock = int(float(row.get("Stock", 0) or 0))
            cost = float(row.get("SpCost", 1) or 1)
            lead = float(row.get("ArriveEvery", 0) or 0)
        except (TypeError, ValueError):
            continue
        demand = _demand(rbd, name)
        if demand <= 0:
            continue
        out.append(PoolSpec(name, stock, cost if cost > 0 else 1.0,
                            demand, lead or 720.0))
    return out


def _demand(rbd: Any, pool: str) -> float:
    from raptor.api.distributions import dist_name  # noqa: F401 - documents codes
    from raptor.core.engine.diststats import compute_stats

    rate = 0.0
    for row in rbd.section("BLOCK_DATA").rows:
        if str(row.get("ID", "0")) == "0":
            continue
        if str(row.get("SparePoolName") or "") != pool:
            continue
        try:
            code = int(float(row.get("FDist", 18) or 18))
            count = int(float(row.get("#FParam", 2) or 2))
            params = [float(row.get(f"FParam{i + 1}", 0) or 0)
                      for i in range(min(3, max(1, count)))]
        except (TypeError, ValueError):
            continue
        stats = compute_stats(code, params)
        if not stats or not isinstance(stats[0], (int, float)):
            continue
        mean = float(stats[0])
        if mean > 0:
            rate += 1.0 / mean
    return rate


def analytic_path(pools: Sequence[PoolSpec],
                  max_add: int | None = None) -> list[dict[str, Any]]:
    """Greedy one-unit increments, largest EBO reduction per unit cost first."""
    stocks = {pool.name: 0 for pool in pools}
    path: list[dict[str, Any]] = []
    budget = max_add or max(1, 6)
    for _ in range(budget * max(1, len(pools))):
        best = None
        for pool in pools:
            current = stocks[pool.name]
            if current >= pool.max_stock:
                continue
            ebo = poisson_ebo(pool.demand * pool.lead_time, current)
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
    seed: int | None = 1,
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
    if mode == "curve" and points:
        recommended = max(points, key=lambda point: point["mean"] or 0)
    elif recommended is None and points:
        recommended = max(points, key=lambda point: point["lower"] or 0)
    costs_default = all(abs(pool.cost - 1.0) < 1e-12 for pool in selected)
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
        "evaluations": evaluations,
        "max_evaluations": max_evaluations,
        "costs_default": costs_default,
        "caption": _caption(recommended, target, metric, costs_default),
        "note": ("A point meets the target only when its lower confidence "
                 "bound does; the analytic path is a seed, not a result."),
        "batches": evaluations,
    }


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
    return {"stocks": dict(stocks), "cost": _cost(stocks, selected),
            "mean": mean, "sem": sem, "lower": lower}


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
        point["meets"] = (point["lower"] is not None and target is not None
                          and point["lower"] >= target)
        points.append(point)
        if progress:
            progress(evaluations, max_evaluations,
                     f"{sum(point['stocks'].values())} spares")
        if mode == "meet" and point["meets"]:
            recommended = point
            if index > 0:
                previous = candidates[index - 1]
                if not any(p["stocks"] == previous for p in points):
                    before = _measure(
                        workspace, model_id, previous, selected, trials=trials,
                        seed=seed, workers=workers, confidence=confidence,
                        timeout_s=timeout_s, abort=abort, metric=metric)
                    if before is not None and evaluations < max_evaluations:
                        evaluations += 1
                        before["meets"] = False
                        points.append(before)
            break
        if mode == "curve" and position >= 3:
            gains = _gains(points[-4:])
            if gains is not None and all(gain is None or gain <= 0
                                          for gain in gains):
                break
    return points, evaluations, recommended


def _gains(points: list[dict[str, Any]]) -> list[float | None] | None:
    """Paired-ish gains between consecutive points, or None if too short."""
    if len(points) < 2:
        return None
    return [(points[i]["mean"] or 0) - (points[i - 1]["mean"] or 0)
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
    row = results[0] if results else {}
    if row.get("error") or not row.get("run_id"):
        return []
    try:
        return read_per_run(run_dir(workspace, row["run_id"]),
                            row["run_id"]) or []
    except Exception:  # noqa: BLE001 - no rows is reported as no data
        return []


def _caption(recommended: dict[str, Any] | None, target: float | None,
             metric: str, costs_default: bool) -> str:
    if recommended is None:
        return "No point was evaluated."
    stocks = ", ".join(f"{name} {stock}"
                       for name, stock in recommended["stocks"].items())
    text = (f"Recommended: {stocks} — {metric} "
            f"{recommended['mean']:.5g} (lower bound {recommended['lower']:.5g})")
    if target is not None:
        text += f" against a target of {target:.5g}"
    if costs_default:
        text += "; all spare costs are 1, so this minimises the number held"
    return text + "."

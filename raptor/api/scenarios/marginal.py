"""Marginal value of one more spare or crew (WP-3.4).

For each finite pool or custom-stocked block, and each crew pool, run the
model with one more (and one fewer, where stock allows) beside Base with the
same seed.  The paired difference says what the next unit would buy: change
in availability, in downtime per run, and in cost where cost analysis is
on.  Pools the base run never waited on are skipped: adding stock to a shelf
nobody queued for cannot help.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from raptor.api.scenarios.sensitivity import _stat, _values


@dataclass(frozen=True)
class MarginalItem:
    name: str
    kind: str                      # pool | custom | resource
    plus: str                      # PATH=VALUE for one more
    minus: str | None = None       # PATH=VALUE for one fewer, when allowed
    stock: int = 0
    cost: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidates(rbd: Any) -> list[MarginalItem]:
    """Finite spare pools, custom-stocked blocks and crew pools."""
    return _spare_items(rbd) + _resource_items(rbd)


def _pool_rows(rbd: Any) -> dict[str, dict[str, Any]]:
    """The spare pools by name: their own stock and cost, not the block's."""
    from raptor.api.advisor.context import ModelContext
    try:
        return {str(pool.get("name") or ""): pool for pool in ModelContext(rbd).pools}
    except Exception:  # noqa: BLE001 - a model without pools has none
        return {}


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _spare_items(rbd: Any) -> list[MarginalItem]:
    items: list[MarginalItem] = []
    seen: set[str] = set()
    pools = _pool_rows(rbd)
    for row in rbd.section("BLOCK_DATA").rows:
        if str(row.get("ID", "0")) == "0":
            continue
        pool = str(row.get("SparePoolName") or "").strip()
        kind = str(row.get("SpType") or "")
        if not pool or pool.lower() in ("none", "unnamed"):
            continue
        stock = _int(row.get("Stock", 0))
        if kind == "Pooled" and pool not in seen:
            seen.add(pool)
            shelf = pools.get(pool)
            if shelf is None:
                continue
            stock = _int(shelf.get("stock"))
            if stock >= 10_000:            # an effectively infinite shelf
                continue
            items.append(MarginalItem(
                f"{pool} spare", "pool", f"pool:{pool}.stock={stock + 1}",
                f"pool:{pool}.stock={stock - 1}" if stock > 0 else None,
                stock, _pool_cost(shelf)))
        elif kind == "Custom":
            name = str(row.get("blockName") or row.get("ID"))
            items.append(MarginalItem(
                f"{name} spare", "custom",
                f"block:{row.get('ID')}.stock={stock + 1}",
                f"block:{row.get('ID')}.stock={stock - 1}" if stock > 0 else None,
                stock, _cost(row)))
    return items


def _resource_items(rbd: Any) -> list[MarginalItem]:
    items: list[MarginalItem] = []
    for row in rbd.section("RESOURCE_DATA").rows:
        name = str(row.get("PoolName") or row.get("resourceName")
                   or row.get("ResourceName") or "")
        if not name or name.lower() == "none":
            continue
        try:
            units = int(float(row.get("numIn") or row.get("NumIn") or 1))
        except (TypeError, ValueError):
            units = 1
        items.append(MarginalItem(
            f"{name} crew", "resource",
            f"resource:{name}.units={units + 1}", None, units, None))
    return items


def _pool_cost(pool: dict[str, Any]) -> float | None:
    try:
        value = float(pool.get("spare_cost") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 and value != 1.0 else None


def _cost(row: dict[str, Any]) -> float | None:
    for key in ("spareCost", "SpareCost", "perSpare", "per_spare_cost"):
        try:
            value = float(row.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0 and value != 1.0:      # the default 1 means "not set"
            return value
    return None


def marginal_value(
    workspace: Any,
    model_id: str,
    *,
    items: Sequence[MarginalItem] | None = None,
    trials: int | None = None,
    metric: str = "availability",
    seed: int | None = None,
    workers: int = 1,
    timeout_s: float = 1800.0,
    skip: set[str] | None = None,
    progress: Any = None,
    abort: Any = None,
) -> dict[str, Any]:
    """Run Base plus each item's +1 (and −1); rank by downtime avoided.

    `skip` holds item names already known not to matter (for example pools
    with no waiting in the base run); they are reported as skipped instead of
    costing two batches each.
    """
    from raptor.api.batch import run_variants

    wanted = list(items if items is not None else candidates(
        workspace.get(model_id).rbd))
    skipped = [item for item in wanted if skip and item.name in skip]
    wanted = [item for item in wanted if not skip or item.name not in skip]
    variants = [{"label": "Base", "assignments": []}]
    for item in wanted:
        variants.append({"label": f"{item.name} +1",
                         "assignments": [item.plus]})
        if item.minus:
            variants.append({"label": f"{item.name} -1",
                             "assignments": [item.minus]})
    results = run_variants(
        workspace, model_id, variants, num_runs=trials, workers=workers,
        timeout_s=timeout_s, seed=seed, progress=progress, abort=abort)
    base = _rows(workspace, results[0]) if results else []
    base_metric = _stat(_values(base, metric))
    base_down = _stat(_values(base, "downtime"))
    base_cost = _stat(_values(base, "grand_total"))
    rows: list[dict[str, Any]] = []
    index = 1
    for item in wanted:
        plus_row = results[index] if index < len(results) else {}
        plus_data = _rows(workspace, plus_row)
        plus = _effect(base, plus_data, metric)
        plus_down = _effect(base, plus_data, "downtime")
        plus_cost = _effect(base, plus_data, "grand_total")
        index += 1
        minus_row = {}
        minus: tuple[float | None, float | None] = (None, None)
        minus_down: tuple[float | None, float | None] = (None, None)
        minus_cost: tuple[float | None, float | None] = (None, None)
        if item.minus:
            minus_row = results[index] if index < len(results) else {}
            rows_minus = _rows(workspace, minus_row)
            minus = _effect(base, rows_minus, metric)
            minus_down = _effect(base, rows_minus, "downtime")
            minus_cost = _effect(base, rows_minus, "grand_total")
            index += 1
        avoided = -plus_down[0] if plus_down[0] is not None else None
        per_cost = (avoided / item.cost
                    if avoided is not None and item.cost else None)
        rows.append({
            "item": item.to_dict(),
            "plus": {"effect": plus[0], "sem": plus[1],
                     "downtime": plus_down[0], "cost": plus_cost[0]},
            "minus": {"effect": minus[0], "sem": minus[1],
                      "downtime": minus_down[0], "cost": minus_cost[0]},
            "downtime_avoided": avoided,
            "avoided_per_cost": per_cost,
            "error": plus_row.get("error") or minus_row.get("error"),
        })
    rows.sort(key=lambda row: -(row["downtime_avoided"] or 0.0))
    from raptor.api.batch import discard_runs
    discard_runs(workspace, results)
    return {
        "metric": metric,
        "trials": int(trials or len(_values(base, metric))),
        "seed": seed,
        "base": {"metric": base_metric[0], "downtime": base_down[0],
                 "cost": base_cost[0],
                 "runs": len(_values(base, metric))},
        "items": rows,
        "skipped": [item.to_dict() for item in skipped],
        "batches": len(results),
        "caption": _caption(rows),
        "note": ("Marginal value of one unit, paired against Base. A pool "
                 "the base run never waited on is skipped."),
    }


def _rows(workspace: Any, row: dict[str, Any]) -> list[dict]:
    run_id = row.get("run_id")
    if not run_id:
        return []
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run
    try:
        return read_per_run(run_dir(workspace, run_id), run_id) or []
    except Exception:  # noqa: BLE001 - the row already carries any error
        return []


def _effect(base: list[dict], other: list[dict], key: str
            ) -> tuple[float | None, float | None]:
    base_values = _values(base, key)
    other_values = _values(other, key)
    n = min(len(base_values), len(other_values))
    if n < 2:
        return None, None
    return _stat([other_values[i] - base_values[i] for i in range(n)])


def _caption(rows: list[dict[str, Any]]) -> str:
    usable = [row for row in rows
              if row.get("downtime_avoided") is not None]
    if not usable:
        return "No spare or crew could be varied."
    best = usable[0]
    return (f"One more {best['item']['name']} would avoid "
            f"{best['downtime_avoided']:.4g} time units of downtime a run.")

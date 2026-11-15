"""One-at-a-time sensitivity: which inputs move the answer, and by how much.

Each candidate is run at a low and a high value with the same seed as Base,
so every effect is a paired difference.  The swing is |high effect − low
effect|; an elasticity is reported where the input is continuous.  This is a
screening view: it shows each input alone and cannot show interactions (the
global methods in the long-range plan do that).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from raptor.api.distributions import scale_mean, scales_with_mean

#: How many block-derived inputs to offer before model-wide ones.
DEFAULT_LIMIT = 12


@dataclass(frozen=True)
class Input:
    name: str
    kind: str                       # failure | repair | pm | stock | resource
    low: str                        # PATH=VALUE at the low value
    high: str                       # PATH=VALUE at the high value
    base_value: float | None = None
    low_value: float | None = None
    high_value: float | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _params(row: dict[str, Any], prefix: str) -> list[float]:
    count = int(float(row.get(f"#{prefix}Param", 2) or 2))
    return [float(row.get(f"{prefix}Param{i + 1}", 0.0) or 0.0)
            for i in range(min(3, max(1, count)))]


def _fmt(params: list[float]) -> str:
    return ",".join(f"{value:.10g}" for value in params)


def _selector(row: dict[str, Any]) -> str:
    name = str(row.get("blockName") or "").strip()
    if not name or name.lower() == "unnamed":
        return str(row.get("ID"))
    return name


def _at_factor(row: dict[str, Any], prefix: str, field_name: str,
               factor: float) -> str | None:
    code = int(float(row.get(f"{prefix}Dist", 18) or 18))
    params = _params(row, prefix)
    if not params or not scales_with_mean(code):
        return None
    try:
        scaled = scale_mean(code, params, factor)
    except ValueError:
        return None
    return f"block:{_selector(row)}.{field_name}={_fmt(scaled)}"


def default_inputs(rbd: Any, attribution: dict[str, Any] | None = None,
                   limit: int = DEFAULT_LIMIT) -> list[Input]:
    """The inputs worth screening, ranked by this run's downtime share."""
    blocks = [row for row in rbd.section("BLOCK_DATA").rows
              if str(row.get("ID", "0")) != "0"]
    if attribution:
        shares = {str(element.get("name")): (
            (element.get("dtci") or {}).get("mean") or 0.0)
            for element in attribution.get("elements") or []}
        blocks.sort(key=lambda row: -shares.get(
            str(row.get("blockName") or ""), 0.0))
    inputs: list[Input] = []
    for row in blocks:
        inputs.extend(_block_inputs(row, limit - len(inputs)))
        if len(inputs) >= limit:
            break
    if len(inputs) < limit:
        inputs.extend(_pool_inputs(rbd, blocks, limit - len(inputs)))
    if len(inputs) < limit:
        inputs.extend(_resource_inputs(rbd, limit - len(inputs)))
    return inputs[:limit]


def _block_inputs(row: dict[str, Any], budget: int) -> list[Input]:
    name = str(row.get("blockName") or row.get("ID"))
    out: list[Input] = []
    life = _at_factor(row, "F", "fail_params", 0.5)
    if life is not None:
        out.append(Input(
            f"{name} life", "failure", life,
            _at_factor(row, "F", "fail_params", 2.0) or life,
            note="mean x0.5 and x2, shape unchanged"))
    repair = _at_factor(row, "R", "repair_params", 0.5)
    if repair is not None:
        out.append(Input(
            f"{name} repair", "repair", repair,
            _at_factor(row, "R", "repair_params", 2.0) or repair,
            note="mean x0.5 and x2, shape unchanged"))
    out.extend(_pm_inputs(row))
    out.extend(_stock_inputs(row))
    return out[:max(0, budget)]


def _pm_inputs(row: dict[str, Any]) -> list[Input]:
    if str(row.get("usesPM", "0")) in ("0", "0.0", ""):
        return []
    try:
        interval = float(row.get("PMFreq", 0) or 0)
    except (TypeError, ValueError):
        return []
    if interval <= 0:
        return []
    name = str(row.get("blockName") or row.get("ID"))
    return [Input(
        f"{name} PM interval", "pm",
        f"block:{_selector(row)}.pm_freq={0.5 * interval:.10g}",
        f"block:{_selector(row)}.pm_freq={2.0 * interval:.10g}",
        base_value=interval, low_value=0.5 * interval,
        high_value=2.0 * interval)]


def _stock_inputs(row: dict[str, Any]) -> list[Input]:
    """A block's own (Custom) spare stock. A pooled spare is varied once, on
    the pool, by `_pool_inputs`: the block row's Stock column is not the
    pool's stock (it read 100 for an empty pool, so the pool was varied at
    99 and 101 and showed no effect)."""
    if str(row.get("SpType") or "") != "Custom":
        return []
    try:
        stock = int(float(row.get("Stock", 0) or 0))
    except (TypeError, ValueError):
        stock = 0
    target = _selector(row)
    return [Input(
        f"{target} stock", "stock",
        f"block:{target}.stock={max(0, stock - 1)}",
        f"block:{target}.stock={stock + 1}",
        base_value=float(stock), low_value=float(max(0, stock - 1)),
        high_value=float(stock + 1))]


def _pool_inputs(rbd: Any, blocks: list[dict[str, Any]], budget: int) -> list[Input]:
    """Each spare pool some block draws from, varied by one unit either way,
    in the order the blocks come (so the largest downtime causes first).
    An effectively infinite pool (10,000 or more) is left out."""
    stocks = {}
    for pool in rbd.section("SPARE_POOL_DATA").rows:
        try:
            stocks[str(pool.get("PoolName") or "")] = int(float(pool.get("Stock", 0) or 0))
        except (TypeError, ValueError):
            continue
    out: list[Input] = []
    seen: set[str] = set()
    for row in blocks:
        name = str(row.get("SparePoolName") or "").strip()
        if (str(row.get("SpType") or "") != "Pooled" or name in seen
                or name not in stocks or stocks[name] >= 10_000):
            continue
        seen.add(name)
        stock = stocks[name]
        out.append(Input(
            f"{name} stock", "stock",
            f"pool:{name}.stock={max(0, stock - 1)}", f"pool:{name}.stock={stock + 1}",
            base_value=float(stock), low_value=float(max(0, stock - 1)),
            high_value=float(stock + 1)))
    return out[:max(0, budget)]


def _resource_inputs(rbd: Any, budget: int) -> list[Input]:
    out: list[Input] = []
    for row in rbd.section("RESOURCE_DATA").rows:
        if len(out) >= budget:
            break
        name = str(row.get("resourceName") or row.get("ResourceName") or "")
        if not name or name.lower() == "none":
            continue
        try:
            units = int(float(row.get("numIn") or row.get("NumIn") or 1))
        except (TypeError, ValueError):
            units = 1
        out.append(Input(
            f"{name} crews", "resource",
            f"resource:{name}.units={max(1, units - 1)}",
            f"resource:{name}.units={units + 1}",
            base_value=float(units), low_value=float(max(1, units - 1)),
            high_value=float(units + 1)))
    return out


def _values(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows
            if isinstance(row.get(key), (int, float))
            and not isinstance(row.get(key), bool)]


def _stat(values: list[float]) -> tuple[float | None, float | None]:
    n = len(values)
    if n == 0:
        return None, None
    mean = sum(values) / n
    if n < 2:
        return mean, None
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    return mean, math.sqrt(variance / n)


def tornado(
    workspace: Any,
    model_id: str,
    inputs: Sequence[Input],
    *,
    trials: int | None = None,
    metric: str = "availability",
    seed: int | None = 1,
    workers: int = 1,
    timeout_s: float = 1800.0,
    progress: Callable[[int, int, str], None] | None = None,
    abort: Any = None,
) -> dict[str, Any]:
    """Run Base and the low/high of each input; return the ranked swings.

    `metric` is a per-run field (`availability`, `mtbde`, `mdt`,
    `capacity_weighted_availability`, `lost_flow`, ...).  Effects are means
    of paired trial differences against Base, with their SEM.
    """
    from raptor.api.batch import run_variants

    variants = [{"label": "Base", "assignments": []}]
    for item in inputs:
        variants.append({"label": f"{item.name} low",
                         "assignments": [item.low]})
        variants.append({"label": f"{item.name} high",
                         "assignments": [item.high]})
    results = run_variants(
        workspace, model_id, variants, num_runs=trials, workers=workers,
        timeout_s=timeout_s, seed=seed, progress=progress, abort=abort)
    base = _rows(workspace, results[0]) if results else []
    base_values = _values(base, metric)
    if base and not base_values:
        from raptor.api.batch import discard_runs
        discard_runs(workspace, results)
        known = sorted(key for key, value in base[0].items()
                       if isinstance(value, (int, float)) and not isinstance(value, bool))
        raise ValueError(f"metric {metric!r} is not a per-run result; one of: "
                         + ", ".join(known))
    base_mean, base_sem = _stat(base_values)
    rows: list[dict[str, Any]] = []
    index = 1
    for item in inputs:
        low_row = results[index] if index < len(results) else {}
        high_row = results[index + 1] if index + 1 < len(results) else {}
        index += 2
        low = _effect(base_values, _values(_rows(workspace, low_row), metric))
        high = _effect(base_values, _values(_rows(workspace, high_row), metric))
        swing = (abs(high[0] - low[0])
                 if high[0] is not None and low[0] is not None else None)
        spread = math.sqrt((low[1] or 0.0) ** 2 + (high[1] or 0.0) ** 2)
        distinguishable = (swing is not None and swing > 2 * spread)
        rows.append({
            "input": item.to_dict(),
            "low": {"effect": low[0], "sem": low[1]},
            "high": {"effect": high[0], "sem": high[1]},
            "swing": swing,
            "elasticity": _elasticity(item, low[0], high[0], base_mean),
            "distinguishable": distinguishable,
            "error": low_row.get("error") or high_row.get("error"),
        })
    rows.sort(key=lambda row: -(row["swing"] or 0.0))
    largest = rows[0]["swing"] if rows and rows[0]["swing"] else 0.0
    for row in rows:
        row["small"] = bool(largest and (row["swing"] or 0.0) < 0.1 * largest)
    from raptor.api.batch import discard_runs
    discard_runs(workspace, results)
    return {
        "metric": metric,
        "trials": int(trials or len(base_values)),
        "seed": seed,
        "base": {"mean": base_mean, "sem": base_sem,
                 "runs": len(base_values)},
        "inputs": rows,
        "batches": len(results),
        "caption": _caption(rows, metric, base_mean),
        "note": ("One-at-a-time: each input is shown alone. It cannot show "
                 "interactions between inputs."),
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


def _effect(base: list[float], other: list[float]
            ) -> tuple[float | None, float | None]:
    n = min(len(base), len(other))
    if n < 2:
        return None, None
    deltas = [other[i] - base[i] for i in range(n)]
    return _stat(deltas)


def _elasticity(item: Input, low: float | None, high: float | None,
                base_mean: float | None) -> float | None:
    if (low is None or high is None or base_mean in (None, 0)
            or item.base_value in (None, 0)
            or item.low_value is None or item.high_value is None):
        return None
    dx = (item.high_value - item.low_value) / item.base_value
    if dx == 0:
        return None
    dy = (high - low) / base_mean
    return dy / dx


def _caption(rows: list[dict[str, Any]], metric: str,
             base_mean: float | None) -> str:
    usable = [row for row in rows if row.get("swing") is not None]
    if not usable:
        return "No input could be varied."
    top = usable[0]
    share = metric in _SHARE_METRICS
    # In the chart's own units: a share metric is drawn in percentage
    # points, and the caption printed the bare fraction beside it.
    swing = f"{100 * top['swing']:.3g} pts" if share else f"{top['swing']:.4g}"
    base = ("" if base_mean is None else
            f" from a base of {100 * base_mean:.4g} %" if share else
            f" from a base of {base_mean:.4g}")
    text = f"{top['input']['name']} has the largest swing on {metric} ({swing}{base})."
    if not any(row.get("distinguishable") for row in usable):
        text += (" No swing is clear of the run-to-run noise at this many "
                 "trials: run more before acting on the order.")
    return text


#: Metrics that are shares, shown in percent and their swings in points.
_SHARE_METRICS = ("availability", "reliability", "capacity_weighted_availability")


def sensitivity_lines(result: dict[str, Any]) -> list[str]:
    """A tornado result as text: base, then each input's swing and its low
    and high effects, widest first. `raptor sensitivity` prints it; the GUI
    copies it."""
    lines: list[str] = []
    base = result["base"]
    shown = (f"{100 * base['mean']:.5g} %" if result["metric"] in _SHARE_METRICS
             else f"{base['mean']:.6g}")
    lines.append(f"Sensitivity of {result['metric']} (base {shown}), "
          f"{result['batches']} batches x {result['trials']} paired runs")
    for row in result["inputs"]:
        if row["swing"] is None:
            lines.append(f"  {row['input']['name']:<28} no data")
            continue
        marks = "" if row["distinguishable"] else "  (not distinguishable)"
        small = "  small" if row.get("small") else ""
        lines.append(f"  {row['input']['name']:<28} swing {row['swing']:.4g}"
              f"  low {row['low']['effect']:+.4g}  high "
              f"{row['high']['effect']:+.4g}{marks}{small}")
    lines.append(f"\n{result['caption']}")
    lines.append(result["note"])
    return lines

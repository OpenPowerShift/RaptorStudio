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


def _mean_of(row: dict[str, Any], prefix: str) -> float | None:
    """The mean of a block's failure (F) or repair (R) law, or None."""
    from raptor.api.distributions import describe_dist
    try:
        mean = describe_dist(int(float(row.get(f"{prefix}Dist", 18) or 18)),
                             _params(row, prefix)).get("mean")
    except (TypeError, ValueError):
        return None
    return float(mean) if isinstance(mean, (int, float)) and mean > 0 else None


def _scaled(mean: float | None) -> tuple[float | None, float | None, float | None]:
    """(base, low, high) of a mean scaled by 0.5 and 2: what an elasticity
    needs. Without them a life or repair input, the commonest kind, had no
    elasticity at all."""
    return (None, None, None) if mean is None else (mean, mean * 0.5, mean * 2.0)


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
    else:
        # no run to rank by: file order screened Utility_A, Generator, UPS
        # and never the CRAC units the downtime came from
        score = _own_downtime(rbd)
        blocks.sort(key=lambda row: -score.get(str(row.get("ID")), 0.0))
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


def _own_downtime(rbd: Any) -> dict[str, float]:
    """Block ID -> a guess at its share of system downtime from its own
    data: its own unavailability (repair / (life + repair), or the chance
    it fails in the run when it is not repaired), squared for a block the
    diagram backs up (another one must be down too)."""
    from raptor.api.advisor.context import ModelContext
    try:
        ctx = ModelContext(rbd)
        length = float(ctx.run_length or 0.0)
    except Exception:  # noqa: BLE001 - a guess: fall back to file order
        return {}
    out: dict[str, float] = {}
    for row in rbd.section("BLOCK_DATA").rows:
        bid = str(row.get("ID", "0"))
        if bid == "0":
            continue
        life, repair = _mean_of(row, "F"), _mean_of(row, "R")
        if not life:
            continue
        own = (repair / (life + repair) if repair
               else min(1.0, length / life) if length else 0.5)
        try:
            backed = ctx.redundant(("block", int(float(bid))))
        except (TypeError, ValueError):
            backed = False
        out[bid] = own * own if backed else own
    return out


def screening(rbd: Any, inputs: Sequence[Input],
              attribution: dict[str, Any] | None = None) -> str:
    """What the screen tried and how it chose: "the largest swing" is only
    the largest among the inputs screened."""
    available = len(default_inputs(rbd, attribution, limit=10_000))
    if len(inputs) >= available:
        return f"All {available} inputs that can be varied were screened."
    how = ("by this run's downtime share" if attribution else
           "by each block's own downtime from its failure and repair data, "
           "blocks the diagram does not back up first")
    return (f"Screened {len(inputs)} of the {available} inputs that can be varied, "
            f"chosen {how}; ask for more to screen the rest.")


def _block_inputs(row: dict[str, Any], budget: int) -> list[Input]:
    name = str(row.get("blockName") or row.get("ID"))
    out: list[Input] = []
    life = _at_factor(row, "F", "fail_params", 0.5)
    if life is not None:
        mean = _mean_of(row, "F")
        out.append(Input(
            f"{name} life", "failure", life,
            _at_factor(row, "F", "fail_params", 2.0) or life,
            *_scaled(mean), note="mean x0.5 and x2, shape unchanged"))
    repair = _at_factor(row, "R", "repair_params", 0.5)
    if repair is not None:
        mean = _mean_of(row, "R")
        out.append(Input(
            f"{name} repair", "repair", repair,
            _at_factor(row, "R", "repair_params", 2.0) or repair,
            *_scaled(mean), note="mean x0.5 and x2, shape unchanged"))
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
    seed: int | None = None,
    workers: int = 1,
    timeout_s: float = 1800.0,
    progress: Callable[[int, int, str], None] | None = None,
    abort: Any = None,
    asked: int | None = None,
    screened: str = "",
) -> dict[str, Any]:
    """Run Base and the low/high of each input; return the ranked swings.

    `asked` is how many inputs the caller asked `default_inputs` for: when
    the model has fewer, the note says so (asking for 11 and seeing 10 read
    as the setting being ignored).

    `metric` is a per-run field (`availability`, `mtbde`, `mdt`,
    `capacity_weighted_availability`, `lost_flow`, ...).  Effects are means
    of paired trial differences against Base, with their SEM.
    """
    from raptor.api.batch import cut_short, require_measured, run_variants

    variants = [{"label": "Base", "assignments": []}]
    for item in inputs:
        variants.append({"label": f"{item.name} low",
                         "assignments": [item.low]})
        variants.append({"label": f"{item.name} high",
                         "assignments": [item.high]})
    results = run_variants(
        workspace, model_id, variants, num_runs=trials, workers=workers,
        timeout_s=timeout_s, seed=seed, progress=progress, abort=abort)
    short = cut_short(results, timeout_s)
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
    require_measured(short, any(row["swing"] is not None for row in rows))
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
        "caption": ((short + " " if short else "")
                    + _caption(rows, metric, base_mean)),
        "cut_short": short,
        "screened": screened,
        "note": ("One-at-a-time: each input is shown alone. It cannot show "
                 "interactions between inputs."
                 + (f" This model has only {len(inputs)} inputs that can be "
                    f"varied, not {asked}." if asked and len(inputs) < asked
                    else f" {screened}" if screened else "")),
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
    if not any(row.get("distinguishable") for row in usable):
        return (f"No swing on {metric} is clear of the run-to-run noise at this "
                f"many trials (the widest, {top['input']['name']}, is {swing}"
                f"{base}): run more before acting on the order.")
    return (f"Of the inputs screened, {top['input']['name']} has the largest "
            f"swing on {metric} ({swing}{base}).")


#: Metrics that are shares, shown in percent and their swings in points.
_SHARE_METRICS = ("availability", "reliability", "capacity_weighted_availability")


def sensitivity_lines(result: dict[str, Any]) -> list[str]:
    """A tornado result as text: base, then each input's swing and its low
    and high effects, widest first. `raptor sensitivity` prints it; the GUI
    copies it."""
    lines: list[str] = []
    base = result["base"]
    share = result["metric"] in _SHARE_METRICS
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
        item = row["input"]
        values = ""
        if item.get("low_value") is not None and item.get("high_value") is not None:
            values = (f"  [{_num(item['low_value'])} .. {_num(item['high_value'])}"
                      f" from {_num(item.get('base_value'))}]")
        lines.append(f"  {item['name']:<28} swing {_eff(row['swing'], share, False)}"
              f"  low {_eff(row['low']['effect'], share)}  high "
              f"{_eff(row['high']['effect'], share)}{values}{marks}{small}")
    if any(row["swing"] is not None and not row["distinguishable"]
           for row in result["inputs"]):
        lines.append("  (not distinguishable: the swing is within two standard "
                     "errors of no effect at this many runs)")
    lines.append(f"\n{result['caption']}")
    lines.append(result["note"])
    return lines


def _num(value: Any) -> str:
    return "?" if value is None else f"{float(value):,.4g}"


def _eff(value: float | None, share: bool, signed: bool = True) -> str:
    """An effect in the caption's own units: points for a share metric
    (the rows printed 0.01254 under a sentence saying 1.25 pts)."""
    if value is None:
        return "?"
    if share:
        return f"{100 * value:{'+' if signed else ''}.3g} pts"
    return f"{value:{'+' if signed else ''}.4g}"

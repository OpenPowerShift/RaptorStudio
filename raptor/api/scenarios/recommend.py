"""Tested recommendations: a change, what it buys, and what it costs.

A finding such as "waiting for spares causes 40 % of downtime" is an
observation.  A decision needs three more things: the concrete change
(``pool:Pumps.stock=3``), the gain that change measurably gives, and its
cost.  This module turns the run's top downtime drivers into candidate
changes, one lever per driver chosen from the cause that dominates its
outages, and tests each one against Base with paired trials
(`raptor.api.scenarios.runner.compare`, same seed, trial *i* against
trial *i*).

Each candidate is a guess about which lever matters; the simulation is what
decides.  The verdicts say what was MEASURED, never whether a change is worth
its price (most changes have no price in the model):

* *clearly helps*: the paired gain is real at the confidence below AND
  material: it removes at least `MATERIAL` (5 %) of Base's downtime, or the
  caller's `min_gain`;
* *helps a little*: real, but smaller than that;
* *may help; run more trials*: probably positive, not yet sure;
* *no measurable gain* and *makes it worse*.

"Real" means a chance of being better of at least 1 − 0.05/k for k changes
tested together (Bonferroni), so testing more changes at once does not let
one through by luck.  The size of each
change is a fixed, stated step (repair or delay time halved, one more
spare, one more crew, maintenance half as often, a part that lasts twice as
long), not an optimum: the optimisers (`raptor optimise ...`) size a lever
once this has shown it is the one to pull.

Cost: one more spare or crew carries the unit cost stored in the model,
when there is one.  With cost analysis on, the paired change in the run's
grand total is reported too (it can be negative: fewer outages can cost
less than the spare).  A change to repair times, delays or part life has no
price in the model, so its cost is reported as unknown rather than zero.
"""
from __future__ import annotations
from raptor.api.insight.axes import pct

import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from raptor.api.distributions import scale_mean, scales_with_mean
from raptor.api.insight.attribution import CAUSE_LABELS

#: The chance, across ALL the changes tested together, of calling one
#: better by luck; each change needs P(better) >= 1 - FAMILY_ERROR / k.
FAMILY_ERROR = 0.05
#: A gain is material when it removes at least this share of Base's downtime.
MATERIAL = 0.05
#: From this chance of being better (below the bar), "may help; run more trials".
UNLIKELY = 0.60

CLEARLY = "clearly helps"
A_LITTLE = "helps a little"
MAYBE = "may help; run more trials"
NO_GAIN = "no measurable gain"
WORSE = "makes it worse"
VERDICTS = (CLEARLY, A_LITTLE, MAYBE, NO_GAIN, WORSE)
#: How many top downtime drivers get a candidate lever by default.
DEFAULT_TOP = 3
#: A second cause is tried too when it holds this share of an element's outages.
SECOND_CAUSE = 0.25

SCHEMA = "raptor-recommend/1"


@dataclass(frozen=True)
class Proposal:
    """One candidate change, before it is tested."""
    name: str                     # "Pump A: halve the repair time"
    element: str                  # the driver it acts on
    lever: str                    # repair | logistics | spare | crew | pm | life
    cause: str | None             # the attribution cause that suggested it
    changes: tuple[str, ...]      # PATH=VALUE, the grammar of `raptor set`
    why: str = ""
    unit_cost: float | None = None
    cost_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["changes"] = list(self.changes)
        return d


@dataclass
class Recommendations:
    trials: int
    seed: int | None
    base: dict[str, Any]
    rows: list[dict[str, Any]]
    untested: list[dict[str, Any]] = field(default_factory=list)
    caption: str = ""
    note: str = ""
    attribution_run_id: str | None = None
    #: The bars the verdicts used: {confidence, min_gain, tested}.
    bars: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "schema": SCHEMA}


# ---- candidate changes --------------------------------------------------------

def _selector(row: dict[str, Any]) -> str:
    name = str(row.get("blockName") or "").strip()
    if not name or name.lower() == "unnamed":
        return str(row.get("ID"))
    return name


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _scaled(row: dict[str, Any], dist_key: str, param_keys: Sequence[str],
            field_name: str, factor: float) -> str | None:
    """`block:NAME.FIELD=a,b,c` with the law's mean times `factor`."""
    code = int(_float(row.get(dist_key), 18))
    params = [_float(row.get(key)) for key in param_keys]
    if not scales_with_mean(code) or not any(params):
        return None
    try:
        scaled = scale_mean(code, params, factor)
    except ValueError:
        return None
    text = ",".join(f"{value:.10g}" for value in scaled)
    return f"block:{_selector(row)}.{field_name}={text}"


def _repair(row: dict[str, Any]) -> str | None:
    count = int(_float(row.get("#RParam"), 2)) or 2
    return _scaled(row, "RDist", [f"RParam{i + 1}" for i in range(min(3, count))],
                   "repair_params", 0.5)


def _life(row: dict[str, Any]) -> str | None:
    count = int(_float(row.get("#FParam"), 2)) or 2
    return _scaled(row, "FDist", [f"FParam{i + 1}" for i in range(min(3, count))],
                   "fail_params", 2.0)


def _logistics(row: dict[str, Any]) -> tuple[str, ...]:
    out = []
    for dist, slot, field_name in (("Pre-LDTDist", 1, "pre_ldt_params"),
                                   ("Post-LDTDist", 2, "post_ldt_params")):
        change = _scaled(row, dist, [f"Param{i}#{slot}" for i in (1, 2, 3)],
                         field_name, 0.5)
        if change:
            out.append(change)
    return tuple(out)


def _unit_cost(value: Any) -> float | None:
    number = _float(value)
    # 1.00 is the format's "not set" default for every cost column
    return number if number > 0 and abs(number - 1.0) > 1e-12 else None


def _spare(rbd: Any, row: dict[str, Any]) -> tuple[str, float | None] | None:
    kind = str(row.get("SpType") or "")
    stock = int(_float(row.get("Stock")))
    if kind == "Custom":
        return (f"block:{_selector(row)}.stock={stock + 1}",
                _unit_cost(row.get("perSpare")))
    pool = str(row.get("SparePoolName") or "").strip()
    if kind != "Pooled" or not pool or pool.lower() in ("none", "unnamed"):
        return None
    for pool_row in rbd.section("SPARE_POOL_DATA").rows:
        if str(pool_row.get("PoolName") or "") == pool:
            stock = int(_float(pool_row.get("Stock")))
            if stock >= 10_000:        # effectively infinite: nothing to add
                return None
            return (f"pool:{pool}.stock={stock + 1}",
                    _unit_cost(pool_row.get("SpCost")))
    return None


def _crew(rbd: Any, row: dict[str, Any]) -> tuple[str, str, float | None] | None:
    name = str(row.get("Resource1Name") or "").strip()
    if not name or name.lower() == "none" or int(_float(row.get("NumRes"))) <= 0:
        return None
    for res in rbd.section("RESOURCE_DATA").rows:
        if str(res.get("PoolName") or "") == name:
            units = int(_float(res.get("NumIn"), 1))
            return (name, f"resource:{name}.units={units + 1}",
                    _unit_cost(res.get("SpareCost")))
    return None


def _pm(row: dict[str, Any]) -> str | None:
    if str(row.get("usesPM", "0")) in ("0", "0.0", "", "False", "false"):
        return None
    interval = _float(row.get("PMFreq"))
    if interval <= 0:
        return None
    return f"block:{_selector(row)}.pm_freq={2.0 * interval:.10g}"


def presets(rbd: Any, block: str) -> list[tuple[str, tuple[str, ...]]]:
    """The ready-made changes for one block, as (label, PATH=VALUE changes):
    the levers Recommend proposes, offered for any block the user picks.
    Only those that apply to this block's data are listed."""
    row = next((r for r in rbd.section("BLOCK_DATA").rows
                if str(r.get("ID", "0")) != "0"
                and (str(r.get("blockName") or "") == block or str(r.get("ID")) == block)),
               None)
    if row is None:
        raise ValueError(f"no block named {block!r}")
    out: list[tuple[str, tuple[str, ...]]] = []
    repair, life, delays = _repair(row), _life(row), _logistics(row)
    if repair:
        out.append(("half the repair time", (repair,)))
    if life:
        out.append(("lasts twice as long", (life,)))
    if delays:
        out.append(("half the delays", delays))
    spare = _spare(rbd, row)
    if spare:
        out.append(("one more spare", (spare[0],)))
    crew = _crew(rbd, row)
    if crew:
        out.append((f"one more {crew[0]}", (crew[1],)))
    pm = _pm(row)
    if pm:
        out.append(("maintenance half as often", (pm,)))
        out.append(("no planned maintenance", (f"block:{_selector(row)}.uses_pm=false",)))
    return out


def _for_cause(rbd: Any, row: dict[str, Any], name: str, cause: str,
               share: float) -> Proposal | None:
    part = f"{100 * share:.0f} % of its outage time is {CAUSE_LABELS.get(cause, cause).lower()}"
    if cause == "repair":
        change = _repair(row)
        return None if change is None else Proposal(
            f"{name}: halve the repair time", name, "repair", cause, (change,),
            why=f"{part}; the repair law's mean is halved, its shape kept.",
            cost_note="the cost of faster repairs is not in the model")
    if cause == "logistics":
        changes = _logistics(row)
        return Proposal(
            f"{name}: halve the delays before and after repair", name,
            "logistics", cause, changes,
            why=f"{part}; the delay laws' means are halved.",
            cost_note="the cost of shorter delays is not in the model"
        ) if changes else None
    if cause in ("wait_spare", "done"):
        spare = _spare(rbd, row)
        if spare is None:
            return None
        change, cost = spare
        return Proposal(
            f"{name}: one more spare", name, "spare", cause, (change,),
            why=f"{part}; one more unit on the shelf.", unit_cost=cost,
            cost_note="" if cost is not None else "no spare cost in the model")
    if cause in ("wait_resource", "pm_wait"):
        crew = _crew(rbd, row)
        if crew is None:
            return None
        crew_name, change, cost = crew
        return Proposal(
            f"{name}: one more {crew_name}", name, "crew", cause, (change,),
            why=f"{part}; one more unit of {crew_name}.", unit_cost=cost,
            cost_note="" if cost is not None else "no crew cost in the model")
    if cause == "pm":
        change = _pm(row)
        return None if change is None else Proposal(
            f"{name}: maintenance half as often", name, "pm", cause, (change,),
            why=f"{part}; the PM interval is doubled. Check the failures it "
                "was preventing in the result.",
            cost_note="a longer interval usually costs less; see the cost change")
    return None


def proposals(rbd: Any, attribution: dict[str, Any], *,
              top: int = DEFAULT_TOP) -> list[Proposal]:
    """Candidate changes for the `top` blocks that cause the most downtime.

    Each block gets the lever for its main cause, and for a second cause
    holding at least `SECOND_CAUSE` of its outage time.  The largest driver
    also gets "a part that lasts twice as long", the value of a more
    reliable part.  Identical changes (two blocks sharing one pool) are
    proposed once."""
    blocks = {str(row.get("ID")): row for row in rbd.section("BLOCK_DATA").rows
              if str(row.get("ID", "0")) != "0"}
    drivers = [element for element in attribution.get("elements") or []
               if element.get("kind") == "block"
               and ((element.get("dtci") or {}).get("mean") or 0) > 0
               and str(element.get("id")) in blocks]
    drivers.sort(key=lambda e: -((e.get("dtci") or {}).get("mean") or 0.0))
    out: list[Proposal] = []
    seen: set[tuple[str, ...]] = set()

    def add(proposal: Proposal | None) -> None:
        if proposal is not None and proposal.changes and proposal.changes not in seen:
            seen.add(proposal.changes)
            out.append(proposal)

    for rank, element in enumerate(drivers[:max(0, top)]):
        row = blocks[str(element["id"])]
        name = str(element.get("name") or _selector(row))
        causes = sorted((element.get("causes") or {}).items(),
                        key=lambda item: -item[1])
        for index, (cause, share) in enumerate(causes[:2]):
            if index and share < SECOND_CAUSE:
                break
            add(_for_cause(rbd, row, name, cause, share))
        if rank == 0:
            life = _life(row)
            if life:
                add(Proposal(
                    f"{name}: a part that lasts twice as long", name, "life",
                    None, (life,),
                    why="The largest driver; the failure law's mean is doubled, "
                        "its shape kept. This prices a more reliable part or a "
                        "redundant one.",
                    cost_note="the price of a better part is not in the model"))
    return out


# ---- testing them -------------------------------------------------------------

def _per_run(workspace: Any, run_id: str | None) -> list[dict]:
    if not run_id:
        return []
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run
    try:
        return read_per_run(run_dir(workspace, run_id), run_id) or []
    except Exception:  # noqa: BLE001 - the comparison row carries any error
        return []


def _paired(base: list[dict], other: list[dict], key: str
            ) -> tuple[float | None, float | None]:
    a = [row.get(key) for row in base]
    b = [row.get(key) for row in other]
    deltas = [float(y) - float(x) for x, y in zip(a, b, strict=False)
              if isinstance(x, (int, float)) and isinstance(y, (int, float))]
    n = len(deltas)
    if n < 2:
        return None, None
    mean = sum(deltas) / n
    sem = math.sqrt(sum((d - mean) ** 2 for d in deltas) / (n - 1) / n)
    return mean, sem


def _verdict(gain: float | None, p_better: float | None, *,
             confidence: float, min_gain: float) -> str:
    if gain is None or p_better is None:
        return "no data"
    if p_better >= confidence and gain > 0:
        return CLEARLY if gain >= min_gain else A_LITTLE
    if p_better <= 1 - confidence and gain < 0:
        return WORSE
    if p_better < UNLIKELY or gain <= 0:
        return NO_GAIN
    return MAYBE


_ORDER = {CLEARLY: 0, A_LITTLE: 1, MAYBE: 2, NO_GAIN: 3, WORSE: 4, "no data": 5}


def _weigh(gain: float | None, proposal: Proposal) -> str:
    """What the user still has to weigh: the gain was measured, not priced."""
    if gain is None or gain <= 0:
        return ""
    if proposal.unit_cost is not None:
        return (f"Weigh {100 * gain:.3g} availability points against "
                f"{proposal.unit_cost:,.6g} a unit; this tool does not.")
    return "Not priced: weigh the gain against what the change would cost you."


def _row(proposal: Proposal, entry: dict[str, Any], base_rows: list[dict],
         workspace: Any, bars: dict[str, Any]) -> dict[str, Any]:
    metrics = entry.get("metrics") or {}
    availability = metrics.get("availability") or {}
    delta = availability.get("delta") or {}
    gain, sem = delta.get("mean"), delta.get("sem")
    p_better = availability.get("p_better")
    other = _per_run(workspace, entry.get("run_id"))
    down, down_sem = _paired(base_rows, other, "downtime")
    cost_delta, cost_sem = (_paired(base_rows, other, "grand_total")
                            if any(row.get("grand_total") for row in base_rows)
                            else (None, None))
    lost = (metrics.get("lost_flow") or {}).get("delta") or {}
    per_unit = (gain / proposal.unit_cost
                if gain is not None and gain > 0 and proposal.unit_cost else None)
    return {
        **proposal.to_dict(),
        "run_id": entry.get("run_id"),
        "error": entry.get("error"),
        "gain": {"availability": gain, "sem": sem, "p_better": p_better,
                 "low": gain - 2 * sem if gain is not None and sem is not None else None,
                 "high": gain + 2 * sem if gain is not None and sem is not None else None,
                 "downtime_avoided": -down if down is not None else None,
                 "downtime_sem": down_sem,
                 "lost_flow_avoided": (-lost["mean"] if lost.get("mean") is not None
                                       else None)},
        "cost": {"unit": proposal.unit_cost, "run_total_change": cost_delta,
                 "run_total_sem": cost_sem, "note": proposal.cost_note},
        "gain_per_cost": per_unit,
        "verdict": "error" if entry.get("error") else _verdict(
            gain, p_better, confidence=bars["confidence"], min_gain=bars["min_gain"]),
        "weigh": _weigh(gain, proposal),
    }


def _gain_words(row: dict[str, Any], units: str) -> str:
    gain = row["gain"]
    text = (f"availability {100 * gain['availability']:+.3g} points "
            f"(± {100 * (gain['sem'] or 0):.2g})")
    if gain.get("downtime_avoided") is not None:
        text += f", downtime {downtime_words(gain['downtime_avoided'], units or 'time units')}"
    return text


def _caption(rows: list[dict[str, Any]], units: str, bars: dict[str, Any]) -> str:
    clear = [row for row in rows if row["verdict"] == CLEARLY]
    small = [row for row in rows if row["verdict"] == A_LITTLE]
    maybe = [row for row in rows if row["verdict"] == MAYBE]
    if clear:
        best = clear[0]
        others = len(clear) - 1
        return (f"Clearly helps: {best['name']}, {_gain_words(best, units)}. "
                + (f"{others} more change(s) clearly help. " if others else "")
                + "Measured, not priced: weigh each gain against its cost.")
    if small:
        return (f"No change removes {100 * MATERIAL:.0f} % or more of the downtime. "
                f"The largest real gain is small: {small[0]['name']}, "
                f"{_gain_words(small[0], units)}.")
    if maybe:
        return (f"No change is clearly better yet; {maybe[0]['name']} may help. "
                "Run more trials to be sure.")
    return "None of the tested changes measurably improves availability."


def recommend(
    workspace: Any,
    model_id: str,
    *,
    run_id: str | None = None,
    top: int = DEFAULT_TOP,
    extra: Sequence[Proposal] = (),
    trials: int | None = None,
    seed: int | None = None,
    workers: int = 1,
    timeout_s: float = 1800.0,
    min_gain: float | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    abort: Any = None,
) -> Recommendations:
    """Propose changes for the top downtime drivers and test each with Base.

    `run_id` names a stored run made with attribution; without it, Base is
    first run with attribution to find the drivers (one more batch).
    `min_gain` is the smallest availability gain (a fraction: 0.001 = 0.1
    points) called material; by default `MATERIAL` of Base's unavailability.
    `progress` receives `(done, total, label)` over every batch.  The open
    model is never modified."""
    from raptor.api.insight import drivers
    from raptor.api.scenarios.runner import Scenario, compare

    rbd = workspace.get(model_id).rbd
    attribution_run = run_id
    if run_id is not None:
        attribution = drivers.read(workspace, run_id)
    else:
        from raptor.api.run import run as run_model
        if progress:
            progress(0, 1, "Finding the downtime drivers")
        result = run_model(workspace, model_id, num_runs=trials, seed=seed,
                           workers=workers, timeout_s=timeout_s,
                           attribution=True, abort=abort,
                           confirm_long_run=True)
        attribution_run = result.run_id
        attribution = drivers.read(workspace, result.run_id)
    found = proposals(rbd, attribution, top=top) + list(extra)
    units = str(attribution.get("units") or "")
    if not found:
        return Recommendations(
            trials=int(trials or attribution.get("runs") or 0), seed=seed,
            base={}, rows=[], caption=(
                "No system downtime to act on." if not attribution.get("elements")
                else "No lever could be proposed for the top drivers (their "
                     "laws have no scale, or nothing is stocked or crewed)."),
            note=_note({}), attribution_run_id=attribution_run)
    comparison = compare(
        workspace, model_id,
        [Scenario(p.name, p.changes, p.why) for p in found],
        trials=trials, seed=seed, workers=workers, timeout_s=timeout_s,
        progress=(lambda done, total, label: progress(done, total, label))
        if progress else None, abort=abort)
    base_rows = _per_run(workspace, comparison.base.get("run_id"))
    base_availability = (comparison.base.get("metrics") or {}).get("availability") or {}
    base_mean = base_availability.get("mean")
    bars = {"confidence": 1.0 - FAMILY_ERROR / max(1, len(found)),
            "min_gain": (float(min_gain) if min_gain is not None
                         else MATERIAL * (1.0 - float(base_mean))
                         if base_mean is not None else 0.0),
            "tested": len(found), "material_share": MATERIAL if min_gain is None else None}
    rows = [_row(p, entry, base_rows, workspace, bars)
            for p, entry in zip(found, comparison.rows, strict=False)]
    # by verdict, then by gain: a known unit cost does not reorder them,
    # since most changes have none to compare with
    rows.sort(key=lambda r: (_ORDER.get(r["verdict"], 6),
                             -(r["gain"]["availability"] or 0.0)))
    return Recommendations(
        trials=comparison.trials, seed=seed,
        base={"run_id": comparison.base.get("run_id"),
              "availability": base_availability.get("mean"),
              "availability_sem": base_availability.get("sem"),
              "units": units},
        rows=rows, caption=_caption(rows, units, bars), note=_note(bars),
        attribution_run_id=attribution_run, bars=bars)


def _note(bars: dict[str, Any]) -> str:
    text = ("Each change is a fixed step chosen from the cause that dominates the "
            "driver's outages, tested against Base with the same seed and paired runs. ")
    if bars:
        material = (f"removes at least {100 * MATERIAL:.0f} % of Base's downtime "
                    f"({100 * bars['min_gain']:.3g} availability points)"
                    if bars.get("material_share") else
                    f"is at least {100 * bars['min_gain']:.3g} availability points")
        text += (f"'Clearly helps' means the gain is real (P(better) of at least "
                 f"{100 * bars['confidence']:.4g} %, the 95 % bar shared across the "
                 f"{bars['tested']} changes tested) and {material}; 'helps a little' "
                 "is real but smaller. ")
    return text + ("No verdict weighs cost: most changes have no price in the model. "
                   "The step is not an optimum: size the lever with 'raptor optimise'.")


def downtime_words(avoided: float, units: str) -> str:
    """`2.19 hours less a run`, `0.3 hours more a run`, `unchanged`."""
    if abs(avoided) < 5e-5:
        return "unchanged"
    return f"{abs(avoided):.3g} {units} {'less' if avoided > 0 else 'more'} a run"


def text_lines(result: Recommendations) -> list[str]:
    """The recommendations as plain text (the command line)."""
    lines = [f"Tested {len(result.rows)} change(s) against Base, "
             f"{result.trials} paired runs each, " + (f"seed {result.seed}." if result.seed else "the default seed.")]
    if result.base.get("availability") is not None:
        lines.append(f"Base availability {100 * result.base['availability']:.5g} %.")
    units = result.base.get("units") or "time units"
    for row in result.rows:
        lines.append("")
        lines.append(f"{row['name']}  [{row['verdict']}]")
        lines.append(f"  change: {'; '.join(row['changes'])}")
        if row.get("error"):
            lines.append(f"  error: {row['error']}")
            continue
        gain = row["gain"]
        if gain["availability"] is not None:
            chance = gain["p_better"]
            lines.append(
                f"  gain:   availability {100 * gain['availability']:+.3g} points"
                f" (± {100 * (gain['sem'] or 0):.2g}, P(better) "
                f"{pct(chance if chance is not None else 0.5)})")
        if gain["downtime_avoided"] is not None:
            lines.append(f"          downtime {downtime_words(gain['downtime_avoided'], units)}")
        cost = row["cost"]
        parts: list[str] = []
        if cost["unit"] is not None:
            parts.append(f"{cost['unit']:,.6g} a unit")
        if cost["run_total_change"] is not None:
            parts.append(f"run cost {cost['run_total_change']:+,.6g}")
        if cost["note"]:
            parts.append(cost["note"])
        lines.append("  cost:   " + ("; ".join(parts) or "unknown"))
        if row.get("weigh"):
            lines.append(f"  weigh:  {row['weigh']}")
        lines.append(f"  why:    {row['why']}")
    lines.append("")
    lines.append(result.caption)
    lines.append(result.note)
    return lines

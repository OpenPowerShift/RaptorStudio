"""The Raptor 7 Availability/Dependability weak-link ratio: one definition.

Availability and Dependability are each computed as
`up / (up + down)` over the weak-state time buckets, where `up` and `down`
name *which* buckets count as numerator and denominator.

Raptor 7: dependability *"excludes the outside influences of standby
(blues), preventive maintenance (oranges) and dependency (brown)"*. So:

- Availability denominator = idle, pm, pm_hold, repair, rep_hold, done
- Dependability denominator = repair, rep_hold, done -- **`idle` is
  dropped**, because a block's `BlockStatus.IDLE` is entered only via
  dependency.py's dependence transitions (the block-level equivalent of a
  node's "brown"). That is dependency time, exactly what dependability
  is defined to exclude.

This lived twice, and the two copies disagreed on exactly that `idle`:
`results.py`'s Weak Link table dropped it, `main_window.py`'s canvas
overlay kept it. Both then fed `apply_weak_colors` with the same GY/YR
thresholds, so for any model with dependencies the same block could be
painted green in the table and red on the canvas.

It is an engine-defined metric over engine-produced buckets, not a
presentation concern, so it belongs here rather than in either consumer.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

#: The two supported analyses. Not free text: `apply_weak_colors` and the
#: results tab both branch on exactly these, and a typo in a caller would
#: silently select the Availability buckets for a Dependability request.
AVAILABILITY = "Availability"
DEPENDABILITY = "Dependability"
#: Not a time ratio: the fraction of attempted missions the element was
#: still up through (`m_completed / missions_attempted`, Raptor 7; the same
#: arithmetic as `reports._weak_*_metrics`' R column).
RELIABILITY = "Reliability"
ANALYSES = (AVAILABILITY, DEPENDABILITY, RELIABILITY)


def canonical(name: str | None) -> str:
    """The analysis a user-facing / file string names (`nodeAnalType`).

    ONE place decides this. It replaces the GUI's scattered
    `.startswith("dep")` tests, which silently treated Reliability as
    Availability. Unknown or empty names are Availability, as in the file
    format's default.
    """
    t = (name or "").strip().lower()
    for a in ANALYSES:
        if t.startswith(a.lower()[:3]):
            return a
    return AVAILABILITY

#: Numerator -- operating time, plus standby when it counts as "up".
AVAILABILITY_UP: tuple[str, ...] = ("run", "standby")
#: Denominator -- everything that is not up. Includes `idle` and the PM
#: states: for AVAILABILITY, dependency-blocked and under-maintenance
#: time genuinely counts against availability, because the block was not
#: available. Only the DEPENDABILITY denominator drops them.
AVAILABILITY_DOWN: tuple[str, ...] = (
    "idle", "pm", "pm_hold", "repair", "rep_hold", "done",
)

#: Numerator -- standby is excluded, so a block on standby is not
#: "dependable".
DEPENDABILITY_UP: tuple[str, ...] = ("run",)
#: Denominator -- as above, `idle` is EXCLUDED. Raptor 7.
DEPENDABILITY_DOWN: tuple[str, ...] = ("repair", "rep_hold", "done")


def buckets(analys: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(up, down)` bucket names for `analys`.

    Reliability has no time buckets (it is mission-based); it falls back to
    Availability here like any unknown name, and callers use `reliability`.
    Unknown analysis names fall back to Availability rather than raising:
    every caller already selects on a user-facing menu value, and a metric
    of 0.0 would be a worse outcome than the wrong-but-total denominator.
    """
    if analys == DEPENDABILITY:
        return DEPENDABILITY_UP, DEPENDABILITY_DOWN
    return AVAILABILITY_UP, AVAILABILITY_DOWN


def reliability(weak_stats: Mapping[str, float],
                missions_attempted: int | None) -> float:
    """One element's Reliability for ONE run: `m_completed / missions`.

    With no attempted missions the engine reports 1.0 (`reports.py`
    `_weak_block_metrics`), and so does this."""
    if not missions_attempted or missions_attempted <= 0:
        return 1.0
    return float(weak_stats.get("m_completed", 0)) / missions_attempted


def weak_metric(weak_stats: Mapping[str, float], analys: str,
                missions_attempted: int | None = None) -> float:
    """The ratio for ONE run's bucket dict, `analys`-selected.

    For Reliability the ratio is mission-based, so `missions_attempted`
    (that run's `RunReportData.missions_attempted`) is needed.

    A run with no time in `up` or `down` has no ratio. Raptor 7 returns
    1.0 there (`_weak_block_metrics`, and 's
    `IF DoDenom>0 ... ELSE BlockEndDo:=1.0`), not 0.0. This matters for a
    phased block that spent the whole run on standby (HighGain in
    Phasing3): the mean of a 1.0 and a real repair fraction is the .971
    Raptor shows, not the .171 a zero would produce.
    """
    kind = canonical(analys)
    if kind == RELIABILITY:
        return reliability(weak_stats, missions_attempted)
    up_keys, down_keys = buckets(kind)
    up = sum(weak_stats.get(k, 0.0) for k in up_keys)
    down = sum(weak_stats.get(k, 0.0) for k in down_keys)
    denom = up + down
    return (up / denom) if denom else 1.0


_NODE_KEYS = ("green", "yellow", "red", "orange", "blue", "brown")


def node_metric(stats: Mapping[str, float], analys: str,
                missions_attempted: int | None = None) -> float:
    """One node's weak-link ratio for one run."""
    kind = canonical(analys)
    if kind == RELIABILITY:
        return reliability(stats, missions_attempted)
    total = sum(float(stats.get(k, 0.0)) for k in _NODE_KEYS)
    if kind == DEPENDABILITY:
        denom = (float(stats.get("green", 0.0))
                 + float(stats.get("yellow", 0.0))
                 + float(stats.get("red", 0.0)))
        up = float(stats.get("green", 0.0)) + float(stats.get("yellow", 0.0))
    else:
        denom = total
        up = (float(stats.get("green", 0.0))
              + float(stats.get("yellow", 0.0))
              + float(stats.get("blue", 0.0)))
    return (up / denom) if denom else 1.0


def event_metric(stats: Mapping[str, float], analys: str,
                 missions_attempted: int | None = None) -> float:
    """One event's weak-link ratio for one run."""
    kind = canonical(analys)
    if kind == RELIABILITY:
        return reliability(stats, missions_attempted)
    run = float(stats.get("run", 0.0))
    standby = float(stats.get("standby", 0.0))
    repair = float(stats.get("repair", 0.0))
    if kind == DEPENDABILITY:
        denom, up = run + repair, run
    else:
        denom, up = run + standby + repair, run + standby
    return (up / denom) if denom else 1.0


def cross_run_node_metric(
    runs: Sequence[Mapping[int, Mapping[str, float]]],
    node_ids: Iterable[int],
    analys: str,
    missions: Sequence[int] | None = None,
) -> dict[int, float]:
    """Average `node_metric` across runs, per node id."""
    out: dict[int, float] = {}
    for nid in node_ids:
        vals = []
        for i, stats in enumerate(runs):
            row = (stats or {}).get(nid)
            if row is None:
                continue
            vals.append(node_metric(
                row, analys, missions[i] if missions is not None else None))
        if vals:
            out[nid] = sum(vals) / len(vals)
    return out


def cross_run_event_metric(
    runs: Sequence[Mapping[int, Mapping[str, float]]],
    event_ids: Iterable[int],
    analys: str,
    missions: Sequence[int] | None = None,
) -> dict[int, float]:
    """Average `event_metric` across runs, per event id."""
    out: dict[int, float] = {}
    for eid in event_ids:
        vals = []
        for i, stats in enumerate(runs):
            row = (stats or {}).get(eid)
            if row is None:
                continue
            vals.append(event_metric(
                row, analys, missions[i] if missions is not None else None))
        if vals:
            out[eid] = sum(vals) / len(vals)
    return out


def cross_run_weak_metric(
    runs: Sequence[Mapping[int, Mapping[str, float]]],
    block_ids: Iterable[int],
    analys: str,
    missions: Sequence[int] | None = None,
) -> dict[int, float]:
    # The OUTER key is a block id and the INNER key is a bucket name. That
    # asymmetry is the point: `runs` is each run's `weak_stats`, which
    # `reporting.RunReportData` types as `dict[int, dict]`, while the inner
    # dict is the per-block bucket breakdown that `weak_metric` reads.
    # An earlier annotation said `str` on both, which was wrong at runtime
    # too -- it only type-checked because nothing verified the caller.
    """Average `weak_metric` across runs, per block id.

    Averaged as a MEAN OF RATIOS, not a ratio of sums: the two views this
    replaces both did that (each summed per-run fractions and divided by
    the run count), and it is the only definition that keeps a block's
    colour comparable to the table's. Blocks absent from a run are skipped
    rather than counted as 0.0, matching the previous behaviour.
    """
    out: dict[int, float] = {}
    for bid in block_ids:
        fracs = []
        for i, ws in enumerate(runs):
            stats = ws.get(bid) if ws else None
            if stats is None:
                continue
            fracs.append(weak_metric(
                stats, analys, missions[i] if missions is not None else None))
        if fracs:
            out[bid] = sum(fracs) / len(fracs)
    return out


#: A node's state-time buckets, and which of them the hierarchy summary
#: reads as "working", "degraded" and "down". Blue (standby), brown
#: (dependency) and orange (PM) count as up and down as the Weak Link node
#: table's Availability does: blue up, brown and orange down.
_NODE_UP = ("green", "yellow", "blue")
_NODE_DOWN = ("red", "brown", "orange")


def hier_time_shares(
    runs: Sequence[Mapping[int, Mapping[str, float]]],
) -> dict[int, dict[str, float]]:
    """Per hierarchy, the share of time it spent fully working, degraded
    and down, averaged over the runs (mean of ratios, as
    `cross_run_weak_metric`), from each run's `weak_hiers` (its Out node's
    state times).

    For the hierarchy's status dot after a run. It used to show how the
    LAST trial ended -- after a thousand trials, usually "all up", because
    most runs end with everything repaired, however often the hierarchy
    was down along the way.

    Returns `{hid: {"working", "degraded", "standby", "down",
    "availability", "runs"}}`: fractions of each run's time; the first
    four add to 1, and availability is working + degraded + standby."""
    acc: dict[int, list[dict[str, float]]] = {}
    for run in runs:
        for hid, st in (run or {}).items():
            total = sum(float(st.get(k, 0.0)) for k in _NODE_UP + _NODE_DOWN)
            if total <= 0:
                continue
            up = sum(float(st.get(k, 0.0)) for k in _NODE_UP)
            acc.setdefault(hid, []).append({
                "working": float(st.get("green", 0.0)) / total,
                "degraded": float(st.get("yellow", 0.0)) / total,
                "standby": float(st.get("blue", 0.0)) / total,
                # down as Availability counts it: failed, in PM, or held
                # down by a dependency -- so the four shares add to 1.
                "down": (total - up) / total,
                "availability": up / total,
            })
    keys = ("working", "degraded", "standby", "down", "availability")
    out: dict[int, dict[str, float]] = {}
    for hid, rows in acc.items():
        n = len(rows)
        out[hid] = {k: sum(r[k] for r in rows) / n for k in keys}
        out[hid]["runs"] = n
    return out

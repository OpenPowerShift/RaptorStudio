"""Parse the interim cost report into cumulative cost series.

The engine's cost plot is Raptor 7's `Interim Cost Report`: one block of
tables per snapshot, each starting with ``SimTime=``.  The tables already
carry every category the cost charts need, so this module reads the totals
rows and turns them into cumulative series without materialising the block
tables.

Categories are cumulative to each snapshot:

``capital``      purchase (charged at the start)
``operating``    running / standby / idle / done / event / disposal costs
``repair``       hands-on repair and repair hold
``pm``           planned maintenance and PM hold
``spares``       block sparing and spare-pool costs
``resources``    crew / equipment resources
``downtime``     system Red-time penalty
``lost_flow``    capacity-weighted lost-flow cost (capacity related)
"""
from __future__ import annotations

import functools
import math
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

CATEGORIES = ("capital", "operating", "repair", "pm", "spares",
              "resources", "downtime", "lost_flow")
CATEGORY_LABELS = {
    "capital": "Capital (purchase)",
    "operating": "Running and other operating",
    "repair": "Repair and repair hold",
    "pm": "Planned maintenance",
    "spares": "Spares",
    "resources": "Crews and equipment",
    "downtime": "System downtime",
    "lost_flow": "Lost flow (capacity)",
}
#: The two rows that are capacity-related, kept separate on purpose.
CAPACITY_CATEGORIES = ("lost_flow", "downtime")

_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(value) for value in _NUMBER.findall(text)]


def _snapshot(sim_time: float) -> dict[str, Any]:
    return {"t": sim_time, "purchase": 0.0, "op": 0.0, "repair": 0.0,
            "idle": 0.0, "rep_hold": 0.0, "pm": 0.0, "pm_hold": 0.0,
            "standby": 0.0, "done": 0.0, "block_spares": 0.0,
            "disposal": 0.0, "pool_spares": 0.0, "resources": 0.0,
            "events": 0.0, "system_down": 0.0, "lost_flow": 0.0,
            "grand": 0.0}


def _add(dst: dict[str, list[float]], row: dict[str, Any]) -> None:
    """Append one snapshot to a run's column lists."""
    if not dst:
        dst.update({key: [] for key in row})
    for key in dst:
        dst[key].append(float(row.get(key) or 0.0))


def _set_operating(snap: dict[str, Any], values: list[float]) -> None:
    (snap["op"], snap["repair"], snap["idle"], snap["rep_hold"], snap["pm"],
     snap["pm_hold"], snap["standby"], snap["done"]) = values[:8]


def _set_life_cycle(snap: dict[str, Any], values: list[float]) -> None:
    snap["purchase"], snap["disposal"] = values[0], values[3]


#: section -> (row prefix, least numbers on the row, how to store them).
#: The totals rows only: every other row is skipped unparsed, since a large
#: model's report is mostly per-block rows and running the number pattern
#: over each of them was most of the parse time.
_TOTAL_ROWS: dict[str, tuple[str, int, Any]] = {
    "Operating": ("Totals", 8, _set_operating),
    "Sparing": ("Totals", 3,
                lambda snap, values: snap.update(block_spares=sum(values[:3]))),
    "Life_cycle_cost": ("Block_Totals", 5, _set_life_cycle),
    "SparePools": ("SparePool_Totals", 4,
                   lambda snap, values: snap.update(pool_spares=values[3])),
    "Resources": ("Resource_Totals", 4,
                  lambda snap, values: snap.update(resources=values[3])),
}
#: Cost_Summary rows: prefix -> snapshot key (the last number on the row).
_SUMMARY_ROWS = (("Events", "events"), ("SystemDownCost", "system_down"),
                 ("SystemLostFlowCost", "lost_flow"), ("Grand_Total", "grand"))


def _totals(snap: dict[str, Any], section: str, stripped: str) -> None:
    """Read one totals row of `section` into `snap`."""
    if section == "Cost_Summary":
        for prefix, key in _SUMMARY_ROWS:
            if stripped.startswith(prefix):
                values = _numbers(stripped[len(prefix):])
                snap[key] = values[-1] if values else 0.0
                return
        return
    row = _TOTAL_ROWS.get(section)
    if row is None or not stripped.startswith(row[0]):
        return
    values = _numbers(stripped)
    if len(values) >= row[1]:
        row[2](snap, values)


def parse_cost_lines(lines: Iterable[str]) -> list[dict[str, list[float]]]:  # noqa: C901, PLR0912 - one report walk
    """Cumulative cost snapshots, one dict of columns per run."""
    runs: list[dict[str, list[float]]] = []
    current: dict[str, list[float]] = {}
    snap: dict[str, Any] | None = None
    section = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if "End_of_Run_" in stripped:
            if snap is not None:
                _add(current, snap)
                snap = None
            if current:
                runs.append(current)
                current = {}
            continue
        if stripped.startswith("SimTime="):
            if snap is not None:
                _add(current, snap)
            values = _numbers(stripped)
            snap = _snapshot(values[0] if values else 0.0)
            section = ""
            continue
        if stripped.startswith("*"):
            # a separator ends the cost tables of a snapshot
            section = ""
            continue
        if stripped in ("Operating", "Sparing", "Life_cycle_cost",
                        "Cost_Summary"):
            section = stripped
            continue
        if (stripped.startswith(("SparePools", "Resources"))
                and "Initial" in stripped):
            section = "SparePools" if stripped.startswith("SparePools") else "Resources"
            continue
        if snap is not None and section:
            _totals(snap, section, stripped)
    if snap is not None:
        _add(current, snap)
    if current:
        runs.append(current)
    return runs


def _final_as_interim(lines: Iterable[str]) -> Iterator[str]:
    """The end-of-run report's ``***COST***`` tables, as interim snapshots.

    Each run of the end-of-run report (ours and Raptor 7's share the layout)
    starts at ``***Run_N***``, gives ``EndingSimTime`` in its summary and
    then the same cost tables the interim report prints, under
    ``***COST***``.  Only those tables are passed on, each run as one
    snapshot at its ending time.
    """
    in_run = in_cost = False
    ending: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("***Run_"):
            if in_run:
                yield "End_of_Run_"
            in_run, in_cost, ending = True, False, None
            continue
        if stripped.startswith("EndingSimTime"):
            ending = stripped[len("EndingSimTime"):]
            continue
        if stripped.startswith("***"):
            in_cost = stripped.startswith("***COST")
            if in_cost:
                yield f"SimTime= {ending or 0}"
            else:
                yield "***"
            continue
        if in_cost:
            yield line
    if in_run:
        yield "End_of_Run_"


def parse_final_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Each run's end-of-run cost totals (one snapshot per run).

    A run without a ``***COST***`` section (cost analysis off) gives an
    empty snapshot list.
    """
    out: list[dict[str, Any]] = []
    for row in parse_cost_lines(_final_as_interim(lines)):
        out.append({key: values[-1] for key, values in row.items()} if row.get("t") else {})
    return out


def parse_cost_file(path: str) -> list[dict[str, list[float]]]:
    """Read a cost-plot file lazily, one row at a time."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return parse_cost_lines(handle)


def parse_final_file(path: str) -> list[dict[str, Any]]:
    """Each run's end-of-run cost totals, from an end-of-run report file."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return parse_final_lines(handle)


def close_runs(runs: list[dict[str, list[float]]],
               finals: list[dict[str, Any]] | None) -> list[dict[str, list[float]]]:
    """Append each run's end-of-run totals as its last snapshot.

    The interim report samples on an interval and never at the run's end, so
    a 20-year run sampled every half year last reports at 19.5 years.  The
    end-of-run report holds the same tables at the true end; without them the
    last part-interval of cost is missing (and holding the last snapshot flat
    to the end, as an earlier version did, put no cost in it at all).

    The runs pair up by position; when the two reports do not have the same
    number of runs, or a final is empty, the interim runs come back as they
    were.
    """
    if not finals or len(finals) != len(runs):
        return runs
    out: list[dict[str, list[float]]] = []
    for row, final in zip(runs, finals, strict=True):
        times = row.get("t") or []
        if not final or not times or float(final.get("t") or 0.0) < float(times[-1]):
            out.append(row)
            continue
        copied = {key: list(values) for key, values in row.items()}
        if float(final["t"]) == float(times[-1]):
            for key, values in copied.items():
                values[-1] = float(final.get(key) or 0.0)
        else:
            for key, values in copied.items():
                values.append(float(final.get(key) or 0.0))
        out.append(copied)
    return out


@functools.lru_cache(maxsize=8)
def _load(cost_path: str, cost_key: tuple[int, int],
          final_path: str | None, final_key: tuple[int, int] | None
          ) -> tuple[list[dict[str, list[float]]], bool]:
    runs = parse_cost_file(cost_path)
    closed = runs
    if final_path:
        closed = close_runs(runs, parse_final_file(final_path))
    return closed, closed is not runs


def _stat_key(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def load_runs(cost_path: str | Path,
              end_of_run_path: str | Path | None = None
              ) -> list[dict[str, list[float]]]:
    """The interim cost runs, closed at each run's end when the end-of-run
    report is given.  Cached by file identity: a stored run's 70 MB cost
    report is parsed once, not once per chart.  Treat the result as
    read-only."""
    cost = Path(cost_path)
    final = Path(end_of_run_path) if end_of_run_path else None
    if final is not None and not final.is_file():
        final = None
    runs, _closed = _load(str(cost), _stat_key(cost),
                          str(final) if final else None,
                          _stat_key(final) if final else None)
    return runs


def stored_runs(workspace: Any, run_id: str) -> list[dict[str, list[float]]] | None:
    """A stored run's cost series for the cost charts, or None when the run
    recorded no interim cost report.  The one loader the MCP tools, the
    command line and the report share."""
    from raptor.api.run import run_dir
    folder = run_dir(workspace, run_id)
    cost = folder / "cost.txt"
    if not cost.is_file():
        return None
    return load_runs(cost, folder / "end_of_run.txt")


def cumulative(row: dict[str, list[float]]) -> dict[str, list[float]]:
    """The category columns of one run, cumulative and ready to draw."""
    out: dict[str, list[float]] = {key: [] for key in CATEGORIES}
    for i in range(len(row.get("t") or [])):
        operating = (row["op"][i] + row["idle"][i] + row["standby"][i]
                     + row["done"][i] + row["events"][i] + row["disposal"][i])
        out["capital"].append(row["purchase"][i])
        out["operating"].append(operating)
        out["repair"].append(row["repair"][i] + row["rep_hold"][i])
        out["pm"].append(row["pm"][i] + row["pm_hold"][i])
        out["spares"].append(row["block_spares"][i] + row["pool_spares"][i])
        out["resources"].append(row["resources"][i])
        out["downtime"].append(row["system_down"][i])
        out["lost_flow"].append(row["lost_flow"][i])
    return out


def category_totals(runs: list[dict[str, list[float]]]) -> dict[str, float]:
    """Mean life-cycle total in each category, over the runs."""
    if not runs:
        return {key: 0.0 for key in CATEGORIES}
    totals = {key: 0.0 for key in CATEGORIES}
    for row in runs:
        values = cumulative(row)
        for key in CATEGORIES:
            totals[key] += values[key][-1] if values[key] else 0.0
    return {key: value / len(runs) for key, value in totals.items()}


def _quantile(ordered: list[float], share: float) -> float:
    if not ordered:
        return 0.0
    pos = share * (len(ordered) - 1)
    low = math.floor(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def mean_curves(runs: list[dict[str, list[float]]], *,
                points: int = 40) -> tuple[list[float], dict[str, list[float]]]:
    """Mean cumulative cost in each category on a common time grid."""
    usable = [row for row in runs if len(row.get("t") or []) >= 2]
    if not usable:
        return [], {key: [] for key in CATEGORIES}
    horizon = min(row["t"][-1] for row in usable)
    count = max(2, points)
    grid = [horizon * i / (count - 1) for i in range(count)]
    curves: dict[str, list[float]] = {key: [] for key in CATEGORIES}
    prepared = [(row["t"], cumulative(row)) for row in usable]
    for x in grid:
        means = {key: 0.0 for key in CATEGORIES}
        for times, values in prepared:
            for key in CATEGORIES:
                series = values[key]
                if series:
                    means[key] += _interp(times, series, x)
        for key in CATEGORIES:
            curves[key].append(means[key] / len(usable))
    return grid, curves


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    if not xs or not ys:
        return 0.0
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    return ys[lo] + (0.0 if span <= 0 else (x - xs[lo]) / span
                     * (ys[hi] - ys[lo]))


def annual_rows(runs: list[dict[str, list[float]]], *,
                year: float = 8760.0,
                include_capital: bool = False) -> list[dict[str, float]]:
    """Cost in each whole year, as the across-run distribution.

    Capital is a one-off at the start, not an annual cost: by default the
    annual series is operating cost only, so a 20-year chart is not one
    enormous first bar and 19 small ones.
    """
    by_year: dict[int, list[float]] = {}
    keys = (CATEGORIES if include_capital else
            tuple(key for key in CATEGORIES if key != "capital"))
    for row in runs:
        if not row.get("t"):
            continue
        values = cumulative(row)
        total = [sum(values[key][i] for key in keys)
                 for i in range(len(row["t"]))]
        horizon = row["t"][-1]
        years = int(horizon // year)
        for index in range(1, years + 1):
            lo = (0.0 if index == 1 else
                  _interp(row["t"], total, (index - 1) * year))
            hi = _interp(row["t"], total, index * year)
            by_year.setdefault(index, []).append(max(0.0, hi - lo))
    out: list[dict[str, float]] = []
    for index in sorted(by_year):
        ordered = sorted(by_year[index])
        out.append({
            "index": float(index), "n": float(len(ordered)),
            "mean": sum(ordered) / len(ordered),
            "p5": _quantile(ordered, 0.05), "p25": _quantile(ordered, 0.25),
            "p50": _quantile(ordered, 0.50), "p75": _quantile(ordered, 0.75),
            "p95": _quantile(ordered, 0.95),
        })
    return out

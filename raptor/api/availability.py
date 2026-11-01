"""Point availability, availability classifications and criticality tables.

These are derived from data the simulation already records. They are kept out
of the legacy `End_Of_Run`/`Final_Results` text on purpose: those files are
byte-compared against captured reference output, and adding rows there would
break fidelity. The API reads the per-run JSON instead.
"""
from __future__ import annotations

import json
import math
from typing import Any

from raptor.api.run import run_dir
from raptor.api.workspace import RunArtefactsError, Workspace

_UP_COLOURS = {"Green", "Yellow"}


def availability_at(run: dict[str, Any], t: float) -> float | None:
    """Point availability at absolute sim time ``t`` for one run row.

    ``run`` is one entry from ``per_run.json``; its ``color_timeline`` is a
    list of ``[time, colour]`` transitions. Returns ``None`` for a row that
    predates the timeline.
    """
    if not math.isfinite(t):
        raise ValueError("time must be a finite number")
    t = float(t)
    if not math.isfinite(t):
        raise ValueError("time must be a finite number")
    if t < 0.0:
        raise ValueError("time must be non-negative")
    timeline = run.get("color_timeline")
    if not isinstance(timeline, list) or not timeline:
        return None
    current: str | None = None
    for entry in timeline:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        try:
            when = float(entry[0])
        except (TypeError, ValueError):
            continue
        if when <= t:
            current = str(entry[1])
        else:
            break
    if current is None:
        return None
    return 1.0 if current in _UP_COLOURS else 0.0


def read_per_run(workspace: Workspace, run_id: str) -> list[dict[str, Any]]:
    """The stored per-run rows for ``run_id``."""
    path = run_dir(workspace, run_id) / "per_run.json"
    try:
        rows = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RunArtefactsError(
            f"run {run_id!r} has no per-run data; re-run it") from exc
    except ValueError as exc:
        raise RunArtefactsError(
            f"run {run_id!r} has corrupt per-run data; re-run it") from exc
    if not isinstance(rows, list):
        raise RunArtefactsError(
            f"run {run_id!r} has corrupt per-run data; re-run it")
    return [row for row in rows if isinstance(row, dict)]


def availability_at_run(workspace: Workspace, run_id: str, t: float,  # noqa: C901
                        *, runs: str = "mean") -> dict[str, Any]:
    """Point availability from every run in a stored batch.

    ``runs`` is ``"mean"`` (default), ``"all"``, or a 1-based run number.
    """
    t = float(t)
    if not math.isfinite(t):
        raise ValueError("time must be a finite number")
    rows = read_per_run(workspace, run_id)
    if not rows:
        raise RunArtefactsError(f"run {run_id!r} has no runs")
    values: list[float] = []
    picked: list[int] = []
    for index, row in enumerate(rows, 1):
        value = availability_at(row, t)
        if value is not None:
            values.append(value)
            picked.append(index)
    if not values:
        raise RunArtefactsError(
            f"run {run_id!r} has no colour timeline; re-run it with this "
            "version")
    result: dict[str, Any] = {
        "run_id": run_id,
        "time": float(t),
        "source_runs": picked,
    }
    if runs == "all":
        result["values"] = values
    elif runs == "mean":
        result["availability"] = sum(values) / len(values)
        result["runs"] = len(values)
        result["min"] = min(values)
        result["max"] = max(values)
    else:
        try:
            wanted = int(runs)
        except (TypeError, ValueError) as exc:
            raise ValueError("runs must be 'mean', 'all', or a run number") from exc
        if not 1 <= wanted <= len(rows):
            raise ValueError(f"run number must be 1..{len(rows)}")
        value = availability_at(rows[wanted - 1], t)
        if value is None:
            raise RunArtefactsError(
                f"run {wanted} has no colour timeline; re-run it")
        result["availability"] = value
        result["run"] = wanted
    return result


def criticality_rows(workspace: Workspace, run_id: str) -> list[dict[str, Any]]:  # noqa: C901
    """Average per-block criticality ratios over a stored batch.

    Block identities are keyed by id. A block missing from some run's table
    contributes only the runs that have it; ``samples`` says how many.
    """
    rows = read_per_run(workspace, run_id)
    if not rows:
        raise RunArtefactsError(f"run {run_id!r} has no runs")
    acc: dict[int, dict[str, Any]] = {}
    for run_row in rows:
        table = run_row.get("criticality")
        if not isinstance(table, dict):
            continue
        for key, value in table.items():
            if not isinstance(value, dict):
                continue
            try:
                bid = int(key)
            except (TypeError, ValueError):
                continue
            entry = acc.setdefault(bid, {
                "id": bid,
                "name": "".join(
                    ch for ch in str(value.get("name", ""))
                    if ch.isprintable())[:256],
                "parent_id": value.get("parent_id", 0),
                "samples": 0,
                "failure": [],
                "downtime": [],
                "cost": [],
                "downing_event": [],
            })
            entry["samples"] += 1
            for field in ("failure", "downtime", "cost", "downing_event"):
                number = value.get(field)
                if isinstance(number, (int, float)) and math.isfinite(float(number)):
                    entry[field].append(float(number))
    out: list[dict[str, Any]] = []
    for entry in acc.values():
        out_row: dict[str, Any] = {
            "id": entry["id"],
            "name": str(entry["name"]) if entry["name"] is not None else "",
            "parent_id": entry["parent_id"],
            "samples": entry["samples"],
        }
        for field in ("failure", "downtime", "cost", "downing_event"):
            samples = entry[field]
            out_row[field] = sum(samples) / len(samples) if samples else None
        out.append(out_row)
    out.sort(key=lambda item: (item["failure"] is None,
                               -(item["failure"] or 0.0),
                               item["name"].lower()))
    return out


def availability_summary(run: dict[str, Any]) -> dict[str, Any]:
    """The availability classifications stored on one per-run row."""
    keys = ("availability", "inherent_availability", "achieved_availability",
            "operational_availability", "mttff", "mttff_censored",
            "num_component_cms", "num_component_pms")
    out: dict[str, Any] = {}
    for key in keys:
        value = run.get(key)
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        out[key] = value
    return out

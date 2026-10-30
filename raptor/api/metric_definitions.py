"""Field-to-label map for the measures the engine reports, and their stats.

**Definitions and equations are not here.** They live once, in
``docs/formulas.adoc`` (shipped as Help ▸ Formulas and readable over MCP with
``read_doc('formulas')``). This module used to carry a third copy -- a
``definition`` and a ``formula`` per metric, alongside the copy in the GUI's
Summary tooltips -- and the copies had already drifted: Availability was
``("Green" + "Yellow") / (Green + Yellow + Red)`` on the formulas page,
``uptime / total simulated time`` here, and ``Ao = Uptime / Total Time`` in
the GUI. Three answers to one question.

What remains is the mapping the code actually needs: which ``RunReportData``
attribute a metric comes from, what the report calls its aggregate, and
whether it is a duration (so a unit is appended).
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

#: ``key`` = ``RunReportData`` attribute; ``aggregate`` = the name
#: ``raptor.api.results.aggregate_metrics`` uses; ``units`` is ``"time"`` for
#: durations so a caller can append the model's own unit.
METRICS: tuple[dict[str, str], ...] = (
    {"key": "availability", "metric": "Availability",
     "aggregate": "Availability", "units": ""},
    {"key": "reliability", "metric": "Reliability",
     "aggregate": "Reliability", "units": ""},
    {"key": "conditional_reliability", "metric": "Conditional reliability",
     "aggregate": "Conditional_Reliability", "units": ""},
    {"key": "dependability", "metric": "Dependability",
     "aggregate": "Dependability", "units": ""},
    {"key": "mtbde", "metric": "MTBDE", "aggregate": "MTBDE",
     "units": "time"},
    {"key": "mdt", "metric": "MDT", "aggregate": "MDT", "units": "time"},
    {"key": "mtbm", "metric": "MTBM", "aggregate": "MTBM", "units": "time"},
    {"key": "mtbmu", "metric": "MTBMu", "aggregate": "MTBMu",
     "units": "time"},
    {"key": "mtbms", "metric": "MTBMs", "aggregate": "MTBMs",
     "units": "time"},
    {"key": "mmt", "metric": "MMT", "aggregate": "MMT", "units": "time"},
    {"key": "mmtu", "metric": "MMTu", "aggregate": "MMTu", "units": "time"},
    {"key": "mmts", "metric": "MMTs", "aggregate": "MMTs", "units": "time"},
    {"key": "num_system_failures", "metric": "System failures",
     "aggregate": "System_Failures", "units": ""},
    {"key": "num_component_failures", "metric": "Component failures",
     "aggregate": "Component_Failures", "units": ""},
    {"key": "green_percent", "metric": "Green %",
     "aggregate": "Green_Percent", "units": "%"},
    {"key": "yellow_percent", "metric": "Yellow %",
     "aggregate": "Yellow_Percent", "units": "%"},
    {"key": "red_percent", "metric": "Red %",
     "aggregate": "Red_Percent", "units": "%"},
    # Capacity analysis only (`analysis`): absent from other runs' summaries.
    {"key": "units_produced", "metric": "Units produced",
     "aggregate": "Units_Produced", "units": "flow x time",
     "analysis": "capacity"},
    {"key": "mean_flow", "metric": "Mean flow",
     "aggregate": "Mean_Flow", "units": "flow", "analysis": "capacity"},
    {"key": "mean_capacity", "metric": "Mean capacity",
     "aggregate": "Mean_Capacity", "units": "flow", "analysis": "capacity"},
    {"key": "capacity_weighted_availability",
     "metric": "Capacity-weighted availability",
     "aggregate": "Capacity_Weighted_Availability", "units": "",
     "analysis": "capacity"},
    {"key": "lost_flow", "metric": "Lost flow",
     "aggregate": "Lost_Flow", "units": "flow", "analysis": "capacity"},
)


def definitions() -> list[dict[str, str]]:
    """Metric names and units. See the module docstring for where the
    definitions themselves live."""
    return [dict(row) for row in METRICS]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def samples_from_per_run(per_run: Iterable[Any]) -> dict[str, int]:
    """Count the runs that contributed a finite value to each metric."""
    rows = [row for row in per_run if isinstance(row, dict)]
    return {
        spec["key"]: sum(1 for row in rows
                         if _finite(row.get(spec["key"])) is not None)
        for spec in METRICS
        if not spec.get("analysis")
        or any(spec["key"] in row for row in rows)
    }


def _metric_row(spec: Mapping[str, str], entry: Any, units: str,
                samples: Mapping[str, int] | None) -> dict[str, Any]:
    """One metric's row of `from_summary`."""
    row: dict[str, Any] = {
        **spec,
        "samples": (samples.get(spec["key"]) if samples is not None
                    else None),
        "units": units if spec["units"] == "time" else spec["units"],
        "censored": False,
    }
    if isinstance(entry, Mapping):
        for key in ("min", "mean", "max", "stdev", "sem"):
            row[key] = _finite(entry.get(key))
        row["censored"] = bool(entry.get("censored", False))
        if row["censored"]:
            row["stdev"] = None
            row["sem"] = None
        if row["mean"] is None:
            row["samples"] = 0
    else:
        for key in ("min", "mean", "max", "stdev", "sem"):
            row[key] = None
        row["samples"] = 0
    return row


def from_summary(summary: Mapping[str, Any], *, units: str = "",
                 runs: int | None = None,
                 samples: Mapping[str, int] | None = None
                 ) -> dict[str, Any]:
    """Metric names plus one run's aggregate statistics, for MCP."""
    rows: list[dict[str, Any]] = []
    for spec in METRICS:
        entry = summary.get(spec["aggregate"])
        if spec.get("analysis") and entry is None:
            continue  # that analysis was off for this run
        rows.append(_metric_row(spec, entry, units, samples))
    notes = []
    if any(row["censored"] for row in rows):
        notes.append("Censored values are lower bounds (marked > in the "
                     "report); their standard deviation and SEM are not "
                     "shown.")
    if samples is not None and runs:
        short = [f"{row['metric']}: {row['samples']} of {runs} runs"
                 for row in rows
                 if row["samples"] is not None and row["samples"] < runs]
        if short:
            notes.append("Sample counts: " + "; ".join(short) + ".")
    return {"metrics": rows, "runs": runs, "units": units, "notes": notes}

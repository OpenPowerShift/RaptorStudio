"""Convert engine ``RunReportData`` objects into plain dicts.

A ``Metric`` is ``{min, mean, max, stdev, sem}`` across all completed
runs.  Metrics whose engine value is ``None`` (e.g. MTBDE when no
down-event occurred) carry ``None`` for *every* stat — the consumer
renders them as ``N/A``.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from raptor.core.engine.reporting import RunReportData


# ---------------------------------------------------------------------------
# Metric dataclass
# ---------------------------------------------------------------------------

@dataclass
class Metric:
    """One named metric with its per-run aggregate stats."""
    name: str
    min: float | None = None
    mean: float | None = None
    max: float | None = None
    stdev: float | None = None
    sem: float | None = None
    #: A lower bound (no downing/maintenance event), matching the ``>``
    #: qualifier in the report and GUI.
    censored: bool = False
    #: The raw value the bound came from (the run length, when a run ended
    #: with no event to measure). ``None`` when not censored.
    censored_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        for k in ("min", "mean", "max", "stdev", "sem", "censored",
                  "censored_at"):
            d[k] = getattr(self, k)
        return d


def _safe_stdev(vals: list[float]) -> float | None:
    """Return sample stdev, or None when fewer than 2 samples."""
    if len(vals) < 2:
        return None
    return statistics.stdev(vals)


def _safe_sem(vals: list[float]) -> float | None:
    """Return standard error of the mean, or None when < 2 samples."""
    sd = _safe_stdev(vals)
    if sd is None:
        return None
    return sd / (len(vals) ** 0.5)


def _aggregate(values: list[float | None], name: str) -> Metric:
    """Build a Metric from per-run values, filtering Nones."""
    finite = [v for v in values if v is not None]
    if not finite:
        return Metric(name=name)
    return Metric(
        name=name,
        min=min(finite),
        mean=statistics.fmean(finite),
        max=max(finite),
        stdev=_safe_stdev(finite),
        sem=_safe_sem(finite),
    )


# ---------------------------------------------------------------------------
# Public aggregation
# ---------------------------------------------------------------------------

_METRIC_FIELDS: list[tuple[str, str]] = [
    ("availability", "Availability"),
    ("reliability", "Reliability"),
    ("conditional_reliability", "Conditional_Reliability"),
    ("dependability", "Dependability"),
    ("mtbde", "MTBDE"),
    ("mdt", "MDT"),
    ("mtbm", "MTBM"),
    ("mtbmu", "MTBMu"),
    ("mtbms", "MTBMs"),
    ("mmt", "MMT"),
    ("mmtu", "MMTu"),
    ("mmts", "MMTs"),
    ("num_system_failures", "System_Failures"),
    ("num_component_failures", "Component_Failures"),
    ("green_percent", "Green_Percent"),
    ("yellow_percent", "Yellow_Percent"),
    ("red_percent", "Red_Percent"),
]


#: Time-between-event metrics whose engine value falls back to the whole
#: run length when the run had no such event: a lower bound, not a
#: measurement. MTBDE has its own failure counter from the same rule.
_CENSORED_BY_RUN_LENGTH = {
    "MTBM": "mtbm",
    "MTBMu": "mtbmu",
    "MTBMs": "mtbms",
    "MMT": "mmt",
    "MMTu": "mmtu",
    "MMTs": "mmts",
}


def _mark_censored(metric: Metric, label: str,
                   report_data: list[RunReportData]) -> None:
    """Flag a cross-run aggregate that is a lower bound, in place.

    The engine writes the raw run length when a run had no downing or
    maintenance event. The number is still useful, but it is a bound; a
    machine reader must be able to tell it apart from a measurement.
    """
    is_censored = False
    if label == "MTBDE":
        is_censored = any(
            getattr(rd, "num_system_failures", 0) == 0
            for rd in report_data)
    else:
        attr = _CENSORED_BY_RUN_LENGTH.get(label)
        if attr:
            is_censored = any(
                getattr(rd, attr, None) is not None
                and getattr(rd, "ending_sim_time", 0)
                and abs(getattr(rd, attr) - rd.ending_sim_time) < 1e-9
                for rd in report_data)
    if is_censored:
        metric.censored = True
        metric.censored_at = (metric.mean if metric.mean is not None
                              else metric.max)


#: Capacity-analysis metrics, present only when the model runs capacity
#: analysis: ``(key in capacity_values, aggregate name)``. The first three
#: are the Final Results rows of the same names.
CAPACITY_FIELDS: list[tuple[str, str]] = [
    ("units_produced", "Units_Produced"),
    ("mean_flow", "Mean_Flow"),
    ("mean_capacity", "Mean_Capacity"),
    ("capacity_weighted_availability", "Capacity_Weighted_Availability"),
    ("lost_flow", "Lost_Flow"),
]


def capacity_values(rd: RunReportData) -> dict[str, float | None] | None:
    """One run's capacity figures, or None without capacity analysis.

    ``mean_flow``/``mean_capacity`` are the system (Start) node's average
    flow and capacity, as in Final Results. ``capacity_weighted_availability``
    is ``mean_flow / FlowGenerated``: the fraction of the generated flow
    delivered, where ``availability`` only says whether *any* flow got
    through. ``lost_flow`` is ``FlowGenerated - mean_flow``."""
    cap = rd.capacity
    if cap is None:
        return None
    system = cap.nodes[cap.system_node_id]
    generated = float(cap.flow_generated)
    return {
        "units_produced": cap.units_produced,
        "mean_flow": system.ave_flow,
        "mean_capacity": system.ave_cap,
        "capacity_weighted_availability": (system.ave_flow / generated
                                           if generated > 0 else None),
        "lost_flow": generated - system.ave_flow,
    }


def aggregate_metrics(report_data: list[RunReportData]) -> list[Metric]:
    """Compute per-metric ``{min, mean, max, stdev, sem}`` across all runs,
    with the capacity metrics appended when the model has capacity analysis."""
    result: list[Metric] = []
    for attr, label in _METRIC_FIELDS:
        vals = [getattr(rd, attr, None) for rd in report_data]
        metric = _aggregate(vals, label)
        _mark_censored(metric, label, report_data)
        result.append(metric)
    caps = [capacity_values(rd) for rd in report_data]
    if caps and all(c is not None for c in caps):
        for key, label in CAPACITY_FIELDS:
            result.append(_aggregate([c[key] for c in caps if c is not None], label))
    return result


def metrics_to_dict(metrics: list[Metric]) -> dict[str, dict[str, Any]]:
    """``{metric_name: {min, mean, max, stdev, sem}}`` for JSON serialisation."""
    return {m.name: m.to_dict() for m in metrics}

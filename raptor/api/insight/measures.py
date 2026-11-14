"""One definition for every measure the application shows, and the ladder.

The GUI, the report and MCP read this registry instead of restating a
definition in each place.  Formulas are kept in `docs/formulas.adoc`; this
module carries the plain-English answer, the clock the number uses, the
names other tools give it, and the honest caveat.

The **downtime ladder** is the one picture shared by MDT, MTTR and
availability:

    failure -> logistics -> wait spare -> wait crew -> repair -> logistics
    |<---------------- time down (MDT) ------------------------------->|
                                        |<--- MTTR in the strict sense -->|
    inherent availability counts only repair;
    achieved availability adds planned maintenance;
    operational availability adds logistics, spares, crews and dependency.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from raptor.api.metric_definitions import METRICS as _METRICS


@dataclass(frozen=True)
class Measure:
    id: str
    name: str
    short: str
    long: str
    answers: str
    formula: str
    clock: str
    counts: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    also_called: dict[str, str] = field(default_factory=dict)
    related: tuple[str, ...] = ()
    use_when: str = ""
    beware: str = ""
    units: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _m(id: str, name: str, short: str, long: str, answers: str,
       formula: str, clock: str, *, units: str = "",
       counts: tuple[str, ...] = (), excludes: tuple[str, ...] = (),
       also_called: dict[str, str] | None = None,
       related: tuple[str, ...] = (), use_when: str = "",
       beware: str = "") -> Measure:
    return Measure(id=id, name=name, short=short, long=long, answers=answers,
                   formula=formula, clock=clock, units=units, counts=counts,
                   excludes=excludes, also_called=also_called or {},
                   related=related, use_when=use_when, beware=beware)


MEASURES: tuple[Measure, ...] = (
    _m("availability", "Availability", "Ao",
       "Mean fraction of simulated time the system was working",
       "What fraction of time does the system work?",
       "uptime / total simulated time", "system Green or Yellow time",
       counts=("Green", "Yellow"),
       excludes=("Red",),
       also_called={"AWB": "Operational availability"},
       related=("mtbde", "mdt"), use_when="A long-run service question.",
       beware="A system at half capacity is still 'up'."),
    _m("inherent_availability", "Inherent availability", "Ai",
       "Availability counting only hands-on repair as downtime",
       "What would the design achieve with repair only?",
       "uptime / (uptime + hands-on repair)",
       "system up time and RED_CORRECTIVE_TIME",
       related=("achieved_availability", "operational_availability"),
       beware="Ignores spares, crews, logistics and PM."),
    _m("achieved_availability", "Achieved availability", "Aa",
       "Availability counting repair and planned maintenance",
       "What would the plant achieve with maintenance as planned?",
       "uptime / (uptime + repair + PM)",
       "system up time, repair and PM",
       related=("inherent_availability", "operational_availability")),
    _m("dependability", "Dependability", "Do",
       "Mean fraction of mission time the system was working",
       "What fraction of the mission was the system working?",
       "mission uptime / mission total time", "mission time only",
       counts=("mission Green", "mission Yellow"),
       related=("availability", "reliability"),
       beware="Undefined or equal to availability for an unphased model."),
    _m("reliability", "Reliability", "R",
       "Missions that finished without a system failure, over missions attempted",
       "What fraction of missions survived intact?",
       "missions with no system failure / missions attempted",
       "one mission per run", use_when="A pass/fail question."),
    _m("conditional_reliability", "Conditional reliability", "R(t)",
       "Probability of surviving the rest of the mission given survival so far",
       "Given it survived to now, will it finish?",
       "survivals to mission end / runs alive at the condition time",
       "mission time", beware="Needs a model with a condition time."),
    _m("mtbde", "Time between failures", "MTBDE",
       "Mean working time between events that down the system",
       "How often does it go down?",
       "uptime / downing events", "system up time after statistics start",
       counts=("failure outages", "PM outages"),
       also_called={"AWB": "MTBO", "BlockSim": "MTBE (system)"},
       related=("mdt", "availability"),
       beware="Undefined when no outage happened; shown as a lower bound."),
    _m("mdt", "Time down", "MDT",
       "Mean time the system is fully down per downing event",
       "How long is one outage?",
       "downtime / downing events", "system Red time",
       related=("mtbde", "mmt"),
       also_called={"AWB": "MTTR (system, loosely)"},
       beware="The whole restoration, including waits; not the strict MTTR."),
    _m("mtbm", "Time between maintenance", "MTBM",
       "Mean total time between maintenance events", "How often is maintenance due?",
       "total time / maintenance events", "total time", related=("mmt",)),
    _m("mtbmu", "Time between corrective maintenance", "MTBMu",
       "Mean time between repairs", "How often is a repair needed?",
       "total time / corrective events", "total time", related=("mmt",)),
    _m("mtbms", "Time between preventive maintenance", "MTBMs",
       "Mean time between planned maintenance", "How often is PM due?",
       "total time / preventive events", "total time", related=("mmt",)),
    _m("mmt", "Mean maintenance time", "MMT",
       "Mean duration of one maintenance event", "How long does maintenance take?",
       "maintenance time / maintenance events", "maintenance time",
       related=("mdt", "mtbm")),
    _m("mmtu", "Mean corrective maintenance time", "MMTu",
       "Mean duration of one repair", "How long does a repair take?",
       "repair time / repairs", "repair time", related=("mmt",)),
    _m("mmts", "Mean preventive maintenance time", "MMTs",
       "Mean duration of one planned maintenance", "How long does PM take?",
       "PM time / PM events", "PM time", related=("mmt",)),
    _m("green_percent", "Green time", "G",
       "Share of time fully working", "How much of the time is it fully up?",
       "Green time / total time", "total time", units="%"),
    _m("yellow_percent", "Yellow time", "Y",
       "Share of time degraded but working", "How much of the time is it degraded?",
       "Yellow time / total time", "total time", units="%"),
    _m("red_percent", "Red time", "R",
       "Share of time fully down", "How much of the time is it down?",
       "Red time / total time", "total time", units="%"),
    _m("mttf", "Time to failure", "MTTF",
       "Expected failure time of a non-repairable element", "When will one part fail?",
       "mean of its failure law", "element age"),
    _m("mttr", "Time to repair", "MTTR",
       "Expected hands-on repair time of an element", "How long is one repair?",
       "mean of its repair law", "repair"),
    _m("fci", "Failure criticality index", "FCI",
       "Share of failure-started outages an element initiated", "What triggers failures?",
       "failure outages it started / all failure outages", "attribution replay",
       related=("deci", "dtci")),
    _m("deci", "Downing event criticality index", "DECI",
       "Share of system outages an element initiated", "What triggers outages?",
       "outages it started / all outages", "attribution replay",
       related=("fci", "dtci")),
    _m("dtci", "Downtime criticality index", "DTCI",
       "Share of system Red time in outages an element initiated",
       "What causes the downtime?", "its outages' Red time / all Red time",
       "attribution replay", related=("deci", "fci"),
       beware="Describes this design; not what a change would achieve."),
    _m("rrw", "Risk reduction worth", "RRW",
       "How much unavailability would fall if an element never failed",
       "How much better if this were perfect?", "baseline u / perfect u",
       "importance re-simulation", related=("raw", "birnbaum"),
       beware="Undefined when the baseline never fails."),
    _m("raw", "Risk achievement worth", "RAW",
       "How much unavailability would rise if an element were lost",
       "How much worse if this were lost?", "failed u / baseline u",
       "importance re-simulation", related=("rrw", "birnbaum")),
    _m("birnbaum", "Birnbaum importance", "BI",
       "Movement in the system metric when an element is perfect versus failed",
       "How sensitive is the system to this element?",
       "perfect metric - failed metric", "importance re-simulation",
       related=("rrw", "raw"), beware="Saturates in a series model."),
    _m("vesely_fussell", "Vesely-Fussell importance", "VF",
       "Share of unavailability an element is responsible for",
       "How much of the downtime is its fault?",
       "(perfect metric - baseline) / baseline unavailability",
       "importance re-simulation", related=("rrw", "dtci")),
    _m("spares_used", "Spares used", "S",
       "Spares drawn from stock over the run", "How many spares did it consume?",
       "sum of spare arrivals from stock", "one run", units="count"),
    _m("capacity_delivered", "Delivered flow", "F",
       "Mean flow delivered over the run", "How much product arrived?",
       "time-weighted delivered flow", "capacity sink", units="flow"),
    _m("operational_availability", "Operational availability", "Ao",
       "Availability including every real delay", "What did the plant actually achieve?",
       "uptime / total time", "total simulated time",
       related=("inherent_availability", "achieved_availability")),
    _m("lost_flow", "Lost flow", "LF",
       "Mean demand not delivered", "How much flow was lost?",
       "flow generated - mean flow", "capacity sink", units="flow"),
    _m("lost_capacity", "Lost capacity per element", "LC",
       "Lost energy charged to the elements that were down",
       "Who loses the capacity?", "integral of lost flow charged by the chosen rule",
       "attribution replay", units="flow x time",
       related=("lost_flow", "dtci")),
    _m("cost_total", "Grand total", "C",
       "Total cost of the run", "What does it cost?",
       "sum of all configured cost terms", "one run", units="cost"),
)


BY_ID: dict[str, Measure] = {measure.id: measure for measure in MEASURES}
#: `metric_definitions` key -> measure id.  A definition without a measure
#: fails `tests/unit/insight/test_measures.py`.
METRIC_TO_MEASURE: dict[str, str] = {
    "availability": "availability",
    "reliability": "reliability",
    "conditional_reliability": "conditional_reliability",
    "dependability": "dependability",
    "mtbde": "mtbde",
    "mdt": "mdt",
    "mtbm": "mtbm",
    "mtbmu": "mtbmu",
    "mtbms": "mtbms",
    "mmt": "mmt",
    "mmtu": "mmtu",
    "mmts": "mmts",
    "mttff": "mttf",
    "mtbf_total": "mttf",
    "mtbe_total": "mtbde",
    "num_system_failures": "deci",
    "num_component_failures": "fci",
    "num_component_cms": "mmtu",
    "num_component_pms": "mmts",
    "inherent_availability": "inherent_availability",
    "achieved_availability": "achieved_availability",
    "operational_availability": "operational_availability",
    "green_percent": "green_percent",
    "yellow_percent": "yellow_percent",
    "red_percent": "red_percent",
    "units_produced": "capacity_delivered",
    "mean_flow": "capacity_delivered",
    "mean_capacity": "capacity_delivered",
    "capacity_weighted_availability": "capacity_delivered",
    "lost_flow": "lost_flow",
    "grand_total": "cost_total",
    "system_down_cost": "cost_total",
    "system_lost_flow_cost": "cost_total",
}


def get(measure_id: str) -> Measure:
    try:
        return BY_ID[measure_id]
    except KeyError:
        raise ValueError(
            f"unknown measure {measure_id!r}; known ids: "
            f"{', '.join(sorted(BY_ID))}") from None


def for_metric(metric_key: str) -> Measure | None:
    return BY_ID.get(METRIC_TO_MEASURE.get(metric_key, metric_key))


def all_measures() -> list[Measure]:
    return list(MEASURES)


def missing_metric_measures() -> list[str]:
    """Metric-definition keys with no measure (the test asserts this is [])."""
    return [row["key"] for row in _METRICS if row["key"] not in METRIC_TO_MEASURE]


def availability_ladder(per_run: list[dict]) -> dict[str, Any]:
    """The three availabilities and the rungs of the downtime ladder.

    `per_run` are `RunReportData`-shaped dicts (from `_rd_to_dict`).  The
    rungs come from the engine's own Red-time split, so they add to the
    reported downtime (tested).
    """
    def mean(key: str) -> float | None:
        values = [float(row[key]) for row in per_run
                  if isinstance(row.get(key), (int, float))]
        return sum(values) / len(values) if values else None

    total = mean("total_time") or 0.0
    down = mean("downtime") or 0.0
    repair = mean("red_corrective_time") or 0.0
    pm = mean("red_pm_time") or 0.0
    logistics = mean("red_logistics_time") or 0.0
    other = mean("red_other_time") or 0.0
    done = mean("red_done_time") or 0.0
    def field_mean(key: str) -> float | None:
        values = [float(row[key]) for row in per_run
                  if isinstance(row.get(key), (int, float))]
        return sum(values) / len(values) if values else None

    return {
        "inherent": {"value": field_mean("inherent_availability"),
                     "source": "from the block data / simulation",
                     "counts": ["repair"]},
        "achieved": {"value": field_mean("achieved_availability"),
                     "source": "from the simulation",
                     "counts": ["repair", "PM"]},
        "operational": {"value": field_mean("operational_availability"),
                        "source": "from the simulation",
                        "counts": ["repair", "PM", "logistics", "spares",
                                   "crew", "dependency"]},
        "simulated_availability": mean("availability"),
        "rungs": {
            "repair": repair, "pm": pm, "logistics": logistics,
            "not_repairable": done, "other": other,
        },
        "time_down": down,
        "rungs_sum": repair + pm + logistics + done + other,
        "balanced": abs((repair + pm + logistics + done + other) - down)
                    <= max(1e-9, 1e-9 * down),
        "units_total": total,
    }


def explain_lines(measure_id: str) -> list[str]:
    """One measure as the text the explainer panel and MCP show."""
    measure = get(measure_id)
    if measure is None:
        return []
    lines = [f"{measure.name} ({measure.short})", "", measure.long + ".",
             f"Answers: {measure.answers}",
             f"Formula: {measure.formula}",
             f"Clock: {measure.clock}"]
    if measure.counts:
        lines.append("Counts: " + ", ".join(measure.counts) + ".")
    if measure.excludes:
        lines.append("Leaves out: " + ", ".join(measure.excludes) + ".")
    if measure.also_called:
        lines.append("Other tools: " + ", ".join(
            f"{tool} calls it {name}" for tool, name in measure.also_called.items()) + ".")
    if measure.use_when:
        lines.append("Use it when: " + measure.use_when)
    if measure.beware:
        lines.append("Beware: " + measure.beware)
    if measure.related:
        lines.append("Related: " + ", ".join(measure.related) + ".")
    return lines

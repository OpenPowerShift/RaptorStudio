"""How results read: labels, number formats and run information.

One module so the Results dock, the spreadsheet, the PDF report and Print
say the same thing the same way. The engine's reports keep Raptor 7's
tokens (``Total_Cost``, ``Green_Percent``, ``AoStdDev``); a person reads
"Total cost", "Green %", "St. dev.". Values keep full precision wherever
they are stored (spreadsheet cells, Copy Table); only what is shown is
rounded, by the kind of quantity:

==========  ===================================  ==================
kind        what                                 shown as
==========  ===================================  ==================
``prob``    availability, reliability (0-1)      6 decimal places
``pct``     a percentage of time                 2 decimal places
``time``    a duration in the model's units      5 significant figures
``cost``    money                                2 decimal places
``count``   failures, spares, delays             whole, or 4 s.f.
``num``     anything else                        5 significant figures
==========  ===================================  ==================

Thousands are separated with commas. Raptor 7's own markers survive: a
``>`` (a censored estimate, at least this large) and ``N/A``.

Nothing here imports Qt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from math import floor, isfinite, log10
from typing import Any

# --------------------------------------------------------------------------
# labels
# --------------------------------------------------------------------------

#: Raptor 7 report tokens and file column names, as a person reads them.
#: Anything not listed goes through `humanise`.
LABELS: dict[str, str] = {
    # Summary / logistics measures
    "Total_Cost": "Total cost",
    "Conditional_Reliability": "Conditional reliability",
    "System_Failures": "System failures",
    "Component_Failures": "Component failures",
    "EndingSimTime": "Ending sim time",
    "Green_Percent": "Green %", "Yellow_Percent": "Yellow %",
    "Red_Percent": "Red %", "Blue_Percent": "Blue %",
    "Brown_Percent": "Brown %", "Orange_Percent": "Orange %",
    "Mission_Green_Percent": "Mission green %",
    "Mission_Yellow_Percent": "Mission yellow %",
    "Mission_Red_Percent": "Mission red %",
    # statistics columns
    "Minimum": "Minimum", "Maximum": "Maximum", "Mean": "Mean",
    "St.Dev": "St. dev.", "StDev": "St. dev.", "SEM": "SEM",
    # sparing
    "blockName": "Block", "SpareSource": "Spare source",
    "MinUsed": "Min used", "AveUsed": "Avg used", "MaxUsed": "Max used",
    "poolName": "Pool", "PoolName": "Pool", "InitNumSpares": "Initial",
    "NewSpares": "New spares", "MinStock": "Min stock",
    "AveStock": "Avg stock", "MaxStock": "Max stock",
    "EndStock": "End stock", "NumDelays": "Delays",
    # resources
    "resourceName": "Resource", "InitNumRes": "Initial",
    "MinNumRes": "Min available", "AveNumRes": "Avg available",
    "MaxNumRes": "Max available", "EndNumRes": "End available",
    # cost
    "InHier": "Level", "RepHold": "Repair hold", "PMHold": "PM hold",
    "InitSpares": "Initial spares", "EmerShip": "Emergency shipping",
    "HierNum": "ID", "SparePools": "Spare pool", "SystemDownCost":
    "System down cost", "Grand_Total": "Grand total",
    # capacity
    "MinFlow": "Min flow", "AveFlow": "Avg flow", "MaxFlow": "Max flow",
    "UsageRate": "Usage rate", "MinCapacity": "Min capacity",
    "AveCapacity": "Avg capacity", "MaxCapacity": "Max capacity",
    "CapableRate": "Capable rate", "SystemCapacity": "System",
    "FlowGenerated": "Flow generated", "UnitsProduced": "Units produced",
    "MinLostFlow": "Min lost flow", "AveLostFlow": "Avg lost flow",
    "MaxLostFlow": "Max lost flow",
    # model data (spare pools, resources, triggers, phases)
    "RSO": "Routine ordering", "Stock": "Initial stock",
    "NewSp": "Spares per order", "ArriveEvery": "Arrives every",
    "ESO": "Emergency ordering", "EmerTime": "Emergency time",
    "SLO": "Stock-level ordering", "Level": "Reorder level",
    "Quan": "Order quantity", "SLOTime": "Stock-level time",
    "SpCost": "Spare cost", "emerShipCost": "Emergency shipping cost",
    "NumIn": "Units", "SpareCost": "Cost per unit",
    "FixedPerUse": "Cost per use", "PerTimeUnit": "Cost per unit time",
    "TriggerName": "Trigger", "IDnum": "ID", "InitUsed": "Initially used",
    "Dist": "Distribution", "PhaseName": "Phase", "Num": "Number",
    "Param1": "Parameter 1", "Param2": "Parameter 2",
    "Param3": "Parameter 3",
}

#: Weak Link column prefixes (`AoMean`, `DoSEM`, `RStdDev`): the table
#: title already says which measure, so the column is just the statistic.
_STAT_SUFFIX = {"Min": "Minimum", "Mean": "Mean", "Max": "Maximum",
                "StdDev": "St. dev.", "SEM": "SEM"}
_WEAK_PREFIX = re.compile(r"^(Ao|Do|R)(Min|Mean|Max|StdDev|SEM)$")
_RUNS_NOTE = re.compile(r"^(.*?)\((\d+)-runs\)$")
_ACRONYM = re.compile(r"^[A-Z]{2,}[a-z]?$")


def humanise(token: str) -> str:
    """A report token or column name as words: ``Life_cycle_cost`` ->
    "Life cycle cost", ``ArriveEvery`` -> "Arrive every". Acronyms
    (``MTBDE``, ``MTBMu``) are kept as they are."""
    token = str(token).strip()
    if not token:
        return ""
    if token in LABELS:
        return LABELS[token]
    m = _RUNS_NOTE.match(token)
    if m:                                   # MDT(18-runs)
        return f"{humanise(m.group(1))} ({m.group(2)} runs)"
    m = _WEAK_PREFIX.match(token)
    if m:
        return _STAT_SUFFIX[m.group(2)]
    if token.startswith("%"):               # %Green
        return f"{humanise(token[1:])} %"
    if _ACRONYM.match(token):
        return token
    words = _words(token)
    if not words:
        return token
    return " ".join(
        w if _ACRONYM.match(w) or (w.isupper() and len(w) > 1)
        else (w.capitalize() if i == 0 else w.lower())
        for i, w in enumerate(words))


def _words(token: str) -> list[str]:
    """``ArriveEvery`` -> [Arrive, Every]; ``SLOTime`` -> [SLO, Time]."""
    words: list[str] = []
    for part in token.replace("-", "_").split("_"):
        words += re.findall(
            r"[A-Z]{2,}(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+|[A-Z]+|\d+", part)
    return words


def with_unit(label: str, kind: str, units: str = "") -> str:
    """``MTBDE`` -> "MTBDE (hours)" for a time measure."""
    if kind != "time" or not units or units in label:
        return label
    m = re.match(r"^(.*?) \((\d+ runs)\)$", label)
    if m:                                   # "MDT (39 runs)"
        return f"{m.group(1)} ({units}, {m.group(2)})"
    return f"{label} ({units})"


# --------------------------------------------------------------------------
# kinds
# --------------------------------------------------------------------------

_TIME = {"mtbde", "mdt", "mtbm", "mtbmu", "mtbms", "mmt", "mmtu", "mmts",
         "mtbf", "mttr", "mttf", "ending sim time", "time"}
_PROB = {"availability", "reliability", "conditional reliability",
         "dependability", "ao", "do", "r"}
_COUNT = {"system failures", "component failures", "failures", "delays",
          "min used", "avg used", "max used", "initial", "new spares",
          "min stock", "avg stock", "max stock", "end stock", "run", "id",
          "level", "initial spares", "min available", "avg available",
          "max available", "end available"}
_COST_WORDS = ("cost", "purchase", "operating", "sparing", "disposal",
               "total", "good", "repair", "idle", "repair hold", "pm",
               "pm hold", "standby", "done", "events", "emergency shipping",
               "value", "grand total", "spare pool")


def kind_of(label: str, *, table: str = "") -> str:
    """The kind of a measure (a row label) or a column, for formatting. A
    *table* title decides the ambiguous ones: "Repair" is a cost in a cost
    table and a time elsewhere; "Mean" is a probability in a Weak Link
    availability table and a percentage in a states table."""
    raw = re.sub(r"\s*\(.*\)\s*$", "", str(label)).strip()
    raw = _RUNS_NOTE.sub(r"\1", raw).strip()
    low = raw.lower()
    if low.endswith("%") or low.startswith("%") or "percent" in low:
        return "pct"
    # Already a display label ("St. dev.", "Avg used"): do not re-split it.
    base = (raw if raw in _DISPLAY_LABELS else humanise(raw)).lower()
    t = table.lower()
    if base in _PROB:
        return "prob"
    if base in _TIME:
        return "time"
    if base in _STATS:
        if "states" in t:
            return "pct"
        if any(w in t for w in ("availability", "dependability",
                                "reliability")):
            return "prob"
    if "cost" in t:
        return "cost" if base in _COST_WORDS or "cost" in base else "num"
    if base in _COUNT:
        return "count"
    if "cost" in base:
        return "cost"
    return "num"


_STATS = {"minimum", "mean", "maximum", "st. dev.", "sem"}
_DISPLAY_LABELS = set(LABELS.values()) | set(_STAT_SUFFIX.values())


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------

def _sig(value: float, digits: int) -> str:
    if value == 0:
        return "0"
    exponent = floor(log10(abs(value)))
    decimals = max(0, digits - 1 - exponent)
    return f"{value:,.{decimals}f}"


def to_number(value: Any) -> float | None:
    """A cell as a float, or None if it is not a number (``N/A``, text).
    ``1,234.5`` and ``>21.2`` (censored) both read as numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if isfinite(value) else None
    text = str(value).strip().lstrip(">").replace(",", "").rstrip("%")
    try:
        f = float(text)
    except ValueError:
        return None
    return f if isfinite(f) else None


def fmt(value: Any, kind: str = "num") -> str:
    """Display text for one value of *kind* (see the module docstring)."""
    if value is None:
        return "N/A"
    censored = isinstance(value, str) and value.strip().startswith(">")
    num = to_number(value)
    if num is None:
        text = str(value).strip()
        return "N/A" if text.upper() in ("", "N/A", "NA", "NAN") else text
    if kind == "prob":
        out = f"{num:.6f}"
    elif kind == "pct":
        out = f"{num:,.2f}"
    elif kind == "cost":
        out = f"{num:,.2f}"
    elif kind == "count":
        # A whole count stays whole; an average of counts (written with a
        # decimal point, "3.00") keeps two places, so a column of averages
        # lines up.
        whole = num == int(num) and not (isinstance(value, str)
                                         and "." in value)
        out = f"{int(num):,}" if whole else f"{num:,.2f}"
    else:
        out = _sig(num, 5)
        if "." in out:
            out = out.rstrip("0").rstrip(".")
    return ">" + out if censored else out


# --------------------------------------------------------------------------
# run information
# --------------------------------------------------------------------------

_TERMINATION = {"TimeSim": "time", "FailSim": "failures",
                "CycleSim": "cycles"}


@dataclass
class RunInfo:
    """What a reader needs to trust a page of results."""
    model: str = ""
    file: str = ""
    runs: int = 0
    termination: str = ""          # "8,760 hours per run"
    stats_start: str = ""          # "" when statistics start at 0
    units: str = ""
    analyses: list[str] = field(default_factory=list)
    seed: str = ""
    result_set: str = ""           # model hash and run digest
    run_at: str = ""
    software: str = ""

    def rows(self) -> list[tuple[str, str]]:
        rows = [("Model", self.model), ("File", self.file),
                ("Runs", f"{self.runs:,}" if self.runs else ""),
                ("Each run", self.termination),
                ("Statistics from", self.stats_start),
                ("Time units", self.units),
                ("Analyses", ", ".join(self.analyses)),
                ("Random numbers", self.seed),
                ("Result set", self.result_set),
                ("Run on", self.run_at), ("Software", self.software)]
        return [(k, v) for k, v in rows if v]


def _system_row(rbd: Any) -> dict:
    sec = getattr(rbd, "sections", {}).get("SYSTEM_DATA") if rbd else None
    return dict(sec.rows[0]) if sec is not None and sec.rows else {}


def run_info(rbd: Any, *, model: str = "", file: str = "", runs: int = 0,
             units: str = "", run_at: datetime | None = None,
             manifest: dict | None = None) -> RunInfo:
    """Run information from the model's system settings and the run's
    manifest (`raptor.api.run`'s, when there is one)."""
    from raptor import __version__

    row = _system_row(rbd)
    units = units or str(row.get("units", "")).strip()
    term = str(row.get("TerminationType", "")).strip()
    cond = to_number(row.get("TermCondition"))
    if term == "TimeSim" and cond is not None:
        termination = f"{fmt(cond, 'num')} {units}".strip()
    elif term in _TERMINATION and cond is not None:
        termination = f"until {fmt(cond, 'count')} {_TERMINATION[term]}"
    else:
        termination = term
    start = to_number(row.get("StartStats"))
    info = RunInfo(
        model=model, file=file,
        runs=runs or int(to_number(row.get("NumRuns")) or 0),
        termination=termination,
        stats_start=(f"{fmt(start, 'num')} {units}".strip()
                     if start else ""),
        units=units,
        run_at=(run_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
        software=f"RAPTOR Studio {__version__}")
    m = manifest or {}
    if m.get("trials") and not runs:
        info.runs = int(m["trials"])
    if m.get("analyses"):
        info.analyses = [humanise(a) for a in m["analyses"]]
    if m.get("seed_strategy"):
        info.seed = str(m["seed_strategy"])
    if m.get("model_hash"):
        info.result_set = f"model {m['model_hash']}" + (
            f", run {m['digest']}" if m.get("digest") else "")
    return info


# --------------------------------------------------------------------------
# result tables: their section and title
# --------------------------------------------------------------------------

#: Final Results sections in reading order, as a person names them.
SECTIONS: dict[str, str] = {
    "SUMMARY": "Summary", "LOGISTICS": "Logistics", "SPARING": "Sparing",
    "RESOURCES": "Resources", "COST": "Cost", "CAPACITY": "Capacity",
    "WEAK_LINK_ANALYSIS": "Weak link",
}

#: A sub-table's own name (the label line above it in the report).
_TABLE_NAMES: dict[str, str] = {
    "Spares_Utilization": "Spare use",
    "SparePool_Sparing_Information": "Spare pools",
    "Sparepool_Sparing_Information": "Spare pools",
    "Custom_Sparing": "Custom sparing",
    "Resource_Information": "Resources",
    "Operating": "Operating cost", "Sparing": "Sparing cost",
    "Life_cycle_cost": "Life-cycle cost", "Event": "Event cost",
    "Hierarchy": "Hierarchy cost", "SparePools": "Spare pool cost",
    "Sparepools": "Spare pool cost", "Resources": "Resource cost",
    "Cost_Summary": "Cost summary", "Grand_Total": "Grand total",
    "Totals": "Flow", "FlowLoss": "Lost flow",
    "SystemCapacity": "System capacity", "NodeCapacity": "Node capacity",
    "Availability_Data": "availability", "Dependability_Data":
    "dependability", "Reliability_Data": "reliability",
    "State_Data": "states",
}
_PARTS = {"NODE_ANALYSIS": "Node", "BLOCK_ANALYSIS": "Block",
          "EVENT_ANALYSIS": "Event", "HIERARCHY_ANALYSIS": "Hierarchy"}
_SECTION_DEFAULT = {"SPARING": "Spare use", "RESOURCES": "Resources"}


def section_of(raw_title: str) -> str:
    """The section a Final Results table belongs to: "COST - Operating" ->
    "Cost"."""
    head = raw_title.split(" - ", 1)[0].strip()
    return SECTIONS.get(head, humanise(head))


def table_title(raw_title: str) -> str:
    """A Final Results table's name: "COST - Life_cycle_cost" ->
    "Life-cycle cost", "WEAK_LINK_ANALYSIS - BLOCK_ANALYSIS State_Data" ->
    "Block states"."""
    head, _, rest = raw_title.partition(" - ")
    head = head.strip()
    if not rest:
        return _SECTION_DEFAULT.get(head, SECTIONS.get(head, humanise(head)))
    words = rest.split()
    part = _PARTS.get(words[0]) if len(words) > 1 else None
    name = words[-1]
    text = _TABLE_NAMES.get(name, humanise(name))
    if part:
        return f"{part} {text}"
    return text[0].upper() + text[1:]


def table_kinds(table: Any) -> tuple[list[str], list[str]]:
    """``(column kinds, row kinds)`` for a `raptor.api.tables.Table`.

    A table of measures (first header "Measure" or "Item", or a row-label
    column with no name) has one kind per row: Availability is a
    probability and MTBDE a time in the same column. Any other table has
    one kind per column, from the header and the table's title."""
    headers = list(table.headers)
    title = f"{getattr(table, 'section', '')} {table.title}"
    first = headers[0] if headers else ""
    if first in ("Measure", "Item") or (first == "" and len(headers) > 1):
        return [""] * len(headers), [kind_of(str(r[0]), table=title)
                                     for r in table.rows]
    return [kind_of(h, table=title) for h in headers], []


def display_rows(table: Any) -> list[list[str]]:
    """The table's rows as display text (see `fmt`); text columns and the
    row-label column are left as they are."""
    cols, rows_k = table_kinds(table)
    text_cols: set[int] = getattr(table, "text_cols", set())
    out = []
    for n, row in enumerate(table.rows):
        cells = []
        for i, v in enumerate(row):
            if i == 0 or i in text_cols or to_number(v) is None:
                cells.append("" if v is None else str(v))
            else:
                kind = rows_k[n] if rows_k else (cols[i] if i < len(cols)
                                                 else "num")
                cells.append(fmt(v, kind))
        out.append(cells)
    return out

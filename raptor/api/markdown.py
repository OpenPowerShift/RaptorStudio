"""Markdown presentation layer for structured results.

Turns structured results into Markdown: summary_table, element_table,
report_markdown, comparison_table, sweep_table, diff_markdown.  Honours
``RbdFile.display_unit`` and puts units in headers; rounds for humans
(4 significant figures for probabilities, 2 decimals for times and
costs) while the structured JSON keeps full precision.  Legacy reports
carry their *meaning*: the censored ``>`` marker, ``N/A`` and the
``MDT(18-runs)`` sample counts all survive as table cells or footnotes.
"""
from __future__ import annotations

import re
from math import floor, log10
from typing import Any

from raptor.api.results import Metric

# The canonical hours-per-unit table lives in the GUI's unit-conversion
# helper, which has no Qt imports.  Import it lazily so this module can
# never drag Qt into a headless MCP process; fall back to a no-op when
# the GUI package is unavailable.
try:  # pragma: no cover - exercised via the fallback only off-tree
    from raptor.gui.ui.units_conv import (
        native_to_display as _native_to_display,
    )
except Exception:  # pragma: no cover
    def _native_to_display(
        value: float, native_unit: str | None, display_unit: str | None,
    ) -> float:
        return value


# ---------------------------------------------------------------------------
# Metric classification / formatting
# ---------------------------------------------------------------------------

_PROBABILITY_METRICS = frozenset({
    "availability", "reliability", "conditional_reliability",
    "dependability", "green_percent", "yellow_percent", "red_percent",
    "mission_green_percent", "mission_yellow_percent",
    "mission_red_percent",
})

_TIME_METRICS = frozenset({
    "mtbde", "mdt", "mtbm", "mtbmu", "mtbms", "mmt", "mmtu", "mmts",
    "mtbf", "mttr", "mttf",
})

_COST_METRICS = frozenset({
    "total_cost", "cost", "operating_cost", "sparing_cost",
    "purchase_cost", "disposal_cost", "life_cycle_cost",
    "system_down_cost", "grand_total", "operating", "sparing",
    "purchase", "disposal", "rephold", "idle", "pm", "pmhold",
    "standby", "done", "emergship", "newspares", "initspares",
})

_FLOW_METRICS = frozenset({
    "units_produced", "mean_flow", "mean_capacity", "lost_flow",
})

_COUNT_METRICS = frozenset({
    "system_failures", "component_failures", "num_system_failures",
    "num_component_failures", "numdelays", "failures",
})


def _metric_key(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _metric_kind(name: str) -> str:
    """One of ``prob``, ``time``, ``cost``, ``count`` or ``flow``."""
    key = _metric_key(name)
    if key in _TIME_METRICS:
        return "time"
    if key in _COST_METRICS:
        return "cost"
    if key in _COUNT_METRICS:
        return "count"
    if key in _FLOW_METRICS:
        return "flow"
    if key in _PROBABILITY_METRICS:
        return "prob"
    if key.endswith("_percent") or "availability" in key \
            or "reliability" in key:
        return "prob"
    # Unclassified metrics read best at probability precision.
    return "prob"


def _sig_figs(value: float, digits: int = 4) -> str:
    """Format *value* with *digits* significant figures (no exponent)."""
    if value == 0:
        return "0"
    exponent = floor(log10(abs(value)))
    decimals = max(0, digits - 1 - exponent)
    return f"{value:.{decimals}f}"


def _fmt_value(value: Any, kind: str) -> str:
    """Human-readable cell text; preserves ``>`` censored markers and
    renders ``None`` as ``N/A``."""
    if value is None:
        return "N/A"
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.upper() in ("N/A", "NA", "NAN"):
            return "N/A"
        if text.startswith(">"):
            return text  # censored: at least this value
        try:
            value = float(text)
        except ValueError:
            return text
    if not isinstance(value, (int, float)):
        return str(value)
    if kind == "time":
        return f"{value:.2f}"
    if kind in ("cost", "flow"):
        return f"{value:.2f}"
    if kind == "count":
        return f"{value:g}"
    return _sig_figs(value, 4)


def _metric_label(
    name: str,
    kind: str,
    display_unit: str | None,
    native_unit: str | None,
    cost_unit: str | None,
) -> str:
    """Append a unit to a metric name when one is known."""
    if kind == "time":
        unit = display_unit or native_unit or "hours"
        return f"{name} ({unit})"
    if kind == "cost" and cost_unit:
        return f"{name} ({cost_unit})"
    return name


def _extract_metrics(
    run_result: Any,
) -> tuple[list[Any] | None, dict[str, Any]]:
    """Normalise a RunResult or a ``to_dict()`` dict into a metric list
    plus any unit metadata carried alongside it."""
    meta: dict[str, Any] = {}
    unit_keys = ("display_unit", "native_unit", "units", "cost_unit")

    if isinstance(run_result, dict):
        for key in unit_keys:
            if run_result.get(key) is not None:
                meta[key] = run_result[key]
        metrics_data = run_result.get("metrics")
    elif hasattr(run_result, "metrics"):
        for key in unit_keys:
            value = getattr(run_result, key, None)
            if value is not None:
                meta[key] = value
        metrics_data = run_result.metrics
    else:
        return None, meta

    if metrics_data is None:
        return [], meta
    if isinstance(metrics_data, dict):
        out = []
        for name, stats in metrics_data.items():
            if isinstance(stats, dict):
                fields = {k: v for k, v in stats.items() if k != "name"}
                out.append(Metric(name=name, **fields))
            else:
                out.append(Metric(name=name))
        return out, meta
    if isinstance(metrics_data, list):
        return metrics_data, meta
    return [], meta


def _metric_dict(metric: Any) -> dict[str, Any]:
    if hasattr(metric, "to_dict"):
        return metric.to_dict()
    if isinstance(metric, dict):
        return metric
    return {
        "name": getattr(metric, "name", ""),
        "min": getattr(metric, "min", None),
        "mean": getattr(metric, "mean", None),
        "max": getattr(metric, "max", None),
        "stdev": getattr(metric, "stdev", None),
        "sem": getattr(metric, "sem", None),
    }


def _notes_line(notes: list[str]) -> list[str]:
    if not notes:
        return []
    return ["", "_Notes: " + " ".join(notes) + "_"]


# ---------------------------------------------------------------------------
# summary_table
# ---------------------------------------------------------------------------

def summary_table(
    run_result: Any,
    display_unit: str | None = None,
    native_unit: str | None = None,
    cost_unit: str | None = None,
) -> str:
    """Convert a RunResult (or ``to_dict()`` dict) into a Markdown
    summary table.

    Units go in the metric labels; ``display_unit`` (explicitly, or from
    the result) converts and relabels time metrics.  Probabilities are
    shown to 4 significant figures, times and costs to 2 decimals, and
    the full-precision values remain in the structured JSON.
    """
    metrics, meta = _extract_metrics(run_result)
    if metrics is None:
        return ""

    display_unit = display_unit or meta.get("display_unit")
    native_unit = native_unit or meta.get("native_unit") \
        or meta.get("units") or "hours"
    cost_unit = cost_unit or meta.get("cost_unit")

    lines = [
        "| Metric | Min | Mean | Max | St.Dev | SEM |",
        "|---|---|---|---|---|---|",
    ]
    has_censored = False
    has_na = False

    for metric in metrics:
        data = _metric_dict(metric)
        name = str(data.get("name", ""))
        kind = _metric_kind(name)
        label = _metric_label(
            name, kind, display_unit, native_unit, cost_unit,
        )
        cells = []
        for key in ("min", "mean", "max", "stdev", "sem"):
            value = data.get(key)
            if kind == "time" and isinstance(value, (int, float)):
                value = _native_to_display(
                    value, native_unit, display_unit,
                )
            if isinstance(value, str) and value.strip().startswith(">"):
                has_censored = True
            if value is None or (
                isinstance(value, str) and value.strip().upper()
                in ("N/A", "NA")
            ):
                has_na = True
            cells.append(_fmt_value(value, kind))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    notes: list[str] = []
    if has_censored:
        notes.append(
            "`>` means at least / censored: the true value is greater "
            "than the printed threshold."
        )
    if has_na:
        notes.append(
            "`N/A` means not available (the metric was not computed for "
            "this run set)."
        )
    lines.extend(_notes_line(notes))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# element_table
# ---------------------------------------------------------------------------

#: Ranking keys that are "worse when larger" (descending default).
_DESCENDING_KEYS = (
    "impact", "unavailability", "contribution", "downtime", "risk",
    "failures", "fail_count", "num_failures", "cost", "mdt", "mttr",
    "spares", "utilization",
)

#: Ranking keys that are "worse when smaller" (ascending default).
_ASCENDING_KEYS = (
    "availability", "reliability", "ao", "percent", "min", "min_",
)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value.strip())
            return True
        except ValueError:
            return False
    return False


def _pick_rank_column(rows: list[dict]) -> str | None:
    """Pick the most likely "worst" column from the row shape."""
    if not rows:
        return None
    keys = list(rows[0].keys())
    numeric = [k for k in keys if any(_is_number(r.get(k)) for r in rows)]
    if not numeric:
        return None
    for token in _DESCENDING_KEYS + _ASCENDING_KEYS:
        for key in numeric:
            if token in key.lower():
                return key
    return numeric[0]


def _default_reverse(key: str) -> bool:
    lowered = key.lower()
    for token in _ASCENDING_KEYS:
        if token in lowered:
            return False
    for token in _DESCENDING_KEYS:
        if token in lowered:
            return True
    return True


def element_table(
    rows: list[dict],
    sort_by: str | None = None,
    reverse: bool | None = None,
) -> str:
    """Render element rows (from SPF, describe or weak-link) as Markdown.

    Sorted **worst-first** by default: when *sort_by* is omitted the
    most likely ranking column is chosen from the row shape (falling
    back to input order when no numeric column exists).  Pass *reverse*
    to override the direction.
    """
    if not rows:
        return "*No elements.*"

    rows = [dict(row) for row in rows]
    key = sort_by or _pick_rank_column(rows)

    if key is not None and key in rows[0]:
        if reverse is None:
            reverse = _default_reverse(key)

        def _sort_key(row: dict) -> Any:
            value = row.get(key)
            return value if isinstance(value, (int, float)) else str(value)

        try:
            rows = sorted(rows, key=_sort_key, reverse=reverse)
        except TypeError:
            pass

    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append(
            "| " + " | ".join(str(row.get(h, "")) for h in headers) + " |"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# report_markdown — legacy fixed-width report text
# ---------------------------------------------------------------------------

_BANNER_RE = re.compile(r"^\*{3,}\s*(.*?)\s*\*{3,}$")
_UNDERSCORE_RE = re.compile(r"^[_\s]+$")
_TOTAL_RUNS_RE = re.compile(r"Results from\s+(\d+)\s+runs", re.IGNORECASE)
_SAMPLE_COUNT_RE = re.compile(r"^(.*?)\((\d+)-runs\)\s*$")
_KV_RE = re.compile(
    r"^([A-Za-z][A-Za-z_ ]*?)\s{2,}([-+]?\d[0-9.,eE+-]*|N/A)\s*$"
)

_CENSORED_NOTE = (
    "`>` means at least / censored: the true value is greater than the "
    "printed threshold."
)
_NA_NOTE = (
    "`N/A` means not available (the metric was not computed for this run "
    "set)."
)


def _heading(text: str) -> str:
    return " ".join(text.replace("_", " ").split()).title()


def _is_header(line: str) -> bool:
    tokens = line.split()
    if not tokens:
        return False
    first = tokens[0]
    if first == "Minimum":
        return True
    if first in ("blockName", "Block", "SimulationTime", "Node"):
        return len(tokens) >= 2 and not _is_number(tokens[1])
    return False


def _header_columns(
    line: str,
) -> tuple[list[str], list[int], list[int], str]:
    """Return ``(names, starts, ends, align)`` for a header line.

    ``align`` is ``"left"`` when the numeric columns start at the header
    start positions (the ``Minimum`` summary/logistics tables) and
    ``"right"`` when they end at the header end positions (element/cost
    tables).
    """
    matches = list(re.finditer(r"\S+", line))
    names = [m.group() for m in matches]
    starts = [m.start() for m in matches]
    ends = [m.end() for m in matches]
    if names and names[0] == "Minimum":
        names = ["Metric"] + names
        starts = [0] + starts
        ends = [0] + ends
        return names, starts, ends, "left"
    return names, starts, ends, "right"


def _slice_row(
    line: str,
    starts: list[int],
    ends: list[int],
    align: str,
) -> list[str]:
    """Assign whitespace-separated tokens to columns by position.

    Legacy reports use two field layouts: the summary/logistics tables
    left-align their numbers on the header starts, while the
    element/cost tables right-align them on the header ends.  A column
    that receives several tokens (a name containing spaces) joins them.
    """
    column_count = len(starts)
    if column_count == 0:
        return []
    if column_count == 1:
        return [line.strip()]

    cells = [""] * column_count
    bounds: list[float]
    if align == "left":
        bounds = starts[1:] + [float("inf")]
        for match in re.finditer(r"\S+", line):
            token_start = match.start()
            column = column_count - 1
            for i in range(column_count):
                if starts[i] <= token_start < bounds[i]:
                    column = i
                    break
            token = match.group()
            cells[column] = token if not cells[column] \
                else f"{cells[column]} {token}"
    else:
        bounds = [float(b) for b in [starts[1], *ends[1:]]]
        for match in re.finditer(r"\S+", line):
            token_end = match.end()
            column = column_count - 1
            for i in range(column_count):
                if token_end <= bounds[i]:
                    column = i
                    break
            token = match.group()
            cells[column] = token if not cells[column] \
                else f"{cells[column]} {token}"
    return cells


def _collect_table(
    lines: list[str], index: int,
) -> tuple[list[str], list[list[str]], int]:
    names, starts, ends, align = _header_columns(lines[index])
    rows: list[list[str]] = []
    j = index + 1
    while j < len(lines):
        stripped = lines[j].strip()
        if stripped == "":
            break
        if _UNDERSCORE_RE.match(stripped):
            break
        if _is_header(lines[j]) or _BANNER_RE.match(stripped):
            break
        rows.append(_slice_row(lines[j], starts, ends, align))
        j += 1
    return names, rows, j


def _render_report_table(
    names: list[str],
    rows: list[list[str]],
    total_runs: int | None,
) -> list[str]:
    lines = [
        "| " + " | ".join(names) + " |",
        "|" + "|".join(["---"] * len(names)) + "|",
    ]
    censored = False
    has_na = False
    samples: list[str] = []

    for row in rows:
        cells: list[str] = []
        for col, cell in enumerate(row):
            cell = cell.strip()
            if col == 0:
                match = _SAMPLE_COUNT_RE.match(cell)
                if match:
                    base = match.group(1).strip()
                    count = int(match.group(2))
                    if total_runs:
                        samples.append(
                            f"`{base}`: {count} of {total_runs} runs"
                        )
                    else:
                        samples.append(f"`{base}`: {count} runs")
                    cells.append(base)
                    continue
            if cell.startswith(">"):
                censored = True
            if cell.upper() == "N/A":
                has_na = True
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")

    notes: list[str] = []
    if censored:
        notes.append(_CENSORED_NOTE)
    if has_na:
        notes.append(_NA_NOTE)
    if samples:
        notes.append("Sample counts: " + "; ".join(samples) + ".")
    lines.extend(_notes_line(notes))
    return lines


def _render_kv_table(pairs: list[tuple[str, str]]) -> list[str]:
    lines = ["| Item | Value |", "|---|---|"]
    for key, value in pairs:
        lines.append(f"| {key} | {value} |")
    return lines


def _capacity_cell(value: float) -> str:
    """Flow/capacity cells are integer unit counts; render them plainly."""
    if value == int(value):
        return str(int(value))
    return f"{value:.4g}"


def _capacity_report_markdown(text: str) -> str:
    """Render capacity-sink rows as Markdown tables (one per run).

    The sink is headerless ``time  flow  capacity`` rows separated by
    ``***End_of_Run_N***`` markers; the generic fixed-width report parser
    would emit those as bare lines, so capacity gets a dedicated table.
    """
    from raptor.api.series import parse_capacity_runs

    runs = [r for r in parse_capacity_runs(text) if r["t"]]
    if not runs:
        return "*No capacity data.*"

    multi = len(runs) > 1
    out: list[str] = []
    for i, run in enumerate(runs):
        if multi:
            out.extend([f"#### Run {i + 1}", ""])
        out.append("| Time | Flow | Capacity |")
        out.append("|---|---|---|")
        for t, flow, capacity in zip(
            run["t"], run["flow"], run["capacity"],
        ):
            out.append(
                f"| {_fmt_value(t, 'time')} | {_capacity_cell(flow)} "
                f"| {_capacity_cell(capacity)} |"
            )
        out.append("")
    return "\n".join(out).rstrip()


def report_markdown(text: str, section: str = "final_results") -> str:
    """Convert legacy fixed-width report text into Markdown tables.

    Detects the Raptor 7 reports' header rows (``Minimum / Mean /
    Maximum / St.Dev / SEM``, ``blockName ...``, ``Block ...``,
    ``SimulationTime ...``, ``Node ...``) and slices the fixed-width
    data rows against the header column starts.  Preserves ``>``
    censored markers, ``N/A`` and ``MDT(18-runs)`` sample-count
    annotations, explaining each in a footnote line under the table.
    Section banners become ``####`` headings.

    ``section="capacity"`` takes the dedicated step-table path (the
    capacity sink has no header row for the generic parser to key on).
    """
    if not text.strip():
        return "*Empty report.*"

    if section == "capacity":
        return _capacity_report_markdown(text)

    lines = text.splitlines()
    out: list[str] = []
    total_runs: int | None = None
    i = 0
    n = len(lines)

    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        if stripped == "":
            if out and out[-1] != "":
                out.append("")
            i += 1
            continue

        match = _TOTAL_RUNS_RE.search(stripped)
        if match:
            total_runs = int(match.group(1))

        if _is_header(raw):
            names, rows, j = _collect_table(lines, i)
            out.extend(_render_report_table(names, rows, total_runs))
            i = j
            continue

        banner = _BANNER_RE.match(stripped)
        if banner:
            title = _heading(banner.group(1))
            if title.lower().startswith("end of run"):
                out.extend(["", f"##### {title}", ""])
            else:
                out.extend(["", f"#### {title}", ""])
            i += 1
            continue

        if _UNDERSCORE_RE.match(stripped):
            i += 1
            continue

        if _KV_RE.match(stripped):
            pairs: list[tuple[str, str]] = []
            while i < n:
                kv = _KV_RE.match(lines[i].strip())
                if not kv:
                    break
                pairs.append((kv.group(1).strip(), kv.group(2)))
                i += 1
            out.extend(_render_kv_table(pairs))
            continue

        if len(stripped.split()) == 1 and not _is_number(stripped):
            out.extend(["", f"##### {_heading(stripped)}", ""])
            i += 1
            continue

        out.append(stripped)
        i += 1

    return "\n".join(out)


# ---------------------------------------------------------------------------
# comparison_table
# ---------------------------------------------------------------------------

def comparison_table(
    comparison: dict,
    display_unit: str | None = None,
    native_unit: str | None = None,
    cost_unit: str | None = None,
) -> str:
    """Render compare() output as a Markdown table, units in the metric
    label where known."""
    models = comparison.get("models_or_runs", [])
    labels = comparison.get("labels") or list(models)
    metrics = comparison.get("metrics", {})

    if not models or not metrics:
        return "*No comparison data.*"

    display_unit = comparison.get("display_unit") or display_unit
    native_unit = comparison.get("native_unit") \
        or comparison.get("units") or native_unit or "hours"
    cost_unit = comparison.get("cost_unit") or cost_unit

    headers = ["Metric"] + list(labels)
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]

    has_na = False
    for name in sorted(metrics.keys()):
        kind = _metric_kind(name)
        label = _metric_label(
            name, kind, display_unit, native_unit, cost_unit,
        )
        row = [label]
        for value in metrics[name]:
            if value is None:
                has_na = True
            row.append(_fmt_value(value, kind))
        lines.append("| " + " | ".join(row) + " |")

    notes = [_NA_NOTE] if has_na else []
    lines.extend(_notes_line(notes))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# sweep_table
# ---------------------------------------------------------------------------

def sweep_table(
    sweep_result: dict,
    display_unit: str | None = None,
    native_unit: str | None = None,
    cost_unit: str | None = None,
) -> str:
    """Render sweep() output as a Markdown table, units in the metric
    headers where known."""
    rows = sweep_result.get("rows", [])
    field = sweep_result.get("field") or sweep_result.get("selector", "")
    monotonic = sweep_result.get("monotonic", True)

    if not rows:
        return "*No sweep data.*"

    display_unit = sweep_result.get("display_unit") or display_unit
    native_unit = sweep_result.get("native_unit") \
        or sweep_result.get("units") or native_unit or "hours"
    cost_unit = sweep_result.get("cost_unit") or cost_unit

    metric_names = sorted(k for k in rows[0] if k != "value")

    headers = [field] + [
        _metric_label(name, _metric_kind(name),
                      display_unit, native_unit, cost_unit)
        for name in metric_names
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]

    has_na = False
    for row in rows:
        vals = [str(row.get("value", ""))]
        for name in metric_names:
            value = row.get(name)
            if value is None:
                has_na = True
            vals.append(_fmt_value(value, _metric_kind(name)))
        lines.append("| " + " | ".join(vals) + " |")

    if not monotonic:
        lines.append("")
        lines.append(
            "*Note: Results are not monotonic in the swept parameter.*"
        )

    if has_na:
        lines.extend(_notes_line([_NA_NOTE]))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# diff_markdown
# ---------------------------------------------------------------------------

_DIFF_TITLES = {
    "elements": "Elements", "links": "Connections", "structure": "Structure",
    "system": "System settings", "rates": "Rates",
    "spare_pools": "Spare pools", "resources": "Resources",
    "triggers": "Triggers", "phases": "Phases", "project": "Project",
}


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _diff_counts(summary: dict) -> str:
    parts = [(summary.get("elements_added"), "added"),
             (summary.get("elements_removed"), "removed"),
             (summary.get("renamed"), "renamed"),
             (summary.get("elements_changed"), "changed")]
    text = ", ".join(f"{n} element(s) {word}" for n, word in parts if n)
    return text or "no element differences"


def diff_markdown(result: dict) -> str:
    """Render ``model_diff.diff_models`` output as Markdown: a summary line,
    then one table (``Element | Field | A | B``) per non-empty group."""
    from raptor.api.model_diff import GROUPS, flatten

    a, b = result["a"]["label"], result["b"]["label"]
    ignored = ", ".join(result.get("ignored") or []) or "nothing"
    if result.get("identical"):
        lines = [f"**{a}** and **{b}** have no differences "
                 f"(ignoring: {ignored})."]
    else:
        s = result["summary"]
        lines = [f"**{a}** vs **{b}**: {_diff_counts(s)}; "
                 f"connectivity: {s['connectivity']} (ignoring: {ignored})."]
    rows = flatten(result)
    for group in GROUPS:
        mine = [r for r in rows if r["group"] == group]
        if not mine:
            continue
        lines += ["", f"### {_DIFF_TITLES[group]}", "",
                  f"| Element | Field | {_cell(a)} | {_cell(b)} |",
                  "|---|---|---|---|"]
        for r in mine:
            who = f"{r['kind']} {r['path']}".strip()
            lines.append(f"| {_cell(who)} | {_cell(r['field'])} | "
                         f"{_cell(r['a'])} | {_cell(r['b'])} |")
    for w in result.get("warnings") or []:
        lines += ["", f"*Warning: {w}*"]
    return "\n".join(lines)
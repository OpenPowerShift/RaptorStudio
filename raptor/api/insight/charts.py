"""Qt-free data for the result charts (WP-2.5).

Each function returns a `ChartSpec`: a kind, a title and subtitle, axis
labels, series, marks, annotations, a one-sentence caption the user can
quote, and provenance.  The GUI paints it with QPainter
(`raptor.gui.ui.chart_widget`), the report renders the same spec to SVG
(`raptor.render.charts_svg`), and the command line prints it.

A chart never computes a measure differently from the tables: every number
comes from `per_run` rows, an `Attribution` dict or the measure registry.
"""
from __future__ import annotations

import math

import itertools

from dataclasses import asdict, dataclass, field, replace
from collections.abc import Callable, Sequence
from typing import Any

from raptor.api.insight import cost_series
from raptor.api.insight import hierarchy as hier
from raptor.api.insight.attribution import CAUSES, CAUSE_LABELS
from raptor.api.insight.ranking import COMPARE_LIMITS
from raptor.api.insight.dots import quantile_dots
from raptor.api.insight import measures
from raptor.api.insight.axes import FAN_LINE_MIN, format_value, pct
from raptor.api.run_needs import message


def _needs(key: str) -> str:
    """A caption that says which option to turn on, as a sentence."""
    text = message(key)
    return f"{text[0].upper()}{text[1:]}."


@dataclass
class ChartSpec:
    kind: str
    title: str
    subtitle: str = ""
    x_label: str = ""
    y_label: str = ""
    series: list[dict[str, Any]] = field(default_factory=list)
    marks: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    caption: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    #: System or hierarchy scope; every surface prints it.  A dict rather
    #: than a dataclass so a stored JSON chart keeps it without a migration.
    scope: dict[str, Any] | None = None
    #: Small multiples: one ChartSpec per panel.  The top-level chart owns
    #: the title and caption; each facet owns its own series and title.
    facets: list[ChartSpec] = field(default_factory=list)
    #: A baseline marker for delta charts (run id, label and the rows'
    #: scope), kept on the spec so an exported figure says what it compares.
    baseline: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Rows that are not one element: a caption does not name them as a cause
#: ("(no single element) loses the largest share" read as a block's name).
_NOT_A_CAUSE = ("(no single element)", "(non-mission phase)", "other", "others")


def _pareto_caption(rows: list[dict], attribution: dict) -> str:
    if not rows:
        return nothing_down(attribution)
    named = [r for r in rows if r.get("name") not in _NOT_A_CAUSE]
    lead = (f"{named[0]['name']} causes the largest share "
            f"({pct(named[0]['dtci']['mean'] or 0)})." if named else
            "No single element is behind the downtime.")
    if named and named[0] is not rows[0]:
        lead = (f"{pct(rows[0]['dtci']['mean'] or 0)} of the downtime was started "
                f"by no single element. " + lead)
    return lead


def _capacity_caption(rows: list[dict], attribution: dict) -> str:
    if not rows:
        return _no_capacity(attribution)
    named = [r for r in rows if r.get("name") not in _NOT_A_CAUSE]
    if not named:
        return ("No single element is behind the lost capacity: it comes from "
                "changes no one element started.")
    top = named[0]
    caption = (f"{top['name']} loses the largest share of capacity "
               f"({pct(top['share']['mean'] or 0)}).")
    if (attribution.get("capacity") or {}).get("rule") == "marginal":
        caption += (" These are overlapping marginal contributions, so they "
                    "need not add to 100 %.")
    unassigned = sum(float((r.get("share") or {}).get("mean") or 0.0)
                     for r in rows if r.get("name") in _NOT_A_CAUSE)
    if unassigned > 0:
        caption += (f" {pct(unassigned)} of the lost flow is not attributed "
                    "to a single element.")
    return caption


def _no_capacity(attribution: dict[str, Any]) -> str:
    """Why a lost-capacity chart is empty: nothing was lost, or the analysis
    was not made. "Needs capacity analysis" was said with it ticked."""
    if attribution.get("capacity"):
        return "No capacity was lost in these runs, so there is nothing to show."
    return _needs("capacity")


def runs_text(n: int) -> str:
    """`1 run`, `5 runs`, `1,200 runs`: captions said "1 runs"."""
    return f"{n:,} run{'' if n == 1 else 's'}"


def _quantity(value: float, unit: str = "") -> str:
    """Human units for a caption: 1,510 h, not 1.51e+03 h."""
    body = f"{abs(value):,.0f}" if abs(value) >= 100 else f"{abs(value):,.3g}"
    return f"{body}{unit}"


def nothing_down(attribution: dict[str, Any]) -> str:
    """The one sentence every downtime chart gives when the system never
    went down: three charts said three different things ("No system
    outage.", "No loss to attribute.", "No outage sequences recorded.")."""
    runs = int(attribution.get("runs") or 0)
    where = f" in {runs_text(runs)}" if runs else ""
    return f"The system never went down{where}, so there is nothing to show."


def cut_note(shown: int, total: int, rest: float | None = None) -> str:
    """`top 10 of 220; the other 210 together 95.4 %`, for a chart that
    draws only its largest items (it cut to 10 without a word)."""
    if total <= shown:
        return ""
    note = f"top {shown} of {total:,}"
    if rest is not None and rest > 0:
        note += f"; the other {total - shown:,} together {pct(rest)}"
    return note


def _with_note(subtitle: str, note: str) -> str:
    return f"{subtitle} ({note})" if note else subtitle


def _number_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def text_lines(chart: ChartSpec, top: int = 10) -> list[str]:
    """The chart as text: its title and caption, then the numbers it draws.
    A timeline gives each layer's value at the end; any other chart its
    first `top` series rows that carry a value.  Money is printed in the
    chart's own currency."""
    from raptor.api.insight.axes import currency
    with currency(chart.provenance.get("currency")):
        return _text_lines(chart, top)


def _text_lines(chart: ChartSpec, top: int) -> list[str]:  # noqa: C901 - one walk per chart shape
    from raptor.api.insight.axes import format_value, with_currency
    fmt = str(chart.provenance.get("format") or "")

    def shown(value: float) -> str:            # a timeline layer's end value
        return format_value(value, fmt, 5) if fmt else f"{value:,.4g}"

    def row_value(value: float) -> str:
        return (with_currency(f"{value:,.2f}") if fmt == "money"
                else format_value(value, fmt, 6))

    scope = ""
    if chart.scope:
        scope = " [scope: " + hier.scope_label(chart.scope) + "]"
    lines = [f"{chart.title}{scope} — {chart.caption}"]
    if chart.kind == "timeline":
        return lines + _timeline_lines(chart, shown)
    if chart.facets:
        for facet in chart.facets:
            lines += [f"  {facet.title}"] + [
                "  " + line for line in text_lines(facet, top)[1:]]
        return lines
    extra = _kind_lines(chart, row_value)
    if extra is not None:
        return lines + extra
    for row in chart.series[:top]:
        name = row.get("name") or row.get("label") or ""
        value = row.get("value", row.get("count", row.get("mean")))
        if name and isinstance(value, (int, float)) and not isinstance(value, bool):
            lines.append(f"  {name}: {row_value(float(value))}")
    return lines


def _timeline_lines(chart: ChartSpec, shown: Callable[[float], str]) -> list[str]:
    """Each layer's value at the end of a chart over time."""
    out = []
    for layer in chart.series:
        if layer.get("read") == "none" or not layer.get("t"):
            continue
        if layer.get("role") != "area":
            text = shown(float(layer["y"][-1]))
        elif layer.get("read") == "range":
            text = f"{shown(float(layer['lo'][-1]))} to {shown(float(layer['hi'][-1]))}"
        else:
            text = shown(float(layer["hi"][-1]) - float(layer["lo"][-1]))
        out.append(f"  {layer.get('name', '')}: {text} at the end")
    return out


def _csv_table(chart: ChartSpec) -> tuple[list[str], list[list[Any]]]:  # noqa: C901, PLR0912 - one walk of every row shape
    """The chart's numbers as a header and rows, for a CSV file.

    The columns are what the rows carry, named for what they hold: the
    export had a fixed "series,name,x,y,value,note" header, left x, y and
    note empty, wrote a share as 0.3349 with no word of what it was, and
    dropped the error bars and the per-cause split drawn on the chart."""
    if chart.kind == "timeline":
        out: list[list[Any]] = []
        for layer in chart.series:
            for k, t in enumerate(layer.get("t") or []):
                out.append([layer.get("name", ""), t,
                            *[(layer.get(key) or [""] * (k + 1))[k]
                              for key in ("y", "lo", "hi")]])
        return ["layer", "time", "value", "low", "high"], out
    if chart.facets:
        header = ["panel"]
        panels: list[tuple[str, list[str], list[list[Any]]]] = []
        for facet in chart.facets:
            fh, fr = _csv_table(facet)
            panels.append((facet.title, fh, fr))
            header += [name for name in fh if name not in header]
        rows = [[title, *[dict(zip(fh, row, strict=True)).get(column, "")
                          for column in header[1:]]]
                for title, fh, fr in panels for row in fr]
        return header, rows
    if chart.kind == "scorecard":
        return (["hierarchy", "share", "sem", "members", "starters", "main_cause"],
                [[row.get("name"), row.get("value"), row.get("sem"),
                  row.get("members"), row.get("starters"), row.get("cause")]
                 for row in chart.series])
    if chart.kind == "delta":
        return (["hierarchy", "before", "after", "change", "status", "path"],
                [[row.get("name"), row.get("before"), row.get("after"),
                  row.get("value"), row.get("status"), row.get("path")]
                 for row in chart.series])
    if chart.kind == "heatmap":
        columns = [str(item.get("key")) for item in
                   chart.provenance.get("columns") or []]
        return ([str(chart.provenance.get("row_label") or "hierarchy"),
                 *columns, "total"],
                [[row.get("name"), *[row.get("cells", {}).get(c) for c in columns],
                  row.get("total")] for row in chart.series])
    if chart.kind == "hops":
        hop_names = [str(row.get("name")) for row in chart.series]
        count = max((len(row.get("runs") or []) for row in chart.series), default=0)
        return (["run", *hop_names],
                [[i + 1, *[(row.get("runs") or [None] * count)[i]
                           for row in chart.series]] for i in range(count)])
    if chart.kind == "sankey":
        return (["source", "target", "value", "cause"],
                [[row.get("source"), row.get("target"), row.get("value"),
                  row.get("cause")] for row in chart.series])
    if chart.kind == "sunburst":
        return (["id", "name", "parent", "depth", "value"],
                [[row.get("id"), row.get("name"), row.get("parent"),
                  row.get("depth"), row.get("value")] for row in chart.series])
    percent = chart.provenance.get("format") == "percent"
    measure = (chart.y_label if chart.kind in ("pareto", "capacity_pareto", "ladder",
                                               "waterfall") else "") or "value"
    unit = " (fraction: 1 = 100 %)" if percent else ""
    names = {"value": f"{measure}{unit}", "sem": f"standard error{unit}",
             "share": f"share{unit}", "n": "runs", "count": "count",
             "lo": "from", "hi": "to"}
    flat: list[dict[str, Any]] = []
    for series in chart.series:
        nested = series.get("dots") or series.get("points")
        for row in (nested if isinstance(nested, list) else [series]):
            item: dict[str, Any] = {}
            if nested is not None:
                item["series"] = series.get("name", "")
            for key, value in row.items():
                if key == "causes" and isinstance(value, dict):
                    for cause, amount in value.items():
                        item[f"cause: {CAUSE_LABELS.get(cause, cause)}{unit}"] = amount
                elif value is None or isinstance(value, (str, int, float, bool)):
                    item[names.get(key, key)] = value
            flat.append(item)
    header = []
    for item in flat:
        header += [k for k in item if k not in header]
    return header, [[item.get(k, "") for k in header] for item in flat]


def csv_table(chart: ChartSpec) -> tuple[list[str], list[list[Any]]]:
    """The chart's numbers plus its scope, for a CSV file."""
    header, rows = _csv_table(chart)
    if chart.scope:
        scope = " > ".join(str(part) for part in chart.scope.get("path") or [])
        header = [*header, "scope"]
        rows = [[*row, scope] for row in rows]
    return header, rows


def _kind_lines(chart: ChartSpec,  # noqa: C901, PLR0912 - one branch per chart kind
                row_value: Callable[[float], str]) -> list[str] | None:
    """The numbers of the charts whose rows are not (name, value) pairs:
    the text gave blank-labelled rows ("  : 179") or nothing for them."""
    rows = chart.series
    if chart.kind == "scorecard":
        return [f"  {row.get('name')}: {row_value(float(row.get('value') or 0))}"
                f" · {int(row.get('members') or 0)} inside, "
                f"{int(row.get('starters') or 0)} started an outage"
                for row in rows]
    if chart.kind == "hops":
        return [f"  {row.get('name')}: mean {row_value(float(row.get('value') or 0))}"
                f", highest in {int(row.get('first') or 0)} of "
                f"{len(row.get('runs') or [])} runs" for row in rows]
    if chart.kind == "delta":
        return [f"  {row.get('name')}: "
                f"{row_value(float(row.get('before') or 0))} to "
                f"{row_value(float(row.get('after') or 0))} "
                f"({float(row.get('value') or 0):+.3g})"
                for row in rows]
    if chart.title == "Downtime by phase":
        out = []
        for row in rows:
            line = (f"  {row.get('name')}: {row_value(float(row.get('value') or 0))} "
                    "of downtime")
            if _number_value(row.get("availability")):
                line += f", availability {row_value(float(row['availability']))}"
            if row.get("mission") is False:
                line += " (non-mission)"
            if row.get("entered") is False:
                line += " (not entered)"
            out.append(line)
        return out
    if chart.kind == "heatmap":
        return [f"  {row.get('name')}: " + ", ".join(
            f"{CAUSE_LABELS.get(key, key)} {row_value(float(value))}"
            for key, value in (row.get("cells") or {}).items())
            for row in rows]
    if chart.kind == "sankey":
        return [f"  {row.get('source')} -> {row.get('target')}: "
                f"{float(row.get('value') or 0):,.4g}"
                for row in rows]
    if chart.kind == "sunburst":
        return [f"  {row.get('name')}: {row_value(float(row.get('value') or 0))}"
                for row in rows]
    if chart.kind == "dot_plot":
        values = [float(v) for r in rows for v in r.get("values") or ()]
        if not values:
            values = [float(d["value"]) for r in rows
                      for d in r.get("dots") or ()
                      if _number_value(d.get("value"))
                      for _ in range(int(d.get("count") or 1))]
        values.sort()
        if not values:
            return []
        return [f"  runs: {len(values):,}",
                f"  lowest: {row_value(values[0])}",
                f"  median: {row_value(_quartile(values, 0.5))}",
                f"  highest: {row_value(values[-1])}"]
    if chart.kind == "exceedance":
        out = []
        for r in rows:
            points = r.get("points") or []
            for share in (0.1, 0.5, 0.9):
                hit = next((p for p in points if float(p["share"]) >= share - 1e-9), None)
                if hit is not None:
                    out.append(f"  {round(100 * share)} % of runs reached at least "
                               f"{row_value(float(hit['value']))}")
        return out
    if chart.kind == "convergence":
        if not rows:
            return []
        last = rows[-1]
        sem = last.get("sem")
        spread = (f" (± {row_value(2 * float(sem))} at 2 standard errors)"
                  if isinstance(sem, (int, float)) else "")
        return [f"  after {int(last['n']):,} runs: {row_value(float(last['mean']))}{spread}"]
    if chart.kind == "histogram":
        from raptor.api.insight.axes import format_value
        unit = str(chart.provenance.get("time_units") or "")
        unit = f" {unit}" if unit else ""
        return [(f"  {format_value(float(r['lo']), '', 4)}{unit}: "
                 if float(r["lo"]) == float(r["hi"]) else      # "2.139 to 2.139"
                 f"  {format_value(float(r['lo']), '', 3)} to "
                 f"{format_value(float(r['hi']), '', 3)}{unit}: ")
                + f"{int(r['count']):,}" for r in rows if _number_value(r.get("count"))]
    if chart.kind == "bubble":
        return [f"  {bubble_tip(chart, r)}" for r in rows[:10]
                if _number_value(r.get("x")) and _number_value(r.get("y"))]
    if chart.kind == "quadrant":
        return [f"  {r.get('name', '')}: RAW {float(r['x']):,.4g}, RRW {float(r['y']):,.4g}"
                for r in rows[:10] if _number_value(r.get("x")) and _number_value(r.get("y"))]
    if chart.kind == "ribbon":
        shares = sorted(float(r["downtime"]) / float(r["span"]) for r in rows
                        if _number_value(r.get("downtime")) and r.get("span"))
        if not shares:
            return []
        return [f"  runs drawn: {len(shares):,}",
                f"  least down: {pct(shares[0])} of its time",
                f"  median: {pct(_quartile(shares, 0.5))}",
                f"  most down: {pct(shares[-1])}"]
    if chart.kind == "scatter":
        return [f"  {r.get('name', '')}: {chart.x_label} {row_value(float(r['x']))}, "
                f"{chart.y_label} {row_value(float(r['y']))}"
                for r in rows[:10] if _number_value(r.get("x")) and _number_value(r.get("y"))]
    return None


def _values(per_run: list[dict], field_name: str) -> list[float]:
    return [float(row[field_name]) for row in per_run
            if isinstance(row.get(field_name), (int, float))
            and not isinstance(row.get(field_name), bool)]


def _quartile(values: list[float], q: float) -> float:
    """The `q` quantile of `values` (sorted, not empty), interpolated."""
    pos = q * (len(values) - 1)
    low = int(pos)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (pos - low)


def quartile_marks(values: list[float]) -> list[dict[str, Any]]:
    """U16: Q1, median and Q3 of the runs, as quiet reference lines."""
    if len(values) < 4:
        return []
    ordered = sorted(values)
    marks: list[dict[str, Any]] = []
    for label, q in (("Q1", 0.25), ("median", 0.5), ("Q3", 0.75)):
        value = _quartile(ordered, q)
        if marks and marks[-1]["value"] == value:     # one line, one label
            marks[-1]["label"] += f", {label}"
        else:
            marks.append({"kind": "quartile", "label": label, "value": value})
    return marks


def trial_outcome(per_run: list[dict], field_name: str = "availability", *,
                  label: str = "Availability", target: float | None = None,
                  units: str = "", higher_is_better: bool = True) -> ChartSpec:
    """C1: one dot per quantile slice of the per-run values."""
    values = _values(per_run, field_name)
    dots = quantile_dots(values)
    met = sum(1 for v in values if target is not None
              and (v >= target if higher_is_better else v <= target))
    caption = f"{label}: {runs_text(len(values))}"
    mean = _mean(values)
    if mean is not None:
        from raptor.api.insight.axes import format_value
        caption += f", mean {format_value(mean, value_format(field_name, units), 6)}"
    quartiles = quartile_marks(values)
    if len(quartiles) > 1:
        from raptor.api.insight.axes import format_value
        fmt = value_format(field_name, units)
        caption += (f"; the middle half of the runs lie between "
                    f"{format_value(quartiles[0]['value'], fmt, 5)} and "
                    f"{format_value(quartiles[-1]['value'], fmt, 5)} (Q1 to Q3)")
    if target is not None and values:
        caption += (f"; {met} of {len(values)} "
                    f"({pct(met / len(values))}) met the target.")
    else:
        caption += "."
    return ChartSpec(
        "dot_plot", f"Run-to-run {label.lower()}",
        "one dot per quantile slice of the runs", label, "runs",
        series=[{"name": "runs", "dots": dots, "values": values}],
        marks=([{"kind": "target", "value": target}] if target is not None else [])
        + quartiles,
        caption=caption,
        provenance={"runs": len(values), "units": units,
                    "format": value_format(field_name, units),
                    "measure": field_name, "met": met, "target": target,
                    "higher_is_better": higher_is_better})


#: Per-run fields that are fractions, printed as percentages.
_FRACTIONS = {"availability", "reliability", "dependability",
              "mission_availability", "mission_reliability",
              "capacity_weighted_availability", "ao", "ai", "aa",
              "green", "yellow", "red"}


def value_format(field_name: str, units: str = "") -> str:
    """How a chart should print this field: ``percent``, ``money`` or ``""``.

    A fraction is a percentage whatever `units` says: callers pass the
    model's time unit there, which is not the unit of a fraction."""
    if field_name in _FRACTIONS:
        return "percent"
    if units in ("cost", "money"):
        return "money"
    return ""


def exceedance_points(values: list[float], *, higher_is_better: bool = True
                      ) -> list[dict[str, float]]:
    """One point per distinct value: the share of runs that reached it.

    With `higher_is_better` a run reaches `v` when its value is at least
    `v`; otherwise when it is at most `v` (downtime, cost).  The points run
    from the share 1/n (the best run) to 1 (every run)."""
    ordered = sorted(values, reverse=higher_is_better)
    n = len(ordered)
    points: list[dict[str, float]] = []
    for i, value in enumerate(ordered):
        share = (i + 1) / n
        if points and points[-1]["value"] == value:
            points[-1]["share"] = share
        else:
            points.append({"value": value, "share": share})
    return points


def _reached(points: list[dict[str, float]], share: float) -> float:
    """The value at least `share` of the runs reached (`points` not empty)."""
    for point in points:
        if point["share"] >= share - 1e-12:
            return point["value"]
    return points[-1]["value"]


def ecdf(per_run: list[dict], field_name: str = "availability", *,
         label: str = "Availability", target: float | None = None,
         units: str = "", higher_is_better: bool = True) -> ChartSpec:
    """C1b: the chance a run reaches each value (an exceedance curve).

    Read across: pick a share of runs on the horizontal axis and the curve
    gives the value that many runs reached.  The runs are the dot plot's."""
    values = _values(per_run, field_name)
    points = exceedance_points(values, higher_is_better=higher_is_better)
    fmt = value_format(field_name, units)
    word = "at least" if higher_is_better else "at most"

    def shown(value: float) -> str:
        from raptor.api.insight.axes import format_value
        return format_value(value, fmt, 5)

    if len(values) == 1:
        # "9 runs in 10 reached ..." was said of a single run
        caption = f"The one run reached {shown(values[0])}."
    elif points and min(values) == max(values):
        caption = f"Every run reached {shown(values[0])}."
    elif points and len(values) < 10:
        # "9 runs in 10" was said of 2 or 5 runs
        ordered = sorted(values, reverse=higher_is_better)
        caption = (f"All {len(values)} runs reached {word} {shown(ordered[-1])}; "
                   f"the best reached {shown(ordered[0])}.")
    elif points:
        nine = _reached(points, 0.9)
        half = _reached(points, 0.5)
        caption = (f"9 runs in 10 reached {word} {shown(nine)}; half reached "
                   f"{word} {shown(half)}.")
    else:
        caption = "No runs."
    if points and target is not None:
        met = sum(1 for v in values
                  if (v >= target if higher_is_better else v <= target))
        caption += (f" {pct(met / len(values))} of runs reached the "
                    f"target {shown(target)}.")
    return ChartSpec(
        "exceedance", f"Chance of reaching {label.lower()}",
        "read across from a share of runs to the value they reached",
        "share of runs", label,
        series=[{"name": label, "points": points}],
        marks=([{"kind": "target", "value": target}] if target is not None else [])
        # U16: where 1 run in 4, half and 3 in 4 sit on the curve
        + ([{"kind": "quartile", "label": label_q, "share": share,
             "value": _reached(points, share)}
            for label_q, share in (("1 in 4", 0.25), ("half", 0.5), ("3 in 4", 0.75))]
           if len(values) >= 4 else []),
        caption=caption,
        provenance={"runs": len(values), "units": units, "format": fmt,
                    "measure": field_name, "reached": word,
                    "higher_is_better": higher_is_better})


def reliability_curve(per_run: list[dict], *, units: str = "") -> ChartSpec:  # noqa: C901, PLR0912, PLR0915 - one estimate, one caption
    """The chance the system has not yet gone down by each time (Kaplan–Meier).

    "Down" is the engine's first-failure time (`mttff`): the first time the
    system went Red for any reason, planned maintenance included.

    Every run contributes its time to first system failure; a run that ended
    without one is censored at its end, not counted as a failure.  A single
    ``reliability`` value per run (0 or 1) cannot show *when* the failures
    happen; this curve can.
    """
    events: list[tuple[float, bool]] = []
    censored = 0
    for row in per_run:
        failed = not bool(row.get("mttff_censored"))
        raw = row.get("mttff")
        if not _number_value(raw):
            raw = row.get("ending_sim_time") or row.get("total_time")
            failed = False
        time = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0
        if time <= 0:
            continue
        events.append((time, failed))
        censored += 0 if failed else 1
    if not events:
        return ChartSpec(
            "timeline", "Reliability over time",
            "the chance the system has not yet gone down by each time",
            "time", "reliability", series=[],
            caption="No run recorded a time to first failure.",
            provenance={"runs": 0, "format": "percent", "y_range": [0.0, 1.0]})
    events.sort()
    total = len(events)
    at_risk = total
    survival = 1.0
    xs: list[float] = [0.0]
    ys: list[float] = [1.0]
    i = 0
    while i < total:
        time = events[i][0]
        failures = 0
        stops = 0
        while i < total and events[i][0] == time:
            if events[i][1]:
                failures += 1
            else:
                stops += 1
            i += 1
        before = survival
        if failures and at_risk > 0:
            survival *= 1.0 - failures / at_risk
        at_risk -= failures + stops
        xs.extend([time, time])
        ys.extend([before, survival])
    unit = f" {units}" if units else ""
    failures = sum(1 for _time_value, failed in events if failed)
    caption = (f"From {runs_text(total)}, Kaplan–Meier estimate"
               + (f"; {censored:,} ended before a failure and are censored"
                  if censored else "")
               + ".")
    if failures:
        # "9 in 10 by 6,469 h" read as nine in ten FAILED by then; name the
        # level the curve falls to instead
        reached = []
        for share in (0.9, 0.5):
            point = next((x for x, y in zip(xs[1::2], ys[2::2])
                          if y <= share + 1e-12), None)
            if point is not None:
                reached.append(f"{pct(share)} or less by {_quantity(point, unit)}")
        if reached:
            caption += " Reliability is " + " and ".join(reached) + "."
        caption += (f" The last failure was at "
                    f"{_quantity(max(t for t, failed in events if failed), unit)}.")
        # `mttff` is the first time the system went down for ANY reason:
        # many runs going down at the very same instant is a scheduled
        # outage (planned maintenance, a phase), not random failures
        counts: dict[float, int] = {}
        for time_value, failed in events:
            if failed:
                counts[time_value] = counts.get(time_value, 0) + 1
        when, same = max(counts.items(), key=lambda item: item[1])
        if same >= max(3, math.ceil(0.2 * total)):
            caption += (f" {same:,} runs first went down at exactly "
                        f"{_quantity(when, unit)}: a scheduled outage such as "
                        "planned maintenance, not a random failure.")
    else:
        caption += " No run failed."
    return ChartSpec(
        "timeline", "Reliability over time",
        "the chance the system has not yet gone down by each time; any "
        "outage counts, planned maintenance included (Kaplan–Meier; a run "
        "still up at its end is censored)",
        f"time ({units})" if units else "time", "reliability",
        series=[{"name": "reliability", "role": "line", "tone": "accent",
                 "t": xs, "y": ys, "width": 2.0}],
        caption=caption,
        provenance={"runs": total, "format": "percent", "time_units": units,
                    "measure": "reliability", "failures": failures,
                    "censored": censored, "y_range": [0.0, 1.0]})


def component_failure_pareto(per_run: list[dict], top: int | None = 10) -> ChartSpec:
    """Which components fail most often (every block failure, not only
    failures that downed the system).

    ``criticality.failure`` is each block's share of the run's component
    failures; multiplied by ``num_component_failures`` it is that block's
    failures in the run, averaged over the runs.  Availability, DTCI and
    the lost-capacity charts cannot show a redundant unit that fails often
    and is masked; this chart can.
    """
    runs = [row for row in per_run
            if _number_value(row.get("num_component_failures"))]
    totals: dict[str, float] = {}
    for row in runs:
        count = float(row["num_component_failures"])
        for item in (row.get("criticality") or {}).values():
            name = str(item.get("name") or "")
            share = item.get("failure")
            if name and _number_value(share):
                totals[name] = totals.get(name, 0.0) + float(share) * count
    ranked = sorted(((name, total / len(runs)) for name, total in totals.items()
                     if total > 0), key=lambda pair: -pair[1])
    if not runs or not ranked:
        return ChartSpec(
            "pareto", "Which components fail most often?",
            "all component failures, including those that did not down the system",
            "element", "component failures a run",
            caption="No component failure was recorded in these runs.",
            provenance={"runs": len(runs), "format": "count"})
    top = len(ranked) if top is None else top    # None: every component
    shown = ranked[:top]
    total = sum(value for _name, value in ranked)
    all_failures = sum(float(row["num_component_failures"]) for row in runs) / len(runs)
    name, value = shown[0]
    share = value / all_failures if all_failures else 0.0
    rest = total - sum(amount for _item, amount in shown)
    note = (f"top {len(shown)} of {len(ranked)}; the other "
            f"{len(ranked) - len(shown):,} together {rest:,.3g} failures a run"
            if len(ranked) > len(shown) else "")
    return ChartSpec(
        "pareto", "Which components fail most often?",
        _with_note("all component failures, including those that did not "
                   "down the system", note),
        "element", "component failures a run",
        series=[{"name": item, "value": amount} for item, amount in shown],
        caption=(f"{name} fails most often: {value:,.3g} failures a run "
                 f"on average ({pct(share)} of all component failures). "
                 f"The runs average {all_failures:,.3g} component failures each."),
        provenance={"runs": len(runs), "format": "count",
                    "measure": "component failures per run",
                    "items": len(ranked),
                    "cut": [{"name": item, "value": amount}
                            for item, amount in ranked[top:]]})


def downtime_pareto(attribution: dict[str, Any], top: int | None = 10) -> ChartSpec:
    """C2: who causes the downtime, stacked by what the time was spent on."""
    ranked = sorted((r for r in attribution.get("elements") or []
                     if (r.get("dtci", {}).get("mean") or 0) > 0),
                    key=lambda r: -(r["dtci"]["mean"] or 0))
    top = len(ranked) if top is None else top    # None: every element
    rows = ranked[:top]
    rest = sum(r["dtci"]["mean"] or 0 for r in ranked[top:])
    return ChartSpec(
        "pareto", "Who causes the downtime",
        # the Story said "Generator_3, waiting for a spare" while this said
        # repair: an outage is charged whole to the element that started it
        _with_note("each outage charged to the element that started it, "
                   "stacked by what that element was doing",
                   cut_note(len(rows), len(ranked), rest)), "element",
        "share of system downtime",
        series=[{"name": r["name"], "value": r["dtci"]["mean"] or 0.0,
                 "sem": r["dtci"].get("sem"),
                 "causes": r.get("causes") or {},
                 "outages": r.get("deci", {}).get("mean") or 0.0,
                 "red_time": r.get("red_time", {}).get("mean") or 0.0}
                for r in rows],
        caption=_pareto_caption(rows, attribution),
        provenance={"items": len(ranked), "runs": attribution.get("runs", 0), "format": "percent",
                    # the rows past `top`, so a fold counts them too (it
                    # said "7 others" where the treemap said "15 others")
                    "cut": [{"name": r["name"], "value": r["dtci"]["mean"] or 0.0,
                             "causes": r.get("causes") or {}} for r in ranked[top:]]})


#: The ladder's rungs, in the order the waterfall subtracts them.
_RUNG_LABELS = {
    "repair": "Hands-on repair",
    "pm": "Planned maintenance",
    "logistics": "Logistics, spares and crews",
    "not_repairable": "Not repairable",
    "other": "Other",
}


def _ladder_waterfall_steps(ladder: dict[str, Any], operational: float,
                            loss: float) -> list[dict[str, Any]]:
    """Steps from the same Red-time split the availability ladder shows."""
    rungs = ladder.get("rungs") or {}
    total = sum(float(value or 0.0) for value in rungs.values())
    if total <= 0:
        return []
    steps: list[dict[str, Any]] = []
    running = 1.0
    for key, label in _RUNG_LABELS.items():
        amount = float(rungs.get(key) or 0.0)
        if amount <= 0:
            continue
        share = amount / total
        drop = share * loss
        steps.append({"name": label, "cause": key, "value": drop,
                      "from": running, "to": running - drop, "share": share})
        running -= drop
    return steps


def downtime_waterfall(attribution: dict[str, Any],
                       operational: float | None = None,
                       target: float | None = None,
                       ladder: dict[str, Any] | None = None) -> ChartSpec:
    """C3: from 100 % availability to the achieved one, one step per rung.

    `ladder` is `measures.availability_ladder(per_run)`.  When given, the
    steps are its Red-time split, so the waterfall and the availability
    ladder read the same numbers; without it the attribution's causes are
    used.  (The engine's Red-time buckets are fixed when the system turns
    Red; attribution reclassifies each instant, so the two need not agree.)
    """
    causes = attribution.get("causes") or {}
    if ladder is not None and ladder.get("operational", {}).get("value") is not None:
        operational = float(ladder["operational"]["value"])
    loss = 1.0 - operational if operational is not None else None
    steps: list[dict[str, Any]] = []
    if loss is not None and ladder is not None and operational is not None:
        steps = _ladder_waterfall_steps(ladder, operational, loss)
    running = 1.0
    if not steps:
        for cause in CAUSES:
            share = float(causes.get(cause, 0.0))
            if share <= 0:
                continue
            drop = share * loss if loss is not None else share
            steps.append({"name": CAUSE_LABELS.get(cause, cause), "cause": cause,
                          "value": drop, "from": running, "to": running - drop,
                          "share": share})
            running -= drop
    else:
        running = steps[-1]["to"]
    if not steps:
        caption = nothing_down(attribution)
    elif loss is None:
        caption = ("Each step's share of the loss; pass the run's operational "
                   "availability for steps in points.")
    else:
        largest = max(steps, key=lambda s: s["value"])
        caption = (f"The largest single loss is "
                   f"{largest['name'].lower()} "
                   f"({100 * largest['value']:.3g} points).")
        if ladder is not None:
            caption += (" The steps use the same Red-time split as the "
                        "availability ladder.")
    return ChartSpec(
        "waterfall", "From full availability to the achieved one",
        "each step is a share of the loss, in the availability ladder's "
        "order" if ladder is not None else "each step is a cause's share of the loss",
        "cause", "share of loss", series=steps,
        # a target only means something on the availability scale
        marks=([{"kind": "end", "value": running}] if steps else [])
        + ([{"kind": "target", "value": target}]
           if target is not None and operational is not None and steps else []),
        caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "operational": operational,
                    "basis": "ladder" if ladder is not None else "attribution"})


def availability_ladder(ladder: dict[str, Any], target: float | None = None) -> ChartSpec:
    """C4: inherent, achieved and operational availability with their gaps."""
    order = (("inherent", "Inherent (repair only)"),
             ("achieved", "Achieved (+ planned maintenance)"),
             ("operational", "Operational (+ logistics, spares, crews)"))
    series = []
    for key, label in order:
        entry = ladder.get(key) or {}
        series.append({"name": label, "key": key, "value": entry.get("value"),
                       "source": entry.get("source", "")})
    present = [s for s in series if s["value"] is not None]
    caption = "; ".join(f"{s['name']} {100 * s['value']:.6g} %"
                        for s in present) or "No availability reported."
    # equal rungs drew three identical bars with no word why
    nothing = [later["name"].split(" (+ ")[-1].rstrip(")")
               for earlier, later in itertools.pairwise(present)
               if abs(float(later["value"]) - float(earlier["value"])) < 1e-12]
    if nothing:
        caption += (". " + " and ".join(nothing).capitalize()
                    + " took nothing away in these runs.")
    return ChartSpec(
        "ladder", "What each kind of availability keeps",
        "the gap between rungs is what the next cause takes away",
        "availability", "fraction", series=present,
        marks=([{"kind": "target", "value": target}] if target is not None else []),
        caption=caption,
        provenance={"runs": ladder.get("runs"), "format": "percent"})


def _open_note(durations: dict[str, Any], count: int) -> str:
    """Outages still going when a run ended: with no repair every outage
    was, and "half end by 267 h" was the run length, not a repair time."""
    still = int(durations.get("open_at_end") or 0)
    if not still or not count:
        return ""
    if still == count:
        return (" Every outage was still going when its run ended, so these are "
                "the times to the end of the run, not how long outages last.")
    return (f" {still:,} of the {count:,} outages were still going when the run "
            "ended; they are counted to the end of the run.")


def outage_histogram(attribution: dict[str, Any]) -> ChartSpec:
    """C6: many short outages or a few long ones (log-x)."""
    durations = attribution.get("outage_durations") or {}
    hist = durations.get("hist") or []
    p50, p90 = durations.get("p50"), durations.get("p90")
    # the time unit was nowhere on the chart or in its caption
    units = str(attribution.get("units") or "")
    unit = (" h" if units.lower().startswith("hour") else
            f" {units}" if units else "")
    count = int(durations.get("count") or sum(float(b.get("count") or 0) for b in hist))
    return ChartSpec(
        "histogram", "How long is an outage?",
        "log scale: many short outages or a few long ones",
        f"duration ({units})" if units else "duration",
        "outages", series=hist,
        marks=([] if count < 2 else
               [{"kind": "quantile", "label": "median", "value": p50},
                {"kind": "quantile", "label": f"1 in 10 longer ({p90:,.4g}{unit})"
                 if p90 is not None else "1 in 10 longer", "value": p90}]),
        caption=(nothing_down(attribution) if p50 is None or p90 is None else
                 # "half end by 267; one in ten lasts over 267" of one outage
                 f"There was one outage in {attribution.get('runs', 0):,} runs; it "
                 f"lasted {p50:,.4g}{unit}." if count == 1 else
                 f"Half of the outages end by {p50:,.4g}{unit}; one in ten "
                 f"lasts over {p90:,.4g}{unit}.") + _open_note(durations, count),
        provenance={"runs": attribution.get("runs", 0),
                    "count": durations.get("count", 0), "time_units": units})


def frequency_duration(attribution: dict[str, Any], top: int | None = 20) -> ChartSpec:
    """C7: reliability against maintainability, bubble area = DTCI.

    x is how many system outages the element starts a run and y how long
    they last on average, in the model's time unit, so both axes read as
    quantities. An attribution without per-element counts (an older stored
    run) falls back to the two shares, y then being relative (1 = an
    average outage)."""
    ranked = sorted((r for r in attribution.get("elements") or []
                     if (r.get("deci", {}).get("mean") or 0) > 0),
                    key=lambda r: -(r.get("dtci", {}).get("mean") or 0))
    top = len(ranked) if top is None else top
    rows = ranked[:top]
    note = cut_note(len(rows), len(ranked),
                    sum(r.get("dtci", {}).get("mean") or 0 for r in ranked[top:]))
    units = str(attribution.get("units") or "")
    absolute = bool(rows) and all(
        (r.get("outages") or {}).get("mean") and (r.get("red_time") or {}).get("mean")
        is not None for r in rows)
    def absolute_point(r: dict[str, Any]) -> dict[str, Any]:
        outages = (r.get("outages") or {}).get("mean")
        red = (r.get("red_time") or {}).get("mean")
        return {"name": r["name"], "x": outages,
                "y": red / outages if outages and red is not None else None,
                "area": r["dtci"]["mean"] or 0.0}

    def relative_point(r: dict[str, Any]) -> dict[str, Any]:
        return {"name": r["name"], "x": r["deci"]["mean"] or 0.0,
                "y": (r["dtci"]["mean"] or 0.0) / (r["deci"]["mean"] or 1.0),
                "area": r["dtci"]["mean"] or 0.0}

    point = absolute_point if absolute else relative_point
    if absolute:
        points = [absolute_point(r) for r in rows]
        subtitle = ("x = system outages it starts a run, y = how long they last, "
                    "area = share of downtime")
        x_label, y_label = "outages it started a run", (
            f"mean outage length ({units})" if units else "mean outage length")
    else:
        points = [relative_point(r) for r in rows]
        subtitle = ("x = share of outages started, y = outage length relative to "
                    "the average (1 = average), area = share of downtime")
        x_label, y_label = "share of outages started", "relative outage length"
    return ChartSpec(
        "bubble", "Frequent or long outages?", _with_note(subtitle, note),
        x_label, y_label,
        series=points,
        # frequent short outages sit low on the right; rare long ones high on
        # the left (the caption had the two levers the other way round)
        caption=("Low right, frequent short outages: improve reliability. "
                 "High left, rare long ones: repair and logistics."
                 if points else nothing_down(attribution)),
        provenance={"items": len(ranked), "runs": attribution.get("runs", 0), "absolute": absolute,
                    # the bubbles past `top`, for the all-items table
                    "cut": [point(r) for r in ranked[top:]],
                    # outages a run are a count, not a share: drawn as a
                    # percentage, 2.8 a run read "280 %"
                    "x_format": "" if absolute else "percent",
                    "units": units})


def bubble_tip(spec: ChartSpec, point: dict[str, Any]) -> str:
    """The hover text of one frequency-against-duration bubble."""
    from raptor.api.insight.axes import pct
    share = pct(float(point.get("area") or 0))
    if spec.provenance.get("absolute"):
        units = spec.provenance.get("units") or ""
        return (f"{point['name']}: {float(point['x']):.3g} outages a run, "
                f"{float(point['y']):.3g}{' ' + units if units else ''} each on "
                f"average; {share} of downtime")
    return f"{point['name']}: {pct(float(point['x']))} of outages, {share} of downtime"


def importance_quadrant(importance: dict[str, Any],
                        metric: str = "Availability") -> ChartSpec:
    """C8: what losing an element costs (RAW) against what fixing it buys."""
    entry = (importance.get("metrics") or {}).get(metric) or {}
    rows = entry.get("rows") or []
    points = [{"name": r.get("block"), "x": r.get("raw"), "y": r.get("rrw")}
              for r in rows
              if r.get("raw") is not None and r.get("rrw") is not None]
    unbounded = [r for r in rows if r.get("raw") is not None and r.get("rrw") is None]
    if points:
        caption = ("Points far above and right of 1 are both critical and "
                   "worth fixing.")
    elif unbounded:
        # "No block has both a RAW and an RRW" read as missing data: the
        # RRW is unbounded (fixing the block removes all the downtime)
        caption = ("Making any one of these blocks perfect would remove all the "
                   "downtime, so their RRW is unbounded and there is no "
                   "quadrant to draw. RAW: " + ", ".join(
                       f"{r.get('block')} {float(r['raw']):.3g}"
                       for r in sorted(unbounded, key=lambda r: -float(r["raw"]))[:5])
                   + ".")
    elif not any(r.get("raw") is not None for r in rows):
        caption = ("No block failed in these runs, so there is no importance "
                   "to measure.")
    else:
        caption = "No block has both a RAW and an RRW."
    xs = [float(p["x"]) for p in points if _number_value(p["x"]) and float(p["x"]) > 0]
    # RAW from 1 to 2,600 piled most blocks at the left edge: log it
    x_log = bool(xs) and max(xs) / max(min(xs), 1e-9) > 50
    return ChartSpec(
        "quadrant", f"Importance on {metric.lower()}",
        "x = RAW (what losing it costs), y = RRW (what fixing it buys)",
        "RAW", "RRW", series=points,
        marks=[{"kind": "cross", "x": 1.0, "y": 1.0}],
        caption=caption,
        provenance={"metric": metric, "x_log": x_log})


def convergence(per_run: list[dict], field_name: str = "availability",
                target: float | None = None,
                label: str | None = None) -> ChartSpec:
    """C11: has the running mean settled; is the run long enough?"""
    values = _values(per_run, field_name)
    points: list[dict[str, Any]] = []
    # Welford's running mean and variance: one pass, not one per run
    mean = m2 = 0.0
    for i, value in enumerate(values, start=1):
        delta = value - mean
        mean += delta / i
        m2 += delta * (value - mean)
        sem = (m2 / (i - 1) / i) ** 0.5 if i > 1 else None
        points.append({"n": i, "mean": mean, "sem": sem})
    last = points[-1] if points else None
    fmt = value_format(field_name)
    median = _quartile(sorted(values), 0.5) if values else None

    def shown(value: float, digits: int) -> str:
        from raptor.api.insight.axes import format_value
        return format_value(value, fmt, digits)

    return ChartSpec(
        "convergence", "Has the run settled?",
        "the band is +/- 2 standard errors; it should stop moving",
        "runs", label or field_name, series=points,
        marks=([{"kind": "target", "value": target}] if target is not None else [])
        + ([{"kind": "median", "value": median, "label": "median"}]
           if median is not None else []),
        # as the Story says it: "± 0.25 points (95 %)", not "give or take
        # 0.24 %" (a spread of a percentage is in points)
        caption=(f"After {len(values):,} runs the mean {label or field_name} is "
                 f"{shown(float(last['mean']), 6)} ± "
                 + (f"{200 * float(last['sem']):.2g} points" if fmt == "percent"
                    else shown(2 * float(last['sem']), 2))
                 + " (2 standard errors, about 95 %)."
                 + (f" Median {shown(median, 6)}." if median is not None else "")
                 if last is not None and last["sem"] is not None
                 else "Not enough runs to estimate the error."),
        provenance={"runs": len(values), "measure": field_name,
                    "format": fmt})


_COST_PARTS = ("purchase", "operating", "sparing", "disposal")


def element_costs(report_data: list[Any]) -> list[dict[str, Any]]:
    """Mean cost per run of each block and spare pool, split into
    purchase, operating, sparing and disposal (a pool's cost is all
    `spare_pool`), from the engine's per-run cost reports.  Runs without
    cost analysis are skipped; [] when none has it."""
    totals: dict[str, dict[str, float]] = {}
    runs = 0
    for report in report_data:
        cost = getattr(report, "cost", None)
        if cost is None:
            continue
        runs += 1
        for block_id, row in (getattr(cost, "blocks", {}) or {}).items():
            parts = totals.setdefault(str(getattr(row, "name", block_id)), {})
            split = {k: float(getattr(row, k, 0.0) or 0.0) for k in _COST_PARTS}
            if not any(split.values()):          # only a total is known
                split["other"] = float(getattr(row, "total", 0.0) or 0.0)
            for key, value in split.items():
                parts[key] = parts.get(key, 0.0) + value
        for pool_id, row in (getattr(cost, "spare_pools", {}) or {}).items():
            parts = totals.setdefault(str(getattr(row, "name", pool_id)), {})
            parts["spare_pool"] = (parts.get("spare_pool", 0.0)
                                   + float(getattr(row, "total", 0.0) or 0.0))
    return [{"name": name, **{k: v / runs for k, v in parts.items()},
             "total": sum(parts.values()) / runs}
            for name, parts in totals.items()] if runs else []


def _named_rows(table: Any, columns: dict[str, str]) -> dict[str, dict[str, float]]:
    """{row name: {key: value}} for the columns present (`columns` maps a
    key to its lower-case header); the report's own "Total" row is left out."""
    names = [str(h).strip().lower() for h in table.headers]
    found = {key: names.index(head) for key, head in columns.items() if head in names}
    return {str(row[0]): {key: _float(row[i]) for key, i in found.items()}
            for row in table.rows if str(row[0]).lower() != "total"}


def element_costs_from_tables(tables: list[Any]) -> list[dict[str, Any]]:
    """The same rows as `element_costs` from a stored run's Final Results
    cost tables (`raptor.api.tables.result_tables`), which already hold the
    mean per run.  This is what the command line and MCP use."""
    by_key = {t.key: t for t in tables}
    out: dict[str, dict[str, Any]] = {}
    life = by_key.get("results.cost_life_cycle_cost")
    if life is not None:
        for name, parts in _named_rows(life, {k: k for k in _COST_PARTS}).items():
            out.setdefault(name, {"name": name}).update(parts)
    pools = (by_key.get("results.cost_sparepools")
             or next((t for k, t in by_key.items()
                      if "sparepool" in k.replace("_", "")), None))
    if pools is not None:
        for name, parts in _named_rows(pools, {"spare_pool": "total"}).items():
            out.setdefault(name, {"name": name}).update(parts)
    for item in out.values():
        item["total"] = sum(float(item.get(k) or 0.0)
                            for k in (*_COST_PARTS, "spare_pool"))
    return list(out.values())


def stored_element_costs(workspace: Any, run_id: str) -> list[dict[str, Any]]:
    """`element_costs_from_tables` for a stored run ([] without cost)."""
    from raptor.api.run_tables import bundle_from_run
    from raptor.api.tables import result_tables
    return element_costs_from_tables(result_tables(bundle_from_run(workspace, run_id)))


COST_CHARTS = ("cost-pareto", "cost-treemap", "cost-categories",
               "cost-cumulative", "cost-annual")
#: The cost charts drawn from the interim cost report
#: (`cost_series.stored_runs`), not from the end-of-run cost tables.
COST_SERIES_CHARTS = ("cost-categories", "cost-cumulative", "cost-annual")

#: Colour-blind-safe line colours for the cost categories.
COST_COLOURS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7",
                "#E69F00", "#56B4E9", "#F0E442", "#6B7785")


def _cost_lines_ok(cost_runs: list[dict[str, Any]] | None) -> bool:
    return bool(cost_runs and any(run.get("t") for run in cost_runs))


def cost_categories(cost_runs: list[dict[str, Any]] | None) -> ChartSpec:
    """Where a run's life-cycle cost goes, one bar per cost category.

    `cost_runs` is `cost_series.stored_runs` (or `load_runs`): the interim
    snapshots closed with each run's end-of-run totals, so the last
    part-interval of cost is counted."""
    if not _cost_lines_ok(cost_runs):
        return ChartSpec(
            "pareto", "Cost by category",
            "purchase, running, repair, maintenance, spares, resources, downtime and lost flow",
            "category", "cost a run",
            caption="No interim cost report was recorded. Tick Cost plot under "
                    "Run panel ▸ Recording and run again.",
            provenance={"runs": 0, "format": "money"})
    totals = cost_series.category_totals(cost_runs or [])
    whole = sum(totals.values())
    rows = sorted(((key, totals[key]) for key in cost_series.CATEGORIES
                   if totals[key] > 0), key=lambda item: -item[1])
    series: list[dict[str, Any]] = [
        {"name": cost_series.CATEGORY_LABELS[key], "value": value,
         "share": value / whole if whole else 0.0, "key": key}
        for key, value in rows]
    capacity = sum(value for key, value in rows
                   if key in cost_series.CAPACITY_CATEGORIES)
    caption = (f"Capital is {pct(series[0]['share'])} of the "
               f"{format_value(whole, 'money')} a run."
               if series and series[0]["key"] == "capital" else
               f"The largest category is {series[0]['name'].lower()} "
               f"({pct(series[0]['share'])})." if series else "No cost was recorded.")
    if series:
        caption += (f" Capacity-related costs (lost flow and system downtime) "
                    f"are {pct(capacity / whole if whole else 0.0)}.")
    return ChartSpec(
        "pareto", "Cost by category",
        "life-cycle cost a run; capacity-related rows are separated from "
        "the rest", "category", "cost a run", series=series,
        caption=caption,
        provenance={"runs": len(cost_runs or []), "format": "money",
                    "capacity_related": capacity / whole if whole else 0.0})


def cost_cumulative(cost_runs: list[dict[str, Any]] | None) -> ChartSpec:
    """Mean cumulative cost over the run horizon, one line per category."""
    if not _cost_lines_ok(cost_runs):
        return ChartSpec(
            "timeline", "Cumulative operating cost over time",
            "mean cumulative cost in each operating category",
            "simulated time", "cost", series=[],
            caption="No interim cost report was recorded. Tick Cost plot under "
                    "Run panel ▸ Recording and run again.",
            provenance={"runs": 0, "format": "money"})
    source = cost_runs or []
    grid, curves = cost_series.mean_curves(source)
    # Capital is a one-off at the start; on the same axis it flattens every
    # reliability and operating line.  It has its own bar in Cost by category.
    shown = tuple(key for key in cost_series.CATEGORIES if key != "capital")
    layers = [{"name": cost_series.CATEGORY_LABELS[key], "role": "line",
               "t": grid, "y": curves[key],
               "colour": COST_COLOURS[cost_series.CATEGORIES.index(key)],
               "width": 1.7}
              for key in shown if any(value > 0 for value in curves[key])]
    totals = cost_series.category_totals(source)
    whole = sum(totals[key] for key in shown)
    capacity = sum(totals[key] for key in cost_series.CAPACITY_CATEGORIES)
    caption = (f"Mean of {len(cost_runs or []):,} runs. At the end, operating "
               f"cost is {format_value(whole, 'money')} a run; "
               f"capacity-related costs are {format_value(capacity, 'money')} "
               f"({pct(capacity / whole if whole else 0.0)}). One-off capital "
               "is shown in Cost by category, not on this axis.")
    return ChartSpec(
        "timeline", "Cumulative operating cost over time",
        "mean cumulative cost in each operating category; one-off capital is "
        "separate; capacity-related costs are the lost-flow and "
        "system-downtime lines",
        "simulated time", "operating cost", series=layers, caption=caption,
        provenance={"runs": len(cost_runs or []), "format": "money",
                    "y_range": [0.0, max((max(curves[key]) for key in shown
                                          if curves[key]), default=1.0)],
                    "capacity_related": capacity / whole if whole else 0.0})


def cost_annual(cost_runs: list[dict[str, Any]] | None,
                year: float = 8760.0) -> ChartSpec:
    """Operating cost per whole year, as the across-run distribution.

    The one-off capital purchase is left out (it is the first bar of *Cost by
    category*), so the year-to-year movement is O&M, repair, spares and
    capacity-related cost rather than one enormous first year.
    """
    if not _cost_lines_ok(cost_runs):
        return ChartSpec(
            "fan", "Operating cost per year",
            "across the runs; one-off capital is separate", "year", "operating cost a year", series=[],
            caption="No interim cost report was recorded. Tick Cost plot under "
                    "Run panel ▸ Recording and run again.",
            provenance={"runs": 0, "format": "money"})
    rows = cost_series.annual_rows(cost_runs or [], year=year)
    if not rows:
        return ChartSpec(
            "fan", "Operating cost per year",
            "across the runs; one-off capital is separate", "year", "operating cost a year", series=[],
            caption="The runs are shorter than one whole year.",
            provenance={"runs": len(cost_runs or []), "format": "money"})
    first, last = rows[0], rows[-1]
    # the same words as the drawing: boxes for a few years, bands for more
    spread = ("the box holds the middle half of the runs, the whiskers 9 in 10, "
              "the bar is the median" if len(rows) < FAN_LINE_MIN else
              "the dark band holds the middle half of the runs, the light one "
              "9 in 10, the line is the median")
    caption = (f"{len(rows)} whole years; the median operating cost moves from "
               f"{format_value(first['p50'], 'money')} to "
               f"{format_value(last['p50'], 'money')}. One-off capital is not "
               "in these years.")
    return ChartSpec(
        "fan", "Operating cost per year",
        f"across the runs: {spread}; one-off capital is separate", "year",
        "operating cost a year", series=rows, caption=caption,
        provenance={"runs": len(cost_runs or []), "format": "money",
                    "years": len(rows), "excludes_capital": True})


def stored_importance(workspace: Any, run_id: str) -> dict[str, Any] | None:
    """The importance measures a stored run made, or None (its summary
    keeps them; `importance-quadrant` reads them)."""
    import json
    from raptor.api.run import run_dir
    path = run_dir(workspace, run_id) / "summary.json"
    try:
        found = json.loads(path.read_text()).get("importance")
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _float(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def cost_pareto(costs: list[dict[str, Any]], top: int | None = 10) -> ChartSpec:
    """C12: where the money goes: mean cost per run by element, from
    `element_costs` or `element_costs_from_tables`."""
    totals = {row["name"]: float(row.get("total") or 0.0) for row in costs}
    ranked = sorted(((n, v) for n, v in totals.items() if v > 0),
                    key=lambda kv: -kv[1])
    top = len(ranked) if top is None else top
    rows = ranked[:top]
    grand = sum(totals.values())
    note = cut_note(len(rows), len(ranked),
                    sum(v for _n, v in ranked[top:]) / grand if grand else None)
    return ChartSpec(
        "pareto", "Where the money goes",
        _with_note("mean cost per run, by element, largest first", note),
        "element", "cost",
        series=[{"name": name, "value": value,
                 "share": value / grand if grand else 0.0}
                for name, value in rows],
        caption=(f"{rows[0][0]} carries the largest cost "
                 f"({pct(rows[0][1] / grand)})."
                 if rows and grand else
                 "No element has a cost in this run." if costs else _needs("cost")),
        provenance={"items": len(ranked), "grand_total": grand, "format": "money",
                    "cut": [{"name": n, "value": v, "share": v / grand if grand else 0.0}
                            for n, v in ranked[top:]]})


def lost_capacity_pareto(attribution: dict[str, Any], top: int | None = 10) -> ChartSpec:
    """K1: who loses the energy beside who is down the longest."""
    capacity = attribution.get("capacity") or {}
    by_name = {row["name"]: row for row in attribution.get("elements") or []}
    ranked = sorted((row for row in capacity.get("elements") or []
                     if (row.get("share", {}).get("mean") or 0) > 0),
                    key=lambda row: -(row["share"]["mean"] or 0))
    top = len(ranked) if top is None else top
    rows = ranked[:top]
    note = cut_note(len(rows), len(ranked),
                    sum(row["share"]["mean"] or 0 for row in ranked[top:]))
    series = []
    for row in rows:
        downtime = (by_name.get(row["name"], {}).get("dtci", {}).get("mean")
                    or 0.0)
        series.append({"name": row["name"],
                       "value": row["share"]["mean"] or 0.0,
                       "downtime": downtime,
                       "lost_energy": row.get("lost_energy", {}).get("mean"),
                       "through_dependents":
                           row.get("through_dependents", {}).get("mean")})
    return ChartSpec(
        "capacity_pareto", "Who loses the capacity",
        _with_note("capacity share against downtime share, same element order",
                   note),
        "element", "share of the total", series=series,
        caption=_capacity_caption(rows, attribution),
        provenance={"items": len(ranked), "runs": attribution.get("runs", 0), "format": "percent",
                    "rule": capacity.get("rule", ""),
                    "cut": [{"name": r["name"], "value": r["share"]["mean"] or 0.0,
                             "downtime": by_name.get(r["name"], {}).get("dtci", {})
                             .get("mean") or 0.0} for r in ranked[top:]]})


def capacity_vs_downtime(attribution: dict[str, Any],
                         top: int | None = 20) -> ChartSpec:
    """K2: the #256 case in one picture: downtime share against lost flow."""
    capacity = attribution.get("capacity") or {}
    lost = {row["name"]: (row.get("share", {}).get("mean") or 0.0)
            for row in capacity.get("elements") or []}
    # an element that loses flow without ever taking the system down has no
    # downtime row: it sits on the left edge, the case this chart is for
    downtime = {row["name"]: (row.get("dtci", {}).get("mean") or 0.0)
                for row in attribution.get("elements") or []}
    points = [{"name": name, "x": downtime.get(name, 0.0), "y": share}
              for name, share in lost.items()
              if name != "(no single element)" and (share or downtime.get(name))]
    points.sort(key=lambda p: -(p["y"]))
    top = len(points) if top is None else top
    cut = points[top:]
    points = points[:top]
    return ChartSpec(
        "scatter", "Downtime share against lost capacity",
        _with_note("points far above the diagonal remove more capacity than "
                   "their downtime suggests",
                   cut_note(len(points), len(points) + len(cut))),
        "share of downtime",
        "share of lost capacity", series=points,
        marks=[{"kind": "diagonal"}],
        caption=(("Points above the diagonal are understated by availability "
                  "alone." if points else _no_capacity(attribution))
                 + ("" if not lost else
                    (f" {pct(sum(share for name, share in lost.items() if name in _NOT_A_CAUSE))}"
                     " of the lost flow is not attributed to a single element."
                     if any(name in _NOT_A_CAUSE for name in lost) else ""))),
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "items": len(points) + len(cut), "cut": cut})


def _capacity_verdict(capacity: dict[str, Any], target: float | None) -> str:
    """" Delivered 97 % ...: it met the capacity target" (or ""), for a
    caption that has no axis to draw the target on."""
    from raptor.api.insight.capacity_attribution import delivered_share, target_sentence
    text = target_sentence(delivered_share(capacity), target)
    return f" {text}" if text else ""


def capacity_waterfall(attribution: dict[str, Any],
                       capacity_target: float | None = None) -> ChartSpec:
    """K5: where the generated flow goes, one step per cause.

    Its axis is the share of the *lost* energy, so a capacity target has
    no line to sit on; with one, the caption says whether delivery met it."""
    capacity = attribution.get("capacity") or {}
    causes = capacity.get("causes") or {}
    total = sum((stat.get("mean") or 0.0) for stat in causes.values())
    steps = []
    running = 1.0
    for cause, stat in sorted(causes.items(),
                              key=lambda kv: -(kv[1].get("mean") or 0.0)):
        value = (stat.get("mean") or 0.0) / total if total else 0.0
        if value <= 0:
            continue
        steps.append({"name": CAUSE_LABELS.get(cause, cause), "cause": cause,
                      "value": value, "from": running, "to": running - value,
                      "share": value})
        running -= value
    marginal = capacity.get("rule") == "marginal"
    verdict = (_capacity_verdict(capacity, capacity_target) if steps else "")
    return ChartSpec(
        "waterfall", "Lost capacity by cause",
        ("each step is a cause's share of the summed marginal contributions"
         if marginal else "each step is a cause's share of the lost energy"),
        "cause", "share of lost energy", series=steps,
        caption=((f"The largest loss is {steps[0]['name'].lower()} "
                  f"({pct(steps[0]['value'])} of the summed contributions)."
                  if marginal else
                  f"The largest loss is {steps[0]['name'].lower()} "
                  f"({pct(steps[0]['value'])} of lost energy).")
                 if steps else _no_capacity(attribution)) + verdict,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "rule": capacity.get("rule", "")})


def annual_capacity_fan(periods: dict[str, Any],
                        target: float | None = None) -> ChartSpec:
    """K6b: capacity-weighted availability year by year (energy delivered /
    flow generated), beside the plain availability fan: with many parallel
    units a year can look fully available and still lose output."""
    capacity = periods.get("capacity") or {}
    spec = annual_fan({**periods, "by_index": capacity.get("by_index") or [],
                       "threshold": capacity.get("threshold")}, target=target)
    spec.title = "Capacity delivered year by year"
    spec.y_label = "capacity-weighted availability"
    rows = spec.series
    if not rows:
        spec.caption = _needs("capacity") + " (or the run is shorter than one year)"
    elif len(rows) >= FAN_LINE_MIN:
        spec.caption = (f"{len(rows)} years; the median share of capacity "
                        f"delivered moves from {pct(rows[0]['p50'])} to "
                        f"{pct(rows[-1]['p50'])}.")
    return spec


def _year_line(periods: dict[str, Any], target: float | None
               ) -> tuple[float | None, str]:
    """The line the year charts draw and its name: the Charts page's
    target when one is set (it said "no threshold was set" beside a
    target the other charts drew), else the summary's own threshold."""
    if target is not None:
        return target, "target"
    return periods.get("threshold"), "threshold"


def annual_fan(periods: dict[str, Any], target: float | None = None) -> ChartSpec:
    """K6: does a bad year happen, and does it get worse with age?"""
    rows = [row for row in periods.get("by_index") or []
            if row.get("n") and row.get("p50") is not None]
    series = [{"name": f"year {row['index']}", "index": row["index"],
               "p5": row.get("p5"), "p25": row.get("p25"),
               "p50": row.get("p50"), "p75": row.get("p75"),
               "p95": row.get("p95"), "n": row.get("n")}
              for row in rows]
    threshold, line = _year_line(periods, target)
    few = len(series) < FAN_LINE_MIN
    medians = ", ".join(pct(row["p50"]) for row in series)
    if not series:
        caption = "No complete year in this run."
    elif few:
        caption = (f"Only {len(series)} year{'s' if len(series) != 1 else ''}, "
                   f"too few to show a trend; median {medians}.")
    else:
        caption = (f"{len(series)} years; the median moves from "
                   f"{pct(series[0]['p50'])} to {pct(series[-1]['p50'])}.")
    runs = periods.get("runs") or 0
    dropped = periods.get("dropped_partial_periods") or 0
    if series and dropped and runs:
        # a 2.5-year run showed 2 years with no word about the half year
        caption += (" The part year at the end of each run is left out."
                    if dropped == runs else
                    f" {dropped:,} part years at the ends of runs are left out.")
    # plain words: "whiskers: P5-P95, box: P25-P75" was jargon
    return ChartSpec(
        "fan", "Availability year by year",
        ("across the runs: the box holds the middle half, the whiskers 9 in 10, "
         "the bar is the median" if few else
         "across the runs: the dark band holds the middle half, the light one "
         "9 in 10, the line is the median"), "year", "availability",
        series=series,
        marks=[{"kind": "target", "value": threshold, "label": line}]
        if threshold is not None else [],
        caption=caption,
        provenance={"runs": periods.get("runs", 0), "format": "percent",
                    "periods_per_run": periods.get("periods_per_run", 0)})


def annual_dots(periods: dict[str, Any], target: float | None = None) -> ChartSpec:
    """K7: all run-years as a dot plot against the guarantee."""
    pooled = periods.get("pooled") or {}
    threshold, line = _year_line(periods, target)
    n = int(pooled.get("n") or 0)
    quantile_fallback = False
    if pooled.get("dots"):
        values = [float(v) for v in pooled["dots"]]
        subtitle = ("each dot is one run-year" if len(values) == n else
                    f"each dot is {pct(1 / len(values))} of the {n} run-years")
    else:
        quantile_fallback = True
        # A run summarised before the equal-share dots: its quantiles are
        # not equal shares, so say what they are.
        quantiles = ("p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99")
        values = [pooled[key] for key in quantiles
                  if isinstance(pooled.get(key), (int, float))]
        subtitle = "each dot is one of the quantiles P1 to P99 of the run-years"
    dots = [{"value": v} for v in values]
    below = pooled.get("p_below")
    about = ""
    if target is not None and target != periods.get("threshold") and values:
        # equal-share dots: the share of dots below is the share of
        # run-years below, exactly when there is one dot per run-year
        below = sum(v < target for v in values) / len(values)
        about = "about " if quantile_fallback or len(values) != n else ""
    caption = (f"{about}{pct(below)} of run-years fall below the {line}."
               if isinstance(below, (int, float)) and threshold is not None else
               f"{pooled.get('n', 0)} run-years; no target was set.")
    if values:
        # a tall stack at the top had no scale: say how many it holds
        top = max(values)
        stacked = sum(1 for v in values if v == top)
        if stacked > 1:
            caption += (f" {stacked} of the {len(values)} dots are at "
                        f"{pct(top)}.")
    return ChartSpec(
        "dot_plot", "How often is a year below target?",
        subtitle, "availability",
        "run-years", series=[{"name": "run-years", "dots": dots}],
        marks=[{"kind": "target", "value": threshold, "label": line}]
        if threshold is not None else [],
        caption=caption,
        provenance={"runs": periods.get("runs", 0), "format": "percent",
                    "periods": pooled.get("n", 0), "below": below})


def _hierarchy_context(attribution: dict[str, Any] | None,
                       hierarchy: int | str | None
                       ) -> tuple[dict[str, Any], dict[str, Any] | None,
                                  dict[str, Any]]:
    """(scoped attribution, hierarchy row or None, scope metadata)."""
    source = attribution or {}
    row = hier.resolve(source, hierarchy) if hierarchy not in (None, "", 0) else None
    return hier.scoped(source, row), row, hier.scope_meta(source, row)


def _zero_hierarchies(attribution: dict[str, Any] | None) -> list[str]:
    return [row["name"] for row in hier.top_rows(attribution)
            if not float((row.get("dtci") or {}).get("mean") or 0.0)]


def scorecard_badge(row: dict[str, Any]) -> tuple[str, str] | None:
    """(tone, text) of a scorecard tile's baseline badge, or None without a
    baseline.  A glyph and a word carry the direction, not colour alone; less
    downtime share is better.  Tone: good, bad, neutral."""
    info = row.get("baseline")
    if not isinstance(info, dict):
        return None
    if info.get("status") == "new":
        return "neutral", "new (not in baseline)"
    points = float(info.get("delta") or 0.0) * 100.0
    if abs(points) < 0.05:
        return "neutral", "= no change"
    if points < 0:
        return "good", f"▼ {abs(points):.2g} pts better"
    return "bad", f"▲ {points:.2g} pts worse"


def scorecard_scale_text(x_max: float) -> str:
    """The end of the strip scale shared by the tiles, e.g. "37 %"."""
    points = x_max * 100.0
    return f"{points:.0f} %" if points >= 10 else f"{points:.2g} %"


def scorecard_strip(dots: Sequence[dict[str, Any]], x_max: float, width: float,
                    radius: float, levels: int = 4) -> list[tuple[float, int]]:
    """(x, level) for each quantile dot of a tile's per-run strip: x on the
    scale shared by every tile (0 to `x_max`, `width` wide); a dot that would
    touch the one before it in its row moves up a level."""
    out: list[tuple[float, int]] = []
    reach: list[float] = [-1e9] * levels
    for dot in dots:
        x = float(dot["value"]) / x_max * width if x_max > 0 else 0.0
        level = next((i for i in range(levels) if x - reach[i] >= 2 * radius + 1),
                     levels - 1)
        reach[level] = x
        out.append((x, level))
    return out


def hierarchy_scorecard(attribution: dict[str, Any], top: int | None = 8,  # noqa: C901
                        depth: int | None = 1,
                        baseline: list[dict[str, Any]] | None = None) -> ChartSpec:
    """P0: one tile per hierarchy at the chosen level, answer first.

    Each tile carries the per-run shares as quantile dots and, given a
    `baseline` (hierarchy rows of an earlier run, matched by id as in
    `hierarchy_delta`), the change in share against it."""
    found = hier.rows_at_depth(attribution, depth or 1)
    found.sort(key=lambda row: (-(float((row.get("dtci") or {}).get("mean") or 0.0)),
                                str(row.get("name") or "")))
    shown = found[:max(1, len(found) if top is None else top)]
    series = [{
        "name": row["name"], "path": hier.path_text(row),
        "value": float((row.get("dtci") or {}).get("mean") or 0.0),
        "sem": (row.get("dtci") or {}).get("sem"),
        "cause": row.get("main_cause"),
        "members": int(row.get("member_count") or 0),
        "starters": int(row.get("starter_count") or 0),
        "trials": [float(item.get("dtci") or 0.0)
                   for item in row.get("trials") or []],
    } for row in shown]
    # the hierarchies past `top`, for the all-items table
    cut = [{"name": row["name"], "path": hier.path_text(row),
            "value": float((row.get("dtci") or {}).get("mean") or 0.0),
            "sem": (row.get("dtci") or {}).get("sem"),
            "cause": row.get("main_cause"),
            "members": int(row.get("member_count") or 0),
            "starters": int(row.get("starter_count") or 0)}
           for row in found[len(shown):]]
    for entry in series:
        entry["dots"] = quantile_dots(entry["trials"], 25)
    dot_max = max((float(dot["value"]) for entry in series
                   for dot in entry["dots"]), default=0.0)
    if baseline:
        before = {int(item.get("id") or 0): item for item in
                  hier.rows_at_depth({"hierarchies": baseline}, depth or 1)}
        for row, entry in zip(shown, series, strict=True):
            was = before.get(int(row.get("id") or 0))
            entry["baseline"] = (
                {"status": "new"} if was is None else
                {"status": "same", "before": float((was.get("dtci") or {}).get("mean") or 0.0),
                 "delta": entry["value"] - float((was.get("dtci") or {}).get("mean") or 0.0)})
    zeros = [row["name"] for row in series if not row["value"]]
    if series and any(row["value"] for row in series):
        caption = (f"{series[0]['name']} holds the largest share of system "
                   f"downtime ({pct(series[0]['value'])}).")
    elif series:
        caption = "No hierarchy has downtime in these runs."
    else:
        caption = "This model has no hierarchy roll-up."
    remainder = hier.unassigned_share(attribution)
    if remainder > 0:
        caption += (f" {pct(remainder)} of system downtime is not inside a "
                    "hierarchy.")
    if len(found) > len(shown):
        caption += f" Showing {len(shown)} of {len(found)} hierarchies."
    if zeros and any(row["value"] for row in series):
        caption += (" No downtime in " + ", ".join(zeros) + "."
                    if len(zeros) > 1 else f" {zeros[0]} had no downtime.")
    return ChartSpec(
        "scorecard", "Hierarchy scorecard",
        "one card per hierarchy: share of system downtime, ±2 standard errors, "
        "members and starters"
        f" · {hier.depth_text(depth)}"
        + (" · badges compare with the baseline run" if baseline else ""),
        "", "share of system downtime",
        series=series, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "depth": depth, "dot_max": dot_max,
                    "has_baseline": bool(baseline), "items": len(found),
                    "cut": cut,
                    "scope": {"kind": "system", "name": "System",
                              "path": ["System"]}},
        scope={"kind": "system", "id": 0, "name": "System", "path": ["System"]})


def _levels(depth: int | None) -> int:
    """Depth inside a chosen hierarchy: None means 1 level down (the
    direct contents), 0 every level."""
    return 1 if depth is None else int(depth)


def hierarchy_pareto(attribution: dict[str, Any],
                     hierarchy: int | str | None = None,
                     top: int | None = 12, depth: int | None = 1) -> ChartSpec:
    """The hierarchy ranking, or the ranking inside one hierarchy."""
    source, row, meta = _hierarchy_context(attribution, hierarchy)
    if row is None:
        ranked = sorted((item for item in hier.rows_at_depth(source, depth or 1)
                         if float((item.get("dtci") or {}).get("mean") or 0.0) > 0),
                        key=lambda item: -(float((item["dtci"] or {}).get("mean") or 0.0)))
        top = len(ranked) if top is None else top
        rows = ranked[:top]
        series = [{"name": item["name"], "path": hier.path_text(item),
                   "value": float((item.get("dtci") or {}).get("mean") or 0.0),
                   "share": float((item.get("dtci") or {}).get("mean") or 0.0),
                   "sem": (item.get("dtci") or {}).get("sem"),
                   "causes": item.get("causes") or {},
                   "members": int(item.get("member_count") or 0),
                   "starters": int(item.get("starter_count") or 0)}
                  for item in rows]
        caption = (f"{series[0]['name']} causes the largest share "
                   f"({pct(series[0]['value'])} of system downtime)."
                   if series else "No hierarchy has downtime in these runs.")
        zeros = [item for item in hier.rows_at_depth(source, depth or 1)
                 if not float((item.get("dtci") or {}).get("mean") or 0.0)]
        remainder = hier.unassigned_share(attribution)
        if remainder > 0:
            caption += (f" {pct(remainder)} of system downtime is not inside a "
                        "hierarchy.")
        if len(ranked) > len(rows):
            caption += f" Showing {len(rows)} of {len(ranked)} hierarchies."
        if zeros:
            caption += " No downtime in " + ", ".join(
                str(item["name"]) for item in zeros) + "."
        subtitle = (f"share of system downtime, stacked by main cause"
                    f" · {hier.depth_text(depth)}")
    else:
        parts = [part for part in hier.child_parts(source, row, _levels(depth))
                 if float(part.get("share") or 0.0) > 0]
        all_parts = list(parts)
        total_parts = len(parts)
        top = total_parts if top is None else top
        parts = parts[:top]
        series = [{"name": part["name"], "path": part.get("path", part["name"]),
                   "value": float(part.get("share") or 0.0),
                   "kind": part.get("kind", "element"), "sem": None,
                   "causes": (part.get("row") or part.get("element") or {})
                   .get("causes") or {},
                   "members": int((part.get("row") or {}).get("member_count") or 1),
                   "starters": int((part.get("row") or {}).get("starter_count") or 1)}
                  for part in parts]
        caption = (f"{series[0]['name']} causes the largest share "
                   f"({pct(series[0]['value'])} of {row['name']}'s downtime)."
                   if series else f"No element inside {row['name']} caused downtime.")
        if total_parts > len(series):
            caption += f" Showing {len(series)} of {total_parts} parts."
        subtitle = (f"share of {row['name']}'s downtime, stacked by main cause"
                    f" · {hier.levels_text(_levels(depth))}")
    spec = ChartSpec(
        "pareto", "Hierarchy Pareto" if row is None else f"Pareto inside {row['name']}",
        subtitle, "hierarchy" if row is None else "element",
        "share of downtime", series=series, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "items": len(ranked) if row is None else total_parts,
                    "cut": ([{"name": item["name"], "path": hier.path_text(item),
                              "value": float((item.get("dtci") or {}).get("mean") or 0.0),
                              "share": float((item.get("dtci") or {}).get("mean") or 0.0)}
                             for item in ranked[top:]] if row is None else
                            [{"name": part["name"],
                              "path": part.get("path", part["name"]),
                              # the number the bars plot (a fraction of
                              # the parent), as the drawn rows carry
                              "value": float(part.get("share") or 0.0),
                              "share": float(part.get("share") or 0.0)}
                             for part in all_parts[top:]]),
                    "row_kind": "hierarchy" if row is None else "element"})
    return hier.annotate(spec, meta)


def hierarchy_treemap(attribution: dict[str, Any],  # noqa: C901  # noqa: C901
                      hierarchy: int | str | None = None, *,
                      width: float = 1000.0, height: float = 600.0,
                      top: int = 200, depth: int | None = None) -> ChartSpec:
    """Nested treemap: hierarchy groups all the way down, elements at the leaves.

    Each hierarchy gets a labelled group; its children are the next level's
    hierarchies or its direct elements.  A parent and its child are never
    both drawn as leaf area, so a nested model cannot double count.
    """
    from raptor.api.insight.treemap import layout

    selected = hier.resolve(attribution, hierarchy) if hierarchy not in (None, 0, "") else None
    tops = ([selected] if selected is not None else
            [row for row in hier.top_rows(attribution)
             if float((row.get("dtci") or {}).get("mean") or 0.0) > 0])
    tops = [row for row in tops
            if float((row.get("dtci") or {}).get("mean") or 0.0) > 0]
    zeros = [] if selected is not None else _zero_hierarchies(attribution)
    if not tops:
        return ChartSpec("treemap", "Hierarchy treemap",
                         "area = share of system downtime; colour = main cause",
                         "width", "height", series=[],
                         caption="No hierarchy has downtime in these runs.",
                         provenance={"format": "percent"})
    tops.sort(key=lambda row: -(float((row["dtci"] or {}).get("mean") or 0.0)))
    assigned = (float((selected["dtci"] or {}).get("mean") or 0.0)
                if selected is not None else
                sum(float((row["dtci"] or {}).get("mean") or 0.0)
                    for row in tops))
    system = (attribution or {}).get("system") or {}
    whole = (selected is None and
             float((system.get("red_time") or {}).get("mean") or 0.0) > 0)
    if whole and assigned < 1.0 - 1e-9:
        tops.append({"id": 0, "name": "Not in a hierarchy", "parent": 0,
                     "depth": 1, "path": ["Not in a hierarchy"],
                     "dtci": {"mean": 1.0 - assigned}, "causes": {},
                     "main_cause": "other", "member_count": 0,
                     "starter_count": 0})
        tops.sort(key=lambda row: -float((row.get("dtci") or {}).get("mean") or 0.0))
    total = 1.0 if whole else (assigned or 1.0)
    series: list[dict[str, Any]] = []
    limit = int(depth) if depth else 0

    def add_group(row: dict[str, Any], rect: Any, level: int,
                  parent: str) -> None:
        share = float((row.get("dtci") or {}).get("mean") or 0.0)
        series.append({
            "name": row["name"], "path": hier.path_text(row),
            **rect.to_dict(), "value": share,
            "share": share / total if total else 0.0,
            "cause": row.get("main_cause"),
            "is_group": row.get("name") != "Not in a hierarchy",
            "group": parent, "depth": level,
            "members": int(row.get("member_count") or 0),
            "starters": int(row.get("starter_count") or 0),
        })
        if row.get("name") == "Not in a hierarchy":
            return
        if limit and level + 1 >= limit:
            return
        parts = [part for part in hier.child_parts(attribution, row)
                 if float(part.get("share") or 0.0) > 0]
        if not parts:
            return
        strip = 72.0 if level == 0 else 48.0
        inner = (max(1.0, rect.w - 4), max(1.0, rect.h - strip))
        cells = layout(parts, inner[0], inner[1],
                       value=lambda part: float(part.get("share") or 0.0))
        for part, cell in zip(parts, cells, strict=True):
            box = {"x": rect.x + cell.x + 2,
                   "y": rect.y + cell.y + strip,
                   "w": cell.w, "h": cell.h}
            value = float(part.get("share") or 0.0) * share
            if part["kind"] == "hierarchy" and part.get("row") is not None:
                add_group(part["row"], type(rect)(**box), level + 1, row["name"])
                continue
            item = part.get("row") or {}
            series.append({
                "name": part["name"], "value": value,
                "share": value / total if total else 0.0,
                "cause": part.get("cause") or (item.get("main_cause")
                                               if isinstance(item, dict) else None),
                **box, "group": row["name"], "path": part.get("path", ""),
                "depth": level + 1,
            })

    rects = layout(tops, width, height,
                   value=lambda row: float((row["dtci"] or {}).get("mean") or 0.0))
    for row, rect in zip(tops, rects, strict=True):
        add_group(row, rect, 0, "")
    if selected is not None:
        children = [row for row in series
                    if row.get("group") == selected["name"]]
        caption = (f"Inside {selected['name']}: {children[0]['name']} is the "
                   f"largest part ({pct(float(children[0].get('share') or 0.0))})."
                   if children else
                   f"{selected['name']} has no downtime below it in this run.")
    else:
        caption = ("Most system downtime is not inside a hierarchy."
                    if tops[0]["name"] == "Not in a hierarchy" else
                    f"{tops[0]['name']} is the largest hierarchy "
                    f"({pct(float((tops[0]['dtci'] or {}).get('mean') or 0.0))} "
                    "of system downtime).")
    if zeros:
        caption += " No downtime in " + ", ".join(zeros) + "."
    spec = ChartSpec(
        "treemap", "Hierarchy treemap",
        "outer blocks are hierarchies, inner cells are elements; area = share "
        "of system downtime; colour = main cause"
        f" · {hier.depth_text(depth, nested=True)}", "width", "height",
        series=series, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "measure": "system downtime", "group_labels": {},
                    "zero": zeros, "max_depth": max(
                        (int(row.get("depth") or 0) for row in series), default=0)})
    return hier.annotate(spec, hier.scope_meta(attribution, selected))


_NUMBER_WORDS = {4: "four", 5: "five", 6: "six", 12: "twelve"}


def _too_many(chart: str, count: int) -> ValueError:
    limit = COMPARE_LIMITS[chart]
    return ValueError(f"hierarchies takes at most {_NUMBER_WORDS.get(limit, limit)}"
                      f" for this chart; got {count}")


def _ranking_rows(available: list[dict[str, Any]],
                  selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every hierarchy a comparison could show, largest first, marking the
    ones drawn: the all-items table."""
    drawn = {int(row.get("id") or 0) for row in selected}
    return [{"name": str(row.get("name") or ""), "path": hier.path_text(row),
             "value": float((row.get("dtci") or {}).get("mean") or 0.0),
             "shown": int(row.get("id") or 0) in drawn} for row in available]


def hierarchy_overlay(attribution: dict[str, Any],
                      hierarchies: Sequence[int | str] | None = None,
                      top: int | None = 4, depth: int | None = 1) -> ChartSpec:
    """Cumulative system downtime caused by each selected hierarchy."""
    available = [row for row in sorted(
        hier.rows_at_depth(attribution, depth or 1),
        key=lambda row: -(float((row.get("dtci") or {}).get("mean") or 0.0)))
        if float((row.get("dtci") or {}).get("mean") or 0.0) > 0]
    selected = hier.resolve_many(attribution, hierarchies) if hierarchies \
        else available[:(COMPARE_LIMITS["hierarchy-overlay"] if top is None
                         else top)]
    if not selected:
        raise ValueError("no hierarchy has downtime to compare")
    if len(selected) > COMPARE_LIMITS["hierarchy-overlay"]:
        raise _too_many("hierarchy-overlay", len(selected))
    overlap = hier.overlaps(selected)
    if overlap:
        raise ValueError(
            "these hierarchies overlap (a parent includes its child): "
            + ", ".join(f"{a} / {b}" for a, b in overlap)
            + ". Choose different branches, or use one level at a time.")
    layers, _end = hier.cumulative_layers(attribution, selected)
    if not layers:
        raise ValueError("this run has no per-hierarchy outage timeline: "
                         "run again with downtime attribution and keep the "
                         "event log")
    units = str(attribution.get("units") or "")
    unit = (" h" if units.lower().startswith("hour") else
            f" {units}" if units else "")
    y_max = max(max(float(v) for v in layer["y"]) for layer in layers)
    y_max = max(y_max * 1.05, 1e-9)
    end_values = [(layer["name"], float(layer["y"][-1])) for layer in layers
                  if layer.get("name") != "system"]
    for layer in layers:
        # direct label at the line end replaces the key (one name, in the
        # line's own colour, beside the line it names)
        layer["end_value"] = f"{float(layer['y'][-1]):,.3g}{unit}"
        layer["end_text"] = f"{layer.get('name')} {layer['end_value']}"
        layer["key"] = False
    caption = ("By the end of the run, " + ", ".join(
        f"{name} caused {value:,.3g}{unit}" for name, value in end_values) + ".")
    if not hierarchies and len(available) > len(selected):
        caption += (f" Showing the top {len(selected)} of {len(available)} "
                    "hierarchies; use Compare to choose.")
    names = [str(row["name"]) for row in selected]
    spec = ChartSpec(
        "timeline", "When did each hierarchy's downtime build up?",
        "cumulative system downtime caused by each hierarchy; system is the "
        "dashed reference"
        f" · {hier.depth_text(depth)}", f"simulated time ({units})" if units
        else "simulated time",
        f"cumulative downtime ({units})" if units else "cumulative downtime",
        series=layers,
        caption=caption,
        provenance={"runs": attribution.get("runs", 0), "y_range": [0.0, y_max],
                    "time_units": units, "format": "", "hierarchies": names,
                    "end_labels": True,
                    "ranking": _ranking_rows(available, selected)},
        scope={"kind": "selection", "id": 0,
               "name": (f"{len(names)} hierarchy" if len(names) == 1
                        else f"{len(names)} hierarchies"),
               "path": names})
    return spec


def _compared_rows(attribution: dict[str, Any],
                   hierarchies: Sequence[int | str] | None, top: int | None,
                   depth: int | None, chart: str = "hierarchy-small-multiples") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The hierarchies a comparison chart shows, and every one it could."""
    available = [row for row in hier.rows_at_depth(attribution, depth or 1)
                 if float((row.get("dtci") or {}).get("mean") or 0.0) > 0]
    selected = hier.resolve_many(attribution, hierarchies) if hierarchies \
        else (available or hier.rows_at_depth(attribution, depth or 1))[
            :max(1, COMPARE_LIMITS[chart] if top is None else top)]
    if not selected:
        raise ValueError("this model has no hierarchies")
    if len(selected) > COMPARE_LIMITS[chart]:
        raise _too_many(chart, len(selected))
    overlap = hier.overlaps(selected)
    if overlap:
        raise ValueError(
            "these hierarchies overlap (a parent includes its child): "
            + ", ".join(f"{a} / {b}" for a, b in overlap)
            + ". Choose different branches, or use one level at a time.")
    return selected, (available or hier.rows_at_depth(attribution, depth or 1))


def hierarchy_small_multiples(attribution: dict[str, Any],
                              hierarchies: Sequence[int | str] | None = None,
                              top: int | None = 4,
                              depth: int | None = 1) -> ChartSpec:
    """One dot-plot panel per hierarchy: each dot is a run's share."""
    selected, available = _compared_rows(attribution, hierarchies, top, depth)
    facets = []
    for row in selected:
        values = hier.per_run(attribution, row)
        facet = trial_outcome(values, "availability",
                              label="share of system downtime")
        facet.title = str(row["name"])
        facet.subtitle = "share of system downtime"
        facet.scope = hier.scope_meta(attribution, row)
        facets.append(facet)
    tagged = [hier.is_tag(row) for row in selected]
    if all(tagged):
        noun, panel = ("tag" if len(facets) == 1 else "tags"), "one panel per tag"
    elif any(tagged):
        noun, panel = "hierarchies and tags", "one panel per hierarchy or tag"
    else:
        noun = "hierarchy" if len(facets) == 1 else "hierarchies"
        panel = f"one panel per hierarchy at {hier.depth_text(depth)}"
    caption = f"{len(facets)} {noun}; each dot is one run's share of system downtime."
    if not hierarchies and len(available) > len(selected):
        caption += (f" Showing the top {len(selected)} of {len(available)} "
                    "hierarchies; use Compare to choose.")
    # A shared x-range makes equal positions comparable across panels.
    shared = [float(dot.get("value")) for facet in facets
              for dot in (facet.series[0].get("dots") or [])
              if isinstance(dot.get("value"), (int, float))]
    if shared:
        from raptor.api.insight.axes import nice_range
        low, high = nice_range(min(shared), max(shared), 5, share=True)
        pad = max(1e-6, (high - low) * 0.07)
        for facet in facets:
            facet.provenance["x_range"] = (low - pad, high + pad)
    return ChartSpec(
        "small_multiples", "Is the hierarchy ranking stable?",
        f"{panel}; each dot is a run",
        "share of system downtime", "runs", facets=facets, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "hierarchies": [row["name"] for row in selected],
                    "ranking": _ranking_rows(available, selected)},
        scope={"kind": "selection", "id": 0,
               "name": f"{len(facets)} {noun}",
               "path": [row["name"] for row in selected]})


def hierarchy_hops(attribution: dict[str, Any],
                   hierarchies: Sequence[int | str] | None = None,
                   top: int | None = 4, depth: int | None = 1,
                   frame: int | None = None) -> ChartSpec:
    """Hypothetical outcome plot: which hierarchy is worse, run by run.

    `frame` None is the static summary (one quantile dot per slice of the
    runs, per hierarchy: what every report, CLI and MCP surface draws).  An
    integer is one run's shares on the same fixed axis, which the GUI steps
    through to show how often the ranking flips (`hops_frame`)."""
    selected, available = _compared_rows(attribution, hierarchies, top, depth,
                                         "hierarchy-hops")
    trials = [row.get("trials") or [] for row in selected]
    count = min((len(t) for t in trials), default=0)
    # a run with no system outage has no share (the same rule as per_run)
    keep = [i for i in range(count)
            if not ("system_red_time" in trials[0][i]
                    and not trials[0][i]["system_red_time"])]
    rows: list[dict[str, Any]] = []
    for row, own in zip(selected, trials, strict=True):
        runs = [float(own[i].get("dtci") or 0.0) for i in keep]
        rows.append({"name": str(row["name"]), "value": _mean(runs) or 0.0,
                     "runs": runs, "dots": quantile_dots(runs), "first": 0})
    rows.sort(key=lambda r: -float(r["value"]))
    for i in range(len(keep)):
        shares = [float(r["runs"][i]) for r in rows]
        if max(shares) > 0:
            rows[shares.index(max(shares))]["first"] += 1
    n = len(keep)
    lead = rows[0]
    if len(rows) > 1 and n:
        caption = (f"{lead['name']} causes the most downtime in "
                   f"{lead['first']} of {n} runs.")
    else:
        caption = (f"{lead['name']}: mean share of system downtime "
                   f"{pct(float(lead['value']))} over {n} runs.")
    from raptor.api.insight.axes import nice_range
    biggest = max((v for r in rows for v in r["runs"]), default=0.0)
    high = nice_range(0.0, max(biggest, 1e-6), 5, share=True)[1]
    spec = ChartSpec(
        "hops", "Which hierarchy is worse, run by run?",
        f"each run's share of system downtime at {hier.depth_text(depth)}; "
        "the dashed line is each hierarchy's mean",
        "share of system downtime", "hierarchy", series=rows, caption=caption,
        provenance={"runs": n, "format": "percent", "frame": None,
                    "x_range": (0.0, high), "lead": lead["name"], "summary": caption,
                    "ranking": _ranking_rows(available, selected),
                    "hierarchies": [r["name"] for r in rows]},
        scope={"kind": "selection", "id": 0,
               "name": (f"{len(rows)} hierarchy" if len(rows) == 1
                        else f"{len(rows)} hierarchies"),
               "path": [r["name"] for r in rows]})
    return hops_frame(spec, frame) if frame is not None else spec


def hops_frame(spec: ChartSpec, index: int | None) -> ChartSpec:
    """The same chart showing run `index` (0-based; None: the summary)."""
    if spec.kind != "hops":
        return spec
    runs = int(spec.provenance.get("runs") or 0)
    if index is None or runs <= 0:
        provenance = dict(spec.provenance, frame=None)
        return replace(spec, provenance=provenance)
    index = max(0, min(runs - 1, int(index)))
    ranked = sorted(spec.series, key=lambda r: -float(r["runs"][index]))
    parts = ", ".join(f"{r['name']} {pct(float(r['runs'][index]))}" for r in ranked)
    summary = str(spec.provenance.get("summary") or spec.caption)
    return replace(spec, caption=f"Run {index + 1} of {runs}: {parts}. {summary}",
                   provenance=dict(spec.provenance, frame=index))


def hierarchy_delta(attribution: dict[str, Any],
                    baseline: list[dict[str, Any]] | None,
                    top: int | None = 10, depth: int | None = 1,
                    baseline_label: str | None = None) -> ChartSpec:
    """Change in each hierarchy's downtime against a baseline run."""
    if not baseline:
        raise ValueError(
            "hierarchy-delta needs a baseline run with a hierarchy roll-up; "
            "pass compare_to (or set a baseline in the Charts tab)")
    current = {int(row.get("id") or 0): row for row in
               hier.rows_at_depth(attribution, depth or 1)}
    before = {int(row.get("id") or 0): row for row in
              hier.rows_at_depth({"hierarchies": baseline}, depth or 1)}
    keys = sorted(set(current) | set(before))
    series = []
    for key in keys:
        now = current.get(key)
        was = before.get(key)
        a = float(((was or {}).get("red_time") or {}).get("mean") or 0.0)
        b = float(((now or {}).get("red_time") or {}).get("mean") or 0.0)
        a_sem = ((was or {}).get("red_time") or {}).get("sem")
        b_sem = ((now or {}).get("red_time") or {}).get("sem")
        sem = (math.sqrt(float(a_sem) ** 2 + float(b_sem) ** 2)
               if isinstance(a_sem, (int, float))
               and isinstance(b_sem, (int, float)) else None)
        series.append({"name": now["name"] if now else (was or {}).get("name", str(key)),
                       "path": hier.path_text(now or was or {}), "before": a if was else None,
                       "after": b if now else None, "value": b - a,
                       "sem": sem,
                       "share_before": float(((was or {}).get("dtci") or {}).get("mean") or 0.0),
                       "share_after": float(((now or {}).get("dtci") or {}).get("mean") or 0.0),
                       "status": "same" if now and was else
                                 "added" if now else "removed",
                       "cause": (now or was or {}).get("main_cause")})
    series.sort(key=lambda row: -abs(float(row["value"])))
    total_rows = len(series)
    top = total_rows if top is None else top
    cut = series[max(1, top):]       # for the all-items table
    series = series[:max(1, top)]
    if not series:
        raise ValueError("no hierarchy is present in both runs")
    top_row = series[0]
    units = str(attribution.get("units") or "")
    unit = (" h" if units.lower().startswith("hour") else
            f" {units}" if units else "")
    sem = top_row.get("sem")
    baseline_name = (f"baseline {baseline_label}" if baseline_label
                     else "the baseline")
    if top_row["value"] > 0:
        caption = (f"{top_row['name']} is down "
                   f"{_quantity(top_row['value'], unit)} a run more than "
                   f"{baseline_name}")
    elif top_row["value"] < 0:
        caption = (f"{top_row['name']} is down "
                   f"{_quantity(top_row['value'], unit)} a run less than "
                   f"{baseline_name}")
    else:
        caption = (f"{top_row['name']} has the same downtime as "
                   f"{baseline_name}")
    if isinstance(sem, (int, float)):
        caption += f" (± {2 * float(sem):,.2g}{unit})"
    caption += "."
    if total_rows > len(series):
        caption += f" Showing {len(series)} of {total_rows} hierarchies."
    spec = ChartSpec(
        "delta", "Hierarchy change against baseline",
        "change in downtime a run; bars right are worse"
        f" · {hier.depth_text(depth)}",
        f"change in downtime ({units})" if units else "change in downtime",
        "hierarchy", series=series,
        caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "",
                    "units": units, "baseline": "stored run",
                    "items": total_rows, "cut": cut},
        baseline={"rows": len(before), "label": baseline_label or "baseline"},
        scope={"kind": "system", "id": 0, "name": "System",
               "path": ["System"]})
    return hier.annotate(spec, hier.scope_meta(attribution, None))


def hierarchy_heatmap(attribution: dict[str, Any],
                      hierarchy: int | str | None = None,
                      depth: int | None = 1) -> ChartSpec:
    """Hierarchy × cause matrix, one level at a time.

    With a scope, the rows are that hierarchy's direct children (child
    hierarchies and elements), normalised within it.  At system level the
    rows are the chosen depth's hierarchies; parent and child rows are never
    drawn together, so a nested model does not read as double counting.
    """
    source, row, meta = _hierarchy_context(attribution, hierarchy)
    matrix: list[dict[str, Any]] = []
    if row is not None:
        parts = [part for part in hier.child_parts(source, row, _levels(depth))
                 if float(part.get("share") or 0.0) > 0]
        for part in parts:
            item = part.get("row") or part.get("element") or {}
            causes = item.get("causes") or {}
            matrix.append({"name": part["name"], "path": part.get("path", ""),
                           "kind": part.get("kind", "element"),
                           "cells": {cause: float(value) for cause, value in causes.items()},
                           "total": float(part.get("share") or 0.0)})
        columns = [cause for cause in CAUSES
                   if any(row["cells"].get(cause, 0.0) > 0 for row in matrix)]
        caption = (f"{matrix[0]['name']} is mostly "
                   f"{CAUSE_LABELS.get(max(matrix[0]['cells'],
                                          key=matrix[0]['cells'].get), '').lower()}."
                   if matrix and matrix[0]["cells"] else
                   f"No element inside {row['name']} caused downtime.")
    else:
        found = [item for item in hier.rows_at_depth(source, depth or 1)
                 if float((item.get("dtci") or {}).get("mean") or 0.0) > 0]
        found.sort(key=lambda item: -(float((item.get("dtci") or {}).get("mean") or 0.0)))
        for item in found:
            causes = item.get("causes") or {}
            matrix.append({"name": item["name"], "path": hier.path_text(item),
                           "cells": {cause: float(value) for cause, value in causes.items()},
                           "total": float((item.get("dtci") or {}).get("mean") or 0.0)})
        columns = [cause for cause in CAUSES
                   if any(row["cells"].get(cause, 0.0) > 0 for row in matrix)]
        caption = (f"{matrix[0]['name']} is mostly "
                   f"{CAUSE_LABELS.get(max(matrix[0]['cells'],
                                          key=matrix[0]['cells'].get), '').lower()}."
                   if matrix and matrix[0]["cells"] else
                   "No hierarchy has downtime in these runs.")
        remainder = hier.unassigned_share(attribution)
        if remainder > 0:
            caption += (f" {pct(remainder)} of system downtime is not inside a "
                        "hierarchy.")
    spec = ChartSpec(
        "heatmap", "Hierarchy by cause" if row is None else f"Causes inside {row['name']}",
        "each row adds to 100 % of that hierarchy's downtime"
        f" · {hier.levels_text(_levels(depth)) if row is not None else hier.depth_text(depth)}", "cause",
        "hierarchy", series=matrix, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "row_label": "hierarchy",
                    "columns": [{"key": cause, "label": CAUSE_LABELS.get(cause, cause)}
                                for cause in columns],
                    "scope": meta, "depth": depth})
    return hier.annotate(spec, meta)


def hierarchy_phase_heatmap(attribution: dict[str, Any],
                            hierarchy: int | str | None = None,
                            depth: int | None = 1) -> ChartSpec:
    """Hierarchy × phase matrix: share of each hierarchy's downtime by phase.

    This is the "which hierarchy behaves differently, and when" view.  It
    holds one hierarchy level at a time (the chosen depth), and a scope
    shows that hierarchy's own phase mix.  The cells are shares of the
    hierarchy's downtime, not phase availability: a long phase collects more
    downtime for the same failure rate.
    """
    if not attribution.get("phases"):
        raise ValueError("the hierarchy phase heatmap needs a phased model: "
                         "this one has no mission phases")
    if (any(float((item.get("dtci") or {}).get("mean") or 0.0) > 0
            for item in attribution.get("hierarchies") or [])
            and all(not item.get("phase_shares")
                    for item in attribution.get("hierarchies") or [])):
        raise ValueError("this run has no per-hierarchy phase breakdown: "
                         "rerun it with phases and attribution on")
    source, row, meta = _hierarchy_context(attribution, hierarchy)
    phase_order = [str(item.get("name"))
                   for item in attribution.get("phases") or []]
    phase_mission = {str(item.get("name")): item.get("mission")
                     for item in attribution.get("phases") or []}
    chosen = ([row] if row is not None else
              [item for item in hier.rows_at_depth(source, depth or 1)
               if float((item.get("dtci") or {}).get("mean") or 0.0) > 0])
    matrix: list[dict[str, Any]] = []
    for item in chosen:
        shares = item.get("phase_shares") or {}
        cells = {name: float(shares.get(name) or 0.0)
                 for name in phase_order if shares.get(name)}
        if not cells:
            continue
        matrix.append({"name": item["name"], "path": hier.path_text(item),
                       "kind": "hierarchy", "cells": cells,
                       "total": float((item.get("dtci") or {}).get("mean") or 0.0)})
    columns = [name for name in phase_order
               if any(row["cells"].get(name, 0.0) > 0 for row in matrix)]
    if matrix and columns:
        weightiest = max(matrix, key=lambda row: float(row.get("total") or 0.0))
        phase = max(weightiest["cells"],
                    key=lambda name: weightiest["cells"].get(name, 0.0))
        share = weightiest['cells'][phase]
        caption = (f"{weightiest['name']} holds the most hierarchy downtime; "
                   + ("all of it " if share >= 0.999 else "most of it ")
                   + f"is in {phase} ({pct(share)} of its downtime).")
    elif chosen and all(not item.get("phase_shares") for item in chosen):
        caption = ("No hierarchy with downtime in this run has a per-phase "
                   "breakdown; rerun with phases and attribution on.")
    elif row is not None:
        caption = f"No downtime inside {row['name']} is split by phase."
    else:
        caption = "No hierarchy has downtime in these runs."
    spec = ChartSpec(
        "heatmap",
        "Hierarchy by phase" if row is None else f"Phases inside {row['name']}",
        "share of each hierarchy's downtime in each phase; rows add to 100 %"
        f" · {hier.depth_text(depth)}",
        "phase", "hierarchy", series=matrix, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "row_label": "hierarchy",
                    "columns": [{"key": name,
                                 "label": name + (" (non-mission)"
                                                  if phase_mission.get(name) is False
                                                  else "")}
                                for name in columns],
                    "row_kind": "hierarchy", "scope": meta, "depth": depth})
    return hier.annotate(spec, meta)


def hierarchy_anatomy(attribution: dict[str, Any],
                      hierarchy: int | str | None = None) -> ChartSpec:
    """Outage paths grouped by hierarchy at the top."""
    source, row, meta = _hierarchy_context(attribution, hierarchy)
    if row is not None:
        spec = outage_anatomy(source)
        spec.title = f"Outage paths inside {row['name']}"
        spec.subtitle = "first step: the element; next: what kept it down"
        return hier.annotate(spec, meta)
    grouped: dict[tuple[str, str], list[float]] = {}
    totals: dict[str, float] = {}
    for seq in source.get("sequences") or []:
        first = (seq.get("steps") or [{}])[0]
        hierarchy = (first.get("hierarchy_path") or first.get("hierarchy")
                     or "(no hierarchy)")
        cause = first.get("cause") or "other"
        entry = grouped.setdefault((hierarchy, cause), [0.0, 0.0])
        entry[0] += float(seq.get("share") or 0.0)
        entry[1] += int(seq.get("outages") or 0)
        totals[hierarchy] = totals.get(hierarchy, 0.0) + float(seq.get("share") or 0.0)
    from raptor.api.insight.anatomy import icicle
    cells = icicle([
        {"steps": [{"element": hierarchy, "cause": cause}],
         "share": values[0], "outages": values[1]}
        for (hierarchy, cause), values in
        sorted(grouped.items(), key=lambda kv: -kv[1][0])
    ], max_depth=1)
    series = [{"column": cell.depth, "label": cell.element, "cause": cell.cause,
               "value": cell.height, "outages": cell.outages}
              for cell in cells]
    return ChartSpec(
        "icicle", "Outage paths across hierarchies",
        "each block is a hierarchy; colour = what started its outages",
        "hierarchy", "share of downtime", series=series,
        caption=(f"{max(totals, key=lambda name: totals[name])} starts the "
                 f"largest share ({pct(max(totals.values()))} of downtime)."
                 if totals else "No outage was recorded."),
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "single_column": True, "row_kind": "hierarchy"},
        scope={"kind": "system", "id": 0, "name": "System", "path": ["System"]})


def capacity_sankey(attribution: dict[str, Any],  # noqa: C901 - two shapes, one flow
                    hierarchy: int | str | None = None,
                    hierarchies: Sequence[int | str] | None = None, *,
                    by_hierarchy: bool = True,
                    show_delivered: bool = True,
                    capacity_target: float | None = None) -> ChartSpec:
    """Generated flow to delivered, lost, hierarchy and cause.

    One hierarchy scopes the chart; several siblings compare their share of
    the lost flow.  A parent with its child is refused: it would double count.
    `by_hierarchy=False` goes straight from the lost flow to causes;
    `show_delivered=False` leaves the delivered branch out and rescales the
    figure to the loss, which is often a small share of the generated flow.
    """
    if (attribution.get("capacity") or {}).get("rule") == "marginal":
        raise ValueError("the capacity Sankey needs proportional capacity "
                         "attribution: marginal contributions overlap. Rerun "
                         "with --capacity-rule proportional.")
    if hierarchy not in (None, "", 0, "0") and hierarchies:
        raise ValueError("pass hierarchy or hierarchies, not both")
    if by_hierarchy and not hier.rows(attribution):
        raise ValueError("the capacity Sankey needs a hierarchy roll-up: this "
                         "model has no hierarchies (or turn Hierarchy off)")
    if not by_hierarchy and hierarchies:
        raise ValueError("hierarchies only applies with the hierarchy view on")
    source, row, meta = _hierarchy_context(attribution, hierarchy)
    chosen: list[dict[str, Any]] = []
    if by_hierarchy:
        chosen = (hier.resolve_many(source, hierarchies) if hierarchies else
                  [row] if row is not None else hier.top_rows(source))
        if hierarchies and len(chosen) > COMPARE_LIMITS["capacity-sankey"]:
            raise _too_many("capacity-sankey", len(chosen))
        overlap = hier.overlaps(chosen)
        if overlap:
            raise ValueError(
                "these hierarchies overlap (a parent includes its child): "
                + ", ".join(f"{a} / {b}" for a, b in overlap)
                + ". Choose different branches, or use one level at a time.")
        if hierarchies:
            meta = {"kind": "selection", "id": 0,
                    "name": f"{len(chosen)} hierarchies",
                    "path": [str(item.get("name") or "") for item in chosen]}
    nodes, links = hier.sankey(source, chosen, by_hierarchy=by_hierarchy,
                               show_delivered=show_delivered)
    if not nodes:
        raise ValueError(_no_capacity(attribution))
    lost = next(node["value"] for node in nodes if node["id"] == "lost")
    named = [node for node in nodes
             if (node["id"].startswith("hier-") if by_hierarchy
                 else node["id"].startswith("cause-"))]
    caption = (f"Lost flow is {lost:,.0f} flow × time a run"
               + (f"; the largest hierarchy is "
                  f"{max(named, key=lambda node: node['value'])['name']}."
                  if by_hierarchy and named else
                  f"; the largest cause is "
                  f"{max(named, key=lambda node: node['value'])['name'].lower()}."
                  if named else "."))
    remainder = next((node["value"] for node in nodes
                      if node["id"] == "unattributed"), 0.0)
    if remainder > 0 and lost > 0:
        caption += (f" {pct(remainder / lost)} of the lost flow is not inside "
                    "these hierarchies." if by_hierarchy else
                    f" {pct(remainder / lost)} of the lost flow is not "
                    "attributed to a cause.")
    if row is None:
        # the delivered branch is the system's: no verdict for one scope
        caption += _capacity_verdict(attribution.get("capacity") or {},
                                     capacity_target)
    subtitle = (("generated flow → delivered and lost → "
                 if show_delivered else "lost flow → ")
                + ("hierarchy → cause" if by_hierarchy else "cause"))
    spec = ChartSpec(
        "sankey", "Lost capacity by hierarchy and cause" if by_hierarchy
        else "Lost capacity by cause",
        subtitle,
        "flow", "flow × time", series=links, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "nodes": nodes,
                    "format": "", "units": "flow × time",
                    "by_hierarchy": by_hierarchy,
                    "show_delivered": show_delivered,
                    "rule": (attribution.get("capacity") or {}).get("rule", "")})
    return hier.annotate(spec, meta)


def _subtract_intervals(system: list[list[float]],
                        covered: list[list[float]]) -> list[list[float]]:
    merged: list[list[float]] = []
    for lo, hi in sorted(covered):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    out: list[list[float]] = []
    for lo, hi in system:
        cursor = lo
        for a, b in merged:
            if b <= cursor:
                continue
            if a >= hi:
                break
            if a > cursor:
                out.append([cursor, min(a, hi)])
            cursor = max(cursor, b)
            if cursor >= hi:
                break
        if cursor < hi:
            out.append([cursor, hi])
    return out


def hierarchy_ribbon(attribution: dict[str, Any], run: int = 1,
                     depth: int | None = 1,
                     hierarchy: int | str | None = None) -> ChartSpec:
    """One run's down intervals, one row per hierarchy at a level."""
    row = (hier.resolve(attribution, hierarchy)
           if hierarchy not in (None, 0, "") else None)
    chosen = [row] if row is not None else hier.rows_at_depth(attribution, depth or 1)
    rows = hier.ribbon_rows(attribution, chosen, run)
    if not rows:
        raise ValueError("this run has no per-hierarchy outage timeline")
    units = str(attribution.get("units") or "")
    unit = " h" if units.lower().startswith("hour") else (
        f" {units}" if units else "")
    attributed = sum(row["downtime"] for row in rows)
    runs = (attribution.get("outages") or {}).get("runs") or []
    red = (runs[run - 1].get("red") or []
           if 0 < run <= len(runs) else [])
    system_pairs = [[float(a), float(b)]
                    for a, b in zip(red[::2], red[1::2], strict=False)]
    system_down = sum(b - a for a, b in system_pairs)
    if row is None and system_down > attributed + 1e-9:
        covered = [list(pair) for item in rows for pair in item.get("red") or []]
        left = _subtract_intervals(system_pairs, covered)
        rows.append({"name": "Not in a hierarchy", "label": "Not in a hierarchy",
                     "hierarchy_id": 0, "start": rows[0]["start"],
                     "end": rows[0]["end"], "red": left,
                     "downtime": sum(b - a for a, b in left),
                     "span": rows[0]["span"]})
    if attributed <= 0 and system_down > 0:
        caption = (f"No downtime in this run is attributed to the named "
                   f"hierarchies; the unassigned row is the "
                   f"{_quantity(system_down, unit)} the system was down.")
    else:
        caption = (f"{len(rows)} {'hierarchy' if len(rows) == 1 else 'hierarchies'}; "
                   f"{_quantity(attributed, unit)} of downtime attributed to them"
                   + (f" (system down {_quantity(system_down, unit)})."
                      if system_down > attributed else "."))
    spec = ChartSpec(
        "ribbon", (f"When was each hierarchy down? (run {run})" if row is None
                  else f"When was {row['name']} down? (run {run})"),
        "each row is a hierarchy; green = no downtime attributed to it "
        "(the system may be down elsewhere); red = down because of it"
        f" · {hier.depth_text(depth)}",
        f"simulated time ({units})" if units else "simulated time",
        "hierarchies", series=rows, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "",
                    "time_units": units, "system_downtime": system_down},
        scope=hier.scope_meta(attribution, row) if row is not None else
        {"kind": "selection", "id": 0,
         "name": (f"{len(rows)} hierarchy" if len(rows) == 1
                  else f"{len(rows)} hierarchies"),
         "path": [str(item.get("name") or "") for item in rows]})
    return spec


def hierarchy_sunburst(attribution: dict[str, Any],
                       hierarchy: int | str | None = None,
                       depth: int | None = None) -> ChartSpec:
    """Concentric rings: one ring per hierarchy level, then its elements."""
    selected = (hier.resolve(attribution, hierarchy)
                if hierarchy not in (None, 0, "") else None)
    meta = hier.scope_meta(attribution, selected)
    nodes = hier.sunburst(attribution,
                          [selected] if selected is not None else None)
    if depth:
        nodes = [node for node in nodes
                 if int(node.get("depth") or 0) < max(1, int(depth))]
    if not nodes:
        return ChartSpec(
            "sunburst",
            "Hierarchy sunburst" if selected is None
            else f"Inside {selected['name']}",
            "inner ring: top level; each next ring is one level deeper"
        f" · {hier.depth_text(depth, nested=True)}",
            "", "", series=[],
            caption="No hierarchy has downtime in these runs.",
            provenance={"runs": attribution.get("runs", 0), "format": "percent",
                        "depth": depth, "scope": meta},
            scope=meta)
    deepest = max(int(node.get("depth") or 0) for node in nodes)
    ring = 1 if selected is not None and deepest > 0 else 0
    top = max((node for node in nodes
               if int(node.get("depth") or 0) == ring),
              key=lambda node: float(node.get("value") or 0.0))
    if selected is not None:
        caption = (f"Inside {selected['name']}: "
                   f"{top['name'].split(' > ')[-1]} is the largest part.")
    elif top["name"] == "Not in a hierarchy":
        caption = "Most system downtime is not inside a hierarchy."
    else:
        caption = (f"{top['name']} is the largest hierarchy "
                   f"({pct(float(top.get('value') or 0.0))} of system downtime).")
    spec = ChartSpec(
        "sunburst",
        "Hierarchy sunburst" if selected is None
        else f"Inside {selected['name']}",
        "inner ring: top level; each next ring is one level deeper"
        f" · {hier.depth_text(depth, nested=True)}",
        "", "", series=nodes, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "depth": depth, "scope": meta},
        scope=meta)
    return hier.annotate(spec, meta)


#: Colour groups a treemap can use beyond the downtime causes: the cost
#: components.  "sparing" shares the waiting-for-a-spare colour on purpose.
COST_GROUPS = {
    "purchase": "Purchase", "operating": "Operating", "sparing": "Spares",
    "disposal": "Disposal", "spare_pool": "Spare pools",
}


def _treemap(items: list[dict[str, Any]], *, title: str, subtitle: str,
             fmt: str, what: str, runs: int, width: float, height: float,
             top: int, group_labels: dict[str, str] | None = None,
             empty: str = "Nothing to show.") -> ChartSpec:
    """Lay out `items` ({name, value, cause}) largest first; past `top`
    the rest share one "N others" cell.  `value` is what the area shows;
    `share` (value / total) is added for the labels."""
    from raptor.api.insight.axes import format_value
    from raptor.api.insight.treemap import layout

    rows = sorted((i for i in items if (i.get("value") or 0) > 0),
                  key=lambda i: -float(i["value"]))
    total = sum(float(i["value"]) for i in rows)
    visible = rows[:max(1, top)]
    rest = rows[max(1, top):]
    cells = [dict(i) for i in visible]
    if rest:
        cells.append({"name": f"{len(rest)} others",
                      "value": sum(float(i["value"]) for i in rest),
                      "cause": None,
                      # the right-click table names every item behind the
                      # one cell, not just their count
                      "items": [{"name": cell["name"],
                                 "value": float(cell["value"]),
                                 "share": (float(cell["value"]) / total
                                           if total else 0.0),
                                 "cause": cell.get("cause")}
                                for cell in rest]})
    for cell in cells:
        cell["share"] = float(cell["value"]) / total if total else 0.0
        if not cell["name"].endswith(" others"):
            # a hierarchy gathered by depth: its members' own shares
            for item in cell.get("items") or []:
                item.setdefault("share", float(item.get("value") or 0.0) / total
                                if total else 0.0)
    rects = layout(cells, width, height)
    series = [{**cell, **rect.to_dict(),
               "aspect": rect.aspect if rect.area > 0 else None}
              for cell, rect in zip(cells, rects, strict=True)]
    caption = empty
    if series:
        first = series[0]
        amount = format_value(float(first["value"]), fmt, 3)
        caption = (f"{first['name']} occupies the largest area ({amount}"
                   + (f", {pct(first['share'])} of {what}" if fmt != "percent"
                      else f" of {what}")
                   + ").")
    provenance: dict[str, Any] = {"runs": runs, "format": fmt, "measure": what,
                                  "elements": len(rows), "shown": len(cells)}
    if group_labels:
        provenance["group_labels"] = group_labels
    return ChartSpec("treemap", title, subtitle, "width", "height",
                     series=series, caption=caption, provenance=provenance)


def phase_downtime(attribution: dict[str, Any]) -> ChartSpec:
    """Which mission phase the downtime falls in, with each phase's
    availability, in the model's phase order."""
    rows = [r for r in attribution.get("phases") or []]

    def mean(stat: Any) -> float:
        return float((stat or {}).get("mean") or 0.0)

    series = [{"name": r["name"], "value": r["share_of_downtime"],
               "sem": r.get("share_sem"),
               "availability": r["availability"],
               "availability_sem": r.get("availability_sem"),
               "mission": r.get("mission"),
               "entered": mean(r.get("time")) > 0,
               "phase_time": mean(r.get("time")),
               "driver": (r["drivers"][0]["name"] if r.get("drivers") else None)}
              for r in rows]
    mission_rows = [r for r in rows if r.get("mission") is not False]
    mission_red = sum(mean(r.get("red_time")) for r in mission_rows)
    nonmission = [r for r in rows if r.get("mission") is False
                  and mean(r.get("red_time")) > 0]
    caption = "The model has no phases."
    if rows and not any(r.get("share_of_downtime") for r in rows):
        caption = nothing_down(attribution)
        series = []
    elif rows and mission_red <= 0 and nonmission:
        # All the down time is outside the mission: say so, rather than
        # naming a fully available mission phase "weakest".
        most = max(nonmission, key=lambda r: r.get("share_of_downtime") or 0.0)
        caption = ("All system downtime is in non-mission phases (by design); "
                   f"{most['name']} holds {pct(most['share_of_downtime'])} "
                   "of it.")
    elif rows:
        # A non-mission phase is not a requirement: never call it the weakest.
        worst = min((r for r in mission_rows if r["availability"] is not None
                     and mean(r.get("red_time")) > 0),
                    key=lambda r: r["availability"], default=None)
        if worst is not None:
            caption = (f"{worst['name']} has the lowest availability of the "
                       f"mission phases ({100 * worst['availability']:.4g} %) "
                       f"and {pct(worst['share_of_downtime'])} of all downtime")
            named = [d for d in worst.get("drivers") or []
                     if d.get("name") not in _NOT_A_CAUSE]
            if named:
                caption += f", mostly started by {named[0]['name']}"
            caption += "."
        else:
            caption = "No mission phase has any downtime."
        most = max(rows, key=lambda r: r.get("share_of_downtime") or 0.0)
        if (most is not worst and (most.get("share_of_downtime") or 0) > 0
                and most.get("mission") is False):
            caption += (f" {most['name']} is a non-mission phase, so its "
                        f"{pct(most['share_of_downtime'])} share of downtime "
                        "is outside the mission requirement.")
        elif most is not worst and (most.get("share_of_downtime") or 0) > 0:
            caption += (f" {most['name']} holds the most downtime "
                        f"({pct(most['share_of_downtime'])}).")
    non_mission = any(r.get("mission") is False for r in rows)
    return ChartSpec(
        "pareto", "Downtime by phase",
        "share of all system downtime in each phase, in phase order"
        + ("; non-mission phases included" if non_mission else ""),
        "phase", "share of system downtime", series=series, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "row_kind": "phase"})


def phase_cause_heatmap(attribution: dict[str, Any]) -> ChartSpec:
    """Phase × cause matrix: what took the time in each mission phase."""
    if not attribution.get("phases"):
        raise ValueError("the phase heatmap needs a phased model: this one "
                         "has no mission phases")
    if not any(row.get("causes") for row in attribution.get("phases") or []):
        raise ValueError("this run has no phase-by-cause breakdown: rerun it "
                         "with phases and attribution on")
    rows = [row for row in attribution.get("phases") or []
            if row.get("causes")]
    columns = [cause for cause in CAUSES
               if any(row["causes"].get(cause, 0.0) > 0 for row in rows)]
    series = [{"name": row["name"] + (" (non-mission)"
                                      if row.get("mission") is False else ""),
               "mission": row.get("mission"),
               "cells": {cause: float(row["causes"].get(cause) or 0.0)
                         for cause in columns},
               "total": 1.0}
              for row in rows]
    if series and columns:
        biggest = max(((row, cause) for row in series for cause in columns),
                      key=lambda item: item[0]["cells"].get(item[1], 0.0))
        name = str(biggest[0]["name"]).replace(" (non-mission)", "")
        caption = (f"{name} is mostly "
                   f"{CAUSE_LABELS.get(biggest[1], biggest[1]).lower()} "
                   f"({pct(biggest[0]['cells'][biggest[1]])} of its downtime)"
                   + (", a non-mission phase (by design)."
                      if biggest[0].get("mission") is False else "."))
    else:
        caption = "No phase has downtime in these runs."
    return ChartSpec(
        "heatmap", "Phase by cause",
        "share of each phase's downtime by cause; rows add to 100 %",
        "cause", "phase", series=series, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "row_label": "phase", "row_kind": "phase",
                    "columns": [{"key": cause,
                                 "label": CAUSE_LABELS.get(cause, cause)}
                                for cause in columns]})


def downtime_treemap(attribution: dict[str, Any], *, width: float = 1000.0,
                     height: float = 600.0, top: int = 200, depth: int = 0,
                     scope_row: dict[str, Any] | None = None) -> ChartSpec:
    """The downtime treemap: area = share of downtime, label = element.

    `depth` (with the chosen `scope_row`) gathers anything nested deeper
    into one cell per hierarchy at that level (`hierarchy.roll_up`)."""
    scope = attribution.get("scope")
    rows = [row for row in attribution.get("elements") or []
            if float((row.get("dtci") or {}).get("mean") or 0.0) > 0]
    total = sum(float(row["dtci"]["mean"] or 0.0) for row in rows)
    items = [{"name": row["name"],
              "value": (float(row["dtci"]["mean"] or 0.0) / total
                        if scope and total else float(row["dtci"]["mean"] or 0.0)),
              "cause": row.get("main_cause"),
              "kind": row.get("kind"), "id": row.get("id")} for row in rows]
    items = hier.roll_up(attribution, items, scope_row, depth)
    what = f"{scope['name']} downtime" if scope else "system downtime"
    return _treemap(items, title="Downtime treemap",
                    subtitle=f"area = share of {what}; colour = main "
                             "cause; click a cell to locate",
                    fmt="percent", what=what,
                    runs=int(attribution.get("runs", 0) or 0),
                    width=width, height=height, top=top,
                    empty=nothing_down(attribution))


def lost_capacity_treemap(attribution: dict[str, Any], *, width: float = 1000.0,
                          height: float = 600.0, top: int = 200, depth: int = 0,
                          scope_row: dict[str, Any] | None = None) -> ChartSpec:
    """Area = share of the lost flow; colour = what caused the element's lost
    flow (its downtime cause when an older result has no capacity causes)."""
    causes = {row["name"]: row.get("main_cause")
              for row in attribution.get("elements") or []}
    capacity = attribution.get("capacity") or {}
    items = [{"name": row["name"], "value": (row.get("share") or {}).get("mean") or 0.0,
              "cause": row.get("main_cause") or causes.get(row["name"]),
              "kind": row.get("kind"), "id": row.get("id")}
             for row in capacity.get("elements") or []]
    scope = attribution.get("scope")
    total = sum(float(item["value"]) for item in items)
    if scope and total:
        # scoped rows keep their share of the system's lost flow; the map
        # of one scope shows shares of that scope's own (as the downtime
        # treemap does), so the cells, caption and items table agree
        items = [{**item, "value": float(item["value"]) / total} for item in items]
    items = hier.roll_up(attribution, items, scope_row, depth)
    what = (f"{scope['name']}'s lost flow" if scope else "the lost flow")
    return _treemap(items, title="Lost capacity treemap",
                    subtitle=f"area = share of {what}; colour = main "
                             "cause; click a cell to locate",
                    fmt="percent", what=what,
                    runs=int(attribution.get("runs", 0) or 0),
                    width=width, height=height, top=top,
                    empty=_no_capacity(attribution))


def cost_treemap(costs: list[dict[str, Any]], *, width: float = 1000.0,
                 height: float = 600.0, top: int = 200, depth: int = 0,
                 attribution: dict[str, Any] | None = None,
                 scope_row: dict[str, Any] | None = None) -> ChartSpec:
    """Area = mean life-cycle cost per run; colour = its largest component
    (purchase, operating, spares, disposal).  Spare pools are cells of
    their own, so the spares picture is complete."""
    items = []
    for row in costs:
        parts = {k: float(row.get(k) or 0.0)
                 for k in (*_COST_PARTS, "spare_pool", "other")}
        group = max(parts, key=lambda k: parts[k]) if any(parts.values()) else None
        items.append({"name": row["name"], "value": float(row.get("total") or 0.0),
                      "cause": group})
    if depth and attribution:
        # cost rows hold names; the element rows give their (kind, id)
        keys = {str(row.get("name")): (str(row.get("kind")), int(row.get("id") or 0))
                for row in attribution.get("elements") or []
                if row.get("kind") is not None}
        items = hier.roll_up(attribution, items, scope_row, depth,
                             key=lambda item: keys.get(str(item.get("name"))))
    return _treemap(items, title="Cost treemap",
                    subtitle="area = mean life-cycle cost per run; colour = "
                             "largest part of it",
                    fmt="money", what="the cost", runs=0,
                    width=width, height=height, top=top,
                    group_labels=dict(COST_GROUPS),
                    empty=("No element has a cost in this run." if costs
                           else _needs("cost")))


def outage_anatomy(attribution: dict[str, Any], max_columns: int = 6
                   ) -> ChartSpec:
    """What outages look like: the ordered steps that start and prolong them."""
    tracks: dict[tuple, dict[str, Any]] = {}
    for sequence in attribution.get("sequences") or []:
        steps = (sequence.get("steps") or [])[:max_columns]
        prefix: list[tuple[str, str]] = []
        for column, step in enumerate(steps):
            prefix.append((str(step.get("element")), str(step.get("cause"))))
            key = (column, tuple(prefix))
            entry = tracks.setdefault(key, {
                "column": column, "path": list(prefix),
                "label": str(step.get("element")),
                "cause": str(step.get("cause")),
                "value": 0.0, "outages": 0.0})
            entry["value"] += float(sequence.get("share") or 0.0)
            entry["outages"] += float(sequence.get("outages") or 0.0)
    series = sorted(tracks.values(),
                    key=lambda row: (row["column"], -row["value"]))
    left = series[0]["value"] if series else 0.0
    return ChartSpec(
        "icicle", "What an outage looks like",
        "each column is the next step; width = share of downtime",
        "step", "share of downtime", series=series,
        caption=(f"The commonest start is {series[0]['label']} "
                 f"({pct(left)} of downtime)."
                 if series else nothing_down(attribution)),
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "sequences": len(attribution.get("sequences") or [])})


def ensemble_ribbon(attribution: dict[str, Any], max_runs: int = 200
                    ) -> ChartSpec:
    """All trials as rows: green where up, red where down (C5/7.6.4)."""
    record = attribution.get("outages") or {}
    runs = list(record.get("runs") or ())
    if not runs:
        return ChartSpec(
            "ribbon", "Run-to-run system state", "needs the outage record",
            "time", "runs", series=[],
            caption=("This run kept no per-run outage record: run again with "
                     "downtime attribution on (and fewer runs if it is very "
                     "large)."))
    rows: list[dict[str, Any]] = []
    for index, run in enumerate(runs):
        start, end = float(run.get("start", 0.0)), float(run.get("end", 0.0))
        red = [float(value) for value in run.get("red") or ()]
        pairs = list(zip(red[0::2], red[1::2], strict=False))
        rows.append({"run": index + 1, "start": start, "end": end,
                     "red": [list(pair) for pair in pairs],
                     "downtime": sum(b - a for a, b in pairs),
                     "span": max(end - start, 1e-12)})
    rows.sort(key=lambda row: row["downtime"])
    total = len(rows)
    worst = rows[-1]["downtime"] / rows[-1]["span"]
    best = rows[0]["downtime"] / rows[0]["span"]
    if len(rows) > max_runs:
        # Include both ends: the worst run is the one a reader looks for.
        if max_runs <= 1:
            rows = [rows[-1]]
        else:
            picks = {round(i * (len(rows) - 1) / (max_runs - 1))
                     for i in range(max_runs)}
            rows = [rows[index] for index in sorted(picks)]
    units = str(attribution.get("units") or "")
    subtitle = "one row per trial, sorted by downtime; red = system down"
    if len(rows) < total:
        # 200 rows were drawn for 205 runs without a word
        subtitle = (f"{len(rows)} of the {total:,} runs, evenly through the "
                    "ranking, sorted by downtime; red = system down")
    return ChartSpec(
        "ribbon", "Run-to-run system state", subtitle,
        f"simulated time ({units})" if units else "simulated time", "runs",
        series=rows,
        caption=(f"{runs_text(len(runs))}; best {pct(1 - best)} up, "
                 f"worst {pct(1 - worst)} up."),
        provenance={"runs": attribution.get("runs", len(runs)),
                    "shown": len(rows)})


#: Chart kinds whose small items `fold_small` can gather into one row or cell.
FOLDABLE_KINDS = ("pareto", "capacity_pareto", "treemap")


def _share_of(spec: ChartSpec, row: dict[str, Any]) -> float | None:
    """A row's share of the whole: its own `share`, or its value when the
    value already is a share (a percent chart)."""
    if isinstance(row.get("share"), (int, float)):
        return float(row["share"])
    if spec.provenance.get("format") == "percent" or spec.kind == "capacity_pareto":
        value = row.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def foldable(spec: ChartSpec) -> bool:
    """Whether `fold_small` applies: element rankings and treemaps (not a
    chart in a fixed order, such as downtime by phase)."""
    if spec.kind not in FOLDABLE_KINDS:
        return False
    if spec.kind == "pareto" and spec.x_label != "element":
        return False
    if any(row.get("is_group") for row in spec.series):
        return False
    return any(_share_of(spec, row) is not None for row in spec.series)


#: The fold presets the Charts tab offers, as `fold` strings the MCP
#: tool and `--chart-fold` take too: label -> mode.
FOLD_PRESETS = {
    "Don't fold": "",
    "Top 10 + others": "top:10",
    "Top 20 + others": "top:20",
    "Top quartile + others": "fraction:0.25",
    "Last 5 % of total": "tail:0.05",
}


def parse_fold(mode: str | float | None) -> dict[str, Any]:
    """`fold_small` keyword arguments for one fold mode.

    ``top:N`` keeps the N largest items, ``fraction:F`` keeps that fraction
    of the items (0.25 = the top quartile), ``tail:S`` folds the smallest
    items until they make up share S of the total, and ``share:S`` (or a
    bare number) folds every item below share S.  Empty, ``none`` or 0 folds
    nothing.  Raises ValueError on anything else, so a typo is never
    silently "fold nothing".
    """
    if mode is None:
        return {}
    if isinstance(mode, (int, float)) and not isinstance(mode, bool):
        mode = f"share:{mode}"
    text = str(mode).strip().lower()
    if text in ("", "none", "all", "0", "0.0"):
        return {}
    kind, _sep, raw = text.partition(":")
    if not _sep:
        kind, raw = "share", text
    try:
        number = float(raw)
    except ValueError:
        raise ValueError(f"fold {mode!r}: expected top:N, fraction:F, "
                         "tail:S or share:S") from None
    if kind == "top":
        if number < 1 or not number.is_integer():
            raise ValueError(f"fold {mode!r}: top takes a whole number of at least 1")
        return {"top": int(number)}
    if kind in ("fraction", "tail", "share"):
        if not 0.0 < number < 1.0:
            raise ValueError(f"fold {mode!r}: {kind} takes a fraction "
                             "between 0 and 1 (0.25, not 25)")
        return {"fraction": number} if kind == "fraction" else (
            {"tail_share": number} if kind == "tail" else {"min_share": number})
    raise ValueError(f"fold {mode!r}: expected top:N, fraction:F, tail:S or share:S")


#: How many folded names a caption lists before "and N more".
_CAPTION_NAMES = 12


def _member_count(rows: list[dict[str, Any]]) -> int:
    """How many items `rows` stand for: an "N others" cell counts its
    members, so folding it again cannot under-count (#351)."""
    return sum(len(row["items"]) if row.get("items") else 1 for row in rows)


def fold_small(spec: ChartSpec, min_share: float = 0.0, *,
               top: int | None = None, fraction: float | None = None,
               tail_share: float | None = None) -> ChartSpec:
    """Gather the tail into one "N others" row or cell (see `_fold_small`),
    writing any money in the caption in the chart's own currency."""
    from raptor.api.insight.axes import currency
    with currency(spec.provenance.get("currency")):
        return _fold_small(spec, min_share, top=top, fraction=fraction,
                           tail_share=tail_share)


def _fold_small(spec: ChartSpec, min_share: float = 0.0, *,  # noqa: C901, PLR0912, PLR0915 - one walk, two layouts
                top: int | None = None, fraction: float | None = None,
                tail_share: float | None = None) -> ChartSpec:
    """Gather the tail into one "N others" row or cell.

    `min_share` folds every item below that share of the whole (the
    original control); `top`/`fraction` keep that many items (or that
    fraction of the item count); `tail_share` takes the smallest items
    until their combined value is that share of the total.  A treemap is
    laid out again.  Charts that are not `foldable`, or a fold that would
    gather fewer than two items, come back unchanged."""
    mode = (top is not None or fraction is not None or tail_share is not None)
    if (not mode and min_share <= 0) or not foldable(spec):
        return spec
    rows = list(spec.series)
    if spec.kind != "treemap":
        rows += spec.provenance.get("cut") or []
    keep: list[dict[str, Any]] = []
    small: list[dict[str, Any]] = []
    if top is not None or fraction is not None:
        count = (int(top) if top is not None
                 else max(1, math.ceil(len(rows) * float(fraction or 0.0))))
        keep, small = rows[:count], rows[count:]
        label = f"{_member_count(small)} others"
    elif tail_share is not None:
        ordered = sorted(rows, key=lambda row: -abs(float(row.get("value") or 0.0)))
        total = sum(abs(float(row.get("value") or 0.0)) for row in ordered)
        target = total * max(0.0, min(1.0, float(tail_share)))
        taken: set[int] = set()
        reached = 0.0
        for index in range(len(ordered) - 1, -1, -1):
            if reached >= target - 1e-12:
                break
            taken.add(index)
            reached += abs(float(ordered[index].get("value") or 0.0))
        keep = [row for index, row in enumerate(ordered) if index not in taken]
        small = [row for index, row in enumerate(ordered) if index in taken]
        label = f"{_member_count(small)} others (last {pct(tail_share)} of the total)"
    else:
        for row in rows:
            share = _share_of(spec, row)
            (small if share is not None and share < min_share else keep).append(row)
        label = f"{_member_count(small)} others below {pct(min_share)}"
    if len(small) < 2:
        return spec
    total = sum(float(r.get("value") or 0.0) for r in small)
    # an earlier "N others" cell is named by its members, never by its label
    names = [str(item.get("name")) for r in small
             for item in (r.get("items") or [r])]
    details: list[dict[str, Any]] = []
    for row in small:
        entry = {"name": row.get("name"), "value": float(row.get("value") or 0.0),
                 "share": _share_of(spec, row), "cause": row.get("cause"),
                 "downtime": row.get("downtime")}
        # a folded group may itself be an earlier "N others" cell: carry
        # its members through, never the group as one opaque line
        if row.get("items"):
            details.extend(dict(item) for item in row["items"])
        else:
            details.append(entry)
    folded: dict[str, Any] = {"name": label, "value": total, "folded": names,
                              "items": details}
    if any("share" in r for r in small):
        folded["share"] = sum(_share_of(spec, r) or 0.0 for r in small)
    if spec.kind == "capacity_pareto":
        folded["downtime"] = sum(float(r.get("downtime") or 0.0) for r in small)
    causes: dict[str, float] = {}
    for row in small:
        for cause, part in (row.get("causes") or {}).items():
            causes[cause] = causes.get(cause, 0.0) + float(part) * float(
                row.get("value") or 0.0)
    if causes and total > 0:
        folded["causes"] = {c: v / total for c, v in causes.items()}
    provenance = {**spec.provenance, "folded_below": min_share,
                  "folded": len(names), "cut": []}
    # a caption is one or two sentences: a long tail is named in full in the
    # folded row's `items` (the right-click table and the CSV), not here
    shown_names = names if len(names) <= _CAPTION_NAMES else [
        *names[:_CAPTION_NAMES], f"and {len(names) - _CAPTION_NAMES:,} more"]
    caption = (f"{spec.caption} " if spec.caption else "") + \
        f"{label}: {', '.join(shown_names)}."
    if spec.kind != "treemap":
        return ChartSpec(spec.kind, spec.title, spec.subtitle, spec.x_label,
                         spec.y_label, series=[*keep, folded], marks=spec.marks,
                         annotations=spec.annotations, caption=caption,
                         provenance=provenance, scope=spec.scope,
                         facets=spec.facets, baseline=spec.baseline)
    from raptor.api.insight.treemap import layout
    width = max(float(r["x"]) + float(r["w"]) for r in spec.series)
    height = max(float(r["y"]) + float(r["h"]) for r in spec.series)
    cells = [{k: v for k, v in r.items() if k not in ("x", "y", "w", "h", "aspect")}
             for r in keep] + [{**folded, "cause": None}]
    cells.sort(key=lambda cell: -float(cell.get("value") or 0.0))
    rects = layout(cells, width, height)
    series = [{**cell, **rect.to_dict(),
               "aspect": rect.aspect if rect.area > 0 else None}
              for cell, rect in zip(cells, rects, strict=True)]
    return ChartSpec(spec.kind, spec.title, spec.subtitle, spec.x_label,
                     spec.y_label, series=series, marks=spec.marks,
                     annotations=spec.annotations, caption=caption,
                     provenance=provenance, scope=spec.scope,
                     facets=spec.facets, baseline=spec.baseline)


def _time_chart(name: str, doc: str) -> Any:
    """A `CHARTS` entry for one of the charts over time (`timecharts`).

    They live in their own module, which imports `ChartSpec` from here, so
    the entry imports it only when called."""
    def build(**kwargs: Any) -> ChartSpec:
        from raptor.api.insight.timecharts import time_chart
        return time_chart(name, **kwargs)
    build.__doc__ = doc
    build.__name__ = name.replace("-", "_")
    return build


#: Every chart the application can draw, keyed by the name the CLI and MCP use.
CHARTS = {
    "availability-through-run": _time_chart(
        "availability-through-run",
        "One run's time so far fully up, degraded and down (the availability plot)."),
    "interval-availability": _time_chart(
        "interval-availability",
        "The availability in each recording interval of one run."),
    "availability-band": _time_chart(
        "availability-band",
        "Every run's availability so far, as bands across the runs."),
    "capacity-through-run": _time_chart(
        "capacity-through-run",
        "One run's flow against a fully working system (the capacity plot)."),
    "capacity-band": _time_chart(
        "capacity-band", "Every run's flow at each moment, as bands across the runs."),
    "trial-outcome": trial_outcome,
    "ecdf": ecdf,
    "reliability-curve": reliability_curve,
    "component-failures": component_failure_pareto,
    "downtime-pareto": downtime_pareto,
    "downtime-waterfall": downtime_waterfall,
    "availability-ladder": availability_ladder,
    "outage-histogram": outage_histogram,
    "frequency-duration": frequency_duration,
    "importance-quadrant": importance_quadrant,
    "convergence": convergence,
    "cost-pareto": cost_pareto,
    "lost-capacity-pareto": lost_capacity_pareto,
    "capacity-vs-downtime": capacity_vs_downtime,
    "capacity-waterfall": capacity_waterfall,
    "annual-fan": annual_fan,
    "annual-capacity-fan": annual_capacity_fan,
    "annual-dots": annual_dots,
    "downtime-treemap": downtime_treemap,
    "phase-downtime": phase_downtime,
    "phase-cause-heatmap": phase_cause_heatmap,
    "lost-capacity-treemap": lost_capacity_treemap,
    "cost-treemap": cost_treemap,
    "cost-categories": cost_categories,
    "cost-cumulative": cost_cumulative,
    "cost-annual": cost_annual,
    "outage-anatomy": outage_anatomy,
    "ensemble-ribbon": ensemble_ribbon,
    "hierarchy-scorecard": hierarchy_scorecard,
    "hierarchy-pareto": hierarchy_pareto,
    "hierarchy-treemap": hierarchy_treemap,
    "hierarchy-overlay": hierarchy_overlay,
    "hierarchy-small-multiples": hierarchy_small_multiples,
    "hierarchy-hops": hierarchy_hops,
    "hierarchy-delta": hierarchy_delta,
    "hierarchy-heatmap": hierarchy_heatmap,
    "hierarchy-phase-heatmap": hierarchy_phase_heatmap,
    "hierarchy-anatomy": hierarchy_anatomy,
    "capacity-sankey": capacity_sankey,
    "hierarchy-ribbon": hierarchy_ribbon,
    "hierarchy-sunburst": hierarchy_sunburst,
}

#: Charts that need the hierarchy roll-up from an attributed run.
HIERARCHY_CHARTS = frozenset(
    name for name in CHARTS if name.startswith("hierarchy-")) | {
        "capacity-sankey"}

#: Charts that compare several hierarchies (rather than scope one).
MULTI_HIERARCHY_CHARTS = frozenset({
    "hierarchy-overlay", "hierarchy-small-multiples", "hierarchy-hops",
    "capacity-sankey"})

#: Charts that already choose one level, so one scope argument is ambiguous.
NO_SINGLE_HIERARCHY = frozenset({
    "hierarchy-scorecard", "hierarchy-overlay",
    "hierarchy-small-multiples", "hierarchy-hops", "hierarchy-delta",
})
#: Charts whose `depth` argument chooses a level.
DEPTH_CHARTS = frozenset({
    "hierarchy-scorecard", "hierarchy-pareto", "hierarchy-treemap",
    "hierarchy-overlay", "hierarchy-small-multiples", "hierarchy-hops",
    "hierarchy-delta", "hierarchy-heatmap", "hierarchy-phase-heatmap", "hierarchy-ribbon",
    "hierarchy-sunburst",
})

#: Where the availability plot's interval is set, by surface.
_INTERVAL_WHERE = {
    "cli": "(ao_interval under experiment: recording: in the .rbds file)",
    "mcp": ("(experiment.recording.ao_interval, in the model's .rbds or "
            "build_model's experiment)"),
}

#: Charts that are system-wide by measurement, not by missing data.  A
#: hierarchy scope must not silently leave them showing the whole system;
#: the caller gets a reason and the GUI greys the entry instead.
_SYSTEM_ONLY = {
    "availability-ladder", "reliability-curve", "component-failures",
    "outage-histogram", "phase-downtime",
    "phase-cause-heatmap", "lost-capacity-pareto", "capacity-vs-downtime",
    "lost-capacity-treemap",
    "ensemble-ribbon", "capacity-waterfall", "annual-fan", "annual-dots",
    "annual-capacity-fan", "interval-availability", "availability-through-run",
    "availability-band", "capacity-through-run", "capacity-band",
    "cost-categories", "cost-cumulative", "cost-annual",
}


def _system_only(name: str) -> str:
    return (f"{name} is a system-wide measurement: it has no per-hierarchy "
            "series. Choose System, or use the hierarchy overlay, small "
            "multiples, heatmap or state ribbon.")


def with_currency_stamp(spec: ChartSpec) -> ChartSpec:
    """`spec` with the current currency (`axes.currency`) recorded in its
    provenance, so it draws and saves in the same currency later, outside
    the block it was built in.  A spec that already records one keeps it."""
    from raptor.api.insight.axes import current_currency
    if not spec.provenance.get("currency"):
        spec.provenance = {**spec.provenance, "currency": current_currency()}
    return spec


def chart_named(name: str, *, currency: str | None = None,
                **kwargs: Any) -> ChartSpec:
    """Build one chart by name (see `_chart_named` for the inputs), its
    money printed in `currency` ("$" when not given: the model's
    `currency`, from `run_tables.run_currency` for a stored run)."""
    from raptor.api.insight.axes import currency as _currency
    with _currency(currency):
        return with_currency_stamp(_chart_named(name, **kwargs))


def _chart_named(name: str, *, per_run: list[dict] | None = None,  # noqa: C901, PLR0912, PLR0915 - one table of charts
                attribution: dict[str, Any] | None = None,
                importance: dict[str, Any] | None = None,
                report_data: list[Any] | None = None,
                costs: list[dict[str, Any]] | None = None,
                cost_runs: list[dict[str, Any]] | None = None,
                periods: dict[str, Any] | None = None,
                field_name: str = "availability",
                label: str = "Availability",
                target: float | None = None,
                units: str = "",
                ao_runs: list[dict[str, Any]] | None = None,
                cap_runs: list[dict[str, Any]] | None = None,
                run: int = 1,
                y_floor: float | None = None,
                surface: str | None = None,
                hierarchy: int | str | None = None,
                hierarchies: Sequence[int | str] | None = None,
                baseline: list[dict[str, Any]] | None = None,
                baseline_label: str | None = None,
                depth: int | None = None,
                by_hierarchy: bool = True,
                show_delivered: bool = True,
                capacity_target: float | None = None) -> ChartSpec:
    """Build one chart by name, from whichever inputs it needs.

    `target` is the availability target; `capacity_target` (a fraction of
    the generated flow) is drawn on the capacity charts over time and
    judged in the captions of the lost-capacity waterfall and Sankey.

    The charts over time (`raptor.api.insight.timecharts.TIME_CHARTS`) take
    the parsed availability plot (`ao_runs`) or capacity plot (`cap_runs`),
    the `run` number for the one-run charts, and `y_floor`, a manual lower
    limit for the availability axis.

    Raises ValueError naming the charts when the name is unknown, and
    ValueError naming the missing input when one is required: an analysis
    the run did not make, or a run too short for the year charts. A chart
    whose analysis ran but found nothing (no capacity lost, nothing down)
    is drawn, its reason as the caption. `surface` ("cli", "mcp", "gui")
    words the how-to for that surface alone.
    """
    if name not in CHARTS:
        raise ValueError(
            f"unknown chart {name!r}; known charts: {', '.join(sorted(CHARTS))}")
    has_one = hierarchy not in (None, "", 0, "0")
    if has_one and hierarchies:
        raise ValueError("pass hierarchy for one scope, or hierarchies for a "
                         "comparison, not both")
    if has_one and name in NO_SINGLE_HIERARCHY:
        raise ValueError(f"{name} does not take one hierarchy: use "
                         "hierarchies for a comparison, or pick a ranking, "
                         "treemap, sunburst, heatmap, ribbon or Sankey")
    if hierarchies and name not in MULTI_HIERARCHY_CHARTS:
        raise ValueError(f"{name} does not take several hierarchies: use "
                         "hierarchy-overlay, hierarchy-small-multiples or hierarchy-hops")
    if (name in HIERARCHY_CHARTS and not hier.rows(attribution)
            and not (name == "capacity-sankey" and not by_hierarchy)):
        raise ValueError(
            "this chart needs the hierarchy roll-up from an attributed run: "
            "run again with attribution=True and keep the event log")
    if depth is not None:
        if name not in DEPTH_CHARTS:
            raise ValueError(f"{name} has no hierarchy depth")
        if int(depth) < 1:
            raise ValueError("depth must be at least 1 (1 is the top level)")
        deepest = hier.max_depth(attribution)
        if int(depth) > deepest:
            raise ValueError(f"depth {depth} does not exist: this model has "
                             f"{deepest} level{'s' if deepest != 1 else ''}")
    row = None
    if hierarchy not in (None, "", 0, "0"):
        if name in _SYSTEM_ONLY:
            raise ValueError(_system_only(name))
        row = hier.resolve(attribution, hierarchy)
    # The hierarchy charts read the roll-up; the element charts read a
    # scoped copy so a filter never silently leaves the system totals in.
    source = hier.scoped(attribution, row) if row is not None else (attribution or {})
    meta = hier.scope_meta(attribution, row)
    if name == "hierarchy-scorecard":
        return hierarchy_scorecard(source, depth=depth, baseline=baseline)
    # checked above: a hierarchy chart without a roll-up has raised
    rolled_up: dict[str, Any] = attribution or {}
    if name == "hierarchy-pareto":
        return hierarchy_pareto(rolled_up, hierarchy, depth=depth)
    if name == "hierarchy-treemap":
        return hierarchy_treemap(rolled_up, hierarchy, depth=depth)
    if name == "hierarchy-overlay":
        return hierarchy_overlay(rolled_up, hierarchies, depth=depth)
    if name == "hierarchy-small-multiples":
        return hierarchy_small_multiples(rolled_up, hierarchies, depth=depth)
    if name == "hierarchy-hops":
        return hierarchy_hops(rolled_up, hierarchies, depth=depth)
    if name == "hierarchy-delta":
        return hierarchy_delta(rolled_up, baseline, depth=depth,
                               baseline_label=baseline_label)
    if name == "hierarchy-heatmap":
        return hierarchy_heatmap(rolled_up, hierarchy, depth=depth)
    if name == "hierarchy-phase-heatmap":
        return hierarchy_phase_heatmap(rolled_up, hierarchy, depth=depth)
    if name == "hierarchy-anatomy":
        return hierarchy_anatomy(rolled_up, hierarchy)
    if name == "capacity-sankey":
        return capacity_sankey(rolled_up, hierarchy, hierarchies,
                               by_hierarchy=by_hierarchy,
                               show_delivered=show_delivered,
                               capacity_target=capacity_target)
    if name == "hierarchy-ribbon":
        return hierarchy_ribbon(rolled_up, run, depth=depth,
                               hierarchy=hierarchy)
    if name == "hierarchy-sunburst":
        return hierarchy_sunburst(rolled_up, hierarchy, depth=depth)
    from raptor.api.insight.timecharts import TIME_CHARTS, time_chart
    if name in TIME_CHARTS:
        generated = (attribution or {}).get("capacity", {}) or {}
        ends = [row.get("ending_sim_time") for row in per_run or []]
        try:
            return time_chart(name, ao_runs=ao_runs, cap_runs=cap_runs, run=run,
                              units=units, target=target, y_floor=y_floor,
                              capacity_target=capacity_target,
                              generated=generated.get("generated"),
                              ends=ends if any(e is not None for e in ends) else None)
        except ValueError as exc:
            # "under Run panel ▸ Recording" was told to a command-line user
            raise ValueError(str(exc).replace(
                "under Run panel ▸ Recording", _INTERVAL_WHERE.get(surface or "", "")
                or "under Run panel ▸ Recording")) from exc

    capacity_chart = name in ("lost-capacity-treemap", "lost-capacity-pareto",
                              "capacity-vs-downtime", "capacity-waterfall")
    rows = per_run or []
    scoped_per_run = rows
    if row is not None and name in ("trial-outcome", "ecdf", "convergence"):
        scoped_per_run = hier.per_run(attribution, row)
        field_name = "availability"
        label = "share of system downtime"
        target = None
    ran_capacity = any(r.get("capacity_weighted_availability") is not None for r in rows)
    ran_cost = any(r.get("grand_total") is not None for r in rows)

    def needs(*keys: str) -> str:
        return message(*keys, surface=surface)

    if capacity_chart and attribution is not None and not source.get("capacity") \
            and ran_capacity:
        # the analysis ran and nothing was lost: "needs capacity analysis"
        # sent a user to tick a box that was ticked
        return ChartSpec(
            "pareto", {"lost-capacity-pareto": "Who loses the capacity",
                       "capacity-vs-downtime": "Downtime against lost capacity",
                       "capacity-waterfall": "Lost capacity by cause",
                       "lost-capacity-treemap": "Lost capacity treemap"}[name],
            caption=("No capacity was lost in these runs, so there is nothing "
                     "to show."))

    def need_attribution() -> dict[str, Any]:
        if attribution is None:
            raise ValueError(needs("attribution", *(("capacity",) if capacity_chart
                                                    else ())))
        # one behaviour for "not in this run": an error, as the charts over
        # time give, not a blank figure carrying the how-to as its caption
        if capacity_chart and not source.get("capacity"):
            raise ValueError(needs("capacity"))
        if name in ("phase-downtime", "phase-cause-heatmap") \
                and not attribution.get("phases"):
            raise ValueError("this chart needs a phased model: this one has no "
                             "mission phases")
        return source

    def need_years() -> dict[str, Any]:
        """The year charts' input, or the real reason there is none: "0
        run-years; no threshold was set" for a 2,000-hour run."""
        unit = str(units or "hours")
        ends = [float(r["ending_sim_time"]) for r in rows
                if isinstance(r.get("ending_sim_time"), (int, float))]
        length = max(ends) if ends else None
        if not unit.lower().startswith("hour"):
            raise ValueError("the year charts need the model's time unit to be "
                             f"hours; this model counts {unit}")
        if length is not None and length < 8760:
            raise ValueError("the year charts need a run at least one whole year "
                             f"long (8,760 hours); this run is {length:,.0f} hours")
        if not periods or not (periods.get("by_index") or periods.get("pooled")):
            raise ValueError(needs("attribution"))
        if name == "annual-capacity-fan" and not (
                (periods.get("capacity") or {}).get("by_index")):
            raise ValueError(needs("capacity"))
        return periods

    def element_cost_rows() -> list[dict[str, Any]]:
        if not ran_cost and rows and costs in (None, []):
            raise ValueError(needs("cost"))
        all_costs = costs if costs is not None else element_costs(report_data or [])
        if row is not None:
            all_costs = hier.cost_rows(all_costs, row, source.get("elements") or [])
        return all_costs

    def need_importance() -> dict[str, Any]:
        if importance is None or importance.get("mode") not in (None, "both"):
            # the quadrant plots RAW against RRW: "improvement" has no RAW
            # and "risk" no RRW, so only "both" can draw it
            raise ValueError(needs("importance"))
        if row is not None:
            return hier.importance_rows(importance, row,
                                        source.get("elements") or []) or importance
        return importance

    builders = {
        "trial-outcome": lambda: trial_outcome(
            scoped_per_run, field_name, label=label, target=target,
            units=units),
        "ecdf": lambda: ecdf(
            scoped_per_run, field_name, label=label, target=target,
            units=units, higher_is_better=row is None),
        "reliability-curve": lambda: reliability_curve(
            per_run or [], units=units),
        "convergence": lambda: convergence(scoped_per_run, field_name,
                                           target, label=label),
        "importance-quadrant": lambda: importance_quadrant(need_importance()),
        "cost-pareto": lambda: cost_pareto(element_cost_rows()),
        "cost-categories": lambda: cost_categories(cost_runs),
        "cost-cumulative": lambda: cost_cumulative(cost_runs),
        "cost-annual": lambda: cost_annual(cost_runs),
        "downtime-waterfall": lambda: downtime_waterfall(
            need_attribution(),
            measures.availability_ladder(per_run or [])["operational"]["value"]
            if per_run and row is None else None,
            target if row is None else None,
            ladder=(measures.availability_ladder(per_run or [])
                    if per_run and row is None else None)),
        "component-failures": lambda: component_failure_pareto(per_run or []),
        "downtime-pareto": lambda: downtime_pareto(need_attribution()),
        "availability-ladder": lambda: availability_ladder(
            measures.availability_ladder(per_run or [])),
        "outage-histogram": lambda: outage_histogram(need_attribution()),
        "frequency-duration": lambda: frequency_duration(need_attribution()),
        "downtime-treemap": lambda: downtime_treemap(need_attribution()),
        "phase-downtime": lambda: phase_downtime(need_attribution()),
        "phase-cause-heatmap": lambda: phase_cause_heatmap(need_attribution()),
        "lost-capacity-treemap": lambda: lost_capacity_treemap(need_attribution()),
        "cost-treemap": lambda: cost_treemap(element_cost_rows()),
        "outage-anatomy": lambda: outage_anatomy(need_attribution()),
        "ensemble-ribbon": lambda: ensemble_ribbon(need_attribution()),
        "lost-capacity-pareto": lambda: lost_capacity_pareto(
            need_attribution()),
        "capacity-vs-downtime": lambda: capacity_vs_downtime(
            need_attribution()),
        "capacity-waterfall": lambda: capacity_waterfall(
            need_attribution(), capacity_target if row is None else None),
        "annual-fan": lambda: annual_fan(need_years()),
        "annual-dots": lambda: annual_dots(need_years()),
        "annual-capacity-fan": lambda: annual_capacity_fan(
            need_years(), capacity_target),
    }
    spec = builders[name]()
    if row is not None:
        if name == "outage-anatomy":
            note = ("Shares are of the selected hierarchy's downtime."
                    if row is not None else
                    "Shares are of system downtime; the other elements are hidden.")
        elif name in {"downtime-treemap", "downtime-waterfall"}:
            note = (f"Shares are of {row['name']}'s downtime; the other "
                    "elements are hidden.")
        elif name in {"trial-outcome", "ecdf", "convergence",
                      "downtime-pareto", "frequency-duration"}:
            note = "Shares are of system downtime; the other elements are hidden."
        elif name in {"lost-capacity-pareto", "capacity-vs-downtime",
                      "lost-capacity-treemap"}:
            note = ("Shares are of the lost flow; the other elements are "
                    "hidden.")
        else:
            note = ""
        if name == "convergence":
            spec.y_label = "share of system downtime"
            spec.provenance = {**(spec.provenance or {}),
                               "measure": "downtime share"}
        spec = hier.annotate(spec, meta, note=note)
    return spec

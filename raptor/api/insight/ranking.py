"""How many items a ranked chart shows, and the table of every item behind it.

A ranking chart draws its largest few items and gathers the rest into an
"N others" cell.  People worry about the 11th item, so two things live here,
free of any GUI so the MCP server and the CLI can use them too:

* `SHOW_RULES`: per chart, the default count, the counts worth offering and
  how far the chart stays readable (an overlay of 20 lines is not).  Each
  chart builder already takes ``top``; ``None`` means every item.
* `ranked_table`: every item behind a ranked chart, unfolded (the members of
  an "N others" cell and the rows cut off the chart included), with the same
  value, share and cumulative share the chart uses, ready for a table or CSV.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeGuard

from raptor.api.insight.attribution import CAUSE_LABELS

#: The counts a ranking offers; None is every item.
SHOW_BASE: tuple[int | None, ...] = (5, 10, 20, 50, None)


@dataclass(frozen=True)
class ShowRule:
    """What one ranked chart offers for "Show".

    `default` is what the chart draws until the reader chooses.  `choices`
    are the offered counts (None = all); the default is always among them.
    `note` is added to the control's tooltip where the chart is capped."""
    default: int
    choices: tuple[int | None, ...] = SHOW_BASE
    note: str = ""

    def options(self) -> list[int | None]:
        counts = {c for c in self.choices if c is not None} | {self.default}
        ordered: list[int | None] = [*sorted(counts)]
        if None in self.choices:
            ordered.append(None)
        return ordered


_BUBBLE = ("Drawn as bubbles, which overlap past about 50: the All items "
           "table lists every element.")
_OVERLAY = ("A line chart stays readable up to about six lines: the All items "
            "table lists every hierarchy.")

#: Chart key -> its Show rule.  A chart that is not here is not a ranking
#: (a time series, a histogram, a treemap that already nests everything).
SHOW_RULES: dict[str, ShowRule] = {
    "hierarchy-scorecard": ShowRule(8),
    "hierarchy-pareto": ShowRule(12),
    "hierarchy-delta": ShowRule(10),
    "component-failures": ShowRule(10),
    "downtime-pareto": ShowRule(10),
    "cost-pareto": ShowRule(10),
    "lost-capacity-pareto": ShowRule(10),
    "frequency-duration": ShowRule(20, (5, 10, 20, 50), _BUBBLE),
    "capacity-vs-downtime": ShowRule(20, (5, 10, 20, 50), _BUBBLE),
    "hierarchy-overlay": ShowRule(4, (4, 5, 6), _OVERLAY),
    "hierarchy-small-multiples": ShowRule(
        4, (4, 6, 8, 12), "One panel each: more than 12 do not fit a page."),
    "hierarchy-hops": ShowRule(
        4, (4, 6, 8, 12), "One strip each: more than 12 do not fit a page."),
}

#: How many hierarchies the comparison charts take when the reader picks
#: them by hand (the same cap as the chart's Show choices).
COMPARE_LIMITS: dict[str, int] = {
    "hierarchy-overlay": 6,
    "hierarchy-small-multiples": 12,
    "hierarchy-hops": 12,
    "capacity-sankey": 4,
}

#: The most any comparison chart takes.
COMPARE_MAX = max(COMPARE_LIMITS.values())

#: Charts whose rows are a ranking of every hierarchy at a level, drawn as a
#: handful picked by the reader or the top few (see `provenance["ranking"]`).
RANKED_KINDS = ("pareto", "capacity_pareto", "scorecard", "bubble", "scatter",
                "delta")


def show_label(count: int | None) -> str:
    """`Top 10`, or `All`."""
    return "All" if count is None else f"Top {count}"


@dataclass
class Column:
    """One column of the all-items table.  `kind` is text, int, percent,
    value (formatted by the chart's own format) or number."""
    key: str
    title: str
    kind: str = "text"


@dataclass
class RankedTable:
    """Every item behind a ranked chart."""
    title: str
    columns: list[Column]
    rows: list[dict[str, Any]]
    #: the chart's value format ("percent", "money", "count" or "")
    format: str = ""
    currency: str | None = None
    note: str = ""
    shown: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def header(self) -> list[str]:
        """Column titles for a CSV, saying that a share is a fraction."""
        out = []
        for col in self.columns:
            fraction = col.kind == "percent" or (
                col.kind == "value" and self.format == "percent")
            out.append(f"{col.title} (fraction: 1 = 100 %)" if fraction else col.title)
        return out

    def grid(self) -> list[list[Any]]:
        """The rows as plain values, in column order (None -> empty)."""
        return [["" if row.get(col.key) is None else row[col.key]
                 for col in self.columns] for row in self.rows]


def _number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _main_cause(row: dict[str, Any]) -> str:
    if row.get("cause"):
        return str(row["cause"])
    causes = row.get("causes") or {}
    return max(causes, key=lambda key: float(causes[key])) if causes else ""


def _flatten(spec: Any) -> list[dict[str, Any]]:
    """The chart's rows in rank order: those drawn (a folded "N others"
    row opened into its members), then the rows cut off the chart."""
    rows: list[dict[str, Any]] = []
    for row in spec.series:
        if row.get("items"):
            rows.extend(dict(item) for item in row["items"])
        else:
            rows.append(dict(row))
    rows.extend(dict(row) for row in spec.provenance.get("cut") or [])
    return rows


def is_ranked(spec: Any) -> bool:
    """Whether `ranked_table` has a table for this chart."""
    if spec is None:
        return False
    if spec.provenance.get("ranking"):
        return True
    return spec.kind in RANKED_KINDS and bool(spec.series)


def ranked_table(spec: Any) -> RankedTable | None:  # noqa: C901, PLR0912, PLR0915 - one column set per chart shape
    """Every item behind a ranked chart, or None where the chart is no ranking."""
    if not is_ranked(spec):
        return None
    prov = spec.provenance
    fmt = str(prov.get("format") or "")
    kind = spec.kind
    columns: list[Column] = [Column("rank", "rank", "int"),
                             Column("name", "name")]
    shown = len(spec.series)
    ranking = prov.get("ranking")
    if ranking:
        rows = [dict(row) for row in ranking]
        shown = sum(1 for row in rows if row.get("shown"))
        value_title = "share of system downtime"
        fmt = "percent"
        for row in rows:
            row["share"] = float(row.get("value") or 0.0)
        columns += [Column("path", "level / path"),
                    Column("value", value_title, "value"),
                    Column("cumulative", "cumulative share", "percent"),
                    Column("shown", "on the chart")]
        for row in rows:
            row["shown"] = "yes" if row.get("shown") else ""
    else:
        rows = _flatten(spec)
        shown = sum(1 for row in spec.series if not row.get("items"))
        if kind == "bubble":
            for row in rows:
                row["value"] = row.get("area")
            columns += [Column("x", str(spec.x_label), "number"),
                        Column("y", str(spec.y_label), "number"),
                        Column("value", "share of downtime", "percent"),
                        Column("cumulative", "cumulative share", "percent")]
            fmt = "percent"
        elif kind == "scatter":
            for row in rows:
                row["value"] = row.get("y")
            columns += [Column("x", str(spec.x_label), "percent"),
                        Column("value", str(spec.y_label), "percent"),
                        Column("cumulative", "cumulative share", "percent")]
            fmt = "percent"
        elif kind == "delta":
            columns += [Column("path", "level / path"),
                        Column("before", "before", "number"),
                        Column("after", "after", "number"),
                        Column("value", "change", "number"),
                        Column("status", "status"),
                        Column("cause", "main cause")]
        else:
            has_path = any(row.get("path") for row in rows)
            if has_path:
                columns.append(Column("path", "level / path"))
            columns.append(Column("value", str(spec.y_label or "value"),
                                  "value"))
            if fmt != "percent" and kind != "capacity_pareto":
                # a percent chart's value already is the share
                columns.append(Column("share", "share", "percent"))
            columns.append(Column("cumulative", "cumulative share", "percent"))
            if any(_main_cause(row) for row in rows):
                columns.append(Column("cause", "main cause"))
            for key, title, col_kind in (
                    ("sem", "standard error", "value"),
                    ("downtime", "share of downtime", "percent"),
                    ("lost_energy", "lost energy", "number"),
                    ("outages", "outages a run", "number"),
                    ("members", "members", "int"),
                    ("starters", "starters", "int")):
                if any(_number(row.get(key)) for row in rows):
                    columns.append(Column(key, title, col_kind))
    # share and cumulative share, where the chart's value allows one
    total = sum(abs(float(row.get("value") or 0.0)) for row in rows
                if _number(row.get("value")))
    running = 0.0
    has_share = kind not in ("delta",)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        row["cause"] = CAUSE_LABELS.get(_main_cause(row), _main_cause(row))
        if not has_share:
            continue
        if not _number(row.get("share")):
            value = row.get("value")
            if not _number(value):
                row["share"] = None
            elif fmt == "percent" or kind == "capacity_pareto":
                row["share"] = float(value)
            elif total > 0:
                row["share"] = abs(float(value)) / total
            else:
                row["share"] = None
        if _number(row.get("share")):
            running += float(row["share"])
            row["cumulative"] = running
    note = ""
    if kind == "delta":
        note = "ranked by the size of the change"
    elif fmt != "percent" and total > 0:
        note = "share is of the items listed here"
    return RankedTable(str(spec.title), columns, rows, fmt,
                       prov.get("currency"), note, shown)

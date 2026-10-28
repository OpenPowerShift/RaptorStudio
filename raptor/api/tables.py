"""The model's tables and a run's result tables, as plain data.

This is the one place that decides WHAT a "table" is, for both the GUI's
Export Tables dialog and the MCP server's ``list_tables`` / ``get_table`` /
``export_tables`` tools, so the two can never disagree about a table's
columns or contents. Nothing here imports Qt.

Two groups, as in Raptor 7's Save.TXT picker:

* **Inputs** -- the model's own data, one table per kind of element (blocks,
  nodes, events, hierarchies, links) and per pool/trigger/phase list, plus
  the system settings. Taken straight from the model's rows, so nothing the
  file holds is left out; blocks also get a short, readable summary table
  because the file has ~90 block columns.
* **Results** -- what a run produced: every section of its Final Results
  report, the per-run figures, capacity samples, and (optional, since they
  are long) the event log, the End-of-Run report and the interim cost
  report.

Cells hold what the model file holds (text such as ``"12.500000"``) or, for
computed tables, numbers. Writers decide presentation: the spreadsheet turns
numeric-looking cells into real numbers, TXT/CSV keep the text as written.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

INPUTS = "Inputs"
RESULTS = "Results"

Cell = Any          # str | int | float | None


class UnknownTable(KeyError):
    """A table key that does not exist for this model/run."""

    def __str__(self) -> str:            # KeyError would quote the message
        return str(self.args[0]) if self.args else "unknown table"


@dataclass
class Table:
    key: str                       # stable id: "inputs.blocks", "results.summary"
    title: str                     # what a person sees: "Blocks", "Summary"
    group: str                     # INPUTS | RESULTS
    headers: list[str]
    rows: list[list[Cell]]
    notes: list[str] = field(default_factory=list)
    #: Column indexes that must stay text even when a cell looks numeric
    #: (names, comments): a block called "12" is not the number 12.
    text_cols: set[int] = field(default_factory=set)
    #: The section a result table belongs to ("Summary", "Cost", "Weak
    #: link"), for grouping on screen and in a report. Empty for inputs.
    section: str = ""

    def to_dict(self, limit: int | None = None,
                offset: int = 0) -> dict[str, Any]:
        rows = self.rows[offset:] if limit is None else \
            self.rows[offset:offset + limit]
        return {"key": self.key, "title": self.title, "group": self.group,
                "headers": self.headers, "rows": rows,
                "total_rows": len(self.rows), "notes": self.notes}


@dataclass
class TableInfo:
    """A catalogue entry: what can be exported, and whether it can be now."""
    key: str
    title: str
    group: str
    rows: int = 0
    available: bool = True
    #: Why it cannot be exported yet ("run a simulation first").
    reason: str = ""
    #: Ticked by default in the picker (long tables are not).
    default: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "group": self.group,
                "rows": self.rows, "available": self.available,
                "reason": self.reason, "default": self.default}


@dataclass
class ResultsBundle:
    """Everything a run produced that the result tables are built from.

    Plain lists, so the GUI (which holds the run in memory) and the MCP
    server (which holds it on disk) can both fill one in."""
    model_name: str = ""
    num_runs: int = 0
    units: str = ""
    final_lines: Sequence[str] = ()
    end_of_run_lines: Sequence[str] = ()
    #: One dict per run: the figures of `raptor.api.run._rd_to_dict`.
    per_run: Sequence[dict] = ()
    capacity_lines: Sequence[str] = ()
    cost_lines: Sequence[str] = ()
    #: One list of event lines per run.
    event_logs: Sequence[Sequence[str]] = ()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^\*{3}(.+?)\*{3}\s*$")
#: A Final Results cell: an optional `>` censoring marker, an optional
#: minus, digits with `,` thousands separators and/or a `.` decimal point
#: (including scientific notation such as `1.5e-05`), an optional `%`, or
#: `N/A` for a figure the report declined to compute. Note the exponent
#: group -- a value the report writes in exponential form is still a
#: value, and a parser that drops it silently loses a whole table row.
_VALUE_RE = re.compile(r"^>?-?[\d,.]+([eE][+-]?\d+)?%?$|^N/A$")
_STAT_HEADERS = {"minimum", "mean"}
_NAMEISH = re.compile(r"name|comment|text", re.IGNORECASE)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "table"


def _is_header(tokens: list[str]) -> bool:
    """A column-header line is >= 2 tokens, NONE of them numeric. Data rows
    always carry numbers or N/A, so this cleanly separates the two."""
    if len(tokens) < 2:
        return False
    # `%Green` (Weak Link's State_Data) is a column name too.
    return all(t.replace(".", "").replace("_", "").lstrip("%").isalpha()
               for t in tokens)


#: A single-token line naming a PART of a section rather than a table:
#: Weak Link's NODE_ANALYSIS / BLOCK_ANALYSIS / EVENT_ANALYSIS /
#: HIERARCHY_ANALYSIS. Each part repeats the same table names
#: (Availability_Data, ...), so the part has to go into the table's name.
_PART_RE = re.compile(r"^[A-Z]+_ANALYSIS$")


def _row_values(tokens: list[str], columns: list[str] | None
                ) -> tuple[str, list[str]] | None:
    """One data row's (label, values), or None if the line is not one.

    Two real shapes, told apart by the token count alone: stats tables
    (the header holds only the stat columns, so the row is label +
    len(header) values) and named-row tables (the header's first token IS
    the label column, so the row is exactly len(header) tokens).
    `columns is None` is a label-only group (Cost_Summary, Grand_Total):
    label plus a single value, with no header of its own.
    """
    if columns is None:
        return (tokens[0], tokens[1:]) if len(tokens) >= 2 else None
    if len(tokens) == len(columns) - 1 and all(
            _VALUE_RE.match(v) for v in tokens):
        # A row with no label under a header that names its own label
        # column: Capacity's `FlowLoss MinLostFlow AveLostFlow MaxLostFlow`
        # over `50.00 91.39 889.38`.
        return "", tokens
    if len(tokens) not in (len(columns), len(columns) + 1):
        return None
    label, values = tokens[0], tokens[1:]
    if not all(_VALUE_RE.match(v) or v.isalpha() for v in values):
        return None
    return label, values


def _emit_section(sections: list, title: str | None,
                  groups: list[tuple[str, list[str] | None,
                                    list[tuple[str, list[str]]]]]) -> None:
    """Emit one `***SECTION***` block as one table per group.

    A group is normalised to its own data width, so no row is shorter than
    its neighbours. `_row_values` already refuses any row WIDER than its
    header -- that is the guard against a value column with no header
    above it, which is what produced the unlabelled F..J tail on the Cost
    sheet. A section with more than one group (COST has seven plus a
    summary tail) titles each table `<SECTION> - <label>`, since they can
    no longer share one header row."""
    if title is None or not groups:
        return
    multi = len(groups) > 1
    for label, columns, rows in groups:
        if not rows:
            continue
        if (columns is not None and label == columns[0] and len(rows) == 1
                and len(rows[0][1]) == len(columns)):
            # A header with no name line above it and no label column of
            # its own (Capacity's system row): name the table by its row.
            label = rows[0][0]
        width = max(len(v) for _, v in rows)
        cols = list(columns) if columns is not None else ["Item", "Value"]
        cols += [""] * max(0, width - len(cols))
        sections.append((f"{title} - {label}" if multi else title, cols,
                         [(lb, v + [""] * (width - len(v)))
                          for lb, v in rows]))


def _is_rule(tokens: list[str]) -> bool:
    """A rule line ("________________________________"). The engine
    separates the Cost sub-tables with them, and one took the next
    sub-table's name with it, so the sheet grew a table literally called
    "Cost - ______________..."."""
    return len(tokens) == 1 and set(tokens[0]) <= set("_-=~ ")


def _name_line(token: str, part: str | None
               ) -> tuple[str | None, str | None]:
    """``(part, pending)`` after a one-token line: a new part
    (NODE_ANALYSIS) or the name of the next table (Operating)."""
    if _PART_RE.match(token):
        return token, None
    return part, token


def parse_final_sections(lines: Iterable[str]
                         ) -> list[tuple[str, list[str],
                                         list[tuple[str, list[str]]]]]:
    """``[(section title, [columns], [(label, [values])])]`` for the
    ``***SECTION***``-delimited tables of a Final Results report.

    **The one place this parse happens.** The Results dock's Final Results
    tab (`gui.ui.results.ResultsDock._parse_final_sections`) and this
    module's own result tables both call this function, so the GUI's table
    view and the API/MCP one cannot disagree about which rows a report
    holds. A second copy of this loop in the GUI is a bug, not a variant.

    **One `***SECTION***` is not necessarily one table.** `***COST***`
    is seven tables plus a summary tail (Operating, Sparing,
    Life_cycle_cost, Event, Hierarchy, SparePools, Resources, then
    Cost_Summary/Grand_Total), each preceded by a single-token label line.
    Treating the whole block as one table kept *accumulating* rows while
    `columns` was reassigned on every header, so the sheet ended up with
    one header row sitting over rows from every one of those tables --
    which is where "the Cost table has columns F to J unlabelled but with
    values in them" came from. Each label line starts a new group here,
    and a section holding more than one group is emitted as one table per
    group, titled ``"<SECTION> - <label>"``.

    Rows are whitespace-split with the **first** token as the label and
    every remaining token as one of that row's values. The two table
    shapes are told apart by the token count alone:

    * a stats table's header holds only the stat columns
      (``Minimum Mean Maximum St.Dev SEM``), so a row is
      label + len(columns) values -- ``Availability 0.74 0.75 ...``;
    * a named-row table has the label column IN its header
      (``blockName SpareSource MinUsed ...``), so a row is exactly
      len(columns) tokens -- ``unnamed Custom 3 3.00 3 0.00``.

    A ``>`` prefix is the report's own censoring marker (``MTBDE``/``MDT``
    rows whose runs do not all contain a downing event carry it, e.g.
    ``MTBDE(8-runs)  4.94  >21.23  >46.54  N/A  N/A``) and is kept, so
    those rows reach the table.

    A label-only group (Cost_Summary, Grand_Total) has no header of its
    own -- it is label plus one value -- so it is given the two columns
    ``Item``/``Value``. Those rows used to be dropped entirely, taking the
    Cost Grand Total with them.
    """
    sections: list[tuple[str, list[str], list[tuple[str, list[str]]]]] = []
    title: str | None = None
    #: [(label, [columns] or None, [(label, [values])])] for the section
    #: being read.
    groups: list[tuple[str, list[str] | None,
                      list[tuple[str, list[str]]]]] = []
    #: A bare label line seen since the last group ("Operating",
    #: "Cost_Summary"): it NAMES the next group rather than being one.
    pending: str | None = None
    part: str | None = None

    for raw in lines:
        line = raw.rstrip()
        m = _SECTION_RE.match(line.strip())
        if m:
            _emit_section(sections, title, groups)
            title, groups, pending, part = m.group(1).strip(), [], None, None
            continue
        tokens = line.split()
        if title is None or not tokens or _is_rule(tokens):
            continue
        if _is_header(tokens):
            # A header always begins a group: everything accumulated so
            # far belongs to the previous header, not this one.
            name = pending or tokens[0]
            groups.append((f"{part} {name}" if part else name, tokens, []))
            pending = None
            continue
        if len(tokens) == 1:
            part, pending = _name_line(tokens[0], part)
            continue
        parsed = _row_values(tokens, groups[-1][1]) if groups else None
        if parsed is None and not groups and pending is None \
                and len(tokens) == 2:
            # Label/value lines before any header (Capacity's
            # `FlowGenerated 900`, `UnitsProduced 2425841.69`).
            pending = "Totals"
        if parsed is None:
            if pending is None:
                continue
            # A labelled group that does not continue the current
            # header's shape. Cost_Summary/Grand_Total (label + one
            # value) arrive after the last real table, whose wider header
            # they would never match -- which is how the Cost Grand Total
            # ended up dropped entirely.
            groups.append((pending, None, []))
            pending = None
            parsed = _row_values(tokens, None)
            if parsed is None:
                continue
        groups[-1][2].append(parsed)
    _emit_section(sections, title, groups)
    return sections


_SPARING_HEADERS = {
    "blockName": "Component", "SpareSource": "Spare Source",
    "MinUsed": "Min Used", "AveUsed": "Avg Used", "MaxUsed": "Max Used",
    "StDev": "Standard Deviation",
}


def _clean_number(text: str) -> str:
    """'1,234.5' -> '1234.5' so a spreadsheet can read it as a number."""
    return text.replace(",", "") if re.match(r"^-?[\d,]+(\.\d+)?$", text) \
        else text


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

#: (section, key, title, id column or None, name column or None)
_INPUT_SECTIONS: tuple[tuple[str, str, str, str | None, str | None], ...] = (
    ("BLOCK_DATA", "blocks_all", "Blocks (file columns)", "ID", "blockName"),
    ("NODE_DATA", "nodes", "Nodes", "ID", "Name"),
    ("EVENT_DATA", "events", "Events", "ID", "eventName"),
    ("HIERARCHY_DATA", "hierarchies", "Hierarchies", "ID", "hierarchyName"),
    ("SPARE_POOL_DATA", "spare_pools", "Spare Pools", None, "PoolName"),
    ("RESOURCE_DATA", "resources", "Resources", None, "PoolName"),
    ("TRIGGERS", "triggers", "Triggers", None, "TriggerName"),
    ("PHASES", "phases", "Phases", None, "PhaseName"),
    ("COMMENTS", "comments", "Comments", None, "Name"),
)


def _real_rows(rbd: Any, section: str, id_col: str | None) -> list[dict]:
    sec = rbd.sections.get(section)
    if sec is None:
        return []
    rows = []
    for r in sec.rows:
        if id_col is not None and str(r.get(id_col, "")).strip() in (
                "0", "0.0", "0.000000"):
            continue          # the ID=0 defaults template, not an element
        rows.append(r)
    return rows


def _raw_table(rbd: Any, section: str, key: str, title: str,
               id_col: str | None) -> Table | None:
    rows = _real_rows(rbd, section, id_col)
    if not rows:
        return None
    cols = list(rbd.sections[section].columns)
    return Table(
        key=f"inputs.{key}", title=title, group=INPUTS, headers=cols,
        rows=[[r.get(c, "") for c in cols] for r in rows],
        text_cols={i for i, c in enumerate(cols) if _NAMEISH.search(c)})


def _names(rbd: Any) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for kind, section, name_col in (("Block", "BLOCK_DATA", "blockName"),
                                    ("Node", "NODE_DATA", "Name"),
                                    ("Event", "EVENT_DATA", "eventName"),
                                    ("Hier", "HIERARCHY_DATA",
                                     "hierarchyName")):
        sec = rbd.sections.get(section)
        out[kind] = {str(r.get("ID", "")).strip(): r.get(name_col, "")
                     for r in (sec.rows if sec else [])}
    return out


def _links_table(rbd: Any) -> Table | None:
    rows = _real_rows(rbd, "LINK_DATA", "ID")
    if not rows:
        return None
    names = _names(rbd)

    def who(kind: str, ident: str) -> str:
        kind = kind.replace("RBD", "", 1)
        return names.get(kind, {}).get(str(ident).strip(), "")

    headers = ["ID", "From", "From Type", "From ID", "To", "To Type",
               "To ID", "Level"]
    body = [[r.get("ID", ""),
             who(r.get("FromType", ""), r.get("FromID", "")),
             r.get("FromType", ""), r.get("FromID", ""),
             who(r.get("ToType", ""), r.get("ToID", "")),
             r.get("ToType", ""), r.get("ToID", ""), r.get("pID", "")]
            for r in rows]
    return Table("inputs.links", "Links", INPUTS, headers, body,
                 text_cols={1, 4})


def _blocks_summary(rbd: Any) -> Table | None:
    rows = _real_rows(rbd, "BLOCK_DATA", "ID")
    if not rows:
        return None
    from raptor.api.distributions import dist_name

    def dist(code: Any) -> str:
        try:
            return dist_name(int(float(code)))
        except (TypeError, ValueError):
            return str(code)

    def params(r: dict, prefix: str) -> str:
        # Only as many as the distribution takes (`#FParam`/`#RParam`): a
        # Fixed repair is "8", not "8, 0, 0".
        try:
            count = int(float(r.get(f"#{prefix}", 3)))
        except (TypeError, ValueError):
            count = 3
        vals = []
        for i in range(1, max(0, min(count, 3)) + 1):
            v = str(r.get(f"{prefix}{i}", "")).strip()
            if v:
                try:
                    vals.append(f"{float(v):g}")
                except ValueError:
                    vals.append(v)
        return ", ".join(vals)

    headers = ["ID", "Name", "Parent", "Failure Distribution",
               "Failure Parameters", "Repair Distribution",
               "Repair Parameters", "Sparing", "Spare Pool", "PM",
               "PM Frequency", "Dependency"]
    body = []
    for r in rows:
        dep_num = str(r.get("DepNum", "")).strip()
        dep = ("" if dep_num in ("", "-2", "-1", "0") else
               f"{r.get('DepType', '')} {dep_num}".strip())
        pool = r.get("SparePoolName", "")
        body.append([
            r.get("ID", ""), r.get("blockName", ""), r.get("ParentId", ""),
            dist(r.get("FDist")), params(r, "FParam"),
            dist(r.get("RDist")), params(r, "RParam"),
            r.get("SpType", ""), "" if pool == "unnamed" else pool,
            "Yes" if str(r.get("usesPM", "0")).strip() not in (
                "0", "") else "No",
            r.get("PMFreq", ""), dep])
    return Table("inputs.blocks", "Blocks", INPUTS, headers, body,
                 notes=["A readable summary; 'Blocks (file columns)' has "
                        "every column of the model file."],
                 text_cols={1, 3, 4, 5, 6, 7, 8, 11})


def _system_table(rbd: Any) -> Table | None:
    sec = rbd.sections.get("SYSTEM_DATA")
    if sec is None or not sec.rows:
        return None
    row = sec.rows[0]
    from raptor.api.presentation import humanise
    skip = {"imageString", "NotUsed"}
    return Table("inputs.system", "System Settings", INPUTS,
                 ["Setting", "Value"],
                 [[humanise(c), row.get(c, "")] for c in sec.columns
                  if c not in skip],
                 text_cols={0})


_NODE_TYPES = {"1": "Start", "2": "Node", "3": "End", "4": "In", "5": "Out"}


def _dist_text(code: Any) -> str:
    from raptor.api.distributions import dist_name
    try:
        return dist_name(int(float(code)))
    except (TypeError, ValueError):
        return str(code)


def _readable(table: Table | None, keep: Sequence[str],
              dist_cols: Sequence[str] = (),
              flag_cols: Sequence[str] = ()) -> Table | None:
    """*table* cut to the columns in *keep* (in that order), with readable
    headers, distribution names, and Yes/No for 1/0 switches."""
    if table is None:
        return None
    from raptor.api.presentation import humanise

    def cell(header: str, value: Any) -> Any:
        if header in dist_cols:
            return _dist_text(value)
        if header in flag_cols:
            return {"1": "Yes", "0": "No"}.get(str(value).strip(), value)
        return value

    idx = [table.headers.index(c) for c in keep if c in table.headers]
    rows = [[cell(table.headers[i], r[i]) for i in idx] for r in table.rows]
    return Table(table.key, table.title, table.group,
                 [humanise(table.headers[i]) for i in idx], rows,
                 text_cols={0} | {n for n, i in enumerate(idx)
                                  if table.headers[i] in (*dist_cols,
                                                          *flag_cols)})


def _readable_nodes(rbd: Any) -> Table | None:
    rows = _real_rows(rbd, "NODE_DATA", "ID")
    if not rows:
        return None
    body = []
    for r in rows:
        kind = _NODE_TYPES.get(str(r.get("Type", "")).strip(), "")
        k, n = str(r.get("k", "")).strip(), str(r.get("n", "")).strip()
        need = f"{k} of {n}" if kind == "Node" and k not in ("", "-1") \
            else ""
        body.append([r.get("Name", ""), kind, need])
    return Table("inputs.nodes", "Nodes", INPUTS,
                 ["Name", "Type", "Needs good"], body, text_cols={0, 1, 2})


def report_input_tables(rbd: Any) -> list[Table]:
    """The model's data as a printed report shows it: the readable block
    summary, and the other elements cut to the columns a reader needs,
    with words for the headers (the file's own columns are in the
    spreadsheet export)."""
    raw = {t.key: t for t in input_tables(rbd)}
    blocks = raw.get("inputs.blocks")
    if blocks is not None:
        blocks = _readable(blocks, [h for h in blocks.headers
                                    if h not in ("ID", "Parent")])
    if blocks is not None and "PM" in blocks.headers:
        pm, freq = blocks.headers.index("PM"), blocks.headers.index(
            "PM frequency")
        for r in blocks.rows:           # a frequency without PM is unused
            if r[pm] == "No":
                r[freq] = ""
    out = [
        blocks, _readable_nodes(rbd),
        _readable(raw.get("inputs.spare_pools"),
                  ["PoolName", "Stock", "RSO", "NewSp", "ArriveEvery",
                   "ESO", "EmerTime", "SLO", "Level", "Quan", "SpCost"],
                  flag_cols=("RSO", "ESO", "SLO")),
        _readable(raw.get("inputs.resources"),
                  ["PoolName", "NumIn", "SpareCost", "FixedPerUse",
                   "PerTimeUnit"]),
        _readable(raw.get("inputs.triggers"),
                  ["TriggerName", "Repeats", "InitUsed", "Dist", "Param1",
                   "Param2", "Param3"], dist_cols=("Dist",),
                  flag_cols=("Repeats",)),
        _readable(raw.get("inputs.phases"),
                  ["PhaseName", "Num", "Mission", "Dist", "Param1",
                   "Param2", "Param3"], dist_cols=("Dist",),
                  flag_cols=("Mission",)),
    ]
    return [x for x in out if x is not None and x.rows]


def input_tables(rbd: Any) -> list[Table]:
    """Every input table the model has, in a fixed, sensible order."""
    out: list[Table | None] = [_system_table(rbd), _blocks_summary(rbd)]
    for section, key, title, id_col, _name in _INPUT_SECTIONS:
        out.append(_raw_table(rbd, section, key, title, id_col))
        if key == "hierarchies":
            out.append(_links_table(rbd))
    order = ["inputs.system", "inputs.blocks", "inputs.blocks_all",
             "inputs.nodes", "inputs.events", "inputs.hierarchies",
             "inputs.links", "inputs.spare_pools", "inputs.resources",
             "inputs.triggers", "inputs.phases", "inputs.comments"]
    tables = [t for t in out if t is not None]
    tables.sort(key=lambda t: order.index(t.key) if t.key in order else 99)
    return tables


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------

_PER_RUN_LABELS = {
    "availability": "Availability", "reliability": "Reliability",
    "conditional_reliability": "Conditional Reliability",
    "dependability": "Dependability",
    "mtbde": "MTBDE", "mdt": "MDT", "mtbm": "MTBM", "mtbmu": "MTBMu",
    "mtbms": "MTBMs", "mmt": "MMT", "mmtu": "MMTu", "mmts": "MMTs",
    "num_system_failures": "System Failures",
    "num_component_failures": "Component Failures",
    "green_percent": "Green %", "yellow_percent": "Yellow %",
    "red_percent": "Red %",
}

#: Result tables that are long and rarely wanted: off by default.
_LONG = {"results.event_log", "results.end_of_run", "results.interim_cost"}


def _section_table(title: str, columns: list[str],
                   rows: list[tuple[str, list[str]]],
                   units: str) -> Table:
    from raptor.api.presentation import (
        humanise, kind_of, section_of, table_title, with_unit)
    is_stats = {c.lower() for c in columns[:2]} == _STAT_HEADERS
    name = table_title(title)
    #: The header has no column for the row label (a stats table, or
    #: Capacity's system/node rows): every row is label + len(columns).
    own_label = bool(rows) and all(len(v) == len(columns) for _, v in rows)
    measures = is_stats or columns[:2] == ["Item", "Value"]
    if own_label:
        first = "Measure" if is_stats else (
            "Node" if "Node" in name else "")
        headers = [first] + [humanise(c) for c in columns]
        measures = measures or not first
    else:
        headers = [humanise(c) if c else "" for c in columns]

    def label_text(label: str) -> str:
        # Measures read as words, with the time unit; element names (a
        # block called "S1B1") are left exactly as the model has them.
        if label == "Totals" or label.endswith("_Totals"):
            return "Total"              # Block_Totals, SparePool_Totals
        if not measures:
            return label
        return with_unit(humanise(label), kind_of(label), units)

    body = [[label_text(label)] + [_clean_number(v) for v in values]
            for label, values in rows]
    if body and all(r[0] == "" for r in body):
        # Capacity's lost-flow row has no label: drop the empty column.
        headers, body = headers[1:], [r[1:] for r in body]
    return Table(f"results.{_slug(title)}", name, RESULTS, headers, body,
                 text_cols={0}, section=section_of(title))


def _per_run_table(per_run: Sequence[dict]) -> Table | None:
    if not per_run:
        return None
    keys = [k for k in _PER_RUN_LABELS if any(k in r for r in per_run)]
    keys += [k for k in per_run[0] if k not in _PER_RUN_LABELS]
    headers = ["Run"] + [_PER_RUN_LABELS.get(k, k) for k in keys]
    body = [[i + 1] + [r.get(k) for k in keys] for i, r in enumerate(per_run)]
    return Table("results.per_run", "Per-Run Results", RESULTS, headers, body)


def _capacity_table(lines: Sequence[str]) -> Table | None:
    if not lines:
        return None
    from raptor.api import series as series_mod
    runs = series_mod.parse_capacity_runs("\n".join(lines))
    body = [[n, t, f, c] for n, run in enumerate(runs, 1)
            for t, f, c in zip(run["t"], run["flow"], run["capacity"])]
    if not body:
        return None
    return Table("results.capacity_samples", "Capacity Samples", RESULTS,
                 ["Run", "Time", "Flow", "Capacity"], body)


def _text_table(key: str, title: str, lines: Sequence[str],
                per_run_split: Sequence[Sequence[str]] | None = None
                ) -> Table | None:
    if per_run_split:
        body = [[n, line] for n, log in enumerate(per_run_split, 1)
                for line in log]
        headers = ["Run", "Line"]
        text_cols = {1}
    else:
        body = [[line] for line in lines]
        headers = ["Line"]
        text_cols = {0}
    if not body:
        return None
    return Table(f"results.{key}", title, RESULTS, headers, body,
                 text_cols=text_cols)


#: The event log's own five fields. The engine writes each line
#: tab-separated (`raptor/core/engine/eventlog.py`:
#: ``time \t type \t name \t action \t comment``), so the split is a
#: parse, not a guess. Defined here rather than in the Qt model that
#: displays them, because this module is Qt-free by design;
#: `gui.ui.event_log_model` imports them from here so there is one copy.
EVENT_LOG_HEADERS: tuple[str, ...] = ("Time", "Type", "Element", "State",
                                     "Detail")


def parse_event_log_line(line: str) -> tuple[str, str, str, str, str]:
    """One tab-separated event-log line -> its five fields.

    Short lines keep their whole text in the first column rather than
    being dropped, so an unexpected line is still visible downstream."""
    parts = [p.strip() for p in line.split("\t")]
    if len(parts) >= 5:
        return (parts[0], parts[1], parts[2], parts[3],
                "\t".join(parts[4:]))
    if len(parts) == 4:
        return (parts[0], parts[1], parts[2], parts[3], "")
    return (line, "", "", "", "")


def _event_log_table(per_run: Sequence[Sequence[str]]) -> Table | None:
    """The event log with one FIELD PER COLUMN.

    The engine already writes tab-separated lines and `LogLine` carries
    the five fields as attributes; the export used to drop the whole raw
    line into a single "Line" cell, so a spreadsheet saw one opaque string
    per event and nothing could be sorted, filtered or charted. The split
    itself is `EVENT_LOG_HEADERS`/`parse_event_log_line`, which live here
    rather than in the Qt model that also needs them -- `raptor.api` must
    not import PySide6.

    `Run` is prepended because the log is per run. Time is numeric so a
    spreadsheet reads it as a number; names and states stay text.
    """
    body: list[list[Any]] = []
    for n, log in enumerate(per_run, 1):
        for line in log:
            row = parse_event_log_line(line)
            body.append([n, row[0], row[1], row[2], row[3], row[4]])
    if not body:
        return None
    return Table("results.event_log", "Event Log", RESULTS,
                 ["Run"] + list(EVENT_LOG_HEADERS), body,
                 text_cols={3, 4, 5})


def result_tables(bundle: ResultsBundle) -> list[Table]:
    """Every result table the run has. Sections of the Final Results report
    come first (Summary, then the rest in the report's own order)."""
    out: list[Table] = []
    seen: set[str] = set()
    for title, columns, rows in parse_final_sections(bundle.final_lines):
        t = _section_table(title, columns, rows, bundle.units)
        if t.key in seen:
            continue
        seen.add(t.key)
        out.append(t)
    for extra in (_per_run_table(bundle.per_run),
                  _capacity_table(bundle.capacity_lines),
                  _text_table("interim_cost", "Interim Cost Report",
                              list(bundle.cost_lines)),
                  _text_table("end_of_run", "End-of-Run Report",
                              list(bundle.end_of_run_lines)),
                  _event_log_table([list(x) for x in bundle.event_logs])):
        if extra is not None and extra.key not in seen:
            seen.add(extra.key)
            out.append(extra)
    return out


# --------------------------------------------------------------------------
# catalogue and collection
# --------------------------------------------------------------------------

def catalogue(rbd: Any, bundle: ResultsBundle | None = None
              ) -> list[TableInfo]:
    """What can be exported, with row counts. Result tables are listed
    (unavailable, with the reason) even before a run, so the picker shows
    what a run would add."""
    infos = [TableInfo(t.key, t.title, INPUTS, len(t.rows),
                       default=t.key != "inputs.blocks_all")
             for t in input_tables(rbd)]
    if bundle is None or not (bundle.final_lines or bundle.per_run):
        infos.append(TableInfo(
            "results.summary", "Summary and other result tables", RESULTS,
            0, available=False, reason="run a simulation first"))
        return infos
    infos += [TableInfo(t.key, t.title, RESULTS, len(t.rows),
                        default=t.key not in _LONG)
              for t in result_tables(bundle)]
    return infos


def collect(rbd: Any, bundle: ResultsBundle | None = None,
            keys: Iterable[str] | None = None) -> list[Table]:
    """The requested tables (all defaults if `keys` is None), in catalogue
    order. Raises :class:`UnknownTable` naming the valid keys if any key
    does not exist -- silently dropping a request would hand back a
    partial export that looks complete."""
    tables = input_tables(rbd)
    if bundle is not None and (bundle.final_lines or bundle.per_run):
        tables += result_tables(bundle)
    by_key = {t.key: t for t in tables}
    if keys is None:
        return [t for t in tables
                if t.key not in _LONG and t.key != "inputs.blocks_all"]
    wanted = list(dict.fromkeys(keys))
    missing = [k for k in wanted if k not in by_key]
    if missing:
        raise UnknownTable(
            f"unknown or unavailable table(s) {missing}; available: "
            f"{sorted(by_key)}")
    return [by_key[k] for k in by_key if k in set(wanted)]

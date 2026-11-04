"""Write tables (see :mod:`raptor.api.tables`) to disk.

Three outputs, any combination:

* **CSV** -- one ``.csv`` per table, UTF-8 with a BOM so a spreadsheet opens
  accented names correctly.
* **Text** -- a fixed-width ``.txt`` per table, or ONE combined report with
  every table in it (Raptor 7's Save.TXT, made one file).
* **Spreadsheet** -- ONE workbook, Excel (``.xlsx``) and/or OpenDocument
  (``.ods``), with a tab per table, real numeric cells shown to the places
  `raptor.api.presentation` uses, a Contents tab, a Run Info tab when there
  are results and a Glossary of the abbreviations used. Layout, colour and
  print set-up are `raptor.core.fileio.workbook`'s, shared by every
  spreadsheet export. The End-of-Run and interim cost reports are text, not
  tables: they go beside the workbook as ``.txt`` files.

Files go in a folder named ``Tables`` next to the model by default
(:func:`default_folder`), and are named ``<model>_<table>.<ext>`` /
``<model>_tables.<ext>``. Nothing here imports Qt.
"""
from __future__ import annotations

import csv
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from raptor.api.glossary import TERMS
from raptor.api.presentation import RunInfo, table_kinds
from raptor.api.tables import INPUTS, RESULTS, Table
from raptor.core.fileio.workbook import Sheet, WorkbookInfo, write_workbook

#: The folder tables are written to, beside the model file. Also the name of
#: the ribbon button, the dialog and the MCP tools ("Tables"), so the same
#: word means the same thing everywhere.
TABLES_DIRNAME = "Tables"


@dataclass
class ExportOptions:
    csv: bool = False              # one .csv per table
    txt_files: bool = False        # one .txt per table
    txt_report: bool = False       # one combined .txt
    xlsx: bool = False             # one Excel workbook, a tab per table
    ods: bool = False              # the same workbook as OpenDocument

    def any(self) -> bool:
        return (self.csv or self.txt_files or self.txt_report or self.xlsx
                or self.ods)

    def workbook_types(self) -> list[str]:
        """The workbook extensions chosen, Excel first."""
        return [ext for ext, on in (("xlsx", self.xlsx), ("ods", self.ods))
                if on]


@dataclass
class ExportResult:
    folder: Path
    files: list[Path] = field(default_factory=list)
    tables: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"folder": str(self.folder), "tables": self.tables,
                "files": [str(f) for f in self.files]}


def default_folder(model_path: str | Path | None) -> Path:
    """``<folder of the model>/Tables``; beside the current directory for a
    model that has never been saved."""
    base = Path(model_path).resolve().parent if model_path else Path.cwd()
    return base / TABLES_DIRNAME


def safe_stem(name: str) -> str:
    """A file-name stem from a model name."""
    stem = Path(str(name)).stem if name else ""
    return re.sub(r"[^\w.\-]+", "_", stem).strip("._") or "model"


def _file_slug(key: str) -> str:
    return key.replace(".", "_")


def plan_files(tables: Sequence[Table], options: ExportOptions,
               stem: str) -> list[str]:
    """The file names an export would write (for an overwrite warning)."""
    names: list[str] = []
    if options.csv:
        names += [f"{stem}_{_file_slug(t.key)}.csv" for t in tables]
    if options.txt_files:
        names += [f"{stem}_{_file_slug(t.key)}.txt" for t in tables]
    if options.txt_report:
        names.append(f"{stem}_tables.txt")
    for ext in options.workbook_types():
        names.append(f"{stem}_tables.{ext}")
    if options.workbook_types() and not options.txt_files:
        names += [f"{stem}_{_file_slug(t.key)}.txt" for t in tables
                  if t.key in TEXT_REPORTS]
    return names


#: Report text (one line per row, no columns): a workbook gets these as
#: `.txt` files beside it, not as sheets of tens of thousands of rows.
TEXT_REPORTS = frozenset({"results.end_of_run", "results.interim_cost"})


# --------------------------------------------------------------------------
# cell formatting
# --------------------------------------------------------------------------

_INT_RE = re.compile(r"^-?(0|[1-9]\d*)$")
_FLOAT_RE = re.compile(r"^-?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def _as_text(value: Any, blank: str = "") -> str:
    if value is None:
        return blank
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _spreadsheet_value(value: Any, is_text_col: bool) -> Any:
    """Numeric-looking text becomes a number, except in name/comment
    columns; anything else stays as it is."""
    if is_text_col or not isinstance(value, str):
        return value
    text = value.strip()
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        try:
            return float(text)
        except ValueError:
            return value
    return value


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------

def _is_number(text: str) -> bool:
    return bool(_INT_RE.match(text) or _FLOAT_RE.match(text.replace(",", "")))


def format_table_text(table: Table) -> str:
    """One table as fixed-width text: a title, the notes, the header, a rule
    and the rows. Numbers are right-aligned, text left-aligned."""
    lines = [table.title, "=" * len(table.title)]
    lines += table.notes
    if lines[2:]:
        lines.append("")
    if len(table.headers) == 1:               # a plain list of lines
        lines += [_as_text(r[0], "N/A") if r else "" for r in table.rows]
        return "\n".join(lines)
    cells = [[_as_text(v, "N/A") for v in row] for row in table.rows]
    column_count = len(table.headers)
    widths = [len(h) for h in table.headers]
    for row in cells:
        for i, c in enumerate(row[:column_count]):
            widths[i] = max(widths[i], len(c))
    right = [all(_is_number(row[i]) or row[i] in ("N/A", "") or
                 row[i].startswith(">")
                 for row in cells if i < len(row)) and cells and
             i not in table.text_cols for i in range(column_count)]

    def fmt(row: Sequence[str]) -> str:
        out = []
        for i in range(column_count):
            c = row[i] if i < len(row) else ""
            out.append(c.rjust(widths[i]) if right[i] else c.ljust(widths[i]))
        return "  ".join(out).rstrip()

    lines.append(fmt(list(table.headers)))
    lines.append("  ".join("-" * w for w in widths))
    lines += [fmt(r) for r in cells]
    return "\n".join(lines)


def format_report_text(tables: Sequence[Table], model_name: str,
                       info: RunInfo | None = None) -> str:
    """Every table in one text report, with a contents list."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    head = [f"RAPTOR Studio - Tables for {model_name}", f"Written {stamp}", ""]
    if info is not None and info.runs:
        width = max(len(k) for k, _ in info.rows())
        head += [f"  {k.ljust(width)}  {v}" for k, v in info.rows()] + [""]
    for group in (INPUTS, RESULTS):
        group_tables = [t for t in tables if t.group == group]
        if group_tables:
            head.append(f"{group}:")
            head += [f"  {t.title}  ({len(t.rows)} rows)"
                     for t in group_tables]
            head.append("")
    body = []
    for group in (INPUTS, RESULTS):
        group_tables = [x for x in tables if x.group == group]
        if not group_tables:
            continue
        rule = "#" * 72
        body.append(f"{rule}\n# {group.upper()}\n{rule}")
        body += [format_table_text(t) for t in group_tables]
    return "\n".join(head) + "\n" + "\n\n\n".join(body) + "\n"


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def _sheet_titles(tables: Sequence[Table]) -> list[str]:
    """Tab names: the table's title, prefixed with its group only where two
    tables would otherwise share a name (an input 'Resources' and a result
    'Resources')."""
    # Tab names are unique ignoring case, so "Spare Pools" (an input) and
    # "Spare pools" (a result) clash too.
    counts: dict[str, int] = {}
    for t in tables:
        counts[t.title.lower()] = counts.get(t.title.lower(), 0) + 1
    return [t.title if counts[t.title.lower()] == 1
            else f"{t.group} - {t.title}" for t in tables]


def _csv_safe(value: Any) -> str:
    """Neutralise spreadsheet formula injection in a CSV cell.

    Excel treats a cell beginning with ``=``, ``+``, ``-``, ``@``, TAB or
    CR as a formula.  A model name/comment is untrusted text exported by
    this tool, so prefix those with an apostrophe (the standard CSV
    mitigation).  A plain negative number keeps its numeric form.
    """
    text = _as_text(value)
    if not text:
        return text
    first = text[0]
    if first in ("=", "+", "@", "\t", "\r", "\n"):
        return "'" + text
    if first == "-" and not _is_number(text):
        return "'" + text
    return text


def _write_csv(table: Table, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([_csv_safe(h) for h in table.headers])
        for row in table.rows:
            w.writerow([_csv_safe(v) for v in row])


def _run_info_sheet(info: RunInfo) -> Sheet:
    return Sheet(name="Run Info", headers=["Item", "Value"],
                 rows=[[k, v] for k, v in info.rows()], text_cols={0, 1},
                 description="The run these results come from: model, "
                             "runs, settings and software")


#: The first sort of a result table: (table-key pattern, column header,
#: descending). The order puts what an engineer looks for first at the top:
#: the weakest element, the largest cost, the most-used spare.
_SORTS: tuple[tuple[str, str, bool], ...] = (
    (r"_(availability|dependability|reliability)_data$", "Mean", False),
    (r"_state_data$", "Red %", True),
    (r"_state_data$", "Repairing %", True),
    (r"^results\.weak_link_analysis_.*_mtbde$", "MTBDE", False),
    (r"^results\.sparing_spares_utilization$", "Avg used", True),
    (r"^results\.cost_", "Total", True),
    (r"^results\.extended_results_block_criticality$", "Failures %", True),
)


def _sort_for(table: Table) -> tuple[int, bool] | None:
    for pattern, header, descending in _SORTS:
        if re.search(pattern, table.key) and header in table.headers:
            return table.headers.index(header), descending
    return None


def _description(table: Table) -> str:
    where = table.section or ("Model input" if table.group == INPUTS
                              else table.group)
    return f"{where}: {table.title}" if where != table.title else where


def _text_columns(table: Table) -> set[int]:
    """The table's own text columns, and a first column of names."""
    cols = set(table.text_cols)
    first = [row[0] for row in table.rows[:50] if row]
    if first and all(isinstance(v, str) and not _is_number(v.strip())
                     and v.strip() not in ("", "N/A") for v in first):
        cols.add(0)
    return cols


def _table_sheet(title: str, table: Table) -> Sheet:
    col_kinds, row_kinds = table_kinds(table)
    scales = {}
    if table.key == "results.per_run" and "Availability" in table.headers:
        scales[table.headers.index("Availability")] = True
    return Sheet(name=title, headers=table.headers, notes=table.notes,
                 rows=[[_spreadsheet_value(v, i in table.text_cols)
                        for i, v in enumerate(row)] for row in table.rows],
                 formats=col_kinds, row_formats=row_kinds,
                 text_cols=_text_columns(table), title=table.title,
                 description=_description(table), sort=_sort_for(table),
                 scales=scales)


def workbook_info(model_name: str, info: RunInfo | None = None, *,
                  what: str = "tables") -> WorkbookInfo:
    """The Contents tab's title block and the page header/footer text."""
    subtitle = [f"Model: {model_name}"] if model_name else []
    if info is not None and info.runs:
        subtitle.append(f"Runs: {info.runs:,}"
                        + (f", {info.termination}" if info.termination
                           else ""))
    return WorkbookInfo(title=f"{model_name or 'Model'} - {what}",
                        subtitle=subtitle, source=model_name,
                        glossary=TERMS)


def export_tables(tables: Sequence[Table], folder: str | Path,
                  options: ExportOptions, *, model_name: str = "",
                  stem: str | None = None,
                  info: RunInfo | None = None,
                  progress: Callable[[str], None] | None = None
                  ) -> ExportResult:
    """Write `tables` to `folder` (created if needed) in the chosen formats.
    Existing files of the same names are replaced. Raises ValueError if no
    format or no table is chosen -- an export that writes nothing and says
    nothing is worse than an error. `progress` is called with each file's
    name before it is written (it may be called from a worker thread)."""
    if not tables:
        raise ValueError("no tables to export")
    if not options.any():
        raise ValueError("choose at least one format (CSV, text, "
                         "spreadsheet)")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stem = stem or safe_stem(model_name)
    result = ExportResult(folder=folder, tables=len(tables))
    tell = progress or (lambda _name: None)

    for t in tables:
        slug = _file_slug(t.key)
        if options.csv:
            p = folder / f"{stem}_{slug}.csv"
            tell(p.name)
            _write_csv(t, p)
            result.files.append(p)
        if options.txt_files:
            p = folder / f"{stem}_{slug}.txt"
            tell(p.name)
            p.write_text(format_table_text(t) + "\n", encoding="utf-8")
            result.files.append(p)
    if options.txt_report:
        p = folder / f"{stem}_tables.txt"
        tell(p.name)
        p.write_text(format_report_text(tables, model_name or stem, info),
                     encoding="utf-8")
        result.files.append(p)
    if options.workbook_types():
        result.files += _write_workbooks(
            tables, folder, stem, info, options.workbook_types(),
            model_name=model_name or stem,
            text_beside=not options.txt_files, tell=tell)
    return result


def workbook_sheets(tables: Sequence[Table],
                    info: RunInfo | None = None) -> list[Sheet]:
    """The workbook's tabs for `tables`: Run Info first when there are
    results, then a tab per table (text reports excluded)."""
    sheet_tables = [x for x in tables if x.key not in TEXT_REPORTS]
    sheets = [_table_sheet(title, x) for title, x in
              zip(_sheet_titles(sheet_tables), sheet_tables)]
    if info is not None and info.runs:
        sheets.insert(0, _run_info_sheet(info))
    return sheets


def write_tables_workbook(tables: Sequence[Table], path: str | Path, *,
                          model_name: str = "",
                          info: RunInfo | None = None) -> Path:
    """`tables` as ONE workbook at `path` (``.xlsx`` or ``.ods``, by its
    extension). Raises ValueError when no table can be a tab."""
    sheets = workbook_sheets(tables, info)
    if not sheets:
        raise ValueError("no tables to write to a workbook")
    return write_workbook(path, sheets, workbook_info(model_name, info))


def _write_workbooks(tables: Sequence[Table], folder: Path, stem: str,
                     info: RunInfo | None, types: Sequence[str], *,
                     model_name: str, text_beside: bool,
                     tell: Callable[[str], None] = lambda _n: None
                     ) -> list[Path]:
    """The workbook(s), and the text reports beside them."""
    written: list[Path] = []
    sheets = workbook_sheets(tables, info)
    if sheets:
        book = workbook_info(model_name, info)
        for ext in types:
            p = folder / f"{stem}_tables.{ext}"
            tell(p.name)
            write_workbook(p, sheets, book)
            written.append(p)
    if text_beside:
        for x in tables:
            if x.key in TEXT_REPORTS:
                tp = folder / f"{stem}_{_file_slug(x.key)}.txt"
                tell(tp.name)
                tp.write_text("\n".join(str(r[-1]) for r in x.rows) + "\n",
                              encoding="utf-8")
                written.append(tp)
    return written

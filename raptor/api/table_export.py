"""Write tables (see :mod:`raptor.api.tables`) to disk.

Three outputs, any combination:

* **CSV** -- one ``.csv`` per table, UTF-8 with a BOM so a spreadsheet opens
  accented names correctly.
* **Text** -- a fixed-width ``.txt`` per table, or ONE combined report with
  every table in it (Raptor 7's Save.TXT, made one file).
* **Spreadsheet** -- ONE ``.xlsx`` workbook with a tab per table, real
  numeric cells shown to the places `raptor.api.presentation` uses, and a
  Run Info tab first when there are results. The End-of-Run and interim
  cost reports are text, not tables: they go beside the workbook as
  ``.txt`` files.

Files go in a folder named ``Tables`` next to the model by default
(:func:`default_folder`), and are named ``<model>_<table>.<ext>`` /
``<model>_tables.<ext>``. Nothing here imports Qt.
"""
from __future__ import annotations

import csv
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from raptor.api.presentation import RunInfo, table_kinds
from raptor.api.tables import INPUTS, RESULTS, Table
from raptor.core.fileio.xlsx_writer import Sheet, write_xlsx

#: The folder tables are written to, beside the model file. Also the name of
#: the ribbon button, the dialog and the MCP tools ("Tables"), so the same
#: word means the same thing everywhere.
TABLES_DIRNAME = "Tables"


@dataclass
class ExportOptions:
    csv: bool = False              # one .csv per table
    txt_files: bool = False        # one .txt per table
    txt_report: bool = False       # one combined .txt
    xlsx: bool = False             # one workbook, a tab per table

    def any(self) -> bool:
        return self.csv or self.txt_files or self.txt_report or self.xlsx


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
    if options.xlsx:
        names.append(f"{stem}_tables.xlsx")
        if not options.txt_files:
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
    counts: dict[str, int] = {}
    for t in tables:
        counts[t.title] = counts.get(t.title, 0) + 1
    return [t.title if counts[t.title] == 1 else f"{t.group} - {t.title}"
            for t in tables]


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
                 rows=[[k, v] for k, v in info.rows()])


def _table_sheet(title: str, table: Table) -> Sheet:
    col_kinds, row_kinds = table_kinds(table)
    return Sheet(name=title, headers=table.headers, notes=table.notes,
                 rows=[[_spreadsheet_value(v, i in table.text_cols)
                        for i, v in enumerate(row)] for row in table.rows],
                 formats=col_kinds, row_formats=row_kinds)


def export_tables(tables: Sequence[Table], folder: str | Path,
                  options: ExportOptions, *, model_name: str = "",
                  stem: str | None = None,
                  info: RunInfo | None = None) -> ExportResult:
    """Write `tables` to `folder` (created if needed) in the chosen formats.
    Existing files of the same names are replaced. Raises ValueError if no
    format or no table is chosen -- an export that writes nothing and says
    nothing is worse than an error."""
    if not tables:
        raise ValueError("no tables to export")
    if not options.any():
        raise ValueError("choose at least one format (CSV, text, "
                         "spreadsheet)")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stem = stem or safe_stem(model_name)
    result = ExportResult(folder=folder, tables=len(tables))

    for t in tables:
        slug = _file_slug(t.key)
        if options.csv:
            p = folder / f"{stem}_{slug}.csv"
            _write_csv(t, p)
            result.files.append(p)
        if options.txt_files:
            p = folder / f"{stem}_{slug}.txt"
            p.write_text(format_table_text(t) + "\n", encoding="utf-8")
            result.files.append(p)
    if options.txt_report:
        p = folder / f"{stem}_tables.txt"
        p.write_text(format_report_text(tables, model_name or stem, info),
                     encoding="utf-8")
        result.files.append(p)
    if options.xlsx:
        result.files += _write_workbook(tables, folder, stem, info,
                                        text_beside=not options.txt_files)
    return result


def _write_workbook(tables: Sequence[Table], folder: Path, stem: str,
                    info: RunInfo | None, *, text_beside: bool) -> list[Path]:
    """The workbook (Run Info first), and the text reports beside it."""
    written: list[Path] = []
    sheet_tables = [x for x in tables if x.key not in TEXT_REPORTS]
    sheets = [_table_sheet(title, x) for title, x in
              zip(_sheet_titles(sheet_tables), sheet_tables)]
    if info is not None and info.runs:
        sheets.insert(0, _run_info_sheet(info))
    if sheets:
        p = folder / f"{stem}_tables.xlsx"
        write_xlsx(p, sheets)
        written.append(p)
    if text_beside:
        for x in tables:
            if x.key in TEXT_REPORTS:
                tp = folder / f"{stem}_{_file_slug(x.key)}.txt"
                tp.write_text("\n".join(str(r[-1]) for r in x.rows) + "\n",
                              encoding="utf-8")
                written.append(tp)
    return written

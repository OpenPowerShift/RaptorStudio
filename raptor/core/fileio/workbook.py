"""Spreadsheet export: one design, two file formats.

Every spreadsheet RAPTOR Studio writes (Export Tables, the analytical
export, Availability and Criticality, the command line and MCP) is a list
of :class:`Sheet` objects handed to :func:`write_workbook`. This module makes
every layout decision once -- the *plan* -- and a renderer turns the plan into
a file:

* ``.xlsx`` -- :mod:`raptor.core.fileio.xlsx_writer` (XlsxWriter);
* ``.ods``  -- :mod:`raptor.core.fileio.ods_writer` (OpenDocument).

So both formats look and behave the same:

* **Row 1 is the header** on every data tab, so the file still reads straight
  into pandas, R or MATLAB. Titles, the model and the date go in the printed
  page header and footer and on the Contents tab, not above the data.
* **Numbers are numbers**, right-aligned, shown to the places their kind of
  quantity needs (probabilities to six places, costs to two, counts whole);
  the cell keeps full precision. A censored estimate (``>21.2``) stays a
  number with a ``>`` in its format; ``N/A`` is grey text.
* **Every data tab** has a frozen header (and frozen label column on a wide
  table), a filter on the header, shading on every other row that follows
  sorting and filtering, and a sensible first sort.
* **Colour means something**: bars show a share or a total's size within its
  column (state percentages in their own state colour), and orange marks the
  lowest values of an availability or reliability column. Nothing is
  coloured for decoration.
* **Printing**: each tab is set up to print as it is -- A4, portrait or
  landscape by its width, one page wide, header row repeated, with the
  workbook and tab in the page header and page numbers in the footer.
* **Acronyms** (MTBDE, SEM, RAW, ...) carry a comment on their header, and a
  Glossary tab at the end lists every one the workbook uses.

Nothing here imports Qt.
"""
from __future__ import annotations

import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

#: Excel's hard limits (an .ods written for Excel users keeps them too).
MAX_ROWS = 1_048_576
MAX_SHEET_NAME = 31

_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
#: XML 1.0 forbids most control characters, whatever the escaping.
BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

# --------------------------------------------------------------------------
# palette (one place, both formats)
# --------------------------------------------------------------------------

INK = "#1F3A5F"          # header fill, title text
HEADER_TEXT = "#FFFFFF"
BAND = "#F2F5F9"         # every other row
RULE = "#8EA3BD"         # the line under the header
MUTED = "#7F7F7F"        # N/A, notes
LINK = "#1F5FA8"
BAR = "#9FC5E8"          # a share or a total (light: the number stays legible)
LOW = "#F9CB9C"          # the low end of an availability scale
LOW_TO = "#FFFFFF"
#: Bars for state percentages, in the state's own colour (Raptor 7's
#: Appendix D colours, `gui.resources.design_tokens`), lightened so the
#: number on the bar stays legible. Longer phrases are matched first.
STATE_BARS: tuple[tuple[str, str], ...] = (
    ("repair hold", "#DD7E6B"),      # Firebrick
    ("pm hold", "#FFE599"),          # Gold
    ("running", "#B6D7A8"),          # LimeGreen
    ("standby", "#9FC5E8"),          # Blue
    ("idle", "#D5B8A6"),             # Sienna
    ("repairing", "#EA9999"),        # Red
    ("pm", "#F9CB9C"),               # Orange
    ("done", "#E6B8AF"),             # IndianRed
    ("green", "#B6D7A8"), ("yellow", "#FFE599"), ("red", "#EA9999"),
    ("blue", "#9FC5E8"), ("brown", "#D5B8A6"), ("orange", "#F9CB9C"),
    ("purple", "#D9D2E9"),
)


def state_colour(header: str) -> str | None:
    """The bar colour for a state-percentage column, or None."""
    low = header.lower()
    for phrase, colour in STATE_BARS:
        if re.search(rf"(?<![a-z]){phrase}(?![a-z])", low):
            return colour
    return None

# --------------------------------------------------------------------------
# number formats (Excel codes; the .ods writer translates them)
# --------------------------------------------------------------------------

#: Format key -> Excel number-format code.
NUMBER_FORMATS: dict[str, str] = {
    "general": "General",
    "prob": "0.000000",
    "pct": "0.00",
    "time": "#,##0.000",
    "cost": "#,##0.00",
    "count": "#,##0",
    "count_avg": "#,##0.00",
    "id": "0",
    "ratio": "0.0000",
}
#: A censored estimate keeps its number and shows a ``>`` before it.
CENSORED_PREFIX = "gt_"
for _key, _code in list(NUMBER_FORMATS.items()):
    NUMBER_FORMATS[CENSORED_PREFIX + _key] = '">"' + _code

#: Columns that hold identifiers or run numbers: whole, no thousands comma.
ID_HEADERS = frozenset({"id", "run", "from id", "to id", "number", "num",
                        "idnum", "hiernum", "phase"})
#: Quantity kind -> format key. Besides `raptor.api.presentation`'s kinds:
#: "rate" (a density or hazard, six places) and "ratio" (RAW, RRW).
_KIND_FORMAT = {"prob": "prob", "pct": "pct", "time": "time",
                "cost": "cost", "count": "count", "rate": "prob",
                "ratio": "ratio"}

_NUMBER_TEXT = re.compile(r"^(>)?\s*(-?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?"
                          r"(?:[eE][+-]?\d+)?)$")
_NA = {"N/A", "NA", "n/a"}

#: Widths, in Excel character units.
_MIN_WIDTH, _MAX_NUMBER_WIDTH, _MAX_TEXT_WIDTH = 6.0, 18.0, 50.0
#: Above this total width a tab prints landscape.
_LANDSCAPE_WIDTH = 100.0
#: Above this, fitting one page wide would shrink the text past reading
#: (about half size on landscape A4): print across pages instead, at
#: `WIDE_PRINT_SCALE` %, with the first column repeated on every page.
_FIT_WIDTH = 230.0
WIDE_PRINT_SCALE = 70


# --------------------------------------------------------------------------
# the input
# --------------------------------------------------------------------------

@dataclass
class Sheet:
    """One tab: a header row and the rows under it.

    Only `name`, `headers` and `rows` are required; the rest are hints that
    make the tab read better. Values are written as they are: numbers as
    numbers, text as text (numeric-looking text outside `text_cols` becomes
    a number; ``>12.3`` a censored number)."""
    name: str
    headers: Sequence[str]
    rows: Sequence[Sequence[Any]]
    #: Free-text lines written under the table, after a blank row.
    notes: Sequence[str] = field(default_factory=tuple)
    #: The kind of each column's numbers ("prob", "pct", "time", "cost",
    #: "count", "num"; see `raptor.api.presentation`).
    formats: Sequence[str] = field(default_factory=tuple)
    #: Per-row kinds for a table of measures (one row per measure); a row's
    #: kind wins over its column's. Column 0 is then the measure's label.
    row_formats: Sequence[str] = field(default_factory=tuple)
    #: Columns of text (names, comments): left-aligned, never converted to
    #: numbers, wrapped when long.
    text_cols: Collection[int] = field(default_factory=frozenset)
    #: The tab's title for the printed page header and the Contents tab
    #: (default: `name`).
    title: str = ""
    #: One line for the Contents tab: what the tab holds.
    description: str = ""
    #: ``(column, descending)``: the order the rows are written in. Blank,
    #: ``N/A`` and text values go last either way. A note says so.
    sort: tuple[int, bool] | None = None
    #: Extra data-bar columns -> bar colour (on top of the automatic ones).
    bars: Mapping[int, str] = field(default_factory=dict)
    #: Extra colour-scale columns -> True when higher is better.
    scales: Mapping[int, bool] = field(default_factory=dict)
    #: Add the automatic colour rules (see the module docstring).
    highlight: bool = True


@dataclass
class WorkbookInfo:
    """What the workbook is: for the Contents tab, page headers/footers and
    the file's own properties."""
    #: "WaterTreatment - tables": the Contents title and page-header left.
    title: str = ""
    #: Lines under the title on the Contents tab ("Model: ...", "Runs: 20").
    subtitle: Sequence[str] = ()
    #: The page footer's left side, usually the model file's name.
    source: str = ""
    written: datetime = field(default_factory=datetime.now)
    #: Term -> meaning. Terms found in headers and row labels get a comment
    #: and a row on the Glossary tab.
    glossary: Mapping[str, str] = field(default_factory=dict)
    #: Add a Contents tab first (only when there are two or more tabs).
    contents: bool = True


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Style:
    """Everything a renderer needs to style one cell."""
    role: str = "data"        # data, na, total, header, note, title,
    #                           subtitle, link
    number: str = ""          # a NUMBER_FORMATS key, "" for text
    align: str = "left"       # left, right
    wrap: bool = False


@dataclass
class Cell:
    value: Any                # str, int, float, bool or None
    style: Style


@dataclass
class SheetPlan:
    name: str                 # the legal, unique tab name
    title: str
    description: str
    headers: list[Cell]
    rows: list[list[Cell]]    # data rows, in the order written
    notes: list[str]
    widths: list[float]
    freeze_rows: int
    freeze_cols: int
    landscape: bool
    filter: bool
    banding: bool
    bars: dict[int, str]
    scales: dict[int, bool]
    #: (row, column) -> comment text; row 0 is the header row.
    comments: dict[tuple[int, int], str]
    #: (row, column) -> target tab name, for the Contents tab's links.
    links: dict[tuple[int, int], str] = field(default_factory=dict)
    #: Rows above the header (the Contents tab's title block); 0 elsewhere.
    header_row: int = 0
    above: list[list[Cell]] = field(default_factory=list)
    #: Excluded from the page-header title (the Contents tab is the cover).
    is_cover: bool = False
    #: Data rows before any pinned "Total" rows (None: all of them). The
    #: filter, bars and scales cover only these.
    body_rows: int | None = None
    #: Print one page wide; False for a very wide tab, which prints across
    #: pages at WIDE_PRINT_SCALE % with its first column repeated.
    fit_width: bool = True

    @property
    def body_count(self) -> int:
        return len(self.rows) if self.body_rows is None else self.body_rows

    @property
    def column_count(self) -> int:
        return len(self.widths)


@dataclass
class WorkbookPlan:
    info: WorkbookInfo
    sheets: list[SheetPlan]

    def header_text(self, sheet: SheetPlan) -> tuple[str, str]:
        """Page header (left, right)."""
        return self.info.title, ("" if sheet.is_cover else sheet.title)

    def footer_text(self) -> tuple[str, str]:
        """Page footer (left, right); the centre is "Page n of N"."""
        return (self.info.source,
                self.info.written.strftime("%Y-%m-%d %H:%M"))


def sheet_name(wanted: str, taken: set[str]) -> str:
    """A legal, unique sheet name: <= 31 characters, none of []:*?/\\, not
    starting or ending with an apostrophe, unique case-insensitively."""
    name = _BAD_SHEET_CHARS.sub(" ", str(wanted)).strip().strip("'") or "Sheet"
    name = name[:MAX_SHEET_NAME].rstrip().rstrip("'") or "Sheet"
    candidate, n = name, 2
    while candidate.lower() in taken:
        suffix = f" ({n})"
        candidate = name[:MAX_SHEET_NAME - len(suffix)] + suffix
        n += 1
    taken.add(candidate.lower())
    return candidate


def clean_text(value: Any) -> str:
    """Text safe for XML: control characters dropped."""
    return BAD_XML_CHARS.sub("", str(value))


# -- values -------------------------------------------------------------------

def _parse(value: Any, is_text: bool) -> tuple[Any, bool, bool]:
    """``(value, censored, na)``: numeric text becomes a number outside a
    text column; ``>x`` a censored number; ``N/A`` and None are marked."""
    if value is None:
        return None, False, False
    if isinstance(value, bool):
        return value, False, False
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return ("N/A" if math.isnan(value) else
                    ("-inf" if value < 0 else "inf")), False, math.isnan(value)
        return value, False, False
    text = str(value).strip()
    if text in _NA:
        return "N/A", False, True
    if is_text or not text:
        return str(value), False, False
    m = _NUMBER_TEXT.match(text)
    if not m or not any(ch.isdigit() for ch in m.group(2) or ""):
        return str(value), False, False
    digits = m.group(2).replace(",", "")
    try:
        number: float | int = (int(digits) if re.fullmatch(r"-?\d+", digits)
                               else float(digits))
    except ValueError:
        return str(value), False, False
    return number, bool(m.group(1)), False


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number_key(kind: str, value: Any, header: str) -> str:
    if header.strip().lower() in ID_HEADERS:
        return "id"
    key = _KIND_FORMAT.get(kind, "general")
    if key == "count" and isinstance(value, float) and value != int(value):
        return "count_avg"
    return key


def display_text(value: Any, number: str) -> str:
    """Roughly what a spreadsheet shows for `value` in format `number`
    (for column widths)."""
    if value is None:
        return ""
    if not _is_number(value):
        return str(value)
    prefix = ""
    if number.startswith(CENSORED_PREFIX):
        prefix, number = ">", number[len(CENSORED_PREFIX):]
    v = float(value)
    text = {
        "prob": f"{v:.6f}", "pct": f"{v:.2f}", "time": f"{v:,.3f}",
        "cost": f"{v:,.2f}", "count": f"{v:,.0f}", "count_avg": f"{v:,.2f}",
        "id": f"{v:.0f}", "ratio": f"{v:.4f}",
    }.get(number, f"{v:.10g}" if isinstance(value, float) else str(value))
    return prefix + text


# -- sorting --------------------------------------------------------------------

def _sorted(rows: list[list[Any]], column: int,
            descending: bool) -> list[list[Any]]:
    """Stable sort on one column; anything that is not a number (blank,
    N/A, text) goes last, in its original order."""
    def number(row: list[Any]) -> float | None:
        value = row[column] if column < len(row) else None
        parsed, _c, _na = _parse(value, False)
        return float(parsed) if _is_number(parsed) else None

    numeric = [r for r in rows if number(r) is not None]
    other = [r for r in rows if number(r) is None]
    numeric.sort(key=lambda r: number(r) or 0.0, reverse=descending)
    return numeric + other


# -- glossary ---------------------------------------------------------------------

def _term_pattern(terms: Collection[str]) -> re.Pattern[str] | None:
    if not terms:
        return None
    alternatives = sorted(terms, key=len, reverse=True)
    # A plural ("CMs", "PMs") counts as the term.
    return re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, alternatives))
                      + r")s?(?![\w-])")


def _terms_in(text: str, pattern: re.Pattern[str] | None) -> list[str]:
    if pattern is None:
        return []
    seen: list[str] = []
    for m in pattern.finditer(text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


# -- one sheet ----------------------------------------------------------------

_TOTAL_HEADERS = {"total", "grand total", "total cost"}
_TOTAL_LABEL = re.compile(r"^\s*(grand\s+)?total\s*$", re.IGNORECASE)
_SPREAD = {"st. dev.", "st.dev", "stdev", "sem", "minimum", "maximum"}


def _auto_rules(sheet: Sheet, headers: list[str], kinds: list[str],
                numeric: list[bool], spread: list[bool]
                ) -> tuple[dict[int, str], dict[int, bool]]:
    """The automatic colour rules for a table with one kind per column:

    * a percentage column gets a bar, in a state's colour when it is a
      state's share of time;
    * a "Total" cost column gets a bar;
    * an availability/reliability column gets an orange low end -- in a
      statistics table only its Mean, elsewhere only when a table has one
      or two such columns (more is noise).
    Constant columns get nothing: there is nothing to compare."""
    bars: dict[int, str] = {}
    scales: dict[int, bool] = {}
    if sheet.row_formats:
        return bars, scales                  # a table of measures: no
    stats = any(h.strip().lower() == "mean" for h in headers)
    prob_cols = [c for c, k in enumerate(kinds) if k == "prob" and numeric[c]
                 and headers[c].strip().lower() not in _SPREAD]
    for col, kind in enumerate(kinds):
        if not numeric[col] or not spread[col]:
            continue
        header = headers[col].strip()
        if kind == "prob" and col in prob_cols:
            if (stats and header.lower() == "mean") or \
                    (not stats and len(prob_cols) <= 2):
                scales[col] = True
        elif kind == "pct" and header.lower() not in _SPREAD:
            bars[col] = state_colour(header) or BAR
        elif kind == "cost" and header.lower() in _TOTAL_HEADERS:
            bars[col] = BAR
    return bars, scales


def _is_total(row: Sequence[Any]) -> bool:
    return bool(row) and isinstance(row[0], str) \
        and bool(_TOTAL_LABEL.match(row[0]))


def _ordered_rows(sheet: Sheet, headers: list[str], notes: list[str]
                  ) -> tuple[list[list[Any]], int]:
    """The rows in the order written, and how many come before the pinned
    "Total" rows. A sort and a truncation add a note."""
    rows = [list(r) for r in sheet.rows]
    # A "Total" row stays last, outside the sort, the filter and the bars.
    totals = [r for r in rows if _is_total(r)]
    rows = [r for r in rows if not _is_total(r)]
    if sheet.sort is not None and rows:
        col, desc = sheet.sort
        if 0 <= col < len(headers):
            rows = _sorted(rows, col, desc)
            label = headers[col] or f"column {col + 1}"
            notes.insert(0, f"Sorted by {label}, "
                            f"{'largest' if desc else 'smallest'} first.")
    room = MAX_ROWS - 1 - (len(notes) + 3) - len(totals)
    if len(rows) > room:
        rows = rows[:room]
        notes.append("Rows beyond the spreadsheet limit were left out.")
    return rows + totals, len(rows)


@dataclass
class _Column:
    """What the cells of one column turned out to hold."""
    numbers: int = 0
    texts: int = 0
    low: float | None = None
    high: float | None = None

    def saw(self, value: float, in_body: bool) -> None:
        self.numbers += 1
        if in_body:
            self.low = value if self.low is None else min(self.low, value)
            self.high = value if self.high is None else max(self.high, value)

    @property
    def spread(self) -> bool:
        return self.low is not None and self.high is not None \
            and self.high > self.low


def _typed_cell(value: Any, is_text: bool, kind: str, header: str,
                stats: _Column, in_body: bool) -> Cell:
    parsed, censored, na = _parse(value, is_text)
    if na:
        return Cell("N/A", Style("na", "", "right"))
    if _is_number(parsed):
        stats.saw(float(parsed), in_body)
        key = _number_key(kind, parsed, header)
        if censored:
            key = CENSORED_PREFIX + key
        return Cell(parsed, Style("data", key, "right"))
    if parsed is None or parsed == "":
        return Cell(None, Style("data", "", "left"))
    if not isinstance(parsed, bool):
        stats.texts += 1
    return Cell(clean_text(parsed) if isinstance(parsed, str) else parsed,
                Style("data", "", "left"))


def _typed_rows(sheet: Sheet, raw_rows: list[list[Any]], headers: list[str],
                text_cols: set[int], body_count: int
                ) -> tuple[list[list[Cell]], list[_Column]]:
    count = len(headers)
    kinds = [sheet.formats[i] if i < len(sheet.formats) else ""
             for i in range(count)]
    stats = [_Column() for _ in range(count)]
    rows = []
    for n, raw in enumerate(raw_rows):
        row_kind = sheet.row_formats[n] if n < len(sheet.row_formats) else ""
        rows.append([_typed_cell(
            raw[col] if col < len(raw) else None, col in text_cols,
            (row_kind if row_kind and col else kinds[col]) or "",
            headers[col], stats[col], n < body_count)
            for col in range(count)])
    return rows, stats


def _restyle(rows: list[list[Cell]], numeric_col: list[bool],
             body_count: int) -> None:
    """Short text in a numeric column ("Infinite", "inf") lines up with the
    numbers, a long one reads left to right; pinned totals are bold."""
    for n, out in enumerate(rows):
        for col, cell in enumerate(out):
            st = cell.style
            if numeric_col[col] and st.role == "data" and not st.number \
                    and cell.value is not None \
                    and len(str(cell.value)) <= _MAX_NUMBER_WIDTH - 2:
                st = Style("data", "", "right")
            if n >= body_count:
                st = Style("total", st.number, st.align, st.wrap)
            cell.style = st


def _sizes(rows: list[list[Cell]], headers: list[str],
           numeric_col: list[bool], stats: list[_Column]) -> list[float]:
    """Column widths; long text columns are set to wrap."""
    widths = []
    for col, header in enumerate(headers):
        # A short header stays on one line; a long one wraps at word breaks.
        longest_word = max((len(w) for w in header.split()), default=0)
        longest_word = max(longest_word, min(len(header), 14))
        data = max((len(display_text(r[col].value, r[col].style.number))
                    for r in rows[:2000]), default=0)
        # A numeric column that also holds text (a settings table's
        # "Value") is sized and wrapped like a text column.
        cap = (_MAX_NUMBER_WIDTH if numeric_col[col] and not stats[col].texts
               else _MAX_TEXT_WIDTH)
        if data + 2 > cap:
            for r in rows:
                st = r[col].style
                r[col].style = Style(st.role, st.number, st.align, True)
        widths.append(min(max(_MIN_WIDTH, longest_word + 2, data + 2),
                          max(cap, longest_word + 2)))
    return widths


def _comments(headers: list[str], rows: list[list[Cell]], labels: bool,
              terms: re.Pattern[str] | None, glossary: Mapping[str, str]
              ) -> tuple[dict[tuple[int, int], str], set[str]]:
    """Glossary comments on headers (and on row labels), and the terms."""
    used: set[str] = set()
    comments: dict[tuple[int, int], str] = {}

    def note(where: tuple[int, int], text: str) -> None:
        found = _terms_in(text, terms)
        if found:
            used.update(found)
            comments[where] = "\n".join(f"{t}: {glossary[t]}"
                                        for t in found)

    for col, header in enumerate(headers):
        note((0, col), header)
    if labels:
        for n, out in enumerate(rows[:500]):
            if out and isinstance(out[0].value, str):
                note((n + 1, 0), out[0].value)
    return comments, used


def _wrap_notes(notes: list[str], width: float) -> list[str]:
    """Long notes broken into lines about as wide as the table (at least 60
    characters), so they print on the page instead of running off it."""
    import textwrap
    limit = max(60, int(width * 1.1))
    out: list[str] = []
    for note in notes:
        out += textwrap.wrap(note, limit) or [""]
    return out


def plan_sheet(sheet: Sheet, name: str,
               terms: re.Pattern[str] | None,
               glossary: Mapping[str, str]) -> tuple[SheetPlan, set[str]]:
    """The layout of one data tab, and the glossary terms it uses."""
    headers = [clean_text(h) for h in sheet.headers]
    count = max([len(headers), *(len(r) for r in sheet.rows), 1])
    headers += [""] * (count - len(headers))
    text_cols = set(sheet.text_cols)
    if sheet.row_formats:
        text_cols.add(0)                     # the measure's label

    notes = [clean_text(n) for n in sheet.notes]
    raw_rows, body_count = _ordered_rows(sheet, headers, notes)
    rows, stats = _typed_rows(sheet, raw_rows, headers, text_cols,
                              body_count)
    # A column is numeric when most of its filled cells are numbers.
    numeric_col = [c not in text_cols and stats[c].numbers > 0
                   and stats[c].numbers >= stats[c].texts
                   for c in range(count)]
    _restyle(rows, numeric_col, body_count)
    widths = _sizes(rows, headers, numeric_col, stats)
    comments, used = _comments(headers, rows,
                               bool(sheet.row_formats or 0 in text_cols),
                               terms, glossary)

    kinds = [sheet.formats[i] if i < len(sheet.formats) else ""
             for i in range(count)]
    bars, scales = ({}, {}) if not sheet.highlight else _auto_rules(
        sheet, headers, kinds,
        [numeric_col[c] and stats[c].numbers >= 2 for c in range(count)],
        [stats[c].spread for c in range(count)])
    bars.update({c: v for c, v in sheet.bars.items() if c < count})
    scales.update({c: v for c, v in sheet.scales.items() if c < count})

    notes = _wrap_notes(notes, sum(widths))
    has_header = any(headers)
    wide = sum(widths) > 60 and count > 6
    plan = SheetPlan(
        name=name, title=clean_text(sheet.title or sheet.name),
        description=clean_text(sheet.description),
        headers=[Cell(h, Style("header", "", "right" if numeric_col[i]
                               else "left", True))
                 for i, h in enumerate(headers)] if has_header else [],
        rows=rows, notes=notes, widths=widths,
        freeze_rows=1 if has_header else 0,
        freeze_cols=1 if wide and not numeric_col[0] else 0,
        landscape=sum(widths) > _LANDSCAPE_WIDTH,
        filter=bool(has_header and rows), banding=len(rows) > 2,
        bars=bars if body_count > 1 else {},
        scales=scales if body_count > 2 else {}, comments=comments,
        body_rows=body_count, fit_width=sum(widths) <= _FIT_WIDTH)
    return plan, used


# -- the workbook -----------------------------------------------------------------

_CONTENTS_NAME = "Contents"
_GLOSSARY_NAME = "Glossary"

#: How to read the colours and markers; written under the Glossary.
READING_NOTES = (
    "Every other row is shaded to help read across; the shading follows "
    "sorting and filtering.",
    "Bars show each value's size within its column: a share, a percentage "
    "of time (in that state's colour) or a total.",
    "Orange marks the lowest values in an availability or reliability "
    "column.",
    "A value shown with > is a censored estimate: the true value is at least "
    "this large. N/A: no value for this item.",
    "Numbers keep full precision; the format only sets how many places are "
    "shown.",
)


def _contents_plan(info: WorkbookInfo, plans: list[SheetPlan],
                   name: str) -> SheetPlan:
    above = [[Cell(clean_text(info.title or "Workbook"),
                   Style("title"))]]
    for line in info.subtitle:
        above.append([Cell(clean_text(line), Style("subtitle"))])
    above.append([Cell(f"Written {info.written:%Y-%m-%d %H:%M}",
                       Style("subtitle"))])
    above.append([])
    headers = [Cell("Tab", Style("header", "", "left", True)),
               Cell("What it holds", Style("header", "", "left", True)),
               Cell("Rows", Style("header", "", "right", True))]
    rows, links = [], {}
    first_row = len(above) + 1
    for n, plan in enumerate(plans):
        rows.append([Cell(plan.name, Style("link")),
                     Cell(plan.description or plan.title,
                          Style("data", "", "left", True)),
                     Cell(len(plan.rows), Style("data", "id", "right"))])
        links[(first_row + n, 0)] = plan.name
    widths = [max(14.0, *(len(p.name) + 2.0 for p in plans)),
              min(70.0, max(20.0, *(len(p.description or p.title) + 2.0
                                    for p in plans))), 8.0]
    return SheetPlan(
        name=name, title="Contents", description="", headers=headers,
        rows=rows, notes=[], widths=widths, freeze_rows=0, freeze_cols=0,
        landscape=False, filter=False, banding=len(rows) > 2, bars={},
        scales={}, comments={}, links=links, header_row=len(above),
        above=above, is_cover=True)


def _glossary_plan(used: Collection[str], glossary: Mapping[str, str],
                   name: str) -> SheetPlan:
    rows = [[Cell(term, Style("data", "", "left")),
             Cell(clean_text(glossary[term]), Style("data", "", "left", True))]
            for term in sorted(used, key=str.lower)]
    widths = [max(10.0, *(len(t) + 2.0 for t in used)), 80.0]
    return SheetPlan(
        name=name, title="Glossary",
        description="What the abbreviations in this workbook mean, and how "
                    "to read its colours",
        headers=[Cell("Term", Style("header", "", "left", True)),
                 Cell("Meaning", Style("header", "", "left", True))],
        rows=rows, notes=list(READING_NOTES), widths=widths, freeze_rows=1,
        freeze_cols=0, landscape=False, filter=len(rows) > 1,
        banding=len(rows) > 2, bars={}, scales={}, comments={})


def plan_workbook(sheets: Sequence[Sheet],
                  info: WorkbookInfo | None = None) -> WorkbookPlan:
    """Every layout decision for `sheets`, shared by both file formats."""
    if not sheets:
        raise ValueError("a workbook needs at least one sheet")
    info = info or WorkbookInfo()
    pattern = _term_pattern(list(info.glossary))
    taken: set[str] = set()
    with_contents = info.contents and len(sheets) >= 2
    contents_name = sheet_name(_CONTENTS_NAME, taken) if with_contents else ""
    plans: list[SheetPlan] = []
    used: set[str] = set()
    for sheet in sheets:
        plan, terms = plan_sheet(sheet, sheet_name(sheet.name, taken),
                                 pattern, info.glossary)
        plans.append(plan)
        used |= terms
    if used:
        plans.append(_glossary_plan(used, info.glossary,
                                    sheet_name(_GLOSSARY_NAME, taken)))
    if with_contents:
        plans.insert(0, _contents_plan(info, plans, contents_name))
    return WorkbookPlan(info=info, sheets=plans)


def write_workbook(path: str | Path, sheets: Sequence[Sheet],
                   info: WorkbookInfo | None = None) -> Path:
    """Write `sheets` as one workbook; the format follows the file's
    extension (``.xlsx`` or ``.ods``). Returns the path."""
    path = Path(path)
    suffix = path.suffix.lower()
    plan = plan_workbook(sheets, info)
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".xlsx":
        from raptor.core.fileio.xlsx_writer import render_xlsx
        render_xlsx(path, plan)
    elif suffix == ".ods":
        from raptor.core.fileio.ods_writer import render_ods
        render_ods(path, plan)
    else:
        raise ValueError(f"unknown spreadsheet type {suffix!r}: use .xlsx "
                         "or .ods")
    return path


#: (extension, file-dialog filter) for the spreadsheet types, Excel first.
SPREADSHEET_FILTERS: tuple[tuple[str, str], ...] = (
    (".xlsx", "Excel workbook (*.xlsx)"),
    (".ods", "OpenDocument spreadsheet (*.ods)"),
)


def spreadsheet_path(path: str, selected_filter: str = "") -> str:
    """`path` with the extension its file-dialog filter implies, when the
    user typed none (or an unknown one)."""
    suffix = Path(path).suffix.lower()
    if suffix in (".xlsx", ".ods"):
        return path
    ext = next((e for e, f in SPREADSHEET_FILTERS if f == selected_filter),
               ".xlsx")
    return path + ext

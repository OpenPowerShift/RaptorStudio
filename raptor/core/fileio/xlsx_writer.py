"""A minimal, dependency-free .xlsx writer.

An .xlsx file is a zip of a few XML parts. This writes exactly what a table
export needs -- named sheets, a bold shaded header row that stays put while
scrolling, real numeric cells, sized columns and a filter on the header --
and nothing else, so no spreadsheet library has to be installed.

Numbers are written as numbers (so the spreadsheet can sort and chart them),
text as text. Nothing here reads a workbook back; the tests do that by
converting the file with LibreOffice when it is available.
"""
from __future__ import annotations

import math
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

#: Excel's hard limits.
MAX_ROWS = 1_048_576
MAX_SHEET_NAME = 31

_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
# XML 1.0 forbids most control characters, whatever the escaping.
_BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

_STYLE_NORMAL, _STYLE_HEADER, _STYLE_NOTE = 0, 1, 2
#: Number formats a numeric cell can carry, by the kind of quantity
#: (`raptor.api.presentation`): the cell keeps its full value, the format
#: only sets how many places are shown.
_NUMBER_STYLES = {"prob": 3, "pct": 4, "cost": 4}


@dataclass
class Sheet:
    """One tab: a header row and the rows under it."""
    name: str
    headers: Sequence[str]
    rows: Sequence[Sequence[Any]]
    #: Free-text lines written under the table, after a blank row.
    notes: Sequence[str] = field(default_factory=tuple)
    #: The kind of each column's numbers ("prob", "pct", "cost", ...).
    formats: Sequence[str] = field(default_factory=tuple)
    #: Per-row kinds for a table of measures (one row per measure); a row's
    #: kind wins over its column's.
    row_formats: Sequence[str] = field(default_factory=tuple)


def sheet_name(wanted: str, taken: set[str]) -> str:
    """A legal, unique sheet name: <= 31 characters, none of []:*?/\\, not
    starting or ending with an apostrophe, unique case-insensitively."""
    name = _BAD_SHEET_CHARS.sub(" ", wanted).strip().strip("'") or "Sheet"
    name = name[:MAX_SHEET_NAME].rstrip().rstrip("'") or "Sheet"
    candidate, n = name, 2
    while candidate.lower() in taken:
        suffix = f" ({n})"
        candidate = name[:MAX_SHEET_NAME - len(suffix)] + suffix
        n += 1
    taken.add(candidate.lower())
    return candidate


def _col_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA ..."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _text(value: Any) -> str:
    return escape(_BAD_XML_CHARS.sub("", str(value)))


def _cell(ref: str, value: Any, style: int = _STYLE_NORMAL) -> str:
    """One <c> element. None is an empty cell (kept so styles apply)."""
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>' if style else ""
    if isinstance(value, bool):
        return f'<c r="{ref}"{s} t="b"><v>{int(value)}</v></c>'
    if isinstance(value, int):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return (f'<c r="{ref}"{s} t="inlineStr"><is><t>'
                    f'{_text(value)}</t></is></c>')
        return f'<c r="{ref}"{s}><v>{value!r}</v></c>'
    return (f'<c r="{ref}"{s} t="inlineStr"><is>'
            f'<t xml:space="preserve">{_text(value)}</t></is></c>')


def _number_style(value: Any, row_kind: str | None,
                  col_kind: str | None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _STYLE_NORMAL
    return _NUMBER_STYLES.get(row_kind or col_kind or "", _STYLE_NORMAL)


def _display_len(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, float):
        return len(f"{value:.6g}")
    return len(str(value))


def _column_widths(headers: Sequence[str],
                   rows: Sequence[Sequence[Any]]) -> list[float]:
    widths = [len(str(h)) + 2 for h in headers]
    for row in rows[:500]:                    # a sample is enough
        for i, v in enumerate(row):
            if i >= len(widths):
                widths.append(0)
            widths[i] = max(widths[i], _display_len(v) + 2)
    return [min(max(w, 8), 60) for w in widths]


def _sheet_xml(sheet: Sheet) -> str:
    rows = list(sheet.rows)
    truncated = len(rows) > MAX_ROWS - 2 - (len(sheet.notes) + 2)
    if truncated:
        rows = rows[:MAX_ROWS - 2 - (len(sheet.notes) + 2)]
    column_count = max(len(sheet.headers), *(len(r) for r in rows), 1)
    widths = _column_widths(sheet.headers, rows)
    widths += [10.0] * (column_count - len(widths))

    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/'
             'spreadsheetml/2006/main">']
    if sheet.headers:
        parts.append(
            '<sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
            'state="frozen"/></sheetView></sheetViews>')
    parts.append("<cols>" + "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w:.1f}" customWidth="1"/>'
        for i, w in enumerate(widths)) + "</cols>")
    parts.append("<sheetData>")
    r = 1
    if sheet.headers:
        cells = "".join(_cell(f"{_col_letter(i)}{r}", h, _STYLE_HEADER)
                        for i, h in enumerate(sheet.headers))
        parts.append(f'<row r="{r}">{cells}</row>')
    for n, row in enumerate(rows):
        r += 1
        row_kind = sheet.row_formats[n] if n < len(sheet.row_formats) \
            else None
        cells = "".join(
            _cell(f"{_col_letter(i)}{r}", v,
                  _number_style(v, row_kind if i else None,
                                sheet.formats[i] if i < len(sheet.formats)
                                else None))
            for i, v in enumerate(row))
        parts.append(f'<row r="{r}">{cells}</row>' if cells
                     else f'<row r="{r}"/>')
    notes = list(sheet.notes)
    if truncated:
        notes.append("(Rows beyond the spreadsheet limit were left out.)")
    if notes:
        r += 1                                # a blank separator row
        for note in notes:
            r += 1
            parts.append(f'<row r="{r}">'
                         f'{_cell(f"A{r}", note, _STYLE_NOTE)}</row>')
    parts.append("</sheetData>")
    if sheet.headers and rows:
        last = f"{_col_letter(len(sheet.headers) - 1)}{len(rows) + 1}"
        parts.append(f'<autoFilter ref="A1:{last}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
    'content-types">'
    '<Default Extension="rels" ContentType="application/vnd.'
    'openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    "{sheets}"
    "</Types>")

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.'
    'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/></Relationships>')

# fonts: 0 normal, 1 bold, 2 italic grey; fills: 0 none, 1 gray125 (both
# required by Excel), 2 header shade.
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
    '2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.000000"/>'
    '</numFmts>'
    '<fonts count="3">'
    '<font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font>'
    '<font><i/><sz val="10"/><color rgb="FF666666"/><name val="Calibri"/>'
    '</font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFDCE6F1"/>'
    '<bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/>'
    '</border><border><left/><right/><top/>'
    '<bottom style="thin"><color auto="1"/></bottom><diagonal/></border>'
    '</borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
    'borderId="0"/></cellStyleXfs>'
    '<cellXfs count="5">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" '
    'applyFont="1" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" '
    'applyFont="1"/>'
    # 3: a probability, six places; 4: money or a percentage, two places
    # with thousands separators (built-in format 4).
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" '
    'applyNumberFormat="1"/>'
    '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" '
    'applyNumberFormat="1"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" '
    'builtinId="0"/></cellStyles></styleSheet>')


def write_xlsx(path: str | Path, sheets: Sequence[Sheet]) -> Path:
    """Write `sheets` as one workbook, one tab each. Sheet names are made
    legal and unique (see :func:`sheet_name`). Returns the path."""
    if not sheets:
        raise ValueError("a workbook needs at least one sheet")
    path = Path(path)
    taken: set[str] = set()
    named = [(sheet_name(s.name, taken), s) for s in sheets]

    content = _CONTENT_TYPES.format(sheets="".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.'
        f'spreadsheetml.worksheet+xml"/>' for i in range(1, len(named) + 1)))
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        '2006/main" xmlns:r="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships"><sheets>'
        + "".join(f'<sheet name="{escape(n, {chr(34): "&quot;"})}" '
                  f'sheetId="{i}" r:id="rId{i}"/>'
                  for i, (n, _s) in enumerate(named, 1))
        + "</sheets></workbook>")
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.'
            f'org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(named) + 1))
        + f'<Relationship Id="rId{len(named) + 1}" Type="http://schemas.'
          f'openxmlformats.org/officeDocument/2006/relationships/styles" '
          f'Target="styles.xml"/></Relationships>')

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, (_n, sheet) in enumerate(named, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sheet))
    return path

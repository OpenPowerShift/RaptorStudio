"""The ``.xlsx`` renderer, on XlsxWriter.

The layout is decided in :mod:`raptor.core.fileio.workbook`, shared with the
``.ods`` writer; this module only turns a :class:`~.workbook.WorkbookPlan`
into Excel's file format. :func:`write_xlsx` and :class:`Sheet` are kept for
callers that predate the shared layer.

Every string is written with ``write_string``: a model name or comment that
begins with ``=`` is text, never a formula.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import xlsxwriter
from xlsxwriter.format import Format
from xlsxwriter.worksheet import Worksheet

from raptor.core.fileio.workbook import (
    BAND, HEADER_TEXT, INK, LINK, LOW, LOW_TO, MAX_ROWS, MAX_SHEET_NAME,
    MUTED, NUMBER_FORMATS, RULE, WIDE_PRINT_SCALE, Cell, Sheet, SheetPlan, Style, WorkbookInfo,
    WorkbookPlan, sheet_name, write_workbook,
)

__all__ = ["MAX_ROWS", "MAX_SHEET_NAME", "Sheet", "render_xlsx",
           "sheet_name", "write_xlsx"]

_FONT = "Calibri"
_A4 = 9


def write_xlsx(path: str | Path, sheets: Sequence[Sheet],
               info: WorkbookInfo | None = None) -> Path:
    """Write `sheets` as one ``.xlsx`` workbook (see
    :func:`~raptor.core.fileio.workbook.write_workbook`)."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        path = path.with_suffix(".xlsx")
    return write_workbook(path, sheets, info)


def _hf(text: str) -> str:
    """Text for a page header/footer: ``&`` is its control character."""
    return text.replace("&", "&&")


class _Formats:
    """One XlsxWriter Format per distinct Style, made on first use."""

    def __init__(self, book: xlsxwriter.Workbook) -> None:
        self._book = book
        self._cache: dict[Style, Format] = {}

    def __call__(self, style: Style) -> Format:
        fmt = self._cache.get(style)
        if fmt is None:
            fmt = self._book.add_format(self._props(style))
            self._cache[style] = fmt
        return fmt

    @staticmethod
    def _props(style: Style) -> dict[str, Any]:
        props: dict[str, Any] = {"font_name": _FONT, "font_size": 11,
                                 "valign": "top", "align": style.align}
        if style.number:
            props["num_format"] = NUMBER_FORMATS[style.number]
        if style.align == "left" and style.role in _INDENTED:
            # A text column after a right-aligned number does not touch it.
            props["indent"] = 1
        if style.wrap:
            props["text_wrap"] = True
        props.update(_ROLE_PROPS.get(style.role, {}))
        return props


#: Roles whose left-aligned text is indented by one character.
_INDENTED = frozenset({"data", "header", "total", "na", "link"})
#: What each role adds to a cell's format.
_ROLE_PROPS: dict[str, dict[str, Any]] = {
    "header": {"bold": True, "font_color": HEADER_TEXT, "bg_color": INK,
               "valign": "vcenter", "bottom": 2, "bottom_color": RULE},
    "total": {"bold": True, "top": 1, "top_color": INK},
    "na": {"font_color": MUTED, "italic": True},
    "note": {"font_color": MUTED, "italic": True, "font_size": 10},
    "title": {"bold": True, "font_size": 16, "font_color": INK},
    "subtitle": {"font_color": MUTED},
    "link": {"font_color": LINK, "underline": 1},
}


def _write(ws: Worksheet, row: int, col: int, cell: Cell,
           fmt: _Formats) -> None:
    value, f = cell.value, fmt(cell.style)
    if value is None or value == "":
        if cell.style.role == "header":
            ws.write_blank(row, col, None, f)
        return
    if isinstance(value, bool):
        ws.write_boolean(row, col, value, f)
    elif isinstance(value, (int, float)):
        ws.write_number(row, col, value, f)
    else:
        ws.write_string(row, col, str(value), f)


def _ref(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _cells(ws: Worksheet, sp: SheetPlan, fmt: _Formats) -> None:
    """The title block, header, rows, notes and comments."""
    for r, line in enumerate(sp.above):
        for c, cell in enumerate(line):
            _write(ws, r, c, cell, fmt)
    top = sp.header_row
    for c, cell in enumerate(sp.headers):
        _write(ws, top, c, cell, fmt)
    first = top + (1 if sp.headers else 0)
    for n, row in enumerate(sp.rows):
        for c, cell in enumerate(row):
            target = sp.links.get((first + n, c))
            if target is not None:
                ws.write_url(first + n, c, f"internal:{_ref(target)}!A1",
                             fmt(cell.style), string=str(cell.value),
                             tip=f"Go to {target}")
            else:
                _write(ws, first + n, c, cell, fmt)
    note_row = first + len(sp.rows) + 1
    for i, note in enumerate(sp.notes):
        ws.write_string(note_row + i, 0, note, fmt(Style("note")))
    for (r, c), text in sp.comments.items():
        ws.write_comment(top + r, c, text, {
            "author": "RAPTOR Studio", "x_scale": 2.2,
            "y_scale": 1 + text.count("\n") * 0.6 + len(text) / 120})


def _rules(book: xlsxwriter.Workbook, ws: Worksheet, sp: SheetPlan) -> None:
    """Banding, bars and colour scales, the filter and the frozen panes."""
    top = sp.header_row
    first = top + (1 if sp.headers else 0)
    last = first + len(sp.rows) - 1
    body_last = first + sp.body_count - 1
    cols = max(sp.column_count, 1)
    if sp.rows and sp.banding:
        ws.conditional_format(first, 0, last, cols - 1, {
            "type": "formula", "criteria": "=MOD(ROW(),2)=0",
            "format": book.add_format({"bg_color": BAND})})
    for c, colour in sp.bars.items():
        ws.conditional_format(first, c, body_last, c, {
            "type": "data_bar", "bar_color": colour, "bar_solid": True,
            "min_type": "num", "min_value": 0, "bar_no_border": True})
    for c, higher_better in sp.scales.items():
        ws.conditional_format(first, c, body_last, c, {
            "type": "2_color_scale",
            "min_color": LOW if higher_better else LOW_TO,
            "max_color": LOW_TO if higher_better else LOW})
    if sp.filter and sp.body_count:
        ws.autofilter(top, 0, body_last, cols - 1)
    if sp.freeze_rows or sp.freeze_cols:
        ws.freeze_panes(top + sp.freeze_rows, sp.freeze_cols)


def _print_setup(ws: Worksheet, plan: WorkbookPlan, sp: SheetPlan) -> None:
    """A4, by width portrait or landscape, one page wide (a very wide tab:
    across pages, its first column repeated), header row on every page."""
    ws.set_paper(_A4)
    if sp.landscape:
        ws.set_landscape()
    else:
        ws.set_portrait()
    if sp.fit_width:
        ws.fit_to_pages(1, 0)
    else:
        ws.set_print_scale(WIDE_PRINT_SCALE)
        ws.repeat_columns(0)
    ws.center_horizontally()
    ws.set_margins(left=0.5, right=0.5, top=0.75, bottom=0.75)
    top = sp.header_row
    if sp.headers:
        ws.repeat_rows(top)
    first = top + (1 if sp.headers else 0)
    end_row = max(first + len(sp.rows) + len(sp.notes), top)
    ws.print_area(0, 0, end_row, max(sp.column_count, 1) - 1)
    left, right = plan.header_text(sp)
    ws.set_header(f"&L&9{_hf(left)}&R&9&B{_hf(right)}")
    f_left, f_right = plan.footer_text()
    ws.set_footer(f"&L&8{_hf(f_left)}&C&8Page &P of &N&R&8{_hf(f_right)}")


def _sheet(book: xlsxwriter.Workbook, plan: WorkbookPlan, sp: SheetPlan,
           fmt: _Formats) -> None:
    ws = book.add_worksheet(sp.name)
    for col, width in enumerate(sp.widths):
        ws.set_column(col, col, width)
    _cells(ws, sp, fmt)
    _rules(book, ws, sp)
    _print_setup(ws, plan, sp)


def render_xlsx(path: Path, plan: WorkbookPlan) -> None:
    """Write `plan` to `path` as an ``.xlsx`` workbook."""
    book = xlsxwriter.Workbook(str(path), {
        "strings_to_numbers": False, "strings_to_formulas": False,
        "strings_to_urls": False, "nan_inf_to_errors": False})
    try:
        book.set_properties({
            "title": plan.info.title, "author": "RAPTOR Studio",
            "comments": "Written by RAPTOR Studio",
            "created": plan.info.written})
        book.set_size(1600, 1000)
        fmt = _Formats(book)
        for sp in plan.sheets:
            _sheet(book, plan, sp, fmt)
    finally:
        book.close()

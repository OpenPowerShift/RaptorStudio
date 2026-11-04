"""The ``.ods`` (OpenDocument spreadsheet) renderer.

The layout is decided in :mod:`raptor.core.fileio.workbook`, shared with the
``.xlsx`` writer; this module turns a :class:`~.workbook.WorkbookPlan` into
an OpenDocument package (ODF 1.3).

Why our own writer and not a library: the only general Python ODS writer,
odfpy, has had no release since 2020, and the features this export needs
(filters, frozen panes, conditional formats, print set-up) are plain XML
that LibreOffice documents by example. The markup here follows what
LibreOffice itself writes (`calcext:` conditional formats, `table:` database
ranges, per-sheet master pages); the tests read every file back, and open
it in LibreOffice when it is installed.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from raptor.core.fileio.workbook import (
    BAND, CENSORED_PREFIX, HEADER_TEXT, INK, LINK, LOW, LOW_TO, MUTED,
    RULE, WIDE_PRINT_SCALE, Cell, SheetPlan, Style, WorkbookPlan,
)

_MIME = "application/vnd.oasis.opendocument.spreadsheet"
#: Excel character width -> centimetres (Calibri 11 at 96 dpi).
_CM_PER_CHAR = 0.21
_A4 = ("21.0cm", "29.7cm")

_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:'
    'xsl-fo-compatible:1.0" '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:'
    'svg-compatible:1.0" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" '
    'xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:'
    'xmlns:calcext:1.0" '
    'xmlns:loext="urn:org:documentfoundation:names:experimental:office:'
    'xmlns:loext:1.0" '
    'office:version="1.3"')


def _attr(text: str) -> str:
    return quoteattr(text)


def _ref_name(name: str) -> str:
    """A sheet name in a cell-range address: always quoted, ' doubled."""
    return "'" + name.replace("'", "''") + "'"


def _col(index: int) -> str:
    out, index = "", index + 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _range(sheet: str, r1: int, c1: int, r2: int, c2: int) -> str:
    s = _ref_name(sheet)
    return f"{s}.{_col(c1)}{r1 + 1}:{s}.{_col(c2)}{r2 + 1}"


# --------------------------------------------------------------------------
# number styles
# --------------------------------------------------------------------------

_FORMAT_SPEC = {
    # key: (decimal places, min decimal places, grouping)
    "general": (10, 0, False),
    "prob": (6, 6, False),
    "pct": (2, 2, False),
    "time": (3, 3, True),
    "cost": (2, 2, True),
    "count": (0, 0, True),
    "count_avg": (2, 2, True),
    "id": (0, 0, False),
    "ratio": (4, 4, False),
    "flow": (3, 3, True),
    "whole": (0, 0, True),
    "num": (3, 3, True),
    "small": (6, 6, False),
}


def _number_style(name: str, key: str) -> str:
    censored = key.startswith(CENSORED_PREFIX)
    base = key[len(CENSORED_PREFIX):] if censored else key
    places, min_places, grouping = _FORMAT_SPEC[base]
    prefix = "<number:text>&gt;</number:text>" if censored else ""
    group = ' number:grouping="true"' if grouping else ""
    return (f'<number:number-style style:name="{name}">{prefix}'
            f'<number:number number:decimal-places="{places}" '
            f'number:min-decimal-places="{min_places}" '
            f'number:min-integer-digits="1"{group}/>'
            f'</number:number-style>')


# --------------------------------------------------------------------------
# cell styles
# --------------------------------------------------------------------------

class _Styles:
    """Automatic styles, numbered as they are first used."""

    def __init__(self) -> None:
        self.cells: dict[Style, str] = {}
        self.numbers: dict[str, str] = {}
        self.columns: dict[str, str] = {}
        self.xml: list[str] = []
        self.number_xml: list[str] = []

    def number(self, key: str) -> str:
        if key not in self.numbers:
            name = f"N{len(self.numbers) + 1}"
            self.numbers[key] = name
            self.number_xml.append(_number_style(name, key))
        return self.numbers[key]

    def cell(self, style: Style) -> str:
        if style in self.cells:
            return self.cells[style]
        name = f"ce{len(self.cells) + 1}"
        self.cells[style] = name
        data = (f' style:data-style-name="{self.number(style.number)}"'
                if style.number else "")
        cell_props = [f'style:vertical-align="'
                      f'{"middle" if style.role == "header" else "top"}"']
        if style.wrap:
            cell_props.append('fo:wrap-option="wrap"')
        text = ['fo:font-family="Calibri"', 'fo:font-size="11pt"']
        if style.role == "header":
            cell_props += [f'fo:background-color="{INK}"',
                           f'fo:border-bottom="1.5pt solid {RULE}"']
            text += ['fo:font-weight="bold"', f'fo:color="{HEADER_TEXT}"']
        elif style.role == "total":
            cell_props.append(f'fo:border-top="0.75pt solid {INK}"')
            text.append('fo:font-weight="bold"')
        elif style.role in ("na", "note"):
            text += ['fo:font-style="italic"', f'fo:color="{MUTED}"']
            if style.role == "note":
                text[1] = 'fo:font-size="10pt"'
        elif style.role == "title":
            text += ['fo:font-weight="bold"', f'fo:color="{INK}"']
            text[1] = 'fo:font-size="16pt"'
        elif style.role == "subtitle":
            text.append(f'fo:color="{MUTED}"')
        elif style.role == "link":
            text += [f'fo:color="{LINK}"', 'style:text-underline-style="solid"',
                     'style:text-underline-width="auto"',
                     'style:text-underline-color="font-color"']
        align = "end" if style.align == "right" else "start"
        margin = (' fo:margin-left="0.2cm"' if style.align == "left"
                  and style.role in ("data", "header", "total", "na", "link")
                  else "")
        self.xml.append(
            f'<style:style style:name="{name}" style:family="table-cell" '
            f'style:parent-style-name="Default"{data}>'
            f'<style:table-cell-properties style:text-align-source="fix" '
            f'{" ".join(cell_props)}/>'
            f'<style:paragraph-properties fo:text-align="{align}"{margin}/>'
            f'<style:text-properties {" ".join(text)}/></style:style>')
        return name

    def column(self, width: float) -> str:
        cm = f"{max(width, 1.0) * _CM_PER_CHAR + 0.2:.3f}cm"
        if cm not in self.columns:
            name = f"co{len(self.columns) + 1}"
            self.columns[cm] = name
            self.xml.append(
                f'<style:style style:name="{name}" '
                f'style:family="table-column"><style:table-column-properties '
                f'fo:break-before="auto" style:column-width="{cm}"/>'
                f'</style:style>')
        return self.columns[cm]


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------

def _cell_xml(cell: Cell, styles: _Styles, link: str | None = None,
              comment: str | None = None) -> str:
    style = f' table:style-name="{styles.cell(cell.style)}"'
    note = ""
    if comment:
        paras = "".join(f"<text:p>{escape(line)}</text:p>"
                        for line in comment.split("\n"))
        note = (f'<office:annotation><dc:creator>RAPTOR Studio'
                f'</dc:creator>{paras}</office:annotation>')
    value = cell.value
    if value is None or value == "":
        return f"<table:table-cell{style}>{note}</table:table-cell>"
    if isinstance(value, bool):
        word = "true" if value else "false"
        return (f'<table:table-cell{style} office:value-type="boolean" '
                f'office:boolean-value="{word}">{note}'
                f"<text:p>{word.upper()}</text:p></table:table-cell>")
    if isinstance(value, (int, float)):
        return (f'<table:table-cell{style} office:value-type="float" '
                f'office:value="{value!r}" calcext:value-type="float">{note}'
                f"<text:p>{value!r}</text:p></table:table-cell>")
    text = escape(str(value))
    if link is not None:
        text = (f'<text:a xlink:type="simple" '
                f'xlink:href={_attr("#" + _ref_name(link) + ".A1")}>'
                f"{text}</text:a>")
    return (f'<table:table-cell{style} office:value-type="string" '
            f'calcext:value-type="string">{note}<text:p>{text}</text:p>'
            f"</table:table-cell>")


def _row_xml(cells: list[str]) -> str:
    return ('<table:table-row table:style-name="ro1">' + "".join(cells)
            + "</table:table-row>")


def _conditional(sp: SheetPlan, first: int, last: int, band: str) -> str:
    if not sp.rows:
        return ""
    body_last = first + sp.body_count - 1
    cols = max(sp.column_count, 1)
    parts = []
    if sp.banding:
        parts.append(
            f'<calcext:conditional-format calcext:target-range-address='
            f'{_attr(_range(sp.name, first, 0, last, cols - 1))}>'
            f'<calcext:condition calcext:apply-style-name="{band}" '
            f'calcext:value="formula-is(MOD(ROW();2)=0)" '
            f'calcext:base-cell-address='
            f'{_attr(_ref_name(sp.name) + "." + "A" + str(first + 1))}/>'
            f"</calcext:conditional-format>")
    for c, colour in sp.bars.items():
        parts.append(
            f'<calcext:conditional-format calcext:target-range-address='
            f'{_attr(_range(sp.name, first, c, body_last, c))}>'
            f'<calcext:data-bar calcext:gradient="false" '
            f'calcext:min-length="0" calcext:max-length="100" '
            f'calcext:positive-color="{colour}" '
            f'calcext:negative-color="#E06666" calcext:axis-color="#000000">'
            f'<calcext:formatting-entry calcext:value="0" '
            f'calcext:type="number"/>'
            f'<calcext:formatting-entry calcext:value="0" '
            f'calcext:type="auto-maximum"/></calcext:data-bar>'
            f"</calcext:conditional-format>")
    for c, higher_better in sp.scales.items():
        low, high = (LOW, LOW_TO) if higher_better else (LOW_TO, LOW)
        parts.append(
            f'<calcext:conditional-format calcext:target-range-address='
            f'{_attr(_range(sp.name, first, c, body_last, c))}>'
            f'<calcext:color-scale>'
            f'<calcext:color-scale-entry calcext:value="0" '
            f'calcext:type="minimum" calcext:color="{low}"/>'
            f'<calcext:color-scale-entry calcext:value="0" '
            f'calcext:type="maximum" calcext:color="{high}"/>'
            f"</calcext:color-scale></calcext:conditional-format>")
    return ("<calcext:conditional-formats>" + "".join(parts)
            + "</calcext:conditional-formats>") if parts else ""


def _table_xml(index: int, sp: SheetPlan, styles: _Styles,
               band: str) -> tuple[str, str]:
    """(the <table:table>, its database range or "")."""
    top = sp.header_row
    first = top + (1 if sp.headers else 0)
    last = first + len(sp.rows) - 1
    note_row = last + 2
    end_row = max(last, note_row + len(sp.notes) - 1, top)
    cols = max(sp.column_count, 1)
    out = [f'<table:table table:name={_attr(sp.name)} '
           f'table:style-name="ta{index + 1}" table:print-ranges='
           f'{_attr(_range(sp.name, 0, 0, end_row, cols - 1))}>']
    columns = [f'<table:table-column table:style-name="{styles.column(w)}"/>'
               for w in sp.widths]
    if not sp.fit_width and columns:
        # Printed across pages: the first column repeats on each one.
        columns[0] = ("<table:table-header-columns>" + columns[0]
                      + "</table:table-header-columns>")
    out += columns
    for line in sp.above:
        out.append(_row_xml([_cell_xml(c, styles) for c in line])
                   if line else '<table:table-row table:style-name="ro1">'
                                "<table:table-cell/></table:table-row>")
    if sp.headers:
        cells = [_cell_xml(c, styles, comment=sp.comments.get((0, i)))
                 for i, c in enumerate(sp.headers)]
        out.append("<table:table-header-rows>" + _row_xml(cells)
                   + "</table:table-header-rows>")
    for n, row in enumerate(sp.rows):
        cells = [_cell_xml(c, styles, link=sp.links.get((first + n, i)),
                           comment=sp.comments.get((n + 1, i)))
                 for i, c in enumerate(row)]
        out.append(_row_xml(cells))
    if sp.notes:
        out.append('<table:table-row table:style-name="ro1"><table:table-cell/>'
                   "</table:table-row>")
        for note in sp.notes:
            out.append(_row_xml([_cell_xml(Cell(note, Style("note")),
                                           styles)]))
    out.append(_conditional(sp, first, last, band))
    out.append("</table:table>")
    db = ""
    if sp.filter and sp.body_count:
        body_last = first + sp.body_count - 1
        db = (f'<table:database-range table:name="__Anonymous_Sheet_DB__'
              f'{index}" table:target-range-address='
              f'{_attr(_range(sp.name, top, 0, body_last, cols - 1))} '
              f'table:display-filter-buttons="true"/>')
    return "".join(out), db


def _content(plan: WorkbookPlan) -> tuple[str, _Styles]:
    styles = _Styles()
    band = "RaptorBand"
    tables, ranges = [], []
    for i, sp in enumerate(plan.sheets):
        table, db = _table_xml(i, sp, styles, band)
        tables.append(table)
        if db:
            ranges.append(db)
    table_styles = "".join(
        f'<style:style style:name="ta{i + 1}" style:family="table" '
        f'style:master-page-name="Page{i + 1}"><style:table-properties '
        f'table:display="true" style:writing-mode="lr-tb"/></style:style>'
        for i in range(len(plan.sheets)))
    row_style = ('<style:style style:name="ro1" style:family="table-row">'
                 '<style:table-row-properties fo:break-before="auto" '
                 'style:use-optimal-row-height="true"/></style:style>')
    body = "".join(tables)
    if ranges:
        body += "<table:database-ranges>" + "".join(ranges) \
            + "</table:database-ranges>"
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           f"<office:document-content {_NS}>"
           "<office:automatic-styles>" + "".join(styles.number_xml)
           + "".join(styles.xml) + table_styles + row_style
           + "</office:automatic-styles><office:body><office:spreadsheet>"
           + body + "</office:spreadsheet></office:body>"
           "</office:document-content>")
    return xml, styles


# --------------------------------------------------------------------------
# styles.xml: defaults, the band style, page layouts and master pages
# --------------------------------------------------------------------------

def _region(text: str, bold: bool = False) -> str:
    span = escape(text)
    if bold:
        span = f'<text:span text:style-name="HB">{span}</text:span>'
    return f"<text:p>{span}</text:p>"


def _styles(plan: WorkbookPlan) -> str:
    layouts, masters = [], []
    f_left, f_right = plan.footer_text()
    for i, sp in enumerate(plan.sheets):
        width, height = _A4 if not sp.landscape else _A4[::-1]
        orientation = "landscape" if sp.landscape else "portrait"
        scale = ('style:scale-to-X="1"' if sp.fit_width
                 else f'style:scale-to="{WIDE_PRINT_SCALE}%"')
        layouts.append(
            f'<style:page-layout style:name="pm{i + 1}">'
            # "continue": page numbers run on across the sheets.
            f'<style:page-layout-properties '
            f'style:first-page-number="continue" fo:page-width="{width}" '
            f'fo:page-height="{height}" style:print-orientation='
            f'"{orientation}" fo:margin-top="1.0cm" fo:margin-bottom="1.0cm" '
            f'fo:margin-left="1.27cm" fo:margin-right="1.27cm" '
            f'{scale} style:table-centering="horizontal" '
            f'style:print-page-order="ttb" style:print="charts drawings '
            f'objects zero-values"/>'
            f'<style:header-style><style:header-footer-properties '
            f'fo:min-height="0.6cm" fo:margin-bottom="0.25cm"/>'
            f'</style:header-style><style:footer-style>'
            f'<style:header-footer-properties fo:min-height="0.6cm" '
            f'fo:margin-top="0.25cm"/></style:footer-style>'
            f"</style:page-layout>")
        left, right = plan.header_text(sp)
        masters.append(
            f'<style:master-page style:name="Page{i + 1}" '
            f'style:page-layout-name="pm{i + 1}">'
            f"<style:header><style:region-left>{_region(left)}"
            f"</style:region-left><style:region-right>"
            f"{_region(right, bold=True)}</style:region-right></style:header>"
            f"<style:footer><style:region-left>{_region(f_left)}"
            f"</style:region-left><style:region-center><text:p>Page "
            f"<text:page-number>1</text:page-number> of "
            f"<text:page-count>1</text:page-count></text:p>"
            f"</style:region-center><style:region-right>{_region(f_right)}"
            f"</style:region-right></style:footer></style:master-page>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-styles {_NS}><office:styles>"
        '<style:default-style style:family="table-cell">'
        '<style:text-properties fo:font-family="Calibri" '
        'fo:font-size="11pt"/></style:default-style>'
        '<style:style style:name="Default" style:family="table-cell"/>'
        f'<style:style style:name="RaptorBand" style:family="table-cell" '
        f'style:parent-style-name="Default"><style:table-cell-properties '
        f'fo:background-color="{BAND}"/></style:style>'
        '<style:style style:name="HB" style:family="text">'
        '<style:text-properties fo:font-weight="bold"/></style:style>'
        "</office:styles><office:automatic-styles>" + "".join(layouts)
        + "</office:automatic-styles><office:master-styles>"
        + "".join(masters) + "</office:master-styles>"
        "</office:document-styles>")


# --------------------------------------------------------------------------
# settings.xml: frozen panes and the first tab shown
# --------------------------------------------------------------------------

def _item(name: str, kind: str, value: object) -> str:
    return (f'<config:config-item config:name="{name}" config:type="{kind}">'
            f"{value}</config:config-item>")


def _settings(plan: WorkbookPlan) -> str:
    entries = []
    for sp in plan.sheets:
        items = [_item("CursorPositionX", "int", 0),
                 _item("CursorPositionY", "int", 0)]
        rows = sp.header_row + sp.freeze_rows if sp.freeze_rows else 0
        cols = sp.freeze_cols
        items += [
            _item("HorizontalSplitMode", "short", 2 if cols else 0),
            _item("VerticalSplitMode", "short", 2 if rows else 0),
            _item("HorizontalSplitPosition", "int", cols),
            _item("VerticalSplitPosition", "int", rows),
            _item("ActiveSplitRange", "short", 2),
            _item("PositionLeft", "int", 0),
            _item("PositionRight", "int", cols),
            _item("PositionTop", "int", 0),
            _item("PositionBottom", "int", rows),
        ]
        entries.append(f"<config:config-item-map-entry config:name="
                       f"{_attr(sp.name)}>" + "".join(items)
                       + "</config:config-item-map-entry>")
    first = escape(plan.sheets[0].name)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-settings {_NS}><office:settings>"
        '<config:config-item-set config:name="ooo:view-settings">'
        '<config:config-item-map-indexed config:name="Views">'
        "<config:config-item-map-entry>"
        + _item("ViewId", "string", "view1")
        + '<config:config-item-map-named config:name="Tables">'
        + "".join(entries) + "</config:config-item-map-named>"
        + _item("ActiveTable", "string", first)
        + _item("ShowGrid", "boolean", "true")
        + "</config:config-item-map-entry></config:config-item-map-indexed>"
        "</config:config-item-set></office:settings>"
        "</office:document-settings>")


def _meta(plan: WorkbookPlan) -> str:
    when = plan.info.written.strftime("%Y-%m-%dT%H:%M:%S")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-meta {_NS}><office:meta>"
        "<meta:generator>RAPTOR Studio</meta:generator>"
        f"<dc:title>{escape(plan.info.title)}</dc:title>"
        f"<meta:creation-date>{when}</meta:creation-date>"
        "</office:meta></office:document-meta>")


_MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:'
    'xmlns:manifest:1.0" manifest:version="1.3">'
    f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{_MIME}"'
    ' manifest:version="1.3"/>'
    '<manifest:file-entry manifest:full-path="content.xml" '
    'manifest:media-type="text/xml"/>'
    '<manifest:file-entry manifest:full-path="styles.xml" '
    'manifest:media-type="text/xml"/>'
    '<manifest:file-entry manifest:full-path="settings.xml" '
    'manifest:media-type="text/xml"/>'
    '<manifest:file-entry manifest:full-path="meta.xml" '
    'manifest:media-type="text/xml"/>'
    "</manifest:manifest>")


def render_ods(path: Path, plan: WorkbookPlan) -> None:
    """Write `plan` to `path` as an OpenDocument spreadsheet."""
    content, _styles_used = _content(plan)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # The mimetype entry comes first and is stored uncompressed, so
        # the file type can be read from fixed offsets (ODF 1.3, 3.3).
        z.writestr(zipfile.ZipInfo("mimetype"), _MIME,
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", _MANIFEST)
        z.writestr("content.xml", content)
        z.writestr("styles.xml", _styles(plan))
        z.writestr("settings.xml", _settings(plan))
        z.writestr("meta.xml", _meta(plan))


__all__ = ["render_ods"]

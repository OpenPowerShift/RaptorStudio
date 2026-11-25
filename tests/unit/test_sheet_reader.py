"""Reading .xlsx/.ods sheets, formulas and cached values."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from raptor.core.fileio import sheet_reader as S

XLSX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
 <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
XLSX_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
XLSX_WORKBOOK = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
XLSX_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
XLSX_SHEET = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData>
  <row r="1"><c r="A1" t="inlineStr"><is><t>id</t></is></c><c r="B1" t="inlineStr"><is><t>Fail Param 1</t></is></c></row>
  <row r="2"><c r="A2"><v>101</v></c><c r="B2"><f>1/2400</f></c></row>
  <row r="3"><c r="A3"><v>102</v></c><c r="B3"><f>LOG10(100)+SUM(A1:B2)</f><v>3</v></c></row>
 </sheetData>
</worksheet>"""


def _xlsx(path: Path, sheet_xml: str = XLSX_SHEET) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", XLSX_CONTENT_TYPES)
        zf.writestr("_rels/.rels", XLSX_RELS)
        zf.writestr("xl/workbook.xml", XLSX_WORKBOOK)
        zf.writestr("xl/_rels/workbook.xml.rels", XLSX_WORKBOOK_RELS)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _sheet_xml(rows_xml: str) -> str:
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<worksheet xmlns=\"http://schemas.openxmlformats.org/"
            "spreadsheetml/2006/main\"><sheetData>"
            f"{rows_xml}</sheetData></worksheet>")


def test_xlsx_reads_values_cached_formulas_and_evaluates_missing_cache(tmp_path):
    path = tmp_path / "data.xlsx"
    _xlsx(path)
    sheet = S.read_sheet(path)
    assert sheet.name == "Data"
    assert sheet.header() == ["id", "Fail Param 1"]
    assert sheet.value(1, 0) == 101
    # no cached value: the safe evaluator computes 1/2400
    assert sheet.value(1, 1) == pytest.approx(1 / 2400)
    # cached value is trusted (3), even though SUM over text cells would fail
    assert sheet.value(2, 1) == 3
    assert sheet.cell(1, 1).formula == "1/2400"


def test_the_safe_evaluator_handles_refs_ranges_functions_and_rejects_unknowns():
    sheet = S.Sheet("calc", rows=[
        [S.Cell(value=2.0), S.Cell(value=3.0)],
        [S.Cell(value=4.0), S.Cell(formula="=A1*B1+A2")],
        [S.Cell(formula="=SUM(A1:B1)+ROUND(10/3,2)")],
        [S.Cell(formula="=IF(A1>1,MAX(A1:B1),0)")],
        [S.Cell(formula="=LOG10(100)")],
        [S.Cell(formula="=NOPE(A1)")],
    ])
    S._resolve_formulas(sheet)
    assert sheet.value(1, 1) == pytest.approx(10.0)
    assert sheet.value(2, 0) == pytest.approx(8.33)
    assert sheet.value(3, 0) == pytest.approx(3.0)
    assert sheet.value(4, 0) == pytest.approx(2.0)
    assert "does not support" in sheet.cell(5, 0).error


ODS_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
 <office:body><office:spreadsheet>
  <table:table table:name="Data">
   <table:table-row>
    <table:table-cell office:value-type="string"><text:p>id</text:p></table:table-cell>
    <table:table-cell office:value-type="string"><text:p>Fail Param 1</text:p></table:table-cell>
   </table:table-row>
   <table:table-row>
    <table:table-cell office:value-type="float" office:value="101"><text:p>101</text:p></table:table-cell>
    <table:table-cell table:formula="of:=1/2400" office:value-type="float" office:value="0.0004166667"><text:p>0.0004166667</text:p></table:table-cell>
   </table:table-row>
  </table:table>
 </office:spreadsheet></office:body>
</office:document-content>"""


def test_ods_reads_values_and_formula(tmp_path):
    path = tmp_path / "data.ods"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        zf.writestr("content.xml", ODS_CONTENT)
    sheet = S.read_sheet(path)
    assert sheet.name == "Data"
    assert sheet.value(1, 0) == 101
    assert sheet.value(1, 1) == pytest.approx(0.0004166667)
    assert sheet.cell(1, 1).formula == "of:=1/2400"


def test_an_unknown_spreadsheet_extension_is_refused(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(S.SheetError, match="expected an .xlsx or .ods"):
        S.read_sheet(path)


def test_percent_is_postfix_and_a_text_cell_in_a_range_is_ignored():
    sheet = S.Sheet("calc", rows=[
        [S.Cell(value="not a number"), S.Cell(value=5.0),
         S.Cell(formula="=SUM(A1:B1)"), S.Cell(formula="=10%*200")],
    ])
    S._resolve_formulas(sheet)
    assert sheet.value(0, 2) == pytest.approx(5.0)
    assert sheet.value(0, 3) == pytest.approx(20.0)


def test_an_absurd_exponent_is_an_error_rather_than_a_hang():
    sheet = S.Sheet("calc", rows=[[S.Cell(formula="=9^9^9")]])
    S._resolve_formulas(sheet)
    assert sheet.value(0, 0) is None
    assert "too large" in sheet.cell(0, 0).error


def test_if_evaluates_only_the_branch_it_returns():
    sheet = S.Sheet("calc", rows=[
        [S.Cell(value=0.0), S.Cell(formula="=IF(A1=0,0,1/A1)"),
         S.Cell(formula="=IF(A1<>0,1,2)")],
    ])
    S._resolve_formulas(sheet)
    assert sheet.value(0, 1) == pytest.approx(0.0)
    assert not sheet.cell(0, 1).error
    # `<>` is not-equal, and `=` (not just `>`) is a comparison
    assert sheet.value(0, 2) == pytest.approx(2.0)


def test_a_reference_to_another_sheet_is_refused_not_read_locally():
    sheet = S.Sheet("calc", rows=[
        [S.Cell(value=7.0),
         S.Cell(formula="=Sheet2!A1"),
         S.Cell(formula="of:=[Sheet2.A1]")],
    ])
    S._resolve_formulas(sheet)
    assert "another sheet" in sheet.cell(0, 1).error
    assert "another sheet" in sheet.cell(0, 2).error


def test_function_names_are_only_valid_when_called():
    sheet = S.Sheet("calc", rows=[[S.Cell(formula="=SUM")]])
    S._resolve_formulas(sheet)
    assert "does not support" in sheet.cell(0, 0).error


def test_repeated_and_out_of_order_cells_keep_their_column(tmp_path):
    path = tmp_path / "data.xlsx"
    _xlsx(path, _sheet_xml(
        '<row r="1">'
        '<c r="B1"><v>2</v></c>'
        '<c r="A1"><v>1</v></c>'
        '<c r="B1"><v>3</v></c>'
        '</row>'))
    sheet = S.read_sheet(path)
    assert [c.value for c in sheet.rows[0]] == [1, 3]


def test_a_cell_beyond_the_column_limit_is_refused(tmp_path):
    path = tmp_path / "data.xlsx"
    _xlsx(path, _sheet_xml('<row r="1"><c r="A1"><v>1</v></c>'
                           '<c r="XFD1"><v>2</v></c></row>'))
    with pytest.raises(S.SheetError, match="import limit"):
        S.read_sheet(path)


def test_an_error_cell_is_reported_not_read_as_blank(tmp_path):
    path = tmp_path / "data.xlsx"
    _xlsx(path, _sheet_xml(
        '<row r="1"><c r="A1" t="e"><v>#DIV/0!</v></c></row>'))
    sheet = S.read_sheet(path)
    assert sheet.value(0, 0) is None
    assert "#DIV/0!" in sheet.cell(0, 0).error


def test_a_skipped_spreadsheet_row_keeps_its_number(tmp_path):
    path = tmp_path / "data.xlsx"
    _xlsx(path, _sheet_xml(
        '<row r="1"><c r="A1"><v>1</v></c></row>'
        '<row r="5"><c r="A5"><v>2</v></c></row>'))
    sheet = S.read_sheet(path)
    assert [c.value for c in sheet.rows[0]] == [1]
    assert sheet.source_row(0) == 1
    assert sheet.source_row(1) == 5


def test_a_blank_row_is_not_a_header(tmp_path):
    path = tmp_path / "data.xlsx"
    _xlsx(path, _sheet_xml(
        '<row r="1"><c r="A1"><v></v></c><c r="B1"><v></v></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>id</t></is></c></row>'
        '<row r="3"><c r="A3"><v>101</v></c></row>'))
    sheet = S.read_sheet(path)
    assert sheet.header() == ["id"]
    assert sheet.source_row(0) == 2


def test_a_blank_sheet_has_no_rows(tmp_path):
    path = tmp_path / "data.xlsx"
    _xlsx(path, _sheet_xml(""))
    sheet = S.read_sheet(path)
    assert sheet.rows == []
    assert sheet.header() == []


def test_a_corrupt_workbook_is_a_sheet_error(tmp_path):
    path = tmp_path / "data.xlsx"
    _xlsx(path, "this is not xml")
    with pytest.raises(S.SheetError, match="not readable"):
        S.read_sheet(path)


ODS_WITH_HEADER_ROWS = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
 <office:body><office:spreadsheet>
  <table:table table:name="Data">
   <table:table-header-rows>
    <table:table-row>
     <table:table-cell office:value-type="string"><text:p>id</text:p></table:table-cell>
     <table:table-cell office:value-type="string"><text:p>Name</text:p></table:table-cell>
     <table:table-cell office:value-type="string"><text:p>Value</text:p></table:table-cell>
    </table:table-row>
   </table:table-header-rows>
   <table:table-row>
    <table:table-cell office:value-type="float" office:value="101"><text:p>101</text:p></table:table-cell>
    <table:covered-table-cell table:number-columns-repeated="1"/>
    <table:table-cell office:value-type="float" office:value="2"><text:p>2</text:p></table:table-cell>
    <table:table-cell table:number-columns-repeated="100"/>
   </table:table-row>
  </table:table>
 </office:spreadsheet></office:body>
</office:document-content>"""


def test_ods_header_rows_covered_cells_and_padding(tmp_path):
    path = tmp_path / "data.ods"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        zf.writestr("content.xml", ODS_WITH_HEADER_ROWS)
    sheet = S.read_sheet(path)
    assert sheet.header() == ["id", "Name", "Value"]
    assert [cell.value for cell in sheet.rows[1]] == [101, None, 2]
    # the 100 padded trailing cells are dropped rather than imported
    assert sheet.columns() == 3


def test_ods_formula_without_a_cache_is_evaluated(tmp_path):
    path = tmp_path / "data.ods"
    content = ODS_CONTENT.replace(
        'office:value-type="float" office:value="0.0004166667"',
        'office:value-type="float"')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        zf.writestr("content.xml", content)
    sheet = S.read_sheet(path)
    assert sheet.value(1, 1) == pytest.approx(1 / 2400)


def test_formula_list_repetition_is_refused_not_allocated():
    sheet = S.Sheet("calc", rows=[
        [S.Cell(formula="=[1]*100000000")],
    ])
    S._resolve_formulas(sheet)
    assert sheet.cell(0, 0).error
    assert "does not support" in sheet.cell(0, 0).error

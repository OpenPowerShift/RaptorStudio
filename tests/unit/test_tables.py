"""Input/result tables and their export (CSV, text, spreadsheet).

Qt-free: the same code the GUI's Export Tables dialog and the MCP tools use.
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from raptor.api.table_export import (
    TABLES_DIRNAME, ExportOptions, default_folder, export_tables,
    format_report_text, format_table_text, plan_files, safe_stem)
from raptor.api.tables import (
    INPUTS, RESULTS, ResultsBundle, Table, UnknownTable, catalogue, collect,
    input_tables, parse_final_sections, result_tables)
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.workbook import WorkbookInfo, write_workbook
from raptor.core.fileio.xlsx_writer import Sheet, sheet_name, write_xlsx

GOLD = Path(__file__).resolve().parents[1] / "golden"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _model(name="Phasing3"):
    return load_rbd(str(GOLD / name / "input.rbd"))


@pytest.fixture(scope="module")
def run_bundle():
    """A small real run of a model with events, sparing and phases."""
    from raptor.api.run import _rd_to_dict
    from raptor.core.engine.runner import run_simulation_with_reports
    rbd = _model("TwoBlockRedundancy")
    rd, cap, cost = [], [], []
    logs, eor, final = run_simulation_with_reports(
        rbd, num_runs=3, report_sink=rd, collect_logs=True,
        capacity_sink=cap, cost_sink=cost)
    return rbd, ResultsBundle(
        model_name="TwoBlockRedundancy", num_runs=3, units="hours",
        final_lines=final, end_of_run_lines=eor,
        per_run=[_rd_to_dict(r) for r in rd], capacity_lines=cap,
        cost_lines=cost, event_logs=logs)


# -- the workbook writer -------------------------------------------------

class TestSheetNames:
    def test_illegal_characters_and_length_are_fixed(self):
        n = sheet_name("Bad:Name/With*Chars?[x]" + "y" * 40, set())
        assert len(n) <= 31 and not any(c in n for c in r"[]:*?/\\")

    def test_names_are_unique_case_insensitively(self):
        taken: set[str] = set()
        a, b, c = (sheet_name(x, taken) for x in ("Summary", "SUMMARY",
                                                   "summary"))
        assert len({a.lower(), b.lower(), c.lower()}) == 3
        assert all(len(x) <= 31 for x in (a, b, c))

    def test_an_empty_name_still_works(self):
        assert sheet_name("  ''  ", set())


def _cells_xlsx(path):
    """{tab: [[value, ...], ...]} read back with openpyxl."""
    import warnings

    from openpyxl import load_workbook
    with warnings.catch_warnings():
        # openpyxl does not read Excel 2010's data-bar extension (x14);
        # Excel and LibreOffice do. The rules themselves are still read.
        warnings.filterwarnings("ignore", category=UserWarning,
                                module="openpyxl")
        book = load_workbook(path)
    return book, {ws.title: [list(r) for r in ws.iter_rows(values_only=True)]
                  for ws in book.worksheets}


_ODF = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "number": "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0",
    "calcext": "urn:org:documentfoundation:names:experimental:calc:"
               "xmlns:calcext:1.0",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
}


def _q(prefix, name):
    return f"{{{_ODF[prefix]}}}{name}"


def _cells_ods(path):
    """{tab: [[value, ...], ...]} read from the package's content.xml."""
    with zipfile.ZipFile(path) as z:
        assert z.namelist()[0] == "mimetype"
        assert z.read("mimetype") == \
            b"application/vnd.oasis.opendocument.spreadsheet"
        root = ET.fromstring(z.read("content.xml"))
    out = {}
    for table in root.iter(_q("table", "table")):
        rows = []
        for row in table.iter(_q("table", "table-row")):
            values = []
            for cell in row.findall(_q("table", "table-cell")):
                kind = cell.get(_q("office", "value-type"))
                if kind == "float":
                    v = float(cell.get(_q("office", "value")))
                    values.append(int(v) if v.is_integer() and "."
                                  not in cell.get(_q("office", "value"))
                                  else v)
                elif kind == "string":
                    values.append("".join(
                        "".join(p.itertext())
                        for p in cell.findall(_q("text", "p"))))
                else:
                    values.append(None)
            rows.append(values)
        out[table.get(_q("table", "name"))] = rows
    return root, out


def _read_back(path):
    return (_cells_xlsx(path)[1] if str(path).endswith(".xlsx")
            else _cells_ods(path)[1])


_SAMPLE = [
    Sheet("Summary", ["Measure", "Mean"],
          [["Availability", 0.98], ["Runs", 3], ["MTBDE", None],
           ["R & D <x> \"q\"", ">21.23"], ["=SUM(A1)", "N/A"]],
          notes=["a note"], row_formats=["prob", "count", "time", "time",
                                         ""]),
    Sheet("Second", ["A"], [["x"]]),
]


@pytest.mark.parametrize("ext", ["xlsx", "ods"])
class TestWorkbookBothFormats:
    """What every spreadsheet export promises, in both file formats."""

    def test_tabs_and_typed_cells(self, tmp_path, ext):
        p = write_workbook(tmp_path / f"t.{ext}", _SAMPLE,
                           WorkbookInfo(title="T"))
        tabs = _read_back(p)
        assert list(tabs) == ["Contents", "Summary", "Second"]
        rows = tabs["Summary"]
        assert rows[0] == ["Measure", "Mean"]          # row 1 is the header
        assert rows[1] == ["Availability", 0.98]       # a number
        assert rows[2][1] == 3
        assert rows[3][1] is None                      # None: empty
        assert rows[4] == ['R & D <x> "q"', 21.23]     # censored: a number
        assert rows[5] == ["=SUM(A1)", "N/A"]          # text, not a formula
        assert "a note" in [r[0] for r in rows if r]

    def test_contents_lists_and_counts_every_tab(self, tmp_path, ext):
        tabs = _read_back(write_workbook(tmp_path / f"t.{ext}", _SAMPLE,
                                         WorkbookInfo(title="My book")))
        contents = tabs["Contents"]
        assert contents[0][0] == "My book"
        listed = {r[0]: r[2] for r in contents if r and r[0] in
                  ("Summary", "Second")}
        assert listed == {"Summary": 5, "Second": 1}

    def test_one_tab_needs_no_contents(self, tmp_path, ext):
        tabs = _read_back(write_workbook(tmp_path / f"t.{ext}", _SAMPLE[1:]))
        assert list(tabs) == ["Second"]

    def test_a_glossary_tab_lists_the_terms_used(self, tmp_path, ext):
        tabs = _read_back(write_workbook(
            tmp_path / f"t.{ext}", _SAMPLE,
            WorkbookInfo(glossary={"MTBDE": "Mean time between downing "
                                            "events", "SEM": "unused"})))
        assert list(tabs)[-1] == "Glossary"
        terms = {r[0]: r[1] for r in tabs["Glossary"][1:]
                 if len(r) > 1 and r[1]}
        assert terms == {"MTBDE": "Mean time between downing events"}

    def test_sorted_with_totals_kept_last(self, tmp_path, ext):
        sheet = Sheet("Cost", ["Block", "Total"],
                      [["A", "1.00"], ["Total", "9.00"], ["B", "5.00"],
                       ["C", "N/A"]], formats=["", "cost"], text_cols={0},
                      sort=(1, True))
        rows = _read_back(write_workbook(tmp_path / f"t.{ext}", [sheet]))[
            "Cost"]
        assert [r[0] for r in rows[1:6]] == ["B", "A", "C", "Total", None]
        assert rows[6][0] == "Sorted by Total, largest first."

    def test_control_characters_do_not_corrupt_the_file(self, tmp_path,
                                                         ext):
        p = write_workbook(tmp_path / f"t.{ext}",
                           [Sheet("S", ["a"], [["bad\x00\x01char"]])])
        assert _read_back(p)["S"][1] == ["badchar"]

    def test_empty_sheets_are_valid(self, tmp_path, ext):
        tabs = _read_back(write_workbook(
            tmp_path / f"t.{ext}", [Sheet("E", [], []),
                                    Sheet("F", ["a"], [])]))
        assert {"E", "F"} <= set(tabs)

    @pytest.mark.skipif(not shutil.which("soffice"),
                        reason="LibreOffice not installed")
    def test_libreoffice_opens_it_and_sees_the_values(self, tmp_path, ext):
        """A real spreadsheet application reads every tab, with numbers as
        numbers."""
        write_workbook(tmp_path / f"t.{ext}", [
            Sheet("Summary", ["Measure", "Mean"],
                  [["Availability", 0.9871234567], ["Runs", 3]],
                  row_formats=["prob", "count"]),
            Sheet("Blocks", ["Name", "Cost"], [["Pump", -2.5e-7]])])
        subprocess.run(
            ["soffice", "--headless", "--convert-to",
             "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,"
             "false,false,false,-1", f"t.{ext}"],
            cwd=tmp_path, capture_output=True, timeout=120, check=True)
        # The cell keeps full precision; the format only sets what is shown.
        assert (tmp_path / "t-Summary.csv").read_text().splitlines() == [
            "Measure,Mean", "Availability,0.9871234567", "Runs,3"]
        assert "Pump" in (tmp_path / "t-Blocks.csv").read_text()


class TestXlsxLayout:
    """The layout decisions, read back from the .xlsx with openpyxl."""

    def _book(self, tmp_path, sheets, info=None):
        return _cells_xlsx(write_workbook(tmp_path / "t.xlsx", sheets,
                                          info))[0]

    def test_header_frozen_filtered_and_printed_on_every_page(self,
                                                             tmp_path):
        ws = self._book(tmp_path, [Sheet("S", ["a", "b"],
                                         [[1, 2], [3, 4]])])["S"]
        assert ws.freeze_panes == "A2"
        assert ws.auto_filter.ref == "A1:B3"
        assert ws["A1"].font.b and ws["A1"].alignment.wrap_text
        assert ws.print_title_rows == "$1:$1"
        assert ws.page_setup.paperSize == 9               # A4
        # One page wide (fitToWidth defaults to 1), as many tall as needed.
        assert ws.sheet_properties.pageSetUpPr.fitToPage
        assert ws.page_setup.fitToHeight in (0, "0")
        assert ws.page_setup.orientation == "portrait"
        assert "Page &P of &N" in ws.oddFooter.center.text

    def test_a_wide_tab_prints_landscape(self, tmp_path):
        ws = self._book(tmp_path, [Sheet(
            "W", [f"Column number {i}" for i in range(12)],
            [[1.5] * 12])])["W"]
        assert ws.page_setup.orientation == "landscape"

    def test_numbers_right_aligned_and_formatted_by_kind(self, tmp_path):
        ws = self._book(tmp_path, [_SAMPLE[0]])["Summary"]
        assert ws["B2"].number_format == "0.000000"         # probability
        assert ws["B2"].alignment.horizontal == "right"
        assert ws["B3"].number_format == "#,##0"            # a count
        assert ws["B5"].number_format == '">"#,##0.000'     # censored time
        assert ws["A2"].alignment.horizontal == "left"
        assert ws["B6"].font.i                              # N/A muted

    def test_banding_bars_and_scales(self, tmp_path):
        sheet = Sheet("States", ["Block", "Green %", "Red %", "Avail"],
                      [["A", 90, 10, 0.9], ["B", 80, 20, 0.8],
                       ["C", 70, 30, 0.95]],
                      formats=["", "pct", "pct", "prob"], text_cols={0})
        ws = self._book(tmp_path, [sheet])["States"]
        rules = {str(rng.sqref): [r.type for r in rng.rules]
                 for rng in ws.conditional_formatting}
        assert rules["A2:D4"] == ["expression"]             # banding
        assert rules["B2:B4"] == ["dataBar"]
        assert rules["C2:C4"] == ["dataBar"]
        assert rules["D2:D4"] == ["colorScale"]

    def test_a_constant_column_is_not_coloured(self, tmp_path):
        sheet = Sheet("S", ["Block", "Avail"],
                      [["A", 1.0], ["B", 1.0], ["C", 1.0]],
                      formats=["", "prob"], text_cols={0})
        ws = self._book(tmp_path, [sheet])["S"]
        assert [str(r.sqref) for r in ws.conditional_formatting] == ["A2:B4"]

    def test_totals_are_outside_the_filter_and_the_bars(self, tmp_path):
        sheet = Sheet("Cost", ["Block", "Total"],
                      [["A", 1.0], ["B", 5.0], ["Total", 6.0]],
                      formats=["", "cost"], text_cols={0})
        ws = self._book(tmp_path, [sheet])["Cost"]
        assert ws.auto_filter.ref == "A1:B3"
        assert "B2:B3" in [str(r.sqref) for r in ws.conditional_formatting]
        assert ws["A4"].font.b and ws["A4"].border.top.style == "thin"

    def test_acronyms_carry_a_comment(self, tmp_path):
        book = self._book(tmp_path, [Sheet("S", ["Block", "MDT"],
                                           [["A", 1.0]])],
                          WorkbookInfo(glossary={"MDT": "Mean Down Time"}))
        assert "MDT: Mean Down Time" in book["S"]["B1"].comment.text

    def test_contents_links_to_each_tab(self, tmp_path):
        book = self._book(tmp_path, _SAMPLE, WorkbookInfo(title="T"))
        links = [c.hyperlink.location for row in book["Contents"].iter_rows()
                 for c in row if c.hyperlink is not None]
        assert links == ["'Summary'!A1", "'Second'!A1"]

    def test_unknown_extension_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="xlsx or .ods"):
            write_workbook(tmp_path / "t.xls", _SAMPLE)

    def test_a_workbook_needs_a_sheet(self, tmp_path):
        with pytest.raises(ValueError):
            write_xlsx(tmp_path / "t.xlsx", [])


    def test_numbers_of_no_known_kind_get_one_format_per_column(self,
                                                                tmp_path):
        ws = self._book(tmp_path, [Sheet(
            "G", ["Whole", "Mixed", "Small"],
            [[1200, 12.5, 0.0012], [3, 1200, 0.5]])])["G"]
        assert ws["A2"].number_format == "#,##0"
        # 1200 is whole, but its column is not: the decimals line up.
        assert ws["B2"].number_format == ws["B3"].number_format == \
            "#,##0.000"
        assert ws["C2"].number_format == "0.000000"

    def test_a_footer_starting_with_a_digit_keeps_it(self, tmp_path):
        from datetime import datetime
        info = WorkbookInfo(title="T", written=datetime(2026, 11, 5, 6, 8))
        ws = self._book(tmp_path, [Sheet("S", ["a"], [[1]])], info)["S"]
        # "&8" + "2026" would be a font size of 82026, eating the year.
        assert ws.oddFooter.right.text == "2026-11-05 06:08"

    def test_a_term_in_words_is_explained_in_any_case(self, tmp_path):
        from raptor.api.glossary import TERMS
        ws = self._book(tmp_path, [Sheet("S", ["Lost Flow", "pm"], [[1, 2]])],
                        WorkbookInfo(glossary=TERMS))["S"]
        assert ws["A1"].comment.text.startswith("Lost flow:")
        assert ws["B1"].comment is None       # an acronym only as written


class TestOdsLayout:
    """The same decisions in the OpenDocument file."""

    def test_filter_frozen_header_print_and_colour(self, tmp_path):
        sheet = Sheet("States", ["Block", "Green %", "Avail"],
                      [["A", 90, 0.9], ["B", 80, 0.8], ["C", 70, 0.95]],
                      formats=["", "pct", "prob"], text_cols={0})
        p = write_workbook(tmp_path / "t.ods", [sheet])
        root, _tabs = _cells_ods(p)
        ranges = [r.get(_q("table", "target-range-address"))
                  for r in root.iter(_q("table", "database-range"))]
        assert ranges == ["'States'.A1:'States'.C4"]
        assert root.find(".//" + _q("table", "table-header-rows")) is not None
        kinds = [child.tag.split("}")[1]
                 for f in root.iter(_q("calcext", "conditional-format"))
                 for child in f]
        assert kinds == ["condition", "data-bar", "color-scale"]
        with zipfile.ZipFile(p) as z:
            styles = z.read("styles.xml").decode()
            settings = z.read("settings.xml").decode()
        assert 'style:print-orientation="portrait"' in styles
        assert 'style:scale-to-X="1"' in styles
        assert 'fo:page-width="21.0cm"' in styles             # A4
        assert "<text:page-count>" in styles
        assert ('config:name="VerticalSplitPosition" config:type="int">1<'
                in settings)

    def test_numbers_keep_their_places(self, tmp_path):
        p = write_workbook(tmp_path / "t.ods", [_SAMPLE[0]])
        with zipfile.ZipFile(p) as z:
            content = z.read("content.xml").decode()
        assert 'number:decimal-places="6" number:min-decimal-places="6"' \
            in content                                        # probability
        assert "<number:text>&gt;</number:text>" in content   # censored

    def test_page_numbers_run_on_across_sheets(self, tmp_path):
        p = write_workbook(tmp_path / "t.ods", list(_SAMPLE))
        with zipfile.ZipFile(p) as z:
            styles = z.read("styles.xml").decode()
        layouts = styles.count("<style:page-layout-properties ")
        assert layouts >= 2
        assert styles.count('style:first-page-number="continue"') == layouts


# -- input tables ----------------------------------------------------------

class TestInputTables:
    def test_the_expected_tables_exist_in_order(self):
        keys = [t.key for t in input_tables(_model())]
        assert keys[:3] == ["inputs.system", "inputs.blocks",
                            "inputs.blocks_all"]
        assert {"inputs.nodes", "inputs.links", "inputs.phases",
                "inputs.spare_pools", "inputs.triggers"} <= set(keys)

    def test_the_id_zero_template_row_is_not_an_element(self):
        rbd = _model()
        real = [r for r in rbd.sections["BLOCK_DATA"].rows
                if r["ID"] not in ("0", "0.000000")]
        blocks = next(t for t in input_tables(rbd)
                      if t.key == "inputs.blocks")
        assert len(blocks.rows) == len(real)
        assert all(r[0] not in ("0", "0.000000") for r in blocks.rows)

    def test_blocks_summary_reads_in_words(self):
        t = next(t for t in input_tables(_model())
                 if t.key == "inputs.blocks")
        h = {n: i for i, n in enumerate(t.headers)}
        row = t.rows[0]
        assert row[h["Failure Distribution"]] not in ("", "15")   # a name
        assert row[h["Sparing"]] in ("Infinite", "None", "Pooled", "Custom")

    def test_the_all_columns_table_has_every_column(self):
        rbd = _model()
        t = next(t for t in input_tables(rbd) if t.key == "inputs.blocks_all")
        assert t.headers == list(rbd.sections["BLOCK_DATA"].columns)

    def test_linksresolve_element_names(self):
        rbd = _model()
        t = next(t for t in input_tables(rbd) if t.key == "inputs.links")
        assert t.headers[:2] == ["ID", "From"]
        names = {r["blockName"] for r in rbd.sections["BLOCK_DATA"].rows}
        assert any(row[1] in names or row[4] in names for row in t.rows)

    def test_system_settings_leave_out_the_image_string(self):
        t = next(t for t in input_tables(_model()) if t.key == "inputs.system")
        assert t.headers == ["Setting", "Value"]
        settings = [r[0] for r in t.rows]
        assert not any("image" in s.lower() for s in settings)
        assert "Termination type" in settings

    def test_a_model_without_events_has_no_events_table(self):
        keys = {t.key for t in input_tables(_model("TwoBlockRedundancy"))}
        assert "inputs.events" not in keys

    def test_names_stay_text_columns(self):
        t = next(t for t in input_tables(_model())
                 if t.key == "inputs.blocks_all")
        assert t.headers.index("blockName") in t.text_cols


# -- result tables -----------------------------------------------------------

class TestParameterNotes:
    def test_names_each_used_distributions_parameters_in_order(self):
        from raptor.api.tables import param_notes
        notes = param_notes([15, 7, 15])
        assert notes == ["What the distribution parameters are, in order:",
                         "Lognormal: 1 mean, 2 standard deviation.",
                         "Weibull: 1 shape, 2 scale, 3 location."]
        coded = param_notes([15], with_codes=True)
        assert coded[1] == "15 = Weibull: 1 shape, 2 scale, 3 location."
        assert param_notes([]) == []

    def test_block_and_trigger_tables_carry_them(self):
        tables = {t.key: t for t in input_tables(_model("Phasing3"))}
        assert any(n.startswith("What the distribution parameters")
                   for n in tables["inputs.blocks"].notes)
        assert any(n.startswith("Distribution codes")
                   for n in tables["inputs.blocks_all"].notes)


class TestParseFinalSections:
    def test_a_stats_table(self):
        got = parse_final_sections([
            "***SUMMARY***", "", "Minimum Mean Maximum St.Dev SEM",
            "Availability 0.74 0.80 0.90 0.05 0.02",
            "MTBDE 4.94 >21.23 >46.54 N/A N/A"])
        (title, cols, rows), = got
        assert title == "SUMMARY"
        assert cols == ["Minimum", "Mean", "Maximum", "St.Dev", "SEM"]
        assert rows[1] == ("MTBDE", ["4.94", ">21.23", ">46.54", "N/A", "N/A"])

    def test_a_named_row_table(self):
        got = parse_final_sections([
            "***SPARING***", "blockName SpareSource MinUsed",
            "unnamed Custom 3"])
        assert got[0][2] == [("unnamed", ["Custom", "3"])]

    def test_text_outside_a_section_and_junk_rows_are_ignored(self):
        assert parse_final_sections(["Results from 3 runs", "x 1 2"]) == []


class TestResultTables:
    def test_per_run_headers_are_words_with_formats(self):
        from raptor.api.presentation import table_kinds
        bundle = ResultsBundle(per_run=[{
            "availability": 0.9, "units_produced": 12345.678,
            "capacity_weighted_availability": 0.8, "lost_flow": 1.5,
            "mtbde_censored": True, "some_new_measure": 2.0}])
        t = next(t for t in result_tables(bundle)
                 if t.key == "results.per_run")
        assert t.headers == ["Run", "Availability", "Units Produced",
                             "Capacity-Weighted Availability", "Lost Flow",
                             "MTBDE Censored", "Some New Measure"]
        kinds, _ = table_kinds(t)
        assert kinds[2:5] == ["flow", "prob", "flow"]

    def test_a_real_run_gives_the_expected_tables(self, run_bundle):
        _rbd, bundle = run_bundle
        keys = [t.key for t in result_tables(bundle)]
        assert keys[0] == "results.summary"
        assert "results.per_run" in keys
        assert "results.event_log" in keys and "results.end_of_run" in keys

    def test_summary_carries_the_reports_numbers(self, run_bundle):
        _rbd, bundle = run_bundle
        t = next(t for t in result_tables(bundle)
                 if t.key == "results.summary")
        assert t.headers[:3] == ["Measure", "Minimum", "Mean"]
        labels = [r[0] for r in t.rows]
        assert "Availability" in labels
        assert any(lb.startswith("MTBDE (hours") for lb in labels)
        assert t.section == "Summary"

    def test_weak_link_keeps_node_and_block_tables_apart(self):
        lines = (GOLD / "LadL7Weak" / "ours" /
                 "rpt_final_results_ours.txt").read_text().splitlines()
        tables = result_tables(ResultsBundle(final_lines=lines))
        weak = {t.title: t for t in tables if t.section == "Weak link"}
        assert {"Node availability", "Block availability", "Node states",
                "Block states"} <= set(weak)
        assert weak["Node states"].headers[:3] == ["Node", "Green %",
                                                   "Yellow %"]
        assert len(weak["Block availability"].rows) == 17

    def test_a_workbook_starts_with_run_info_and_formats_its_numbers(
            self, run_bundle, tmp_path):
        from raptor.api.presentation import run_info
        rbd, bundle = run_bundle
        info = run_info(rbd, model="TwoBlockRedundancy", runs=3,
                        units="hours")
        res = export_tables(collect(rbd, bundle, ["results.summary",
                                                  "results.end_of_run"]),
                            tmp_path, ExportOptions(xlsx=True),
                            model_name="m", info=info)
        names = sorted(f.name for f in res.files)
        assert names == ["m_results_end_of_run.txt", "m_tables.xlsx"]
        book, tabs = _cells_xlsx(tmp_path / "m_tables.xlsx")
        assert book.sheetnames[:3] == ["Contents", "Run Info", "Summary"]
        assert ["Model", "TwoBlockRedundancy"] in tabs["Run Info"]
        ws = book["Summary"]
        row = next(r for r in ws.iter_rows(min_row=2)
                   if r[0].value == "Availability")
        assert row[2].number_format == "0.000000"   # availability: 6 places

    def test_capacity_keeps_its_totals_and_lost_flow(self):
        lines = (GOLD / "LadL7Cap" / "ours" /
                 "rpt_final_results_ours.txt").read_text().splitlines()
        cap = {t.title: t for t in result_tables(ResultsBundle(
            final_lines=lines)) if t.section == "Capacity"}
        assert [r[0] for r in cap["Flow"].rows] == ["Flow generated",
                                                    "Units produced"]
        assert cap["Lost flow"].headers == ["Min lost flow",
                                            "Avg lost flow",
                                            "Max lost flow"]
        assert cap["System capacity"].rows[0][0] == "System"
        assert cap["Node capacity"].headers[0] == "Node"

    def test_per_run_has_one_row_per_run(self, run_bundle):
        _rbd, bundle = run_bundle
        t = next(t for t in result_tables(bundle)
                 if t.key == "results.per_run")
        assert [r[0] for r in t.rows] == [1, 2, 3]
        assert "Availability" in t.headers

    def test_event_log_rows_are_numbered_by_run(self, run_bundle):
        _rbd, bundle = run_bundle
        t = next(t for t in result_tables(bundle)
                 if t.key == "results.event_log")
        assert {r[0] for r in t.rows} == {1, 2, 3}

    def test_event_log_is_one_column_per_field(self, run_bundle):
        """The engine writes tab-separated fields and the GUI already
        splits them; the export used to drop the whole raw line into one
        "Line" cell, leaving nothing a spreadsheet could sort or filter."""
        from raptor.api.tables import EVENT_LOG_HEADERS
        _rbd, bundle = run_bundle
        t = next(t for t in result_tables(bundle)
                 if t.key == "results.event_log")
        h = list(EVENT_LOG_HEADERS)
        # The element's ID follows its name: names are not unique.
        assert t.headers == ["Run", *h[:3], "ID", *h[3:]]
        # Every cell must land in a column that has a header above it --
        # the unlabelled-tail failure mode the Cost sheet had.
        for row in t.rows:
            assert len(row) == len(t.headers)
        first = bundle.event_logs[0][0]
        fields = [f.strip() for f in first.split("\t")][:len(h)]
        row = t.rows[0]
        assert row[1:4] + row[5:5 + len(fields) - 3] == fields

    def test_every_element_row_carries_its_id(self, run_bundle):
        """Names are not unique, so a result row about a block, node or
        event says which one by id; the report text itself is unchanged."""
        _rbd, bundle = run_bundle
        tables = {t.key: t for t in result_tables(bundle)}
        log = tables["results.event_log"]
        i_type, i_id = log.headers.index("Type"), log.headers.index("ID")
        blocks = [r for r in log.rows if r[i_type] == "Block"]
        assert blocks and all(isinstance(r[i_id], int) for r in blocks)
        named = [t for t in tables.values() if "ID" in t.headers
                 and t.key != "results.event_log"]
        assert named, "no Final Results table carries element ids"
        for t in named:
            i = t.headers.index("ID")
            assert all(r[i] != "" for r in t.rows
                       if str(r[0]).strip().lower() != "total"), t.key

    def test_saved_tags_round_trip(self):
        from raptor.core.engine.report_rows import (
            apply_tags, element_of, tag, tags_of)
        lines = ["head", tag("Pump   3.0", "block", 7), "tail"]
        saved = tags_of(lines)
        assert saved == [[1, "block", 7]]
        back = apply_tags([str(x) for x in lines], saved)
        assert back == lines and element_of(back[1]) == ("block", 7)
        assert element_of(back[0]) is None

    def test_cost_section_splits_into_one_table_per_sub_table(self):
        """`***COST***` is seven tables plus a summary tail under one
        marker. Emitting it as one table gave the sheet the LAST header
        over rows from all of them -- values in columns F..J with nothing
        above them. Synthetic, shaped like `_cost_table_lines`."""
        from raptor.api.tables import parse_final_sections
        secs = parse_final_sections([
            "***COST***",
            "",
            "Operating",
            "",
            "Block InHier Good Repair Idle RepHold PM PMHold Standby Done",
            "Pump        0     1.00    0.50  0.20   0.10  0.05  0.05   0.15   0.10   0.00",
            "",
            "Sparing",
            "",
            "Block InitSpares NewSpares EmerShip",
            "Pump        1.00      2.00    3.00",
            "",
            "SparePools               Initial   NewSpares    EmerShip       Total",
            "Pool1                1.00         2.00      3.00      6.00",
            "",
            "Cost_Summary",
            "",
            "Blocks               1.50",
            "Events               0.00",
            "",
            "Grand_Total          1.50",
        ])
        assert [s[0] for s in secs] == [
            "COST - Operating", "COST - Sparing", "COST - SparePools",
            "COST - Cost_Summary"], f"got {[s[0] for s in secs]}"
        for title, columns, rows in secs:
            assert all(h != "" for h in columns), \
                f"{title}: unlabelled column(s) {columns}"
            for label, values in rows:
                # A named-row table's header carries the label column, so
                # its rows are legitimately one narrower. The invariant is
                # only that NO row is WIDER than the header.
                assert len(values) <= len(columns), (
                    f"{title}: row {label!r} is {len(values)} wide under a "
                    f"{len(columns)}-column header {columns}")
        # The Grand Total used to be dropped entirely: it is a 2-token
        # line, so it matched no group's header width.
        summary = secs[-1]
        assert summary[0] == "COST - Cost_Summary"
        assert ("Grand_Total", ["1.50"]) in summary[2]


# -- catalogue and collect -----------------------------------------------------

class TestCatalogue:
    def test_before_a_run_results_are_listed_but_unavailable(self):
        infos = catalogue(_model())
        res = [i for i in infos if i.group == RESULTS]
        assert res and not res[0].available
        assert "run a simulation" in res[0].reason
        assert all(i.available for i in infos if i.group == INPUTS)

    def test_after_a_run_the_long_tables_are_off_by_default(self, run_bundle):
        rbd, bundle = run_bundle
        by_key = {i.key: i for i in catalogue(rbd, bundle)}
        assert by_key["results.summary"].default
        assert not by_key["results.event_log"].default
        assert not by_key["results.end_of_run"].default
        assert not by_key["inputs.blocks_all"].default

    def test_collect_defaults_match_the_catalogue(self, run_bundle):
        rbd, bundle = run_bundle
        defaults = {i.key for i in catalogue(rbd, bundle) if i.default}
        assert {t.key for t in collect(rbd, bundle)} == defaults

    def test_collect_returns_the_asked_for_tables_in_catalogue_order(
            self, run_bundle):
        rbd, bundle = run_bundle
        got = [t.key for t in collect(rbd, bundle,
                                      ["results.summary", "inputs.system"])]
        assert got == ["inputs.system", "results.summary"]

    def test_an_unknown_key_names_the_valid_ones(self):
        with pytest.raises(UnknownTable) as e:
            collect(_model(), None, ["inputs.nope"])
        assert "inputs.blocks" in str(e.value)
        assert not str(e.value).startswith("'")     # not KeyError-quoted

    def test_result_tables_need_a_run(self):
        with pytest.raises(UnknownTable):
            collect(_model(), None, ["results.summary"])

    def test_prebuilt_tables_give_the_same_answers(self, run_bundle):
        from raptor.api.tables import all_tables
        rbd, bundle = run_bundle
        built = all_tables(rbd, bundle)
        assert catalogue(rbd, bundle, tables=built) == catalogue(rbd, bundle)
        keys = ["results.summary", "inputs.blocks"]
        assert [t.key for t in collect(rbd, bundle, keys, tables=built)] \
            == [t.key for t in collect(rbd, bundle, keys)]


# -- export ----------------------------------------------------------------------

def _sample_tables():
    return [
        Table("inputs.blocks", "Blocks", INPUTS, ["ID", "Name", "Cost"],
              [["1", "Pump", "12.500000"], ["2", "12", "3"]],
              text_cols={1}),
        Table("results.summary", "Summary", RESULTS,
              ["Summary", "Mean"],
              [["Availability", 0.98], ["MTBDE", None]], notes=["n"]),
    ]


class TestExport:
    def test_progress_names_each_file_before_it_is_written(self, tmp_path):
        told = []
        result = export_tables(_sample_tables(), tmp_path,
                               ExportOptions(csv=True, xlsx=True),
                               stem="m", progress=told.append)
        assert told == [f.name for f in result.files]

    def test_no_format_or_no_tables_is_an_error(self, tmp_path):
        with pytest.raises(ValueError, match="format"):
            export_tables(_sample_tables(), tmp_path, ExportOptions())
        with pytest.raises(ValueError, match="no tables"):
            export_tables([], tmp_path, ExportOptions(csv=True))

    def test_csv_one_file_per_table_with_a_bom(self, tmp_path):
        res = export_tables(_sample_tables(), tmp_path,
                            ExportOptions(csv=True), model_name="M.rbd")
        assert [f.name for f in res.files] == [
            "M_inputs_blocks.csv", "M_results_summary.csv"]
        raw = res.files[0].read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        rows = list(csv.reader(res.files[0].read_text(
            encoding="utf-8-sig").splitlines()))
        assert rows == [["ID", "Name", "Cost"], ["1", "Pump", "12.500000"],
                        ["2", "12", "3"]]
        blank = list(csv.reader(res.files[1].read_text(
            encoding="utf-8-sig").splitlines()))
        assert blank[2] == ["MTBDE", ""]                # None is blank

    def test_text_files_align_numbers_right(self, tmp_path):
        res = export_tables(_sample_tables(), tmp_path,
                            ExportOptions(txt_files=True), stem="M")
        text = res.files[0].read_text()
        assert text.startswith("Blocks\n======")
        lines = text.splitlines()
        assert lines[2].split() == ["ID", "Name", "Cost"]
        assert lines[4].endswith("12.500000")           # right-aligned number
        assert lines[5].split()[1] == "12"              # a text column

    def test_the_combined_report_lists_its_contents(self, tmp_path):
        res = export_tables(_sample_tables(), tmp_path,
                            ExportOptions(txt_report=True), model_name="M")
        text = res.files[0].read_text()
        assert [f.name for f in res.files] == ["M_tables.txt"]
        assert "Inputs:\n  Blocks  (2 rows)" in text
        assert "Results:\n  Summary  (2 rows)" in text
        assert "# INPUTS" in text and "# RESULTS" in text
        assert "N/A" in text                            # None shown as N/A

    def test_the_workbook_has_a_tab_per_table_with_numbers(self, tmp_path):
        res = export_tables(_sample_tables(), tmp_path,
                            ExportOptions(xlsx=True), stem="M")
        book, tabs = _cells_xlsx(res.files[0])
        # MTBDE is in the Summary, so a Glossary closes the workbook.
        assert book.sheetnames == ["Contents", "Blocks", "Summary",
                                   "Glossary"]
        blocks = tabs["Blocks"]
        assert blocks[1][2] == 12.5                             # number
        assert blocks[1][0] == 1                                # id: number
        assert blocks[2][1] == "12"                             # name: text

    def test_same_titles_get_a_group_prefix_on_their_tabs(self, tmp_path):
        tables = [Table("inputs.resources", "Resources", INPUTS, ["a"], [[1]]),
                  Table("results.resources", "Resources", RESULTS, ["a"],
                        [[2]])]
        res = export_tables(tables, tmp_path, ExportOptions(xlsx=True),
                            stem="M")
        assert _cells_xlsx(res.files[0])[0].sheetnames == [
            "Contents", "Inputs - Resources", "Results - Resources"]

    def test_the_same_name_in_another_case_gets_a_prefix_too(self,
                                                             tmp_path):
        tables = [Table("inputs.spare_pools", "Spare Pools", INPUTS, ["a"],
                        [[1]]),
                  Table("results.pools", "Spare pools", RESULTS, ["a"],
                        [[2]])]
        res = export_tables(tables, tmp_path, ExportOptions(xlsx=True),
                            stem="M")
        assert _cells_xlsx(res.files[0])[0].sheetnames[1:] == [
            "Inputs - Spare Pools", "Results - Spare pools"]

    def test_excel_and_opendocument_hold_the_same_tables(self, tmp_path):
        res = export_tables(_sample_tables(), tmp_path,
                            ExportOptions(xlsx=True, ods=True), stem="M")
        assert sorted(f.name for f in res.files) == ["M_tables.ods",
                                                     "M_tables.xlsx"]
        xlsx = _read_back(tmp_path / "M_tables.xlsx")
        ods = _read_back(tmp_path / "M_tables.ods")
        assert list(xlsx) == list(ods)
        def filled(rows):
            out = []
            for r in rows:
                r = list(r)
                while r and r[-1] is None:
                    r.pop()
                if r:
                    out.append(r)
            return out

        for tab in xlsx:
            assert filled(xlsx[tab]) == filled(ods[tab]), tab

    def test_every_format_at_once_and_the_plan_matches(self, tmp_path):
        opts = ExportOptions(csv=True, txt_files=True, txt_report=True,
                             xlsx=True, ods=True)
        tables = _sample_tables()
        planned = plan_files(tables, opts, "M")
        res = export_tables(tables, tmp_path / "sub" / "dir", opts, stem="M")
        assert sorted(f.name for f in res.files) == sorted(planned)
        assert len(planned) == 2 + 2 + 1 + 2

    def test_default_folder_is_named_tables_beside_the_model(self, tmp_path):
        assert TABLES_DIRNAME == "Tables"
        assert default_folder(tmp_path / "Plant.rbds") == \
            (tmp_path / "Tables").resolve()

    def test_safe_stem(self):
        assert safe_stem("My Plant (v2).rbds") == "My_Plant_v2"
        assert safe_stem("") == "model"

    def test_a_real_run_exports_every_default_table(self, run_bundle,
                                                    tmp_path):
        rbd, bundle = run_bundle
        tables = collect(rbd, bundle)
        res = export_tables(tables, tmp_path, ExportOptions(
            csv=True, txt_report=True, xlsx=True), model_name="TwoBlock")
        assert len(res.files) == len(tables) + 2
        assert (tmp_path / "TwoBlock_tables.xlsx").stat().st_size > 1000


class TestTextFormatting:
    def test_single_column_tables_print_as_plain_lines(self):
        t = Table("results.event_log", "Event Log", RESULTS, ["Line"],
                  [["first"], ["second"]])
        assert format_table_text(t).splitlines()[-2:] == ["first", "second"]

    def test_the_report_names_the_model(self):
        assert "Tables for Plant" in format_report_text(
            _sample_tables(), "Plant")

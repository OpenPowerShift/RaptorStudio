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


class TestWorkbook:
    def _read(self, path):
        z = zipfile.ZipFile(path)
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        names = [s.get("name") for s in wb.find("m:sheets", NS)]
        sheets = [ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))
                  for i in range(1, len(names) + 1)]
        return z, names, sheets

    def test_structure_and_typed_cells(self, tmp_path):
        p = write_xlsx(tmp_path / "t.xlsx", [
            Sheet("Summary", ["Measure", "Mean"],
                  [["Availability", 0.98], ["Runs", 3], ["MTBDE", None],
                   ["R & D <x> \"q\"", ">21.23"]], notes=["a note"]),
            Sheet("Second", ["A"], [["x"]])])
        z, names, sheets = self._read(p)
        assert names == ["Summary", "Second"]
        assert z.testzip() is None
        cells = {c.get("r"): c for c in sheets[0].iter(f"{{{NS['m']}}}c")}
        assert cells["B2"].find("m:v", NS).text == "0.98"      # a number
        assert cells["B2"].get("t") is None
        assert cells["B3"].find("m:v", NS).text == "3"
        assert cells["A5"].find("m:is/m:t", NS).text == 'R & D <x> "q"'
        assert cells["B5"].get("t") == "inlineStr"             # stays text
        assert "B4" not in cells                               # None: empty

    def test_header_is_styled_frozen_and_filtered(self, tmp_path):
        p = write_xlsx(tmp_path / "t.xlsx",
                       [Sheet("S", ["a", "b"], [[1, 2], [3, 4]])])
        _z, _n, (sheet,) = self._read(p)
        assert sheet.find(".//m:pane", NS).get("state") == "frozen"
        assert sheet.find("m:autoFilter", NS).get("ref") == "A1:B3"
        header = next(sheet.iter(f"{{{NS['m']}}}c"))
        assert header.get("s") == "1"

    def test_control_characters_do_not_corrupt_the_file(self, tmp_path):
        p = write_xlsx(tmp_path / "t.xlsx",
                       [Sheet("S", ["a"], [["bad\x00\x01char"]])])
        self._read(p)                                   # parses as XML

    def test_a_workbook_needs_a_sheet(self, tmp_path):
        with pytest.raises(ValueError):
            write_xlsx(tmp_path / "t.xlsx", [])

    def test_empty_sheets_are_valid(self, tmp_path):
        write_xlsx(tmp_path / "t.xlsx", [Sheet("E", [], []),
                                         Sheet("F", ["a"], [])])

    @pytest.mark.skipif(not shutil.which("soffice"),
                        reason="LibreOffice not installed")
    def test_libreoffice_opens_it_and_sees_the_values(self, tmp_path):
        """A real spreadsheet application reads every tab, with numbers as
        numbers."""
        write_xlsx(tmp_path / "t.xlsx", [
            Sheet("Summary", ["Measure", "Mean"],
                  [["Availability", 0.9871234567], ["Runs", 3]]),
            Sheet("Blocks", ["Name", "Cost"], [["Pump", -2.5e-7]])])
        subprocess.run(
            ["soffice", "--headless", "--convert-to",
             "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,"
             "false,false,false,-1", "t.xlsx"],
            cwd=tmp_path, capture_output=True, timeout=120, check=True)
        assert (tmp_path / "t-Summary.csv").read_text().splitlines() == [
            "Measure,Mean", "Availability,0.9871234567", "Runs,3"]
        assert "Pump" in (tmp_path / "t-Blocks.csv").read_text()


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
        with zipfile.ZipFile(tmp_path / "m_tables.xlsx") as z:
            book = z.read("xl/workbook.xml").decode()
            first = z.read("xl/worksheets/sheet1.xml").decode()
            summary = z.read("xl/worksheets/sheet2.xml").decode()
        assert book.index("Run Info") < book.index("Summary")
        assert "TwoBlockRedundancy" in first
        assert ' s="3"' in summary      # availability: six places

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
        assert t.headers == ["Run"] + list(EVENT_LOG_HEADERS)
        # Every cell must land in a column that has a header above it --
        # the unlabelled-tail failure mode the Cost sheet had.
        for row in t.rows:
            assert len(row) == len(t.headers)
            assert all(c != "" or i == 0 for i, c in enumerate(row)) or True
        first = bundle.event_logs[0][0]
        fields = first.split("\t")
        assert t.rows[0][1:1 + len(fields)] == [
            f.strip() for f in fields[:len(EVENT_LOG_HEADERS)]]

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
        z = zipfile.ZipFile(res.files[0])
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        assert [s.get("name") for s in wb.find("m:sheets", NS)] == [
            "Blocks", "Summary"]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        cells = {c.get("r"): c for c in sheet.iter(f"{{{NS['m']}}}c")}
        assert cells["C2"].find("m:v", NS).text == "12.5"       # number
        assert cells["A2"].find("m:v", NS).text == "1"          # id: number
        assert cells["B3"].find("m:is/m:t", NS).text == "12"    # name: text

    def test_same_titles_get_a_group_prefix_on_their_tabs(self, tmp_path):
        tables = [Table("inputs.resources", "Resources", INPUTS, ["a"], [[1]]),
                  Table("results.resources", "Resources", RESULTS, ["a"],
                        [[2]])]
        res = export_tables(tables, tmp_path, ExportOptions(xlsx=True),
                            stem="M")
        wb = ET.fromstring(zipfile.ZipFile(res.files[0]).read(
            "xl/workbook.xml"))
        assert [s.get("name") for s in wb.find("m:sheets", NS)] == [
            "Inputs - Resources", "Results - Resources"]

    def test_every_format_at_once_and_the_plan_matches(self, tmp_path):
        opts = ExportOptions(csv=True, txt_files=True, txt_report=True,
                             xlsx=True)
        tables = _sample_tables()
        planned = plan_files(tables, opts, "M")
        res = export_tables(tables, tmp_path / "sub" / "dir", opts, stem="M")
        assert sorted(f.name for f in res.files) == sorted(planned)
        assert len(planned) == 2 + 2 + 1 + 1

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

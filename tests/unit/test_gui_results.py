"""Headless GUI tests for the results dock's structured and plain-English
views.

Runs the real engine on a small fixture so every number shown is real
engine output, then checks the presentation layers built on top of it:
- Overview metric cards fed by `RunReportData` (not text scraping),
- Final_Results table parsing (stats tables and sparing tables),
- Event-log parsing, filtering, and click-to-select signalling,
- Raw tab preserving the byte-exact report text.
"""
from __future__ import annotations

import os

import pytest
from raptor.gui.ui import filedialogs as _fdg

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QTableWidget

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.results import ResultsDock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests/golden/TwoBlockRedundancy/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def real_results(qapp):
    """Real 2-run simulation through the public entry point."""
    rbd = load_rbd(FIXTURE)
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(rbd, num_runs=2,
                                                 date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    return dock, report_data


def test_report_sink_populated_incrementally_not_batched(qapp):
    """report_sink grows as each run finishes, not all at once after
    `run_simulation_with_reports` returns. A background-run worker
    thread (main_window.py) polls this same list while the call is still
    in progress to drive the live Ao/MTBDE/MDT/G-Y-R readouts."""
    rbd = load_rbd(FIXTURE)
    report_data: list = []
    seen_at_callback: list[int] = []

    def progress(completed, total):
        seen_at_callback.append(len(report_data))

    run_simulation_with_reports(rbd, num_runs=3, date="fixed",
                                report_sink=report_data, progress=progress)
    assert len(report_data) == 3
    # each progress callback sees at least that many entries already in
    # report_data
    assert seen_at_callback == [1, 2, 3], seen_at_callback


def test_report_sink_returns_structured_data(real_results):
    dock, report_data = real_results
    assert len(report_data) == 2
    assert 0.0 < report_data[0].availability < 1.0
    # The Overview shows the same availability as the structured data
    # (derived from it, not re-parsed text) as a full-precision decimal,
    # not a rounded percent.
    card_text = dock._cards["Availability"]._value.text()
    assert card_text.startswith("0.")
    assert float(card_text) == pytest.approx(
        (report_data[0].availability + report_data[1].availability) / 2,
        rel=1e-6)


def test_overview_cards_have_plain_english_tooltips(real_results):
    dock, _ = real_results
    for acronym in ("Availability", "MTBDE", "MDT", "System_Failures"):
        tip = dock._cards[acronym].toolTip()
        assert acronym.split("_")[0].lower() in tip.lower()
        assert len(tip) > 40  # a definition, not just the acronym echoed back


def test_details_parses_stats_and_sparing_tables(qapp):
    lines = [
        "Final_Results_Report for x.rbd  fixed",
        "***SUMMARY***",
        "                         Minimum         Mean            Maximum         St.Dev          SEM",
        "Availability             0.746514202     0.752532575     0.758550947     0.008511264     0.006018372",
        "MTBMs                    N/A             N/A             N/A             N/A             N/A",
        "***LOGISTICS***",
        "                         Minimum         Mean            Maximum         St.Dev          SEM",
        "MTBM                     0.371839        0.373486        0.375133        0.002329        1.647180e-03",
        "***SPARING***",
        "Spares_Utilization",
        "blockName             SpareSource            MinUsed      AveUsed   MaxUsed        StDev",
        "unnamed               Custom                       3         3.00         3         0.00",
    ]
    dock = ResultsDock()
    dock._populate_details(lines)
    # One sub-tab per section, each with its own column headers.
    assert set(dock._detail_tables) == {"SUMMARY", "LOGISTICS", "SPARING"}
    summary = dock.section_table("SUMMARY")
    headers = [summary.horizontalHeaderItem(i).text()
               for i in range(summary.columnCount())]
    assert headers == ["Measure", "Minimum", "Mean", "Maximum", "St. dev.",
                       "SEM"]
    # Numbers are in the table rather than a prose summary: a probability
    # to six places, the full value kept for Copy Table.
    assert summary.item(0, 0).text() == "Availability"
    assert summary.item(0, 1).text() == "0.746514"
    assert summary.item(0, 5).text() == "0.006018"
    from raptor.gui.ui.results import RAW_VALUE_ROLE
    assert summary.item(0, 1).data(RAW_VALUE_ROLE) == 0.746514202
    assert summary.item(0, 0).toolTip().startswith("Operational Availability")

    spare = dock.section_table("SPARING")
    spare_headers = [spare.horizontalHeaderItem(i).text()
                     for i in range(spare.columnCount())]
    # Sparing headers are humanized in the GUI; the report text keeps the
    # raw tokens.
    assert spare_headers == ["Block", "Spare source", "Min used",
                             "Avg used", "Max used", "St. dev."]
    assert spare.item(0, 0).text() == "unnamed"
    assert spare.item(0, 1).text() == "Custom"
    assert spare.item(0, 3).text() == "3.00"


def test_gyr_segments_proportional(qapp):
    from raptor.core.engine.reporting import RunReportData
    from raptor.core.engine.stats import RunningStat

    def mk(run, g, y, r):
        return RunReportData(
            rcond_assigned=False, stats_start_time=0.0,
            run_number=run, ending_sim_time=100.0, availability=(g + y) / 100,
            mtbde=None, mdt=None, reliability=0.0, conditional_reliability=0.0,
            num_system_failures=1, num_component_failures=2, mtbm=None,
            mtbmu=None, mtbms=None, mmt=None, mmtu=None, mmts=None,
            dependability=None, has_phases=False,
            green_percent=g, yellow_percent=y, red_percent=r,
            mission_green_percent=None, mission_yellow_percent=None,
            mission_red_percent=None, sys_fail_stat=RunningStat(),
            red_stat=RunningStat(), comp_fail_stat=RunningStat(),
            comp_repair_stat=RunningStat(), block_spares={},
        )

    dock = ResultsDock()
    # A 3%-green / 0%-yellow / 97%-red model renders as proportional
    # stripes, not three equal ones.
    dock.set_results([[], []], ["eor"], ["fin"],
                     report_data=[mk(1, 3.0, 0.0, 97.0)])
    lay = dock._gyr_lay
    segs = dock._gyr_segments
    stretches = [lay.stretch(lay.indexOf(segs[k]))
                 for k in ("operating", "degraded", "failed")]
    assert stretches[2] > 20 * stretches[0]   # red dwarfs green
    assert not segs["degraded"].isVisible()   # zero segment hidden
    legend = dock._gyr_legend_labels["red"].text()
    assert "97.0%" in legend and "Fully down" in legend


def test_mission_gyr_bar_hidden_without_phasing_shown_with_it(qapp):
    """Ch.17 Fig 17.27 "Phased Logistics Results": a second Green/
    Yellow/Red bar for MISSION time only, meaningful only for a phased
    model with mission tracking. `mission_*_percent` is None otherwise,
    and the whole row stays hidden then."""
    from raptor.core.engine.reporting import RunReportData
    from raptor.core.engine.stats import RunningStat

    def mk(run, g, y, r, mg, my, mr):
        return RunReportData(
            rcond_assigned=False, stats_start_time=0.0,
            run_number=run, ending_sim_time=100.0, availability=(g + y) / 100,
            mtbde=None, mdt=None, reliability=0.0, conditional_reliability=0.0,
            num_system_failures=1, num_component_failures=2, mtbm=None,
            mtbmu=None, mtbms=None, mmt=None, mmtu=None, mmts=None,
            dependability=None, has_phases=mg is not None,
            green_percent=g, yellow_percent=y, red_percent=r,
            mission_green_percent=mg, mission_yellow_percent=my,
            mission_red_percent=mr, sys_fail_stat=RunningStat(),
            red_stat=RunningStat(), comp_fail_stat=RunningStat(),
            comp_repair_stat=RunningStat(), block_spares={},
        )

    dock = ResultsDock()
    dock.set_results([[], []], ["eor"], ["fin"],
                     report_data=[mk(1, 90.0, 5.0, 5.0, None, None, None)])
    assert dock._mission_gyr_container.isHidden()

    dock.set_results([[], []], ["eor"], ["fin"],
                     report_data=[mk(1, 90.0, 5.0, 5.0, 10.0, 0.0, 90.0)])
    assert not dock._mission_gyr_container.isHidden()
    lay = dock._mission_gyr_lay
    segs = dock._mission_gyr_segments
    stretches = [lay.stretch(lay.indexOf(segs[k]))
                 for k in ("operating", "degraded", "failed")]
    assert stretches[2] > 5 * stretches[0]   # mission-red dwarfs mission-green
    assert segs["degraded"].isHidden()  # zero mission-yellow hidden
    legend = dock._mission_gyr_legend_labels["red"].text()
    assert "90.0%" in legend and "Fully down" in legend


def test_event_log_rows_parse_and_signal_names(qapp):
    dock = ResultsDock()
    log_line = ("0.452836          \tBlock    \tunnamed               "
                "\tFailed           \tOperating_time=0.452836")
    node_line = "1.000000\tNode\tjoin\tDegraded\t1-1/2_ur2_df4"
    system_line = "1.000000\tSystem   \tState_change          \tYellow           \t_d4"
    dock.set_results([[log_line, node_line, system_line]], ["eor"], ["fin"])
    model = dock._log_model
    assert model.rowCount() == 3
    assert model.index(0, 3).data() == "Failed"

    clicked: list[tuple[str, str]] = []
    dock.element_clicked.connect(lambda k, n: clicked.append((k, n)))
    for r in range(3):       # System rows aren't clickable
        dock._on_log_row_clicked(model.index(r, 3))
    assert clicked == [("block", "unnamed"), ("node", "join")]


def test_event_log_filter(qapp):
    dock = ResultsDock()
    lines = ["0.0\tBlock\talpha\tGood\tx", "0.1\tBlock\tbeta\tFailed\ty"]
    dock.set_results([lines], ["eor"], ["fin"])
    assert dock._log_model.rowCount() == 2
    dock._filter_edit.setText("failed")
    assert dock._log_model.rowCount() == 1
    assert dock._log_model.index(0, 2).data() == "beta"
    dock._filter_edit.setText("")
    assert dock._log_model.rowCount() == 2


def test_event_log_filter_never_matches_across_columns(qapp):
    """'alpha' is in the Element column and 'Good' in State; neither the
    text 'alpha good' nor 'alphagood' is in any ONE column."""
    dock = ResultsDock()
    dock.set_results([["0.0\tBlock\talpha\tGood\tx"]], ["e"], ["f"])
    for needle in ("alpha good", "alphagood", "alpha\x00good"):
        dock._filter_edit.setText(needle)
        assert dock._log_model.rowCount() == 0, needle


def test_event_log_table_is_what_the_exporters_see(qapp):
    dock = ResultsDock()
    lines = ["0.0\tBlock\talpha\tGood\tx", "0.1\tBlock\tbeta\tFailed\ty"]
    dock.set_results([lines], ["eor"], ["fin"])
    headers, rows = dock.event_log_table()
    assert headers == ["Time", "Type", "Element", "State", "Detail"]
    assert len(rows) == 2
    dock._filter_edit.setText("beta")
    assert [r[2] for r in dock.event_log_table()[1]] == ["beta"]
    assert len(dock.event_log_table(visible_only=False)[1]) == 2


def test_large_event_log_shows_without_freezing_and_filters_fast(qapp):
    """The tab used to build one widget item per line: 100k lines froze the
    UI for ~4 s and every filter keystroke cost 0.3-0.4 s. With a model,
    cost no longer depends on log length.

    **What is actually bounded is the cost PER LINE, and that is what this
    asserts.** The docstring used to say "cost no longer depends on log
    length", and measurement says that is not true: cost is still very
    nearly linear (10x the lines costs ~7x the time). What changed is the
    constant -- about 5.3 us/line now against ~40 us/line before, which is
    the whole of the 4 s freeze.

    So the assertion is a per-line rate, not a wall-clock total. A rate is
    the right shape for two reasons:

    * It is the quantity the regression moved. One widget per line scales
      the *rate*; a model-backed view does not.
    * A ratio between two lengths cannot catch this regression at all --
      both behaviours are linear, so the ratio is ~1 either way. Measured
      here to be sure before relying on it.

    The budget is a coarse guard and its headroom is deliberately stated
    rather than hidden: 5.3 us/line unloaded, 19.7 us/line at 1.5x CPU
    oversubscription (12 spinners on 8 cores), against ~40 us/line for the
    regression. 25 us/line sits between the last two, so it has 4.7x
    headroom unloaded and only 1.3x when loaded. Raising it for comfort
    would put it inside the noise of the bug it exists to catch, and
    lowering it would flake. The `test` CI job runs pytest serially, so
    real contention sits well below the oversubscribed case above.

    The previous absolute (`shown < 1.5` for the full 100k) was really
    asserting that the machine is idle: it passed 3/3 unloaded at 0.95 s
    and failed at 1.71 s under `pytest -n 6` -- a 14% overshoot with no
    code change involved. The filter budget below is left alone: at
    0.007 s unloaded and 0.066 s loaded it has 3.8x headroom against the
    old 0.3-0.4 s, so it never flaked.
    """
    import time
    dock = ResultsDock()
    n = 100_000
    lines = [f"{i * 0.5:.3f}\tBlock\tB{i % 40}\tFailed\tdetail {i}"
             for i in range(n)]
    t = time.perf_counter()
    dock.set_results([lines], ["eor"], ["fin"])
    shown = time.perf_counter() - t
    assert dock._log_model.rowCount() == n

    budget_us_per_line = 25.0
    per_line_us = shown / n * 1e6
    assert per_line_us < budget_us_per_line, (
        f"{n} lines took {shown:.2f}s = {per_line_us:.1f} us/line, over the "
        f"{budget_us_per_line:.0f} us/line budget. The pre-model view cost "
        f"~40 us/line; anything near that is one widget per line again.")

    # Filter passes are model-side; the debounce is only the UI timer.
    t = time.perf_counter()
    dock._log_model.set_filter("B7")
    filtered = time.perf_counter() - t
    assert dock._log_model.rowCount() == n // 40
    assert filtered < 0.25, f"filtering {n} lines took {filtered:.2f}s"


def test_raw_tab_preserves_exact_text(real_results):
    dock, _ = real_results
    raw = dock._raw_views["Final Results"].toPlainText()
    assert "Final_Results_Report for" in raw
    assert "***SUMMARY***" in raw
    eor = dock._raw_views["End of Run"].toPlainText()
    assert "End_Of_Run Report for" in eor


def test_save_raw_report_writes_the_selected_subtab(real_results,
                                                     monkeypatch, tmp_path):
    """Raw tab views can be exported to a file rather than copy/paste
    only."""
    dock, _ = real_results
    idx = dock._raw_tabs.indexOf(dock._raw_views["Final Results"])
    dock._raw_tabs.setCurrentIndex(idx)
    path = str(tmp_path / "final_results.txt")
    monkeypatch.setattr(_fdg, "getSaveFileName",
                        staticmethod(lambda *a, **kw: (path, "")))
    dock._save_raw_report()
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        saved = f.read()
    assert saved == dock._raw_views["Final Results"].toPlainText()
    assert "Final_Results_Report for" in saved


def test_save_graph_image_writes_a_png(real_results, monkeypatch, tmp_path):
    """The Graphs tab (Ao/Capacity plots) exports as a PNG image."""
    dock, _ = real_results
    dock.resize(900, 600)
    dock.show()
    QApplication.instance().processEvents()
    path = str(tmp_path / "graph.png")
    monkeypatch.setattr(_fdg, "getSaveFileName",
                        staticmethod(lambda *a, **kw: (path, "")))
    dock._save_graph_image()
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0


def test_units_appended_to_time_quantities(real_results):
    dock, _ = real_results
    dock.set_results(dock._event_logs, ["eor"], ["fin"],
                     report_data=dock._report_data, units="h")
    assert dock._cards["MTBDE"]._value.text().endswith(" h")
    assert "h" in dock._summary_sentence.text()
    # The run-length phrase separates number and unit ("730.5 h", never
    # "730.5h" -- it once used the bare unit instead of `time_u`).
    import re
    assert re.search(r"simulated [\d.,]+ h\.", dock._summary_sentence.text())
    # Percentages and counts do not carry the unit.
    assert not dock._cards["Availability"]._value.text().endswith("h")
    assert not dock._cards["System_Failures"]._value.text().endswith("h")


def test_overview_cards_show_min_max_range(qapp):
    data = []
    from raptor.core.engine.reporting import RunReportData
    from raptor.core.engine.stats import RunningStat

    def mk(run, avail):
        return RunReportData(
            rcond_assigned=False, stats_start_time=0.0,
            run_number=run, ending_sim_time=100.0, availability=avail,
            mtbde=10.0 + run, mdt=1.0, reliability=0.0,
            conditional_reliability=0.0, num_system_failures=run,
            num_component_failures=2 * run, mtbm=None, mtbmu=None, mtbms=None,
            mmt=None, mmtu=None, mmts=None, dependability=None, has_phases=False,
            green_percent=50.0, yellow_percent=30.0, red_percent=20.0,
            mission_green_percent=None, mission_yellow_percent=None,
            mission_red_percent=None, sys_fail_stat=RunningStat(),
            red_stat=RunningStat(), comp_fail_stat=RunningStat(),
            comp_repair_stat=RunningStat(), block_spares={},
        )

    data = [mk(1, 0.70), mk(2, 0.80)]
    dock = ResultsDock()
    dock.set_results([[], []], ["eor"], ["fin"], report_data=data)
    card = dock._cards["Availability"]
    assert float(card._value.text()) == pytest.approx(0.75)
    assert "min 0.7" in card._range.text() and "max 0.8" in card._range.text()
    # Legend names each colour's meaning with its percentage.
    legend = [dock._gyr_legend_labels[k].text() for k in ("green", "yellow", "red")]
    assert any("Fully working" in t and "50.0%" in t for t in legend)
    assert any("Degraded" in t and "30.0%" in t for t in legend)
    assert any("Fully down" in t and "20.0%" in t for t in legend)


def test_sections_render_differently(qapp):
    lines = [
        "***SUMMARY***",
        "                         Minimum         Mean            Maximum         St.Dev          SEM",
        "MTBDE                    10.0            12.0            14.0            1.0             0.5",
        "***LOGISTICS***",
        "                         Minimum         Mean            Maximum         St.Dev          SEM",
        "MMT                      1.0             2.0             3.0             0.4             0.2",
        "***SPARING***",
        "Spares_Utilization",
        "blockName             SpareSource            MinUsed      AveUsed   MaxUsed        StDev",
        "pump                  Custom                       3         3.00         3         0.00",
    ]
    dock = ResultsDock()
    dock._populate_details(lines)
    # SUMMARY: numeric grid only (the plain-English paragraph was removed
    # as too lengthy and repetitive of the table).
    summary_table = dock.section_table("SUMMARY")
    assert summary_table.item(0, 1).text() == "10"
    notes = [w for w in dock._details_tabs.widget(0).findChildren(QLabel)
             if w.objectName() == "PlainEnglishNote"]
    assert not notes
    # LOGISTICS: pure grid.
    logistics = dock.section_table("LOGISTICS")
    assert logistics.item(0, 0).text() == "MMT" and logistics.item(0, 1).text() == "1"
    # SPARING: its own table with humanized headers.
    spare = dock.section_table("SPARING")
    assert [spare.horizontalHeaderItem(i).text() for i in range(2)] == \
        ["Block", "Spare source"]
    assert spare.item(0, 0).text() == "pump"


# ---------------------------------------------------------------------
# Units on failure/repair time measures
# ---------------------------------------------------------------------

_UNITS_LINES = [
    "***SUMMARY***",
    "                         Minimum         Mean            Maximum         St.Dev          SEM",
    "MTBDE                    10.0            12.0            14.0            1.0             0.5",
    "Availability              0.8             0.9             0.95           0.04            0.01",
    "***LOGISTICS***",
    "                         Minimum         Mean            Maximum         St.Dev          SEM",
    "MTBM                     20.0            24.0            28.0            3.0             1.5",
    "MMT                       1.0             2.0             3.0             0.4             0.2",
    "Green_Percent             0.7             0.8             0.9             0.08            0.02",
]


def test_time_measures_carry_units(qapp):
    dock = ResultsDock()
    dock.set_results([["x"]], _UNITS_LINES, _UNITS_LINES,
                     report_data=[], units="Hours")
    summary = dock.section_table("SUMMARY")
    assert summary.item(0, 0).text() == "MTBDE (Hours)"
    # Dimensionless measures stay bare.
    assert summary.item(1, 0).text() == "Availability"
    logistics = dock.section_table("LOGISTICS")
    assert logistics.item(0, 0).text() == "MTBM (Hours)"
    assert logistics.item(1, 0).text() == "MMT (Hours)"
    assert logistics.item(2, 0).text() == "Green %"


def test_no_units_when_units_empty(qapp):
    dock = ResultsDock()
    dock._units = ""
    dock._populate_details(_UNITS_LINES)
    assert dock.section_table("SUMMARY").item(0, 0).text() == "MTBDE"


# ---------------------------------------------------------------------
# Hierarchy level filter for Sparing/Capacity rows
# ---------------------------------------------------------------------

_SPARING_LINES = [
    "***SPARING***",
    "Spares_Utilization",
    "blockName             SpareSource            MinUsed      AveUsed   MaxUsed        StDev",
    "alpha                  Custom                       3         3.00         3         0.00",
    "beta                   Custom                       1         1.00         1         0.00",
    "gamma                  Custom                       2         2.00         2         0.00",
]


def test_level_filter_hides_sparing_rows(qapp):
    dock = ResultsDock()
    dock._populate_details(_SPARING_LINES)
    spare = dock.section_table("SPARING")

    def visible():
        return {spare.item(r, 0).text() for r in range(spare.rowCount())
                if not spare.isRowHidden(r)}

    assert visible() == {"alpha", "beta", "gamma"}
    dock.set_level_filter({"alpha", "beta"})   # inside the drilled level
    assert visible() == {"alpha", "beta"}
    dock.set_level_filter(None)                # home: everything back
    assert visible() == {"alpha", "beta", "gamma"}


def test_level_filter_survives_new_results(qapp):
    """Drill in FIRST, then run: re-population must re-apply the filter."""
    dock = ResultsDock()
    dock.set_level_filter({"alpha"})
    dock.set_results([["x"]], _SPARING_LINES, _SPARING_LINES,
                     report_data=[])
    spare = dock.section_table("SPARING")
    visible = [spare.item(r, 0).text() for r in range(spare.rowCount())
               if not spare.isRowHidden(r)]
    assert visible == ["alpha"]


# ---------------------------------------------------------------------
# Cost analysis view (manual Ch.12)
# ---------------------------------------------------------------------

def _run_with_cost():
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbd import load_rbd
    fixture = os.path.join(REPO, "tests/golden/Capacity1/input.rbd")
    rbd = load_rbd(str(fixture))
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(rbd, num_runs=2, date="fixed",
                                                 report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    return dock


def _cost_page(dock):
    for i in range(dock._details_tabs.count()):
        if dock._details_tabs.tabText(i) == "Cost":
            return dock._details_tabs.widget(i)
    return None


def test_cost_tab_present_and_populated(qapp):
    dock = _run_with_cost()
    page = _cost_page(dock)
    assert page is not None
    tables = [t for t in page.findChildren(QTableWidget) if t.rowCount() > 0]
    assert tables, "cost-enabled model must produce cost tables"


def test_cost_resource_pool_relabels_columns(qapp):
    """Resource pools swap NewSpares/EmerShip -> PerTime/PerUse
    (`PoolCostRow`'s own docstring)."""
    dock = _run_with_cost()
    page = _cost_page(dock)
    pool_tables = [t for t in page.findChildren(QTableWidget)
                   if t.columnCount() == 5 and t.rowCount() > 0
                   and "[Resource]" in (t.item(0, 0).text()
                                        if t.item(0, 0) else "")]
    assert pool_tables, "Capacity1 has an Astronauts resource pool"
    names = {pool_tables[0].item(r, 0).text()
             for r in range(pool_tables[0].rowCount())}
    assert any("[Resource]" in n for n in names)


def test_cost_totals_are_consistent(qapp):
    """Each block row: Purchase + Operating + Sparing + Disposal == Total."""
    dock = _run_with_cost()
    page = _cost_page(dock)
    blocks_table = next(t for t in page.findChildren(QTableWidget)
                        if t.columnCount() == 6 and t.rowCount() > 0)
    for r in range(blocks_table.rowCount()):
        vals = [float(blocks_table.item(r, c).text().replace(",", ""))
                for c in range(1, 6)]
        assert vals[0] + vals[1] + vals[2] + vals[3] == \
            pytest.approx(vals[4], rel=1e-6), blocks_table.item(r, 0).text()


def test_cost_tab_shows_note_when_disabled(qapp):
    lines = ["***SUMMARY***", ""]
    dock = ResultsDock()
    dock.set_results([["x"]], lines, lines, report_data=[])
    assert _cost_page(dock) is not None  # placeholder note page still exists


# ---------------------------------------------------------------------
# Weak Link analysis view (manual Ch.11)
# ---------------------------------------------------------------------

def test_weak_link_tab_from_enabled_model(qapp):
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbd import load_rbd
    fixture = os.path.join(REPO, "tests/golden/Capacity1/input.rbd")
    rbd = load_rbd(fixture)
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    assert all(d.weak_stats for d in report_data), \
        "weakAnalysis=1 must populate per-block state times"
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    idx = next((i for i in range(dock._details_tabs.count())
                if dock._details_tabs.tabText(i) == "Weak Link"), None)
    assert idx is not None
    table = dock._details_tabs.widget(idx).findChildren(QTableWidget)[0]
    # Ch.11 layout: Name | Colour chip | metric + stats.
    headers = [table.horizontalHeaderItem(c).text()
               for c in range(table.columnCount())]
    assert headers[:3] == ["Name", "Colour", "Availability"], headers
    assert "Min" in headers and "StdDev" in headers  # Fig 11.6 stats
    for r in range(table.rowCount()):
        band = table.item(r, 1).text()
        assert band in ("Green", "Yellow", "Red"), band
        val = float(table.item(r, 2).text())
        assert 0.0 <= val <= 1.0
        if band == "Green":
            assert val >= 0.95
        elif band == "Red":
            assert val < 0.90


def test_weak_link_table_keeps_stats_columns_for_one_run(qapp):
    """Raptor's Output Tables keep Min/Max/StdDev/SEM even at n=1;
    StdDev/SEM are blank rather than the columns disappearing."""
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Capacity1/input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=1, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    idx = next(i for i in range(dock._details_tabs.count())
               if dock._details_tabs.tabText(i) == "Weak Link")
    table = dock._details_tabs.widget(idx).findChildren(QTableWidget)[0]
    headers = [table.horizontalHeaderItem(c).text()
               for c in range(table.columnCount())]
    assert headers[3:] == ["Min", "Max", "StdDev", "SEM"]
    assert table.rowCount() >= 1
    assert table.item(0, 3).text() != ""
    assert table.item(0, 4).text() != ""
    assert table.item(0, 5).text() == ""     # StdDev undefined at n=1
    assert table.item(0, 6).text() == ""     # SEM undefined at n=1


def test_capacity_table_shows_envelope_and_run_average_extremes(qapp):
    """Both views are needed: the worst/best-run envelope for planning,
    and Raptor's Final Results means of each run's own min/max."""
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Capacity2/input.rbd"))
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=3, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    page = dock._build_capacity_page()
    table = page.findChildren(QTableWidget)[0]
    headers = [table.horizontalHeaderItem(c).text()
               for c in range(table.columnCount())]
    assert headers == [
        "Node",
        "Min Flow (worst)", "Min Flow (avg runs)", "Ave Flow",
        "Max Flow (avg runs)", "Max Flow (best)", "Usage",
        "Min Cap (worst)", "Min Cap (avg runs)", "Ave Cap",
        "Max Cap (avg runs)", "Max Cap (best)", "Capability"]

    caps = [d.capacity for d in report_data if d.capacity is not None]
    sys_nid = caps[0].system_node_id
    per_run = [cap.nodes[sys_nid] for cap in caps]
    mins = [s.min_flow for s in per_run]
    maxs = [s.max_flow for s in per_run]
    assert table.item(0, 1).text() == f"{int(min(mins)):,}"
    assert table.item(0, 2).text() == f"{sum(mins) / len(mins):,.2f}"
    assert table.item(0, 4).text() == f"{sum(maxs) / len(maxs):,.2f}"
    assert table.item(0, 5).text() == f"{int(max(maxs)):,}"


def test_weak_link_tab_shows_note_when_disabled(qapp):
    """The Weak Link tab exists even when the analysis is off, saying how
    to turn it on rather than vanishing without explanation."""
    lines = ["***SUMMARY***", ""]
    dock = ResultsDock()
    dock._populate_details(lines)
    idx = next((i for i in range(dock._details_tabs.count())
                if dock._details_tabs.tabText(i) == "Weak Link"), None)
    assert idx is not None
    page = dock._details_tabs.widget(idx)
    assert page.findChildren(QTableWidget) == []


# ---------------------------------------------------------------------
# Appendix C "Files and Reports" exports (Key Parameters, System Failure
# and Down Times, Ending Simulation Times, Availability Plot Data).
# ---------------------------------------------------------------------

def test_export_appendix_c_no_run_shows_message_not_crash(qapp):
    from PySide6.QtWidgets import QMessageBox
    dock = ResultsDock()
    seen = []
    QMessageBox.information = staticmethod(
        lambda *a, **k: seen.append(1))
    dock._export_appendix_c_files()
    assert seen == [1]


def test_export_appendix_c_dialog_is_non_modal(real_results):
    """The export dialog is non-modal (`show()`, not `exec()`), so it does
    not hang headless test automation."""
    dock, _ = real_results
    dock._export_appendix_c_files()
    dlg = dock._appendix_c_dialog
    assert dlg.isVisible()
    assert not dlg.isModal()
    dlg.reject()
    assert not dlg.isVisible()


def test_export_appendix_c_data_files_use_the_engine_writers(
        real_results, monkeypatch, tmp_path):
    from raptor.core.engine import data_files
    dock, report_data = real_results
    monkeypatch.setattr(
        _fdg, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(tmp_path)))
    dock._export_appendix_c_files()
    assert dock._appendix_c_dialog.isVisible()
    dock._appendix_c_dialog.accept()   # non-modal: accept() drives the export

    for kind in ("key_parameters", "failure_times", "down_times", "end_times"):
        path = tmp_path / f"rpt_{kind}.txt"
        assert path.exists(), kind
        assert path.read_bytes().decode("latin-1") == data_files.legacy_text(
            data_files.DATA_FILES[kind](report_data)), kind
    # Availability Plot Data is absent: TwoBlockRedundancy is
    # FailSim-terminated, so this run has no _ao_lines.
    assert not (tmp_path / "rpt_availability_plot.txt").exists()
    key_lines = (tmp_path / "rpt_key_parameters.txt").read_text().splitlines()
    assert len(key_lines) == len(report_data) + 1       # header + one row per trial


def test_export_appendix_c_availability_plot_data_when_present(
        qapp, monkeypatch, tmp_path):
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Phasing1/input.rbd"))
    report_data: list = []
    ao_lines: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data,
        ao_sink=ao_lines)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data,
                     ao_lines=ao_lines)
    assert ao_lines   # this fixture is time-truncated

    monkeypatch.setattr(
        _fdg, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(tmp_path)))
    dock._export_appendix_c_files()
    dock._appendix_c_dialog.accept()   # non-modal: accept() drives the export

    ao_path = tmp_path / "rpt_availability_plot.txt"
    assert ao_path.exists()
    text = ao_path.read_text()
    assert text.splitlines()[0].startswith("SimulationTime")
    non_blank = [ln for ln in text.splitlines() if ln]
    assert non_blank == [ln for ln in ao_lines if ln]


def test_y_axis_minimum_is_the_models_and_changes_the_graph_scale(qapp):
    from PySide6.QtCore import QRectF
    from raptor.gui.ui.plots import AoPlotWidget
    plot = AoPlotWidget()
    area = QRectF(0, 0, 100, 100)
    assert plot._y_map(area, 1.0) == 0.0 and plot._y_map(area, 0.0) == 100.0
    plot.set_y_min(0.5)
    assert plot.y_min == 0.5
    assert plot._y_map(area, 0.75) == 50.0            # halfway up the 0.5..1.0 range
    assert plot._y_map(area, 0.2) == 100.0            # below the axis: clamped to the floor
    plot.set_y_min(5.0)
    assert plot.y_min == 0.999999                     # the original's own ceiling


def test_y_axis_minimum_round_trips_through_the_model_without_a_spurious_dirty_flag(qapp):
    import os
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    repo = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    rbd = load_rbd(os.path.join(repo, "tests", "golden", "MotorDemo", "input.rbd"))
    before = dict(rbd.section("SYSTEM_DATA").rows[0])
    w = MainWindow()
    w._load_into_app(rbd, "x")
    assert not w._dirty
    assert w._results._ao_plot.y_min == float(before["graphYmin"])
    w._results.y_axis_min_changed.emit(0.25)
    assert w._rbd.section("SYSTEM_DATA").rows[0]["graphYmin"] == "0.250000" and w._dirty


def test_event_log_more_placeholder_keeps_the_current_run(qapp):
    dock = ResultsDock()
    dock.show_live_logs([["Begin_Run_1", "0.000000 Type Element Good"]], 3)
    assert dock._run_selector.count() == 2
    dock._run_selector.setCurrentIndex(1)    # "…2 more"
    qapp.processEvents()
    assert dock._run_selector.currentIndex() == 0
    assert dock._log_model.rowCount() > 0


def test_weak_tables_expand_to_all_rows(qapp):
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Capacity1/input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    idx = next(i for i in range(dock._details_tabs.count())
               if dock._details_tabs.tabText(i) == "Weak Link")
    blocks = dock._details_tabs.widget(idx).findChildren(QTableWidget)[0]
    assert blocks.rowCount() > 8
    assert blocks.minimumHeight() >= blocks.rowCount() * 20


def test_y_axis_min_is_disabled_for_capacity_plots(qapp):
    dock = ResultsDock()
    dock._graph_mode.setCurrentIndex(3)   # Capacity (Flow)
    assert dock._y_axis_btn.isEnabled() is False
    dock._graph_mode.setCurrentIndex(0)
    assert dock._y_axis_btn.isEnabled() is True


def test_capacity_matches_raptor_for_full_flow_nodes(qapp):
    """Full-flow boundary nodes print N/A for the capacity columns, while
    the SystemCapacity row keeps the Start node's real capability."""
    from PySide6.QtWidgets import QLabel

    rbd = load_rbd(os.path.join(REPO, "tests/golden/Capacity2/input.rbd"))
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=3, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    page = dock._build_capacity_page()
    table = page.findChildren(QTableWidget)[0]
    names = [table.item(r, 0).text() for r in range(table.rowCount())]
    sys_row = names.index("SystemCapacity")
    assert table.item(sys_row, 12).text() != "N/A"
    for name in ("end", "n1"):
        row = names.index(name)
        # Usage + all five capacity cells + capability are N/A.
        for col in (6, 7, 8, 9, 10, 11, 12):
            assert table.item(row, col).text() == "N/A", (name, col)
    head = next(lb.text() for lb in page.findChildren(QLabel)
                if lb.text().startswith("Flow generated"))
    assert "hours" not in head


def test_weak_page_rebuilds_when_metric_or_bands_change(qapp):
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Capacity1/input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    dock.set_weak_settings("Availability", 0.95, 0.90)
    idx = next(i for i in range(dock._details_tabs.count())
               if dock._details_tabs.tabText(i) == "Weak Link")
    table = dock._details_tabs.widget(idx).findChildren(QTableWidget)[0]
    assert table.horizontalHeaderItem(2).text() == "Availability"
    first = table.item(0, 2).text()
    dock.set_weak_settings("Dependability", 0.80, 0.50)
    idx = next(i for i in range(dock._details_tabs.count())
               if dock._details_tabs.tabText(i) == "Weak Link")
    table = dock._details_tabs.widget(idx).findChildren(QTableWidget)[0]
    assert table.horizontalHeaderItem(2).text() == "Dependability"
    assert table.item(0, 2).text() != first


def test_export_csv_includes_capacity_cost_and_weak(qapp, tmp_path,
                                                    monkeypatch):
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Capacity1/input.rbd"))
    row = rbd.sections["SYSTEM_DATA"].rows[0]
    row["weakAnalysis"] = "1"
    row["costAnalysis"] = "1"
    row["capacityAnalysis"] = "1"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    target = tmp_path / "results.csv"
    monkeypatch.setattr(
        _fdg, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "CSV (*.csv)")))
    dock._export_csv()
    text = target.read_text(encoding="utf-8")
    assert "# Capacity" in text
    assert "# Cost" in text
    assert "# Weak Link" in text


def test_cost_tables_keep_raw_values_for_the_top_copy(qapp):
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Capacity1/input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["costAnalysis"] = "1"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    from raptor.gui.ui.results import RAW_VALUE_ROLE
    blocks = dock._cost_tables[0]
    assert blocks.item(0, 1).data(RAW_VALUE_ROLE) is not None


def test_every_cost_table_has_a_title_copy_and_csv(qapp):
    """The report-section cost tables (Operating/Sparing/Cost summary)
    were added without the Copy/CSV actions the life-cycle tables had."""
    from PySide6.QtWidgets import QPushButton
    from raptor.core.fileio.rbds import load_rbds

    rbd = load_rbds(os.path.join(REPO, "new_docs", "models", "Water2.rbds"))
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=1, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    page = dock._details_tabs.widget(next(
        i for i in range(dock._details_tabs.count())
        if dock._details_tabs.tabText(i) == "Cost"))
    scroll = page.widget() if hasattr(page, "widget") else page
    titles = [lb.text() for lb in scroll.findChildren(QLabel)
              if lb.objectName() == "TableTitle"]
    buttons = [b for b in scroll.findChildren(QPushButton)
               if b.text() in ("Copy", "CSV…")]
    assert dock._cost_extra, "Water2 should exercise the extra cost tables"
    assert len(titles) == len(dock._cost_tables)
    assert len(buttons) == 2 * len(dock._cost_tables)
    assert any("Operating" in t for t in titles)
    assert any("Sparing" in t for t in titles)


def test_capacity_tab_flow_loss_is_the_final_results_row(qapp):
    """The Capacity tab's flow-loss line carries the Final Results `FlowLoss` row's three numbers
    (min, average, max), not only an average."""
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Capacity2/input.rbd"))
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(rbd, num_runs=3, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    page = dock._build_capacity_page()
    text = next(lb.text() for lb in page.findChildren(QLabel) if lb.text().startswith("Flow generated"))
    row = fin[fin.index("FlowLoss              MinLostFlow  AveLostFlow  MaxLostFlow") + 1].split()
    shown = text.split("Flow loss (min · avg · max): ")[1].split(" · ")
    assert [f"{float(x):,.2f}" for x in row] == shown

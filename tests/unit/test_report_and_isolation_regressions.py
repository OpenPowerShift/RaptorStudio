"""Regression tests for the report, isolation and results review round.

Covers the MCP report/semantics fixes and the matching GUI readouts:
run slicing for end-marker sinks, isolation metadata, censored metrics,
.rbd export loss reporting, sparing flags/ranges, timeout/date guards,
empty capacity reports, the Summary hierarchy filter, N/A cards, the
terminated-run header, censor-safe Copy/CSV, the Event Log empty state
and stepped-session readiness.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"
HOSPITAL = REPO / "new_docs" / "models" / "HospitalPower.rbds"


# ---------------------------------------------------------------------------
# MCP harness
# ---------------------------------------------------------------------------

def _use_workspace(tmp_path: Path) -> None:
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None


def _error(result) -> dict:
    assert result.is_error, result.content
    return result.structured_content["error"]


@pytest.fixture
def hospital(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import open_model
    r = open_model("HospitalPower.rbds")
    assert not r.is_error, r.content
    return r.structured_content["model_id"]


@pytest.fixture
def motor(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import open_model
    r = open_model("MotorDemo")
    assert not r.is_error, r.content
    return r.structured_content["model_id"]


def _simulate(model_id: str, **kwargs):
    from raptor.mcp.tools_run import simulate
    return asyncio.run(simulate(model_id, confirm_long_run=True, **kwargs))


def test_report_run_number_slices_end_marker_sinks(hospital):
    from raptor.mcp.tools_run import get_report

    run = _simulate(hospital, num_runs=3, seed=7)
    assert not run.is_error, run.content
    run_id = run.structured_content["run_id"]

    first = get_report(run_id, section="availability_plot", run_number=1,
                       limit=200)
    last = get_report(run_id, section="availability_plot", run_number=3,
                      limit=200)
    assert not first.is_error and not last.is_error
    # A page may stop before the run's end marker; read the tail page.
    for report, marker in ((first, "End_of_Run_1"),
                           (last, "End_of_Run_3")):
        total = report.structured_content["total"]
        tail = get_report(run_id, section="availability_plot",
                          run_number=1 if marker.endswith("1") else 3,
                          offset=max(0, total - 3), limit=3)
        assert marker in "\n".join(tail.structured_content["lines"])
    assert first.structured_content["lines"][0].startswith("SimulationTime")
    assert not any(line.startswith("SimulationTime")
                   for line in last.structured_content["lines"])
    assert last.structured_content["total"] > 1

    cost = get_report(run_id, section="cost", run_number=1, limit=200)
    assert not cost.is_error
    assert "End_of_Run_2" not in "\n".join(cost.structured_content["lines"])


def test_isolation_metadata_reaches_tables_and_exports(hospital):
    from raptor.mcp.tools_model import describe_model
    from raptor.mcp.tools_run import get_report
    from raptor.mcp.tools_tables import export_tables, get_table

    described = describe_model(hospital, detail="full")
    selector = described.structured_content["hierarchies"][0]["selector"]
    run = _simulate(hospital, num_runs=1, hierarchy=selector)
    assert not run.is_error, run.content
    run_id = run.structured_content["run_id"]
    assert run.structured_content.get("isolation")

    table = get_table(hospital, "results.summary", run_id=run_id)
    exported = export_tables(hospital, run_id=run_id, formats=["csv"],
                             tables=["results.summary"])
    report = get_report(run_id, section="final_results", limit=1)
    for result in (table, exported, report):
        assert not result.is_error, result.content
        assert result.structured_content.get("isolation")


def test_censored_metrics_are_flagged(hospital):
    run = _simulate(hospital, num_runs=3)
    assert not run.is_error, run.content
    summary = run.structured_content["summary"]
    assert summary["MTBDE"]["censored"] is True
    assert summary["MTBDE"]["censored_at"] == pytest.approx(8760.0)

    from raptor.mcp.tools_tables import get_table
    per_run = get_table(hospital, "results.per_run",
                        run_id=run.structured_content["run_id"])
    headers = per_run.structured_content["headers"]
    assert "mtbde_censored" in headers
    row = per_run.structured_content["rows"][0]
    assert row[headers.index("mtbde_censored")] in (True, "True", 1)


def test_rbd_export_reports_what_it_dropped(hospital):
    from raptor.mcp.tools_model import save_model_as

    saved = save_model_as(hospital, "lossy", format="rbd")
    assert not saved.is_error, saved.content
    assert "rate definitions" in saved.structured_content["dropped"]


def test_sparing_flags_ranges_and_levels(motor):
    from raptor.mcp.tools_model import configure_sparing, get_element

    result = configure_sparing(
        motor, "Armature", mode="custom", stock=3, restock_every=100.0,
        emergency=12.0, slo=5, quantity=2, slo_time=24.0)
    assert not result.is_error, result.content
    sparing = get_element(motor, "Armature").structured_content["sparing"]
    assert sparing["rso"] == 1 and sparing["eso"] == 1
    assert sparing["slo"] == 1          # the 0/1 enable flag
    assert sparing["level"] == 5        # the reorder point
    assert sparing["quan"] == 2
    assert sparing["slo_time"] == 24.0

    for bad in ({"restock_every": -5}, {"emergency": -7},
                {"stock": -1}, {"quantity": -2}):
        err = _error(configure_sparing(motor, "Armature", **bad))
        assert err["code"] == "invalid_argument"


def test_simulate_rejects_bad_timeout_and_date(motor):
    for kwargs in ({"timeout_s": 0}, {"timeout_s": -3},
                   {"date": "banana"}):
        err = _error(_simulate(motor, num_runs=1, **kwargs))
        assert err["code"] == "invalid_argument"


def test_empty_capacity_is_a_precondition_error(motor):
    from raptor.mcp.tools_run import get_report, get_timeseries

    run = _simulate(motor, num_runs=2)
    run_id = run.structured_content["run_id"]
    for result in (get_report(run_id, section="capacity"),
                   get_timeseries(run_id, series="capacity")):
        err = _error(result)
        assert err["code"] == "precondition_failed"
        assert "Capacity" in err["message"]


# ---------------------------------------------------------------------------
# GUI harness
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def gui_run(qapp):
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbds import load_rbds
    rows: list = []
    logs, eor, fin = run_simulation_with_reports(
        load_rbds(str(HOSPITAL)), num_runs=1, date="fixed",
        report_sink=rows)
    return logs, eor, fin, rows


@pytest.fixture
def dock(qapp, gui_run):
    from raptor.gui.ui.results import ResultsDock
    logs, eor, fin, rows = gui_run
    result = ResultsDock()
    result.set_results(list(logs), eor, fin, report_data=rows,
                       units="hours")
    result.set_hierarchy_options({100: "Generator_Bank"}, 0)
    return result


def test_summary_hides_the_hierarchy_selector(dock, qapp):
    idx = next(i for i in range(dock._details_tabs.count())
               if dock._details_tabs.tabText(i) == "Summary")
    dock._details_tabs.setCurrentIndex(idx)
    qapp.processEvents()
    assert dock._hier_filter_combo.isHidden()
    assert dock._hier_filter_label.isHidden()


def test_na_metric_cards_have_no_dangling_unit(dock):
    assert dock._cards["MDT"]._value.text() == "N/A"
    assert "hours" not in dock._cards["MDT"]._value.text()


def test_final_results_header_uses_the_terminated_time(dock, qapp, gui_run):
    from PySide6.QtWidgets import QLabel
    from raptor.gui.ui.results import ResultsDock

    _logs, _eor, fin, rows = gui_run
    early = replace(rows[0], ending_sim_time=730.0)
    result = ResultsDock()
    result.set_results([], [], fin, report_data=[early], units="hours")
    qapp.processEvents()
    titles = [label.text() for label in result.findChildren(QLabel)
              if label.objectName() == "SectionTitle"]
    assert any("730 hours each" in text for text in titles)


def test_copy_keeps_censored_values_numeric(dock):
    from raptor.gui.ui.table_export import table_to_grid

    table = dock.section_table("SUMMARY")
    headers, rows = table_to_grid(table)
    assert any(header.endswith("(censored)") for header in headers)
    row = next(r for r in rows if r[0].startswith("MTBDE"))
    values = dict(zip(headers, row))
    assert values["Mean (censored)"] == ">"
    assert values["Mean"] != ">"              # still a usable number
    assert float(values["Mean"]) == pytest.approx(8760.0)


def test_event_log_explains_a_filter_that_matches_nothing(qapp, gui_run):
    from raptor.gui.ui.results import ResultsDock

    logs, eor, fin, rows = gui_run
    result = ResultsDock()
    result._filter_edit.setText("ZZZ-no-such-event")
    result.set_results(list(logs), eor, fin, report_data=rows,
                       units="hours")
    qapp.processEvents()
    assert not result._log_empty_label.isHidden()
    assert "No events match" in result._log_empty_label.text()
    result._filter_edit.setText("")
    qapp.processEvents()
    assert result._log_empty_label.isHidden()


def test_stepped_session_is_not_ready_to_run(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    window = MainWindow()
    window._load_into_app(load_rbds(str(HOSPITAL)), str(HOSPITAL))
    window.show()
    qapp.processEvents()
    try:
        window._ensure_step_session()
        window._update_readiness()
        qapp.processEvents()
        text = window._run_panel._readiness.text()
        assert "Step session in progress" in text
        assert not text.startswith("Ready")
    finally:
        window._dirty = False
        window.close()


# ---------------------------------------------------------------------------
# Round nine
# ---------------------------------------------------------------------------

def test_deleting_a_non_empty_hierarchy_is_refused(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import (
        add_block, add_hierarchy, create_model, delete_element)

    mid = create_model("hier_delete", template="series", n=2) \
        .structured_content["model_id"]
    hier = add_hierarchy(mid, "Sub").structured_content
    add_block(mid, "Inner", parent=hier["hier_id"])
    err = _error(delete_element(mid, f"Hier:{hier['hier_id']}"))
    assert "still contains" in err["message"]


def test_isolated_run_validates_the_extract_not_the_source(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import (
        add_block, add_hierarchy, connect, create_model)

    mid = create_model("iso_source", template="series", n=2) \
        .structured_content["model_id"]
    hier = add_hierarchy(mid, "Sub").structured_content
    add_block(mid, "Inner", parent=hier["hier_id"])
    connect(mid, ["Node", hier["in_id"]], "Inner")
    connect(mid, "Inner", ["Node", hier["out_id"]])
    # The parent-level graph is deliberately incomplete; only the extract
    # is used.
    run = _simulate(mid, num_runs=1, hierarchy=hier["hier_id"])
    assert not run.is_error, run.content
    assert run.structured_content["isolation"]


def test_isolated_input_tables_describe_the_extract(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import (
        add_block, add_hierarchy, connect, create_model)
    from raptor.mcp.tools_tables import get_table

    mid = create_model("iso_inputs", template="series", n=3) \
        .structured_content["model_id"]
    hier = add_hierarchy(mid, "Sub").structured_content
    add_block(mid, "Inner", parent=hier["hier_id"])
    connect(mid, ["Node", hier["in_id"]], "Inner")
    connect(mid, "Inner", ["Node", hier["out_id"]])
    run = _simulate(mid, num_runs=1, hierarchy=hier["hier_id"])
    assert not run.is_error, run.content
    table = get_table(mid, "inputs.blocks",
                      run_id=run.structured_content["run_id"])
    names = {row[1] for row in table.structured_content["rows"]}
    assert names == {"Inner"}


def test_simulate_honours_model_recording_intervals(tmp_path):
    _use_workspace(tmp_path)
    from raptor.core import run_experiment
    from raptor.mcp import context
    from raptor.mcp.tools_model import create_model, configure_system
    from raptor.mcp.tools_run import get_report

    mid = create_model("intervals", template="series", n=1) \
        .structured_content["model_id"]
    configure_system(mid, run_length=1000.0, cost=True)
    ws = context.workspace()
    handle = ws.get(mid)
    handle.rbd.experiment = run_experiment.normalise({
        "analyses": ["cost"],
        "recording": {"ao_plot": True, "ao_interval": 2.0,
                      "cost_plot": True, "cost_interval": 5.0},
        "term_condition": 1000.0,
    })
    ws.save(mid)
    run = _simulate(mid, num_runs=1)
    assert not run.is_error, run.content
    rid = run.structured_content["run_id"]

    ao = get_report(rid, section="availability_plot", limit=20)
    samples = [line.split() for line in ao.structured_content["lines"]
               if line[:1].isdigit()]
    times = [float(row[0]) for row in samples]
    assert times[:3] == [pytest.approx(2.0), pytest.approx(4.0),
                         pytest.approx(6.0)]

    cost = get_report(rid, section="cost", limit=20)
    assert not cost.is_error, cost.content
    stamps = [float(line.split("=", 1)[1])
              for line in cost.structured_content["lines"]
              if line.strip().startswith("SimTime=")]
    assert stamps and stamps[0] == pytest.approx(5.0)


def test_open_model_owns_a_snapshot(tmp_path):
    from raptor.mcp import server  # noqa: F401
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import (
        describe_model, open_model, save_model_as)
    from raptor.mcp.tools_model import create_model
    mid = create_model("snap_src", template="series", n=2) \
        .structured_content["model_id"]
    saved = save_model_as(mid, "snapshot_source")
    src = Path(saved.structured_content["path"])

    other = create_model("snap_other", template="series", n=4) \
        .structured_content["model_id"]
    other_saved = save_model_as(other, "snapshot_other")
    opened = open_model(src.name).structured_content["model_id"]

    # Replace the source after opening: the handle must keep its own copy.
    src.write_bytes(Path(other_saved.structured_content["path"]).read_bytes())
    described = describe_model(opened).structured_content
    assert described["blocks"] == 2


def test_cost_report_without_samples_is_a_precondition(motor):
    from raptor.mcp.tools_run import get_report

    run = _simulate(motor, num_runs=2)
    err = _error(get_report(run.structured_content["run_id"], section="cost"))
    assert err["code"] == "precondition_failed"
    assert "cost samples" in err["message"]


def test_describe_full_lists_events(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import (
        add_event, create_model, describe_model)

    mid = create_model("events", template="series", n=2) \
        .structured_content["model_id"]
    add_event(mid, "E1", 0.1)
    described = describe_model(mid, detail="full").structured_content
    assert [e["name"] for e in described["event_names"]] == ["E1"]


def test_simulate_rejects_nonpositive_workers(motor):
    err = _error(_simulate(motor, num_runs=1, workers=0))
    assert err["code"] == "invalid_argument"


def test_export_cannot_target_the_render_tree(motor):
    from raptor.mcp.tools_tables import export_tables

    err = _error(export_tables(motor, folder="renders/out", formats=["csv"]))
    assert "renders" in err["message"]


def test_empty_data_table_note_fills_its_viewport(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow

    window = MainWindow()
    window.resize(1200, 800)
    window._load_into_app(load_rbd(str(GOLDEN / "CS_Probe" / "input.rbd")),
                          None)
    window.show()
    qapp.processEvents()
    tables = window._data_tables
    table = tables._events_table
    tables._tabs.setCurrentIndex(tables._tabs.indexOf(table))
    qapp.processEvents()
    label = tables._empty_labels[table]
    assert label.isVisible()
    assert label.geometry().width() == table.viewport().width()
    assert label.geometry().height() == table.viewport().height()
    window._dirty = False
    window.close()


def test_validation_flags_a_child_of_a_missing_hierarchy():
    from raptor.api.validate import issues
    from raptor.core.fileio.rbd import load_rbd

    rbd = load_rbd(str(GOLDEN / "HierarchyExample" / "input.rbd"))
    rows = rbd.section("HIERARCHY_DATA").rows
    removed = next(r for r in rows if str(r.get("ID")) not in ("0", "None"))
    rows.remove(removed)
    hid = str(removed["ID"])
    if not any(str(b.get("ParentId")) == hid
               for b in rbd.section("BLOCK_DATA").rows):
        rbd.section("BLOCK_DATA").rows[0]["ParentId"] = hid
    found = [str(i) for i in issues(rbd)]
    assert any("missing hierarchy" in text for text in found)


def test_save_model_as_creates_a_nested_folder(hospital):
    from raptor.mcp.tools_model import save_model_as

    saved = save_model_as(hospital, "smoke/nested/model")
    assert not saved.is_error, saved.content
    assert Path(saved.structured_content["path"]).is_file()

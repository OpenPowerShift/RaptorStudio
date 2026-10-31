"""Importance measures at the two surfaces a user actually touches.

`test_importance.py` pins the arithmetic. This file pins the wiring: the
Run panel setting, the Results tab, the canvas overlay, and the MCP tool.
Those are the places where a feature can be correct and still unusable --
the overlay shipped greyed out with no way to discover what ungreyed it,
which is what this file exists to stop recurring.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"
MODEL = str(REPO / "new_docs" / "models" / "Lantern1.rbds")

SAMPLE = {
    "mode": "both", "runs": 100, "batches": 3, "blocks": 2,
    "metrics": {"Availability": {
        "baseline": 0.9,
        "rows": [
            {"block": "A", "block_id": 1, "birnbaum": 0.02, "raw": 1.5,
             "rrw": 1.1, "vesely_fussell": 0.2, "perfect": 0.92,
             "failed": 0.90, "sem": 0.001},
            {"block": "B", "block_id": 2, "birnbaum": 0.30, "raw": 4.0,
             "rrw": 1.4, "vesely_fussell": 0.5, "perfect": 0.95,
             "failed": 0.65, "sem": 0.002},
        ]}},
    "notes": [],
}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------- Run panel
def test_run_panel_checkbox_sets_both_by_default(qapp):
    from raptor.gui.ui.run_panel import RunPanel

    p = RunPanel()
    p.set_importance_blocks(6)
    assert p.experiment()["importance"] == "none"
    p._importance_check.setChecked(True)
    assert p.experiment()["importance"] == "both"
    # The cost line has to state the actual work before the user commits.
    note = p._importance_note.text()
    assert "6 block(s)" in note and "13 batches" in note
    p._importance_check.setChecked(False)
    assert p.experiment()["importance"] == "none"


def test_run_panel_mode_picker_changes_the_family(qapp):
    from raptor.gui.ui.run_panel import RunPanel

    p = RunPanel()
    p.set_importance_blocks(4)
    p._importance_check.setChecked(True)
    idx = p._importance_mode.findData("improvement")
    p._importance_mode.setCurrentIndex(idx)
    assert p.experiment()["importance"] == "improvement"
    assert "5 batches" in p._importance_note.text()


# ------------------------------------------------------------ Results tab
def test_results_tab_appears_only_with_data(qapp):
    from raptor.gui.ui.results import ResultsDock

    dock = ResultsDock()
    dock.set_results([], "", "", report_data=[], units="h")
    assert not [i for i in range(dock._details_tabs.count())
                if dock._details_tabs.tabText(i) == "Importance"]
    dock.set_results([], "", "", report_data=[], units="h",
                     importance=SAMPLE)
    tabs = [dock._details_tabs.tabText(i)
            for i in range(dock._details_tabs.count())]
    assert "Importance" in tabs
    dock.deleteLater()


# --------------------------------------------------------------- Overlay
def test_overlay_is_greyed_out_without_data_and_tooltip_says_how_to_enable(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._sync_overlay_actions([])
    action = win._importance_overlay_action
    assert not action.isEnabled()
    assert "Importance measures" in action.toolTip()
    win._on_importance_overlay_toggled(True)
    assert "Importance measures" in win.statusBar().currentMessage()
    win.close()


def test_overlay_colours_the_canvas_once_the_run_has_measures(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._results.set_results([], "", "", report_data=[], units="h",
                             importance=SAMPLE)
    win._results._importance_metric = "Availability"
    win._sync_overlay_actions([])
    assert win._importance_overlay_action.isEnabled()
    win._on_importance_overlay_toggled(True)
    visuals = win._diagram_dock.view._last_result_visuals or {}
    assert visuals.get("kind") == "importance"
    # The overlay ranks by the same rule as the tab: Vesely-Fussell here.
    assert visuals.get("measure") == "Vesely-Fussell"
    assert win._overlay_chip.text() == "Canvas: Importance"
    win.close()


def test_the_importance_columns_explain_their_acronyms(qapp):
    """Reported from use: "Birnbaum / RAW / RRW" are bare acronyms.

    The header tooltip is the only place that can say what each column
    answers without adding a paragraph to the page. It must also say what
    a dash means, since undefined is a real answer here, not zero.
    """
    import copy as _copy

    from PySide6.QtWidgets import QTableWidget

    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._results.set_results([], "", "", report_data=[], units="h",
                             importance=_copy.deepcopy(SAMPLE))
    qapp.processEvents()

    tabs = win._results._details_tabs
    page = next((tabs.widget(i) for i in range(tabs.count())
                 if tabs.tabText(i) == "Importance"), None)
    assert page is not None, "the Importance tab did not appear"
    table = page.findChild(QTableWidget)
    tips = {}
    for col in range(table.columnCount()):
        head = table.horizontalHeaderItem(col)
        tips[head.text()] = head.toolTip()

    assert "Risk Achievement Worth" in tips["RAW"]
    assert "Risk Reduction Worth" in tips["RRW"]
    assert "share of the current downtime" in tips["Vesely\u2013Fussell"]
    assert "system" in tips["Birnbaum"].lower()
    for label, tip in tips.items():
        assert len(tip) > 40, f"{label}'s tooltip says nothing"
    # Birnbaum does exist when the baseline never failed; the three ratios
    # are the ones that vanish, so they are the ones that must say so.
    for label in ("RAW", "RRW", "Vesely\u2013Fussell"):
        assert "never failed" in tips[label], (
            f"{label} must say what a dash means")
    win.close()


def test_switching_the_metric_recolours_the_canvas(qapp):
    """The tab measures on Availability / Reliability / Dependability and
    the overlay colours from the chosen one. Changing the metric used to
    rebuild the table and leave the canvas on the old metric, with the
    status line still naming it."""
    import copy as _copy

    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    data = _copy.deepcopy(SAMPLE)
    data["metrics"]["Reliability"] = {
        "baseline": 0.7,
        "rows": [
            {"block": "A", "block_id": 1, "birnbaum": 0.01, "raw": 1.1,
             "rrw": 1.05, "vesely_fussell": 0.1, "perfect": 0.71,
             "failed": 0.70, "sem": 0.003},
            {"block": "B", "block_id": 2, "birnbaum": 0.9, "raw": 9.0,
             "rrw": 3.0, "vesely_fussell": 0.8, "perfect": 0.95,
             "failed": 0.10, "sem": 0.004},
        ]}

    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._results.set_results([], "", "", report_data=[], units="h",
                             importance=data)
    win._results._importance_metric = "Availability"
    win._sync_overlay_actions([])
    win._importance_overlay_action.setChecked(True)
    win._on_importance_overlay_toggled(True)
    view = win._diagram_dock.view
    assert view._last_result_visuals.get("metric_name") == "Availability"

    win._results._set_importance_metric("Reliability")
    visuals = view._last_result_visuals or {}
    assert visuals.get("kind") == "importance"
    assert visuals.get("metric_name") == "Reliability", (
        "the canvas kept the metric the table no longer shows")
    # And it is the NEW numbers: block B holds 0.8 of the downtime here.
    assert visuals["values"][2] == pytest.approx(0.8)
    assert "on Reliability" in win.statusBar().currentMessage()

    # With the overlay off, switching only prepares the next switch-on.
    win._importance_overlay_action.setChecked(False)
    win._on_importance_overlay_toggled(False)
    win._results._set_importance_metric("Availability")
    assert view._last_result_visuals is None
    win.close()


# -------------------------------------------------------------------- MCP
def _use_workspace(tmp_path: Path) -> None:
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None


def test_mcp_simulate_can_request_importance_and_report_it(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import get_report, simulate

    mid = open_model("TwoBlockRedundancy"
                     ).structured_content["model_id"]
    run = asyncio.run(simulate(mid, num_runs=20, importance="improvement",
                               confirm_long_run=True))
    assert not run.is_error, run.content
    data = run.structured_content
    assert data["importance"]["mode"] == "improvement"
    assert data["importance"]["batches"] == 1 + 2      # two blocks
    rows = data["importance"]["metrics"]["Availability"]["rows"]
    assert rows and "vesely_fussell" in rows[0]
    assert "Importance measures" in run.content[0].text

    rep = get_report(run_id=data["run_id"], section="importance", limit=200)
    assert not rep.is_error, rep.content
    text = "\n".join(rep.structured_content["lines"])
    assert "IMPORTANCE_AVAILABILITY" in text
    json.dumps(data, allow_nan=False)


def test_mcp_run_without_importance_has_no_section(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import get_report, simulate

    mid = open_model("TwoBlockRedundancy"
                     ).structured_content["model_id"]
    run = asyncio.run(simulate(mid, num_runs=5, confirm_long_run=True))
    assert "importance" not in run.structured_content
    rep = get_report(run_id=run.structured_content["run_id"],
                     section="importance")
    assert rep.is_error


# ------------------------------------------------- a whole GUI run, as a user
def test_a_user_run_with_importance_fills_the_tab_and_unlocks_the_overlay(qapp):
    """The default Run path is the SYNCHRONOUS one (`_run_simulation`).

    Only the background worker computed importance, so ticking the box and
    pressing Run silently dropped the setting -- no tab, no overlay data.
    This drives the same sequence a user does: open a model, tick it in the
    Run panel, press Run, look at the Results.
    """
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._set_exp_field("trials", 10)
    win._run_panel.set_importance_blocks(3)
    win._run_panel._importance_check.setChecked(True)   # the user's click
    qapp.processEvents()
    assert win._current_experiment()["importance"] == "both"

    win._dirty = False          # no "unsaved changes" prompt mid-test
    win._run_simulation()
    qapp.processEvents()

    assert win._results.has_results()
    data = win._results._importance
    assert data is not None, "the synchronous run dropped the setting"
    assert data["mode"] == "both"
    assert len(data["metrics"]["Availability"]["rows"]) == 3
    tabs = [win._results._details_tabs.tabText(i)
            for i in range(win._results._details_tabs.count())]
    assert "Importance" in tabs

    assert win._importance_overlay_action.isEnabled()
    win._on_importance_overlay_toggled(True)
    assert win._diagram_dock.view._last_result_visuals["kind"] == "importance"
    win._dirty = False
    win.close()


def test_a_user_run_without_importance_still_works_and_has_no_tab(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._set_exp_field("trials", 5)
    win._dirty = False
    win._run_simulation()
    qapp.processEvents()
    assert win._results.has_results()
    assert win._results._importance is None
    tabs = [win._results._details_tabs.tabText(i)
            for i in range(win._results._details_tabs.count())]
    assert "Importance" not in tabs
    win._dirty = False
    win.close()


# ------------------------------------------------------- MCP, as an assistant
def test_mcp_user_flow_model_then_importance_table(tmp_path):
    """A tutorial model, a run that asks for the measures, then reading
    them back the two ways a client would."""
    _use_workspace(tmp_path)
    from raptor.mcp.tools_metrics import describe_metrics
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import get_report, simulate

    mid = open_model("Lantern1.rbds").structured_content["model_id"]
    run = asyncio.run(simulate(mid, num_runs=10, importance="both",
                               confirm_long_run=True))
    assert not run.is_error, run.content
    data = run.structured_content
    body = run.content[0].text
    assert "IMPORTANCE_AVAILABILITY" in body          # the ranking table
    assert "ranked by:" in body                       # names the measure
    blocks = len(data["importance"]["metrics"]["Availability"]["rows"])
    assert blocks >= 3

    rep = get_report(run_id=data["run_id"], section="importance", limit=200)
    assert not rep.is_error
    assert "IMPORTANCE_AVAILABILITY" in "\n".join(
        rep.structured_content["lines"])

    # And the metrics tool still explains what those numbers are -- by
    # pointing at the formulas page, not by restating it.
    desc = describe_metrics(run_id=data["run_id"])
    assert not desc.is_error
    assert "read_doc('formulas')" in desc.content[0].text
    assert desc.structured_content["runs"] == 10


# ------------------------------------------------------------- progress UI
def test_the_background_poller_displays_the_workers_importance_progress(qapp):
    """The worker set `state["importance_progress"]` and no one read it."""
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._apply_importance_progress(
        {"importance_progress": (4, 13, "failed:Engine_Core")})
    assert win._run_progress_label.text() == "30%"
    assert "variant 4 of 13" in win.statusBar().currentMessage()
    win._apply_importance_progress({})          # nothing to do, no crash
    win.close()


def _block_rows(win):
    import json
    return json.dumps([dict(r) for r in
                       win._rbd.section("BLOCK_DATA").rows], sort_keys=True)


def test_importance_leaves_the_model_and_the_canvas_alone(qapp):
    """Variants are copies; the user must see no model change, no extra undo
    step, and no repaint from a variant."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._set_exp_field("trials", 5)
    win._run_panel.set_importance_blocks(3)
    win._run_panel._importance_check.setChecked(True)
    win._dirty = False
    qapp.processEvents()

    before = _block_rows(win)
    undo_after_tick = win._diagram_dock.view._stack.count()

    win._run_simulation()
    qapp.processEvents()

    assert _block_rows(win) == before, "the live model was modified"
    assert win._diagram_dock.view._stack.count() == undo_after_tick, (
        "the importance pass pushed an undo step")
    assert not win._dirty, "the importance pass marked the model dirty"
    win.close()


def test_aborting_during_importance_leaves_the_model_untouched(qapp):
    """Stop during the variant phase: the pass must stop (it used to ignore
    Stop entirely) and leave the model exactly as it was."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._set_exp_field("trials", 5)
    win._run_panel.set_importance_blocks(3)
    win._run_panel._importance_check.setChecked(True)
    win._dirty = False
    qapp.processEvents()
    before = _block_rows(win)

    # Stop lands during the first variant batch.
    real = win._update_importance_progress
    seen: list[int] = []

    def stop_after_first(done, total, label=""):
        seen.append(int(done))
        real(done, total, label)
        if int(done) >= 2:
            win._sync_stop = True

    win._update_importance_progress = stop_after_first
    win._run_simulation()
    qapp.processEvents()
    win._sync_stop = False

    # Verified: no variant ever reaches the live model, whether the phase
    # completes or is cut short. NOT yet verified: prompt cancellation --
    # `_sync_stop` is cleared by the run's own epilogue before the variant
    # phase reads it, so Stop mid-importance is still open (runbook §8).
    assert _block_rows(win) == before
    assert win._rbd is not None
    win.close()


def test_importance_progress_shows_a_percentage_on_the_run_bar(qapp):
    """With Animate off nothing moves on the canvas, so the variant phase
    must show on the progress bar and in the status line."""
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._update_importance_progress(7, 35, "perfect:Pump B")
    assert win._run_progress_label.text() == "20%"
    assert not win._run_progress.isHidden()
    assert win._run_progress.value() == 20
    message = win.statusBar().currentMessage()
    assert "variant 7 of 35" in message and "(20%)" in message
    win._end_importance_progress("done")
    assert win._run_progress.isHidden()
    assert win._run_progress_label.isHidden()
    win.close()


def test_the_background_poller_displays_the_workers_importance_progress(qapp):
    """The worker set `state["importance_progress"]` and nothing read it."""
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._apply_importance_progress(
        {"importance_progress": (4, 13, "failed:Engine_Core")})
    assert win._run_progress_label.text() == "30%"
    assert "variant 4 of 13" in win.statusBar().currentMessage()
    win._apply_importance_progress({})
    win.close()


def test_a_user_run_reports_progress_for_every_variant(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    seen: list[tuple[int, int]] = []
    real = win._update_importance_progress

    def spy(done, total, label=""):
        seen.append((int(done), int(total)))
        return real(done, total, label)

    win._update_importance_progress = spy
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._set_exp_field("trials", 5)
    win._run_panel.set_importance_blocks(3)
    win._run_panel._importance_check.setChecked(True)
    win._dirty = False
    win._run_simulation()
    qapp.processEvents()

    assert seen, "the run never reported importance progress"
    assert {total for _d, total in seen} == {7}      # 1 + 3 blocks x 2
    assert [d for d, _t in seen] == sorted(d for d, _t in seen)
    assert seen[-1][0] == 7
    assert win._run_progress.isHidden()
    win.close()


def test_importance_markers_survive_entering_and_leaving_a_hierarchy(qapp):
    """Reported from use: markers disappeared inside a hierarchy and did not
    come back on the way out. `_reapply_result_visuals` had no branch for
    this kind, so a level rebuild dropped it silently."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView

    model = load_rbd(str(GOLDEN / "HierarchyExample" / "input.rbd"))
    v = DiagramView()
    v.load_rbd(model)
    assert v.hiers, "this fixture must have a hierarchy to enter"
    # Block ids come from the model, not from `v.blocks`: the item dict is
    # populated lazily, so deriving them from the view made `values` empty
    # and the assertions vacuous.
    ids = [int(float(r["ID"])) for r in model.section("BLOCK_DATA").rows
           if str(r.get("ID", "")) != "0"]
    values = {bid: float(i + 1) for i, bid in enumerate(ids)}
    v.apply_importance_colors(values, measure="Vesely-Fussell",
                              metric_name="Availability")

    hid = next(iter(v.hiers))
    v.enter_level(hid)                     # enter
    assert v._last_result_visuals["kind"] == "importance"
    inside = [it for _bid, it in v._block_items.items()]
    if inside:      # this fixture's hierarchy may be nodes-only
        assert all(it.toolTip().startswith("Importance") for it in inside), (
            "markers were lost on entering the hierarchy")

    v.go_home()                            # back out
    outside = [it for bid, it in v._block_items.items() if bid in values]
    assert all(it.toolTip().startswith("Importance") for it in outside), (
        "markers did not come back when leaving the hierarchy")
    assert v._last_result_visuals["measure"] == "Vesely-Fussell"

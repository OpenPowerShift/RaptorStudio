"""Analyse tab: What If, Sensitivity and the three optimisers run in the
background on a copy, show what they measured, and Apply writes the chosen
change into the model as one undo step."""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

SPEC = {
    "blocks": [
        {"name": "Pump", "fail_dist": "Weibull", "fail_params": [2.0, 100.0, 0.0],
         "repair_dist": "Fixed", "repair_params": [5, 0, 0],
         "uses_pm": 1, "pm_freq": 400.0, "pm_dist": "Fixed", "pm_params": [2, 0, 0],
         "pm_cost": 1.0, "pm_fixed_cost": 10.0, "rep_fixed_cost": 100.0,
         "spare_pool": "Seals", "sp_type": "Pooled",
         "resource1_name": "Crew", "num_res": 1, "res1_rep": 1},
        {"name": "Valve", "fail_dist": "Exponential", "fail_params": [400],
         "repair_dist": "Exponential", "repair_params": [2]}],
    "spare_pools": [{"name": "Seals", "stock": 0, "rso": 1, "new_spares": 1,
                     "arrive_every": 200.0}],
    "resources": [{"name": "Crew", "num_in": 1}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "Valve"},
              {"from": "Valve", "to": "End"}],
    "experiment": {"trials": 4, "termination_type": "TimeSim", "term_condition": 1000.0},
}


@pytest.fixture
def win(tmp_path):
    from raptor.api import build
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("files/save_before_run", "never")
    w = MainWindow()
    w._load_into_app(build.build_from_spec(SPEC), str(tmp_path / "m.rbd"))
    yield w
    w._dirty = False
    w.close()


def _run(dlg, seconds: float = 180.0) -> None:
    assert dlg.start(), dlg.caption.text()
    assert not dlg._run_btn.isEnabled() and dlg._stop_btn.isEnabled()
    end = time.monotonic() + seconds
    while dlg._job is not None and not dlg._job.done and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    dlg._poll()
    assert dlg._job.error is None, dlg._job.error
    assert dlg._run_btn.isEnabled() and not dlg._stop_btn.isEnabled()


def _rows(w, section):
    # pool and resource rows have no ID; BLOCK_DATA's ID 0 is the template
    return [r for r in w._diagram_dock.view._rbd_ref.section(section).rows
            if str(r.get("ID", "x")) != "0"]


def _pump(w):
    return next(r for r in _rows(w, "BLOCK_DATA") if r.get("blockName") == "Pump")


def test_the_analyse_tab_holds_the_analyses(win):
    from raptor.gui.command_table import LAYOUT
    page = next(p for p in LAYOUT if p.title == "Analyse")
    ids = [b.id for g in page.groups for b in g.items if hasattr(b, "id")]
    for cid in ("analyse.whatif", "sim.recommend", "analyse.sensitivity", "sim.sweep",
                "analyse.optimise_spares", "analyse.optimise_pm",
                "analyse.optimise_crews", "sim.compare"):
        assert cid in ids
    assert win._whatif_action.text() == "What If…"


def test_what_if_compares_a_preset_and_applies_it(win):
    dlg = win._open_whatif_dialog()
    assert win._open_whatif_dialog() is dlg
    assert dlg.start() is False and "Add a scenario" in dlg.caption.text()
    dlg._block.setCurrentText("Pump")
    labels = [dlg._preset.itemText(i) for i in range(dlg._preset.count())]
    assert "half the repair time" in labels and "one more spare" in labels
    dlg._preset.setCurrentText("half the repair time")
    dlg._add_preset()
    dlg._trials.setValue(4)
    _run(dlg)
    assert dlg.table.rowCount() == 2
    assert dlg.table.item(0, 0).text() == "Base"
    assert dlg.table.item(1, 0).text() == "Pump: half the repair time"
    assert "pts" in dlg.table.item(1, 2).text()
    assert "Availability" in dlg.result_text()
    dlg.table.selectRow(1)
    assert dlg._apply_btn.isEnabled()
    assert dlg.apply_selected()
    assert float(_pump(win)["RParam1"]) == pytest.approx(2.5)
    assert not dlg._apply_btn.isEnabled()
    win._undo_stack.undo()
    assert float(_pump(win)["RParam1"]) == pytest.approx(5.0)


def test_sensitivity_draws_the_tornado_and_applies_the_better_end(win):
    dlg = win._open_sensitivity_dialog()
    dlg._top.setValue(3)
    dlg._trials.setValue(4)
    _run(dlg)
    rows = dlg.chart.rows
    assert rows and rows == sorted(rows, key=lambda r: -(r["swing"] or 0))
    assert "swing" in dlg.result_text()
    chosen = dlg.selection()
    if chosen is not None:                      # an end that improves the metric
        assert dlg.apply_selected()
        win._undo_stack.undo()


def test_spares_optimiser_recommends_and_applies_a_stock(win):
    dlg = win._open_optimise_spares()
    dlg._target.setValue(50.0)
    dlg._budget.setValue(3)
    dlg._trials.setValue(4)
    _run(dlg)
    assert dlg.table.rowCount() == len(dlg.result["points"]) >= 1
    assert dlg.result["recommended"] is not None
    _key, _label, changes = dlg.selection()
    assert all(c.startswith("pool:Seals.") for c in changes)
    if any(c != "pool:Seals.stock=0" for c in changes):
        assert dlg.apply_selected()
        pool = next(r for r in _rows(win, "SPARE_POOL_DATA") if r.get("PoolName") == "Seals")
        assert int(float(pool["Stock"])) == dlg.result["recommended"]["stocks"]["Seals"]
        win._undo_stack.undo()


def test_pm_optimiser_lists_each_block(win):
    dlg = win._open_optimise_pm()
    dlg._trials.setValue(3)
    _run(dlg)
    blocks = [dlg.table.item(i, 0).text() for i in range(dlg.table.rowCount())]
    assert "Pump" in blocks
    assert dlg.caption.text()


def test_crew_optimiser_applies_the_size(win):
    dlg = win._open_optimise_crews()
    dlg._target.setValue(50.0)
    dlg._extra.setValue(1)
    dlg._trials.setValue(3)
    _run(dlg)
    assert dlg.table.item(0, 0).text() == "Crew"
    units = int(dlg.table.item(0, 1).text())
    _key, _label, changes = dlg.selection()
    assert changes == [f"resource:Crew.units={units}"]
    if units != 1:
        assert dlg.apply_selected()
        crew = next(r for r in _rows(win, "RESOURCE_DATA") if r.get("PoolName") == "Crew")
        assert int(float(crew["NumIn"])) == units
        win._undo_stack.undo()


def test_a_model_without_pools_says_so(tmp_path):
    from raptor.api import build
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    spec = {**SPEC, "spare_pools": [], "resources": [],
            "blocks": [{k: v for k, v in b.items()
                        if k not in ("spare_pool", "sp_type", "resource1_name", "num_res",
                                     "res1_rep")} for b in SPEC["blocks"]]}
    w._load_into_app(build.build_from_spec(spec), str(tmp_path / "n.rbd"))
    try:
        spares = w._open_optimise_spares()
        assert spares.start() is False and "no finite spare pool" in spares.caption.text()
        crews = w._open_optimise_crews()
        assert crews.start() is False and "no repair resource" in crews.caption.text()
    finally:
        w._dirty = False
        w.close()


def test_trials_to_precision_sizes_the_next_run(win):
    from raptor.gui.command_table import LAYOUT
    page = next(p for p in LAYOUT if p.title == "Simulate")
    assert "sim.precision" in [b.id for g in page.groups for b in g.items if hasattr(b, "id")]
    win._run_panel._precision_btn.click()
    dlg = win._precision_dialog
    dlg._target.setValue(30.0)
    dlg._max.setValue(400)
    _run(dlg)
    result = dlg.result
    assert dlg.table.rowCount() == len(result.history) >= 1
    assert result.trials == result.history[-1]["trials"]
    assert "unavailability" in dlg.caption.text()
    if result.trials != int(win._current_experiment()["trials"]):
        assert dlg.apply_selected()
        assert int(win._current_experiment()["trials"]) == result.trials
        assert win._run_panel._trials.value() == result.trials
        win._undo_stack.undo()
        assert int(win._current_experiment()["trials"]) == 4


def test_undoing_a_run_setting_puts_the_experiment_back_too(win):
    # undo used to restore NumRuns but leave the experiment (which the panel
    # and the next run read) at the new value
    win._set_exp_field("trials", 77)
    assert int(win._current_experiment()["trials"]) == 77
    win._undo_stack.undo()
    assert int(win._current_experiment()["trials"]) == 4
    assert win._run_panel._trials.value() == 4
    assert int(float(win._rbd.section("SYSTEM_DATA").rows[0]["NumRuns"])) == 4
    win._undo_stack.redo()
    assert int(win._current_experiment()["trials"]) == 77
    assert win._run_panel._trials.value() == 77


def test_estimate_from_experience_writes_the_law_as_one_undo_step(win):
    from PySide6.QtWidgets import QPushButton
    view = win._diagram_dock.view
    valve = next(bid for bid, b in view.blocks.items() if b.name == "Valve")
    win._inspector.show_block(valve, view.blocks)
    win._inspector.ensure_all_tabs_built()
    buttons = [b for b in win._inspector.findChildren(QPushButton)
               if b.text() == "Estimate from experience…"]
    assert len(buttons) == 2                        # failure and repair
    next(b for b in buttons if "share fails" in b.toolTip()).click()
    dlg = win._experience_dialog
    assert dlg.block_id == valve and dlg.use == "fail"
    assert not dlg.apply_btn.isEnabled()            # no ages yet
    dlg.set_answers([(10, 300), (50, 100)])
    assert dlg.fit is None and "inconsistent" in dlg.summary.text()
    dlg.set_answers([(10, 100), (50, 300)])
    assert dlg.fit["law"] == "weibull" and "wears out" in dlg.summary.text().lower()
    assert dlg.apply()
    row = next(r for r in _rows(win, "BLOCK_DATA") if r.get("blockName") == "Valve")
    assert int(float(row["FDist"])) == 15
    assert float(row["FParam1"]) == pytest.approx(dlg.fit["params"]["shape"], rel=1e-5)
    win._undo_stack.undo()
    row = next(r for r in _rows(win, "BLOCK_DATA") if r.get("blockName") == "Valve")
    assert int(float(row["FDist"])) == 4 and float(row["FParam1"]) == pytest.approx(400)


def test_a_stopped_study_says_its_results_are_partial_and_cannot_be_applied(win):
    dlg = win._open_whatif_dialog()
    dlg._block.setCurrentText("Pump")
    dlg._preset.setCurrentText("half the repair time")
    dlg._add_preset()
    dlg._trials.setValue(2)
    _run(dlg)
    dlg._job.abort.set()                # as if Stop came before the end
    dlg._poll()
    assert dlg._progress.format() == "Stopped: partial results"
    assert dlg.caption.text().startswith("Stopped before the end")
    dlg.table.selectRow(1)
    assert not dlg._apply_btn.isEnabled()


def test_a_failed_study_shows_the_message_not_the_exception_class(win):
    from raptor.gui.ui.analysis_dialog import AnalysisJob

    def work(ws, mid, progress, abort):
        raise ValueError("no input could be varied in this model")
    job = AnalysisJob(win._rbd, work)
    job.start()
    job.wait(10)
    assert job.error == "no input could be varied in this model"

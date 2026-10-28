"""Regression tests for the UI review fixes.

Grouped by the surface they pin: link/weak-link readouts, run panel, and
selection/canvas behaviour.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

LEGACY = "legacy/rbd/Ch14_Problem5.rbd"
MODEL = "new_docs/models/HospitalPower.rbds"


def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_link_warning_names_its_endpoints_and_hides_source():
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.preflight import preflight_warnings

    warnings = preflight_warnings(load_rbd(LEGACY))
    assert any("Link 7 (Path_C → n2)" in w for w in warnings)
    assert all(".mod" not in w and "Raptor 7," not in w
               for w in warnings)
    assert any("Lower this link's Max Flow" in w for w in warnings)


def test_enabling_ao_plot_seeds_only_the_default_interval():
    from raptor.core import run_experiment as rx
    from raptor.gui.ui.run_panel import RunPanel

    qapp()
    panel = RunPanel()
    panel.set_experiment(rx.normalise(
        {"term_condition": 1.0,
         "recording": {"ao_plot": False, "ao_interval": 10.0}}))
    panel._recording_checks["ao_plot"].setChecked(True)
    assert panel.experiment()["recording"]["ao_interval"] < 1.0

    panel.set_experiment(rx.normalise(
        {"term_condition": 1.0,
         "recording": {"ao_plot": False, "ao_interval": 5.0}}))
    panel._recording_checks["ao_plot"].setChecked(True)
    assert panel.experiment()["recording"]["ao_interval"] == 5.0


def test_multirun_ao_autoscales_to_the_envelope():
    from raptor.gui.ui.plots import AoPlotWidget

    qapp()
    plot = AoPlotWidget()
    runs = [
        {"t": [0, 1], "ao": [0.9991, 0.9992], "g": [1, 1],
         "y": [0, 0], "r": [0, 0], "iv": [None, None]},
        {"t": [0, 1], "ao": [0.9994, 0.9995], "g": [1, 1],
         "y": [0, 0], "r": [0, 0], "iv": [None, None]},
    ]
    plot.set_data(runs)
    plot.set_mode(AoPlotWidget.MULTI_RUN)
    low, high = plot._y_bounds()
    assert 0.9990 < low < 0.9992
    assert 0.9994 < high < 1.0


def test_simulate_restores_a_minimised_run_panel():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    app = qapp()
    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win.show()
    for _ in range(5):
        app.processEvents()
    win._dock_chrome["Results"]["set_minimized"](True)
    app.processEvents()
    assert win._run_dock.widget().isHidden()
    win._ribbon.select_page("Simulate")
    app.processEvents()
    assert not win._run_dock.widget().isHidden()
    win._dirty = False
    win.close()


def test_a_noop_row_edit_pushes_no_undo_step():
    """editingFinished fires even when nothing changed; that must not add
    an empty macro (it marked the model dirty after a no-op)."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.diagram_commands import apply_row_edits
    from raptor.gui.ui.main_window import MainWindow

    app = qapp()
    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win.show()
    for _ in range(4):
        app.processEvents()
    win._dirty = False
    v = win._diagram_dock.view
    row = win._rbd.sections["SYSTEM_DATA"].rows[0]
    before = v._stack.index()
    n = apply_row_edits(
        v, "SYSTEM_DATA", 0, row,
        [("SysStreamsArray", row.get("SysStreamsArray", ""))],
        "System settings")
    assert n == 0
    assert v._stack.index() == before
    assert win._dirty is False
    win.close()


def test_capacity_rows_tolerate_a_node_missing_from_the_first_run():
    """A node only present in a later snapshot must not crash the tab
    with KeyError; the system row still comes first."""
    from types import SimpleNamespace as NS

    from raptor.gui.ui.results import ResultsDock

    caps = [
        NS(nodes={1: NS(name="Start"), 2: NS(name="Pump")}),
        NS(nodes={1: NS(name="Start"), 2: NS(name="Pump"),
                  3: NS(name="Later")}),
    ]
    assert ResultsDock._capacity_node_ids(caps, 1) == [1, 3, 2]


def test_unreachable_end_is_a_warning_not_a_blocking_issue():
    """The reachability rule is a warning in api.validate (models still
    run) and preflight_warnings already lists it. The window used to
    promote every finding to a blocking issue and show it twice."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow

    qapp()
    rbd = load_rbd("tests/golden/TwoBlockRedundancy/input.rbd")
    end = next(r for r in rbd.sections["NODE_DATA"].rows
               if r.get("Type") == "3")
    end["Type"] = "2"
    win = MainWindow()
    win._rbd = rbd
    issues = win._validation_issues()
    warnings = win._validation_warnings()
    assert not any("Start to End" in i for i in issues)
    assert any("Start to End" in w for w in warnings)
    win.close()


def test_weak_table_counts_an_empty_denominator_as_one():
    """Phasing3 HighGain spends whole runs on standby. The table used to
    drop those runs (mean 0.700) while the canvas and engine used Raptor's
    1.0 fallback (mean 0.971)."""
    from raptor.gui.ui.results import ResultsDock

    runs = [{7: {"name": "HighGain", "standby": 100.0}} for _ in range(9)]
    runs.append({7: {"name": "HighGain", "run": 70.0, "repair": 30.0}})
    rows = ResultsDock._weak_metric_rows(
        runs, ("run",), ("repair", "rep_hold", "done"))
    assert rows == [("HighGain", [1.0] * 9 + [0.7])]


def test_collapse_and_zoom_selection_follow_what_they_can_act_on():
    """A hierarchy oval is selectable but Collapse cannot move it, and
    Zoom to Selection needs something selected at all."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    app = qapp()
    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win.show()
    for _ in range(4):
        app.processEvents()
    win._diagram_dock.view._scene.clearSelection()
    win._refresh_commands()
    assert win._collapse_action.isEnabled() is False
    assert win._zoom_selection_action.isEnabled() is False

    view = win._diagram_dock.view
    hid = next(iter(view._hier_items))
    view._hier_items[hid].setSelected(True)
    win._refresh_commands()
    assert win._collapse_action.isEnabled() is False
    assert win._zoom_selection_action.isEnabled() is True

    bid = next(iter(view._block_items))
    view._block_items[bid].setSelected(True)
    win._refresh_commands()
    assert win._collapse_action.isEnabled() is True
    win.close()


def test_weak_labels_cover_every_element_and_keep_text_readable():
    from raptor.gui.ui.contrast import contrast_ratio
    from raptor.gui.ui.main_window import MainWindow
    from raptor.core.fileio.rbd import load_rbd

    app = qapp()
    path = "tests/golden/Phasing3/input.rbd"
    win = MainWindow()
    win._load_into_app(load_rbd(path), path)
    win.show()
    for _ in range(4):
        app.processEvents()
    view = win._diagram_dock.view
    bid = next(iter(view._block_items))
    nid = next(iter(view._node_items))
    eid = next(iter(view._event_items))
    hid = next(iter(view._hier_items))
    view.apply_weak_colors(
        {bid: 0.971}, 0.95, 0.90, "Dependability",
        node_metric={nid: 0.984}, event_metric={eid: 0.945},
        hier_metric={hid: 0.446}, digits=4)
    assert view._block_items[bid]._anal_label.toPlainText() == "0.9710"
    assert view._node_items[nid]._anal_label.toPlainText() == "0.9840"
    assert "Dependability" in view._node_items[nid]._inner_label.toolTip()
    assert view._event_items[eid]._anal_label.toPlainText() == "0.9450"
    assert view._hier_items[hid]._anal_label.toPlainText() == "0.4460"
    item = view._block_items[bid]
    ratio = contrast_ratio(
        item.brush().color().name(),
        item._label.defaultTextColor().name())
    assert ratio >= 4.5
    win._dirty = False
    win.close()


def test_run_panel_starts_locked_without_a_model():
    from raptor.gui.ui.main_window import MainWindow

    qapp()
    win = MainWindow()
    assert win._run_panel.is_enabled() is False
    win.close()


def test_annotation_tools_need_a_model_and_the_guard_holds():
    from PySide6.QtCore import QPointF
    from raptor.gui.ui.main_window import MainWindow

    qapp()
    win = MainWindow()
    assert not win._anno_text_tool.isEnabled()
    assert win._diagram_dock.view._begin_annotation(
        "text", QPointF(0, 0)) is None
    win.close()


def test_loading_a_model_clears_the_old_canvas_overlay():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    app = qapp()
    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    # Put the UI in the state a finished Weak run leaves behind.
    win._weak_color_action.blockSignals(True)
    win._weak_color_action.setChecked(True)
    win._weak_color_action.blockSignals(False)
    win._diagram_dock.view._weak_colors_on = True
    win._load_into_app(load_rbds("new_docs/models/Water1.rbds"),
                       "new_docs/models/Water1.rbds")
    for _ in range(4):
        app.processEvents()
    assert win._weak_color_action.isChecked() is False
    assert win._diagram_dock.view._weak_colors_on is False
    win._dirty = False
    win.close()


def test_close_clears_data_tables_and_locks_the_run_panel():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    app = qapp()
    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._dirty = False
    win._close_current_file()
    for _ in range(4):
        app.processEvents()
    assert win._rbd is None
    assert win._data_tables._blocks_table.rowCount() == 0
    assert win._run_panel.is_enabled() is False
    # A mirror push that lands after the close (a queued signal, or the
    # panel's own editingFinished) must not re-enable the panel. Under load
    # this arrived after the processEvents above, so drive it explicitly.
    win._sync_run_mirrors()
    app.processEvents()
    assert win._run_panel.is_enabled() is False
    win.close()


def test_a_batch_cannot_start_inside_a_stepped_session():
    from raptor.gui.commands import AppState, PREDICATES

    state = AppState(model_open=True, valid=True, stepping=True)
    assert PREDICATES["can_simulate"](state)   # Step/Next Time still work
    assert not PREDICATES["can_start_batch"](state)


def test_weak_selection_toggle_keeps_the_weak_tab():
    from PySide6.QtWidgets import QCheckBox
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    app = qapp()
    win = MainWindow()
    win._load_into_app(load_rbds(MODEL), MODEL)
    # AFTER the load: loading the model replaces the experiment fields, so
    # setting trials first let the model's own 200 trials through and the
    # run stopped at a "Long simulation" confirmation nothing answered.
    win._set_exp_field("trials", 2)
    win.show()
    for _ in range(5):
        app.processEvents()
    win._dirty = False
    win._run_simulation()
    app.processEvents()
    results = win._results
    results._tabs.setCurrentWidget(results._details_holder)
    idx = next(i for i in range(results._details_tabs.count())
               if results._details_tabs.tabText(i) == "Weak Link")
    results._details_tabs.setCurrentIndex(idx)
    chk = next(c for c in results._details_tabs.widget(idx)
               .findChildren(QCheckBox)
               if "Selected elements" in c.text())
    chk.setChecked(True)
    app.processEvents()
    assert results._details_tabs.tabText(
        results._details_tabs.currentIndex()) == "Weak Link"
    win._dirty = False
    win.close()

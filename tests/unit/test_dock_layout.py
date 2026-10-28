"""Dock layout: saved with its chrome, restored without trusting the blob, and Reset Layout puts it all back."""
from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from raptor.gui.ui import settings
from raptor.gui.ui.main_window import MainWindow

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_settings():
    QSettings().clear()
    yield
    QSettings().clear()


def _shown(qapp):
    w = MainWindow()
    w._dirty = False
    w.resize(1500, 900)
    w.show()
    for _ in range(6):
        qapp.processEvents()
    return w


def _arrangement(w):
    return {d.windowTitle(): (w.dockWidgetArea(d), d.isFloating(), d.isVisible()) for d in w.findChildren(type(w._results_dock))
            if d.parent() is w}


def test_blob_round_trips_the_chrome(qapp):
    w = _shown(qapp)
    w._dock_chrome["Model Explorer"]["set_closed"](True)
    w._dock_chrome["Inspector"]["set_minimized"](True)
    blob = json.loads(json.dumps(w._layout_blob()))
    assert blob["chrome"]["Model Explorer"]["closed"] is True
    assert blob["chrome"]["Inspector"]["minimized"] is True
    w2 = _shown(qapp)
    assert w2._apply_layout_blob(blob) is True
    assert w2._dock_chrome["Model Explorer"]["get"]()["closed"] is True
    assert w2._dock_chrome["Inspector"]["get"]()["minimized"] is True
    for x in (w, w2):
        x.close()


@pytest.mark.parametrize("bad", [None, "x", {}, {"v": 99}, {"v": 1}, {"v": 1, "state": "!!!", "chrome": {}},
                                 {"v": 1, "state": "AAAA", "chrome": {}}, {"v": 1, "state": "AAAA", "chrome": []},
                                 {"v": 1, "chrome": {}}])
def test_a_corrupt_blob_is_refused_and_changes_nothing(qapp, bad):
    w = _shown(qapp)
    before = _arrangement(w)
    assert w._apply_layout_blob(bad) is False
    assert _arrangement(w) == before
    w.close()


def test_saved_on_close_and_restored_on_start(qapp):
    w = _shown(qapp)
    w.enable_layout_persistence()
    w._dock_chrome["Model Explorer"]["set_closed"](True)
    w.close()
    assert settings.get("layout/docks")
    w2 = MainWindow()
    w2._dirty = False
    w2.enable_layout_persistence()
    w2.show()
    for _ in range(8):
        qapp.processEvents()
    import time
    time.sleep(0.05)
    qapp.processEvents()
    assert w2._dock_chrome["Model Explorer"]["get"]()["closed"] is True
    w2.close()


def test_show_results_restores_a_minimized_floating_results(qapp):
    """Show Results on a collapsed floating dock used to maximise the
    empty 36px shell; it must expand the content first."""
    w = _shown(qapp)
    d = w._results_dock
    w._dock_chrome["Results"]["set_minimized"](True)
    w._float_results_properly(d, True)
    w._show_results_full()
    for _ in range(4):
        qapp.processEvents()
    assert d.widget().isHidden() is False
    assert w._dock_chrome["Results"]["get"]()["minimized"] is False
    w._dirty = False
    w.close()


def test_floating_never_carries_a_titlebar_collapse(qapp):
    w = _shown(qapp)
    d = w._results_dock
    w._dock_chrome["Results"]["set_minimized"](True)
    w._float_results_properly(d, True)
    for _ in range(4):
        qapp.processEvents()
    assert d.widget().isHidden() is False
    assert w._dock_chrome["Results"]["get"]()["minimized"] is False
    w._dirty = False
    w.close()


def test_old_blobs_with_minimized_floating_results_restore_content(qapp):
    """A layout blob written by a build that allowed this combination
    used to reopen as an empty floating window. Repair it on load."""
    w = _shown(qapp)
    w._float_results_properly(w._results_dock, True)
    for _ in range(4):
        qapp.processEvents()
    blob = json.loads(json.dumps(w._layout_blob()))
    blob["chrome"]["Results"]["minimized"] = True
    w2 = _shown(qapp)
    assert w2._apply_layout_blob(blob) is True
    for _ in range(4):
        qapp.processEvents()
    assert w2._results_dock.isFloating()
    assert w2._results_dock.widget().isHidden() is False
    assert w2._dock_chrome["Results"]["get"]()["minimized"] is False
    for x in (w, w2):
        x._dirty = False
        x.close()


def test_bottom_dock_tab_labels_are_never_elided(qapp):
    """Floating/re-docking can create a fresh tab bar with ElideRight;
    Data Tables/Results/Run must keep their full names."""
    from PySide6.QtWidgets import QTabBar
    w = _shown(qapp)
    for floating in (True, False, True, False):
        w._float_results_properly(w._results_dock, floating)
        for _ in range(6):
            qapp.processEvents()
        for bar in w.findChildren(QTabBar):
            if bar.objectName() == "qt_tabwidget_tabbar":
                continue
            assert bar.elideMode() == Qt.TextElideMode.ElideNone
    w._dirty = False
    w.close()


def test_reset_layout_restores_the_results_tab_title(qapp):
    """Re-docking the Results window directly left the floating title
    ('Results — model') on the tab."""
    w = _shown(qapp)
    w.setWindowTitle("RAPTOR Studio — input.rbd")
    w._float_results_properly(w._results_dock, True)
    for _ in range(4):
        qapp.processEvents()
    assert w._results_dock.windowTitle() != "Results"
    w.reset_layout()
    for _ in range(4):
        qapp.processEvents()
    assert w._results_dock.windowTitle() == "Results"
    assert w._results_dock.isFloating() is False
    w._dirty = False
    w.close()


def test_an_unusable_saved_layout_is_forgotten(qapp):
    """Keeping a rejected blob made every launch retry the same restore."""
    settings.set_value("layout/docks", json.dumps({"v": 99}))
    w = MainWindow()
    w._dirty = False
    w.enable_layout_persistence()
    w.show()
    for _ in range(6):
        qapp.processEvents()
    assert settings.get("layout/docks") is None
    w.close()


def test_reset_layout_restores_areas_chrome_and_forgets_the_saved_layout(qapp):
    w = _shown(qapp)
    first = {d: w.dockWidgetArea(d) for d in (w._explorer_dock, w._inspector_dock, w._results_dock,
                                               w._data_tables_dock, w._run_dock)}
    w._dock_chrome["Model Explorer"]["set_closed"](True)
    w._dock_chrome["Results"]["set_minimized"](True)
    w._inspector_dock.setFloating(True)
    settings.set_value("layout/docks", "stale")
    w.reset_layout()
    for _ in range(4):
        qapp.processEvents()
    assert {d: w.dockWidgetArea(d) for d in first} == first
    assert not w._inspector_dock.isFloating()
    assert all(not c["get"]()["closed"] and not c["get"]()["minimized"] for c in w._dock_chrome.values())
    assert w._explorer_dock.isVisible() and w._inspector_dock.isVisible()
    assert settings.get("layout/docks") is None
    # the three bottom docks share one tabbed slot again, Results in front
    assert set(w.tabifiedDockWidgets(w._results_dock)) == {w._data_tables_dock, w._run_dock}
    assert w.dockWidgetArea(w._live_log_dock) == Qt.RightDockWidgetArea and not w._live_log_dock.isVisible()
    w.close()


def test_results_strip_is_resizable_after_the_run_tab_sized_itself(qapp):
    """The Run tab pins the shared bottom strip to its content height.

    That cap used to survive when Results was raised, so after opening
    Simulate the Results panel could never be made taller than the Run
    panel again. Results must keep the splitter free both ways, and Run
    must return to its own height when its tab comes back.
    """
    from pathlib import Path

    from raptor.core.fileio.rbd import load_rbd

    gold = Path(__file__).resolve().parents[1] / "golden" / "Phasing3" / "input.rbd"
    w = _shown(qapp)
    w._load_into_app(load_rbd(str(gold)), None)

    def pump():
        for _ in range(10):
            qapp.processEvents()

    pump()
    w._results._report_data = [object()]        # "a run finished"
    w._ribbon.tab_bar.setCurrentIndex(w._simulate_page_index)
    pump()

    run, results = w._run_dock, w._results_dock
    run_height = run.maximumHeight()
    assert run_height < w._max_bottom_dock_height()

    w._show_results_full()
    pump()
    assert results.height() > run_height + 50, \
        "the hidden Run tab still capped Results"

    w.resizeDocks([results], [200], Qt.Orientation.Vertical)
    pump()
    smaller = results.height()
    assert smaller < run_height

    w.resizeDocks([results], [400], Qt.Orientation.Vertical)
    pump()
    assert results.height() > smaller + 100

    run.raise_()
    pump()
    assert run.maximumHeight() == run_height
    assert run.height() <= run_height + 5
    w._dirty = False
    w.close()

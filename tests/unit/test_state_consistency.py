"""GUI state consistency across workflows.

Invariants when moving between ribbon tabs and after actions:
1. Opening another model clears results, undo stack, weak visuals, DSI.
2. Weak-link bands survive ribbon-tab round-trips and level traversal
   while the model is unchanged; the toggle-OFF forgets them.
3. Edit lock tracks the Model ribbon tab exactly.
4. Undo history never crosses a file boundary.
5. Validation badge re-evaluates after undo of an invalidating edit.
6. Step session is invalidated by edits and by opening a file."""

from __future__ import annotations

import os
import pathlib

import pytest

from PySide6.QtWidgets import QApplication, QGraphicsItem

from raptor.gui.ui.main_window import MainWindow

def pe(app=None):
    (QApplication.instance() or QApplication()).processEvents()

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def w(qapp):
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    app = QApplication.instance()
    app.processEvents()
    return win




def _pe(app):
    app.processEvents()


def test_opening_another_model_resets_everything(qapp, w):
    # Replacing the model in the same window (Preferences: open another
    # model in this window); the default opens a second window.
    from raptor.gui.ui import settings
    settings.set_value("files/open_in", "this")
    w._open_path(os.path.join(GOLD, "TwoBlockRedundancy",
                              "input.rbd"))
    w._run_count_spin.setValue(1)
    w._run_simulation(); pe(qapp)
    assert w._results._report_data
    v = w._diagram_dock.view

    # open a different model
    w._open_path(os.path.join(GOLD, "HierarchyExample", "input.rbd"))
    pe()
    assert not w._results._report_data, "stale cross-model results"
    assert v._stack.count() == 0, "undo crossed file boundary"
    assert getattr(v, "_last_result_visuals", None) is None
    assert getattr(v, "_last_hier_dsi", None) in (None, {})
    assert "OK" in w._validation_badge.text()   # revalidated on open


def test_weak_bands_survive_tab_roundtrip(qapp, w, monkeypatch):
    w._open_path(os.path.join(GOLD, "HierarchyExample", "input.rbd"))
    w._rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    w._analysis_actions["weakAnalysis"].setChecked(True)
    v = w._diagram_dock.view
    w._run_count_spin.setValue(1)
    # The weakAnalysis toggle marks the model dirty, so Run's
    # save-before-run prompt would otherwise pop a real modal and hang
    # headless. Patch `_confirm_discard_unsaved` itself: it builds its own
    # QMessageBox and calls exec(), so patching the static question()
    # convenience method would not intercept it.
    from raptor.gui.ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_confirm_discard_unsaved",
                        lambda self, **kw: True)
    w._run_simulation(); pe()
    w._toggle_weak_colors()          # ON
    fill_before = next(iter(v._block_items.values())).brush().color().name()

    # round-trip: leave Model tab and come back
    w._ribbon.tab_bar.setCurrentIndex(w._simulate_page_index); pe()
    w._ribbon.tab_bar.setCurrentIndex(w._model_page_index); pe()
    fill_after = next(iter(v._block_items.values())).brush().color().name()
    assert fill_after == fill_before, \
        f"weak bands lost on tab switch ({fill_before} -> {fill_after})"
    # and traversal keeps them too
    v.enter_level(23); pe()
    inside = next(iter(v._block_items.values())).brush().color().name()
    v.go_home(); pe()
    home_again = next(iter(v._block_items.values())).brush().color().name()
    assert inside == fill_before and home_again == fill_before


def test_toggle_off_forgets_bands(qapp, w, monkeypatch):
    w._open_path(os.path.join(GOLD, "HierarchyExample", "input.rbd"))
    w._rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    w._analysis_actions["weakAnalysis"].setChecked(True)
    v = w._diagram_dock.view
    w._run_count_spin.setValue(1)
    # As in test_weak_bands_survive_tab_roundtrip: the toggle marks the
    # model dirty, so patch `_confirm_discard_unsaved` rather than the
    # static question() convenience method, which the code does not use.
    from raptor.gui.ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_confirm_discard_unsaved",
                        lambda self, **kw: True)
    w._run_simulation(); pe()
    # A run no longer auto-enables a canvas overlay. Turn Weak Links on
    # explicitly, then one toggle turns it off and forgets.
    assert not getattr(v, "_weak_colors_on", False)
    w._toggle_weak_colors()                      # on
    assert getattr(v, "_weak_colors_on", False)
    w._toggle_weak_colors()                      # off -> forget
    assert getattr(v, "_last_result_visuals", None) is None
    v.enter_level(23); pe()
    fill = next(iter(v._block_items.values())).brush().color().name()
    themed_surface = __import__(
        "raptor.gui.resources.design_tokens",
        fromlist=["active_colour"]).active_colour()["surface"]
    # restored to themed look, not a band colour
    assert fill != "#218b61" or themed_surface == "#218b61"


def test_tabs_never_lock_editing(qapp, w):
    """Editing used to lock on the Simulate and Results tabs. A tab is a
    place to find commands, not a mode: what needs protecting is a
    displayed result set, which `_arm_results_guard` handles where the
    edit lands (see test_results_edit_guard.py)."""
    w._new_file(); pe()
    v = w._diagram_dock.view
    bid = next(b for b in v._block_items if b != 0)
    item = v._block_items[bid]
    for tab in (w._simulate_page_index, w._results_page_index,
                w._model_page_index):
        w._ribbon.tab_bar.setCurrentIndex(tab); pe()
        assert item.flags() & QGraphicsItem.ItemIsMovable


def test_validation_badge_after_undo(qapp, w):
    w._new_file(); pe()
    v = w._diagram_dock.view
    w._validate_model(quiet=True); pe()
    assert "OK" in w._validation_badge.text()
    # delete every block -> invalid
    for bid in [b for b in v._block_items if b != 0]:
        v._scene.clearSelection()
        v._block_items[bid].setSelected(True)
        v.delete_selected()
    pe(); w._validate_model(quiet=True); pe()
    assert "Invalid" in w._validation_badge.text()
    v._stack.undo(); pe()
    # debounce timer fires within 600 ms + margin
    loop = __import__("PySide6.QtCore", fromlist=["QEventLoop"]).QEventLoop()
    __import__("PySide6.QtCore", fromlist=["QTimer"]).QTimer.singleShot(
        900, loop.quit)
    loop.exec()
    assert "OK" in w._validation_badge.text(), "undo did not refresh badge"


def test_step_session_invalidated_by_open(qapp, w):
    w._new_file(); pe()
    w._ensure_step_session()
    assert w._step_session is not None
    w._open_path(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    pe()
    assert w._step_session is None, "step session crossed a file open"

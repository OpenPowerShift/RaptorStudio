"""Commands that act on the selection follow it through the derived state.

Align & Distribute and Mass Edit used to be enabled from a model opening
(Mass Edit, and it just told you to select more) or by a hand-written
line in a selection handler (Align). Both are now predicates over
`AppState`, so the greyed-out state, the reason in the tooltip and the
results lock all come from one place.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.gui.commands import PREDICATES, AppState, unmet_reason

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"


class TestPredicates:
    def test_align_needs_two_movable_items_and_no_results_lock(self):
        p = PREDICATES["can_align"]
        base = dict(model_open=True)
        assert not p(AppState(**base, selected_movable=1))
        assert p(AppState(**base, selected_movable=2))
        assert not p(AppState(selected_movable=5))          # no model

    def test_mass_edit_needs_two_elements(self):
        p = PREDICATES["can_mass_edit"]
        assert not p(AppState(model_open=True, selected_elements=1))
        assert p(AppState(model_open=True, selected_elements=2))
        assert not p(AppState(selected_elements=9))

    @pytest.mark.parametrize("state,expect", [
        (AppState(), "open or create a model first"),
        (AppState(model_open=True, selected_movable=1),
         "select two or more"),
    ])
    def test_the_reason_says_what_to_do(self, state, expect):
        assert expect in unmet_reason("can_align", state)


pytestmark_gui = pytest.mark.gui


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    qapp.processEvents()
    yield w
    w._dirty = False
    w.close()


def _select(win, n):
    v = win._diagram_dock.view
    v._scene.clearSelection()
    for bid in sorted(b for b in v.blocks if b)[:n]:
        v._block_items[bid].setSelected(True)
    QApplication.processEvents()


@pytest.mark.gui
def test_the_commands_follow_the_selection(win):
    assert not win._mass_edit_action.isEnabled()
    assert not win._align_btn.isEnabled()
    _select(win, 1)
    assert not win._mass_edit_action.isEnabled()
    assert not win._align_btn.isEnabled()
    _select(win, 2)
    assert win._mass_edit_action.isEnabled()
    assert win._align_btn.isEnabled()
    _select(win, 0)
    assert not win._mass_edit_action.isEnabled()
    assert not win._align_btn.isEnabled()


@pytest.mark.gui
def test_a_disabled_command_says_why(win):
    assert "Unavailable: select two or more elements" in \
        win._mass_edit_action.toolTip()
    _select(win, 2)
    assert "Unavailable" not in win._mass_edit_action.toolTip()


@pytest.mark.gui
def test_closing_the_model_disables_both(win):
    _select(win, 2)
    win._dirty = False
    win._close_current_file()
    assert not win._mass_edit_action.isEnabled()
    assert not win._align_btn.isEnabled()


# -- Zoom to Hierarchy --------------------------------------------------

@pytest.mark.gui
def test_zoom_hierarchy_needs_a_selected_hierarchy(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    path = "tests/golden/HierarchyExample/input.rbd"
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(path), path)
    qapp.processEvents()
    v = w._diagram_dock.view
    assert v._hier_items, "fixture needs a hierarchy at the top level"
    assert not w._zoom_hierarchy_action.isEnabled()
    assert "select a hierarchy oval" in w._zoom_hierarchy_action.toolTip()
    v._scene.clearSelection()
    next(iter(v._hier_items.values())).setSelected(True)
    qapp.processEvents()
    assert w._zoom_hierarchy_action.isEnabled()
    v._scene.clearSelection()
    next(iter(v._block_items.values())).setSelected(True)
    qapp.processEvents()
    assert not w._zoom_hierarchy_action.isEnabled()      # a block is not one
    w._dirty = False
    w.close()


@pytest.mark.gui
def test_zoom_hierarchy_says_when_there_are_none(win):
    assert not win._diagram_dock.view._hier_items
    assert not win._zoom_hierarchy_action.isEnabled()
    assert "no hierarchies at this level" in \
        win._zoom_hierarchy_action.toolTip()


def test_zoom_hierarchy_reasons():
    from raptor.gui.commands import AppState, unmet_reason
    assert "no hierarchies" in unmet_reason(
        "can_zoom_hierarchy", AppState(model_open=True))
    assert "select a hierarchy" in unmet_reason(
        "can_zoom_hierarchy", AppState(model_open=True, hierarchies_here=2))
    assert "open or create" in unmet_reason(
        "can_zoom_hierarchy", AppState())


@pytest.mark.gui
def test_zoom_hierarchy_follows_a_hierarchy_being_removed_and_restored(qapp):
    """Deleting the last hierarchy must grey the command out with the right
    reason, and undo must bring it back -- neither depends on the selection
    changing."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    path = "tests/golden/SingleHierarchy/input.rbd"
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(path), path)
    qapp.processEvents()
    v = w._diagram_dock.view
    hids = sorted(v._hier_items)
    assert len(hids) == 1
    v._scene.clearSelection()
    v._hier_items[hids[0]].setSelected(True)
    qapp.processEvents()
    assert w._zoom_hierarchy_action.isEnabled()

    v._dissolve_hierarchies(hids)                 # remove it
    qapp.processEvents()
    qapp.processEvents()                          # the deferred refresh
    assert not v._hier_items
    assert not w._zoom_hierarchy_action.isEnabled()
    assert "no hierarchies at this level" in w._zoom_hierarchy_action.toolTip()

    v._stack.undo()                               # and restore it
    qapp.processEvents()
    qapp.processEvents()
    assert v._hier_items
    assert "select a hierarchy oval" in w._zoom_hierarchy_action.toolTip()
    v._hier_items[sorted(v._hier_items)[0]].setSelected(True)
    qapp.processEvents()
    assert w._zoom_hierarchy_action.isEnabled()
    w._dirty = False
    w.close()


@pytest.mark.gui
def test_zoom_hierarchy_follows_a_hierarchy_being_created(win):
    v = win._diagram_dock.view
    assert not win._zoom_hierarchy_action.isEnabled()
    assert "no hierarchies" in win._zoom_hierarchy_action.toolTip()
    hid = v.add_hierarchy("H", 5.0, 5.0)
    QApplication.instance().processEvents()
    QApplication.instance().processEvents()
    assert "select a hierarchy oval" in win._zoom_hierarchy_action.toolTip()
    v._scene.clearSelection()
    v._hier_items[hid].setSelected(True)
    QApplication.instance().processEvents()
    assert win._zoom_hierarchy_action.isEnabled()

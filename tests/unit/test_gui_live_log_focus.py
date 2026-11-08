"""Focus Event selects and reveals the matching row in the Live Log.

The row must be the event just described (not simply the last row), the
run being stepped must be brought back on screen when an earlier run is
being read, and with Focus Event off the user's selection/scroll must not
move.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.api.tables import parse_event_log_line
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    rbd = load_rbd(FIXTURE)
    rbd.section("SYSTEM_DATA").rows[0]["TermCondition"] = "200"
    rbd.section("SYSTEM_DATA").rows[0]["NumRuns"] = "3"
    w._load_into_app(rbd, None)
    w.show()
    w._live_sim_action.setChecked(True)
    qapp.processEvents()
    yield w
    w._invalidate_step_session()
    w._dirty = False
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _rows(panel):
    return [panel.tree.topLevelItem(i)
            for i in range(panel.tree.topLevelItemCount())]


def _visible(panel, item) -> bool:
    return bool(panel.tree.viewport().rect().intersects(
        panel.tree.visualItemRect(item)))


def test_focus_event_selects_and_reveals_a_middle_row(window, qapp):
    """The API is not just "scroll to bottom": a specific row wins."""
    panel = window._live_log_panel
    for _ in range(25):
        window._on_step()
    qapp.processEvents()
    rows = _rows(panel)
    assert len(rows) > 5

    panel.tree.scrollToTop()
    qapp.processEvents()
    index = len(window._step_session.logs) - 3
    target = rows[index - panel._cut]

    assert panel.focus_event(index)
    assert panel.tree.currentItem() is target
    assert _visible(panel, target), "the selected row was not scrolled into view"


def test_focus_event_uses_the_described_row_not_the_last_one(
        window, qapp):
    """A trailing System bookkeeping line must not be the selected row."""
    panel = window._live_log_panel
    window._on_step()
    session = window._step_session
    view = window._diagram_dock.view
    name = next(iter(view.blocks.values())).name
    first_new = len(session.logs)
    session.logs.extend([
        f"999.000\tBlock\t{name}\tUp\t",
        "999.000\tSystem\tBookkeeping\t--\t",
    ])
    panel.update_session([], session.logs, 1, 999.0)
    qapp.processEvents()

    window._describe_and_focus_step(session, {}, first_new)
    qapp.processEvents()
    rows = _rows(panel)
    selected = panel.tree.currentItem()
    assert selected is not None
    assert selected is not rows[-1], "the trailing System row was selected"
    assert selected.text(1) == "Block" and selected.text(2) == name
    assert _visible(panel, selected)


def test_focus_event_returns_to_the_run_being_stepped(window, qapp):
    panel = window._live_log_panel
    window._on_step()
    window._on_step_jump()
    window._on_step()
    qapp.processEvents()

    panel.prev_button.click()
    qapp.processEvents()
    assert not panel.following, "test needs an earlier run on screen"

    window._on_step()
    qapp.processEvents()
    assert panel.following
    assert panel.run_combo.currentIndex() == window._step_session.run_index
    selected = panel.tree.currentItem()
    assert selected is not None
    assert panel.tree.indexOfTopLevelItem(selected) >= 0
    assert _visible(panel, selected)


def test_focus_event_updates_the_log_with_animate_off(window, qapp):
    """Focus Event is a live-log feature: stepping must feed the panel
    even when the canvas animation is off, or the selected row is stale."""
    panel = window._live_log_panel
    window._live_sim_action.setChecked(False)
    window._focus_event_action.setChecked(True)
    window._on_step()
    qapp.processEvents()
    session = window._step_session
    assert panel.tree.topLevelItemCount() == len(session.logs)
    selected = panel.tree.currentItem()
    assert selected is not None
    selected_fields = tuple(selected.text(i) for i in range(5))
    assert any(tuple(parse_event_log_line(line)) == selected_fields
               for line in session.logs)
    assert _visible(panel, selected)


def test_focus_event_selects_the_row_after_an_automatic_rollover(
        window, qapp):
    """Stepping off the end of run 1 jumps to run 2 itself; Focus Event
    must use the NEW run's line count, not the replaced log's length."""
    panel = window._live_log_panel
    window._focus_event_action.setChecked(True)
    window._on_step()
    session = window._step_session
    assert session.has_next_run

    window._end_of_stepped_run(session)
    qapp.processEvents()
    assert session.run_index == 1
    assert panel.following
    assert panel.tree.topLevelItemCount() == len(session.logs)
    selected = panel.tree.currentItem()
    assert selected is not None, "rollover did not focus the new run's event"
    assert _visible(panel, selected)


def test_focus_off_keeps_the_previous_run_at_a_rollover(window, qapp):
    panel = window._live_log_panel
    window._focus_event_action.setChecked(False)
    window._on_step()
    session = window._step_session
    assert session.has_next_run

    window._end_of_stepped_run(session)
    qapp.processEvents()
    assert session.run_index == 1
    assert panel.shown_index == 0 and not panel.following
    assert len(_rows(panel)) == len(session.completed[0][0])


def test_focus_off_leaves_selection_and_scroll_alone(window, qapp):
    panel = window._live_log_panel
    for _ in range(20):
        window._on_step()
    qapp.processEvents()
    panel.tree.scrollToTop()
    qapp.processEvents()
    first = _rows(panel)[0]
    panel.tree.setCurrentItem(first)
    bar = panel.tree.verticalScrollBar()
    before = bar.value()

    window._focus_event_action.setChecked(False)
    window._on_step()
    qapp.processEvents()

    assert panel.tree.currentItem() is first
    assert bar.value() == before

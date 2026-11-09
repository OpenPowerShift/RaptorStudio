"""The Live Log of a stepped simulation follows the run being stepped, keeps
the finished runs reachable, and marks the rows of the latest step."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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
    w.close()
    w.deleteLater()


def _rows(panel):
    return [panel.tree.topLevelItem(i)
            for i in range(panel.tree.topLevelItemCount())]


def _highlighted(panel):
    return [r for r in _rows(panel) if r.font(0).bold()]


def test_the_log_follows_every_step(window, qapp):
    panel = window._live_log_panel
    seen = []
    for _ in range(3):
        window._on_step()
        qapp.processEvents()
        seen.append(len(_rows(panel)))
        assert len(_rows(panel)) == len(window._step_session.logs)
    assert seen == sorted(seen) and seen[-1] > seen[0]


def test_the_next_run_shows_its_own_rows_not_the_old_ones(window, qapp):
    """Jump used to leave run 1's rows on screen: the fed-line counter was
    never reset, so run 2's shorter log never reached the tree."""
    panel = window._live_log_panel
    for _ in range(3):
        window._on_step()
    run1_rows = len(_rows(panel))
    window._on_step_jump()
    qapp.processEvents()
    session = window._step_session
    assert session.run_index == 1
    assert len(_rows(panel)) == len(session.logs) != run1_rows
    window._on_step()
    qapp.processEvents()
    assert len(_rows(panel)) == len(session.logs)
    assert panel.run_combo.currentIndex() == 1
    assert "Run 2 of 3" in panel.status.text()


def test_an_earlier_run_can_be_looked_at_while_stepping_on(window, qapp):
    panel = window._live_log_panel
    window._on_step()
    window._on_step_jump()
    window._on_step()
    qapp.processEvents()
    finished = window._step_session.completed[0][0]
    assert panel.run_combo.count() == 2
    panel.prev_button.click()
    qapp.processEvents()
    assert panel.shown_index == 0 and not panel.following
    assert len(_rows(panel)) == len(finished)
    assert _rows(panel)[0].text(0) == finished[0].split("\t")[0].strip()
    assert not _highlighted(panel), "a finished run has no current event"
    # Stepping on leaves the run on screen alone...
    before = [r.text(0) for r in _rows(panel)]
    window._on_step()
    qapp.processEvents()
    assert [r.text(0) for r in _rows(panel)] == before
    assert panel.run_combo.currentIndex() == 0
    # ...and going back to the current run shows the newest rows again.
    panel.next_button.click()
    qapp.processEvents()
    assert panel.following
    assert len(_rows(panel)) == len(window._step_session.logs)
    assert _highlighted(panel)


def test_the_latest_steps_rows_are_highlighted_and_it_moves(window, qapp):
    panel = window._live_log_panel
    window._on_step()
    qapp.processEvents()
    n1 = len(_rows(panel))
    first = _highlighted(panel)
    assert first and len(first) == n1
    window._on_step()
    qapp.processEvents()
    rows = _rows(panel)
    now = _highlighted(panel)
    assert now and len(now) == len(rows) - n1
    assert all(r in rows[n1:] for r in now)
    assert not any(r.font(0).bold() for r in rows[:n1])
    # The tint is the theme's accent, translucent so state colours show.
    tint = now[0].background(0).color()
    assert 0 < tint.alpha() < 255


def test_the_newest_row_is_scrolled_into_view(window, qapp):
    panel = window._live_log_panel
    window.resize(1400, 500)
    for _ in range(25):
        window._on_step()
    qapp.processEvents()
    bar = panel.tree.verticalScrollBar()
    assert bar.value() == bar.maximum()


def test_the_tree_is_capped_and_says_so(window, qapp, monkeypatch):
    from raptor.gui.ui import live_log_panel
    monkeypatch.setattr(live_log_panel, "ROW_CAP", 6)
    for _ in range(6):
        window._on_step()
    qapp.processEvents()
    panel = window._live_log_panel
    assert len(_rows(panel)) == 6
    assert len(window._step_session.logs) > 6
    assert "newest 6" in panel.status.text()


def test_the_results_event_log_lists_every_run_so_far(window, qapp):
    window._results.show()
    window._on_step()
    window._on_step_jump()
    window._on_step()
    qapp.processEvents()
    assert len(window._results._event_logs) == 2


def test_closing_the_session_clears_the_panel(window, qapp):
    window._on_step()
    window._invalidate_step_session()
    panel = window._live_log_panel
    assert panel.tree.topLevelItemCount() == 0 and panel.run_combo.count() == 0


def test_the_live_log_action_is_disabled_and_hidden_before_a_run(window, qapp):
    action = window._live_log_toggle_action
    assert not action.isEnabled()
    assert "Unavailable" in action.toolTip()
    assert not window._live_log_dock.isVisible()


def test_starting_a_step_shows_the_live_log_and_checks_its_action(window, qapp):
    action = window._live_log_toggle_action
    window._on_step()
    qapp.processEvents()
    assert action.isEnabled()
    assert action.isChecked()
    assert window._live_log_dock.isVisible()
    assert window._live_log_dock in \
        window.tabifiedDockWidgets(window._inspector_dock)


def test_toggling_the_live_log_action_hides_and_restores_it(window, qapp):
    action = window._live_log_toggle_action
    dock = window._live_log_dock
    window._on_step()
    qapp.processEvents()
    assert dock.isVisible()

    action.setChecked(False)
    qapp.processEvents()
    assert not dock.isVisible()
    assert window._inspector_dock.isVisible()

    action.setChecked(True)
    qapp.processEvents()
    assert dock.isVisible()
    assert dock in window.tabifiedDockWidgets(window._inspector_dock)


def test_new_model_returns_to_the_inspector_and_hides_the_live_log(
        window, qapp, monkeypatch):
    window._on_step()
    qapp.processEvents()
    assert window._live_log_dock.isVisible()
    monkeypatch.setattr(window, "_confirm_discard_unsaved",
                        lambda **kwargs: True)
    window._new_file()
    for _ in range(6):
        qapp.processEvents()
    assert not window._live_log_dock.isVisible()
    assert window._inspector_dock.isVisible()
    assert not window._live_log_toggle_action.isChecked()
    assert not window._live_log_toggle_action.isEnabled()
    assert window._live_log_panel.tree.topLevelItemCount() == 0


def test_reset_layout_keeps_the_live_log_hidden(window, qapp):
    window._on_step()
    qapp.processEvents()
    assert window._live_log_dock.isVisible()
    window.reset_layout()
    for _ in range(8):
        qapp.processEvents()
    assert not window._live_log_dock.isVisible()
    assert window._inspector_dock.isVisible()
    assert not window._live_log_toggle_action.isChecked()

"""Stepping with Animate off still colours the canvas and focuses the
Live Log row for the event just described -- across several events, a
hierarchy target with no current-level item, and an automatic run rollover.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests", "golden", "HierarchyExample", "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    rbd = load_rbd(FIXTURE)
    rbd.section("SYSTEM_DATA").rows[0]["TermCondition"] = "100"
    rbd.section("SYSTEM_DATA").rows[0]["NumRuns"] = "2"
    w._load_into_app(rbd, None)
    w.show()
    w._live_sim_action.setChecked(False)      # Animate off
    w._focus_event_action.setChecked(True)
    qapp.processEvents()
    yield w
    w._invalidate_step_session()
    w._dirty = False
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _latest_described(session, prev_lines):
    """The row the status/focus should describe: newest non-System line,
    or the newest System line when that is all the step logged."""
    parsed = []
    for index in range(prev_lines, len(session.logs)):
        parts = [p.strip() for p in session.logs[index].split("\t")]
        if len(parts) >= 4:
            parsed.append((index, parts))
    for index, parts in reversed(parsed):
        if parts[1] != "System" and parts[2] not in ("--", ""):
            return index, parts
    return parsed[-1] if parsed else (None, None)


def test_animate_off_colours_and_focuses_the_described_event(window, qapp):
    view = window._diagram_dock.view
    panel = window._live_log_panel
    prev_lines = 0
    prev_run = 0
    saw_hierarchy_target = False
    saw_non_last_row = False

    for _ in range(40):
        window._on_step()
        qapp.processEvents()
        session = window._step_session
        if session is None:
            break
        if session.run_index != prev_run:
            # The log list was replaced by the next trial.
            prev_lines = 0
            prev_run = session.run_index
        index, parts = _latest_described(session, prev_lines)
        prev_lines = len(session.logs)
        if index is None:
            continue

        # (a) Animate off still paints/caches live block colours.
        assert view._live_state_cache is not None
        live_item = next(iter(view._block_items.values()))
        assert live_item._live_active and live_item._live_colour

        # (b) The selected row is the event described by the status bar,
        # not merely the last row in the tree.
        selected = panel.tree.currentItem()
        assert selected is not None, f"no Live Log row selected at t={session.time}"
        assert (selected.text(0), selected.text(1), selected.text(2),
                selected.text(3)) == tuple(parts[:4])
        selected_row = panel.tree.indexOfTopLevelItem(selected) + panel._cut
        assert selected_row == index
        description = f"{parts[1]} {parts[2]} → {parts[3]}"
        assert description in window._statusbar.currentMessage()
        if index != len(session.logs) - 1:
            saw_non_last_row = True
        if parts[1] == "Block" and panel.tree.indexOfTopLevelItem(
                selected) >= 0:
            name = parts[2]
            on_level = any(
                b.name == name for b in view.blocks.values())
            if not on_level:
                saw_hierarchy_target = True

        # Keep the run short after the rollover has been exercised.
        if session.run_index >= 1 and prev_lines > 8:
            break

    assert view._live_state_cache is not None
    assert saw_non_last_row, "no step had trailing System lines to skip"
    assert saw_hierarchy_target, "no hierarchy-only target was exercised"


def test_finishing_clears_the_animate_off_live_colours(window, qapp):
    view = window._diagram_dock.view
    window._on_step()
    qapp.processEvents()
    assert view._live_state_cache is not None

    window._on_step_finish()
    qapp.processEvents()
    assert view._live_state_cache is None

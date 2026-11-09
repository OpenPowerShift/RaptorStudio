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
from raptor.gui.ui import msgbox
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
        parent_before = view._current_parent
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
        if view._current_parent != parent_before:
            saw_hierarchy_target = True

        # (a) Animate off still paints/caches live block colours.
        assert view._live_state_cache is not None
        live_item = next(iter(view._block_items.values()))
        assert live_item._live_active and live_item._live_colour
        assert not window._system_swatch.isHidden(), \
            "Animate-off stepping must show the system-status square"

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
        if parts[1] == "Block":
            # Focus on Event must reveal the block's own level, even when
            # it lives inside a hierarchy and was absent from the level
            # shown before this step.
            name = parts[2]
            assert any(b.name == name for b in view.blocks.values()), (
                f"focus did not reveal block {name!r}")

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


def test_hier_log_row_selects_the_hierarchy_row(window, qapp):
    view = window._diagram_dock.view
    panel = window._live_log_panel
    window._on_step()
    session = window._step_session
    name = next(iter(view.hiers.values())).name
    first = len(session.logs)
    session.logs.append(f"{session.time:.6f}\tHier\t{name}\tDown\t")
    panel.update_session([], session.logs, 1, session.time)
    window._describe_and_focus_step(session, {}, first)
    qapp.processEvents()

    selected = panel.tree.currentItem()
    assert selected is not None
    assert (selected.text(1), selected.text(2), selected.text(3)) == (
        "Hier", name, "Down")
    assert f"Hier {name} → Down" in window._statusbar.currentMessage()


def test_jump_selects_the_exact_row_and_reports_it(window, qapp):
    panel = window._live_log_panel
    window._on_step()
    window._on_step_jump()
    qapp.processEvents()
    session = window._step_session
    assert session.run_index == 1
    index, parts = _latest_described(session, 0)
    selected = panel.tree.currentItem()
    assert selected is not None
    assert (selected.text(0), selected.text(1), selected.text(2),
            selected.text(3)) == tuple(parts[:4])
    assert panel.tree.indexOfTopLevelItem(selected) + panel._cut == index
    assert f"{parts[1]} {parts[2]} → {parts[3]}" \
        in window._statusbar.currentMessage()


def test_multi_paints_once_and_focuses_the_final_event(
        window, qapp, monkeypatch):
    view = window._diagram_dock.view
    panel = window._live_log_panel
    session = window._ensure_step_session()
    prev_lines = len(session.logs)
    calls = []
    original = view.update_live_states

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(view, "update_live_states", counted)
    monkeypatch.setattr(window._run_panel, "step_count", lambda: 20)
    window._on_step_multi()
    qapp.processEvents()
    session = window._step_session
    assert len(calls) == 1, "Multi painted once per internal event"
    index, parts = _latest_described(session, prev_lines)
    selected = panel.tree.currentItem()
    assert selected is not None
    assert (selected.text(0), selected.text(1), selected.text(2),
            selected.text(3)) == tuple(parts[:4])
    assert panel.tree.indexOfTopLevelItem(selected) + panel._cut == index
    assert f"{parts[1]} {parts[2]} → {parts[3]}" \
        in window._statusbar.currentMessage()


def test_run_to_paints_once_and_focuses_the_final_event(
        window, qapp, monkeypatch):
    view = window._diagram_dock.view
    panel = window._live_log_panel
    session = window._ensure_step_session()
    prev_lines = len(session.logs)
    calls = []
    original = view.update_live_states

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(view, "update_live_states", counted)
    monkeypatch.setattr(window._run_panel, "run_to_time", lambda: 20.0)
    window._on_run_to()
    qapp.processEvents()
    session = window._step_session
    assert session.time >= 20.0
    assert len(calls) == 1, "Run-To painted once per internal event"
    index, parts = _latest_described(session, prev_lines)
    selected = panel.tree.currentItem()
    assert selected is not None
    assert (selected.text(0), selected.text(1), selected.text(2),
            selected.text(3)) == tuple(parts[:4])
    assert panel.tree.indexOfTopLevelItem(selected) + panel._cut == index
    assert f"{parts[1]} {parts[2]} → {parts[3]}" \
        in window._statusbar.currentMessage()


def test_a_failed_step_clears_live_colours_and_the_live_log(
        window, qapp, monkeypatch):
    view = window._diagram_dock.view
    panel = window._live_log_panel
    window._on_step()
    qapp.processEvents()
    assert view._live_state_cache is not None
    assert panel.tree.topLevelItemCount() > 0
    session = window._step_session

    def fail():
        raise RuntimeError("fake step failure")

    monkeypatch.setattr(session, "step", fail)
    errors = []
    monkeypatch.setattr(msgbox, "critical",
                        lambda *a, **k: errors.append(a))
    window._on_step()
    qapp.processEvents()

    assert errors and errors[0][1] == "Step Failed"
    assert window._step_session is None
    assert view._live_state_cache is None
    assert panel.tree.topLevelItemCount() == 0

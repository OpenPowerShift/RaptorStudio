"""Stepping without a trip to another ribbon tab: the Debug tab opens when a
stepped run starts, and the stepping verbs have debugger-convention keys."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent.parent
MODEL = str(REPO / "new_docs" / "models" / "Bakery1.rbds")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    window = MainWindow()
    window._load_into_app(load_rbds(MODEL), MODEL)
    qapp.processEvents()
    window._dirty = False
    return window


def _tab(window) -> str:
    bar = window._ribbon.tab_bar
    return bar.tabText(bar.currentIndex())


def test_starting_a_stepped_run_opens_the_debug_tab(qapp, win):
    win._ribbon.select_page("Model")
    win._step_action.trigger()
    qapp.processEvents()
    assert win._step_session is not None
    assert _tab(win) == "Debug"
    # A later step leaves the tab the user has chosen since alone.
    win._ribbon.select_page("View")
    win._step_action.trigger()
    qapp.processEvents()
    assert _tab(win) == "View"
    win._invalidate_step_session()


@pytest.mark.parametrize("attr, key", [
    ("_step_finish_action", "F8"),
    ("_terminate_action", "Shift+F5"),
    ("_restart_step_action", "Ctrl+Shift+F5"),
    ("_step_change_action", "F10"),
    ("_pause_action", "F9"),
])
def test_the_stepping_verbs_have_their_keys(win, attr, key):
    action = getattr(win, attr)
    assert action.shortcut().toString() == key
    # Reachable with its menu closed: the action belongs to the window.
    assert action in win.actions() or action.parent() is win


def test_no_two_actions_share_a_key(win):
    seen: dict[str, str] = {}
    for action in win.findChildren(type(win._run_action)):
        for shortcut in action.shortcuts():
            key = shortcut.toString()
            if not key:
                continue
            assert seen.setdefault(key, action.text()) == action.text(), (
                f"{key} is bound to both {seen[key]!r} and {action.text()!r}")

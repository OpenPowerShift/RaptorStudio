"""Alt key tips: File and each tab, then each button of the tab.

The letters are data (`raptor/gui/key_tips.py`), never derived from a label.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from raptor.gui.command_table import COMMANDS, LAYOUT
from raptor.gui.commands import Btn
from raptor.gui.key_tips import FILE_KEY_TIP, KEY_TIPS, TAB_KEY_TIPS


def test_file_is_f_and_the_tab_letters_are_mnemonic_and_unique():
    assert FILE_KEY_TIP == "F"
    assert TAB_KEY_TIPS["Results"] == "R" and TAB_KEY_TIPS["Simulate"] == "S"
    tops = [FILE_KEY_TIP, *TAB_KEY_TIPS.values()]
    assert len(tops) == len(set(tops))
    assert set(TAB_KEY_TIPS) == {p.title for p in LAYOUT}


def test_every_ribbon_button_has_a_letter_unique_on_its_tab():
    for page in LAYOUT:
        ids = [i.id for g in page.groups for i in g.items
               if isinstance(i, Btn)]
        tips = KEY_TIPS[page.title]
        assert set(tips) == set(ids), page.title
        letters = list(tips.values())
        assert len(letters) == len(set(letters)), page.title
        assert all(len(t) == 1 and t.isalpha() and t.isupper()
                   for t in letters)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    m = "tests/golden/TwoBlockRedundancy/input.rbd"
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(m), m)
    qapp.processEvents()
    yield w
    w._dirty = False
    w.close()


def _press(win, letter):
    ev = QKeyEvent(QEvent.Type.KeyPress, getattr(Qt.Key, f"Key_{letter}"),
                   Qt.KeyboardModifier.NoModifier, letter.lower())
    return win._key_press_event_filter(win, ev)


def _badges(win):
    return sorted(b.text() for b in win._key_tip_badges)


@pytest.mark.gui
def test_alt_letters_file_and_every_tab_then_alt_again_hides(win):
    win._show_key_tips()
    assert _badges(win) == sorted([FILE_KEY_TIP, *TAB_KEY_TIPS.values()])
    assert all(b.isVisible() for b in win._key_tip_badges)
    win._show_key_tips()
    assert not win._key_tips_active and not win._key_tip_badges


@pytest.mark.gui
def test_f_opens_the_file_view(win):
    win._show_key_tips()
    assert _press(win, "F")
    assert win._backstage.isVisible() and not win._key_tips_active
    win._backstage.close_view()


@pytest.mark.gui
def test_a_tab_letter_opens_the_tab_and_letters_its_buttons(win):
    win._show_key_tips()
    assert _press(win, "R")
    assert win._ribbon.tab_bar.tabText(
        win._ribbon.tab_bar.currentIndex()) == "Results"
    assert win._key_tips_active
    assert "S" in _badges(win) and "C" in _badges(win)
    win._dismiss_key_tips()


@pytest.mark.gui
def test_the_model_tab_letters_the_element_tools(win):
    win._show_key_tips()
    _press(win, "M")
    shown = set(_badges(win))
    for cid in ("model.add_block", "model.add_node", "model.add_event",
                "model.add_hier", "model.connect"):
        assert KEY_TIPS["Model"][cid] in shown, cid
    win._dismiss_key_tips()


@pytest.mark.gui
def test_the_second_letter_runs_the_command(win):
    action = win._commands.action("sim.live_sim")
    before = action.isChecked()
    win._show_key_tips()
    _press(win, "S")
    assert _press(win, KEY_TIPS["Simulate"]["sim.live_sim"])
    assert action.isChecked() != before
    assert not win._key_tips_active and not win._key_tip_badges


@pytest.mark.gui
def test_a_letter_for_an_unavailable_command_says_so_instead(win):
    win._show_key_tips()
    _press(win, "S")
    assert not win._commands.action("sim.stop_run").isEnabled()
    assert _press(win, KEY_TIPS["Simulate"]["sim.stop_run"])
    assert "not available" in win._statusbar.currentMessage()


@pytest.mark.gui
def test_an_unknown_letter_stays_and_backspace_goes_up_a_level(win):
    win._show_key_tips()
    _press(win, "R")
    assert _press(win, "J") and win._key_tips_active   # no J on Results
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace,
                   Qt.KeyboardModifier.NoModifier, "")
    assert win._key_press_event_filter(win, ev)
    assert _badges(win) == sorted([FILE_KEY_TIP, *TAB_KEY_TIPS.values()])
    win._dismiss_key_tips()


@pytest.mark.gui
def test_alt_held_with_a_tab_letter_goes_straight_to_that_tab(win):
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_R,
                   Qt.KeyboardModifier.AltModifier, "r")
    win.keyPressEvent(ev)
    assert win._key_tips_active and win._key_tip_page == "Results"
    win._dismiss_key_tips()


@pytest.mark.gui
def test_a_letter_reaches_the_tips_from_whichever_widget_has_focus(win, qapp):
    """REGRESSION: the window filtered its OWN events, but the key goes to
    the focused widget (the canvas), so the tips showed and no letter did
    anything. The press is sent to the canvas here, as a user's would be."""
    from PySide6.QtTest import QTest
    view = win._diagram_dock.view
    win.raise_()
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 2000)
    view.setFocus()
    QTest.qWait(20)
    win._show_key_tips()
    QTest.qWait(10)
    assert QApplication.focusWidget() is not win
    # Through the window system, as a real key press: Qt routes it to the
    # focused widget unless something holds the keyboard.
    QTest.keyClick(win.windowHandle(), getattr(Qt.Key, f"Key_{FILE_KEY_TIP}"))
    assert getattr(win, "_backstage", None) is not None and win._backstage.isVisible()
    assert not win._key_tips_active
    win._backstage.close_view()


@pytest.mark.gui
def test_a_bare_modifier_does_not_dismiss_the_tips(win):
    win._show_key_tips()
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Shift,
                   Qt.KeyboardModifier.ShiftModifier, "")
    assert not win._key_press_event_filter(win, ev)
    assert win._key_tips_active
    win._dismiss_key_tips()


@pytest.mark.parametrize("attr", ["_open_action", "_new_action",
                                  "_run_action", "_fit_action"])
def test_a_shortcut_works_whatever_tab_is_showing(attr):
    """Shortcuts used to work only while their ribbon button was on screen
    (F5 only on Simulate; Ctrl+O and Ctrl+N never)."""
    from PySide6.QtTest import QTest
    from raptor.gui.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    from raptor.core.fileio.rbds import load_rbds
    w = MainWindow()
    w.show()
    w.activateWindow()
    QTest.qWaitForWindowActive(w, 2000)
    QTest.qWait(10)
    try:
        m = "new_docs/models/Espresso1.rbds"
        w._load_into_app(load_rbds(m), m)
        bs = getattr(w, "_backstage", None)    # built on first use
        if bs is not None and bs.isVisible():
            bs.close_view()
        # Loading can deactivate the window (the busy dialog takes
        # activation); a WindowShortcut only fires for the active window.
        w.activateWindow()
        QTest.qWaitForWindowActive(w, 2000)
        QTest.qWait(5)
        act = getattr(w, attr)
        hits = []
        act.triggered.disconnect()
        act.triggered.connect(lambda *_: hits.append(1))
        for i in range(w._ribbon.tab_bar.count()):
            w._ribbon.tab_bar.setCurrentIndex(i)
            app.processEvents()
            QTest.qWait(5)
            seq = act.shortcut()
            QTest.keySequence(w, seq)
            # Let the shortcut map deliver the event before the next tab is
            # selected: a single processEvents() left the last tab's press
            # queued, so the count came out one short.
            QTest.qWait(5)
            app.processEvents()
        assert len(hits) == w._ribbon.tab_bar.count(), (attr, len(hits))
    finally:
        w._dirty = False
        w.close()


@pytest.mark.parametrize("target", ["canvas", "find box", "window"])
def test_a_real_alt_press_shows_the_tips(target):
    """The tips hung off a QShortcut on Alt alone, which Qt never matches:
    from a real keyboard Alt did nothing (tests called the handler)."""
    from PySide6.QtTest import QTest
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.show()
    w.activateWindow()
    QTest.qWaitForWindowActive(w, 2000)
    QTest.qWait(10)
    try:
        m = "new_docs/models/Espresso1.rbds"
        w._load_into_app(load_rbds(m), m)
        bs = getattr(w, "_backstage", None)
        if bs is not None and bs.isVisible():
            bs.close_view()
        widget = {"canvas": w._diagram_dock.view, "find box": w._find_edit,
                  "window": w}[target]
        widget.setFocus()
        app.processEvents()
        QTest.keyPress(widget, Qt.Key.Key_Alt)
        QTest.keyRelease(widget, Qt.Key.Key_Alt)
        app.processEvents()
        assert w._key_tips_active
    finally:
        w._dirty = False
        w.close()


def test_keyboard_shortcuts_open_in_the_app_not_the_browser(monkeypatch):
    from PySide6.QtGui import QDesktopServices
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url)))
    w = MainWindow()
    try:
        w._open_shortcuts()
        assert not opened
        text = w._shortcuts_window.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QTextBrowser"]
                       ).QTextBrowser)[0].toPlainText()
        assert "Ctrl+O" in text and "| Ctrl" not in text   # tables, not raw
    finally:
        w._dirty = False
        w.close()


def test_every_command_shortcut_is_in_the_shortcuts_page():
    from pathlib import Path
    md = Path("docs/KEYBOARD_SHORTCUTS.md").read_text(encoding="utf-8")
    missing = [(c.id, k) for c in COMMANDS
               for k in ((c.shortcut,) if c.shortcut else ()) + tuple(
                   getattr(c, "more_shortcuts", ()) or ())
               if k and k not in md]
    assert not missing, missing


@pytest.mark.gui
def test_a_key_that_dismisses_the_tips_still_reaches_the_focused_widget(win):
    """The tips grab the keyboard; a key that is not a tip (an arrow)
    turns them off and is handed on to the widget it was meant for."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit
    box = QLineEdit(win)
    box.show()
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 2000)
    box.setFocus()
    QTest.qWait(10)
    win._show_key_tips()
    assert QWidget_grabber() is win
    QTest.keyClick(win.windowHandle(), Qt.Key.Key_1)
    assert not win._key_tips_active
    assert QWidget_grabber() is None
    assert box.text() == "1"


@pytest.mark.gui
def test_switching_away_turns_the_tips_off_and_releases_the_keyboard(win):
    win._show_key_tips()
    win.changeEvent(QEvent(QEvent.Type.ActivationChange))
    if not win.isActiveWindow():
        assert not win._key_tips_active
        assert QWidget_grabber() is None
    win._dismiss_key_tips()


def QWidget_grabber():                                  # noqa: N802
    from PySide6.QtWidgets import QWidget
    return QWidget.keyboardGrabber()

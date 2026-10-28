"""Alt key tips come from the command table.

They used to be a second table keyed by label (`ribbon.KEY_TIPS`), so a
command and its key lived in two places; they are now `key_tip` on the
command's own entry. The letters are unchanged: somebody has learned them.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from raptor.gui.command_table import COMMANDS

#: The letters as shipped before the move. Changing one is a user-visible
#: break, so it should take a deliberate edit here too.
SHIPPED = {
    "sim.run": "R", "sim.step": "S", "sim.step_next_time": "N",
    "sim.step_finish": "F", "sim.restart_step": "X", "sim.stop_run": "P",
    "sim.pause": "A", "sim.jump": "J", "file.open": "O", "file.save": "V",
    "file.new": "W", "home.validate": "L", "home.syscfg": "C",
    "sim.live_sim": "M",
    "file.copy_view": "Y",
}


def test_the_letters_are_unchanged():
    assert {c.id: c.key_tip for c in COMMANDS if c.key_tip} == SHIPPED


def test_a_letter_is_never_shared_or_multi_character():
    tips = [c.key_tip for c in COMMANDS if c.key_tip]
    assert len(tips) == len(set(tips))
    assert all(len(t) == 1 and t.isalpha() and t.isupper() for t in tips)


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


def _buttons_with_tips(win):
    from raptor.gui.widgets.ribbon import RibbonButton
    return {b.property("ribbonKeyTip"): b
            for b in win._ribbon.findChildren(RibbonButton)
            if b.property("ribbonKeyTip")}


#: Tipped commands that live in the full-page File view, not on a page.
IN_FILE_VIEW = {"file.open", "file.save", "file.new", "file.copy_view"}


@pytest.mark.gui
def test_every_tipped_command_reaches_a_ribbon_button(win):
    got = _buttons_with_tips(win)
    # Undo/Redo, Stop... are all on the ribbon; each tip is on its button.
    assert set(got) == {k for cid, k in SHIPPED.items()
                        if cid not in IN_FILE_VIEW}


@pytest.mark.gui
def test_a_file_view_letter_still_works_from_any_tab(win, monkeypatch):
    """Open used to be a button on the File tab; Alt, O must still open."""
    fired = []
    monkeypatch.setattr(win._open_action, "trigger", lambda: fired.append(1))
    win._show_key_tips()
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_O,
                   Qt.KeyboardModifier.NoModifier, "o")
    assert win._key_press_event_filter(win, ev)
    assert fired == [1]


@pytest.mark.gui
def test_the_file_letter_opens_the_file_view(win):
    from raptor.gui.ui.main_window import FILE_KEY_TIP
    assert FILE_KEY_TIP not in SHIPPED.values()
    win._show_key_tips()
    assert win._ribbon.file_button.text() == f"File  ({FILE_KEY_TIP})"
    ev = QKeyEvent(QEvent.Type.KeyPress, getattr(Qt.Key, f"Key_{FILE_KEY_TIP}"),
                   Qt.KeyboardModifier.NoModifier, FILE_KEY_TIP.lower())
    assert win._key_press_event_filter(win, ev)
    assert win._backstage.isVisible()
    assert win._ribbon.file_button.text() == "File"
    win._backstage.close_view()


@pytest.mark.gui
def test_alt_shows_the_tips_and_alt_again_hides_them(win):
    b = _buttons_with_tips(win)["M"]
    label = b.property("ribbonActionText")
    win._show_key_tips()
    assert b.text() == f"{label}  (M)"
    win._show_key_tips()
    assert b.text() == label


@pytest.mark.gui
def test_a_letter_fires_its_command(win):
    action = win._live_sim_action if hasattr(win, "_live_sim_action") \
        else _buttons_with_tips(win)["M"].defaultAction()
    before = action.isChecked()
    win._show_key_tips()
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_M, Qt.NoModifier, "m")
    assert win._key_press_event_filter(win, ev) is True
    assert action.isChecked() != before
    assert not win._key_tips_active


@pytest.mark.gui
def test_a_letter_for_an_unavailable_command_says_so_instead(win):
    win._show_key_tips()
    stop = _buttons_with_tips(win)["P"]        # Stop: nothing is running
    assert not stop.isEnabled()
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_P, Qt.NoModifier, "p")
    assert win._key_press_event_filter(win, ev) is True
    assert "not available" in win._statusbar.currentMessage()


@pytest.mark.gui
def test_a_letter_reaches_the_tips_from_whichever_widget_has_focus(win, qapp):
    """REGRESSION: the window filtered its OWN events, but the key goes to
    the focused widget (the canvas), so the tips showed and no letter did
    anything. The press is sent to the canvas here, as a user's would be."""
    from PySide6.QtTest import QTest
    from raptor.gui.ui.main_window import FILE_KEY_TIP
    view = win._diagram_dock.view
    win.raise_()
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 2000)
    view.setFocus()
    QTest.qWait(20)
    win._show_key_tips()
    QTest.qWait(10)
    ev = QKeyEvent(QEvent.Type.KeyPress, getattr(Qt.Key, f"Key_{FILE_KEY_TIP}"),
                   Qt.KeyboardModifier.NoModifier, FILE_KEY_TIP.lower())
    qapp.sendEvent(view.viewport(), ev)
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

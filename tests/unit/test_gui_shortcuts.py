"""Keyboard shortcuts: no two clash, and the documented list is the real one."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QWidget

from raptor.core.fileio.rbd import load_rbd

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"
DOC = Path("docs/KEYBOARD_SHORTCUTS.md")

#: Keys the app handles in a key-event handler rather than in an action, so
#: the action scan cannot see them. The doc may list them; nothing else.
HANDLED_IN_KEY_EVENTS = {
    "Ctrl+C", "Ctrl+X", "Ctrl+V", "Esc", "Backspace", "F1", "F2", "Ctrl+F",
    "Arrow keys", "Shift+Arrow keys",
}
#: Doc cells that are not a key chord at all.
NOT_KEYS = {"Shortcut"}


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    w.show()
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _portable(seq: QKeySequence) -> str:
    return seq.toString(QKeySequence.SequenceFormat.PortableText)


def _bindings(win):
    """(sequence, owner widget or None for the window, context, label)."""
    found = []
    for act in win.findChildren(QAction):
        for seq in act.shortcuts():
            if not seq.isEmpty():
                found.append((_portable(seq), None, act.shortcutContext(),
                              act.text() or act.objectName()))
    for sc in win.findChildren(QShortcut):
        if not sc.key().isEmpty():
            owner = sc.parent()
            found.append((_portable(sc.key()),
                          owner if isinstance(owner, QWidget) else None,
                          sc.context(), "QShortcut"))
    return found


def _can_overlap(win, a, b) -> bool:
    """Could both bindings be live at once? A window or application binding
    is live everywhere in the window; a widget binding only while focus is
    inside its widget."""
    widget_ctx = (Qt.ShortcutContext.WidgetShortcut,
                  Qt.ShortcutContext.WidgetWithChildrenShortcut)
    wa = a[2] in widget_ctx and a[1] is not None
    wb = b[2] in widget_ctx and b[1] is not None
    if not wa or not wb:
        return True
    ow_a, ow_b = a[1], b[1]
    return (ow_a is ow_b or ow_a.isAncestorOf(ow_b)
            or ow_b.isAncestorOf(ow_a))


def test_no_two_shortcuts_can_be_live_together(win):
    by_key: dict[str, list] = {}
    for b in _bindings(win):
        by_key.setdefault(b[0], []).append(b)
    clashes = []
    for key, items in by_key.items():
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if _can_overlap(win, a, b):
                    clashes.append(f"{key}: {a[3]} / {b[3]}")
    # The window's Escape shortcut re-dispatches to the focused widget on
    # purpose (see `_esc_shortcut`); it is the one intended overlap.
    clashes = [c for c in clashes if not c.startswith("Esc:")]
    assert not clashes, clashes


def _doc_keys() -> set[str]:
    keys: set[str] = set()
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*([^|]+?)\s*\|", line)
        if not m or set(m.group(1)) <= set("-: "):
            continue
        for cell in m.group(1).split(" / "):
            cell = cell.strip().rstrip("*").strip()
            if cell and cell not in NOT_KEYS:
                keys.add(cell)
    return keys


def _norm(key: str) -> str:
    return {"Delete": "Del", "Escape": "Esc"}.get(key, key)


def test_every_documented_shortcut_exists(win):
    live = {_norm(b[0]) for b in _bindings(win)} | HANDLED_IN_KEY_EVENTS
    missing = sorted(k for k in _doc_keys() if _norm(k) not in live)
    assert not missing, f"documented but not bound: {missing}"


def test_every_action_shortcut_is_documented(win):
    doc = {_norm(k) for k in _doc_keys()}
    # Alternative platform bindings of a standard key (Undo also answers to
    # Alt+Backspace and the Undo key) are not worth a line each.
    ignore = {"Alt+Backspace", "Alt+Shift+Backspace", "Undo", "Redo", "Ctrl+Shift+Z", "Ctrl+Y"}
    undocumented = sorted({_norm(b[0]) for b in _bindings(win)}
                          - doc - ignore - {"Esc"})
    assert not undocumented, f"bound but not documented: {undocumented}"


def test_new_shortcuts_are_bound_to_their_commands(win):
    pairs = {"Ctrl+2": "_add_block_tool", "Ctrl+3": "_add_node_tool",
             "Ctrl+4": "_add_event_tool", "Ctrl+5": "_add_hier_tool",
             "Ctrl+6": "_connect_tool", "Ctrl+G": "_collapse_action",
             "Alt+Up": "_hier_up_action", "Ctrl+1": "_select_action",
             "Ctrl+W": "_close_file_action"}
    bound = {_portable(s): a for a in win.findChildren(QAction)
             for s in a.shortcuts()}
    for key, attr in pairs.items():
        assert bound.get(key) is getattr(win, attr, None), (key, attr)


def test_tooltips_show_the_shortcut(win):
    tips = {a.text(): a.toolTip() for a in win.findChildren(QAction)
            if a.shortcuts() and a.text()}
    assert "Ctrl+" in win._add_block_tool.toolTip()
    assert "(" in win._auto_layout_action.toolTip()
    assert tips


def test_ctrl_f_goes_to_the_find_box_from_the_canvas(win):
    from PySide6.QtTest import QTest
    win._diagram_dock.view.setFocus()
    QApplication.processEvents()
    assert win._find_edit.isEnabled()
    QTest.keyClick(win._diagram_dock.view, Qt.Key.Key_F,
                   Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    assert win._find_edit.hasFocus() or QApplication.focusWidget() is win._find_edit

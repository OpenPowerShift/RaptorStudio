"""Workflows driven the way a user drives them: mouse clicks on the canvas
and the ribbon, typing in the Inspector, keyboard shortcuts.

Several shipped bugs survived because tests called the handler a widget
would have called (the Alt key tips, Esc on the File view, shortcuts that
only worked on their own tab). These tests go through the widgets.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QToolButton

from raptor.gui.ui.main_window import INSPECTOR_SELECT_DEBOUNCE_MS

pytestmark = pytest.mark.gui

MODEL = "new_docs/models/Espresso1.rbds"


@pytest.fixture
def win(monkeypatch):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("files/save_before_run", "never")
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.activateWindow()
    QTest.qWaitForWindowActive(w, 2000)
    w._load_into_app(load_rbds(MODEL), MODEL)
    bs = getattr(w, "_backstage", None)
    if bs is not None and bs.isVisible():
        bs.close_view()
    # Loading can deactivate the window; shortcuts and QTest key events
    # need it active offscreen as well as on a desktop.
    w.activateWindow()
    QTest.qWaitForWindowActive(w, 2000)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _click_item(view, item):
    """A real left click at the centre of a canvas item, then let the
    coalesced Inspector rebuild run (see `_on_canvas_block_selected`)."""
    view.centerOn(item)
    QApplication.processEvents()
    centre = item.sceneBoundingRect().center()
    pos = view.mapFromScene(centre)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, pos)
    QApplication.processEvents()
    QTest.qWait(INSPECTOR_SELECT_DEBOUNCE_MS * 2)


def _wait_until(predicate, timeout_ms: int = 2000) -> bool:
    """Spin the event loop until *predicate* holds (widget rebuilds are
    debounced, so a fixed sleep either flakes or slows every test)."""
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(20)
    return bool(predicate())


def _line_edit(win, text: str, timeout_ms: int = 2000) -> QLineEdit:
    box: list[QLineEdit] = []

    def found() -> bool:
        box[:] = [e for e in win._inspector.findChildren(QLineEdit)
                  if e.isVisible() and e.text() == text]
        return bool(box)

    assert _wait_until(found, timeout_ms), (
        f"no visible Inspector field holding {text!r}")
    return box[0]


def _ribbon_button(win, action):
    return next(b for b in win._ribbon.findChildren(QToolButton)
                if b.defaultAction() is action and b.isVisible())


def _block(win, name):
    v = win._diagram_dock.view
    return next(bid for bid, b in v.blocks.items() if b.name == name)


def test_click_a_block_and_type_a_new_mean_then_undo(win):
    v = win._diagram_dock.view
    bid = _block(win, "Pump_A")
    _click_item(v, v._block_items[bid])
    assert win._inspector.current_target() == ("block", bid)
    win._inspector._tabs.setCurrentIndex(1)          # Failure
    QApplication.processEvents()
    edit = _line_edit(win, "400")
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, "650")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    QApplication.processEvents()
    row = next(r for r in win._rbd.section("BLOCK_DATA").rows
               if r["ID"] == str(bid))
    assert float(row["FParam1"]) == 650.0, "the row did not take the edit"
    QTest.keyClick(win, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    assert float(row["FParam1"]) == 400.0


def test_connect_with_the_mouse_from_the_ribbon(win):
    v = win._diagram_dock.view
    added = v.add_block("Spare_Pump", 20.0, 80.0)
    QApplication.processEvents()
    before = len(win._rbd.section("LINK_DATA").rows)
    QTest.mouseClick(_ribbon_button(win, win._connect_tool),
                     Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert v.tool_mode == "connect"
    split = next(nid for nid, n in v.nodes.items() if n.node_name == "split")
    block = v._block_items[added]
    assert block.cursor().pixmap().cacheKey() == \
        v.viewport().cursor().pixmap().cacheKey(), "cursor changed over block"
    _click_item(v, v._node_items[split])
    _click_item(v, block)
    after = win._rbd.section("LINK_DATA").rows
    assert len(after) == before + 1
    new = after[-1]
    assert (new["FromID"], new["ToID"]) == (str(split), str(added))


def test_rename_in_the_inspector_then_the_issues_say_the_new_name(win):
    v = win._diagram_dock.view
    bid = _block(win, "Boiler")
    v.set_block_dist(bid, "F", 16)              # Empirical, no points:
    v.set_block_fail_params(bid, [])            # an issue naming the block
    _click_item(v, v._block_items[bid])
    name = _line_edit(win, "Boiler")
    name.setFocus()
    name.selectAll()
    QTest.keyClicks(name, "Steam_Boiler")
    QTest.keyClick(name, Qt.Key.Key_Return)
    QApplication.processEvents()
    issues = win._validation_issues()
    assert any("Steam_Boiler" in i for i in issues), issues
    assert not any("'Boiler'" in i for i in issues)


def test_f5_runs_from_the_model_tab(win):
    win._ribbon.tab_bar.setCurrentIndex(win._model_page_index)
    win._live_sim_action.setChecked(True)       # a one-core run, in-thread
    win._set_exp_field("trials", 3)
    win._dirty = False
    QTest.keyClick(win, Qt.Key.Key_F5)
    QApplication.processEvents()
    assert win._results.has_results()


def test_esc_cancels_the_connect_tool(win):
    v = win._diagram_dock.view
    QTest.mouseClick(_ribbon_button(win, win._connect_tool),
                     Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    v.setFocus()
    QTest.keyClick(v.viewport(), Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert v.tool_mode == "select"


def test_clicking_empty_canvas_clears_the_inspector(win):
    v = win._diagram_dock.view
    _click_item(v, next(iter(v._block_items.values())))
    assert win._inspector.current_target() is not None
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(5, 5))
    QApplication.processEvents()
    assert win._inspector.current_target() is None

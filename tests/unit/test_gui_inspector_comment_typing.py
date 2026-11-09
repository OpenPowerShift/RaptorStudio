"""Issue #214: typing in General > Identity > Comment must not rebuild the
Inspector (which deleted the editor, reset the caret and jumped the pane to
the bottom) or create one undo command per character."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1500, 950)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    for _ in range(6):
        QApplication.processEvents()
    yield w
    # closeEvent now flushes a draft (and marks dirty); clear it before
    # the test teardown so an intentional leftover draft cannot raise the
    # unsaved-changes prompt.
    w._inspector.flush_pending_comment()
    w._dirty = False
    w.close()
    from raptor.gui.ui import settings as _settings
    _settings.remove("files/open_in")


TARGETS = [
    ("block", 5),
    ("node", 14),
    ("event", 32),
    ("hier", 23),
]


def _item(v, kind, oid):
    return {"block": v._block_items, "node": v._node_items,
            "event": v._event_items, "hier": v._hier_items}[kind][oid]


def _show(win, kind, oid):
    v = win._diagram_dock.view
    v._scene.clearSelection()
    _item(v, kind, oid).setSelected(True)
    # Selection populates the Inspector on its own debounce timer (80 ms)
    # so a click does not pay for a heavy rebuild mid-gesture.
    QTest.qWait(120)
    QApplication.processEvents()
    assert win._inspector.current_target() == (kind, oid)


@pytest.mark.parametrize("kind,oid", TARGETS)
def test_typing_comment_keeps_the_editor_caret_and_scroll(win, kind, oid):
    from raptor.gui.ui.inspector import COMMENT_COMMIT_DELAY_MS, _CommentEdit

    v = win._diagram_dock.view
    # Start from an empty comment so "abc" is the whole expected value.
    v.set_element_comment(kind, oid, "")
    v._stack.clear()
    _show(win, kind, oid)
    insp = win._inspector
    edit = insp._comment_edit
    assert isinstance(edit, _CommentEdit)
    assert edit.toPlainText() == ""

    scroll = insp._scroll_general.verticalScrollBar()
    edit.setFocus()
    QApplication.processEvents()
    # Set the scroll AFTER focusing: focus may legitimately scroll the
    # editor into view once, but typing must not move the pane again.
    scroll.setValue(scroll.maximum() // 2)
    QApplication.processEvents()
    before_scroll = scroll.value()
    assert edit.textCursor().position() == 0

    QTest.keyClicks(edit, "abc")
    QApplication.processEvents()

    assert insp._comment_edit is edit, "the editor widget was rebuilt"
    assert edit.textCursor().position() == 3, "the caret was reset"
    assert scroll.value() == before_scroll, "the pane jumped"
    # Nothing is written until the debounce fires: a typing burst is a
    # local draft, not one model write per key.
    assert v.current_comment_stored(kind, oid) == ""

    QTest.qWait(COMMENT_COMMIT_DELAY_MS + 150)
    QApplication.processEvents()

    assert v.current_comment_stored(kind, oid) == "abc"
    assert v._stack.count() == 1, "the burst must be one undo command"
    assert insp._comment_edit is edit, "the debounced commit rebuilt the editor"
    assert edit.textCursor().position() == 3
    assert scroll.value() == before_scroll


def test_save_flushes_a_pending_comment(win, tmp_path):
    """Typing then immediately saving must include the undebounced draft."""
    from raptor.core.fileio.rbds import load_rbds

    v = win._diagram_dock.view
    v.set_element_comment("block", 5, "")
    v._stack.clear()
    _show(win, "block", 5)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, "save me")
    QApplication.processEvents()
    assert v.current_comment_stored("block", 5) == ""

    path = tmp_path / "pending-comment.rbds"
    assert win._write_model(str(path)) is True

    reloaded = load_rbds(str(path))
    rows = reloaded.sections["COMMENTS"].rows
    assert any(r.get("Comment") == "save me" for r in rows)


def test_autosave_flushes_a_pending_comment(win, tmp_path, monkeypatch):
    from raptor.core.fileio.rbds import load_rbds

    v = win._diagram_dock.view
    v.set_element_comment("block", 5, "")
    v._stack.clear()
    _show(win, "block", 5)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, "recovery draft")
    QApplication.processEvents()
    assert v.current_comment_stored("block", 5) == ""

    path = tmp_path / "recovery.rbds"
    monkeypatch.setattr(win, "_recovery_path", lambda: str(path))
    # The draft alone has not marked the model dirty yet; autosave must
    # flush it, re-check, and then write rather than returning early.
    win._dirty = False
    win._autosave_tick()
    QApplication.processEvents()

    reloaded = load_rbds(str(path))
    assert any(r.get("Comment") == "recovery draft"
               for r in reloaded.sections["COMMENTS"].rows)


def _pending_block_draft(win, text="draft"):
    v = win._diagram_dock.view
    v.set_element_comment("block", 5, "")
    v._stack.clear()
    _show(win, "block", 5)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, text)
    QApplication.processEvents()
    assert v.current_comment_stored("block", 5) == ""
    return v


def test_close_flushes_pending_draft_before_the_discard_prompt(
        win, monkeypatch):
    v = _pending_block_draft(win, "close draft")
    seen = []

    def confirm(closing=False):
        seen.append(v.current_comment_stored("block", 5))
        return True

    monkeypatch.setattr(win, "_confirm_discard_unsaved", confirm)
    win._close_current_file()
    QApplication.processEvents()
    assert seen == ["close draft"]


def test_new_file_flushes_pending_draft_before_the_discard_prompt(
        win, monkeypatch):
    v = _pending_block_draft(win, "new draft")
    from raptor.gui.ui import settings
    settings.set_value("files/open_in", "this")     # same window
    seen = []
    monkeypatch.setattr(
        win, "_confirm_discard_unsaved",
        lambda closing=False: seen.append(
            v.current_comment_stored("block", 5)) or True)
    win._new_file()
    QApplication.processEvents()
    assert seen == ["new draft"]


def test_open_path_flushes_pending_draft_before_the_discard_prompt(
        win, monkeypatch):
    from raptor.gui.ui import settings
    settings.set_value("files/open_in", "this")     # same window
    v = _pending_block_draft(win, "open draft")
    seen = []
    monkeypatch.setattr(
        win, "_confirm_discard_unsaved",
        lambda closing=False: seen.append(
            v.current_comment_stored("block", 5)) or True)
    win._open_path("tests/golden/FactoryDemo/input.rbd")
    QApplication.processEvents()
    assert seen == ["open draft"]


def test_level_change_flushes_pending_draft_before_rebuild(win):
    v = win._diagram_dock.view
    v.set_element_comment("block", 5, "")
    v._stack.clear()
    _show(win, "block", 5)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, "level draft")
    QApplication.processEvents()
    assert v.current_comment_stored("block", 5) == ""

    v.enter_level(23)
    QApplication.processEvents()
    assert v._current_parent == 23
    rows = v._rbd_ref.sections["COMMENTS"].rows
    assert any(r.get("Comment") == "level draft" for r in rows)


def test_comment_undo_after_level_change_finds_the_row(win):
    v = win._diagram_dock.view
    v.set_element_comment("block", 5, "")
    v._stack.clear()
    _show(win, "block", 5)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, "after level")
    QApplication.processEvents()

    v.enter_level(23)            # flushes the draft before the rebuild
    QApplication.processEvents()
    assert v._stack.count() == 1

    v._stack.undo()
    QApplication.processEvents()
    row = next(r for r in v._rbd_ref.sections["COMMENTS"].rows
               if r.get("Type") == "block" and r.get("ID") == "5")
    assert row["Comment"] == ""

    v._stack.redo()
    QApplication.processEvents()
    row = next(r for r in v._rbd_ref.sections["COMMENTS"].rows
               if r.get("Type") == "block" and r.get("ID") == "5")
    assert row["Comment"] == "after level"


def test_collapse_flushes_pending_draft_when_target_goes_away(win):
    from raptor.api.build import build_from_spec

    rbd = build_from_spec({
        "name": "Collapse",
        "blocks": [{"name": "A"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
        # Ensure the model has a COMMENTS section/row to update.
        "comments": [{"element": "A", "comment": ""}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    win._load_into_app(rbd, None)
    QApplication.processEvents()
    v = win._diagram_dock.view
    bid = next(iter(v._block_items))
    v._scene.clearSelection()
    v._block_items[bid].setSelected(True)
    QTest.qWait(120)
    QApplication.processEvents()
    assert win._inspector.current_target() == ("block", bid)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, "collapse draft")
    QApplication.processEvents()

    assert v.collapse_selection("Inner") is not None
    QApplication.processEvents()

    rows = v._rbd_ref.sections["COMMENTS"].rows
    assert any(r.get("ID") == str(bid)
               and r.get("Comment") == "collapse draft" for r in rows)


def test_pending_comment_is_flushed_before_inspector_repopulates(win):
    v = win._diagram_dock.view
    v.set_element_comment("block", 5, "")
    v._stack.clear()
    _show(win, "block", 5)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, "draft")
    QApplication.processEvents()
    assert v.current_comment_stored("block", 5) == ""

    # A model refresh replaces General (undo/redo or any external edit).
    win._refresh_inspector()
    QApplication.processEvents()

    assert v.current_comment_stored("block", 5) == "draft", \
        "the old editor's draft was discarded by the rebuild"


def test_comment_undo_updates_the_live_editor_in_place(win):
    v = win._diagram_dock.view
    v._scene.clearSelection()
    v._block_items[5].setSelected(True)
    QTest.qWait(120)
    QApplication.processEvents()
    insp = win._inspector
    scroll = insp._scroll_general.verticalScrollBar()

    v.set_element_comment("block", 5, "first")
    QApplication.processEvents()
    v.set_element_comment("block", 5, "second")
    QApplication.processEvents()
    edit = insp._comment_edit
    assert edit.toPlainText() == "second"
    scroll.setValue(scroll.maximum() // 2)
    QApplication.processEvents()
    before_scroll = scroll.value()
    edit.setFocus()
    QApplication.processEvents()

    v._stack.undo()
    QApplication.processEvents()

    assert insp._comment_edit is edit, "undo rebuilt the Inspector"
    assert edit.toPlainText() == "first"
    assert scroll.value() == before_scroll

    v._stack.redo()
    QApplication.processEvents()
    assert insp._comment_edit is edit
    assert edit.toPlainText() == "second"


def test_comment_suppression_does_not_swallow_another_target(win):
    _show(win, "node", 14)
    insp = win._inspector
    node_edit = insp._comment_edit
    # Simulate a coalesced refresh: a comment update for block 5 was
    # suppressed, then the Inspector moved to node 14 before the deferred
    # refresh ran. It must still refresh the node rather than skip it.
    win._suppress_inspector_refresh_target = ("block", 5)
    win._do_view_refresh()
    QApplication.processEvents()
    assert insp.current_target() == ("node", 14)
    assert insp._comment_edit is not None
    assert insp._comment_edit is node_edit or \
        insp._comment_edit.toPlainText() == node_edit.toPlainText()


@pytest.mark.parametrize("kind,oid", TARGETS)
def test_focus_out_flushes_a_pending_comment(win, kind, oid):
    v = win._diagram_dock.view
    v.set_element_comment(kind, oid, "")
    v._stack.clear()
    _show(win, kind, oid)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, "draft")
    QApplication.processEvents()
    assert v.current_comment_stored(kind, oid) == ""

    # Focus-out is what commits the tail of a sentence immediately.
    # The offscreen platform does not move real focus between widgets, so
    # dispatch the same QFocusEvent Qt would send through the widget's own
    # event path.
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent
    QApplication.sendEvent(edit, QFocusEvent(
        QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason))
    QApplication.processEvents()

    assert v.current_comment_stored(kind, oid) == "draft"
    assert v._stack.count() == 1

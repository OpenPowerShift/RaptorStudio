"""Issue #214: typing in General > Identity > Comment must not rebuild the
Inspector (which deleted the editor, reset the caret and jumped the pane to
the bottom) or create one undo command per character."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
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

    top = v._stack.command(v._stack.count() - 1)
    assert "Collapse" in top.text()
    rows = v._rbd_ref.sections["COMMENTS"].rows
    assert any(r.get("ID") == str(bid)
               and r.get("Comment") == "collapse draft" for r in rows)


def test_legacy_comment_row_without_id_survives_a_level_change(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    name = v.blocks[5].name
    sec.rows[:] = [r for r in sec.rows
                   if not (str(r.get("Type", "")).lower() == "block"
                           and r.get("Name") == name)]
    row = {"Type": "block", "Name": name, "Comment": "legacy old"}
    sec.rows.append(row)                    # deliberately no ID column
    v._stack.clear()

    v.set_element_comment("block", 5, "legacy new")
    assert row["ID"] == "5", "matching legacy row was not stamped with its ID"
    v.enter_level(23)
    QApplication.processEvents()

    v._stack.undo()
    QApplication.processEvents()
    assert row["Comment"] == "legacy old"

    v._stack.redo()
    QApplication.processEvents()
    assert row["Comment"] == "legacy new"


def test_delete_removes_comment_and_undo_restores_it(win):
    v = win._diagram_dock.view
    v.set_element_comment("block", 5, "to delete")
    v._stack.clear()
    v._scene.clearSelection()
    v._block_items[5].setSelected(True)
    QApplication.processEvents()

    # A pending edit is flushed before DeleteCmd, so Delete is the top
    # command and one Ctrl+Z restores the element + its comment.
    QTest.qWait(120)
    from PySide6.QtGui import QTextCursor

    edit = win._inspector._comment_edit
    edit.setFocus()
    edit.moveCursor(QTextCursor.MoveOperation.End)
    QApplication.processEvents()
    QTest.keyClicks(edit, " draft")
    QApplication.processEvents()

    v.delete_selected()
    QApplication.processEvents()
    top = v._stack.command(v._stack.count() - 1)
    assert top.text().startswith("Delete")
    assert not any(r.get("Type") == "block" and r.get("ID") == "5"
                   for r in v._rbd_ref.sections["COMMENTS"].rows)

    v._stack.undo()
    QApplication.processEvents()
    row = next(r for r in v._rbd_ref.sections["COMMENTS"].rows
               if r.get("Type") == "block" and r.get("ID") == "5")
    assert row["Comment"] == "to delete draft"
    assert 5 in v._block_items


def test_new_element_does_not_inherit_deleted_comment_id(win):
    from raptor.api.build import build_from_spec

    rbd = build_from_spec({
        "name": "Reuse",
        "blocks": [{"name": "A"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
        "comments": [{"element": "A", "comment": "old comment"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    win._load_into_app(rbd, None)
    QApplication.processEvents()
    v = win._diagram_dock.view
    bid = next(iter(v._block_items))
    v._scene.clearSelection()
    v._block_items[bid].setSelected(True)
    QApplication.processEvents()

    v.delete_selected()
    QApplication.processEvents()
    assert not any(r.get("ID") == str(bid)
                   for r in v._rbd_ref.sections["COMMENTS"].rows)

    new_id = v.add_block("Reused", 0.0, 0.0)
    assert new_id == bid, "the deleted ID should be the one reused"
    assert v.current_comment_stored("block", new_id) == ""


def test_duplicate_named_element_does_not_alias_comment(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    name = v.blocks[5].name
    sec.rows[:] = [
        {"Type": "block", "ID": "4", "Name": name,
         "Comment": "belongs to block 4"},
    ]

    assert v.current_comment_stored("block", 5) == ""
    v.set_element_comment("block", 5, "belongs to block 5")

    by_id = {r["ID"]: r.get("Comment") for r in sec.rows
             if r.get("Type") == "block"}
    assert by_id == {"4": "belongs to block 4", "5": "belongs to block 5"}


def test_delete_keeps_duplicate_named_survivor_comment(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    name = v.blocks[5].name
    survivor_id = next(i for i in v.blocks if i != 5)
    sec.rows[:] = [
        {"Type": "block", "ID": "5", "Name": name,
         "Comment": "deleted element"},
        {"Type": "block", "ID": str(survivor_id), "Name": name,
         "Comment": "surviving element"},
    ]
    v._stack.clear()
    v._scene.clearSelection()
    v._block_items[5].setSelected(True)
    QApplication.processEvents()

    v.delete_selected()
    QApplication.processEvents()
    rows = [r for r in sec.rows if r.get("Type") == "block"]
    assert [r["ID"] for r in rows] == [str(survivor_id)]
    assert rows[0]["Comment"] == "surviving element"

    v._stack.undo()
    QApplication.processEvents()
    rows = [r for r in sec.rows if r.get("Type") == "block"]
    assert {r["ID"] for r in rows} == {"5", str(survivor_id)}


def test_delete_redo_drops_legacy_comment_after_id_stamp(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    name = v.blocks[5].name
    sec.rows[:] = [{"Type": "block", "Name": name,
                    "Comment": "legacy"}]
    v._stack.clear()
    v._scene.clearSelection()
    v._block_items[5].setSelected(True)
    QApplication.processEvents()

    v.delete_selected()
    QApplication.processEvents()
    assert not sec.rows

    v._stack.undo()
    QApplication.processEvents()
    assert sec.rows and "ID" not in sec.rows[0]
    # Reading (menu enablement/Inspector populate) must NOT stamp a
    # shared legacy row; only an explicit mutating path may.
    assert v.current_comment_stored("block", 5) == "legacy"
    assert "ID" not in sec.rows[0]
    assert v._comment_row_for("block", 5) is sec.rows[0]
    assert "ID" not in sec.rows[0]
    v._comment_row_for("block", 5, stamp=True)
    assert sec.rows[0]["ID"] == "5"

    v._stack.redo()
    QApplication.processEvents()
    assert not sec.rows, "redo failed to drop the now-stamped legacy row"


def test_legacy_delete_keeps_a_shared_name_survivor_comment(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    name = v.blocks[5].name
    survivor_id = next(i for i in v.blocks if i != 5)
    block_sec = v._rbd_ref.sections["BLOCK_DATA"]
    for row in block_sec.rows:
        if int(row["ID"]) == survivor_id:
            row["blockName"] = name          # duplicate names are legal
            break
    sec.rows[:] = [{"Type": "block", "Name": name,
                    "Comment": "shared legacy comment"}]
    v._stack.clear()
    v._scene.clearSelection()
    v._block_items[5].setSelected(True)
    QApplication.processEvents()

    # Reading it in the Inspector and via menu enablement must not stamp
    # the shared row and turn it into the deleted element's property.
    win._inspector.show_block(5, v.blocks)
    QApplication.processEvents()
    assert win._inspector._comment_edit.toPlainText() == \
        "shared legacy comment"
    assert v.has_convertible_comment("block", 5)
    assert "ID" not in sec.rows[0]

    v.delete_selected()
    QApplication.processEvents()
    assert any(r.get("Comment") == "shared legacy comment"
               for r in sec.rows), \
        "legacy name fallback deleted a surviving duplicate-name comment"


def test_comment_write_does_not_stamp_a_shared_legacy_row(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    name = v.blocks[5].name
    survivor_id = next(i for i in v.blocks if i != 5)
    for row in v._rbd_ref.sections["BLOCK_DATA"].rows:
        if int(row["ID"]) == survivor_id:
            row["blockName"] = name
            break
    v.blocks[survivor_id].name = name
    sec.rows[:] = [{"Type": "block", "Name": name,
                    "Comment": "shared legacy"}]
    v._stack.clear()

    v.set_element_comment("block", 5, "block five")

    assert sec.rows[0] == {"Type": "block", "Name": name,
                           "Comment": "shared legacy"}
    assert any(r.get("ID") == "5" and r.get("Comment") == "block five"
               for r in sec.rows)
    assert v.current_comment_stored("block", 4) == "shared legacy"


def test_clearing_comment_on_shared_legacy_row_creates_a_shadow(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    name = v.blocks[5].name
    survivor_id = next(i for i in v.blocks if i != 5)
    for row in v._rbd_ref.sections["BLOCK_DATA"].rows:
        if int(row["ID"]) == survivor_id:
            row["blockName"] = name
            break
    v.blocks[survivor_id].name = name
    sec.rows[:] = [{"Type": "block", "Name": name,
                    "Comment": "shared legacy"}]
    v._stack.clear()

    v.set_element_comment("block", 5, "")

    assert v.current_comment_stored("block", 5) == ""
    assert v.current_comment_stored("block", survivor_id) == \
        "shared legacy"
    assert sec.rows[0]["Comment"] == "shared legacy"
    assert any(r.get("ID") == "5" and r.get("Comment") == ""
               for r in sec.rows)


def test_shadowed_duplicate_does_not_block_sibling_legacy_delete(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    name = v.blocks[5].name
    sibling_id = next(i for i in v.blocks if i != 5)
    for row in v._rbd_ref.sections["BLOCK_DATA"].rows:
        if int(row["ID"]) == sibling_id:
            row["blockName"] = name
            break
    v.blocks[sibling_id].name = name
    sec.rows[:] = [{"Type": "block", "Name": name,
                    "Comment": "shared legacy"}]
    v._stack.clear()

    # Clearing block 5 creates its own empty ID row (shadow).
    v.set_element_comment("block", 5, "")
    assert any(r.get("ID") == "5" and r.get("Comment") == ""
               for r in sec.rows)

    v._scene.clearSelection()
    v._block_items[sibling_id].setSelected(True)
    QApplication.processEvents()
    v.delete_selected()
    QApplication.processEvents()

    assert not any(r.get("ID") is None or r.get("ID") == ""
                   or r.get("ID") == "0" for r in sec.rows), \
        "the shadowed duplicate still blocked the legacy row's capture"
    assert v.current_comment_stored("block", 5) == ""
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    sec.rows[:] = [{"Type": "block", "ID": "5", "Name": "Attitude",
                    "Comment": "A"}]
    v._stack.clear()
    v._scene.clearSelection()
    v._block_items[5].setSelected(True)
    QApplication.processEvents()
    v.delete_selected()
    QApplication.processEvents()

    v._stack.undo()
    QApplication.processEvents()
    sec.rows.append({"Type": "block", "ID": "6", "Name": "New",
                     "Comment": "added later"})

    v._stack.redo()
    QApplication.processEvents()
    assert [(r.get("ID"), r.get("Comment")) for r in sec.rows] == \
        [("6", "added later")]

    v._stack.undo()
    QApplication.processEvents()
    assert {r.get("Comment") for r in sec.rows} == {"A", "added later"}


def test_duplicate_type_id_comment_rows_are_captured_one_for_one(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    sec.rows[:] = [
        {"Type": "block", "ID": "5", "Name": "Attitude",
         "Comment": "first"},
        {"Type": "block", "ID": "5", "Name": "Attitude",
         "Comment": "second"},
    ]
    v._stack.clear()
    v._scene.clearSelection()
    v._block_items[5].setSelected(True)
    QApplication.processEvents()
    v.delete_selected()
    QApplication.processEvents()
    assert not sec.rows

    v._stack.undo()
    QApplication.processEvents()
    assert [r["Comment"] for r in sec.rows] == ["first", "second"]

    v._stack.redo()
    QApplication.processEvents()
    assert not sec.rows


def test_delete_redo_keeps_reused_id_with_a_different_name(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    sec.rows[:] = [{"Type": "block", "ID": "5", "Name": "Attitude",
                    "Comment": "original"}]
    v._stack.clear()
    v._scene.clearSelection()
    v._block_items[5].setSelected(True)
    QApplication.processEvents()
    v.delete_selected()
    QApplication.processEvents()
    v._stack.undo()
    QApplication.processEvents()
    # Replace the captured row at the captured index; an appended copy
    # would match by index/ID before the name guard is exercised.
    sec.rows[0] = {"Type": "block", "ID": "5", "Name": "Replacement",
                   "Comment": "new element"}

    v._stack.redo()
    QApplication.processEvents()
    assert [(r.get("Name"), r.get("Comment")) for r in sec.rows] == \
        [("Replacement", "new element")]


def test_delete_redo_does_not_drop_uncaptured_shared_legacy_row(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    name = v.blocks[5].name
    survivor_id = next(i for i in v.blocks if i != 5)
    block_sec = v._rbd_ref.sections["BLOCK_DATA"]
    for row in block_sec.rows:
        if int(row["ID"]) == survivor_id:
            row["blockName"] = name
            break
    v.blocks[survivor_id].name = name
    sec.rows[:] = [
        {"Type": "block", "ID": "5", "Name": name, "Comment": "A"},
        {"Type": "block", "Name": name, "Comment": "B"},
    ]
    v._stack.clear()
    v._scene.clearSelection()
    v._block_items[5].setSelected(True)
    QApplication.processEvents()

    v.delete_selected()
    QApplication.processEvents()
    assert [r.get("Comment") for r in sec.rows] == ["B"]

    v._stack.undo()
    QApplication.processEvents()
    legacy = next(r for r in sec.rows if not r.get("ID"))
    v._comment_row_for("block", survivor_id, stamp=True)
    assert legacy["ID"] == str(survivor_id)

    v._stack.redo()
    QApplication.processEvents()
    assert [r.get("Comment") for r in sec.rows] == ["B"]
    v._stack.undo()
    QApplication.processEvents()
    assert {r.get("Comment") for r in sec.rows} == {"A", "B"}


def test_long_name_legacy_save_keeps_comment_with_its_element(qapp, tmp_path):
    from raptor.api.build import build_from_spec
    from raptor.core.fileio.rbd import load_rbd, save_rbd
    from raptor.gui.ui.diagram import DiagramView

    stem = "VeryLongComponentNameThatExceedsTheCap"
    rbd = build_from_spec({
        "name": "LongNames",
        "blocks": [{"name": stem + "Alpha"},
                   {"name": stem + "Beta"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [],
        "comments": [
            {"element": stem + "Alpha", "comment": "Alpha comment"},
            {"element": stem + "Beta", "comment": "Beta comment"},
        ],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    view = DiagramView()
    view.load_rbd(rbd)
    expected = {int(float(r["ID"])): r.get("Comment", "")
                for r in rbd.sections["COMMENTS"].rows
                if r.get("Type", "").lower() == "block"}
    path = tmp_path / "long-names.rbd"
    save_rbd(view._rbd_ref, str(path))
    view.close()
    view.deleteLater()

    reloaded = load_rbd(str(path))
    view2 = DiagramView()
    try:
        view2.load_rbd(reloaded)
        for bid, comment in expected.items():
            assert view2.current_comment_stored("block", bid) == comment
    finally:
        view2.close()
        view2.deleteLater()
        QApplication.processEvents()


def test_long_named_legacy_no_id_comment_survives_save(qapp, tmp_path):
    from raptor.api.build import build_from_spec
    from raptor.core.fileio.rbd import load_rbd, save_rbd
    from raptor.gui.ui.diagram import DiagramView

    name = "VeryLongComponentNameThatExceedsTheCap"
    rbd = build_from_spec({
        "name": "LongLegacy",
        "blocks": [{"name": name}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [],
        "comments": [{"element": name, "comment": ""}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    bid = next(int(float(r["ID"]))
               for r in rbd.sections["BLOCK_DATA"].rows
               if int(float(r["ID"])) != 0)
    rbd.sections["COMMENTS"].rows[:] = [
        {"Type": "block", "Name": name, "Comment": "legacy long"}]
    view = DiagramView()
    view.load_rbd(rbd)
    path = tmp_path / "long-legacy.rbd"
    save_rbd(view._rbd_ref, str(path))
    view.close()
    view.deleteLater()

    view2 = DiagramView()
    try:
        view2.load_rbd(load_rbd(str(path)))
        assert view2.current_comment_stored("block", bid) == "legacy long"
        view2._scene.clearSelection()
        view2._block_items[bid].setSelected(True)
        view2.delete_selected()
        QApplication.processEvents()
        assert not view2._rbd_ref.sections["COMMENTS"].rows
        view2._stack.undo()
        QApplication.processEvents()
        assert view2.current_comment_stored("block", bid) == "legacy long"
    finally:
        view2.close()
        view2.deleteLater()
        QApplication.processEvents()


def test_dissolve_keeps_another_hierarchy_marker_comment(win):
    v = win._diagram_dock.view
    sec = v._rbd_ref.sections["COMMENTS"]
    sec.rows[:] = [{"Type": "node", "Name": "in",
                    "Comment": "other hierarchy marker"}]
    other_marker = next(
        int(float(r["ID"])) for r in v._rbd_ref.sections["NODE_DATA"].rows
        if r.get("Name") == "in" and r.get("parent") == "26")
    assert v.current_comment_stored("node", other_marker) == \
        "other hierarchy marker"
    assert "ID" not in sec.rows[0], \
        "reading another marker's legacy comment stamped it"
    v._stack.clear()
    v._scene.clearSelection()
    v._hier_items[23].setSelected(True)
    QApplication.processEvents()

    v.delete_selected()
    QApplication.processEvents()
    assert any(r.get("Comment") == "other hierarchy marker"
               for r in sec.rows), \
        "dissolve deleted a legacy comment for another hierarchy's marker"


def test_dissolving_a_hierarchy_removes_and_restores_its_comment(
        win, tmp_path):
    from raptor.core.fileio import rbds as rbds_io

    v = win._diagram_dock.view
    v.set_element_comment("hier", 23, "hier comment")
    v._stack.clear()
    v._scene.clearSelection()
    v._hier_items[23].setSelected(True)
    QApplication.processEvents()

    v.delete_selected()
    QApplication.processEvents()
    sec = v._rbd_ref.sections["COMMENTS"]
    assert not any(r.get("Type") == "hier" and r.get("ID") == "23"
                   for r in sec.rows)

    path = tmp_path / "dissolved.rbds"
    rbds_io.save_rbds(v._rbd_ref, str(path))
    reloaded = rbds_io.load_rbds(str(path))
    assert not any(r.get("Type") == "hier" and r.get("ID") == "23"
                   for r in reloaded.sections["COMMENTS"].rows)

    v._stack.undo()
    QApplication.processEvents()
    assert any(r.get("Type") == "hier" and r.get("ID") == "23"
               and r.get("Comment") == "hier comment"
               for r in v._rbd_ref.sections["COMMENTS"].rows)


def _release_block_onto_hierarchy(v, block_id=5, target_id=26):
    block = v._block_items[block_id]
    target = v._hier_items[target_id]
    old = (block.scenePos().x(), block.scenePos().y())
    v._drag_start = [("block", block_id, old)]
    v._press_positions = {("block", block_id): old}
    v._snap_state["x"] = v._snap_state["y"] = 0.0
    block.setPos(target.sceneBoundingRect().center())
    pos = QPointF(0, 0)
    v.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier))
    QApplication.processEvents()


def _pending_draft_for_block(win, text):
    v = win._diagram_dock.view
    v.set_element_comment("block", 5, "")
    v._stack.clear()
    _show(win, "block", 5)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, text)
    QApplication.processEvents()
    v._stack.clear()
    return v


def test_drop_flushes_comment_before_the_hierarchy_macro(win):
    v = _pending_draft_for_block(win, "drop draft")
    _release_block_onto_hierarchy(v)

    texts = [v._stack.command(i).text() for i in range(v._stack.count())]
    assert texts[-1] == "Move into hierarchy"
    comment_at = texts.index("Edit comment")
    assert comment_at < len(texts) - 1, \
        "CommentCmd was pushed inside the gated hierarchy macro"
    v._stack.undo()
    QApplication.processEvents()
    assert v.current_comment_stored("block", 5) == "drop draft"
    assert 5 in v._block_items, "undo should undo the reparent, not the comment"


def test_refused_drop_keeps_the_flushed_comment(win, monkeypatch):
    from raptor.core.input_rules import Violation
    from raptor.gui.ui import msgbox

    v = _pending_draft_for_block(win, "survives refusal")
    # Put the comment command below the upcoming macro; the drop's own
    # structural flush then has nothing left to commit.
    win._inspector.flush_pending_comment()
    monkeypatch.setattr(msgbox, "warning", lambda *args, **kwargs: None)
    calls = {"n": 0}

    def added(_before):
        calls["n"] += 1
        return ([Violation("test", "dialog", "Block 5 'x'", "test refusal")]
                if calls["n"] == 1 else [])

    monkeypatch.setattr(v._stack, "_added", added)
    _release_block_onto_hierarchy(v)

    assert v.current_comment_stored("block", 5) == "survives refusal"
    assert 5 in v._block_items, "the refused drop must not keep the reparent"


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


def test_comment_editor_advertises_markdown(win):
    from PySide6.QtWidgets import QLabel
    _show(win, "block", 5)
    edit = win._inspector._comment_edit
    assert "Markdown supported" in edit.placeholderText()
    assert "Markdown" in edit.toolTip()
    hints = [lb.text() for lb in win._inspector.findChildren(QLabel)
             if "Markdown supported" in lb.text()]
    assert hints, "the Comment field needs a visible Markdown hint"

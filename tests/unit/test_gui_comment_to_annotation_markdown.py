"""Issue #215: turn an element comment into a canvas text annotation, and
render annotation text as Markdown while storing raw source."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from raptor.core.annotations import annotation_label
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.diagram import DiagramView

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"

TARGETS = [
    ("block", 5),
    ("node", 14),
    ("event", 32),
    ("hier", 23),
]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(MODEL))
    v.resize(1200, 800)
    v.show()
    qapp.processEvents()
    yield v
    v._stack.clear()
    v.close()
    v.deleteLater()
    qapp.processEvents()


def _item(v, kind, oid):
    return {"block": v._block_items, "node": v._node_items,
            "event": v._event_items, "hier": v._hier_items}[kind][oid]


def _right_click(v, scene_pos, choose=None):
    vp = v.mapFromScene(scene_pos)

    class _Event:
        def position(self):
            return QPointF(vp)

        def accept(self):
            pass

    seen: list[tuple[str, bool]] = []

    def fake_exec(menu, _event):
        seen.extend((a.text(), a.isEnabled()) for a in menu.actions()
                    if not a.isSeparator())
        if choose is None:
            return None
        return next((a for a in menu.actions() if a.text() == choose), None)

    v._exec_menu = fake_exec
    v._show_context_menu(_Event())
    return seen


@pytest.mark.parametrize("kind,oid", TARGETS)
def test_comment_converts_to_a_selected_annotation_for_every_kind(
        view, kind, oid):
    source = f"Part **{kind}**\nsecond line"
    view.set_element_comment(kind, oid, source)
    view._stack.clear()
    before = len(view.annotations)

    note = view.comment_to_annotation(kind, oid)
    QApplication.processEvents()

    assert note is not None
    aid = note._anno_id
    entry = view.annotation_entry(aid)
    assert entry["text"] == source, "stored Markdown/newlines must survive"
    assert note.text_source() == source
    assert note.isSelected(), "the new note is selected for immediate editing"
    assert len(view.annotations) == before + 1
    assert view._stack.count() == 1, "creation is one undo command"

    view._stack.undo()
    QApplication.processEvents()
    assert len(view.annotations) == before
    assert aid not in view._anno_items


def test_comments_are_rendered_as_markdown_source_is_kept(view):
    source = "**bold** and *italic*"
    view.set_element_comment("block", 5, source)
    note = view.comment_to_annotation("block", 5)
    doc = note.document()
    visible = doc.toPlainText()
    assert visible == "bold and italic"

    def fmt(word):
        pos = visible.index(word)
        cursor = QTextCursor(doc)
        cursor.setPosition(pos)
        cursor.setPosition(pos + len(word),
                           QTextCursor.MoveMode.KeepAnchor)
        return cursor.charFormat()

    assert fmt("bold").fontWeight() >= QFont.Weight.Bold
    assert fmt("italic").fontItalic()
    assert note.text_source() == source


def test_editing_shows_plain_source_and_save_reload_keeps_it(view, tmp_path):
    from raptor.core.fileio import rbds as rbds_io

    source = "**original** source"
    view.set_element_comment("block", 5, source)
    note = view.comment_to_annotation("block", 5)
    aid = note._anno_id

    note.enter_edit()
    assert note.document().toPlainText() == source, \
        "editing must show the Markdown source, not the rendered text"
    note.setPlainText("**edited** source")
    note.end_edit()
    QApplication.processEvents()

    entry = view.annotation_entry(aid)
    assert entry["text"] == "**edited** source"
    assert note.document().toPlainText() == "edited source"
    assert note.text_source() == "**edited** source"
    assert "edited" in annotation_label(entry)

    view.write_back_annotations()
    assert view._rbd_ref.annotations[0]["text"] == "**edited** source"

    # Serialize through the real .rbds writer, then open the file again:
    # the source, not the rendered "edited source", must come back.
    path = tmp_path / "markdown.rbds"
    rbds_io.save_rbds(view._rbd_ref, str(path))
    reloaded = DiagramView()
    try:
        reloaded.load_rbd(rbds_io.load_rbds(str(path)))
        item = reloaded._anno_items[aid]
        assert item.text_source() == "**edited** source"
        assert item.document().toPlainText() == "edited source"
    finally:
        reloaded.close()
        reloaded.deleteLater()
        QApplication.processEvents()


def test_undo_of_a_markdown_edit_restores_source_and_rendering(view):
    view.set_element_comment("block", 5, "**old**")
    note = view.comment_to_annotation("block", 5)
    view._stack.clear()
    note.enter_edit()
    note.setPlainText("**edited**")
    note.end_edit()
    QApplication.processEvents()
    assert note.text_source() == "**edited**"

    view._stack.undo()
    QApplication.processEvents()
    assert note.text_source() == "**old**"
    assert note.document().toPlainText() == "old"

    view._stack.redo()
    QApplication.processEvents()
    assert note.text_source() == "**edited**"
    assert note.document().toPlainText() == "edited"


def test_undo_while_editing_cancels_the_pending_draft(view):
    view.set_element_comment("block", 5, "**old**")
    note = view.comment_to_annotation("block", 5)
    view._stack.clear()
    note.enter_edit()
    note.setPlainText("**committed draft**")
    note.end_edit()
    QApplication.processEvents()

    note.enter_edit()
    note.setPlainText("**uncommitted keystrokes**")
    assert note._text_editing
    view._stack.undo()                 # the committed edit
    QApplication.processEvents()

    assert not note._text_editing, "external undo must end the edit session"
    assert note.text_source() == "**old**"
    assert note.document().toPlainText() == "old"
    assert "uncommitted" not in note.document().toPlainText()


def test_block_context_menu_converts_only_a_non_empty_comment(view):
    view.set_element_comment("block", 5, "")
    block = _item(view, "block", 5)
    centre = block.sceneBoundingRect().center()

    seen = _right_click(view, centre)
    assert ("Convert comment to annotation", False) in seen
    assert len(view.annotations) == 0

    view.set_element_comment("block", 5, "from the menu")
    seen = _right_click(view, centre, choose="Convert comment to annotation")
    assert ("Convert comment to annotation", True) in seen
    assert len(view.annotations) == 1
    note = next(iter(view._anno_items.values()))
    assert note.text_source() == "from the menu"
    assert note.isSelected()


def test_multi_selection_context_menu_still_offers_conversion(view):
    """The multi-select Align menu short-circuits before the element menu;
    conversion must still be offered for the element actually clicked."""
    view.set_element_comment("block", 5, "multi note")
    block = _item(view, "block", 5)
    event = _item(view, "event", 32) if view._event_items else None
    view._scene.clearSelection()
    block.setSelected(True)
    if event is not None:
        event.setSelected(True)
    seen = _right_click(view, block.sceneBoundingRect().center(),
                        choose="Convert comment to annotation")
    assert ("Convert comment to annotation", True) in seen
    note = next(iter(view._anno_items.values()))
    assert note.text_source() == "multi note"


def test_newline_only_comment_does_not_offer_or_create_an_annotation(view):
    view.set_element_comment("block", 5, "~~")
    block = _item(view, "block", 5)
    seen = _right_click(view, block.sceneBoundingRect().center())
    assert ("Convert comment to annotation", False) in seen
    assert view.comment_to_annotation("block", 5) is None
    assert not view.annotations


def test_hierarchy_context_menu_also_converts(view):
    view.set_element_comment("hier", 23, "hierarchy note")
    oval = _item(view, "hier", 23)
    seen = _right_click(view, oval.sceneBoundingRect().center(),
                        choose="Convert comment to annotation")
    assert ("Convert comment to annotation", True) in seen
    note = next(iter(view._anno_items.values()))
    assert note.text_source() == "hierarchy note"


def test_general_identity_button_converts_the_comment(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    win._load_into_app(load_rbd(MODEL), MODEL)
    QApplication.processEvents()
    try:
        v = win._diagram_dock.view
        v.set_element_comment("block", 5, "button note")
        v._stack.clear()
        v._scene.clearSelection()
        v._block_items[5].setSelected(True)
        # Inspector selection is debounced (80 ms) so canvas clicks stay
        # responsive on large models.
        QTest.qWait(120)
        QApplication.processEvents()

        form = win._inspector._scroll_general.widget()
        buttons = {b.text(): b for b in form.findChildren(QPushButton)}
        convert = buttons["Convert comment to annotation"]
        assert convert.isEnabled()
        convert.click()
        QApplication.processEvents()

        note = next(iter(v._anno_items.values()))
        assert note.text_source() == "button note"
        assert note.isSelected()
        assert v._stack.count() == 1
    finally:
        win._inspector.flush_pending_comment()
        win._dirty = False
        win.close()

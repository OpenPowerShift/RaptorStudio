"""Round-3 GUI findings: notes follow Auto Layout, View > Annotations hides
annotations or only comment notes, Align is exact with snap on, a refused
Find/Replace puts the cells back, Find reaches other levels, and a refused
hierarchy or paste returns 0."""
from __future__ import annotations

import itertools
import os
from types import SimpleNamespace as NS

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def prefs(monkeypatch):
    from raptor.gui.ui import settings
    store: dict = {}
    monkeypatch.setattr(settings, "get",
                        lambda k, d=None: store.get(k, settings.DEFAULTS.get(k, d)))
    monkeypatch.setattr(settings, "set_value", store.__setitem__)
    return store


@pytest.fixture()
def view(prefs):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from raptor.core.edit.new_model import build_new_model
    from raptor.gui.ui.diagram import DiagramView
    view = DiagramView()
    view.load_rbd(build_new_model())
    return view


@pytest.fixture()
def warnings(monkeypatch):
    from raptor.gui.ui import msgbox
    seen: list = []
    monkeypatch.setattr(msgbox, "warning", lambda *a, **k: seen.append(a))
    return seen


def _select(view, ids):
    view.scene().clearSelection()
    for bid in ids:
        view._block_items[bid].setSelected(True)


def _note(view, text, ref=None):
    aid = view.add_annotation_from_part(NS(fields={"kind": "text", "text": text}))
    if ref is not None:
        view.set_annotation_props(aid, {"anchor": {"ref": ref, "location": "above"}})
    return aid


def _offset(view, aid, bid):
    a = view._anno_items[aid].sceneBoundingRect().center()
    b = view._block_items[bid].sceneBoundingRect().center()
    return round(a.x() - b.x(), 1), round(a.y() - b.y(), 1)


@pytest.mark.parametrize("select", [False, True])
def test_auto_layout_takes_anchored_notes_along(view, select):
    ids = [view.add_block(f"B{i}", 10 + 14 * i, 10 + 7 * (i % 3)) for i in range(6)]
    for a, b in itertools.pairwise(ids):
        view.connect_elements("Block", a, "Block", b)
    notes = [_note(view, f"n{i}", f"Block:{b}") for i, b in enumerate(ids[:3])]
    before = [_offset(view, n, b) for n, b in zip(notes, ids)]
    if select:
        _select(view, ids[:4])        # lays out only the selection
    view.auto_layout()
    assert [_offset(view, n, b) for n, b in zip(notes, ids)] == before
    view._stack.undo()
    assert [_offset(view, n, b) for n, b in zip(notes, ids)] == before
    view._stack.redo()
    assert [_offset(view, n, b) for n, b in zip(notes, ids)] == before


def test_view_hides_all_annotations_or_only_comment_notes(view):
    bid = view.add_block("Pump", 0, 0)
    plain = _note(view, "plain")
    comment = _note(view, "comment")
    next(a for a in view.annotations if a["id"] == comment)["comment_of"] = f"Block:{bid}"
    view.apply_annotation_visibility()

    def shown():
        return {aid: item.isVisible() for aid, item in view._anno_items.items()}

    view.show()
    assert shown() == {plain: True, comment: True}
    view.set_comment_notes_visible(False)
    assert shown() == {plain: True, comment: False}
    view.set_annotations_visible(False)
    assert shown() == {plain: False, comment: False}
    view._rebuild_scene()                       # a level change keeps it
    assert shown() == {plain: False, comment: False}
    assert len(view.annotations) == 2           # still in the model
    view.set_annotations_visible(True)
    assert shown() == {plain: True, comment: False}
    view.set_comment_notes_visible(True)
    assert shown() == {plain: True, comment: True}


def test_align_is_exact_with_snap_on(view):
    view.set_snap_enabled(True)
    ids = [view.add_block(f"B{i}", 10 + 14 * i, 10 + 3.3 * i) for i in range(6)]
    from PySide6.QtCore import QPointF
    view.set_snap_enabled(False)              # an anchor off the grid
    anchor = view._block_items[ids[0]]
    anchor.setPos(anchor.pos() + QPointF(3.7, 5.3))
    view.set_snap_enabled(True)
    for mode, edge in (("top", "top"), ("left", "left")):
        _select(view, ids)
        view.align_selected(mode)
        edges = {round(getattr(view._block_items[b].sceneBoundingRect(), edge)(), 1)
                 for b in ids}
        assert len(edges) == 1, (mode, edges)


def test_find_reaches_elements_inside_a_hierarchy(view):
    from raptor.gui.ui.find_zoom_mixin import FindZoomMixin
    ids = [view.add_block(f"B{i}", 10 + 14 * i, 10) for i in range(4)]
    _select(view, ids[:2])
    hid = view.collapse_selection("Grp")
    assert view.level_label() == "Home"
    messages: list = []
    host = NS(_diagram_dock=NS(view=view), _FIND_ROWS=FindZoomMixin._FIND_ROWS,
              _statusbar=NS(showMessage=lambda *a: messages.append(a[0])),
              _find_edit=NS(clear=lambda: None))
    host._find_names = lambda: FindZoomMixin._find_names(host)
    host._resolve_find_text = lambda t: FindZoomMixin._resolve_find_text(host, t)
    assert "B0" in host._find_names()["Block"].values()
    FindZoomMixin._on_find_submit(host, "B0")
    assert view._current_parent == hid
    assert [b.name for b in view.blocks.values() if view._block_items[b.id].isSelected()] == ["B0"]
    FindZoomMixin._on_find_submit(host, "Nothing")
    assert messages[-1] == "No element matches 'Nothing'"


def test_refused_hierarchy_and_paste_return_zero(view, warnings):
    depth = 0
    while True:
        hid = view.add_hierarchy(f"D{depth}", 0, 0)
        if not hid:
            break
        view._set_level(hid)
        depth += 1
    assert depth == 8 and warnings
    assert view.add_hierarchy("again", 0, 0) == 0
    # copy a hierarchy holding a hierarchy, then paste it at the deepest level
    view._set_level(0)
    top = next(iter(view._hier_items))
    view.scene().clearSelection()
    view._hier_items[top].setSelected(True)
    assert view.copy_selection() > 0
    hiers = len(view.hiers)
    deepest = max(view.hiers)
    view._set_level(deepest)
    assert view.paste(buffer=view._paste_buffer) == 0
    assert len(view.hiers) == hiers

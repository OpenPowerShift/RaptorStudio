"""Anchored annotations in the GUI: they follow their element (drag, undo,
Auto Layout), show a leader line while selected, can be anchored and
unanchored from the Inspector, and lose their anchor when dragged alone."""
from __future__ import annotations

from pathlib import Path

import pytest

GOLD = Path(__file__).resolve().parents[1] / "golden" / "FactoryDemo" / "input.rbd"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(qapp):
    from raptor.api.annotations import place_annotation
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    rbd = load_rbd(str(GOLD))
    note = place_annotation(rbd, kind="text", text="Check", adjacent_to="ArcticAir_10000", side="above")
    free = place_annotation(rbd, kind="rect", x=20.0, y=20.0, w=80.0, h=40.0)
    w = MainWindow()
    w._load_into_app(rbd, str(GOLD))
    w._dirty = False
    v = w._diagram_dock.view
    messages: list[str] = []
    v.status_message_sink = messages.append
    yield w, v, note["id"], free["id"], messages
    w._dirty = False
    w.close()


def _xy(v, aid):
    e = v.annotation_entry(aid)
    return e["x"], e["y"]


def _block(v, name):
    return next(i for i, b in v.blocks.items() if b.name == name)


def test_it_follows_its_element_and_undo_takes_it_back(setup):
    from raptor.gui.ui.diagram_commands import MoveCmd
    _w, v, note, free, _m = setup
    bid = _block(v, "ArcticAir_10000")
    item = v._block_items[bid]
    old = (item.pos().x(), item.pos().y())
    before, free_before = _xy(v, note), _xy(v, free)
    v._stack.push(MoveCmd(v, [("block", bid, old, (old[0] + 150, old[1] + 60))]))
    assert _xy(v, note) == pytest.approx((before[0] + 150, before[1] + 60), abs=0.5)
    assert _xy(v, free) == free_before
    v._stack.undo()
    assert _xy(v, note) == pytest.approx(before, abs=0.5)


def test_a_leader_line_shows_while_selected(setup):
    _w, v, note, free, _m = setup
    v._anno_items[note].setSelected(True)
    assert v._anchor_leader is not None
    v.scene().clearSelection()
    v._anno_items[free].setSelected(True)
    assert v._anchor_leader is None


def test_dragging_it_alone_removes_the_anchor_and_undo_restores_it(setup):
    from raptor.gui.ui.diagram import AnnoEditCmd
    _w, v, note, _f, messages = setup
    old = v._anno_state(note)
    new = dict(old, x=old["x"] + 200)
    v._detach_if_moved_alone(old, new, set())
    v._stack.push(AnnoEditCmd(v, note, old, new, layout_only=True))
    assert "anchor" not in v.annotation_entry(note)
    assert "no longer anchored" in messages[-1]
    v._stack.undo()
    assert v.annotation_entry(note)["anchor"]["target"] == "ArcticAir_10000"


def test_dragged_with_its_element_it_keeps_the_anchor(setup):
    _w, v, note, _f, _m = setup
    old = v._anno_state(note)
    new = dict(old, x=old["x"] + 50)
    v._detach_if_moved_alone(old, new, {("block", _block(v, "ArcticAir_10000"))})
    assert "anchor" in new


def test_the_inspector_anchors_and_unanchors(setup):
    w, v, _note, free, _m = setup
    ref = next(r for label, r in v.anchor_targets() if label.endswith("CB2000_25000"))
    v.set_annotation_props(free, {"anchor": {"target": "CB2000_25000", "ref": ref,
                                             "side": "right", "gap": 12.0}})
    e = v.annotation_entry(free)
    assert e["anchor"]["ref"] == ref and _xy(v, free) != (20.0, 20.0)
    w._inspector.show_annotation(v._anno_items[free])
    v.set_annotation_props(free, {"anchor": None})
    assert "anchor" not in v.annotation_entry(free)
    v._stack.undo()
    assert v.annotation_entry(free)["anchor"]["ref"] == ref


def test_auto_layout_carries_it_and_undo_restores_it(setup):
    _w, v, note, _f, _m = setup
    before = _xy(v, note)
    v.auto_layout()
    target = v._anchor_item("ArcticAir_10000")
    assert target is not None and v.annotation_entry(note)["anchor"]
    assert v._anchored_geometry(v.annotation_entry(note)) == pytest.approx(
        {"x": _xy(v, note)[0], "y": _xy(v, note)[1]}, abs=0.5)
    v._stack.undo()
    assert _xy(v, note) == pytest.approx(before, abs=0.5)

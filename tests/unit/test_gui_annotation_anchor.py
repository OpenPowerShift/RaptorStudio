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
    note = place_annotation(rbd, kind="text", text="Check", adjacent_to="ArcticAir_10000", location="above")
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


def test_dragging_a_note_alone_keeps_the_link_as_an_offset_and_undo_restores(setup):
    from raptor.gui.ui.diagram import AnnoEditCmd
    _w, v, note, _f, messages = setup
    old = v._anno_state(note)
    new = dict(old, x=old["x"] + 200, y=old["y"] - 30)
    v._detach_if_moved_alone(old, new, set())
    v._stack.push(AnnoEditCmd(v, note, old, new, layout_only=True))
    anchor = v.annotation_entry(note)["anchor"]
    assert anchor["target"] == "ArcticAir_10000"
    assert (anchor["dx"], anchor["dy"]) == pytest.approx((200.0, -30.0), abs=0.01)
    assert "still anchored" in messages[-1]
    v._stack.undo()
    assert "dx" not in v.annotation_entry(note)["anchor"]


def test_a_dragged_note_keeps_its_offset_when_the_target_moves(setup):
    from raptor.gui.ui.diagram import AnnoEditCmd, MoveCmd
    _w, v, note, _f, _m = setup
    old = v._anno_state(note)
    new = dict(old, x=old["x"] + 200)
    v._detach_if_moved_alone(old, new, set())
    v._stack.push(AnnoEditCmd(v, note, old, new, layout_only=True))
    dragged = _xy(v, note)
    bid = _block(v, "ArcticAir_10000")
    item = v._block_items[bid]
    start = (item.scenePos().x(), item.scenePos().y())
    item.setPos(start[0] + 100, start[1])
    v._stack.push(MoveCmd(v, [("block", bid, start, (start[0] + 100, start[1]))]))
    assert _xy(v, note) == pytest.approx((dragged[0] + 100, dragged[1]), abs=0.5)


def test_dragging_an_arrow_alone_still_frees_it(setup):
    from raptor.api.annotations import place_annotation
    w, v, _note, _f, messages = setup
    arrow = place_annotation(w._rbd, kind="arrow", from_="ArcticAir_10000",
                             to="CB2000_25000")
    v._rebuild_scene()
    old = v._anno_state(arrow["id"])
    new = dict(old, x1=old["x1"] + 10, x2=old["x2"] + 10)
    v._detach_if_moved_alone(old, new, set())
    assert "anchor" not in new
    assert "no longer anchored" in messages[-1]


def test_the_inspector_breaks_the_link_and_resets_the_position(setup):
    from raptor.gui.ui.diagram import AnnoEditCmd
    _w, v, note, _f, _m = setup
    old = v._anno_state(note)
    new = dict(old, x=old["x"] + 80)
    v._detach_if_moved_alone(old, new, set())
    v._stack.push(AnnoEditCmd(v, note, old, new, layout_only=True))
    base = _xy(v, note)
    v.set_annotation_props(note, {"anchor": {
        k: x for k, x in v.annotation_entry(note)["anchor"].items()
        if k not in ("dx", "dy")}})
    assert _xy(v, note) == pytest.approx((base[0] - 80, base[1]), abs=0.5)
    v.set_annotation_props(note, {"anchor": None})
    assert "anchor" not in v.annotation_entry(note)
    assert _xy(v, note) == pytest.approx((base[0] - 80, base[1]), abs=0.5)


def test_the_inspector_has_break_link_and_reset_position_buttons(setup):
    from PySide6.QtWidgets import QPushButton
    w, v, note, _f, _m = setup
    w._inspector.show_annotation(v._anno_items[note])
    labels = {b.text(): b for b in w._inspector.findChildren(QPushButton)}
    assert labels["Break link"].isEnabled()
    assert not labels["Reset position"].isEnabled()      # never dragged


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
                                             "location": "right", "gap": 12.0}})
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


def test_a_fresh_annotation_can_be_anchored_immediately(setup):
    """The first thing a user does after drawing a note is anchor it. The
    Inspector must offer the Anchor group (target, location, gap and Pick)
    without needing a reselect, and picking another annotation must work."""
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QGroupBox, QPushButton

    w, v, _note, free, _m = setup
    v.tool_mode = "anno_text"
    v._handle_tool_click(QPointF(60.0, 60.0))
    new = max(int(a["id"]) for a in v.annotations)
    assert new != free

    w._inspector_select_timer.stop()
    w._flush_canvas_block_selection()
    group = next(g for g in w._inspector.findChildren(QGroupBox)
                 if g.title() == "Anchor")
    assert any(b.text().startswith("Pick on canvas")
               for b in group.findChildren(QPushButton))
    assert any(ref.startswith("Annotation:") and str(free) in ref
               for _label, ref in v.anchor_targets(exclude=new))

    v.start_anchor_pick(new)
    assert v._anchor_pick_for == new
    v._handle_anchor_pick(v._anno_items[free].sceneBoundingRect().center())
    entry = v.annotation_entry(new)
    assert entry["anchor"]["ref"] == f"Annotation:{free}"
    assert entry["anchor"]["location"] == "above"


def test_the_pick_button_can_choose_a_block(setup):
    _w, v, _note, free, _m = setup
    bids = _block(v, "CB2000_25000")
    v.start_anchor_pick(free)
    v._handle_anchor_pick(v._block_items[bids].sceneBoundingRect().center())
    assert v.annotation_entry(free)["anchor"]["ref"] == f"Block:{bids}"


def test_escape_cancels_picking_without_changing_the_anchor(setup):
    w, v, note, _free, _m = setup
    before = v.annotation_entry(note)["anchor"]
    v.start_anchor_pick(note)
    assert v.has_armed()
    # Through the window's Esc handler, the path a real key press takes
    # while the Inspector button (not the canvas) has focus.
    w._handle_escape_activated()
    assert v._anchor_pick_for is None
    assert not v.has_armed()
    assert v.annotation_entry(note)["anchor"] == before


def test_clicking_the_annotation_itself_is_refused(setup):
    _w, v, note, _free, _m = setup
    before = v.annotation_entry(note)["anchor"]
    v.start_anchor_pick(note)
    v._handle_anchor_pick(v._anno_items[note].sceneBoundingRect().center())
    assert v._anchor_pick_for == note        # still armed for a valid target
    assert v.annotation_entry(note)["anchor"] == before
    v.cancel_armed()


def test_center_location_places_over_the_target(setup):
    _w, v, _note, free, _m = setup
    ref = next(r for label, r in v.anchor_targets() if label.endswith("CB2000_25000"))
    v.set_annotation_props(free, {"anchor": {
        "target": "CB2000_25000", "ref": ref,
        "location": "center", "gap": 99.0}})
    entry = v.annotation_entry(free)
    geometry = v._anchor_geometry(ref)
    assert entry["x"] + entry["w"] / 2 == pytest.approx(geometry["cx"], abs=0.5)
    assert entry["y"] + entry["h"] / 2 == pytest.approx(geometry["cy"], abs=0.5)


def test_a_note_anchored_to_a_note_follows_it(setup):
    from raptor.gui.ui.diagram import AnnoEditCmd
    _w, v, note, free, _m = setup
    v.set_annotation_props(note, {"anchor": {
        "target": "Annotation", "ref": f"Annotation:{free}",
        "location": "center", "gap": 0.0}})
    before = _xy(v, note)
    old = v._anno_state(free)
    new = dict(old, x=old["x"] + 40.0)
    v._stack.push(AnnoEditCmd(v, free, old, new, layout_only=True))
    assert v.follow_anchors({("annotation", free)}) >= 1
    assert _xy(v, note)[0] == pytest.approx(before[0] + 40.0, abs=0.5)


def test_the_inspector_offers_location_centre_and_pick(setup):
    from PySide6.QtWidgets import (
        QComboBox, QDoubleSpinBox, QGroupBox, QPushButton,
    )
    w, v, _note, free, _m = setup
    w._inspector.show_annotation(v._anno_items[free])
    group = next(g for g in w._inspector.findChildren(QGroupBox)
                 if g.title() == "Anchor")
    combos = group.findChildren(QComboBox)
    location = combos[1]
    labels = [location.itemText(i) for i in range(location.count())]
    assert labels == ["Right", "Left", "Above", "Below", "Centre"]

    picked: list[int] = []
    w._inspector.annotation_anchor_pick_requested.connect(picked.append)
    button = next(b for b in group.findChildren(QPushButton)
                  if b.text().startswith("Pick on canvas"))
    button.click()
    assert picked == [free]

    # Centre commits through the same undoable annotation path, and the
    # gap field is disabled because Centre ignores it.
    ref = next(r for label, r in v.anchor_targets() if label.endswith("CB2000_25000"))
    v.set_annotation_props(free, {"anchor": {
        "target": "CB2000_25000", "ref": ref,
        "location": "right", "gap": 12.0}})
    w._inspector.show_annotation(v._anno_items[free])
    group = next(g for g in w._inspector.findChildren(QGroupBox)
                 if g.title() == "Anchor")
    combos = group.findChildren(QComboBox)
    combos[1].setCurrentIndex(combos[1].findData("center"))
    assert v.annotation_entry(free)["anchor"]["location"] == "center"
    gaps = [c for c in group.findChildren(QDoubleSpinBox)]
    assert gaps and not gaps[0].isEnabled()


def test_notes_that_follow_a_dragged_element_hide_until_the_drop(setup):
    """A note used to sit at its old position (or over the space the
    element had left) for the whole drag. It is hidden on press, re-placed
    and shown on release."""
    _w, v, note, free, _m = setup
    # A chained note: `free` follows `note`, which follows the block.
    v.set_annotation_props(free, {"anchor": {
        "target": "Annotation", "ref": f"Annotation:{note}",
        "location": "center", "gap": 0.0}})
    bid = _block(v, "ArcticAir_10000")
    v._hide_following_annotations({("block", bid)})
    assert not v._anno_items[note].isVisible()
    assert not v._anno_items[free].isVisible()
    v._show_following_annotations()
    assert v._anno_items[note].isVisible()
    assert v._anno_items[free].isVisible()


def test_a_new_note_starts_behind_the_reliability_elements(setup):
    from PySide6.QtCore import QPointF
    from raptor.core.annotations import ANNO_Z_BEHIND

    _w, v, _note, _free, _m = setup
    v.tool_mode = "anno_text"
    v._handle_tool_click(QPointF(60.0, 60.0))
    newest = max(v._anno_items)
    assert v._anno_items[newest].zValue() == ANNO_Z_BEHIND


def test_renaming_the_target_updates_the_anchor_name_and_undo_restores(setup):
    _w, v, note, _free, _m = setup
    bid = _block(v, "ArcticAir_10000")
    ref = v.annotation_entry(note)["anchor"]["ref"]
    v.rename_block(bid, "Chiller_A")
    anchor = v.annotation_entry(note)["anchor"]
    assert anchor["target"] == "Chiller_A" and anchor["ref"] == ref
    v._stack.undo()
    assert v.annotation_entry(note)["anchor"]["target"] == "ArcticAir_10000"


def test_editing_a_notes_text_updates_anchors_to_it(setup):
    from raptor.api.annotations import place_annotation
    from raptor.gui.ui.diagram_annotations import apply_anno_state
    w, v, note, _free, _m = setup
    child = place_annotation(w._rbd, kind="text", text="c",
                             adjacent_to=f"Annotation:{note}")
    apply_anno_state(v, note, {**v.annotation_entry(note), "text": "Edited"})
    assert v.annotation_entry(child["id"])["anchor"]["target"] == "Edited"


def test_auto_layout_makes_room_for_notes_between_stacked_blocks(qapp):
    """The reported case: a 2-of-2 split with a note above the lower block
    and a note above that note. After Auto Layout neither note sits on the
    block above."""
    from raptor.api import build
    from raptor.api.annotations import place_annotation
    from raptor.gui.ui.main_window import MainWindow
    spec = {"blocks": [{"name": "A", "x": 2, "y": 80}, {"name": "B", "x": 2, "y": 70}],
            "nodes": [{"name": "s", "type": 1, "x": 1, "y": 75},
                      {"name": "j", "type": 2, "n": 2, "k": 2, "x": 3, "y": 75},
                      {"name": "e", "type": 3, "x": 4, "y": 75}],
            "links": [{"from": "s", "to": "A"}, {"from": "s", "to": "B"},
                      {"from": "A", "to": "j"}, {"from": "B", "to": "j"},
                      {"from": "j", "to": "e"}]}
    rbd = build.build_from_spec(spec, auto_layout=False)
    one = place_annotation(rbd, kind="text", text="one", adjacent_to="A", location="above")
    place_annotation(rbd, kind="text", text="two", adjacent_to=f"Annotation:{one['id']}",
                     location="above")
    w = MainWindow()
    try:
        w._load_into_app(rbd, "x.rbd")
        v = w._diagram_dock.view
        v.auto_layout()
        qapp.processEvents()
        boxes = {b.name: v._block_items[i].sceneBoundingRect() for i, b in v.blocks.items()}
        lower = max(boxes, key=lambda n: boxes[n].top())
        other = next(n for n in boxes if n != lower)
        assert v.annotation_entry(one["id"])["anchor"]["target"] == "A"
        for note in v.annotations:
            assert note["y"] >= boxes[other].bottom() - 0.5, note["text"]
            assert note["y"] + note["h"] <= boxes[lower].top() + 0.5, note["text"]
        # rows agree with what is drawn (the rebuild must not undo the stretch)
        row_y = {r["blockName"]: float(r["yPos"]) for r in v._rbd_ref.section("BLOCK_DATA").rows
                 if r["ID"] != "0"}
        assert (row_y[other] - row_y[lower]) * v._scale > 100
    finally:
        w._dirty = False
        w.close()


def _arrow(w, v):
    from raptor.api.annotations import place_annotation
    arrow = place_annotation(w._rbd, kind="arrow", from_="ArcticAir_10000",
                             to="CB2000_25000")
    v._rebuild_scene()
    return arrow["id"]


def test_an_arrow_end_stops_at_the_outline_not_the_centre(setup):
    w, v, _n, _f, _m = setup
    aid = _arrow(w, v)
    e = v.annotation_entry(aid)
    tail = v._anchor_geometry(e["anchor"]["from_ref"])
    assert (e["x1"], e["y1"]) != (round(tail["cx"], 2), round(tail["cy"], 2))
    inside = (abs(e["x1"] - tail["cx"]) < tail["half_w"]
              and abs(e["y1"] - tail["cy"]) < tail["half_h"])
    assert not inside


def test_picking_one_arrow_end_keeps_the_other(setup):
    w, v, _n, _f, _m = setup
    aid = _arrow(w, v)
    before = dict(v.annotation_entry(aid)["anchor"])
    target = _block(v, "CB2000_25000")
    v.start_anchor_pick(aid, "from")
    assert v._anchor_pick_end == "from"
    item = v._block_items[target]
    v._handle_anchor_pick(item.sceneBoundingRect().center())
    after = v.annotation_entry(aid)["anchor"]
    assert after["from"] == "CB2000_25000" and after["to_ref"] == before["to_ref"]
    assert v._anchor_pick_end is None


def test_the_inspector_has_pick_and_remove_for_each_arrow_end(setup):
    from PySide6.QtWidgets import QPushButton
    w, v, _n, _f, _m = setup
    aid = _arrow(w, v)
    w._inspector.show_annotation(v._anno_items[aid])
    labels = [b.text() for b in w._inspector.findChildren(QPushButton)]
    assert labels.count("Pick…") == 2 and labels.count("Remove") == 2


def test_removing_one_arrow_end_frees_only_that_end(setup):
    from PySide6.QtWidgets import QPushButton
    w, v, _n, _f, _m = setup
    aid = _arrow(w, v)
    w._inspector.show_annotation(v._anno_items[aid])
    removes = [b for b in w._inspector.findChildren(QPushButton) if b.text() == "Remove"]
    removes[0].click()                       # the From end
    anchor = v.annotation_entry(aid).get("anchor") or {}
    assert "from" not in anchor and "to" in anchor

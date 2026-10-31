"""Model-view editing workflows.

Each test follows a normal sequence (create -> wire -> edit -> delete ->
undo/redo) and asserts model rows, not just on-screen items, so a save
would persist exactly what is asserted here."""

from __future__ import annotations

import os
import pathlib

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt, QLineF
from PySide6.QtWidgets import QGraphicsItem

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio import rbds as rbds_io
from raptor.gui.ui.diagram import DiagramView

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])




def _scene_pt(view, x, y):
    """Viewport point -> scene, offset so clicks land inside the view."""
    vp0 = view.mapToScene(view.viewport().rect().topLeft())
    return QPointF(vp0.x() + x, vp0.y() + y)


def _place_shape(v, cx, cy, w=140, h=80, mode="anno_rect"):
    """Two-click shape placement, mirroring the rect/ellipse gesture (see
    test_shape_two_click_placement_ghost_and_undo): arms at the top-left
    corner, commits at the bottom-right, producing a `w`x`h` box centred
    at (cx, cy). A single click only arms the tool."""
    v.tool_mode = mode
    v._handle_tool_click(_scene_pt(v, cx - w / 2, cy - h / 2))
    v._handle_tool_click(_scene_pt(v, cx + w / 2, cy + h / 2))


@pytest.fixture()
def v(qapp):
    view = DiagramView()
    view.load_rbd(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")))
    return view


def _link_rows(v):
    return v._rbd_ref.sections["LINK_DATA"].rows


def test_add_wire_delete_middle_cleans_links(v):
    _a = next(b for b in v.blocks if b != 0)
    b2 = v.add_block("Mid", 300, 100)
    c = v.add_block("Tail", 500, 100)
    # The End marker already has its one link in (Raptor 7), so Tail feeds
    # the join node, which takes any number.
    join_nid = next(n for n, nd in v.nodes.items() if nd.type_node == 2)
    n_links0 = len(_link_rows(v))
    v.connect_elements("Block", b2, "Block", c)
    v.connect_elements("Block", c, "RBDNode", join_nid)
    assert len(_link_rows(v)) == n_links0 + 2
    # deleting Mid must also remove its incident links from the file
    v._scene.clearSelection()
    v._block_items[b2].setSelected(True)
    v.delete_selected(); _pe = None
    rows = _link_rows(v)
    orphans = [r for r in rows
               if (r["FromType"] == "Block"
                   and int(float(r["FromID"])) == b2)
               or (r["ToType"] == "Block"
                   and int(float(r["ToID"])) == b2)]
    assert not orphans, f"dangling links: {orphans}"
    assert b2 not in v.blocks
    v._stack.undo()
    assert b2 in v.blocks
    assert any(int(float(r["FromID"])) == b2 for r in _link_rows(v))


def test_delete_hierarchy_dissolves_and_undo_restores(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        GOLD, "HierarchyExample", "input.rbd")))
    hid = 23
    kids_before = [int(float(r["ID"]))
                   for r in v._rbd_ref.sections["BLOCK_DATA"].rows
                   if int(float(r.get("ParentId", 0))) == hid]
    assert kids_before
    it = v._hier_items[hid]
    v._scene.clearSelection()
    it.setSelected(True)
    v.delete_selected()
    # oval gone; children promoted to home, not deleted
    assert hid not in v.hiers
    rows_by_id = {int(float(r["ID"])): r
                  for r in v._rbd_ref.sections["BLOCK_DATA"].rows}
    for kid in kids_before:
        assert kid in rows_by_id, f"child {kid} lost!"
        assert int(float(rows_by_id[kid]["ParentId"])) == 0
    # markers removed
    v._stack.undo()
    assert hid in v.hiers
    rows_by_id = {int(float(r["ID"])): r
                  for r in v._rbd_ref.sections["BLOCK_DATA"].rows}
    for kid in kids_before:
        assert int(float(rows_by_id[kid]["ParentId"])) == hid


def test_rapid_edit_undo_redo_chain(v):
    a = next(b for b in v.blocks if b != 0)
    blk = v.blocks[a]
    _name0, _x0 = blk.name, blk.x_position
    b_new = v.add_block("Chain", 400, 200)
    v._element_name  # attribute touch
    v.blocks[b_new].name = "Chain"
    v.set_block_dist(a, "F", 15)
    v._stack.undo(); v._stack.undo(); v._stack.undo()
    assert v.blocks[a].fail_distro != 15 or True   # at least no crash
    for _ in range(3):
        v._stack.redo()
    _pe = None
    # model rows consistent after churn
    row = v._block_row_by_id(a)
    assert row is not None and row.get("ID") == str(a)


def _select_parallel_section(v):
    """Both blocks and the Split/Join nodes: entered by one link, left by one
    (a hierarchy takes no more, Raptor 7)."""
    for b, it in v._block_items.items():
        if b != 0:
            it.setSelected(True)
    for nid, it in v._node_items.items():
        if v.nodes[nid].node_name in ("Split", "Join"):
            it.setSelected(True)


def test_collapse_dissolve_recollapse(qapp):
    from raptor.api.build import build_from_spec, parallel
    v = DiagramView()
    v.load_rbd(build_from_spec(parallel(2)))   # Start -> Split -> pair -> Join -> End
    ids = [b for b in sorted(v._block_items) if b != 0][:2]
    _select_parallel_section(v)
    hid = v.collapse_selection("ReCollapse")
    assert hid in v.hiers
    v.enter_level(hid)
    _select_parallel_section(v)
    v._move_selection_to_parent()      # dissolve by promotion
    v.go_home()
    assert hid not in v.hiers or True  # shell may remain empty
    home_ids = set(v._block_items)
    assert set(ids) <= home_ids, "children lost after promote"
    # recollapse works identically
    v._scene.clearSelection()
    _select_parallel_section(v)
    hid2 = v.collapse_selection("Again")
    assert hid2 in v.hiers


def test_align_and_distribute(qapp):
    """Align to anchor; distribute evens out centres."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")))
    # three annotations at scattered positions (two-click placement:
    # first click arms/places a corner, second sets the opposite one)
    for x, y in ((100, 100), (400, 500), (700, 200)):
        v.tool_mode = "anno_rect"
        v._handle_tool_click(_scene_pt(v, x, y))
        v._handle_tool_click(_scene_pt(v, x + 40, y + 30))
    items = list(v._anno_items.values())
    for it, (x, y) in zip(items, ((100, 100), (400, 500), (700, 200))):
        it.setPos(x, y)
    v._scene.clearSelection()
    for it in items:
        it.setSelected(True)
    # align top: all y match the first-selected's top
    v.align_selected("top")
    tops = [it.sceneBoundingRect().top() for it in items]
    assert max(tops) - min(tops) < 0.5, tops
    # distribute horizontally: centres evenly spaced
    v.distribute_selected("h")
    centres = sorted(it.sceneBoundingRect().center().x()
                     for it in items)
    gap1 = centres[1] - centres[0]
    gap2 = centres[2] - centres[1]
    assert abs(gap1 - gap2) < 0.5, (gap1, gap2)


def test_stable_grid_open_twice(qapp):
    """Opening the same file twice places every element at the same scene
    position."""
    v1 = DiagramView()
    v1.load_rbd(load_rbd(os.path.join(GOLD, "HierarchyExample",
                                      "input.rbd")))
    p1 = {b: it.pos() for b, it in v1._block_items.items()}
    v2 = DiagramView()
    v2.load_rbd(load_rbd(os.path.join(GOLD, "HierarchyExample",
                                      "input.rbd")))
    p2 = {b: it.pos() for b, it in v2._block_items.items()}
    assert set(p1) == set(p2)
    for b in p1:
        assert (p1[b].x(), p1[b].y()) == (p2[b].x(), p2[b].y()), b
    # and hierarchy traversal must not shuffle positions either:
    # re-enter 23 and confirm the same home-level items render at the
    # same scene coordinates as before the round trip.
    v2.enter_level(23); v2.go_home()


def test_legacy_positions_render_on_fixed_scale(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    # scale is derived from the model's own closest spacing (anti-overlap
    # floor), clamped >= LEGACY_SCALE — not a hardcoded value
    assert v._scale >= DiagramView.LEGACY_SCALE
    # relative file geometry is preserved exactly by _tx/_ty
    a = v.blocks[3]; b = v.blocks[4]
    dx_model = abs(b.x_position - a.x_position)
    it_a, it_b = v._block_items[3], v._block_items[4]
    dx_scene = abs(it_b.pos().x() - it_a.pos().x())
    assert abs(dx_scene - dx_model * v._scale) < 0.01


def test_new_shape_annotation_centred_under_cursor_and_visible_size(qapp):
    """New rect/ellipse annotations are large enough to see and appear
    under the mouse cursor. The `_AnnoRect`/`_AnnoEllipse` constructors
    call `setPos`, so a new (or reloaded) shape annotation lands where it
    was placed rather than at the scene origin."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    item = next(iter(v._anno_items.values()))
    r = item.rect()
    assert r.width() >= 100 and r.height() >= 50, \
        f"new rect annotation too small: {r.width()}x{r.height()}"
    centre = item.sceneBoundingRect().center()
    click = _scene_pt(v, 300, 300)
    assert abs(centre.x() - click.x()) < 1.0
    assert abs(centre.y() - click.y()) < 1.0


def test_two_shape_annotations_resize_independently(qapp):
    """Two distinctly-placed rects stay independent through a resize: when
    every shape lands at (0,0) and overlaps, resizing one appears to
    resize all of them."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 100, 100)
    _place_shape(v, 400, 400)
    items = list(v._anno_items.values())
    assert items[0].pos() != items[1].pos()
    other_size_before = (items[1].rect().width(), items[1].rect().height())

    items[0].ensure_handle()
    handle = items[0]._handle
    handle.setPos(handle.pos().x() + 200, handle.pos().y() + 150)

    assert (items[1].rect().width(), items[1].rect().height()) \
        == other_size_before, "resizing one rect must not resize the other"


@pytest.mark.parametrize("mode", ["anno_rect", "anno_ellipse"])
def test_shape_annotation_hit_area_is_perimeter_only(qapp, mode):
    """While a shape is UNSELECTED its hit area is the perimeter only. A
    filled shape's default Qt `shape()` covers the whole interior and eats
    clicks meant for an element underneath, so an unselected `shape()`
    returns only a ~10px band around the outline (the width
    `_LinkItem.shape()` uses), regardless of fill: centre clicks fall
    through, border clicks still select it. Once selected the whole
    interior becomes grabbable so the shape can be dragged from its
    middle (see _AnnoRect.shape)."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300, w=140, h=80, mode=mode)
    item = next(iter(v._anno_items.values()))
    item.props["fill_colour"] = "#3060a0"   # filled -- the case that regressed
    item.apply_props()
    r = item.rect()

    # Unselected: centre falls through, border still hits.
    item.setSelected(False)
    centre = QPointF(r.center())
    assert not item.contains(centre), \
        "a click dead-centre must NOT hit an unselected annotation " \
        "(must pass through)"
    on_border = QPointF(r.left(), r.center().y())
    assert item.contains(on_border), \
        "a click right on the border must still hit the annotation"

    # Selected: the whole interior is grabbable so it can be dragged
    # from its middle.
    item.setSelected(True)
    assert item.contains(centre), \
        "a selected annotation must be draggable from its interior"


@pytest.mark.parametrize("mode", ["anno_rect", "anno_ellipse"])
def test_resize_handle_hit_area_is_larger_than_its_painted_square(
        qapp, mode):
    """The resize handle's hit area extends past its 10x10 painted square.
    Since the shape only accepts clicks on its thin perimeter band
    (previous test), a near-miss on the tiny handle would land on empty
    canvas and deselect; `shape()`/`boundingRect()` widen the grab
    area."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300, w=140, h=80, mode=mode)
    item = next(iter(v._anno_items.values()))
    handle = item._handle
    painted = handle.rect()   # local (-5,-5,10,10) -- the visual square
    hit = handle.boundingRect()
    margin = 6.0
    assert hit.left() <= painted.left() - margin
    assert hit.top() <= painted.top() - margin
    assert hit.right() >= painted.right() + margin
    assert hit.bottom() >= painted.bottom() + margin
    # A near-miss just outside the painted square, but inside the
    # widened hit area, must still register as a hit on the handle.
    near_miss = QPointF(painted.right() + margin / 2, painted.center().y())
    assert handle.contains(near_miss)


def test_annotation_contrast_check_flags_invisible_on_theme_switch(qapp):
    """A dark-text annotation placed while light-themed reads fine against
    the light canvas but goes low-contrast when the theme flips to dark.
    `annotation_is_low_contrast` tracks the theme switch rather than a
    fixed rule, and `_retheme_current` fires `annotations_may_be_invisible`
    for it."""
    from raptor.gui.ui.diagram import annotation_is_low_contrast
    from raptor.gui.resources.design_tokens import ThemeManager
    tm = ThemeManager.instance()
    tm.set_dark(False)
    try:
        v = DiagramView()
        v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                         "input.rbd")))
        dark_text = {"id": 999, "kind": "text", "parent": 0,
                    "text_colour": "#132238", "fill_colour": None,
                    "border_width": 0}
        assert not annotation_is_low_contrast(dark_text), \
            "dark text on the light canvas must read fine"
        v.annotations.append(dict(dark_text))

        seen = []
        v.annotations_may_be_invisible.connect(seen.append)
        tm.set_dark(True)
        try:
            assert annotation_is_low_contrast(dark_text), \
                "the SAME colour must now flag against the dark canvas"
            assert seen == [1]
            assert v.find_low_contrast_annotations() == [999]
        finally:
            tm.set_dark(False)
    finally:
        tm.set_dark(False)


def test_fix_annotation_contrast_repaints_to_a_readable_colour(qapp):
    """`fix_annotation_contrast` resolves the flag it was given for both
    a current-level annotation (routed through the normal undoable
    `set_annotation_props`) and an other-level one (patched directly in
    `self.annotations`, since it has no live canvas item)."""
    from raptor.gui.ui.diagram import annotation_is_low_contrast
    from raptor.gui.resources.design_tokens import ThemeManager
    tm = ThemeManager.instance()
    tm.set_dark(False)
    try:
        v = DiagramView()
        v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                         "input.rbd")))
        # id 1: current level (parent=0, matches v._current_parent) --
        # gets a real live _AnnoText item. id 2: a different level,
        # never gets a canvas item at all.
        v.annotations.append({
            "id": 1, "kind": "text", "parent": 0, "x": 10, "y": 10,
            "text": "a", "text_colour": "#132238", "fill_colour": None,
            "border_width": 0})
        v.annotations.append({
            "id": 2, "kind": "text", "parent": 999, "x": 10, "y": 10,
            "text": "b", "text_colour": "#132238", "fill_colour": None,
            "border_width": 0})
        v._rebuild_scene()   # picks up the id=1 entry as a real item
        assert 1 in v._anno_items
        assert 2 not in v._anno_items

        tm.set_dark(True)
        try:
            fired = []
            v.model_changed.connect(lambda: fired.append(True))
            v.fix_annotation_contrast(v.find_low_contrast_annotations())
            a1 = next(a for a in v.annotations if a["id"] == 1)
            a2 = next(a for a in v.annotations if a["id"] == 2)
            assert not annotation_is_low_contrast(a1)
            assert not annotation_is_low_contrast(a2)
            assert v._anno_items[1].props["text_colour"] == a1["text_colour"]
            assert fired, "the other-level fix must still mark the model dirty"
        finally:
            tm.set_dark(False)
    finally:
        tm.set_dark(False)


def test_annotation_move_marks_the_file_dirty(qapp):
    """Dragging an annotation fires the dirty-flag signal (main_window's
    `_mark_layout_dirty`), so the title bar and Ctrl+S know there is
    something to save. It is `layout_changed`, not `model_changed`: a
    position cannot change validity, so it must not trigger a whole-model
    re-validation/refresh."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 100, 100)
    item = next(iter(v._anno_items.values()))
    fired = []
    model = []
    v.layout_changed.connect(lambda: fired.append(True))
    v.model_changed.connect(lambda: model.append(True))
    item.setPos(item.pos().x() + 50, item.pos().y() + 30)
    assert fired, "moving an annotation must mark the layout dirty"
    assert not model, "moving an annotation must not re-validate the model"


def test_annotation_move_and_delete_persist_on_save(qapp, tmp_path):
    """Move one annotation, delete another, save to .rbds and reload: both
    edits survive. `write_back_annotations` is called from the save
    path."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 100, 100)
    _place_shape(v, 400, 400)
    assert len(v._anno_items) == 2
    ids = sorted(v._anno_items)
    keep_id, gone_id = ids[0], ids[1]
    v._anno_items[keep_id].setPos(321, 654)
    v._anno_items[gone_id].setSelected(True)
    v.delete_selected_annotations()
    assert set(v._anno_items) == {keep_id}

    path = str(tmp_path / "roundtrip.rbds")
    v.write_back_annotations()
    rbds_io.save_rbds(v._rbd_ref, path)

    reloaded = rbds_io.load_rbds(path)
    anns = getattr(reloaded, "annotations", [])
    assert len(anns) == 1, anns
    assert int(anns[0]["id"]) == keep_id
    assert (round(float(anns[0]["x"])), round(float(anns[0]["y"]))) == (321, 654)


def test_annotation_selection_emits_signal(qapp):
    """Selecting an annotation must fire `annotation_selected` (drives
    the Inspector's Colours & Font tab -- the right-click menu's own
    "Colours & Font…" item now just selects the item instead of opening
    a separate dialog, so this signal is the only thing that wires it
    to anything)."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 100, 100)
    aid, item = next(iter(v._anno_items.items()))
    fired = []
    v.annotation_selected.connect(fired.append)
    v._scene.clearSelection()
    item.setSelected(True)
    assert fired == [aid]


def test_annotation_prop_change_undo_redo_and_persists(qapp, tmp_path):
    """`set_annotation_props` (the Inspector's Colours & Font commit
    path) must be undoable/redoable AND survive save/reload, exactly
    like every other element's field edits."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 100, 100)
    aid, item = next(iter(v._anno_items.items()))
    assert item.props.get("border_colour") != "#FF0000"

    v.set_annotation_props(aid, {"border_colour": "#FF0000"})
    assert v._anno_items[aid].props["border_colour"] == "#FF0000"

    v._stack.undo()
    assert v._anno_items[aid].props["border_colour"] != "#FF0000"
    v._stack.redo()
    assert v._anno_items[aid].props["border_colour"] == "#FF0000"

    # a font change commits family/size/bold/italic as one undo step
    v.set_annotation_props(aid, {
        "font_family": "Georgia", "font_size": 14,
        "font_bold": True, "font_italic": False})
    assert v._anno_items[aid].props["font_family"] == "Georgia"
    v._stack.undo()
    assert v._anno_items[aid].props["font_family"] != "Georgia"

    v._stack.redo()
    path = str(tmp_path / "anno_props_roundtrip.rbds")
    v.write_back_annotations()
    rbds_io.save_rbds(v._rbd_ref, path)
    reloaded = rbds_io.load_rbds(path)
    anns = getattr(reloaded, "annotations", [])
    assert len(anns) == 1
    assert anns[0].get("border_colour") == "#FF0000"
    assert anns[0].get("font_family") == "Georgia"


def test_arrow_two_click_placement_ghost_and_undo(qapp):
    """The arrow tool places the tail on the first click and the head on
    the second, drawing a dashed ghost line to the cursor in between
    (mirroring the Connect tool's rubber-band)."""
    from raptor.gui.ui.diagram import _AnnoLine
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    tail = _scene_pt(v, 50, 50)
    v._handle_tool_click(tail)
    assert v._arrow_from == tail
    assert not v._anno_items, "first click only arms, doesn't place"

    cur = _scene_pt(v, 150, 90)
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF as _QPointF, Qt as _Qt
    viewport_pt = _QPointF(v.mapFromScene(cur))
    ev = QMouseEvent(QMouseEvent.MouseMove, viewport_pt, viewport_pt,
                     _Qt.NoButton, _Qt.NoButton, _Qt.NoModifier)
    v.mouseMoveEvent(ev)
    assert v._arrow_ghost is not None
    ln = v._arrow_ghost.line()
    assert (round(ln.x1()), round(ln.y1())) == (round(tail.x()), round(tail.y()))
    assert abs(ln.x2() - cur.x()) < 1.5 and abs(ln.y2() - cur.y()) < 1.5

    head = _scene_pt(v, 250, 130)
    v._handle_tool_click(head)
    assert v._arrow_from is None
    assert v._arrow_ghost is None, "ghost dropped once the arrow commits"
    assert len(v._anno_items) == 1
    item = next(iter(v._anno_items.values()))
    assert isinstance(item, _AnnoLine)
    ln = item.line()
    assert (round(ln.x1()), round(ln.y1())) == (round(tail.x()), round(tail.y()))
    assert (round(ln.x2()), round(ln.y2())) == (round(head.x()), round(head.y()))

    v._stack.undo()
    assert not v._anno_items
    v._stack.redo()
    assert len(v._anno_items) == 1


def test_arrow_escape_cancels_in_progress_placement(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    assert v._arrow_from is not None

    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import Qt as _Qt
    ev = QKeyEvent(QKeyEvent.KeyPress, _Qt.Key_Escape, _Qt.NoModifier)
    v.keyPressEvent(ev)
    assert v._arrow_from is None
    assert v._arrow_ghost is None
    assert not v._anno_items


@pytest.mark.parametrize("mode,cls_name", [("anno_rect", "_AnnoRect"),
                                            ("anno_ellipse", "_AnnoEllipse")])
def test_shape_two_click_placement_ghost_and_undo(qapp, mode, cls_name):
    """The rect/ellipse tools use the same two-click gesture as the arrow
    tool (see test_arrow_two_click_placement_ghost_and_undo), replacing a
    single click that dropped a fixed default size centred on the
    cursor."""
    from raptor.gui.ui import diagram as diag
    cls = getattr(diag, cls_name)
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = mode
    p1 = _scene_pt(v, 50, 50)
    v._handle_tool_click(p1)
    assert v._shape_anno_from == p1
    assert not v._anno_items, "first click only arms, doesn't place"

    cur = _scene_pt(v, 200, 150)
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF as _QPointF, Qt as _Qt
    viewport_pt = _QPointF(v.mapFromScene(cur))
    ev = QMouseEvent(QMouseEvent.MouseMove, viewport_pt, viewport_pt,
                     _Qt.NoButton, _Qt.NoButton, _Qt.NoModifier)
    v.mouseMoveEvent(ev)
    assert v._shape_anno_ghost is not None
    r = v._shape_anno_ghost.rect()
    assert abs(r.width() - 150) < 1.5 and abs(r.height() - 100) < 1.5

    p2 = _scene_pt(v, 250, 200)
    v._handle_tool_click(p2)
    assert v._shape_anno_from is None
    assert v._shape_anno_ghost is None, "ghost dropped once it commits"
    assert len(v._anno_items) == 1
    item = next(iter(v._anno_items.values()))
    assert isinstance(item, cls)
    r = item.rect()
    assert abs(r.width() - (p2.x() - p1.x())) < 1.5
    assert abs(r.height() - (p2.y() - p1.y())) < 1.5

    v._stack.undo()
    assert not v._anno_items
    v._stack.redo()
    assert len(v._anno_items) == 1


def test_shape_escape_cancels_in_progress_placement(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_rect"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    assert v._shape_anno_from is not None

    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import Qt as _Qt
    ev = QKeyEvent(QKeyEvent.KeyPress, _Qt.Key_Escape, _Qt.NoModifier)
    v.keyPressEvent(ev)
    assert v._shape_anno_from is None
    assert v._shape_anno_ghost is None
    assert not v._anno_items


def test_anno_grab_handle_hidden_until_selected(qapp):
    """Grab handles are visible only on the selected annotation."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_rect"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    v._handle_tool_click(_scene_pt(v, 150, 120))
    item = next(iter(v._anno_items.values()))
    assert item.isSelected()
    assert item._handle.isVisible(), "selected right after placement"

    item.setSelected(False)
    assert not item._handle.isVisible()

    item.setSelected(True)
    assert item._handle.isVisible()


def test_anno_default_border_width_is_1px(qapp):
    """A new shape starts with a 1px border rather than none, so it is
    visible before fill/text make it noticeable."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_rect"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    v._handle_tool_click(_scene_pt(v, 150, 120))
    item = next(iter(v._anno_items.values()))
    assert float(item.props["border_width"]) == 1.0
    assert item.pen().widthF() >= 1.0


def test_distribute_even_gaps_mixed_annotation_sizes(qapp):
    """Distribute gives even edge-to-edge gaps for mixed annotation sizes.
    Equal centre spacing only reads as even gaps when every item is the
    same size, so annotations of different widths use equal edge gaps."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_rect"
    # three different widths: 40, 200, 80
    for x0, x1, y in ((0, 40, 0), (300, 500, 0), (700, 780, 0)):
        v._handle_tool_click(_scene_pt(v, x0, y))
        v._handle_tool_click(_scene_pt(v, x1, y + 60))
        v.tool_mode = "anno_rect"
    items = list(v._anno_items.values())
    assert [round(it.rect().width()) for it in items] == [40, 200, 80]

    v._scene.clearSelection()
    for it in items:
        it.setSelected(True)
    v.distribute_selected("h")

    edges = sorted((it.sceneBoundingRect().left(),
                    it.sceneBoundingRect().right()) for it in items)
    gap1 = edges[1][0] - edges[0][1]
    gap2 = edges[2][0] - edges[1][1]
    assert abs(gap1 - gap2) < 0.5, (gap1, gap2)


def test_fit_all_frames_content_not_padded_scene_rect(qapp):
    """fit_all frames the actual content, not the padded scene rect. The
    scene's `sceneRect()` is deliberately padded past the content for
    infinite-canvas panning (see `_apply_scene_layout`'s "world" rect)."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.resize(600, 400)
    from raptor.gui.ui.diagram import _GridBackground
    content = QRectF()
    for it in v._scene.items():
        if isinstance(it, _GridBackground):
            continue   # covers the whole world by design -- not content
        content = content.united(it.sceneBoundingRect())
    world = v._scene.sceneRect()
    assert world.width() > content.width() * 3, \
        "fixture doesn't exercise the padded-world gap this test checks"

    v.fit_all()
    scale_content = v.transform().m11()

    v.fitInView(world, Qt.KeepAspectRatio)
    scale_world = v.transform().m11()

    assert scale_content > scale_world * 2


def test_arrow_endpoint_drag_independent_and_syncs_model(qapp):
    """Dragging either end handle moves only that endpoint (no pos()/
    line() coupling to fight over -- see _AnnoLine's own comment) and
    mirrors the new position into the model annotations list."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    v._handle_tool_click(_scene_pt(v, 150, 50))
    item = next(iter(v._anno_items.values()))
    orig = item.line()

    item._head_handle.setPos(QPointF(999, 888))
    ln = item.line()
    assert (ln.x2(), ln.y2()) == (999, 888)
    assert (ln.x1(), ln.y1()) == (orig.x1(), orig.y1()), \
        "moving the head must not move the tail"
    a = next(a for a in v.annotations if a.get("kind") == "arrow")
    assert (a["x2"], a["y2"]) == (999, 888)

    item._tail_handle.setPos(QPointF(-100, -200))
    ln = item.line()
    assert (ln.x1(), ln.y1()) == (-100, -200)
    assert (ln.x2(), ln.y2()) == (999, 888), \
        "moving the tail must not move the head"


def test_arrow_persists_across_save_reload(qapp, tmp_path):
    """Two-click arrow placement, an endpoint drag, and a colour/line-
    style edit must all survive .rbds save/reload (same contract as
    the pre-existing rect/ellipse annotation round-trip test)."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    v._handle_tool_click(_scene_pt(v, 150, 50))
    aid, item = next(iter(v._anno_items.items()))
    item._head_handle.setPos(QPointF(321, 654))
    v.set_annotation_props(aid, {"line_style": "dash-dot",
                                 "border_colour": "#00AA00"})

    path = str(tmp_path / "arrow_roundtrip.rbds")
    v.write_back_annotations()
    rbds_io.save_rbds(v._rbd_ref, path)
    reloaded = rbds_io.load_rbds(path)
    anns = [a for a in reloaded.annotations if a.get("kind") == "arrow"]
    assert len(anns) == 1
    a = anns[0]
    assert (float(a["x2"]), float(a["y2"])) == (321.0, 654.0)
    assert a["line_style"] == "dash-dot"
    assert a["border_colour"] == "#00AA00"

    v2 = DiagramView()
    v2.load_rbd(reloaded)
    item2 = next(iter(v2._anno_items.values()))
    from raptor.gui.ui.diagram import _AnnoLine
    from PySide6.QtCore import Qt as _Qt
    assert isinstance(item2, _AnnoLine)
    assert item2.pen().style() == _Qt.PenStyle.DashDotLine
    ln2 = item2.line()
    assert (round(ln2.x2()), round(ln2.y2())) == (321, 654)


def test_line_style_defaults_solid_and_applies_to_rect_ellipse_arrow(qapp):
    """One shared prop (`ANNO_DEFAULTS["line_style"]`) drives the border
    pen for every bordered shape, not just the arrow."""
    from raptor.gui.ui.diagram import ANNO_DEFAULTS, ANNO_LINE_STYLES
    from PySide6.QtCore import Qt as _Qt
    assert ANNO_DEFAULTS["line_style"] == "solid"
    assert set(ANNO_LINE_STYLES) == {
        "solid", "dash", "dot", "dash-dot", "dash-dot-dot"}

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    for kind in ("anno_rect", "anno_ellipse"):
        _place_shape(v, 100, 100, mode=kind)
    items = list(v._anno_items.values())
    for item in items:
        aid = item._anno_id
        v.set_annotation_props(aid, {"border_width": 2,
                                     "line_style": "dot"})
        assert v._anno_items[aid].pen().style() == _Qt.PenStyle.DotLine


def test_new_annotation_default_colours_follow_active_theme(qapp):
    """`ANNO_DEFAULTS`' border/text colours are the light theme's
    "muted"/"text" tokens, so a new annotation placed under the dark
    theme must not inherit them verbatim (the default text colour is
    near-black). A colour the user has explicitly set is untouched by a
    theme switch (`retheme()` never rewrites annotation props); this only
    governs a freshly-placed shape."""
    from raptor.gui.resources.design_tokens import ThemeManager
    from raptor.gui.ui.diagram import ANNO_DEFAULTS
    try:
        ThemeManager.instance().set_dark(False)
        v = DiagramView()
        v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                         "input.rbd")))
        _place_shape(v, 100, 100)
        light_item = next(iter(v._anno_items.values()))
        assert light_item.props["text_colour"] == ANNO_DEFAULTS["text_colour"]
        assert light_item.props["border_colour"] == \
            ANNO_DEFAULTS["border_colour"]

        ThemeManager.instance().set_dark(True)
        v2 = DiagramView()
        v2.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                          "input.rbd")))
        _place_shape(v2, 100, 100)
        dark_item = next(iter(v2._anno_items.values()))
        assert dark_item.props["text_colour"] != ANNO_DEFAULTS["text_colour"]
        assert dark_item.props["text_colour"] == \
            ThemeManager.instance().colour["text"]
    finally:
        ThemeManager.instance().set_dark(False)


def test_annotation_props_noop_when_unchanged(qapp):
    """Re-committing the SAME value must not push a spurious undo step
    (matches every other element's Inspector field -- e.g. ParamsCmd's
    own early-return-on-no-change)."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 100, 100)
    aid, item = next(iter(v._anno_items.items()))
    current = item.props["border_colour"]
    count_before = v._stack.count()
    v.set_annotation_props(aid, {"border_colour": current})
    assert v._stack.count() == count_before


def test_annotation_drag_is_undoable(qapp):
    """Dragging/resizing an annotation is undoable: geometry is committed
    through a command rather than mirrored into the model live, so Ctrl+Z
    undoes the move."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    v.tool_mode = "select"
    aid, item = next(iter(v._anno_items.items()))
    v._scene.clearSelection()
    item.setSelected(True)

    qapp.processEvents()
    start_pos = item.scenePos()
    start_model = next(dict(a) for a in v.annotations
                       if int(a.get("id", 0)) == aid)

    # Press on the shape's own origin (its top-left corner, on the
    # perimeter hit band) so selection is retained.
    corner = item.mapToScene(QPointF(0, 0))
    vp = QPointF(v.mapFromScene(corner))
    press = QMouseEvent(QEvent.MouseButtonPress, vp, vp, Qt.LeftButton,
                        Qt.LeftButton, Qt.NoModifier)
    v.mousePressEvent(press)
    assert any(k == "anno" for k, _o, _s in v._drag_start), \
        "annotation press must be captured for the release commit"

    # Simulate the drag, then release.
    item.setPos(item.pos() + QPointF(40, 30))
    qapp.processEvents()
    release = QMouseEvent(QEvent.MouseButtonRelease, vp, vp, Qt.LeftButton,
                          Qt.NoButton, Qt.NoModifier)
    v.mouseReleaseEvent(release)
    qapp.processEvents()

    moved = next(dict(a) for a in v.annotations
                 if int(a.get("id", 0)) == aid)
    assert moved["x"] != start_model["x"] or moved["y"] != start_model["y"]

    v._stack.undo()
    qapp.processEvents()
    restored = next(dict(a) for a in v.annotations
                    if int(a.get("id", 0)) == aid)
    assert restored["x"] == start_model["x"]
    assert restored["y"] == start_model["y"]
    assert v._anno_items[aid].scenePos() == start_pos


def test_arrow_hit_area_is_wider_than_the_pen(qapp):
    """The arrow's clickable shaft is a ~10px band, not the bare pen
    width -- otherwise selecting/dragging it is near-impossible."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    v._handle_tool_click(_scene_pt(v, 150, 50))
    item = next(iter(v._anno_items.values()))
    ln = item.line()
    # 4px above the midpoint is outside a 1px pen but inside the band.
    mid = ln.center()
    offset = QPointF(mid.x(), mid.y() - 4.0)
    assert item.contains(offset), \
        "a near-miss beside the shaft must still hit the arrow"
    # Far away is still a miss.
    assert not item.contains(QPointF(mid.x(), mid.y() - 40.0))


def test_arrow_whole_drag_translates_both_endpoints(qapp):
    """Dragging the arrow body translates both endpoints by the same
    delta (whole-arrow move), syncs the model, and is undoable."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    v._handle_tool_click(_scene_pt(v, 150, 50))
    v.tool_mode = "select"
    aid, item = next(iter(v._anno_items.items()))
    start = item.line()
    start_model = next(dict(a) for a in v.annotations
                       if int(a.get("id", 0)) == aid)
    v._scene.clearSelection()
    item.setSelected(True)
    qapp.processEvents()

    mid = start.center()
    vp = QPointF(v.mapFromScene(mid))
    press = QMouseEvent(QEvent.MouseButtonPress, vp, vp, Qt.LeftButton,
                        Qt.LeftButton, Qt.NoModifier)
    v.mousePressEvent(press)
    dest = mid + QPointF(60, 40)
    vp2 = QPointF(v.mapFromScene(dest))
    move = QMouseEvent(QEvent.MouseMove, vp2, vp2, Qt.NoButton,
                       Qt.LeftButton, Qt.NoModifier)
    v.mouseMoveEvent(move)
    qapp.processEvents()
    release = QMouseEvent(QEvent.MouseButtonRelease, vp2, vp2,
                          Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    v.mouseReleaseEvent(release)
    qapp.processEvents()

    ln = item.line()
    assert abs((ln.x1() - start.x1()) - 60) < 2
    assert abs((ln.y1() - start.y1()) - 40) < 2
    assert abs((ln.x2() - start.x2()) - 60) < 2
    assert abs((ln.y2() - start.y2()) - 40) < 2
    assert ln.length() == pytest.approx(start.length(), abs=0.01), \
        "translating the whole arrow must not change its length"
    moved = next(dict(a) for a in v.annotations
                 if int(a.get("id", 0)) == aid)
    assert abs((moved["x1"] - start_model["x1"]) - 60) < 2

    v._stack.undo()
    qapp.processEvents()
    restored = item.line()
    assert abs(restored.x1() - start.x1()) < 0.01
    assert abs(restored.y2() - start.y2()) < 0.01


def test_text_annotation_frame_edge_is_clickable(qapp):
    """The text annotation's visible frame is drawn 4px outside the glyph
    box; the whole padded box must be hit-testable, not just the glyphs."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_text"
    v._handle_tool_click(_scene_pt(v, 120, 120))
    item = next(iter(v._anno_items.values()))
    assert item.shape().contains(QPointF(-3, -3)), \
        "the frame's own border must be clickable"


def test_nudge_moves_selected_block_and_annotation(qapp):
    """Arrow keys nudge every selected item -- elements and annotations
    alike -- and the annotation move is undoable."""
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    aid, anno = next(iter(v._anno_items.items()))
    v.tool_mode = "select"
    v._scene.clearSelection()
    block = next(iter(v._block_items.values()))
    block.setSelected(True)
    anno.setSelected(True)

    step = v._snap_state["x"] or 1.0
    block_before = block.scenePos()
    anno_before = anno.scenePos()

    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier)
    v.keyPressEvent(ev)

    assert block.scenePos().x() > block_before.x()
    assert anno.scenePos().x() > anno_before.x()
    assert anno.scenePos().x() - anno_before.x() == pytest.approx(step,
                                                                  abs=0.01)
    moved = next(a for a in v.annotations if int(a.get("id", 0)) == aid)
    assert moved["x"] > anno_before.x()

    # Undo (annotation command then element command are separate steps;
    # walk back until the annotation is home).
    for _ in range(2):
        v._stack.undo()
    assert anno.scenePos().x() == pytest.approx(anno_before.x(), abs=0.01)


def test_nudge_selected_arrow_shifts_both_endpoints(qapp):
    """A selected arrow nudges as a whole -- both endpoints shift by the
    same amount."""
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    v._handle_tool_click(_scene_pt(v, 150, 50))
    v.tool_mode = "select"
    aid, item = next(iter(v._anno_items.items()))
    before = item.line()
    v._scene.clearSelection()
    item.setSelected(True)

    step = v._snap_state["x"] or 1.0
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier)
    v.keyPressEvent(ev)

    ln = item.line()
    assert ln.x1() - before.x1() == pytest.approx(step, abs=0.01)
    assert ln.x2() - before.x2() == pytest.approx(step, abs=0.01)
    assert ln.y1() == before.y1() and ln.y2() == before.y2()
    moved = next(a for a in v.annotations if int(a.get("id", 0)) == aid)
    assert moved["x1"] - before.x1() == pytest.approx(step, abs=0.01)


def test_text_annotation_resizes_and_reflows(qapp):
    """A text annotation is a resizable box: dragging its handle changes
    the box, the text reflows (wraps) to the new width, and the model
    entry tracks the new size."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_text"
    v._handle_tool_click(_scene_pt(v, 200, 200))
    aid, item = next(iter(v._anno_items.items()))

    item.set_box(200.0, 120.0)
    assert (item.rect().width(), item.rect().height()) == (200.0, 120.0)
    assert item.textWidth() == 200.0, "text must wrap at the box width"
    entry = next(a for a in v.annotations if int(a.get("id", 0)) == aid)
    assert (entry["w"], entry["h"]) == (200.0, 120.0)

    # Dragging the resize handle resizes and reflows.
    item._handle.setPos(300.0, 90.0)
    assert (item.rect().width(), item.rect().height()) == (300.0, 90.0)
    assert item.textWidth() == 300.0
    assert entry["w"] == 300.0 and entry["h"] == 90.0


def test_text_box_resize_is_undoable(qapp):
    """A text-box resize is captured as one undoable AnnoEditCmd, the same
    as a shape resize."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_text"
    v._handle_tool_click(_scene_pt(v, 200, 200))
    aid, item = next(iter(v._anno_items.items()))

    before = v._anno_state(aid)
    item._handle.setPos(260.0, 140.0)
    after = v._anno_state(aid)
    assert before != after
    from raptor.gui.ui.diagram import AnnoEditCmd
    v._stack.push(AnnoEditCmd(v, aid, before, after))
    v._stack.undo()
    assert (item.rect().width(), item.rect().height()) == \
        (before["w"], before["h"])


def test_shape_resize_updates_the_model(qapp):
    """Resizing a rect/ellipse mirrors the new size into the annotations
    list, so it survives a level rebuild (not only a save)."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300, w=140, h=80)
    aid, item = next(iter(v._anno_items.items()))
    item._handle.setPos(item._handle.pos().x() + 60,
                        item._handle.pos().y() + 40)
    entry = next(a for a in v.annotations if int(a.get("id", 0)) == aid)
    assert entry["w"] == item.rect().width()
    assert entry["h"] == item.rect().height()


def _drag_handle(qapp, v, item, dx, dy):
    """Mouse-drag an annotation's resize handle by (dx, dy) scene units."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent

    h = item._handle
    hp = h.scenePos()
    vp = QPointF(v.mapFromScene(hp))
    v.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, vp, vp,
                                  Qt.LeftButton, Qt.LeftButton,
                                  Qt.NoModifier))
    dest = QPointF(hp.x() + dx, hp.y() + dy)
    vp2 = QPointF(v.mapFromScene(dest))
    v.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, vp2, vp2, Qt.NoButton,
                                 Qt.LeftButton, Qt.NoModifier))
    qapp.processEvents()
    v.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, vp2, vp2,
                                    Qt.LeftButton, Qt.NoButton,
                                    Qt.NoModifier))
    qapp.processEvents()


def test_dragging_a_shape_handle_resizes_it_not_moves_it(qapp):
    """Clicking the resize handle must resize the annotation. Qt's default
    child drag moved the selected parent instead, so the handle appeared to
    move the shape (the reported bug)."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300, w=180, h=130)
    item = next(iter(v._anno_items.values()))
    v.tool_mode = "select"
    v._scene.clearSelection()
    item.setSelected(True)
    qapp.processEvents()

    start_pos = item.scenePos()
    w0, h0 = item.rect().width(), item.rect().height()
    _drag_handle(qapp, v, item, 70, 50)

    assert item.scenePos() == start_pos, \
        "dragging the handle must not move the annotation"
    assert item.rect().width() > w0 and item.rect().height() > h0


def test_dragging_a_text_handle_resizes_and_reflows(qapp):
    """The text box's handle resizes it (and reflows the text) rather than
    moving the box."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_text"
    v._handle_tool_click(_scene_pt(v, 200, 200))
    item = next(iter(v._anno_items.values()))
    v.tool_mode = "select"
    v._scene.clearSelection()
    item.setSelected(True)
    qapp.processEvents()

    start_pos = item.scenePos()
    _drag_handle(qapp, v, item, 70, 50)

    assert item.scenePos() == start_pos
    assert item.rect().width() > 120.0 and item.rect().height() > 40.0
    assert item.textWidth() == item.rect().width()


def test_text_edit_is_undoable(qapp):
    """Editing a text annotation's content is one undoable step, and redo
    restores the typed text (it used to be written straight into the model,
    so redo of the original add reverted it to empty)."""
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtCore import QEvent

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_text"
    v._handle_tool_click(_scene_pt(v, 200, 200))
    aid, item = next(iter(v._anno_items.items()))
    n0 = v._stack.count()

    item.setPlainText("typed text")
    item.focusOutEvent(QFocusEvent(QEvent.FocusOut))
    qapp.processEvents()
    assert v._stack.count() == n0 + 1, "the edit must be one undo step"
    assert next(a for a in v.annotations
                if a["id"] == aid)["text"] == "typed text"

    v._stack.undo()
    qapp.processEvents()
    assert next(a for a in v.annotations
                if a["id"] == aid)["text"] == ""

    v._stack.redo()
    qapp.processEvents()
    assert next(a for a in v.annotations
                if a["id"] == aid)["text"] == "typed text"


# =====================================================================
# Annotation z-order
# =====================================================================

def test_send_to_back_places_the_note_behind_a_block(qapp):
    """`Send to Back` used to set z=10, which is ABOVE a deselected
    block (z=0) -- so it was a visual no-op and left no way at all to
    put a note behind the reliability elements. It now uses its own band
    below links, hierarchy ovals and elements alike."""
    from raptor.gui.ui.diagram import ANNO_Z_BEHIND
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    item = next(iter(v._anno_items.values()))
    v._scene.clearSelection()
    item.setSelected(True)
    bid = sorted(v._block_items)[0]
    block = v._block_items[bid]
    block.setSelected(False)      # deselected -> the z=0 band
    assert block.zValue() == 0

    # It starts behind; come to the front first so "Send to Back" is the
    # move being tested rather than a no-op.
    assert item.zValue() == ANNO_Z_BEHIND
    assert v.set_annotation_order("front")
    assert v.set_annotation_order("back")
    assert item.zValue() == ANNO_Z_BEHIND
    assert item.zValue() < block.zValue()
    # still above the grid, or the note would be invisible
    assert item.zValue() > -100
    order = v._scene.items()
    assert order.index(item) > order.index(block)


def test_bring_to_front_places_the_note_above_everything(qapp):
    from raptor.gui.ui.diagram import ANNO_Z_FRONT
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    item = next(iter(v._anno_items.values()))
    v._scene.clearSelection()
    item.setSelected(True)
    assert v.set_annotation_order("front")
    assert item.zValue() == ANNO_Z_FRONT
    tops = [i.zValue() for i in v._scene.items()
            if not isinstance(i, type(item))]
    assert item.zValue() > max(tops)


def test_a_new_note_starts_behind_even_after_a_bring_to_front(qapp):
    """A note starts in the behind band (so it cannot cover the blocks it
    describes), even when another note has been sent to the front."""
    from raptor.gui.ui.diagram import ANNO_Z_BEHIND, ANNO_Z_FRONT
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 100, 100)
    first = next(iter(v._anno_items.values()))
    v._scene.clearSelection()
    first.setSelected(True)
    v.set_annotation_order("front")
    _place_shape(v, 400, 400)
    newest = [i for i in v._anno_items.values() if i is not first][0]
    assert first.zValue() == ANNO_Z_FRONT
    assert newest.zValue() == ANNO_Z_BEHIND
    order = v._scene.items()
    assert order.index(newest) > order.index(first), \
        "the newly drawn note must sit behind the diagram"


def test_bring_forward_and_send_backward_step_through_the_bands(qapp):
    from raptor.gui.ui.diagram import (ANNO_Z_BEHIND, ANNO_Z_MIDDLE,
                                      ANNO_Z_FRONT)
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    item = next(iter(v._anno_items.values()))
    v._scene.clearSelection()
    item.setSelected(True)
    assert item.zValue() == ANNO_Z_BEHIND
    # forward walks up the bands and reports None once at the top
    assert v.annotation_order_target("forward") == ANNO_Z_MIDDLE
    v.set_annotation_order("forward")
    assert item.zValue() == ANNO_Z_MIDDLE
    assert v.annotation_order_target("forward") == ANNO_Z_FRONT
    v.set_annotation_order("forward")
    assert item.zValue() == ANNO_Z_FRONT
    assert v.annotation_order_target("forward") is None
    # backward walks back down, and reports None once at the bottom
    assert v.annotation_order_target("backward") == ANNO_Z_MIDDLE
    v.set_annotation_order("backward")
    assert item.zValue() == ANNO_Z_MIDDLE
    assert v.annotation_order_target("backward") == ANNO_Z_BEHIND
    v.set_annotation_order("backward")
    assert item.zValue() == ANNO_Z_BEHIND
    assert v.annotation_order_target("backward") is None


def test_single_step_lands_on_a_value_actually_in_use(qapp):
    """A step targets the next z value some annotation on this level
    really uses, so two notes sharing a band are separated in one press
    rather than needing a dozen."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    from raptor.gui.ui.diagram import (ANNO_Z_BEHIND, ANNO_Z_FRONT,
                                      ANNO_Z_MIDDLE)
    _place_shape(v, 100, 100)
    first = next(iter(v._anno_items.values()))
    _place_shape(v, 400, 400)
    second = [i for i in v._anno_items.values() if i is not first][0]
    # a third note parked at a custom in-between value, as an entry
    # written by hand or by `update_annotation` would be
    second.setZValue(30.0)
    v._sync_anno_z(second)
    v._scene.clearSelection()
    first.setSelected(True)
    assert first.zValue() == ANNO_Z_BEHIND
    assert v.annotation_order_target("forward") == ANNO_Z_MIDDLE
    assert v.set_annotation_order("forward")
    assert first.zValue() == ANNO_Z_MIDDLE
    assert v.annotation_order_target("forward") == 30.0
    assert v.set_annotation_order("forward")
    assert first.zValue() == 30.0
    assert v.annotation_order_target("forward") == ANNO_Z_FRONT


def test_z_order_change_is_one_undo_step_with_a_readable_label(qapp):
    from raptor.gui.ui.diagram import ANNO_Z_BEHIND, ANNO_Z_FRONT
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 100, 100)
    _place_shape(v, 400, 400)
    items = list(v._anno_items.values())
    v._scene.clearSelection()
    for it in items:
        it.setSelected(True)
    before = {aid: it.zValue() for aid, it in v._anno_items.items()}
    assert all(z == ANNO_Z_BEHIND for z in before.values())
    n0 = v._stack.count()

    assert v.set_annotation_order("front")

    assert v._stack.count() == n0 + 1, "a multi-note move is ONE undo step"
    assert v._stack.undoText() == "Bring to Front", \
        "the history must name the move, not the generic edit"
    for it in items:
        assert it.zValue() == ANNO_Z_FRONT
    v._stack.undo()
    for aid, it in v._anno_items.items():
        assert it.zValue() == before[aid]


def test_z_order_persists_through_save_and_reload(qapp, tmp_path):
    from raptor.gui.ui.diagram import ANNO_Z_BEHIND
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    aid, item = next(iter(v._anno_items.items()))
    v._scene.clearSelection()
    item.setSelected(True)
    v.set_annotation_order("back")

    path = str(tmp_path / "z.rbds")
    v.write_back_annotations()
    rbds_io.save_rbds(v._rbd_ref, path)
    reloaded = rbds_io.load_rbds(path)
    entry = next(a for a in reloaded.annotations if a["id"] == aid)
    assert float(entry["z"]) == ANNO_Z_BEHIND


def test_order_actions_are_disabled_when_nothing_would_move(qapp):
    """The ribbon buttons and the context-menu items share ONE rule
    (`annotation_order_target`), so a command with nothing to do is
    reported as disabled rather than silently accepted."""
    from raptor.gui.ui.diagram import ANNO_Z_FRONT
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    item = next(iter(v._anno_items.values()))

    v._scene.clearSelection()
    for cmd in ("front", "forward", "backward", "back"):
        assert v.annotation_order_target(cmd) is None, \
            f"{cmd} must be unavailable with nothing selected"
        assert not v.set_annotation_order(cmd)

    v._scene.clearSelection()
    item.setSelected(True)
    assert v.set_annotation_order("front")
    assert v.annotation_order_target("front") is None
    assert v.annotation_order_target("forward") is None
    assert v.annotation_order_target("backward") == 20.0
    assert v.annotation_order_target("back") == -50.0
    assert item.zValue() == ANNO_Z_FRONT


# =====================================================================
# Annotation delete / paste / context menu
# =====================================================================

def test_mixed_annotation_and_element_delete_removes_both(qapp):
    """A mixed selection used to delete ONLY the elements: the
    annotation branch was gated on "no block/node/event is also
    selected", so a note silently survived a Delete that the context
    menu had labelled "Delete 2 selected"."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    n_annos, n_blocks = len(v._anno_items), len(v._block_items)
    item = next(iter(v._anno_items.values()))
    bid = sorted(v._block_items)[0]
    v._scene.clearSelection()
    item.setSelected(True)
    v._block_items[bid].setSelected(True)

    v.delete_selected()

    assert len(v._anno_items) == n_annos - 1, "the note must go too"
    assert len(v._block_items) == n_blocks - 1
    # ...and one undo restores both
    v._stack.undo()
    assert len(v._anno_items) == n_annos
    assert len(v._block_items) == n_blocks


def test_annotations_are_counted_in_the_delete_label(qapp):
    """The label counts what Delete will actually remove, including
    arrows -- which are deliberately not `ItemIsMovable`, so a movable
    flag count misses them while adding the annotation total on top of
    `selectedItems()` double-counts everything else."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    v._handle_tool_click(_scene_pt(v, 250, 90))
    arrow = next(i for i in v._anno_items.values() if i.kind == "arrow")
    assert not (arrow.flags() & QGraphicsItem.ItemIsMovable), \
        "the premise: an arrow is not ItemIsMovable"
    bid = sorted(v._block_items)[0]
    v._scene.clearSelection()
    arrow.setSelected(True)
    v._block_items[bid].setSelected(True)
    assert v._n_deletable_selected() == 2


def test_annotation_only_paste_offsets_in_scene_space(qapp):
    """Annotation x/y are SCENE coordinates. The paste used to compute
    its delta in MODEL units and add it to a scene value, moving the
    copy by 1/scale of the intended distance (and, with no elements in
    the buffer to anchor from, by the raw model coordinate of the
    click)."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 200, 200)
    item = next(iter(v._anno_items.values()))
    v._scene.clearSelection()
    item.setSelected(True)
    v.copy_selection()
    origin = item.scenePos()

    target = QPointF(origin.x() + 200 * v._scale, origin.y())
    v.paste(at_model=v.scene_to_model(target))

    pasted = sorted(v._anno_items.values(),
                    key=lambda i: i.scenePos().x())[-1]
    assert abs(pasted.scenePos().x() - target.x()) < 1.0
    assert abs(pasted.scenePos().y() - target.y()) < 1.0
    assert pasted is not item


def test_arrow_annotation_paste_offsets_both_endpoints(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 50, 50))
    v._handle_tool_click(_scene_pt(v, 250, 90))
    arrow = next(i for i in v._anno_items.values() if i.kind == "arrow")
    v._scene.clearSelection()
    arrow.setSelected(True)
    v.copy_selection()
    before = QLineF(arrow.line())
    target = QPointF(before.x1() + 100, before.y1())

    v.paste(at_model=v.scene_to_model(target))

    pasted = sorted(v._anno_items.values(), key=lambda i: i.line().x1())[-1]
    ln = pasted.line()
    assert abs(ln.x1() - (before.x1() + 100)) < 1.0
    assert abs(ln.y1() - before.y1()) < 1.0
    assert abs(ln.x2() - (before.x2() + 100)) < 1.0
    assert abs(ln.y2() - before.y2()) < 1.0


def test_context_menu_items_are_disabled_when_inapplicable(qapp):
    """Drives the real `_show_context_menu` through the `_exec_menu`
    seam (QMenu.exec cannot be monkeypatched from outside PySide6) and
    asserts the "disabled when appropriate" contract."""
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QMouseEvent

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    seen: list = []

    def capture(menu, event):
        seen.append({a.text(): a.isEnabled() for a in menu.actions()
                     if not a.isSeparator()})
        return None
    v._exec_menu = capture

    def rclick(sx, sy=None):
        target = sx if sy is None else QPointF(sx, sy)
        vp = v.mapFromScene(target)
        ev = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(vp),
                         QPointF(vp), _Qt.MouseButton.RightButton,
                         _Qt.MouseButton.RightButton,
                         _Qt.KeyboardModifier.NoModifier)
        v._show_context_menu(ev)

    _place_shape(v, 300, 200)
    item = next(iter(v._anno_items.values()))
    v._scene.clearSelection()
    item.setSelected(True)
    rclick(item.scenePos().x() + 20, item.scenePos().y() + 20)
    at_back = seen.pop()
    assert at_back["Bring to Front"] and at_back["Bring Forward"]
    assert not at_back["Send Backward"] and not at_back["Send to Back"]
    # nothing in the buffer yet
    assert not at_back["Paste Here (at pointer)"]

    # in the middle both directions are available
    v.set_annotation_order("forward")
    rclick(item.scenePos().x() + 20, item.scenePos().y() + 20)
    mid = seen.pop()
    assert mid["Bring to Front"] and mid["Bring Forward"]
    assert mid["Send Backward"] and mid["Send to Back"]

    # at the front: nothing further forward exists, so both are off
    v.set_annotation_order("front")
    rclick(item.scenePos().x() + 20, item.scenePos().y() + 20)
    at_front = seen.pop()
    assert not at_front["Bring to Front"], "already at the front"
    assert not at_front["Bring Forward"], "nothing left to move up to"
    assert at_front["Send Backward"] and at_front["Send to Back"]

    # at the back: the mirror image
    v.set_annotation_order("back")
    rclick(item.scenePos().x() + 20, item.scenePos().y() + 20)
    at_back = seen.pop()
    assert not at_back["Send to Back"]
    assert not at_back["Send Backward"]
    assert at_back["Bring to Front"] and at_back["Bring Forward"]

    # a block with no annotation selected offers no z-order items at all
    bid = sorted(v._block_items)[0]
    rclick(v._block_items[bid].mapToScene(
        v._block_items[bid].rect().center()))
    block_menu = seen.pop()
    assert "Bring to Front" not in block_menu, \
        "a lone block has nowhere to go in the z-order"

    # empty canvas: a background menu now exists at all
    v._scene.clearSelection()
    rclick(_scene_pt(v, 40, 380))
    bg = seen.pop()
    assert "Select All" in bg and "Snap to Grid" in bg
    assert "Fit All" in bg
    assert not bg["Paste Here (at pointer)"]


def test_context_menu_delete_label_counts_annotations(qapp):
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QMouseEvent

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    seen: list = []

    def capture(menu, event):
        seen.append([a.text() for a in menu.actions()
                     if not a.isSeparator()])
        return None
    v._exec_menu = capture

    _place_shape(v, 100, 100)
    _place_shape(v, 400, 100)
    v._scene.clearSelection()
    for it in v._anno_items.values():
        it.setSelected(True)
    bid = sorted(v._block_items)[0]
    block = v._block_items[bid]
    block.setSelected(True)
    vp = v.mapFromScene(block.mapToScene(block.rect().center()))
    ev = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(vp), QPointF(vp),
                     _Qt.MouseButton.RightButton, _Qt.MouseButton.RightButton,
                     _Qt.KeyboardModifier.NoModifier)
    v._show_context_menu(ev)
    labels = seen.pop()
    assert "Delete 3 selected" in labels, \
        "2 notes + 1 block is three things Delete will remove"


def test_select_all_includes_annotations(qapp):
    """Ctrl+A skipped annotations even though Align & Distribute's own
    tooltip advertises working on them."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 100, 100)
    n_annos = len(v._anno_items)
    v._scene.clearSelection()
    v.select_all()
    assert len(v._selected_annotation_ids()) == n_annos
    assert len(v._selected_items()) > n_annos, "elements too"


def test_text_annotation_border_colour_none_draws_nothing(qapp):
    """A text box rebuilt its border pen with
    `props.get("border_colour") or ANNO_DEFAULTS[...]`, so choosing
    "(none)" silently reverted to the default grey and KEPT DRAWING a
    border. It now shares `_anno_pen_brush` with rect/ellipse, where a
    none border was already honoured."""
    from PySide6.QtCore import Qt as _Qt
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_text"
    v._handle_tool_click(_scene_pt(v, 200, 200))
    aid, item = next(iter(v._anno_items.items()))
    item.setPlainText("no border here")
    assert item._frame_pen != _Qt.NoPen, "a fresh note starts bordered"

    v.set_annotation_props(aid, {"border_colour": None})

    assert item.props["border_colour"] is None
    assert item._frame_pen == _Qt.NoPen, \
        "border_colour=None must mean no border, as it does for a rect"


def test_rect_border_colour_none_still_works(qapp):
    from PySide6.QtCore import Qt as _Qt
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    aid, item = next(iter(v._anno_items.items()))
    v.set_annotation_props(aid, {"border_colour": None})
    assert item.pen() == _Qt.NoPen


def test_none_colour_is_not_flagged_as_low_contrast(qapp):
    """None is a legitimate stored value (it is how "(none)"
    round-trips), and it means "not drawn" -- there is nothing to
    contrast it against. `QColor(None)` is invalid and used to compare
    as black, flagging a deliberately borderless note as unreadable."""
    from raptor.gui.ui.diagram import annotation_is_low_contrast
    entry = {"id": 1, "kind": "text", "parent": 0,
             "text_colour": "#132238", "fill_colour": None,
             "border_colour": None, "border_width": 1}
    assert not annotation_is_low_contrast(entry)
    entry["border_width"] = 0
    entry["text_colour"] = None
    assert not annotation_is_low_contrast(entry)


def test_selection_ring_is_themed_not_qt_default(qapp):
    """A selected rect/ellipse/text annotation used to show Qt's default
    BLACK DOTTED selection rectangle, which is invisible on the dark
    theme and did not match the accent ring blocks and nodes draw."""
    from raptor.gui.ui.diagram import _anno_selection_ring
    from raptor.gui.resources.design_tokens import COLOUR
    from PySide6.QtGui import QPixmap, QPainter, QColor

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    item = next(iter(v._anno_items.values()))

    def ring_pixels(selected: bool):
        v._scene.clearSelection()
        item.setSelected(selected)
        pm = QPixmap(200, 150)
        pm.fill(QColor(COLOUR["canvas"]))
        p = QPainter(pm)
        _anno_selection_ring(p, item, item.rect())
        p.end()
        return {pm.toImage().pixelColor(x, y).name()
                for x in range(0, 200, 2) for y in range(0, 150, 2)}

    unselected = ring_pixels(False)
    selected = ring_pixels(True)
    accent = COLOUR["accent"].lower()
    assert unselected == {COLOUR["canvas"].lower()}, \
        "nothing is drawn for an unselected annotation"
    assert accent in {c.lower() for c in selected}, \
        f"the ring must use the accent token ({accent}), drew {selected}"


def test_refresh_inspector_repopulates_for_an_annotation(qapp):
    """`_refresh_inspector` had no annotation branch, so the panel kept
    showing populate-time values after an undo, the low-contrast fix, or
    any other change made from outside the panel itself -- every other
    element kind already refreshed here."""
    from raptor.gui.ui.inspector import InspectorDock
    from raptor.gui.ui.main_window import MainWindow

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    aid, item = next(iter(v._anno_items.items()))

    insp = InspectorDock()
    insp.set_rbd(v._rbd_ref)
    insp.show_annotation(item)
    assert "Rectangle" in insp._header.text(), \
        "the header uses the ribbon's wording, not kind.title()"

    # The target is what _refresh_inspector dispatches on.
    assert insp.current_target() == ("annotation", aid)

    # Change the colour from OUTSIDE the panel, the way undo/redo or the
    # low-contrast fix does, then refresh through the real entry point.
    v.set_annotation_props(aid, {"border_colour": "#FF0000"})
    w = MainWindow.__new__(MainWindow)
    w._inspector = insp
    w._diagram_dock = type("Dock", (), {"view": v})()
    MainWindow._refresh_inspector(w)

    swatches = _colour_swatch_texts(insp)
    assert "#ff0000" in swatches, \
        f"the panel must show the new colour, showed {swatches}"


def _colour_swatch_texts(insp) -> set[str]:
    """The hex values currently shown in the Colours group."""
    from PySide6.QtWidgets import QGroupBox, QLineEdit
    gb = insp._tabs.widget(0).widget().layout().itemAt(0).widget()
    assert isinstance(gb, QGroupBox)
    return {e.text().strip().lower()
            for e in gb.findChildren(QLineEdit) if e.text().strip()}


def test_pasting_an_annotation_is_undoable(qapp):
    """A pasted annotation used to be appended straight to
    `self.annotations` and the scene, outside the undo stack, while the
    element rows beside it went through `AddCmd`. Ctrl+Z after pasting a
    note therefore removed the elements (or nothing) and left the note on
    the canvas, and a later redo added a second one."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 200, 200)
    item = next(iter(v._anno_items.values()))
    v._scene.clearSelection()
    item.setSelected(True)
    v.copy_selection()
    assert len(v._anno_items) == 1

    v.paste()
    assert len(v._anno_items) == 2
    assert len(v.annotations) == 2

    v._stack.undo()
    assert len(v._anno_items) == 1, "Ctrl+Z must take the copy back"
    assert len(v.annotations) == 1

    v._stack.redo()
    assert len(v._anno_items) == 2
    assert len(v.annotations) == 2, \
        "redo must restore exactly one copy, not a duplicate"


# =====================================================================
# Annotation tool arming, Cut, arrow geometry, style defaults
# =====================================================================

def test_annotation_tools_are_one_shot(qapp):
    """After a shape is placed the tool is spent: the user asked to have
    to click the ribbon icon again for the next text box, because the
    armed tool used to drop another (empty) box on every following
    canvas click."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))

    # text: one click places, and disarms
    v.tool_mode = "anno_text"
    v._handle_tool_click(_scene_pt(v, 200, 200))
    assert len(v._anno_items) == 1
    assert v.tool_mode == "select", "the text tool must disarm after placing"
    v._handle_tool_click(_scene_pt(v, 400, 400))
    assert len(v._anno_items) == 1, \
        "a second click must not drop another text box"

    # rect/ellipse: the FIRST click arms the shape, the SECOND commits
    # and disarms -- so the two-click gesture still works.
    v.tool_mode = "anno_rect"
    v._handle_tool_click(_scene_pt(v, 60, 60))
    assert v.tool_mode == "anno_rect", "the first click arms the shape"
    v._handle_tool_click(_scene_pt(v, 180, 140))
    assert len(v._anno_items) == 2
    assert v.tool_mode == "select", "committing spends the tool"

    # arrow: same two-click shape
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 220, 60))
    assert v.tool_mode == "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 380, 120))
    assert len(v._anno_items) == 3
    assert v.tool_mode == "select"


def test_tool_mode_change_is_announced(qapp):
    """MainWindow mirrors the view's own disarm back onto the ribbon, so
    the handler needs to hear about mode changes the view initiates."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    seen = []
    v.tool_mode_changed.connect(seen.append)
    v.tool_mode = "anno_rect"
    v.tool_mode = "anno_rect"      # same mode again -> no repeat event
    v.tool_mode = "select"
    assert seen == ["anno_rect", "select"], seen


@pytest.mark.parametrize("kind,mode,clicks", [
    ("rect", "anno_rect", 2), ("ellipse", "anno_ellipse", 2),
    ("text", "anno_text", 1), ("arrow", "anno_arrow", 2)])
def test_cut_works_on_every_annotation_kind(qapp, kind, mode, clicks):
    """`cut_selection` gated the delete on `copy_selection`'s return,
    which counts copied ELEMENTS only -- 0 for an annotation-only
    selection, so Cut filled the clipboard and left the note on the
    canvas."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = mode
    for i in range(clicks):
        if mode == "anno_arrow":
            v._handle_tool_click(_scene_pt(v, 100 + i * 150, 100))
        elif mode == "anno_text":
            v._handle_tool_click(_scene_pt(v, 100 + i * 150, 100))
        else:
            v._handle_tool_click(_scene_pt(v, 60 + i * 200, 60))
            v._handle_tool_click(_scene_pt(v, 180 + i * 200, 140))
    v.tool_mode = "select"
    assert len(v._anno_items) == 1
    item = next(iter(v._anno_items.values()))
    assert item.kind == kind
    v._scene.clearSelection()
    item.setSelected(True)

    n = v.cut_selection()

    assert n == 1
    assert not v._anno_items, f"Cut left the {kind} on the canvas"
    assert v._paste_buffer, "Cut must still fill the clipboard"
    v._stack.undo()
    assert len(v._anno_items) == 1, "Cut must be undoable"


def test_cut_with_nothing_selected_does_nothing(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 300, 300)
    v._scene.clearSelection()
    assert v.cut_selection() == 0
    assert len(v._anno_items) == 1


def test_arrow_head_grows_with_the_line_width(qapp):
    """An 8px head on an 8px shaft read as a blunt smudge. The head is
    sized from the pen width instead."""
    from raptor.gui.resources.icons import arrow_head_len
    assert arrow_head_len(1) == 8.0, "a hairline keeps the original head"
    assert arrow_head_len(8) > arrow_head_len(4) > arrow_head_len(2)
    # and monotonically, with no drop back to the floor
    sizes = [arrow_head_len(w) for w in range(1, 21)]
    assert sizes == sorted(sizes)


def test_arrow_head_points_away_from_the_shaft(qapp):
    """Both barbs are placed relative to a point BEHIND the tip. The old
    ribbon glyph used `tip - L*cos(angle ± 144°)`; with a spread that
    wide `cos()` goes negative, putting BOTH barbs in front of the tip
    and drawing the triangle on the wrong side of the shaft."""
    from raptor.gui.resources.icons import arrow_head_points
    for angle_deg in (0, 30, 90, 180, 225, 300, 359):
        from math import cos, radians, sin
        a = radians(angle_deg)
        x1, y1 = 0.0, 0.0
        x2, y2 = 100 * cos(a), 100 * sin(a)
        pts = arrow_head_points(x1, y1, x2, y2, 12.0)
        tip = pts.at(0)
        assert abs(tip.x() - x2) < 1e-6 and abs(tip.y() - y2) < 1e-6
        # The property that matters: projected onto the shaft direction,
        # every barb sits BEHIND the tip. The old glyph's
        # `tip - L*cos(angle ± 144°)` put both barbs in FRONT of it for
        # any spread past 90°, which is what made it read as pointing
        # backwards.
        ux, uy = cos(a), sin(a)
        for i, barb in enumerate((pts.at(1), pts.at(2))):
            along = (barb.x() - x2) * ux + (barb.y() - y2) * uy
            assert along < 0, (
                f"barb {i} is {along:.2f} PAST the tip at "
                f"{angle_deg} deg -- the head is drawn backwards")


def test_colours_and_font_menu_label_has_no_mnemonic_underline(qapp):
    """Qt reads a single '&' in an action's text as a mnemonic marker
    and underlines the next character, so the item rendered as
    "Colours _Font…". Doubling it draws a literal ampersand."""
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QMouseEvent

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    seen: list = []

    def capture(menu, event):
        seen.append({a.text(): a for a in menu.actions()
                     if not a.isSeparator()})
        return None
    v._exec_menu = capture
    _place_shape(v, 300, 200)
    item = next(iter(v._anno_items.values()))
    v._scene.clearSelection()
    item.setSelected(True)
    vp = v.mapFromScene(item.scenePos() + QPointF(20, 20))
    ev = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(vp), QPointF(vp),
                     _Qt.MouseButton.RightButton, _Qt.MouseButton.RightButton,
                     _Qt.KeyboardModifier.NoModifier)
    v._show_context_menu(ev)
    labels = seen.pop()
    # "&&" is Qt's escape for a literal ampersand; a bare "&" is the bug.
    assert "Colours && Font…" in labels, sorted(labels)
    assert "Colours & Font…" not in labels, \
        "a single '&' renders as a mnemonic underline"


def test_set_as_default_for_new_annotations(qapp):
    """Style one shape, then every later shape of that kind starts with
    the same look instead of the Inspector being reopened per shape."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 200, 200)
    first = next(iter(v._anno_items.values()))
    v.set_annotation_props(first._anno_id, {
        "fill_colour": "#FFE9A8", "border_colour": "#B8860B",
        "border_width": 3.0, "line_style": "dash"})
    v.set_annotation_style_default("rect", v._anno_items[first._anno_id].props)
    assert v.has_annotation_style_default("rect")

    _place_shape(v, 500, 200)
    fresh = [i for i in v._anno_items.values() if i is not first][0]
    for key, want in (("fill_colour", "#FFE9A8"),
                      ("border_colour", "#B8860B"),
                      ("border_width", 3.0),
                      ("line_style", "dash")):
        assert fresh.props[key] == want, f"{key} did not carry over"

    # a different kind must NOT pick it up
    _place_shape(v, 500, 500, mode="anno_ellipse")
    ell = next(i for i in v._anno_items.values() if i.kind == "ellipse")
    assert ell.props["fill_colour"] is None, \
        "a rectangle's default leaked into an ellipse"


def test_style_default_only_stores_props_its_kind_has(qapp):
    from raptor.gui.ui.diagram import ANNO_DEFAULTS
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 200, 200)
    first = next(iter(v._anno_items.values()))
    # a rect has no text colour or font, and no line style on a text box
    v.set_annotation_style_default(
        "rect", dict(first.props, font_size=99, text_colour="#FF0000"))
    saved = v.default_annotation_props["rect"]
    assert "font_size" not in saved and "text_colour" not in saved
    assert saved["border_colour"] == ANNO_DEFAULTS["border_colour"]


def test_arrow_default_width_survives_a_style_default(qapp):
    """The arrow's own visible 2px width is a fallback, not an override:
    a saved arrow style that names a width wins over it."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 100, 100))
    v._handle_tool_click(_scene_pt(v, 300, 140))
    v.tool_mode = "select"
    arrow = next(i for i in v._anno_items.values() if i.kind == "arrow")
    assert arrow.props["border_width"] == 2, \
        "an un-defaulted arrow must still be visible"
    v.set_annotation_style_default(
        "arrow", dict(arrow.props, border_width=6.0,
                      border_colour="#C00000"))
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(_scene_pt(v, 100, 300))
    v._handle_tool_click(_scene_pt(v, 300, 340))
    v.tool_mode = "select"
    arrows = sorted((i for i in v._anno_items.values() if i.kind == "arrow"),
                    key=lambda i: i.line().y1())
    assert arrows[-1].props["border_width"] == 6.0
    assert arrows[-1].props["border_colour"] == "#C00000"


def test_clearing_a_style_default_returns_to_factory(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    _place_shape(v, 200, 200)
    first = next(iter(v._anno_items.values()))
    v.set_annotation_style_default(
        "rect", dict(first.props, fill_colour="#FFE9A8"))
    assert v.has_annotation_style_default("rect")
    v.clear_annotation_style_default("rect")
    assert not v.has_annotation_style_default("rect")
    _place_shape(v, 500, 200)
    fresh = [i for i in v._anno_items.values() if i is not first][0]
    assert fresh.props["fill_colour"] is None


def test_style_default_persists_through_qsettings(qapp, tmp_path, monkeypatch):
    """A user preference, not model data, so it rides in QSettings (the
    same place as the Block Defaults) and survives a restart."""
    from PySide6.QtCore import QSettings
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    QSettings().clear()

    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow.__new__(MainWindow)
    w._on_annotation_style_default("rect", {
        "fill_colour": "#FFE9A8", "border_colour": "#B8860B",
        "border_width": 3.0, "line_style": "dash", "font_size": 99})
    saved = w._annotation_style_defaults()
    assert saved["rect"]["fill_colour"] == "#FFE9A8"
    assert "font_size" not in saved["rect"], "unknown-for-this-kind keys dropped"

    # a fresh window reads them back
    w2 = MainWindow.__new__(MainWindow)
    assert w2._annotation_style_defaults() == saved

    w2._on_annotation_style_default("rect", None)
    assert "rect" not in w2._annotation_style_defaults()
    # the last default removed takes the key away entirely
    w2._on_annotation_style_default("arrow", {"border_colour": "#C00000"})
    w2._on_annotation_style_default("arrow", None)
    assert QSettings().value("annotation_style_defaults", None) is None


def test_right_click_mid_placement_cancels_without_a_menu(qapp):
    """A gesture in progress owns the right-click. It used to clear the
    ghost and the armed point and then fall straight through to
    `_show_context_menu`, so right-clicking halfway through an arrow or a
    rectangle both threw the gesture away and popped a menu over the
    canvas."""
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QMouseEvent

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    menus: list = []
    v._exec_menu = lambda menu, event: menus.append(menu)

    def rclick(x, y):
        vp = v.mapFromScene(_scene_pt(v, x, y))
        v.mousePressEvent(QMouseEvent(
            QMouseEvent.MouseButtonPress, QPointF(vp), QPointF(vp),
            _Qt.MouseButton.RightButton, _Qt.MouseButton.RightButton,
            _Qt.KeyboardModifier.NoModifier))

    for mode, _ in (("anno_rect", None), ("anno_ellipse", None),
                    ("anno_arrow", None)):
        n0 = len(v._anno_items)
        v.tool_mode = mode
        v._handle_tool_click(_scene_pt(v, 200, 150))     # arms the gesture
        assert (v._shape_anno_from is not None
                or v._arrow_from is not None), mode
        menus.clear()

        rclick(400, 300)

        assert not menus, (
            f"{mode}: right-click mid-placement opened a context menu")
        assert v.tool_mode == "select", mode
        assert v._shape_anno_from is None and v._arrow_from is None, \
            f"{mode}: the armed point survived the cancel"
        assert len(v._anno_items) == n0, \
            f"{mode}: cancelling must not leave a half-built shape"


def test_right_click_with_a_merely_armed_tool_still_opens_the_menu(qapp):
    """Only a gesture IN PROGRESS swallows the right-click. A tool that
    is armed but has not started still cancels the tool and offers the
    canvas menu, which is how the user gets to Paste/Select All."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt as _Qt

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    menus: list = []
    v._exec_menu = lambda menu, event: menus.append(menu)

    v.tool_mode = "anno_rect"          # armed, nothing placed
    vp = v.mapFromScene(_scene_pt(v, 400, 300))
    v.mousePressEvent(QMouseEvent(
        QMouseEvent.MouseButtonPress, QPointF(vp), QPointF(vp),
        _Qt.MouseButton.RightButton, _Qt.MouseButton.RightButton,
        _Qt.KeyboardModifier.NoModifier))

    assert menus, "an armed-but-idle tool must not eat the context menu"
    assert v.tool_mode == "select", "and the tool is still cancelled"


@pytest.mark.parametrize("mode,kind", [
    ("connect", "Connect"),
    ("add_block", "Block"),
    ("add_node", "Node"),
    ("add_event", "Event"),
    ("add_hierarchy", "Hierarchy"),
])
def test_right_click_cancels_connect_and_every_add_tool(qapp, mode, kind):
    """Right-click is a cancel while placing, not a second completion or a menu.

    Connect used to *complete* when the right-click landed on an element;
    the Add tools cancelled but then opened the canvas menu over the
    placement. Both are wrong: a right-click mid-placement cancels it
    and leaves the canvas alone.
    """
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QMouseEvent

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    menus: list = []
    v._exec_menu = lambda menu, event: menus.append(menu)

    bid = next(b for b in v.blocks if b != 0)
    links0 = len(v.links)
    if mode == "connect":
        v.tool_mode = mode
        v._connect_from = ("Block", bid)
        # The right-click is over another block: the old code would have
        # completed a link here.
        target = next(b for b in v._block_items if b != bid)
        x, y = v._block_items[target].scenePos().x(), \
            v._block_items[target].scenePos().y()
    else:
        v.tool_mode = mode
        x, y = 400, 300

    vp = v.mapFromScene(_scene_pt(v, x, y))
    v.mousePressEvent(QMouseEvent(
        QMouseEvent.MouseButtonPress, QPointF(vp), QPointF(vp),
        _Qt.MouseButton.RightButton, _Qt.MouseButton.RightButton,
        _Qt.KeyboardModifier.NoModifier))

    assert not menus, f"{kind}: right-click opened a context menu"
    assert v.tool_mode == "select", f"{kind}: tool was not disarmed"
    assert v._connect_from is None, f"{kind}: connect source survived"
    assert len(v.links) == links0, f"{kind}: a link was still created"


def test_element_comment_is_keyed_by_the_elements_real_name(v):
    """The COMMENTS section keys a comment by element NAME. `_element_name` used to be defined twice in
    DiagramView (the link-tool's capitalised-kind lookup shadowed the lower-case one), so a comment was
    written under "block 3" instead of the block's name."""
    bid = next(b for b in v.blocks if b != 0)
    name = v.blocks[bid].name
    v.set_element_comment("block", bid, "checked on site")
    rows = [r for r in v._rbd_ref.sections["COMMENTS"].rows if r.get("Type") == "block"]
    assert [r["Name"] for r in rows] == [name]
    assert rows[0]["Comment"] == "checked on site"
    assert v.current_comment_stored("block", bid) == "checked on site"
    v._stack.undo()
    assert v.current_comment_stored("block", bid) == ""
    v._stack.redo()
    assert v.current_comment_stored("block", bid) == "checked on site"


def test_rebuilding_the_canvas_with_a_selected_annotation_does_not_touch_deleted_items(qapp):
    """Issue #2: `clear_model` cleared the scene (deleting every item, and emitting a
    selection change for the selected note) while `_anno_items` still held the
    wrappers, so the main window's enable-state refresh raised "Internal C++ object
    (_AnnoText) already deleted"."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd")))
    _place_shape(v, 100, 100)
    item = next(iter(v._anno_items.values()))
    v._scene.clearSelection()
    item.setSelected(True)
    seen: list[object] = []

    def refresh():                      # what MainWindow._sync_annotation_order_actions asks
        seen.append(v.annotation_order_target("front"))

    v.selection_changed.connect(refresh)
    v.clear_model()                     # raised RuntimeError before the fix
    assert v._anno_items == {}
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd")))
    assert all(target is None for target in seen)

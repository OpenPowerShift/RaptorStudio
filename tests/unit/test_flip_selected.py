"""Flip Horizontal / Flip Vertical mirror a multi-selection about the centre
of its bounding box, so a chain drawn left-to-right can be turned around
without redrawing it."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui import diagram

pytestmark = pytest.mark.gui
# blocks spread over several columns AND rows, so a flip visibly moves them
MODEL = "tests/golden/Capacity2/input.rbd"


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp):
    v = diagram.DiagramView()
    v.load_rbd(load_rbd(MODEL))
    return v


def _select_all_blocks(v):
    v._scene.clearSelection()
    items = [v._block_items[b] for b in sorted(v._block_items) if b]
    for it in items:
        it.setSelected(True)
    box = _bbox(items)
    assert box.width() > 200 and box.height() > 100, "fixture is degenerate"
    return items


def _centres(items):
    return [it.sceneBoundingRect().center() for it in items]


def _bbox(items):
    r = items[0].sceneBoundingRect()
    for it in items[1:]:
        r = r.united(it.sceneBoundingRect())
    return r


@pytest.mark.parametrize("axis", ["h", "v"])
def test_positions_reflect_about_the_middle_of_the_centres(view, axis):
    items = _select_all_blocks(view)
    before = _centres(items)
    coord = (lambda p: p.x()) if axis == "h" else (lambda p: p.y())
    lo, hi = min(map(coord, before)), max(map(coord, before))
    assert view.flip_selected(axis) is True
    for b, a in zip(before, _centres(items)):
        assert coord(a) == pytest.approx(lo + hi - coord(b))
        other = (lambda p: p.y()) if axis == "h" else (lambda p: p.x())
        assert other(a) == pytest.approx(other(b))     # only one axis moves
    after = list(map(coord, _centres(items)))
    assert (min(after), max(after)) == pytest.approx((lo, hi))   # span stays


def test_the_layout_really_reverses(view):
    """The leftmost item becomes the rightmost, and vice versa."""
    items = _select_all_blocks(view)
    order = sorted(range(len(items)), key=lambda i: _centres(items)[i].x())
    view.flip_selected("h")
    after = sorted(range(len(items)), key=lambda i: _centres(items)[i].x())
    # ties (same column) may reorder among themselves; the extremes swap
    assert _centres(items)[order[0]].x() >= _centres(items)[order[-1]].x() - 1e-6 \
        or _centres(items)[order[0]].x() == pytest.approx(
            _centres(items)[order[-1]].x())
    assert after != order


@pytest.mark.parametrize("axis", ["h", "v"])
def test_flipping_twice_restores_the_layout(view, axis):
    items = _select_all_blocks(view)
    before = [v for c in _centres(items) for v in (c.x(), c.y())]
    view.flip_selected(axis)
    view.flip_selected(axis)
    after = [v for c in _centres(items) for v in (c.x(), c.y())]
    assert after == pytest.approx(before)     # flat: approx takes no nesting


def test_the_flip_reaches_the_model_and_undoes_in_one_step(view):
    items = _select_all_blocks(view)
    mid = _bbox(items).center().x()
    # a block that is not on the mirror line, so its position must change
    bid = max((b for b in view._block_items if b),
              key=lambda b: abs(view._block_items[b]
                                .sceneBoundingRect().center().x() - mid))
    x0 = view.blocks[bid].x_position
    before = [v for c in _centres(items) for v in (c.x(), c.y())]
    view.flip_selected("h")
    assert view.blocks[bid].x_position != pytest.approx(x0)   # typed object
    view._stack.undo()
    assert [v for c in _centres(items) for v in (c.x(), c.y())] == pytest.approx(before)
    assert view.blocks[bid].x_position == pytest.approx(x0)


def test_fewer_than_two_items_do_nothing(view):
    view._scene.clearSelection()
    assert view.flip_selected("h") is False
    b = next(b for b in sorted(view._block_items) if b)
    view._block_items[b].setSelected(True)
    before = view._block_items[b].pos()
    assert view.flip_selected("v") is False
    assert view._block_items[b].pos() == before
    assert not view._stack.canUndo() or view._stack.undoText() != "Flip Vertical"


def test_the_undo_step_is_named(view):
    _select_all_blocks(view)
    view.flip_selected("v")
    assert view._stack.undoText() == "Flip Vertical"


def test_the_ribbon_buttons_follow_the_selection(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    qapp.processEvents()
    assert not w._flip_h_action.isEnabled()
    v = w._diagram_dock.view
    _select_all_blocks(v)
    qapp.processEvents()
    assert w._flip_h_action.isEnabled() and w._flip_v_action.isEnabled()
    w._flip_h_action.trigger()
    assert v._stack.undoText() == "Flip Horizontal"
    w._dirty = False
    w.close()


def test_the_drag_snap_grid_is_restored_afterwards(view):
    _select_all_blocks(view)
    before = dict(view._snap_state)
    view.flip_selected("h")
    assert dict(view._snap_state) == before

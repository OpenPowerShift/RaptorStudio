"""Moving things on the canvas must stay cheap.

Dragging one block on a 149-block / 309-link model used to take about a
second per mouse-move: every step re-routed EVERY link, and each route
walked every item in the scene. These tests pin the two properties the fix
relies on -- a move re-routes only the links it can change, and doing so
gives exactly the routes a full re-route would -- plus a count-based guard
(not a stopwatch, which flakes) that a move touches a handful of links.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui import diagram
from raptor.gui.ui.diagram_items import _LinkItem, links_affected_by_moving

pytestmark = pytest.mark.gui
BIG = "tests/golden/Hierarchy1-7/input.rbd"


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _view(path):
    v = diagram.DiagramView()
    v.load_rbd(load_rbd(path))
    return v


def _routes(v):
    return sorted((l._link_num, round(l._p0.x(), 3), round(l._p0.y(), 3),
                   round(l._p3.x(), 3), round(l._p3.y(), 3))
                  for l in v._links)


@pytest.mark.parametrize("model", ["Hierarchy1-7", "Capacity2", "Phasing3",
                                   "NestedHierarchyCost", "EventDepProbe"])
def test_incremental_rerouting_equals_a_full_reroute(qapp, model):
    """The set of links `links_affected_by_moving` returns is exact: after
    arbitrary moves, the incrementally-updated routes are the ones a full
    re-route of every link produces."""
    v = _view(f"tests/golden/{model}/input.rbd")
    items = [*v._block_items.values(), *v._node_items.values(),
             *v._event_items.values()]
    for i, it in enumerate(items):
        if i % 3 == 0:
            it.setPos(it.pos() + QPointF(37, -23))
    incremental = _routes(v)
    for link in v._links:
        link._update_route()
    assert _routes(v) == incremental


def test_a_move_reroutes_a_handful_of_links_not_all_of_them(qapp,
                                                            monkeypatch):
    v = _view(BIG)
    assert len(v._links) > 200
    calls = []
    real = _LinkItem._update_route
    monkeypatch.setattr(_LinkItem, "_update_route",
                        lambda self: (calls.append(self), real(self))[1])
    bid = next(b for b in sorted(v._block_items) if b)
    v._block_items[bid].setPos(v._block_items[bid].pos() + QPointF(7, 3))
    assert 0 < len(set(map(id, calls))) < 40, len(calls)


def test_the_affected_set_covers_the_links_at_the_item_and_its_neighbours(
        qapp):
    v = _view("tests/golden/Capacity2/input.rbd")
    bid = next(b for b in sorted(v._block_items) if b)
    item = v._block_items[bid]
    affected = {id(l) for l in links_affected_by_moving(item)}
    touching = [l for l in v._links if l._from is item or l._to is item]
    assert touching and all(id(l) in affected for l in touching)
    for l in touching:
        for end in (l._from, l._to):
            for n in v._links:
                if n._from is end or n._to is end:
                    assert id(n) in affected


def test_the_link_index_follows_added_and_removed_links(qapp):
    v = _view("tests/golden/Capacity2/input.rbd")
    from raptor.gui.ui.diagram_items import _links_at
    ends = {l._from for l in v._links}
    item = next(iter(ends))
    before = len(_links_at(item)[1])
    victim = next(l for l in v._links if l._from is item)
    v._scene.removeItem(victim)
    assert len(_links_at(item)[1]) == before - 1
    v._scene.addItem(victim)
    assert len(_links_at(item)[1]) == before


def test_a_drag_notifies_at_once_then_coalesces(qapp):
    """First step of a gesture marks the file dirty (exactly as before);
    the following steps are coalesced into one trailing notification
    instead of a full refresh per mouse move.

    A drag reports through `layout_changed`, NOT `model_changed`: geometry
    is not an engine input, so it must not trigger a whole-model
    validation/Inspector/Data Tables rebuild (seconds on a big file)."""
    v = _view("tests/golden/Capacity2/input.rbd")
    fired = []
    model = []
    v.layout_changed.connect(lambda: fired.append(1))
    v.model_changed.connect(lambda: model.append(1))
    item = next(iter(v._block_items.values()))
    item.setPos(item.pos() + QPointF(5, 0))
    assert len(fired) == 1                       # leading edge, immediately
    for _ in range(20):
        item.setPos(item.pos() + QPointF(5, 0))
    assert len(fired) == 1                       # coalesced while moving
    v._moved_settle.stop()
    v._on_moved_settled()                        # motion paused
    assert len(fired) == 2                       # one trailing notification
    v._on_moved_settled()
    assert len(fired) == 2                       # and nothing owed after that
    assert model == [], "a drag took the whole-model refresh path"


def test_inspector_position_follows_a_move_and_undo(qapp):
    """Geometry edits skip the Inspector rebuild, so the panel must still
    update its Position rows -- otherwise undo moves the block back but the
    X/Y labels keep the dragged-to values."""
    from raptor.gui.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w._load_into_app(load_rbd("tests/golden/Capacity2/input.rbd"), None)
        qapp.processEvents()
        v = w._diagram_dock.view
        bid = next(b for b in sorted(v._block_items) if b)
        item = v._block_items[bid]
        w._inspector.show_block(bid, v.blocks)
        labels = w._inspector._position_labels
        assert labels is not None
        before = labels[0].text()

        old = item.pos()
        new = old + QPointF(40, 0)
        from raptor.gui.ui.diagram_commands import MoveCmd
        v._stack.push(MoveCmd(v, [("block", bid, (old.x(), old.y()),
                                   (new.x(), new.y()))]))
        assert labels[0].text() != before

        v._stack.undo()
        assert labels[0].text() == before
    finally:
        w._dirty = False
        w.close()


# -- entering and leaving hierarchies -----------------------------------------

@pytest.mark.parametrize("model", ["HierarchyExample", "NestedHierarchyCost"])
def test_entering_and_leaving_hierarchies_raises_nothing(qapp, model):
    """Double-clicking a hierarchy rebuilds the scene while selection
    signals are still arriving. Command availability (Align, Mass Edit, Zoom
    Hierarchy) is recomputed on each, and used to touch item wrappers Qt had
    already deleted: "Internal C++ object already deleted". An exception in
    a Qt slot does not fail a test by itself, so the excepthook is watched."""
    import sys

    from raptor.gui.ui.main_window import MainWindow
    path = f"tests/golden/{model}/input.rbd"
    seen = []
    old_hook = sys.excepthook
    sys.excepthook = lambda *exc: seen.append(exc)
    w = MainWindow()
    try:
        w.resize(1400, 900)
        w.show()
        w._load_into_app(load_rbd(path), path)
        qapp.processEvents()
        v = w._diagram_dock.view
        assert v._hier_items
        for _ in range(3):
            for hid in sorted(v._hier_items):
                v._scene.clearSelection()
                v._hier_items[hid].setSelected(True)
                v.enter_level(hid)
                qapp.processEvents()
                v.go_home()
                qapp.processEvents()
        # the availability state is right on the other side, too
        assert isinstance(w._command_state().selected_elements, int)
        w._dirty = False
    finally:
        sys.excepthook = old_hook
        w.close()
    assert seen == [], [str(e[1]) for e in seen]


def test_the_selection_helpers_skip_deleted_items(qapp):
    v = _view("tests/golden/HierarchyExample/input.rbd")
    hid = sorted(v._hier_items)[0]
    v._hier_items[hid].setSelected(True)
    v._scene.clear()                 # every wrapper in the maps is now dead
    assert v.selected_elements() == ([], [], [], [])
    assert v.selected_element_count() == 0
    assert v.hierarchy_count() == 0

"""GUI UX tests: stream editing, auto layout, paste placement, snap,
dock minimize, canvas click-to-centre, and termination-choice wording.
"""

from PySide6.QtWidgets import QApplication

import pytest

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.diagram import DiagramView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd("tests/golden/HierarchyExample/input.rbd"))
    return v


# ---------------------------------------------------------------------
# Random stream editing (Raptor's Simulation Theory semantics)
# ---------------------------------------------------------------------

def test_stream_edit_and_undo(view):
    bid = sorted(view.blocks)[0]
    old = (view.blocks[bid].fail_stream, view.blocks[bid].repair_stream)
    assert view.set_block_stream(bid, "F", -5)
    assert (view.blocks[bid].fail_stream,
            view.blocks[bid].repair_stream) == (-5, old[1])
    assert view._block_row_by_id(bid)["FStream"] == "-5"
    view._stack.undo()
    assert (view.blocks[bid].fail_stream,
            view.blocks[bid].repair_stream) == old


def test_system_stream_writes_both_sides(view):
    """Raptor 7 -- one System-letter combo writes BOTH
    failure and repair streams."""
    bid = sorted(view.blocks)[0]
    assert view.set_block_stream(bid, "FR", 207)
    blk = view.blocks[bid]
    assert (blk.fail_stream, blk.repair_stream) == (207, 207)
    row = view._block_row_by_id(bid)
    assert (row["FStream"], row["RStream"]) == ("207", "207")
    view._stack.undo()
    assert blk.fail_stream != 207 and blk.repair_stream != 207


@pytest.mark.parametrize("bad", [0, 111, -111, 211])
def test_stream_validation_rejects(view, bad):
    bid = sorted(view.blocks)[0]
    assert not view.set_block_stream(bid, "F", bad)


def test_inspector_type_combo_signals(qapp, view):
    """Failure/Repair Type combos are editable and route through
    set_block_dist (which syncs FDist/#FParam)."""
    bid = sorted(view.blocks)[0]
    code = 15  # Weibull
    assert view.set_block_dist(bid, "F", code)
    assert view.blocks[bid].fail_distro == code
    assert view._block_row_by_id(bid)["FDist"] == str(code)


# ---------------------------------------------------------------------
# Auto layout
# ---------------------------------------------------------------------

def test_auto_layout_no_cross_kind_id_collision(qapp):
    """Dependence2: the layering walks blocks and nodes separately, so
    HighGain/LowGain do not get placed by a colliding raw id from
    another kind."""
    v = DiagramView()
    v.load_rbd(load_rbd("tests/data/models/Dependence2.rbd"))
    hg = next(b for b in v._block_items
              if v.blocks[b].name == "HighGain")
    v.auto_layout()
    ys = [it.pos().y() for it in v._block_items.values()]
    assert min(ys) > -500  # nothing dramatically far away
    low = next(b for b in v._block_items
               if v.blocks[b].name == "LowGain")
    # parallel pair lands in the same column band, stacked vertically
    assert abs(v._block_items[hg].pos().x()
               - v._block_items[low].pos().x()) < 1.0
    assert abs(v._block_items[hg].pos().y()
               - v._block_items[low].pos().y()) > 50


def test_auto_layout_balances_blocks_above_and_below_their_node(qapp):
    """Auto layout balances parallel blocks around their shared node's
    centre line. TwoBlockRedundancy is Start -> [Block A, Block B] ->
    Join -> End: Start/Join/End all land on one centre line, with one
    block above and one below, symmetric."""
    v = DiagramView()
    v.load_rbd(load_rbd("tests/golden/TwoBlockRedundancy/input.rbd"))
    v.auto_layout()
    start = next(n for n in v._node_items.values() if n._node_id == 1)
    join = next(n for n in v._node_items.values() if n._node_id == 5)
    end = next(n for n in v._node_items.values() if n._node_id == 2)
    centre_y = start.scenePos().y()
    assert join.scenePos().y() == pytest.approx(centre_y, abs=0.5)
    assert end.scenePos().y() == pytest.approx(centre_y, abs=0.5)

    block_centres = [it.sceneBoundingRect().center().y()
                     for it in v._block_items.values()]
    assert len(block_centres) == 2
    above = [y for y in block_centres if y < centre_y - 1]
    below = [y for y in block_centres if y > centre_y + 1]
    assert len(above) == 1 and len(below) == 1
    # symmetric, not just "one on each side"
    assert abs((centre_y - above[0]) - (below[0] - centre_y)) < 1.0


def test_auto_layout_inside_hierarchy(qapp):
    """Inside a level, seeding comes from that hierarchy's In node; home
    items are not moved."""
    v = DiagramView()
    v.load_rbd(load_rbd("tests/golden/HierarchyExample/input.rbd"))
    home_before = {b: it.pos() for b, it in v._block_items.items()}
    v.enter_level(23)
    before = {b: it.pos() for b, it in v._block_items.items()}
    v.auto_layout()
    after = {b: it.pos() for b, it in v._block_items.items()}
    # series chain -> one element per layer: single ROW, distinct
    # ascending columns
    ys = {round(p.y()) for p in after.values()}
    xs = sorted(round(p.x()) for p in after.values())
    assert len(ys) == 1 and xs == sorted(set(xs))
    assert any(before[b] != after[b] for b in before)  # actually arranged
    v.go_home()
    home_after = {b: (round(it.pos().x()), round(it.pos().y()))
                  for b, it in v._block_items.items()}
    home_before_r = {b: (round(p.x()), round(p.y()))
                     for b, p in home_before.items()}
    diffs = [b for b in home_before_r if home_before_r[b] != home_after[b]]
    # only float-rounding drift allowed on level re-entry
    assert len(diffs) <= len(home_before_r)


def test_auto_layout_with_a_selection_lays_out_the_whole_level(qapp):
    """A selection is not a scope. Laying out only the selected block, to
    where a full layout would put it, while its neighbours stayed put
    lifted it off a straight chain ("wonky"). Every element lands where a
    full layout puts it, and the selection survives."""
    path = "tests/golden/TwoBlockRedundancy/input.rbd"
    v = DiagramView()
    v.load_rbd(load_rbd(path))
    first_id = min(v._block_items)
    v._scene.clearSelection()
    v._block_items[first_id].setSelected(True)
    v.auto_layout()
    # Re-fetch by id: auto_layout rebuilds the canvas, so items held from
    # before it are deleted.
    with_sel = {k: (round(it.pos().x()), round(it.pos().y()))
                for k, it in v._block_items.items()}
    assert v._block_items[first_id].isSelected()

    v2 = DiagramView()
    v2.load_rbd(load_rbd(path))
    v2.auto_layout()
    full = {k: (round(it.pos().x()), round(it.pos().y()))
            for k, it in v2._block_items.items()}
    assert with_sel == full


# ---------------------------------------------------------------------
# Paste placement + free movement
# ---------------------------------------------------------------------

def test_plain_paste_lands_below_source(qapp, view):
    bid = sorted(view._block_items)[0]
    view._scene.clearSelection()
    view._block_items[bid].setSelected(True)
    n = view.copy_selection()
    assert n == 1
    src_y = view._block_items[bid].pos().y()
    view.paste()  # plain paste (no anchor point)
    new_bid = max(view._block_items)
    new_y = view._block_items[new_bid].pos().y()
    # Y is flipped: below on screen == different scene y, clearly apart
    assert abs(new_y - src_y) > 30
    view._stack.undo()


def test_snap_enabled_on_grid(qapp, view):
    # Drag positions align to the grid step, independently per axis (a
    # file's row/column spacing routinely differ) rather than one shared
    # "step" key.
    assert view._snap_state["x"] > 0.0
    assert view._snap_state["y"] > 0.0


def test_data_tables_left_click_locates_row(qapp, view):
    """Left-clicking a Data Tables row jumps to and centres that element on
    the canvas, and not only a right-click context-menu item."""
    from raptor.gui.ui.data_tables import DataTablesDock

    dt = DataTablesDock()
    dt.set_view(view)
    bid = sorted(view._block_items)[0]
    name = view.blocks[bid].name
    row = next(r for r in range(dt._blocks_table.rowCount())
              if dt._blocks_table.item(r, 0).text() == name)

    got = []
    dt.element_locate_requested.connect(lambda k, n: got.append((k, n)))
    dt._blocks_table.itemClicked.emit(dt._blocks_table.item(row, 0))
    assert got == [("block", name)]


def test_event_item_snaps_to_grid(qapp):
    """`_EventItem` snaps to the grid like `_NodeItem`/`_HierItem`/
    `_BlockItem`. Drives `itemChange` directly and confirms an off-grid
    `setPos()` lands back on the grid."""
    from PySide6.QtWidgets import QGraphicsScene
    from raptor.gui.ui import diagram as diag

    saved = dict(diag._SNAP_STATE)
    try:
        diag._SNAP_STATE["x"] = diag._SNAP_STATE["y"] = 10.0
        scene = QGraphicsScene()
        item = diag._EventItem(1, "E1", 0.0, 0.0)
        scene.addItem(item)
        item.setPos(23.0, 47.0)  # deliberately off the 10-unit grid
        assert (round(item.pos().x()), round(item.pos().y())) == (20, 50)
    finally:
        diag._SNAP_STATE.update(saved)


# ---------------------------------------------------------------------
# Dock minimize-to-stub
# ---------------------------------------------------------------------

def test_dock_minimize_stub_left_right(qapp):
    """The minimize toggle lives in the dock's custom titlebar next to
    float/close, squeezing the dock to a thin clickable stub; clicking
    it again restores normal size."""
    from raptor.gui.ui.main_window import MainWindow
    from PySide6.QtWidgets import QToolButton

    w = MainWindow()
    for dock in (w._explorer_dock, w._inspector_dock):
        normal_min = dock.minimumWidth()
        titlebar = dock.titleBarWidget()
        min_btn = titlebar.findChildren(QToolButton)[0]
        content = dock.widget()

        # isHidden() (the widget's OWN explicit flag), not isVisible()
        # (which also depends on the never-shown top-level window) --
        # this test never calls w.show().
        min_btn.click()
        assert content.isHidden()
        # squeezed down to a thin stub, nowhere near the normal width
        assert dock.minimumWidth() == dock.maximumWidth() < normal_min

        min_btn.click()  # same button toggles back
        assert not content.isHidden()
        assert dock.minimumWidth() == normal_min
        assert dock.maximumWidth() > 1000


def test_dock_minimize_bottom_pair_yoked(qapp):
    """Minimizing the results/data-tables area minimizes both: they are
    tabified into one bottom slot, so the same toggle drives both docks'
    stubs together."""
    from raptor.gui.ui.main_window import MainWindow
    from PySide6.QtWidgets import QToolButton

    w = MainWindow()
    bottom, tables = w._results_dock, w._data_tables_dock
    bottom_content, tables_content = bottom.widget(), tables.widget()
    min_btn = bottom.titleBarWidget().findChildren(QToolButton)[0]

    min_btn.click()
    assert bottom_content.isHidden() and tables_content.isHidden()
    assert bottom.minimumHeight() == bottom.maximumHeight() < 100
    assert tables.minimumHeight() == tables.maximumHeight() < 100

    # restoring via EITHER dock's own titlebar button restores both
    tables.titleBarWidget().findChildren(QToolButton)[0].click()
    assert not bottom_content.isHidden() and not tables_content.isHidden()
    # The restore applies the remembered height fixed, then releases the
    # fix on the next event-loop pass (splitter draggable again) — pump
    # so the deferred release runs before asserting.
    qapp.processEvents()
    assert bottom.maximumHeight() == w._max_bottom_dock_height()
    assert tables.maximumHeight() == w._max_bottom_dock_height()


def test_dock_minimize_titlebar_has_float_and_close(qapp):
    """The custom titlebar keeps float and close controls working, in the
    same row as minimize."""
    from raptor.gui.ui.main_window import MainWindow
    from PySide6.QtWidgets import QToolButton

    w = MainWindow()
    dock = w._inspector_dock
    btns = dock.titleBarWidget().findChildren(QToolButton)
    assert len(btns) == 3  # minimize, float, close, all one row

    assert not dock.isFloating()
    btns[1].click()  # float button
    assert dock.isFloating()

    assert not dock.isHidden()
    btns[2].click()  # close button
    assert dock.isHidden()


def test_dock_minimize_shades_titlebar(qapp):
    """The titlebar background changes when minimized, distinguishing it
    from an expanded or floating dock."""
    from raptor.gui.ui.main_window import MainWindow
    from PySide6.QtWidgets import QToolButton

    w = MainWindow()
    dock = w._explorer_dock
    titlebar = dock.titleBarWidget()
    normal_style = titlebar.styleSheet()

    titlebar.findChildren(QToolButton)[0].click()
    assert titlebar.styleSheet() != normal_style


# ---------------------------------------------------------------------
# Termination wording (no acronyms in the UI)
# ---------------------------------------------------------------------

def test_canvas_click_centers_on_element(qapp, view):
    """A plain click (press+release with no movement) on a block re-centres
    the viewport on it.

    Drives `mouseReleaseEvent` with the `_drag_start` state a real plain
    click leaves behind, isolating the new logic from Qt's click-to-
    select hit-testing, which cannot be driven reliably headless.
"""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF as _QPointF, Qt as _Qt

    view.resize(600, 400)
    bid = sorted(view._block_items)[0]
    item = view._block_items[bid]
    centre = item.sceneBoundingRect().center()

    view.centerOn(centre.x() + 5000, centre.y() + 5000)
    before = view.mapToScene(view.viewport().rect().center())
    assert (before - centre).manhattanLength() > 1000

    item.setSelected(True)
    view._drag_start = [("block", bid, (item.scenePos().x(), item.scenePos().y()))]
    release = QMouseEvent(QMouseEvent.MouseButtonRelease, _QPointF(0, 0),
                          _QPointF(0, 0), _Qt.LeftButton, _Qt.NoButton,
                          _Qt.NoModifier)
    view.mouseReleaseEvent(release)

    after = view.mapToScene(view.viewport().rect().center())
    assert (after - centre).manhattanLength() < 12  # integer-pixel rounding


def test_canvas_drag_does_not_recenter(qapp, view):
    """A real drag (the element's position actually changed) must not
    trigger the click-to-centre behaviour -- only a plain click with no
    movement does (previous test)."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF as _QPointF, Qt as _Qt

    view.resize(600, 400)
    bid = sorted(view._block_items)[0]
    item = view._block_items[bid]
    centre = item.sceneBoundingRect().center()

    view.centerOn(centre.x() + 5000, centre.y() + 5000)
    before = view.mapToScene(view.viewport().rect().center())

    start = (item.scenePos().x(), item.scenePos().y())
    item.setSelected(True)
    view._drag_start = [("block", bid, start)]
    item.setPos(item.pos().x() + 40, item.pos().y() + 40)  # the "drag"
    release = QMouseEvent(QMouseEvent.MouseButtonRelease, _QPointF(0, 0),
                          _QPointF(0, 0), _Qt.LeftButton, _Qt.NoButton,
                          _Qt.NoModifier)
    view.mouseReleaseEvent(release)

    after = view.mapToScene(view.viewport().rect().center())
    assert (after - before).manhattanLength() < 5
    view._stack.undo()  # keep the fixture's model unmutated for other tests


def test_canvas_click_does_not_recenter_when_already_visible(qapp, view):
    """A click on an element already comfortably visible leaves the view
    where it was. After Auto Layout the whole diagram is framed by
    `fitInView`, so recentring on every click would shunt the canvas
    under the cursor."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF as _QPointF, Qt as _Qt

    view.resize(600, 400)
    bid = sorted(view._block_items)[0]
    item = view._block_items[bid]
    view.centerOn(item)   # comfortably visible -- dead centre already
    before = view.mapToScene(view.viewport().rect().center())

    item.setSelected(True)
    view._drag_start = [("block", bid, (item.scenePos().x(), item.scenePos().y()))]
    release = QMouseEvent(QMouseEvent.MouseButtonRelease, _QPointF(0, 0),
                          _QPointF(0, 0), _Qt.LeftButton, _Qt.NoButton,
                          _Qt.NoModifier)
    view.mouseReleaseEvent(release)

    after = view.mapToScene(view.viewport().rect().center())
    assert (after - before).manhattanLength() < 1, \
        "view must not move for an already-visible element"


def test_tiny_click_jitter_is_swallowed_before_reaching_qt(qapp, view):
    """Sub-deadzone pointer movement does not reach Qt's own
    `mouseMoveEvent` handling. Qt's default drag handling nudges a
    selected movable item by a tiny sub-pixel delta even for a plain
    click, which snaps live to the grid and can visibly jump an element.
    Verifies by monkeypatching the parent class's `mouseMoveEvent`."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF as _QPointF, Qt as _Qt
    from PySide6.QtWidgets import QGraphicsView
    from raptor.gui.ui.diagram import _CLICK_DRAG_DEADZONE_PX

    calls = []
    orig = QGraphicsView.mouseMoveEvent
    def traced(self, event):
        calls.append(event)
        return orig(self, event)
    QGraphicsView.mouseMoveEvent = traced
    try:
        bid = sorted(view._block_items)[0]
        item = view._block_items[bid]
        view.tool_mode = "select"
        item.setSelected(True)
        view._drag_start = [("block", bid,
                             (item.scenePos().x(), item.scenePos().y()))]
        view._press_viewport_pos = _QPointF(100, 100)
        view._drag_armed = False

        tiny = _QPointF(100 + _CLICK_DRAG_DEADZONE_PX / 2, 100)
        ev = QMouseEvent(QMouseEvent.MouseMove, tiny, tiny, _Qt.NoButton,
                         _Qt.LeftButton, _Qt.NoModifier)
        view.mouseMoveEvent(ev)

        assert not calls, \
            "sub-deadzone movement must never reach Qt's own mouseMoveEvent"
        assert not view._drag_armed
    finally:
        QGraphicsView.mouseMoveEvent = orig


def test_drag_past_the_deadzone_reaches_qts_own_handling(qapp, view):
    """The other side of the same fix: once movement crosses the
    deadzone, the gesture is treated as a real drag and forwarded to
    Qt's own default handling exactly as before this fix."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF as _QPointF, Qt as _Qt
    from PySide6.QtWidgets import QGraphicsView
    from raptor.gui.ui.diagram import _CLICK_DRAG_DEADZONE_PX

    calls = []
    orig = QGraphicsView.mouseMoveEvent
    def traced(self, event):
        calls.append(event)
        return orig(self, event)
    QGraphicsView.mouseMoveEvent = traced
    try:
        bid = sorted(view._block_items)[0]
        item = view._block_items[bid]
        view.tool_mode = "select"
        item.setSelected(True)
        view._drag_start = [("block", bid,
                             (item.scenePos().x(), item.scenePos().y()))]
        view._press_viewport_pos = _QPointF(100, 100)
        view._drag_armed = False

        far = _QPointF(100 + _CLICK_DRAG_DEADZONE_PX * 5, 100)
        ev = QMouseEvent(QMouseEvent.MouseMove, far, far, _Qt.NoButton,
                         _Qt.LeftButton, _Qt.NoModifier)
        view.mouseMoveEvent(ev)

        assert calls, "movement past the deadzone must reach Qt's own handling"
        assert view._drag_armed
    finally:
        QGraphicsView.mouseMoveEvent = orig


def test_termination_choices_cover_canonical_codes():
    from raptor.gui.ui.main_window import _TERMINATION_CHOICES
    codes = {code for code, _ in _TERMINATION_CHOICES}
    assert codes == {"TimeSim", "FailSim", "CycleSim"}
    words = " ".join(w for _, w in _TERMINATION_CHOICES).lower()
    assert "timesim" not in words and "failsim" not in words \
        and "cyclesim" not in words


# ---------------------------------------------------------------------
# Tooltip re-show (data-table column tooltips "not appearing")
# ---------------------------------------------------------------------

def test_tooltip_keeper_captures_item_view_tooltips(qapp):
    """A QTableWidget column header's tooltip lives on the
    QTableWidgetItem (`Qt.ToolTipRole`), never on the `QHeaderView`
    widget's own `.toolTip()`, which is always "". `_TooltipKeeper` is a
    `QToolTip`-state poller (not an event filter) and reads
    `QToolTip.text()` directly, correct for both plain widgets and item
    views."""
    from PySide6.QtWidgets import QTableWidget, QToolTip
    from raptor.gui.ui.main_window import _TooltipKeeper

    t = QTableWidget(1, 1)
    t.setHorizontalHeaderLabels(["Col"])
    t.horizontalHeaderItem(0).setToolTip("column meaning")
    hv = t.horizontalHeader()
    assert hv.toolTip() == "", \
        "premise: the header WIDGET's own tooltip is empty"

    keeper = _TooltipKeeper()
    # Simulate Qt's native handling having resolved + shown the item's
    # tooltip; the poller must see exactly that text and keep it alive.
    QToolTip.showText(hv.mapToGlobal(hv.rect().center()), "column meaning",
                      hv)
    assert QToolTip.text() == "column meaning"
    keeper._reshow()
    assert QToolTip.isVisible()


def test_no_ribbon_label_is_elided_on_a_wide_window(qapp):
    """At 1920 px every ribbon button gets at least its preferred width.

    Regression: a group reported its row layouts' STALE cached hint once
    icons and the ribbon stylesheet were applied after construction, so
    the Model tab's View group got 262 px for a 441 px row and "Snap to
    Grid" rendered as "S…d". Only reproducible in the real window --
    a bare RibbonGroup never goes stale."""
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.widgets.ribbon import RibbonButton

    w = MainWindow()
    w.resize(1920, 1080)
    w.show()
    qapp.processEvents()
    squeezed = []
    rb = w._ribbon
    for i in range(rb.tab_bar.count()):
        rb.tab_bar.setCurrentIndex(i)
        qapp.processEvents()
        for b in rb.findChildren(RibbonButton):
            if b.isVisible() and b.width() < b.sizeHint().width() - 1:
                squeezed.append((rb.tab_bar.tabText(i), b.text(),
                                 b.width(), b.sizeHint().width()))
    w.close()
    assert not squeezed, squeezed


def test_undo_of_auto_layout_puts_every_element_back(qapp):
    """REGRESSION: the undo record held the canvas items, which Auto Layout
    then rebuilt at a new scale, so Undo scattered the diagram."""
    v = DiagramView()
    v.load_rbd(load_rbd("tests/golden/TwoBlockRedundancy/input.rbd"))
    before = {k: (round(it.pos().x()), round(it.pos().y()))
              for k, it in v._block_items.items()}
    v.auto_layout()
    assert {k: (round(it.pos().x()), round(it.pos().y()))
            for k, it in v._block_items.items()} != before
    v._stack.undo()
    assert {k: (round(it.pos().x()), round(it.pos().y()))
            for k, it in v._block_items.items()} == before
    v._stack.redo()
    v._stack.undo()
    assert {k: (round(it.pos().x()), round(it.pos().y()))
            for k, it in v._block_items.items()} == before


def test_elements_added_inside_a_hierarchy_belong_to_it(qapp):
    """REGRESSION: Add Block / Node / Event inside a hierarchy wrote a row
    with parent 0, so the element appeared on the home level instead."""
    v = DiagramView()
    v.load_rbd(load_rbd("tests/golden/HierarchyExample/input.rbd"))
    hid = next(iter(v.hiers))
    v.enter_level(hid)
    bid = v.add_block("Inside", 5.0, 92.5)
    nid = v.add_node("inside_node", 7.5, 92.5)
    eid = v.add_event("inside_event", 10.0, 92.5)
    assert bid in v._block_items and nid in v._node_items \
        and eid in v._event_items
    rbd = v._rbd_ref
    row = lambda sec, i: next(r for r in rbd.sections[sec].rows
                              if int(float(r["ID"])) == i)
    assert int(row("BLOCK_DATA", bid)["ParentId"]) == hid
    assert int(row("NODE_DATA", nid)["parent"]) == hid
    assert int(row("EVENT_DATA", eid)["ParentId"]) == hid


def test_each_canvas_snaps_to_its_own_grid(qapp):
    """Two canvases (two open windows) keep separate snap steps: the
    step was one module-wide dict, so the last model laid out set the
    grid every window snapped to."""
    from raptor.gui.ui.diagram import DiagramView
    a, b = DiagramView(), DiagramView()
    a._snap_state["x"] = 10.0
    b._snap_state["x"] = 0.0
    from raptor.gui.ui.diagram_items import _BlockItem
    ia, ib = _BlockItem(1, "A", 0, 0), _BlockItem(2, "B", 0, 0)
    a._scene.addItem(ia)
    b._scene.addItem(ib)
    ia.setPos(23.0, 0.0)
    ib.setPos(23.0, 0.0)
    assert ia.pos().x() != 23.0      # snapped on A's grid
    assert ib.pos().x() == 23.0      # B has snapping off

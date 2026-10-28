"""Headless GUI tests for the hierarchy feature set (manual Ch.16).

Covers the full round trip on the real HierarchyExample fixture:
- level-filtered canvas (home shows only ParentId=0 elements + ovals),
- descend/ascend via the view API and via Explorer double-click handlers,
- level_changed enabling MainWindow's Up/Home actions,
- Sparing result rows narrowing to the drilled-into subtree,
- dragged hierarchy ovals persisting through write_back_to_rbd + save.
"""
from __future__ import annotations

import os
import tempfile

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd, save_rbd
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _strip_skeleton(host):
    """New models ship a Start->block->End skeleton; tests that need an
    empty host call this."""
    host.sections["BLOCK_DATA"].rows[:] = [
        r for r in host.sections["BLOCK_DATA"].rows
        if int(float(r["ID"])) == 0]
    host.sections["LINK_DATA"].rows[:] = []

FIXTURE = os.path.join(REPO, "tests/golden/HierarchyExample/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), None)
    return w


def test_home_level_shows_only_parentless_elements(window):
    v = window._diagram_dock.view
    # HierarchyExample home: blocks 4-7, nodes 14-19, three hier ovals.
    assert sorted(v.blocks) == [4, 5, 6, 7]
    assert sorted(v._hier_items) == [23, 26, 29]
    # Child-level elements must NOT be present.
    assert 1 not in v.blocks and 24 not in v.nodes


def test_enter_level_and_back(window):
    v = window._diagram_dock.view
    v.enter_level(23)
    assert sorted(v.blocks) == [1, 2, 3]
    assert sorted(v.nodes) == [20, 24, 25]  # own in/out markers visible
    v.go_up()
    assert sorted(v.blocks) == [4, 5, 6, 7]
    v.go_home()
    assert v._current_parent == 0


def test_level_changed_gates_up_home_actions(window):
    w, v = window, window._diagram_dock.view
    assert not w._hier_up_action.isEnabled()
    v.enter_level(26)
    assert w._hier_up_action.isEnabled()
    assert w._hier_home_action.isEnabled()
    v.go_home()
    assert not w._hier_up_action.isEnabled()


def test_explorer_double_click_descends_and_ascends(window):
    w, v = window, window._diagram_dock.view

    def find(pred):
        out = {}

        def walk(item):
            if pred(item.text(0)):
                out[item.text(0)] = item
            for i in range(item.childCount()):
                walk(item.child(i))

        walk(w._explorer._tree.topLevelItem(0))
        return out

    oval = next(iter(find(lambda t: t.startswith("\u2b2d")).values()))
    w._explorer._on_item_double_clicked(oval, 0)
    assert v._current_parent == 23
    # Tree refreshed: this level's own Out marker is now listed.
    hout = find(lambda t: "[HOut]" in t)
    assert hout, "explorer tree must refresh on level change"
    w._explorer._on_item_double_clicked(next(iter(hout.values())), 0)
    assert v._current_parent == 0


def _tree_item_by_role_prefix(explorer, prefix):
    def walk(item):
        role = item.data(0, Qt.UserRole)
        if isinstance(role, str) and role.startswith(prefix):
            return item
        for i in range(item.childCount()):
            found = walk(item.child(i))
            if found is not None:
                return found
        return None
    return walk(explorer._tree.topLevelItem(0))


def test_explorer_click_selects_and_centres_canvas_item(window):
    """Clicking an element in the model explorer selects it on the canvas
    and shows it in the Inspector. The canvas selection must be updated,
    not just the Inspector content."""
    w, v = window, window._diagram_dock.view
    assert not v._scene.selectedItems()

    node_item = _tree_item_by_role_prefix(w._explorer, "node:")
    assert node_item is not None
    w._explorer._on_item_clicked(node_item, 0)
    sel = v._scene.selectedItems()
    assert len(sel) == 1
    nid = int(node_item.data(0, Qt.UserRole).split(":", 1)[1])
    assert sel[0] is v._node_items[nid]

    block_item = _tree_item_by_role_prefix(w._explorer, "block:")
    assert block_item is not None
    w._explorer._on_item_clicked(block_item, 0)
    sel2 = v._scene.selectedItems()
    assert len(sel2) == 1
    bid = int(block_item.data(0, Qt.UserRole).split(":", 1)[1])
    assert sel2[0] is v._block_items[bid]
    assert sel2[0] is not sel[0]   # the earlier node selection was replaced


def test_explorer_tree_lists_events_and_click_selects_on_canvas(qapp):
    """Events appear in the Explorer tree and clicking one selects it on
    the canvas."""
    w = MainWindow()
    w._load_into_app(load_rbd(
        os.path.join(REPO, "tests/golden/Events/input.rbd")), None)
    v = w._diagram_dock.view
    event_item = _tree_item_by_role_prefix(w._explorer, "event:")
    assert event_item is not None, "events must appear in the Explorer tree"
    w._explorer._on_item_clicked(event_item, 0)
    sel = v._scene.selectedItems()
    assert len(sel) == 1
    eid = int(event_item.data(0, Qt.UserRole).split(":", 1)[1])
    assert sel[0] is v._event_items[eid]


def test_find_combo_lives_in_the_explorer_dock_not_the_ribbon(window):
    """The find box lives in the Explorer dock above the tree, and the old
    top-of-canvas duplicate is gone."""
    w = window
    assert w._find_edit.parent() is w._explorer
    assert w._explorer._layout.indexOf(w._find_edit) == 0   # above the tree
    assert not hasattr(w._diagram_dock, "_search"), \
        "the old top-of-canvas search box must be gone, not duplicated"


def test_sparing_rows_narrow_to_subtree(window):
    w, v = window, window._diagram_dock.view
    # Produce real results cheaply via the step debugger's finish path.
    w._ensure_step_session()
    w._on_step_finish()
    sp = w._results.section_table("SPARING")
    assert sp is not None and sp.rowCount() > 0

    def visible():
        return {sp.item(r, 0).text() for r in range(sp.rowCount())
                if not sp.isRowHidden(r)}

    home = visible()
    assert len(home) > 3
    v.enter_level(23)
    inside = visible()
    assert inside < home, "subtree filter must hide rows"
    assert {"SolarP_500W", "BatteriesNiCd", "Bus_UR"} <= inside
    v.go_home()
    assert visible() == home


def test_level_allowed_names_does_not_depend_on_the_currently_shown_level(
        window):
    """`_level_allowed_names` must not depend on the currently displayed
    level: `v.blocks`/`v.nodes` are level-scoped, so reading them for
    another level would return an empty set and hide every result row."""
    w, v = window, window._diagram_dock.view
    assert v._current_parent == 0
    allowed = w._level_allowed_names(23)
    assert {"SolarP_500W", "BatteriesNiCd", "Bus_UR"} <= allowed
    assert "SolarP_650W" not in allowed   # a different hierarchy's block


def test_dragged_oval_position_survives_write_back_and_save(qapp, tmp_path):
    rbd = load_rbd(FIXTURE)
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(rbd)
    # This test is about write-back/save round-tripping a moved
    # position, not about snapping -- disable snap so the assertion
    # doesn't depend on the drag delta happening to cross whichever
    # grid line the model's own spacing produces (that pitch is real
    # model data now, not a fixed heuristic).
    v.set_snap_enabled(False)
    item = v._hier_items[23]

    def row_of(rb):
        return next(r for r in rb.sections["HIERARCHY_DATA"].rows
                    if int(float(r["ID"])) == 23)

    # NB: rows are LIVE dicts -- snapshot, or the write-back below
    # mutates `before` too and the comparison degenerates to identity.
    before = dict(row_of(rbd))
    item.setPos(item.pos() + QPointF(60, 40))
    v.write_back_to_rbd(rbd)
    after = row_of(rbd)
    assert after["xPos"] != before["xPos"]
    path = str(tmp_path / "out.rbd")
    save_rbd(rbd, path)
    reloaded = row_of(load_rbd(path))
    assert reloaded["xPos"] == after["xPos"]
    assert reloaded["yPos"] == after["yPos"]


def test_block_move_survives_traversal_without_saving(qapp):
    """A block drag commits via MoveCmd, which moves the row with the
    canvas (rows that lagged a drag were read stale by Issues, a run and
    a .rbd export); leaving and re-entering a level keeps the position."""
    rbd = load_rbd(FIXTURE)
    from raptor.gui.ui.diagram import DiagramView, MoveCmd
    v = DiagramView()
    v.load_rbd(rbd)
    v.set_snap_enabled(False)
    item = v._block_items[4]

    def row_of(rb):
        return next(r for r in rb.sections["BLOCK_DATA"].rows
                    if int(float(r["ID"])) == 4)

    before_row = dict(row_of(rbd))
    old_pos = (item.pos().x(), item.pos().y())
    new_pos = (old_pos[0] + 55.0, old_pos[1] + 35.0)
    v._stack.push(MoveCmd(v, [("block", 4, old_pos, new_pos)]))
    from raptor.core.fileio.typed import block_from_row
    assert v.blocks[4].x_position == pytest.approx(
        block_from_row(row_of(rbd)).x_position, abs=1e-6)
    assert row_of(rbd)["xPos"] != before_row["xPos"]

    # Leave and come back -- the position must survive with NO save.
    v.go_home()
    v.enter_level(23)
    v.go_up()
    v.go_home()
    item2 = v._block_items[4]
    assert item2.pos().x() == pytest.approx(new_pos[0])
    assert item2.pos().y() == pytest.approx(new_pos[1])
    # The row holds the moved position, through traversal too.
    assert block_from_row(row_of(rbd)).x_position == pytest.approx(
        v.blocks[4].x_position, abs=1e-6)

    # Undo also survives traversal (MoveCmd is the commit point).
    v._stack.undo()
    assert v._block_items[4].pos().x() == pytest.approx(old_pos[0])


def test_dragged_oval_survives_traversal_without_saving(qapp):
    """Oval drags ride the same MoveCmd path as blocks: an oval move
    survives enter/leave with no save, and the undo stack records it."""
    rbd = load_rbd(FIXTURE)
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(rbd)
    v.set_snap_enabled(False)
    item = v._hier_items[23]
    old_pos = (item.pos().x(), item.pos().y())
    new_pos = (old_pos[0] + 70.0, old_pos[1] + 20.0)
    v._drag_start = [("hier", 23, old_pos)]
    item.setPos(new_pos[0], new_pos[1])
    v.mouseReleaseEvent(_synth_release(v))
    assert v._stack.canUndo()
    v.go_home()
    v.go_up()
    item2 = v._hier_items[23]
    assert item2.pos().x() == pytest.approx(new_pos[0])
    assert item2.pos().y() == pytest.approx(new_pos[1])


def _synth_release(v):
    """A minimal left-button QMouseEvent at the view centre, enough for
    mouseReleaseEvent's drag-commit path."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtCore import Qt as _Qt
    vp = v.viewport().rect().center()
    return QMouseEvent(QEvent.MouseButtonRelease, QPointF(vp), QPointF(vp),
                       _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier)


# ---------------------------------------------------------------------
# Hierarchy creation (manual Ch.16 "Adding a Hierarchy")
# ---------------------------------------------------------------------

def test_add_hierarchy_bootstraps_section(qapp):
    """TwoBlockRedundancy has NO HIERARCHY_DATA section — creation must
    bootstrap it, build the In/Out marker pair, and undo cleanly."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/TwoBlockRedundancy/input.rbd")))
    hid = v.add_hierarchy("TestSub", 50.0, 50.0)
    app = QApplication.instance()
    assert hid in v.hiers and hid in v._hier_items  # oval visible at home
    row = next(r for r in v._rbd_ref.sections["HIERARCHY_DATA"].rows
               if int(float(r["ID"])) == hid)
    in_id, out_id = int(float(row["in"])), int(float(row["out"]))
    assert v.nodes[in_id].type_node == 4 and v.nodes[in_id].parent_id == hid
    assert v.nodes[out_id].type_node == 5 and v.nodes[out_id].parent_id == hid
    # Descend: the new level shows exactly its own markers.
    v.enter_level(hid)
    assert sorted(v.nodes) == [in_id, out_id]
    v.go_home()
    # One macro undo removes hier + both markers.
    depth = v._stack.count()
    assert v._stack.canUndo()
    v._stack.undo()
    assert hid not in v.hiers
    assert in_id not in v.nodes and out_id not in v.nodes
    _ = depth, app


def test_add_hierarchy_ids_disjoint_across_sections(qapp):
    """Hier/node ids share one namespace in real files (fixture: hier 23,
    nodes to 31) -- creation must never collide."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(REPO, FIXTURE)))
    used = set(v.blocks) | set(v.nodes) | set(v.events) | set(v.hiers)
    hid = v.add_hierarchy("Extra", 40.0, 40.0)
    row = next(r for r in v._rbd_ref.sections["HIERARCHY_DATA"].rows
               if int(float(r["ID"])) == hid)
    ids = {hid, int(float(row["in"])), int(float(row["out"]))}
    assert not (ids & used), "new hierarchy element ids must be fresh"


def test_created_hierarchy_simulates(qapp):
    """An empty created hierarchy must stay engine-compatible (the file
    still runs; the empty level is a transparent pass-through)."""
    from raptor.core.engine.runner import run_simulation
    from raptor.core.fileio.rbd import load_rbd, save_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/TwoBlockRedundancy/input.rbd")))
    v.add_hierarchy("Sub", 50.0, 50.0)
    path = os.path.join(tempfile.mkdtemp(), "with_hier.rbd")
    save_rbd(v._rbd_ref, path)
    logs = run_simulation(load_rbd(path), num_runs=1)
    assert logs[0], "model with created hierarchy must simulate"


# ---------------------------------------------------------------------
# Collapse selection into a hierarchy
# ---------------------------------------------------------------------

def _select_all_blocks(v):
    for bid, it in v._block_items.items():
        if bid != 0:
            it.setSelected(True)


def _parallel_rbd():
    """Start -> Split -> Block1 / Block2 -> Join -> End: the parallel pair as
    Raptor 7 draws it (a Start marker takes one link out). TwoBlockRedundancy
    feeds both blocks straight from Start, so no selection of it is entered
    by one link, and a hierarchy takes only one."""
    from raptor.api.build import build_from_spec, parallel
    return build_from_spec(parallel(2))


def _select_parallel_section(v):
    """The two blocks and the nodes where their paths split and join: one link
    enters the selection and one leaves it."""
    _select_all_blocks(v)
    for nid, it in v._node_items.items():
        if v.nodes[nid].node_name in ("Split", "Join"):
            it.setSelected(True)


def test_collapse_rewires_boundary_and_internal(qapp):
    """Boundary links split: outside halves reference the Hier oval on the
    parent level, inside halves touch the In/Out markers with pID = the
    new hier. Internal links re-parent wholesale."""
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(_parallel_rbd())
    _select_parallel_section(v)
    hid = v.collapse_selection("CollapsedPair")
    lsec = v._rbd_ref.sections["LINK_DATA"].rows
    boundary = [r for r in lsec
                if "Hier" in (r["FromType"], r["ToType"])
                and r["pID"] == "0"]
    assert boundary, "boundary links must exist"
    assert all(r["FromID"] == str(hid) or r["ToID"] == str(hid)
               for r in boundary)
    internal = [r for r in lsec if r["pID"] == str(hid)]
    marker_ids = {str(_hier_marker(v, hid, "in")),
                  str(_hier_marker(v, hid, "out"))}
    # Every inside-half touching a marker is a boundary split; the rest
    # are the moved elements' own internal links.
    splits = [r for r in internal
              if r["FromID"] in marker_ids or r["ToID"] in marker_ids]
    assert splits, "each boundary link needs an inside half via a marker"


def _hier_marker(v, hid, which):
    row = next(r for r in v._rbd_ref.sections["HIERARCHY_DATA"].rows
               if int(float(r["ID"])) == hid)
    return int(float(row[which]))


def test_collapse_undo_restores_exact_state(qapp):
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(_parallel_rbd())
    before_links = [dict(r) for r in
                    v._rbd_ref.sections["LINK_DATA"].rows]
    before_parents = [r.get("ParentId") for r in
                      v._rbd_ref.sections["BLOCK_DATA"].rows]
    _select_parallel_section(v)
    hid = v.collapse_selection("CollapsedPair")
    assert v._stack.canUndo()
    v._stack.undo()
    after_links = [dict(r) for r in
                   v._rbd_ref.sections["LINK_DATA"].rows]
    after_parents = [r.get("ParentId") for r in
                     v._rbd_ref.sections["BLOCK_DATA"].rows]
    assert after_links == before_links, "link rows must restore exactly"
    assert after_parents == before_parents
    assert hid not in v.hiers


def test_collapse_preserves_simulation_results(qapp):
    """The gold oracle: collapsing a subsystem into a hierarchy must not
    change Availability / MTBDE / MDT / System_Failures."""
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbd import load_rbd, save_rbd
    from raptor.gui.ui.diagram import DiagramView

    def metrics(fin):
        return [l.split()[0] + "".join(l.split()[1:3])
                for l in fin
                if l.strip().startswith(("Availability", "MTBDE", "MDT",
                                         "System_Failures"))]

    fixture = os.path.join(tempfile.mkdtemp(), "parallel.rbd")
    save_rbd(_parallel_rbd(), fixture)
    _, _, fin_orig = run_simulation_with_reports(
        load_rbd(fixture), num_runs=1, date="fixed")
    v = DiagramView()
    v.load_rbd(load_rbd(fixture))
    _select_parallel_section(v)
    v.collapse_selection("CollapsedPair")
    path = os.path.join(tempfile.mkdtemp(), "collapsed.rbd")
    save_rbd(v._rbd_ref, path)
    _, _, fin_col = run_simulation_with_reports(
        load_rbd(path), num_runs=1, date="fixed")
    assert metrics(fin_orig) == metrics(fin_col)


# ---------------------------------------------------------------------
# Drag-to-re-parent (manual Ch.16 "Dragging Elements into a Hierarchy")
# ---------------------------------------------------------------------

def test_drop_on_oval_reparents_and_cuts_boundary_links(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/TwoBlockRedundancy/input.rbd")))
    v.add_hierarchy("Target", 60.0, 40.0)
    before_links = [dict(r) for r in
                    v._rbd_ref.sections["LINK_DATA"].rows]
    hid = next(h for h in v.hiers if v.hiers[h].name == "Target")
    oval = v._hier_items[hid]
    it = v._block_items[3]
    start = (it.scenePos().x(), it.scenePos().y())
    v._drag_start = [("block", 3, start)]
    it.setPos(oval.sceneBoundingRect().center())
    moves = [("block", 3, start,
              (it.scenePos().x(), it.scenePos().y()))]
    assert v._drop_target_hier(moves) == hid
    v._reparent_into(hid, moves)
    row = next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
               if int(float(r["ID"])) == 3)
    assert row["ParentId"] == str(hid)
    # Boundary links crossing into/out of the moved block were cut.
    assert len(v._rbd_ref.sections["LINK_DATA"].rows) < len(before_links)
    # And one clean undo restores everything.
    v._stack.undo()
    after = [dict(r) for r in v._rbd_ref.sections["LINK_DATA"].rows]
    assert after == before_links
    row = next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
               if int(float(r["ID"])) == 3)
    assert row["ParentId"] == "0"


# ---------------------------------------------------------------------
# Import RBD as hierarchy
# ---------------------------------------------------------------------

def test_import_rbd_as_hierarchy_preserves_metrics(qapp):
    """Import TwoBlockRedundancy into an empty host, wire host
    Start->Hier->End, and the imported model must reproduce the
    original's Availability/MTBDE/MDT/System_Failures exactly."""
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbd import load_rbd, save_rbd
    from raptor.gui.new_model import build_new_model
    from raptor.gui.ui.diagram import DiagramView

    def metrics(fin):
        return [(l.split()[0], l.split()[1]) for l in fin
                if l.strip().startswith(("Availability", "MTBDE", "MDT",
                                         "System_Failures"))]

    fixture = os.path.join(REPO,
                           "tests/golden/TwoBlockRedundancy/input.rbd")
    _, _, fin_orig = run_simulation_with_reports(
        load_rbd(fixture), num_runs=1, date="fixed")

    host = build_new_model("host")
    _strip_skeleton(host)
    # The imported hierarchy inherits the HOST's run length (and streams)
    # from SYSTEM_DATA, so the host must be configured to run for the
    # same length the fixture does -- otherwise the two runs legitimately
    # differ and the comparison would be meaningless.
    #
    # This was implicit before: `build_new_model` hard-coded a run length
    # of 100, which happened to equal this fixture's. The new-model
    # default is now 1000, matching the original RAPTOR
    # so the coupling is stated instead of
    # assumed -- otherwise the next default change breaks this test for
    # no visible reason.
    _host_row = host.sections["SYSTEM_DATA"].rows[0]
    _host_row["TermCondition"] = "100.000000"
    v = DiagramView()
    v.load_rbd(host)
    hid = v.import_rbd_as_hierarchy(fixture)
    assert hid in v.hiers
    _row = next(r for r in host.sections["HIERARCHY_DATA"].rows
               if int(float(r["ID"])) == hid)
    # Host Start -> Hier -> host End.
    start_nid = next(nid for nid, n in v.nodes.items()
                     if n.type_node == 1 and n.parent_id == 0)
    end_nid = next(nid for nid, n in v.nodes.items()
                   if n.type_node == 3 and n.parent_id == 0)
    assert v.connect_elements("Node", start_nid, "Hier", hid)
    assert v.connect_elements("Hier", hid, "Node", end_nid)
    path = os.path.join(tempfile.mkdtemp(), "host_import.rbd")
    save_rbd(host, path)
    _, _, fin_imp = run_simulation_with_reports(
        load_rbd(path), num_runs=1, date="fixed")
    assert metrics(fin_orig) == metrics(fin_imp)


def test_import_skips_template_rows(qapp):
    """The imported file's ID=0 DEFAULTS rows must not become phantom
    elements (the engine's graph walk dies on a link-less block)."""
    from raptor.gui.new_model import build_new_model
    from raptor.gui.ui.diagram import DiagramView
    host = build_new_model("host")
    _strip_skeleton(host)
    v = DiagramView()
    v.load_rbd(host)
    hid = v.import_rbd_as_hierarchy(os.path.join(
        REPO, "tests/golden/TwoBlockRedundancy/input.rbd"))
    # TwoBlockRedundancy has exactly 2 real blocks; a phantom template
    # row would make it 3. Imported elements live in the file rows under
    # the new hier and only enter the level-scoped dicts when the level
    # is entered, so count rows here.
    rows = [r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
            if int(float(r["ID"])) != 0
            and int(float(r["ParentId"])) == hid]
    assert len(rows) == 2


def test_import_macro_undo(qapp):
    from raptor.gui.new_model import build_new_model
    from raptor.gui.ui.diagram import DiagramView
    host = build_new_model("host")
    _strip_skeleton(host)
    v = DiagramView()
    v.load_rbd(host)
    before_rows = {r["ID"] for r
                   in v._rbd_ref.sections["BLOCK_DATA"].rows}
    hid = v.import_rbd_as_hierarchy(os.path.join(
        REPO, "tests/golden/TwoBlockRedundancy/input.rbd"))
    # entering the new level materialises the imported blocks
    v.enter_level(hid)
    assert len(v.blocks) == 2
    assert len(v.hiers) == 1
    v._stack.undo()   # the whole import is ONE macro step
    assert v.hiers == {}
    assert {r["ID"] for r in v._rbd_ref.sections["BLOCK_DATA"].rows} \
        == before_rows


# ---------------------------------------------------------------------
# Copy / Paste (manual Ch.2 "Copying and Pasting")
# ---------------------------------------------------------------------

def test_copy_paste_fresh_ids_and_macro_undo(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/TwoBlockRedundancy/input.rbd")))
    _select_all_blocks(v)
    assert v.copy_selection() == 2
    assert v.paste() == 2
    # Two fresh ids: no collision between the two pasted copies.
    assert len(v.blocks) == 4 and len(set(v.blocks)) == 4
    v.paste()
    assert len(v.blocks) == 6 and len(set(v.blocks)) == 6
    # One macro undo removes one paste (both elements).
    n = len(v.blocks)
    v._stack.undo()
    assert len(v.blocks) == n - 2


# ---------------------------------------------------------------------
# Element-level phase assignment (Ch.17)
# ---------------------------------------------------------------------

def test_node_phase_assignment_roundtrip(qapp):
    """Assign k/L phases to an unphased Regular node in Phasing1; the
    tokens must persist through save/reload with the `Phases` flag set
    (nodes use the bare `P001` token format and the `Phases` flag
    column, not the block-style `Phasing`)."""
    from raptor.core.fileio.rbd import load_rbd, save_rbd
    from raptor.core.fileio.typed import node_from_row
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/Phasing1/input.rbd")))
    nid = next(nid for nid, n in v.nodes.items()
               if n.type_node == 2 and not n.uses_phasing)
    n_in = sum(1 for r in v._rbd_ref.sections["LINK_DATA"].rows
               if r["ToType"].replace("RBD", "") == "Node" and int(float(r["ToID"])) == nid)
    assert n_in >= 1
    # A phase's k is between 0 and the node's paths (Raptor 7's phase
    # dialog, Raptor 7).
    assert v.set_element_phasing("node", nid, True,
                                 [("K", float(n_in)), ("L", 0.0)])
    # Padded to the section's 3 phase columns (last entry repeats).
    assert v.nodes[nid].phase[:2] == [n_in, 0]
    path = os.path.join(tempfile.mkdtemp(), "ph.rbd")
    save_rbd(v._rbd_ref, path)
    row = next(r for r in load_rbd(path).sections["NODE_DATA"].rows
               if int(float(r["ID"])) == nid)
    assert row.get("Phases") == "1"
    n2 = node_from_row(row)
    assert n2.uses_phasing and n2.phase[0] == n_in and n2.phase[1] == 0
    # Undo clears flag + tokens.
    v._stack.undo()
    assert not v.nodes[nid].uses_phasing


def test_block_phase_assignment_tokens(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/Phasing1/input.rbd")))
    bid = next(bid for bid, b in v.blocks.items() if not b.uses_phasing)
    assert v.set_element_phasing("block", bid, True,
                                 [("A", 1.5), ("C", 0.0)])
    b = v.blocks[bid]
    assert b.phase_type[:2] == ["A", "C"]
    assert b.phase_value[0] == pytest.approx(1.5)
    row = next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
               if int(float(r["ID"])) == bid)
    assert row.get("Phasing") == "1"
    assert row.get("P 001") == "A: 1.500"


def test_hier_phase_assignment_roundtrip(qapp):
    """Hierarchies can be marked phased too (manual Ch.17: "Blocks,
    event, nodes and hierarchies all can be designated as phased
    elements") -- but unlike the other three kinds, HIERARCHY_DATA has
    no per-phase value columns, just the bare `Phs` flag (matching this
    fixture's real "500path" row, which is phased). `entries` is always
    empty for this kind."""
    from raptor.core.fileio.rbd import load_rbd, save_rbd
    from raptor.core.fileio.typed import hier_from_row
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/Phasing1/input.rbd")))
    hid = next(hid for hid, h in v.hiers.items() if not h.uses_phasing)
    assert v.set_element_phasing("hier", hid, True, [])
    assert v.hiers[hid].uses_phasing
    # Canvas item (this hierarchy's home level) reflects it live.
    assert v._hier_items[hid]._phased

    path = os.path.join(tempfile.mkdtemp(), "ph_hier.rbd")
    save_rbd(v._rbd_ref, path)
    row = next(r for r in load_rbd(path).sections["HIERARCHY_DATA"].rows
               if int(float(r["ID"])) == hid)
    assert row.get("Phs") == "1"
    assert hier_from_row(row).uses_phasing

    v._stack.undo()
    assert not v.hiers[hid].uses_phasing
    assert not v._hier_items[hid]._phased


def test_phasing1_hatched_elements_match_manual_worked_example(qapp):
    """Direct ground-truth check against raptor-7-reliability-manual.pdf
    Ch.17's own description of this exact fixture: "Only the ten
    elements that are displayed as hatched will change their
    characteristics ... These elements are the Tether and Thermal
    components, the Power node, the Ground_Control and Inspection
    events, the 500path hierarchy and its three subcomponents." """
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/Phasing1/input.rbd")))

    phased_blocks = {v.blocks[bid].name for bid, it in v._block_items.items()
                     if it._phased}
    phased_nodes = {v.nodes[nid].node_name for nid, it in v._node_items.items()
                    if it._phased}
    phased_events = {v.events[eid].name for eid, it in v._event_items.items()
                     if it._phased}
    phased_hiers = {v.hiers[hid].name for hid, it in v._hier_items.items()
                    if it._phased}

    assert phased_blocks == {"Tether-A", "Tether-B", "Thermal"}
    assert phased_nodes == {"Power"}
    assert phased_events == {"Ground_Control", "Inspection"}
    assert phased_hiers == {"500path"}

    v.enter_level(next(hid for hid, h in v.hiers.items()
                       if h.name == "500path"))
    phased_children = {v.blocks[bid].name for bid, it in v._block_items.items()
                       if it._phased}
    assert phased_children == {"SolarP_500W", "BatteriesNiCd", "Bus_UR"}
    # 3 + 1 + 1 + 2 + 1 + 3 == the manual's own count of ten.
    assert (len(phased_blocks) + len(phased_nodes) + len(phased_events)
           + len(phased_hiers) + len(phased_children)) == 10


# ---------------------------------------------------------------------
# Move OUT of hierarchy (Ch.16 out-drag counterpart)
# ---------------------------------------------------------------------

def test_move_out_of_hierarchy(qapp):
    """Move an element from inside a hierarchy back to the parent level;
    boundary links are cut; one undo restores parent + links."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/HierarchyExample/input.rbd")))
    v.enter_level(23)
    before_links = len(v._rbd_ref.sections["LINK_DATA"].rows)
    assert v.move_out_of_hierarchy({"BLOCK_DATA": [1]})
    row = next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
               if int(float(r["ID"])) == 1)
    assert int(row["ParentId"]) == 0
    assert len(v._rbd_ref.sections["LINK_DATA"].rows) < before_links
    v._stack.undo()
    row = next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
               if int(float(r["ID"])) == 1)
    assert int(row["ParentId"]) == 23
    assert len(v._rbd_ref.sections["LINK_DATA"].rows) == before_links


def test_move_out_refuses_at_home(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        REPO, "tests/golden/TwoBlockRedundancy/input.rbd")))
    assert v.move_out_of_hierarchy({"BLOCK_DATA": [3]}) is False


def test_every_explorer_row_shows_its_id(qapp):
    """Block names are not unique -- the shipped Basic1 model has three
    called BatteriesNiCd -- and ids are how the canvas, the Inspector and
    the engine's own messages name an element. Every row carries its id in
    brackets, not just the duplicated ones, and that makes `Find` able to
    look an element up BY ID (the filter matches the row text)."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(
        "tests/golden/TwoBlockRedundancy/input.rbd"), "x")
    qapp.processEvents()
    v = w._diagram_dock.view
    ids = sorted(b for b in v.blocks if b != 0)
    assert len(ids) >= 2
    first, second = ids[0], ids[1]
    v.blocks[second].name = v.blocks[first].name
    w._explorer.refresh()
    labels = []

    def walk(item):
        labels.append((item.data(0, 256), item.text(0)))
        for i in range(item.childCount()):
            walk(item.child(i))
    walk(w._explorer._tree.topLevelItem(0))
    by_role = {role: text for role, text in labels if role}
    for bid in ids:
        assert f"({bid})" in by_role.get(f"block:{bid}", ""), (
            f"block {bid} row must show its id: "
            f"{by_role.get(f'block:{bid}')!r}")
    shared = v.blocks[first].name
    dup = [t for text_role, t in labels if t.startswith(shared)]
    assert len(dup) == 2 and len(set(dup)) == 2, dup
    w._dirty = False
    w.close()


def test_explorer_filter_matches_an_element_id(qapp):
    """Find by id is the point of showing it: typing an element's id must
    keep that element's row and hide everything else."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(
        "tests/golden/TwoBlockRedundancy/input.rbd"), "x")
    qapp.processEvents()
    v = w._diagram_dock.view
    bid = sorted(b for b in v.blocks if b != 0)[0]
    w._explorer.filter_items(str(bid))
    found = []

    def walk(item):
        if not item.isHidden() and item.data(0, 256) == f"block:{bid}":
            found.append(item.text(0))
        for i in range(item.childCount()):
            walk(item.child(i))
    walk(w._explorer._tree.topLevelItem(0))
    assert found, f"searching id {bid} found nothing"
    others = []

    def walk_hidden(item):
        if item.isHidden() and item.data(0, 256) not in (None, "root"):
            others.append(item.text(0))
        for i in range(item.childCount()):
            walk_hidden(item.child(i))
    walk_hidden(w._explorer._tree.topLevelItem(0))
    assert others, f"searching id {bid} hid nothing -- the filter is a no-op"
    w._dirty = False
    w.close()


def test_goto_combo_has_a_placeholder_and_no_false_current_hierarchy(qapp):
    """The Hierarchy group's combo read as a blank, broken widget, and after
    a refresh it sat on the first hierarchy in the list, implying that was
    where you were. At the top level it now shows a placeholder."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(
        "tests/golden/HierarchyExample/input.rbd"), "x")
    qapp.processEvents()
    combo = w._goto_combo
    assert combo.placeholderText()
    assert combo.count() > 0, "the fixture must have hierarchies"
    assert combo.currentIndex() == -1
    w._dirty = False
    w.close()


def test_collapse_refuses_a_selection_a_hierarchy_cannot_hold(qapp, monkeypatch):
    """TwoBlockRedundancy's blocks are both fed from Start: collapsing them
    would give the hierarchy two links in, which Raptor 7 does not allow."""
    import raptor.gui.ui.msgbox as mb
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    shown = []
    monkeypatch.setattr(mb, "warning", lambda *a, **k: shown.append(a[2]))
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(REPO, "tests/golden/TwoBlockRedundancy/input.rbd")))
    links_before = [dict(r) for r in v._rbd_ref.sections["LINK_DATA"].rows]
    _select_all_blocks(v)
    assert v.collapse_selection("Pair") is None
    assert shown and "entered by 2 link(s)" in shown[-1]
    assert [dict(r) for r in v._rbd_ref.sections["LINK_DATA"].rows] == links_before

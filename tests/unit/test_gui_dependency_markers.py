"""Dependency-marker canvas badges.

A badge marks a dependent block or node; its target may live on a
different hierarchy level, in which case there is no same-level connector
line but the badge's click still navigates there via `locate_element`.

Block/Node `dependency_num`/`dep_type` encoding (see `_dependency_group`
in inspector.py): 0=independent, -1=locally dependent, -2=system
dependent, >0 with dep_type=="RBDBlock"/"RBDNode"=dependent on that
element.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.diagram import DiagramView

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEP_FIXTURE = os.path.join(REPO, "tests/golden/BlockDependency/input.rbd")
HIER_FIXTURE = os.path.join(REPO, "tests/golden/HierarchyExample/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_independent_block_has_no_badge(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(DEP_FIXTURE))
    QApplication.processEvents()
    assert v._block_items[3]._dep_badge is None


def test_block_dependent_on_block_gets_badge_and_same_level_target(qapp):
    """Block 4 depends on block 3 (DepNum=3, DepType=RBDBlock), both on
    the home level -- the badge's target_item must resolve to block 3's
    OWN canvas item so the hover connector has something to point at."""
    v = DiagramView()
    v.load_rbd(load_rbd(DEP_FIXTURE))
    QApplication.processEvents()
    badge = v._block_items[4]._dep_badge
    assert badge is not None
    assert badge["glyph"] == "D"
    assert badge["target_kind"] == "block"
    assert badge["target_item"] is v._block_items[3]
    assert "click to jump to it" in badge["tooltip"]
    assert "different hierarchy level" not in badge["tooltip"]


def test_hover_draws_and_clears_a_dashed_connector(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(DEP_FIXTURE))
    QApplication.processEvents()
    item4 = v._block_items[4]
    before = len(v._scene.items())
    v._on_dep_hover(item4, True)
    assert v._dep_hover_line is not None
    assert len(v._scene.items()) == before + 1
    v._on_dep_hover(item4, False)
    assert v._dep_hover_line is None
    assert len(v._scene.items()) == before


def test_hover_on_independent_block_draws_nothing(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(DEP_FIXTURE))
    QApplication.processEvents()
    v._on_dep_hover(v._block_items[3], True)
    assert v._dep_hover_line is None


def test_click_on_dependent_block_navigates_to_target(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(DEP_FIXTURE))
    QApplication.processEvents()
    v._scene.clearSelection()
    v._on_dep_click(v._block_items[4])
    selected = [it._block_id for it in v._scene.selectedItems()
               if hasattr(it, "_block_id")]
    assert selected == [3]


def test_dependency_badge_no_longer_intercepts_mouse_press(qapp):
    """`_BlockItem`/`_NodeItem` do not override `mousePressEvent`, so a
    click on the badge sub-area falls through to Qt's default
    select/move handling rather than jumping to the dependency."""
    from raptor.gui.ui.diagram import _BlockItem, _NodeItem
    from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsEllipseItem
    assert _BlockItem.mousePressEvent is QGraphicsRectItem.mousePressEvent
    assert _NodeItem.mousePressEvent is QGraphicsEllipseItem.mousePressEvent


def test_focus_on_dependency_offered_only_for_a_single_badged_element():
    """The right-click "Focus on Dependency" entry (`_show_context_menu`)
    is the only way to navigate to a dependency's target; it is gated
    by `_dep_focus_offered`, a pure predicate testable headlessly."""
    from raptor.gui.ui.diagram import _dep_focus_offered
    assert _dep_focus_offered(1, True)
    assert not _dep_focus_offered(1, False)   # no badge -- nothing to focus
    assert not _dep_focus_offered(2, True)    # multi-select -- ambiguous
    assert not _dep_focus_offered(0, True)


def test_locally_and_system_dependent_use_distinct_badges(qapp):
    """HierarchyExample has real -1 (SolarP_500W) and -2 (Attitude)
    blocks -- confirms both virtual-target cases get a badge (no
    target_item, since there's no single element to point a line at)
    with visibly different colours/glyphs from each other and from the
    block-dependent ('D') case."""
    v = DiagramView()
    v.load_rbd(load_rbd(HIER_FIXTURE))
    QApplication.processEvents()
    # SolarP_500W/Attitude aren't even necessarily on the home level
    # DiagramView opens on -- read their dependency fields straight off
    # the raw model rows rather than depending on `v.blocks` (level-
    # scoped) already containing them.
    rbd = v._rbd_ref
    from raptor.core.fileio.typed import block_from_row
    solar_row = next(r for r in rbd.sections["BLOCK_DATA"].rows
                     if r.get("blockName") == "SolarP_500W")
    attitude_row = next(r for r in rbd.sections["BLOCK_DATA"].rows
                        if r.get("blockName") == "Attitude")
    solar_blk = block_from_row(solar_row)
    attitude_blk = block_from_row(attitude_row)
    assert solar_blk.dependency_num == -1
    assert attitude_blk.dependency_num == -2
    local_desc = v._dependency_visual(solar_blk.dependency_num, solar_blk.dep_type)
    system_desc = v._dependency_visual(attitude_blk.dependency_num, attitude_blk.dep_type)
    assert local_desc["glyph"] == "L"
    assert system_desc["glyph"] == "S"
    assert local_desc["colour"] != system_desc["colour"]
    assert local_desc["target_item"] is None
    assert system_desc["target_item"] is None
    dependent_desc = v._dependency_visual(3, "RBDBlock")
    assert dependent_desc["glyph"] not in (local_desc["glyph"], system_desc["glyph"])


def test_cross_level_dependency_target_has_no_line_but_is_navigable(qapp):
    """A sub-level block depending on a home-level block shows a badge
    with a resolvable name (via the whole-model index) but no
    `target_item`, so no line is drawn; clicking still navigates there
    via `locate_element`, crossing the level boundary."""
    rbd = load_rbd(HIER_FIXTURE)
    solar_row = next(r for r in rbd.sections["BLOCK_DATA"].rows
                     if r.get("blockName") == "SolarP_500W")
    thermal_row = next(r for r in rbd.sections["BLOCK_DATA"].rows
                       if r.get("blockName") == "Thermal")
    assert solar_row["ParentId"] != thermal_row["ParentId"], \
        "fixture assumption: SolarP_500W and Thermal are on different levels"
    solar_row["DepNum"] = thermal_row["ID"]
    solar_row["DepType"] = "RBDBlock"

    v = DiagramView()
    v.load_rbd(rbd)
    QApplication.processEvents()
    v._set_level(int(float(solar_row["ParentId"])))
    QApplication.processEvents()
    solar_id = int(float(solar_row["ID"]))
    item = v._block_items.get(solar_id)
    assert item is not None
    badge = item._dep_badge
    assert badge is not None
    assert badge["target_item"] is None
    assert badge["target_name"] == "Thermal"
    assert "different hierarchy level" in badge["tooltip"]

    v._on_dep_hover(item, True)
    assert v._dep_hover_line is None   # nothing to connect to on THIS level

    v._on_dep_click(item)
    QApplication.processEvents()
    thermal_id = int(float(thermal_row["ID"]))
    assert v._current_parent == int(float(thermal_row["ParentId"]))
    selected = [it._block_id for it in v._scene.selectedItems()
               if hasattr(it, "_block_id")]
    assert selected == [thermal_id]


def test_set_block_dep_master_refreshes_badge_live(qapp):
    """Editing dependency via the Inspector's own entry point
    (`set_block_dep_master`, pushed through the undo stack) must update
    the badge immediately, not just on the next full scene rebuild."""
    v = DiagramView()
    v.load_rbd(load_rbd(DEP_FIXTURE))
    QApplication.processEvents()
    item3 = v._block_items[3]
    assert item3._dep_badge is None
    v.set_block_dep_master(3, -1, "None")
    assert item3._dep_badge is not None
    assert item3._dep_badge["glyph"] == "L"
    v._stack.undo()
    assert item3._dep_badge is None

"""Hierarchy comparison: chip row on the Charts tab and rings on the canvas."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QToolButton

from raptor.api.insight.hierarchy import SERIES_COLOURS
from raptor.gui.ui.charts_page import ChartsPage
from tests.unit.test_charts_page import _hierarchy_inputs

MODEL = "new_docs/models/WaterTreatment.rbds"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _page(chart="hierarchy-overlay"):
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    page.select(chart)      # the chips show only where Compare applies
    return page


def _closes(page):
    return page.chip_row.findChildren(QToolButton, "CompareChipClose")


def test_chips_follow_the_explicit_comparison(qapp):
    page = _page()
    seen = []
    page.compare_changed.connect(seen.append)
    assert page.chip_row.isHidden()          # automatic top four: no chips
    page.set_compare([100, 200])
    assert not page.chip_row.isHidden()
    assert seen[-1] == [100, 200]
    names = [b.accessibleName() for b in _closes(page)]
    assert names[0] == "Remove Intake from comparison"
    assert len(names) == 2
    page.set_compare([100, 200])             # unchanged: no repeat signal
    assert len(seen) == 1


def test_chip_close_removes_one_and_menu_stays_in_sync(qapp):
    page = _page()
    page.set_compare([100, 200])
    _closes(page)[0].click()
    assert page.selected_hierarchies() == [200]
    assert page.compare_button.text() == "Compare: 1 selected"
    assert len(_closes(page)) == 1
    _closes(page)[0].click()
    assert page.selected_hierarchies() == []
    assert page.chip_row.isHidden()
    assert page.compare_button.text() == "Compare: top 4"


def test_picker_pick_and_clear_emit(qapp):
    page = _page()
    seen = []
    page.compare_changed.connect(seen.append)
    picker = page.picker("compare")
    picker._items[100].setCheckState(0, Qt.CheckState.Checked)
    assert seen[-1] == [100]
    page.set_compare([])
    assert seen[-1] == []


def test_a_rerun_dropping_a_hierarchy_clears_its_chip(qapp):
    page = _page()
    seen = []
    page.set_compare([100, 200])
    page.compare_changed.connect(seen.append)
    per_run, attribution, importance = _hierarchy_inputs()
    attribution = dict(attribution)
    attribution["hierarchies"] = [h for h in attribution["hierarchies"]
                                  if h["id"] != 200]
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_2")
    assert seen[-1] == [100]
    assert len(_closes(page)) == 1


@pytest.mark.gui
def test_canvas_rings_and_fades_and_restores(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    try:
        w._load_into_app(load_rbds(MODEL), MODEL)
        view = w._diagram_dock.view
        ids = sorted(view._hier_items)
        assert len(ids) >= 2
        dirty = w._dirty
        w._on_chart_compare_changed([ids[1], ids[0]])
        first, second = view._hier_items[ids[1]], view._hier_items[ids[0]]
        assert first._compare_ring == SERIES_COLOURS[0]
        assert second._compare_ring == SERIES_COLOURS[1]
        others = [i for i in view._hier_items if i not in ids[:2]]
        for hid in others:
            assert view._hier_items[hid].opacity() == pytest.approx(0.35)
            assert view._hier_items[hid]._compare_ring is None
        assert first.opacity() == 1.0
        assert any(b.opacity() == pytest.approx(0.35)
                   for b in view._block_items.values()) or not view._block_items
        assert w._dirty == dirty
        assert not w._undo_stack.canUndo() if hasattr(w, "_undo_stack") else True
        # a level change rebuilds the items: the emphasis is re-applied
        view._set_level(ids[0])
        view._set_level(0)
        assert view._hier_items[ids[1]]._compare_ring == SERIES_COLOURS[0]
        w._on_chart_compare_changed([])
        assert all(h._compare_ring is None and h.opacity() == 1.0
                   for h in view._hier_items.values())
    finally:
        w._dirty = False
        w.close()


@pytest.mark.gui
def test_links_fade_with_their_faded_ends_and_restore(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    try:
        w._load_into_app(load_rbds(MODEL), MODEL)
        view = w._diagram_dock.view
        assert view._links
        ids = sorted(view._hier_items)
        assert all(link.opacity() == 1.0 for link in view._links)
        dirty = w._dirty
        w._on_chart_compare_changed([ids[0]])
        faded = 0
        for link in view._links:
            want = min(link._from.opacity(), link._to.opacity())
            assert link.opacity() == pytest.approx(want)
            faded += want < 1.0
        assert faded                      # some link touches a faded end
        assert w._dirty == dirty
        # an out-of-service "cut" end dims the element, not its links
        end = view._links[0]._from
        end._oos_state = "cut"
        w._on_chart_compare_changed([])
        end.setOpacity(0.45)
        view._apply_group_view()
        assert view._links[0].opacity() == pytest.approx(
            min(view._view_fade(view._links[0]._from),
                view._view_fade(view._links[0]._to)))
        end._oos_state = None
        end.setOpacity(1.0)
        view._apply_group_view()
        assert all(link.opacity() == 1.0 for link in view._links)
        # group view fades links too
        view.set_group_view({f"hier:{ids[0]}"})
        assert any(link.opacity() < 1.0 for link in view._links)
        view.set_group_view(None)
        assert all(link.opacity() == 1.0 for link in view._links)
    finally:
        w._dirty = False
        w.close()


def test_chips_hide_where_compare_does_not_apply(qapp):
    page = _page()
    page.set_compare([100, 200])
    assert not page.chip_row.isHidden()
    page.select("hierarchy-scorecard")       # not a comparison chart
    assert page.chip_row.isHidden()
    assert page.compare_button.isHidden()
    page.select("hierarchy-overlay")
    assert not page.chip_row.isHidden()
    page.nav.setCurrentItem(page.nav.topLevelItem(0))   # a group overview
    assert page.chip_row.isHidden()

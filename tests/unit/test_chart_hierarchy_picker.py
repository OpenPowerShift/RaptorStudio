"""The searchable Scope / Compare popup (hierarchy_tree_popup)."""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from raptor.gui.ui.hierarchy_tree_popup import COMPARE, SCOPE, HierarchyTreePopup


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _rows(n_top=3, fan=3, depth=3):
    """A nested tree: Area i > Unit j > Pump k ..."""
    rows: list[dict] = []
    counter = [0]

    def add(parent, path, level, share):
        for i in range(n_top if level == 1 else fan):
            counter[0] += 1
            ref = counter[0]
            name = (f"Area {i}" if level == 1 else
                    f"Unit {i}" if level == 2 else f"Pump {i}")
            here = [*path, name]
            rows.append({"id": ref, "name": name, "path": here,
                         "depth": level, "parent": parent,
                         "dtci": {"mean": share / (i + 1)}})
            if level < depth:
                add(ref, here, level + 1, share / 3)
    add(0, [], 1, 0.6)
    return rows


def _picker(mode=SCOPE, rows=None, current=()):
    picker = HierarchyTreePopup(mode)
    picker.set_rows(rows if rows is not None else _rows(), current=current)
    return picker


def test_collapsed_below_the_first_level_by_default(qapp):
    picker = _picker()
    picker.show()
    top = picker.tree.topLevelItem(1)
    assert not top.isExpanded()
    assert picker.tree.currentItem().text(0) == "System"


def test_current_scope_is_selected_and_its_path_open(qapp):
    rows = _rows()
    deep = next(r for r in rows if r["depth"] == 3)
    picker = _picker(current=[deep["id"]], rows=rows)
    item = picker.tree.currentItem()
    assert item.text(0) == deep["name"]
    assert item.parent().isExpanded() and item.parent().parent().isExpanded()


def test_filter_shows_matches_and_their_ancestors(qapp):
    picker = _picker()
    found = picker.apply_filter("area 1 > unit 2")
    assert found == 1 + 3  # the unit and its three pumps match by path
    shown = picker.visible_names()
    assert "Area 1" in shown and "Unit 2" in shown
    assert "Area 0" not in shown
    assert picker.count_label.text() == f"{found} of {len(picker._all)}"
    assert picker.apply_filter("zzz") == 0
    assert "No match" in picker.count_label.text()
    assert picker.apply_filter("") == len(picker._all)
    assert len(picker.visible_names()) == len(picker._all)


def test_plain_text_matches_names_not_every_descendant(qapp):
    picker = _picker()
    found = picker.apply_filter("area 1")
    assert found == 1
    assert picker.apply_filter("area 1 >unit 2") == 4


def test_filter_is_case_insensitive(qapp):
    picker = _picker()
    assert picker.apply_filter("PUMP") == picker.apply_filter("pump") > 0


def test_share_of_downtime_is_shown_per_row(qapp):
    picker = _picker()
    assert picker.tree.topLevelItem(1).text(1) == "60.0 %"


def test_activating_a_row_chooses_it_and_closes(qapp):
    picker = _picker()
    picker.show()
    spy = QSignalSpy(picker.scope_chosen)
    item = picker.tree.topLevelItem(2)
    picker._on_activated(item, 0)
    assert spy.at(0)[0] == item.data(0, Qt.ItemDataRole.UserRole)
    assert not picker.isVisible()


def test_choosing_system_gives_zero(qapp):
    picker = _picker()
    spy = QSignalSpy(picker.scope_chosen)
    picker._on_activated(picker.tree.topLevelItem(0), 0)
    assert spy.at(0)[0] == 0


def test_down_from_the_search_box_moves_the_tree_and_enter_chooses(qapp):
    picker = _picker()
    picker.show()
    picker.search.setFocus()
    spy = QSignalSpy(picker.scope_chosen)
    QTest.keyClick(picker.search, Qt.Key.Key_Down)
    assert picker.tree.currentItem() is picker.tree.topLevelItem(1)
    QTest.keyClick(picker.search, Qt.Key.Key_Up)
    assert picker.tree.currentItem() is picker.tree.topLevelItem(0)
    QTest.keyClick(picker.search, Qt.Key.Key_Down)
    QTest.keyClick(picker.search, Qt.Key.Key_Return)
    assert spy.count() == 1


def test_escape_closes(qapp):
    picker = _picker()
    picker.show()
    picker.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                   Qt.KeyboardModifier.NoModifier))
    assert not picker.isVisible()


def test_accessible_names(qapp):
    picker = _picker()
    assert picker.search.accessibleName()
    assert picker.tree.accessibleName()


def _tick(picker, ref):
    item = picker._items[ref]
    item.setCheckState(0, Qt.CheckState.Checked)


def test_compare_limit_is_four(qapp):
    picker = _picker(COMPARE)
    spy = QSignalSpy(picker.compare_chosen)
    for ref in (1, 2, 3, 4):
        _tick(picker, ref)
    assert spy.at(spy.count() - 1)[0] == [1, 2, 3, 4]
    _tick(picker, 5)
    assert picker._items[5].checkState(0) == Qt.CheckState.Unchecked
    assert not picker.note_label.isHidden()
    assert picker.checked_refs() == [1, 2, 3, 4]
    picker._items[2].setCheckState(0, Qt.CheckState.Unchecked)
    assert picker.note_label.isHidden()


def test_space_toggles_a_compare_row(qapp):
    picker = _picker(COMPARE)
    picker.show()
    picker.tree.setFocus()
    picker.tree.setCurrentItem(picker.tree.topLevelItem(0))
    QTest.keyClick(picker.tree, Qt.Key.Key_Space)
    assert picker.tree.topLevelItem(0).checkState(0) == Qt.CheckState.Checked


def test_top_four_sends_an_empty_set_and_unticks_everything(qapp):
    picker = _picker(COMPARE, current=[1, 2])
    assert picker.checked_refs() == [1, 2]
    spy = QSignalSpy(picker.compare_chosen)
    picker.top_button.click()
    assert spy.at(spy.count() - 1)[0] == []
    assert picker.checked_refs() == []


def test_there_is_no_clear_button_that_would_repeat_top_four(qapp):
    # An empty comparison is the top four, so "Clear" did the same thing.
    picker = _picker(COMPARE)
    assert not hasattr(picker, "clear_button")


def test_large_tree_builds_and_filters_within_budget(qapp):
    rows = _rows(n_top=10, fan=6, depth=4)  # about 2,600 rows
    assert len(rows) > 2500
    began = time.perf_counter()
    picker = _picker(rows=rows)
    build = time.perf_counter() - began
    began = time.perf_counter()
    picker.apply_filter("pump 3")
    filt = time.perf_counter() - began
    print(f"build {build * 1000:.0f} ms, filter {filt * 1000:.0f} ms, "
          f"{len(rows)} rows")
    assert build < 3.0
    assert filt < 0.5
    began = time.perf_counter()
    picker.apply_filter("")
    assert time.perf_counter() - began < 0.5


# -- on the Charts page ------------------------------------------------------

def test_the_page_applies_scope_and_compare_through_the_picker(qapp):
    from tests.unit.test_charts_page import _deep_hierarchy_inputs
    from raptor.gui.ui.charts_page import ChartsPage
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    scope = page.picker("scope")
    scope._on_activated(scope._items[101], 0)
    assert page._scope == 101
    assert page.follow_button.isChecked() is False
    assert "Pump house" in page.scope_button.text()
    compare = page.picker("compare")
    spy = QSignalSpy(page.compare_changed)
    compare._items[100].setCheckState(0, Qt.CheckState.Checked)
    compare._items[200].setCheckState(0, Qt.CheckState.Checked)
    assert page.selected_hierarchies() == [100, 200]
    assert spy.count() == 2
    assert "2 selected" in page.compare_button.text()
    compare.top_button.click()
    assert page.selected_hierarchies() == []
    # the picker is reused, not rebuilt, while the hierarchies are the same
    assert page.picker("compare") is compare


def test_a_few_hierarchies_get_a_short_list_and_many_get_the_scroll_cap(qapp):
    from raptor.gui.resources.design_tokens import px
    from raptor.gui.ui.hierarchy_tree_popup import TREE_MAX_HEIGHT
    few = _picker(COMPARE, rows=_rows(n_top=3, fan=1, depth=1))
    many = _picker(COMPARE, rows=_rows(n_top=10, fan=6, depth=3))
    assert few.tree.height() < px(TREE_MAX_HEIGHT) / 2
    assert many.tree.height() == px(TREE_MAX_HEIGHT)
    # Sized once per set of rows, not on each keystroke: no jumping popup.
    before = many.tree.height()
    many.apply_filter("unit")
    assert many.tree.height() == before

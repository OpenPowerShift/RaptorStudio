"""Data Tables: the Whole model switch (every level's rows, a Level column)."""
from __future__ import annotations

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "new_docs/models/DC2.rbds"      # Home holds no blocks; three levels do


@pytest.fixture
def win():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbds(MODEL), MODEL)
    w.resize(1400, 800)
    w.show()
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _level_col(t):
    return t.columnCount() - 1


def test_level_column_exists_only_in_whole_model(win):
    dt = win._data_tables
    t = dt._blocks_table
    assert t.isColumnHidden(_level_col(t))
    assert t.rowCount() == 0                       # Home has no blocks
    dt._whole_box.setChecked(True)
    assert not t.isColumnHidden(_level_col(t))
    assert t.rowCount() == 14
    levels = {t.item(r, _level_col(t)).text() for r in range(t.rowCount())}
    assert levels == {"IT_sub", "Power_sub", "Cooling_sub"}
    dt._whole_box.setChecked(False)
    assert t.rowCount() == 0 and t.isColumnHidden(_level_col(t))


def test_scope_label_names_the_whole_model(win):
    dt = win._data_tables
    dt._whole_box.setChecked(True)
    assert dt._scope_label.text() == (
        "· Whole model (14 blocks across 4 levels)")


def test_every_tab_follows_the_switch(win):
    dt = win._data_tables
    dt._whole_box.setChecked(True)
    for t in (dt._nodes_table, dt._events_table, dt._sparing_table):
        assert not t.isColumnHidden(_level_col(t))
    assert dt._nodes_table.rowCount() > 0
    assert dt._sparing_table.rowCount() == 14
    levels = {dt._nodes_table.item(r, _level_col(dt._nodes_table)).text()
              for r in range(dt._nodes_table.rowCount())}
    assert "Home" in levels and "IT_sub" in levels


def test_rows_of_other_levels_are_read_only(win):
    dt = win._data_tables
    dt._whole_box.setChecked(True)
    t = dt._blocks_table
    dt._realize_all(t)
    from PySide6.QtCore import Qt
    for r in range(t.rowCount()):            # none of these levels is open
        assert not (t.item(r, 0).flags() & Qt.ItemFlag.ItemIsEditable)
        assert not t.cellWidget(r, 2).isEnabled()
        assert "another level" in t.item(r, 0).toolTip()


def test_the_open_level_stays_editable(win):
    view = win._diagram_dock.view
    hid = next(iter(view._hier_items))
    view.enter_level(hid)
    QApplication.processEvents()
    dt = win._data_tables
    dt._whole_box.setChecked(True)
    t = dt._blocks_table
    dt._realize_all(t)
    live = set(view.blocks)
    assert live
    editable = {dt._row_key(t, r) for r in range(t.rowCount())
                if t.cellWidget(r, 2).isEnabled()}
    assert editable == live


def test_renaming_never_touches_a_row_of_another_level(win):
    dt = win._data_tables
    dt._whole_box.setChecked(True)
    t = dt._blocks_table
    before = {r: t.item(r, 0).text() for r in range(t.rowCount())}
    t.item(0, 0).setText("Renamed")          # must be ignored, not raise
    assert set(win._diagram_dock.view.blocks) == set()
    assert before[0] != "Renamed"


def test_the_level_column_can_be_filtered(win):
    dt = win._data_tables
    dt._whole_box.setChecked(True)
    t = dt._blocks_table
    bar = dt._filter_bars[t]
    assert bar.available()
    bar.set_shown(True)
    bar._editors[_level_col(t)].setText("Cool*")
    QTest.qWait(bar._timer.interval() + 80)
    shown, total = bar.rows_shown_of_total()
    assert total == 14 and 0 < shown < 14
    assert all(t.item(r, _level_col(t)).text() == "Cooling_sub"
               for r in range(t.rowCount()) if not t.isRowHidden(r))


def test_csv_export_has_the_level_column_only_in_whole_model(win):
    from raptor.gui.ui.table_export import table_to_grid
    dt = win._data_tables
    t = dt._blocks_table
    assert "Level" not in table_to_grid(t)[0]
    dt._whole_box.setChecked(True)
    headers, rows = table_to_grid(t)
    assert headers[-1] == "Level" and len(rows) == 14
    assert {r[-1] for r in rows} == {"IT_sub", "Power_sub", "Cooling_sub"}


def test_go_to_level_goes_by_id(win, monkeypatch):
    dt = win._data_tables
    dt._whole_box.setChecked(True)
    t = dt._blocks_table
    seen = []
    dt.element_locate_id_requested.connect(lambda k, i: seen.append((k, i)))
    pos = t.visualRect(t.model().index(0, 0)).center()
    menu = dt._row_menu(t, "block", pos)         # (a real exec() would block)
    assert menu.actions()[0].text().startswith("Go to level: ")
    menu.actions()[0].trigger()
    assert seen and seen[0][0] == "block"
    assert win._diagram_dock.view.locate_id(*seen[0])      # switches level
    assert win._diagram_dock.view.blocks


def test_the_bar_buttons_have_spacing(win):
    bar = win._data_tables.layout().itemAt(0).layout()
    assert bar.spacing() > 0

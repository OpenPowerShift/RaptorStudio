"""Data Tables: the Costs page (one row per block, 14 cost columns).

Every cost cell commits the WHOLE row through `DiagramView.set_block_costs`,
so the block and its BLOCK_DATA row move together and one edit is one undo
step. Values follow the Inspector's range and three-decimal precision;
non-finite/out-of-range text reverts. A Pooled block's spare and emergency
shipping costs are read-only, as in the Inspector.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Capacity1/input.rbd"      # Infinite, Custom and Pooled
WHOLE = "new_docs/models/DC2.rbds"              # Home holds no blocks


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, monkeypatch):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import dialogs, settings
    from raptor.gui.ui.main_window import MainWindow

    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    w._asked = []
    monkeypatch.setattr(
        dialogs, "ask_apply_to_rows",
        lambda parent, n: (w._asked.append(n) or (True, False)))
    yield w
    w._dirty = False
    w.close()


def _cost_table(win):
    dt = win._data_tables
    t = dt._costs_table
    dt._tabs.setCurrentWidget(t)
    return dt, t, win._diagram_dock.view


def _bid(t, row):
    return int(t.item(row, 1).text())


def _row_of(t, bid):
    return next(r for r in range(t.rowCount()) if _bid(t, r) == bid)


def _col(name):
    from raptor.gui.model_edit import BLOCK_COST_FIELDS
    return 2 + [c for _a, c in BLOCK_COST_FIELDS].index(name)


def _select(table, rows):
    sm = table.selectionModel()
    sm.clearSelection()
    for r in rows:
        sm.select(table.model().index(r, 0),
                  QItemSelectionModel.SelectionFlag.Select
                  | QItemSelectionModel.SelectionFlag.Rows)


def _cur(table, row, col):
    table.selectionModel().setCurrentIndex(
        table.model().index(row, col),
        QItemSelectionModel.SelectionFlag.NoUpdate)


def _block_row(rbd, bid):
    return next(r for r in rbd.sections["BLOCK_DATA"].rows
                if int(float(r["ID"])) == bid)


def test_costs_page_has_the_block_and_cost_columns_and_every_block(win):
    from raptor.gui.model_edit import BLOCK_COST_FIELDS

    dt, t, view = _cost_table(win)
    assert dt._tabs.tabText(dt._tabs.indexOf(t)) == "Costs"
    headers = [t.horizontalHeaderItem(c).text()
               for c in range(t.columnCount())]
    assert headers == (["Name", "ID"]
                       + [c for _a, c in BLOCK_COST_FIELDS] + ["Level"])
    assert t.rowCount() == len(view.blocks)
    assert {_bid(t, r) for r in range(t.rowCount())} == set(view.blocks)
    assert t.isColumnHidden(t.columnCount() - 1)   # not Whole model
    for r in range(t.rowCount()):
        block = view.blocks[_bid(t, r)]
        for (attr, _c), col in zip(BLOCK_COST_FIELDS, range(2, 16)):
            assert t.item(r, col).text() == f"{getattr(block, attr):.3f}"


def test_a_cell_commit_updates_block_and_file_in_one_undo_step(win, qapp):
    _dt, t, view = _cost_table(win)
    row = 0
    bid = _bid(t, row)
    col = _col("Running")
    old = view.blocks[bid].operating_cost
    depth = view._stack.count()

    t.item(row, col).setText("12.5")
    qapp.processEvents()

    assert view.blocks[bid].operating_cost == pytest.approx(12.5)
    assert float(_block_row(view._rbd_ref, bid)["Running"]) == pytest.approx(
        12.5)
    assert t.item(row, col).text() == "12.500"
    assert view._stack.count() == depth + 1
    assert win._dirty

    view._stack.undo()
    assert view.blocks[bid].operating_cost == pytest.approx(old)
    assert float(_block_row(view._rbd_ref, bid)["Running"]) == pytest.approx(
        old)


@pytest.mark.parametrize("text", ["nan", "inf", "-inf", "1e13", "-1e13",
                                  "not a number", ""])
def test_invalid_cost_text_reverts(win, text):
    _dt, t, view = _cost_table(win)
    row = 0
    bid = _bid(t, row)
    col = _col("initial")
    item = t.item(row, col)
    old = view.blocks[bid].initial_cost
    depth = view._stack.count()

    item.setText(text)

    assert t.item(row, col).text() == f"{old:.3f}"
    assert view.blocks[bid].initial_cost == pytest.approx(old)
    assert view._stack.count() == depth
    assert float(_block_row(view._rbd_ref, bid)["initial"]) == pytest.approx(old)


def test_pooled_blocks_spare_costs_are_read_only(win):
    from raptor.core.model import SparingType

    dt, t, view = _cost_table(win)
    pooled = [b for b in view.blocks.values()
              if b.sparing_type == SparingType.SPARE_POOL]
    assert pooled
    for block in pooled:
        row = _row_of(t, block.id)
        for name, before in (("perSpare", block.spare_cost),
                             ("emerShipping", block.emergency_shipping_cost)):
            item = t.item(row, _col(name))
            assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)
            assert item.toolTip() == dt._POOLED_COST_TIP
            item.setText("99")           # even programmatically refused
            assert getattr(block, "spare_cost" if name == "perSpare"
                           else "emergency_shipping_cost") == pytest.approx(
                               before)
            assert t.item(row, _col(name)).text() == f"{before:.3f}"

    normal = next(b for b in view.blocks.values()
                  if b.sparing_type != SparingType.SPARE_POOL)
    row = _row_of(t, normal.id)
    for name in ("perSpare", "emerShipping"):
        assert t.item(row, _col(name)).flags() & Qt.ItemFlag.ItemIsEditable


def test_paste_fills_the_selected_rows_in_one_undo_step(win, qapp):
    dt, t, view = _cost_table(win)
    _select(t, [0, 1, 2])
    ids = [_bid(t, r) for r in range(3)]
    col = _col("Running")
    _cur(t, 0, col)
    before_running = {i: view.blocks[i].operating_cost for i in ids}
    before_initial = {i: view.blocks[i].initial_cost for i in ids}
    depth = view._stack.count()

    assert dt.paste_into_selection("7.5")
    qapp.processEvents()

    assert all(view.blocks[i].operating_cost == pytest.approx(7.5)
               for i in ids)
    # The other 13 values of every target row stay as they were: only the
    # edited column is set.
    assert all(view.blocks[i].initial_cost == pytest.approx(before_initial[i])
               for i in ids)
    assert view._stack.count() == depth + 1
    assert win._asked == [3]

    view._stack.undo()
    assert all(view.blocks[i].operating_cost == pytest.approx(
        before_running[i]) for i in ids)


def test_ctrl_d_fills_down_across_the_selected_rows(win):
    dt, t, view = _cost_table(win)
    col = _col("Running")
    _select(t, [0])
    t.item(0, col).setText("8.5")
    assert view.blocks[_bid(t, 0)].operating_cost == pytest.approx(8.5)

    _select(t, [0, 1, 2])
    _cur(t, 0, col)
    assert dt.fill_down()
    assert all(view.blocks[_bid(t, r)].operating_cost == pytest.approx(8.5)
               for r in range(3))


def test_whole_model_cost_rows_are_read_only(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow

    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbds(WHOLE), WHOLE)
    w.show()
    qapp.processEvents()
    dt = w._data_tables
    dt._tabs.setCurrentWidget(dt._costs_table)
    dt._whole_box.setChecked(True)
    qapp.processEvents()
    t = dt._costs_table

    assert t.rowCount() == 14
    assert not t.isColumnHidden(t.columnCount() - 1)
    for r in range(t.rowCount()):
        for c in range(2, 16):
            assert not (t.item(r, c).flags() & Qt.ItemFlag.ItemIsEditable)
        before = t.item(r, _col("Running")).text()
        t.item(r, _col("Running")).setText("99")
        assert t.item(r, _col("Running")).text() == before
    w._dirty = False
    w.close()


def test_csv_filter_and_sort_use_the_displayed_rows(win, qapp):
    from raptor.gui.ui.table_export import table_to_grid

    dt, t, view = _cost_table(win)
    headers, rows = table_to_grid(t)
    assert headers == [t.horizontalHeaderItem(c).text()
                       for c in range(t.columnCount())
                       if not t.isColumnHidden(c)]
    assert len(rows) == t.rowCount() == len(view.blocks)
    assert float(rows[0][_col("Running")]) == pytest.approx(
        view.blocks[_bid(t, 0)].operating_cost)

    bar = dt._filter_bars[t]
    assert bar.available()
    bar.set_shown(True)
    col = _col("Running")
    bar._editors[col].setText(">200")
    QTest.qWait(bar._timer.interval() + 80)
    shown, total = bar.rows_shown_of_total()
    expected = sum(1 for b in view.blocks.values() if b.operating_cost > 200)
    assert (shown, total) == (expected, len(view.blocks))
    bar.clear()

    t.sortItems(col, Qt.SortOrder.DescendingOrder)
    qapp.processEvents()
    values = [float(t.item(r, col).text()) for r in range(t.rowCount())]
    assert values == sorted(values, reverse=True)

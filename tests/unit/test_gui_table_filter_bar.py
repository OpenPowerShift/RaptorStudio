"""The filter row under big tables: debounce, N of M, small tables, repopulate,
the results-table hook and the Data Tables (filtering the model, not widgets)."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

from raptor.gui.ui.table_filter_bar import MIN_FILTER_ROWS, FilterBar

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _table(n: int, cols: int = 3) -> QTableWidget:
    t = QTableWidget(n, cols)
    t.setHorizontalHeaderLabels([f"C{c}" for c in range(cols)])
    for r in range(n):
        t.setItem(r, 0, QTableWidgetItem(f"Pump_{r}"))
        for c in range(1, cols):
            it = QTableWidgetItem(f"{(r * 7 % 100) / 100:.2f}")
            it.setData(Qt.ItemDataRole.UserRole + 1, (r * 7 % 100) / 100)
            t.setItem(r, c, it)
    t.resize(500, 300)
    t.show()
    QApplication.processEvents()
    return t


def _wait(bar: FilterBar) -> None:
    QTest.qWait(bar._timer.interval() + 60)


def test_small_tables_get_no_filter():
    t = _table(MIN_FILTER_ROWS)
    bar = FilterBar.attach(t)
    assert not bar.available()
    bar.set_shown(True)
    assert not bar.is_shown()
    assert FilterBar.attach(_table(MIN_FILTER_ROWS + 1)).available()


def test_typing_is_debounced_to_one_pass():
    t = _table(200)
    bar = FilterBar.attach(t, raw_role=Qt.ItemDataRole.UserRole + 1)
    bar.set_shown(True)
    assert bar._editors and bar._passes == 0
    for ch in "Pump_1":
        bar._editors[0].setText(bar._editors[0].text() + ch)
        QTest.qWait(10)
    assert bar._passes == 0                     # still typing
    _wait(bar)
    assert bar._passes == 1
    shown, total = bar.rows_shown_of_total()
    assert total == 200 and shown == 111        # Pump_1, Pump_10-19, Pump_100-199


def test_numeric_and_count_label_and_clear():
    t = _table(200)
    bar = FilterBar.attach(t, raw_role=Qt.ItemDataRole.UserRole + 1)
    bar.set_shown(True)
    bar._editors[1].setText(">0.5")
    _wait(bar)
    shown, total = bar.rows_shown_of_total()
    assert 0 < shown < total
    assert bar._badge.isVisible() and f"{shown:,} of {total:,}" in bar._count.text()
    bar.clear()
    assert bar.rows_shown_of_total() == (200, 200)
    assert not bar._badge.isVisible()


def test_bad_expression_leaves_rows_and_flags_the_box():
    t = _table(50)
    bar = FilterBar.attach(t)
    bar.set_shown(True)
    bar._editors[1].setText(">abc")
    _wait(bar)
    assert bar.rows_shown_of_total() == (50, 50)
    assert "border" in bar._editors[1].styleSheet()


def test_hiding_the_strip_clears_the_filter():
    t = _table(50)
    bar = FilterBar.attach(t)
    bar.set_shown(True)
    bar._editors[0].setText("Pump_4*")
    _wait(bar)
    assert bar.rows_shown_of_total()[0] < 50
    bar.set_shown(False)
    assert bar.rows_shown_of_total() == (50, 50)


def test_filter_survives_a_repopulate_and_a_sort():
    t = _table(60)
    bar = FilterBar.attach(t)
    bar.set_shown(True)
    bar._editors[0].setText("Pump_5*")
    _wait(bar)
    first = bar.rows_shown_of_total()[0]
    t.sortItems(0, Qt.SortOrder.DescendingOrder)
    QTest.qWait(60)
    assert bar.rows_shown_of_total()[0] == first
    assert all(t.item(r, 0).text().startswith("Pump_5")
               for r in bar.visible_rows())
    t.setRowCount(0)
    t.setRowCount(70)
    for r in range(70):
        t.setItem(r, 0, QTableWidgetItem(f"Pump_{r}"))
    QTest.qWait(60)
    assert all(t.item(r, 0).text().startswith("Pump_5")
               for r in bar.visible_rows())


def test_a_table_that_shrinks_below_the_threshold_closes_the_strip():
    t = _table(40)
    bar = FilterBar.attach(t)
    bar.set_shown(True)
    t.setRowCount(5)
    QTest.qWait(30)
    assert not bar.is_shown()


def test_filtering_30000_rows_is_fast():
    t = _table(30000, cols=4)
    bar = FilterBar.attach(t, raw_role=Qt.ItemDataRole.UserRole + 1)
    bar.set_shown(True)
    bar._editors[0].setText("Pump_1*")
    bar._editors[2].setText(">0.3 <0.9")
    t0 = time.perf_counter()
    bar.apply()
    assert time.perf_counter() - t0 < 2.0      # ~0.1 s; generous for slow CI
    shown, total = bar.rows_shown_of_total()
    assert 0 < shown < total


def test_results_tables_get_a_bar_only_when_long(monkeypatch):
    from raptor.gui.ui.results import ResultsDock
    make = ResultsDock._make_table
    owner = ResultsDock.__new__(ResultsDock)
    small = make(owner, ["A", "B"], [[f"r{i}", str(i)] for i in range(MIN_FILTER_ROWS)])
    big = make(owner, ["A", "B"], [[f"r{i}", (f"{i:.2f}", float(i))] for i in range(60)])
    assert getattr(small, "_filter_bar", None) is None
    bar = big._filter_bar
    bar.set_shown(True)
    bar._editors[1].setText(">=50")
    _wait(bar)
    assert bar.rows_shown_of_total() == (10, 60)
    # the table still shows every row it had in full once the strip closes,
    # and the fixed-height table grew by the strip and shrank back
    h = big.maximumHeight()
    bar.set_shown(False)
    assert big.maximumHeight() == h - 24
    assert bar.rows_shown_of_total() == (60, 60)


# ---------------------------------------------------------------- Data Tables
N = 150


def _model(n: int = N):
    from raptor.api.build import build_from_spec
    blocks = [{"name": f"B{i:03d}"} for i in range(n)]
    links = ([{"from": "Start", "to": "B000"}]
             + [{"from": f"B{i:03d}", "to": f"B{i + 1:03d}"} for i in range(n - 1)]
             + [{"from": f"B{n - 1:03d}", "to": "End"}])
    return build_from_spec({
        "name": "Big", "blocks": blocks,
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": links, "system": {"term_condition": 100, "num_runs": 1}},
        auto_layout=True)


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1500, 900)
    w.show()
    w._load_into_app(_model(), None)
    QApplication.processEvents()
    w._data_tables.refresh(force=True)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _built(t, col=2):
    return [r for r in range(t.rowCount()) if t.cellWidget(r, col) is not None]


def test_data_tables_filter_reads_the_model_not_the_widgets(win):
    dock = win._data_tables
    t = dock._blocks_table
    bar = dock._filter_bars[t]
    before = len(_built(t))
    assert before < N
    bar.set_shown(True)
    bar._editors[0].setText("B1*")             # B100..B149 + B1xx names
    _wait(bar)
    shown, total = bar.rows_shown_of_total()
    assert total == N and shown == 50
    # The matching rows that are in view got widgets; the far ones did not.
    far = [r for r in bar.visible_rows() if r > N - 5]
    assert far and all(t.cellWidget(r, 2) is None for r in far)
    # widget columns filter without widgets: every row shares one failure dist
    from raptor.gui.ui.distro_combo import distro_display_name
    name = distro_display_name(
        dock._view.blocks[1].fail_distro, is_failure=True)
    bar._editors[0].setText("")
    bar._editors[2].setText(name)
    _wait(bar)
    assert bar.rows_shown_of_total()[0] == N
    bar._editors[2].setText("!" + name)
    _wait(bar)
    assert bar.rows_shown_of_total()[0] == 0


def test_data_tables_filter_on_a_parameter_value(win):
    dock = win._data_tables
    t = dock._blocks_table
    view = dock._view
    # make two blocks distinct
    first = next(iter(sorted(view.blocks)))
    view.blocks[first].fail_values[0] = 12345.0
    dock.refresh(force=True)
    QApplication.processEvents()
    bar = dock._filter_bars[t]
    bar.set_shown(True)
    bar._editors[3].setText("12345")
    _wait(bar)
    assert bar.rows_shown_of_total()[0] == 1


def test_sorting_still_works_while_filtered(win):
    dock = win._data_tables
    t = dock._blocks_table
    bar = dock._filter_bars[t]
    bar.set_shown(True)
    bar._editors[0].setText("B0*")
    _wait(bar)
    n = bar.rows_shown_of_total()[0]
    t.sortItems(0, Qt.SortOrder.DescendingOrder)
    QTest.qWait(80)
    rows = bar.visible_rows()
    assert len(rows) == n
    names = [t.item(r, 0).text() for r in rows]
    assert names == sorted(names, reverse=True)


def test_export_reads_only_the_visible_rows(win, monkeypatch, tmp_path):
    from raptor.gui.ui import data_tables as dt
    dock = win._data_tables
    t = dock._blocks_table
    dock._tabs.setCurrentWidget(t)
    bar = dock._filter_bars[t]
    bar.set_shown(True)
    bar._editors[0].setText("B07?")
    _wait(bar)
    out = tmp_path / "blocks.csv"
    titles = []
    monkeypatch.setattr(dt.fdg, "getSaveFileName",
                        staticmethod(lambda p, title, *a, **k:
                                     (titles.append(title) or str(out), "")))
    dock._export_current_table_csv()
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 1 + 10                # header + B070..B079
    assert "10 of 150" in titles[0]
    # hidden rows never had to be built for it
    assert t.cellWidget(N - 1, 2) is None


def test_an_edited_row_that_stops_matching_stays_until_the_next_pass(win):
    dock = win._data_tables
    t = dock._blocks_table
    bar = dock._filter_bars[t]
    bar.set_shown(True)
    bar._editors[3].setText("<=100")
    _wait(bar)
    shown0 = bar.rows_shown_of_total()[0]
    assert shown0 > 0
    row = bar.visible_rows()[0]
    t.setCurrentCell(row, 0)
    t.selectRow(row)
    bid = dock._row_key(t, row)
    dock._view.blocks[bid].fail_values[0] = 99999.0     # no longer <=100
    dock.refresh(force=True)
    QTest.qWait(60)
    keys = {dock._row_key(t, r) for r in bar.visible_rows()}
    assert bid in keys                          # kept under the cursor
    bar._editors[3].setText("<=100 ")           # the user types again
    _wait(bar)
    keys = {dock._row_key(t, r) for r in bar.visible_rows()}
    assert bid not in keys


def test_small_data_tables_have_no_strip(win):
    dock = win._data_tables
    bar = dock._filter_bars[dock._nodes_table]      # Start + End only
    assert not bar.available()

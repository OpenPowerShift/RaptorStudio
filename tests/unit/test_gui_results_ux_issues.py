"""Focused regressions for the Final Results UX issues.

#199 Y-axis minimum, #205 per-table Copy/CSV actions, #208 click-to-sort
with filters, and #211 the Weak Link row context menu.
"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import (
    QApplication, QInputDialog, QMenu, QPushButton, QScrollArea,
    QTableWidget, QVBoxLayout, QWidget)

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui import filedialogs as fdg
from raptor.gui.ui.results import RAW_VALUE_ROLE, ResultsDock

REPO = Path(__file__).resolve().parents[2]
GOLD = REPO / "tests" / "golden"

#: Two blocks with a deliberately mixed ranking, enough for the Importance
#: default-order check without another simulation.
IMPORTANCE_SAMPLE = {
    "mode": "both", "runs": 10, "batches": 2, "blocks": 3,
    "metrics": {"Availability": {
        "baseline": 0.9,
        "rows": [
            {"block": "A", "block_id": 1, "birnbaum": 0.02, "raw": 1.5,
             "rrw": 1.1, "vesely_fussell": 0.2, "perfect": 0.92,
             "failed": 0.90, "sem": 0.001},
            {"block": "B", "block_id": 2, "birnbaum": 0.30, "raw": 4.0,
             "rrw": 1.4, "vesely_fussell": 0.5, "perfect": 0.95,
             "failed": 0.65, "sem": 0.002},
            {"block": "C", "block_id": 3, "birnbaum": None, "raw": None,
             "rrw": None, "vesely_fussell": None, "perfect": 0.90,
             "failed": 0.90, "sem": 0.003},
        ]}},
    "notes": [],
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def rich_dock(qapp):
    """One real run with capacity, cost and weak-link data."""
    rbd = load_rbd(str(GOLD / "Capacity1" / "input.rbd"))
    row = rbd.sections["SYSTEM_DATA"].rows[0]
    row["weakAnalysis"] = "1"
    row["costAnalysis"] = "1"
    row["capacityAnalysis"] = "1"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    yield dock
    dock.deleteLater()


def _page_body(page: QWidget) -> QWidget:
    return page.widget() if isinstance(page, QScrollArea) else page


def _table_buttons(page: QWidget) -> list[QPushButton]:
    body = _page_body(page)
    return [b for b in body.findChildren(QPushButton)
            if b.accessibleName().startswith(("Copy ", "Save "))]


def _demo_table(dock: ResultsDock, n: int = 30):
    """A table with a real filter bar and full-precision raw values."""
    rows = [[f"B{i:02d}", (f"{i / 100:.2f}", i / 100)] for i in range(n)]
    table = dock._make_table(["Name", "Value"], rows)
    page = QWidget()
    lay = QVBoxLayout(page)
    dock._add_table_header(lay, "Demo", table)
    lay.addWidget(table)
    page.show()
    return page, table


# --------------------------------------------------------------- #199 Y-min

@pytest.mark.parametrize("mode,enabled", [
    (0, True),   # Per Trial Ao
    (1, True),   # Interval Ao
    (2, True),   # Multi-run Ao
    (3, False),  # Capacity (Flow)
    (4, False),  # Multi-run Capacity (Flow)
    (5, False),  # Availability Convergence
])
def test_y_axis_control_is_honest_for_every_graph_mode(qapp, mode, enabled):
    dock = ResultsDock()
    dock._graph_mode.setCurrentIndex(mode)
    assert dock._y_axis_btn.isEnabled() is enabled
    tip = dock._y_axis_btn.toolTip()
    if enabled:
        assert "Auto" in dock._y_axis_btn.text()
        assert "graphYmin" in tip and "0.999999" in tip
    else:
        assert dock._y_axis_btn.text() == "Y-axis min: n/a"
        assert ("capacity" in tip.lower()
                or "convergence" in tip.lower())


def test_y_axis_dialog_round_trip_and_range(qapp, monkeypatch):
    dock = ResultsDock()
    calls: dict[str, object] = {}

    def fake_get_double(parent, title, label, value, minimum, maximum,
                        decimals):
        calls.update(title=title, minimum=minimum, maximum=maximum,
                     decimals=decimals)
        return 0.25, True

    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(fake_get_double))
    seen: list[float] = []
    dock.y_axis_min_changed.connect(seen.append)
    dock._y_axis_btn.click()

    assert calls == {"title": "Set Y-Axis", "minimum": 0.0,
                     "maximum": 0.999999, "decimals": 6}
    assert dock._ao_plot.y_min == 0.25
    assert seen == [0.25]
    assert dock._y_axis_btn.text() == "Y-axis min: 0.2500"


def test_y_axis_dialog_cancel_changes_nothing(qapp, monkeypatch):
    dock = ResultsDock()
    dock.set_y_axis_min(0.4)
    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (0.8, False)))
    seen: list[float] = []
    dock.y_axis_min_changed.connect(seen.append)
    dock._y_axis_btn.click()
    assert dock._ao_plot.y_min == 0.4 and seen == []


def test_multi_run_manual_floor_is_not_masked_by_auto_fit(qapp):
    from raptor.gui.ui.plots import AoPlotWidget
    plot = AoPlotWidget()
    plot.set_data([
        {"t": [0, 1], "ao": [0.9991, 0.9992], "g": [1, 1],
         "y": [0, 0], "r": [0, 0], "iv": [None, None]},
        {"t": [0, 1], "ao": [0.9994, 0.9995], "g": [1, 1],
         "y": [0, 0], "r": [0, 0], "iv": [None, None]},
    ])
    plot.set_mode(AoPlotWidget.MULTI_RUN)
    low, high = plot._y_bounds()
    assert 0.9990 < low < 0.9992 and 0.9994 < high < 1.0
    plot.set_y_min(0.5)
    assert plot._y_bounds() == (0.5, 1.0)
    plot.set_y_min(0.999)
    assert plot._y_bounds() == (0.999, 1.0)
    plot.set_y_min(0.0)
    assert plot._y_bounds()[0] < 0.9992       # back to Auto


# -------------------------------------------------------- #205 table actions

def test_no_global_copy_export_header_row_remains(rich_dock):
    texts = [b.text() for b in rich_dock.findChildren(QPushButton)]
    assert "Copy Table" not in texts
    assert "Export CSV…" not in texts
    assert "Export Tables…" not in texts


def test_every_results_table_card_has_a_copy_csv_pair(rich_dock):
    checked = 0
    for i in range(rich_dock._details_tabs.count()):
        page = rich_dock._details_tabs.widget(i)
        body = _page_body(page)
        tables = [t for t in body.findChildren(QTableWidget)
                  if t.rowCount() > 0]
        if not tables:
            continue
        buttons = _table_buttons(page)
        assert len(buttons) == 2 * len(tables), (
            rich_dock._details_tabs.tabText(i))
        for button in buttons:
            assert button.text() == ""
            assert button.accessibleName()
            assert button.toolTip()
            assert not button.icon().isNull()
        checked += 1
    assert checked >= 4, "expected Summary/Logistics/Sparing/Capacity/Cost/Weak"


def test_importance_table_has_per_table_copy_csv(qapp):
    dock = ResultsDock()
    dock.set_results([], "", "", report_data=[], importance=IMPORTANCE_SAMPLE)
    index = next(i for i in range(dock._details_tabs.count())
                 if dock._details_tabs.tabText(i) == "Importance")
    page = dock._details_tabs.widget(index)
    assert len(_table_buttons(page)) == 2
    buttons = _table_buttons(page)
    assert all(not b.icon().isNull() for b in buttons)
    dock.deleteLater()


def test_per_table_copy_and_csv_respect_filter_sort_and_precision(
        qapp, monkeypatch, tmp_path):
    dock = ResultsDock()
    page, table = _demo_table(dock, 30)
    bar = table._filter_bar
    bar.set_shown(True)
    bar._editors[0].setText("B1")
    bar._timer.stop()
    bar.apply()
    dock._apply_sort(table, "DEMO", (1, Qt.SortOrder.DescendingOrder))
    bar.apply()       # sorting changes the model layout; re-hide the misses
    visible = bar.visible_rows()
    assert 0 < len(visible) < 30
    raw_order = [table.item(r, 1).data(RAW_VALUE_ROLE) for r in visible]
    assert raw_order == sorted(raw_order, reverse=True)

    copy = next(b for b in page.findChildren(QPushButton)
                if b.accessibleName() == "Copy Demo table")
    copy.click()
    lines = qapp.clipboard().text().splitlines()
    assert lines[0] == "Name\tValue"
    assert [ln.split("\t")[0] for ln in lines[1:]] == [
        table.item(r, 0).text() for r in visible]
    assert str(raw_order[0]) in lines[1]

    target = tmp_path / "demo.csv"
    monkeypatch.setattr(
        fdg, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "CSV (*.csv)")))
    save = next(b for b in page.findChildren(QPushButton)
                if b.accessibleName() == "Save Demo table as CSV")
    save.click()
    parsed = list(csv.reader(io.StringIO(target.read_text(encoding="utf-8"))))
    assert parsed[0] == ["Name", "Value"]
    assert [row[0] for row in parsed[1:]] == [
        table.item(r, 0).text() for r in visible]
    assert parsed[1][1] == f"{raw_order[0]:.17g}" or \
        float(parsed[1][1]) == pytest.approx(raw_order[0])
    page.deleteLater()
    dock.deleteLater()


# ------------------------------------------------------------- #208 sorting

def test_numeric_sort_uses_raw_value_and_keeps_bad_values_last(qapp):
    dock = ResultsDock()
    table = dock._make_table(
        ["Name", "Value"],
        [["a", ("0.10", 0.2)], ["b", ("0.10", 0.1)],
         ["c", ("N/A", None)], ["d", ("—", None)]])
    for i, order in enumerate((Qt.SortOrder.AscendingOrder,
                               Qt.SortOrder.DescendingOrder)):
        dock._apply_sort(table, f"NUMERIC-{i}", (1, order))
        names = [table.item(r, 0).text() for r in range(table.rowCount())]
        assert names[-2:] == ["c", "d"]      # N/A and — always last
    dock._apply_sort(table, "NUMERIC-ASC", (1, Qt.SortOrder.AscendingOrder))
    assert [table.item(r, 0).text() for r in range(2)] == ["b", "a"]
    dock._apply_sort(table, "NUMERIC-DESC", (1, Qt.SortOrder.DescendingOrder))
    assert [table.item(r, 0).text() for r in range(2)] == ["a", "b"]


def test_sort_uses_full_unelided_name(qapp):
    dock = ResultsDock()
    one = "Pump_Alpha_Station_Number_One"
    two = "Pump_Alpha_Station_Number_Two"
    rows = [[dock._elide(two), (1, 1)], [dock._elide(one), (2, 2)]]
    table = dock._make_table(["Name", "X"], rows)
    table.item(0, 0).setData(Qt.ItemDataRole.UserRole, two)
    table.item(1, 0).setData(Qt.ItemDataRole.UserRole, one)
    dock._apply_sort(table, "NAMES", (0, Qt.SortOrder.AscendingOrder))
    assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) == one
    assert table.item(1, 0).data(Qt.ItemDataRole.UserRole) == two


@pytest.mark.parametrize("title,column,order", [
    ("SUMMARY", -1, None),
    ("LOGISTICS", -1, None),
    ("SPARING", 0, Qt.SortOrder.AscendingOrder),
])
def test_section_default_order(rich_dock, title, column, order):
    table = rich_dock.section_table(title)
    if table is None:
        pytest.skip(f"{title} not present in this fixture")
    header = table.horizontalHeader()
    assert header.sortIndicatorSection() == column
    if order is not None:
        assert header.sortIndicatorOrder() == order


def test_capacity_cost_and_weak_default_orders(rich_dock):
    cap = rich_dock._cap_table
    assert cap.horizontalHeader().sortIndicatorSection() == 3
    assert cap.horizontalHeader().sortIndicatorOrder() == \
        Qt.SortOrder.DescendingOrder
    flows = [cap.item(r, 3).data(RAW_VALUE_ROLE)
             for r in range(cap.rowCount())]
    assert flows == sorted(flows, reverse=True)

    cost = rich_dock._cost_tables[0]
    assert cost.horizontalHeader().sortIndicatorSection() == 5
    assert cost.horizontalHeader().sortIndicatorOrder() == \
        Qt.SortOrder.DescendingOrder
    totals = [cost.item(r, 5).data(RAW_VALUE_ROLE)
              for r in range(cost.rowCount())]
    assert totals == sorted(totals, reverse=True)

    weak_index = next(i for i in range(rich_dock._details_tabs.count())
                      if rich_dock._details_tabs.tabText(i) == "Weak Link")
    weak_page = rich_dock._details_tabs.widget(weak_index)
    blocks = _page_body(weak_page).findChildren(QTableWidget)[0]
    assert blocks.horizontalHeader().sortIndicatorSection() == 2
    assert blocks.horizontalHeader().sortIndicatorOrder() == \
        Qt.SortOrder.DescendingOrder
    values = [blocks.item(r, 2).data(RAW_VALUE_ROLE)
              for r in range(blocks.rowCount())]
    assert values == sorted(values, reverse=True)


def test_importance_defaults_to_birnbaum_descending(qapp):
    dock = ResultsDock()
    dock.set_results([], "", "", report_data=[], importance=IMPORTANCE_SAMPLE)
    index = next(i for i in range(dock._details_tabs.count())
                 if dock._details_tabs.tabText(i) == "Importance")
    table = _page_body(dock._details_tabs.widget(index)).findChildren(
        QTableWidget)[0]
    assert table.horizontalHeader().sortIndicatorSection() == 1
    assert table.horizontalHeader().sortIndicatorOrder() == \
        Qt.SortOrder.DescendingOrder
    values = [table.item(r, 1).data(RAW_VALUE_ROLE)
              for r in range(table.rowCount())]
    assert values[:-1] == sorted(values[:-1], reverse=True)
    assert values[-1] is None       # Birnbaum undefined sorts last
    dock.deleteLater()


def test_sort_survives_a_filter_pass(qapp):
    dock = ResultsDock()
    _page, table = _demo_table(dock, 30)
    dock._apply_sort(table, "FILTERED", (1, Qt.SortOrder.DescendingOrder))
    header = table.horizontalHeader()
    before = (header.sortIndicatorSection(), header.sortIndicatorOrder())
    bar = table._filter_bar
    bar.set_shown(True)
    bar._editors[0].setText("B1")
    bar._timer.stop()
    bar.apply()
    assert (header.sortIndicatorSection(),
            header.sortIndicatorOrder()) == before
    visible = bar.visible_rows()
    assert [table.item(r, 1).data(RAW_VALUE_ROLE) for r in visible] == \
        sorted((table.item(r, 1).data(RAW_VALUE_ROLE) for r in visible),
               reverse=True)
    bar.clear()
    assert (header.sortIndicatorSection(),
            header.sortIndicatorOrder()) == before


def test_sort_indicator_survives_a_results_rebuild(qapp):
    dock = ResultsDock()
    lines = [
        "Final_Results_Report for x.rbd  fixed",
        "***SUMMARY***",
        "                         Minimum         Mean            Maximum         St.Dev          SEM",
        "Availability             0.746514202     0.752532575     0.758550947     0.008511264     0.006018372",
        "MTBDE                    100.0           110.0           120.0           1.0             0.1",
    ]
    dock._populate_details(lines)
    table = dock.section_table("SUMMARY")
    table.sortItems(0, Qt.SortOrder.AscendingOrder)  # the user's click
    assert dock._sort_states["SECTION::SUMMARY"] == (
        0, Qt.SortOrder.AscendingOrder)
    dock._populate_details(lines)
    rebuilt = dock.section_table("SUMMARY")
    assert rebuilt.horizontalHeader().sortIndicatorSection() == 0
    assert rebuilt.horizontalHeader().sortIndicatorOrder() == \
        Qt.SortOrder.AscendingOrder
    assert rebuilt.item(0, 0).text() == "Availability"


# --------------------------------------------------------- #211 Weak menus

def test_weak_link_context_menu_selects_row_and_emits_element(rich_dock):
    index = next(i for i in range(rich_dock._details_tabs.count())
                 if rich_dock._details_tabs.tabText(i) == "Weak Link")
    page = rich_dock._details_tabs.widget(index)
    page.show()
    QApplication.processEvents()
    tables = [t for t in _page_body(page).findChildren(QTableWidget)
              if t.rowCount() > 1]
    assert tables
    table = tables[0]
    kind = "block"
    row = 1
    point = table.visualItemRect(table.item(row, 0)).center()
    seen: list[tuple[str, str]] = []
    rich_dock.element_clicked.connect(
        lambda k, n: seen.append((k, n)))
    menu = rich_dock._goto_menu_at(table, kind, point)
    assert isinstance(menu, QMenu)
    assert table.currentRow() == row
    action = next(a for a in menu.actions()
                  if a.text() == "Go to selected element")
    action.trigger()
    name = table.item(row, 0).data(Qt.ItemDataRole.UserRole) or \
        table.item(row, 0).text()
    assert seen[-1] == (kind, name)


def test_weak_link_context_menu_uses_the_right_kind_per_table(rich_dock):
    index = next(i for i in range(rich_dock._details_tabs.count())
                 if rich_dock._details_tabs.tabText(i) == "Weak Link")
    page = rich_dock._details_tabs.widget(index)
    page.show()
    QApplication.processEvents()
    tables = [t for t in _page_body(page).findChildren(QTableWidget)
              if t.rowCount() > 1]
    kinds = ["block"] + ["node"] * (len(tables) - 1)
    seen: list[tuple[str, str]] = []
    rich_dock.element_clicked.connect(lambda k, n: seen.append((k, n)))
    for table, kind in zip(tables, kinds):
        point = table.visualItemRect(table.item(0, 0)).center()
        menu = rich_dock._goto_menu_at(table, kind, point)
        assert menu is not None
        next(a for a in menu.actions()
             if a.text() == "Go to selected element").trigger()
        assert seen[-1][0] == kind
    assert [k for k, _n in seen] == kinds


def test_weak_link_empty_context_area_does_nothing(rich_dock):
    index = next(i for i in range(rich_dock._details_tabs.count())
                 if rich_dock._details_tabs.tabText(i) == "Weak Link")
    page = rich_dock._details_tabs.widget(index)
    tables = [t for t in _page_body(page).findChildren(QTableWidget)
              if t.rowCount() > 0]
    assert tables
    assert rich_dock._goto_menu_at(tables[0], "block",
                                   QPoint(-10, -10)) is None


def test_weak_link_context_signal_wiring_and_double_click(rich_dock,
                                                          monkeypatch):
    index = next(i for i in range(rich_dock._details_tabs.count())
                 if rich_dock._details_tabs.tabText(i) == "Weak Link")
    page = rich_dock._details_tabs.widget(index)
    table = next(t for t in _page_body(page).findChildren(QTableWidget)
                 if t.rowCount() > 0)
    wired: list[tuple[object, str, object]] = []
    monkeypatch.setattr(
        rich_dock, "_show_goto_menu",
        lambda t, k, p: wired.append((t, k, p)))
    point = table.visualItemRect(table.item(0, 0)).center()
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, point,
        table.viewport().mapToGlobal(point))
    QApplication.sendEvent(table.viewport(), event)
    assert wired and wired[-1][0] is table and wired[-1][1] == "block"

    seen: list[tuple[str, str]] = []
    rich_dock.element_clicked.connect(lambda k, n: seen.append((k, n)))
    table.setCurrentCell(0, 0)
    table.cellDoubleClicked.emit(0, 0)
    assert seen and seen[-1][0] == "block"

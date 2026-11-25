"""Money shows the model's currency on every screen, not only on charts:
the Run panel and Inspector cost boxes, the Data Tables money columns, the
pool dialogs, the optimisers, Recommend, Compare and the canvas Cost
overlay. Changing it in Preferences updates what is already on screen."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Capacity1/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow

    settings.set_value("startup/show_start_page", False)
    rbd = load_rbd(MODEL)
    rbd.currency = "€"
    w = MainWindow()
    w._load_into_app(rbd, MODEL)
    w.resize(1400, 800)
    w.show()
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _first_block(w):
    view = w._diagram_dock.view
    return next(iter(sorted(view.blocks)))


def test_run_panel_cost_boxes_show_the_currency(win):
    panel = win._run_panel
    assert panel._system_red_cost_field.prefix() == "€"
    assert panel._lost_flow_cost_field.prefix() == "€"
    assert panel._system_red_cost_field.text().startswith("€")


def test_inspector_cost_boxes_show_the_currency(win):
    win._inspector.show_block(_first_block(win))
    win._inspector._populate_costs(
        win._diagram_dock.view.blocks[_first_block(win)])
    spins = win._inspector._scroll_costs.widget().findChildren(QDoubleSpinBox)
    assert spins and all(sp.prefix() == "€" for sp in spins)


def test_data_tables_money_columns_name_the_currency(win):
    tables = win._data_tables
    costs = [tables._costs_table.horizontalHeaderItem(c).text()
             for c in range(tables._costs_table.columnCount())]
    assert costs[0] == "Name" and costs[-1] == "Level"
    assert all(text.endswith(" (€)") for text in costs[2:-1])
    resources = [tables._resources_table.horizontalHeaderItem(c).text()
                 for c in range(tables._resources_table.columnCount())]
    assert "Spare cost (€)" in resources and "Units" in resources


def test_cost_overlay_tooltips_and_badge_use_the_currency(win):
    view = win._diagram_dock.view
    bid = next(iter(view._block_items))
    view.apply_cost_colors({bid: 1234.5})
    item = view._block_items[bid]
    assert "€1,234.50" in item.toolTip()
    assert "$" not in item.toolTip()
    assert item._result_badge[0] == "€"


def test_pool_dialog_money_fields_show_the_currency(win):
    from raptor.gui.ui.pool_dialogs import _EntityDialog, plain_english_summary
    dlg = _EntityDialog(win, "RESOURCE_DATA")
    assert dlg.money_prefix == "€"
    assert "€5 to acquire each" in plain_english_summary(
        "RESOURCE_DATA", {"PoolName": "Crew", "NumIn": "2", "SpareCost": "5"},
        "hours", "€")
    dlg.reject()


def test_recommend_and_optimisers_print_the_currency(win):
    from raptor.gui.ui.optimise_dialogs import _money
    from raptor.gui.ui.recommend_dialog import _cost_text
    assert _money(win, 1500.0) == "€1,500"
    assert _money(win, None) == "—"
    row = {"cost": {"unit": 40.0, "run_total_change": -300.0, "note": ""}}
    assert _cost_text(row, "€") == "€40 a unit; run cost -€300"


def test_changing_the_currency_updates_the_screens(win):
    win._rbd.currency = "£"
    win._apply_currency(changed=True)
    assert win._run_panel._system_red_cost_field.prefix() == "£"
    header = win._data_tables._costs_table.horizontalHeaderItem(2).text()
    assert header == "Initial cost (£)"

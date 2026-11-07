"""Hovering a data-table cell says what that cell means (#145).

The distribution, parameter and PM cells are cell widgets sitting over their
cells, so the hover reaches the widget and never an item beneath it: the tip
has to be on the widget (and the field inside it)."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from raptor.core.fileio.rbd import load_rbd

pytestmark = pytest.mark.gui

MODEL = "tests/golden/HierarchyExample/input.rbd"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1500, 900)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    QApplication.processEvents()
    w._data_tables.refresh(force=True)
    yield w
    w._dirty = False
    w.close()


def _tips(table, row: int, col: int) -> list[str]:
    """Every tooltip a hover on this cell could reach."""
    item = table.item(row, col)
    if item is not None:
        return [item.toolTip()]
    widget = table.cellWidget(row, col)
    return [t.toolTip() for t in (widget, *widget.findChildren(QWidget))]


def _col(table, name: str) -> int:
    return next(c for c in range(table.columnCount())
                if table.horizontalHeaderItem(c).text().startswith(name))


def test_every_cell_of_the_blocks_grid_names_the_block_and_what_it_is(win):
    t = win._data_tables._blocks_table
    assert t.rowCount()
    for row in range(t.rowCount()):
        name = t.item(row, 0).text()
        for col in range(t.columnCount()):
            for tip in _tips(t, row, col):
                head, _, meaning = tip.partition("\n")
                assert head == name, (row, col, tip)
                assert meaning, f"no meaning for row {row} col {col}"


def test_a_parameter_cell_says_what_this_rows_distribution_uses_it_for(win):
    """Not the generic column text: this row's distribution decides it."""
    t = win._data_tables._blocks_table
    view = win._diagram_dock.view
    seen = set()
    for row in range(t.rowCount()):
        bid = int(t.item(row, _col(t, "ID")).text())
        block = view.blocks[bid]
        tip = _tips(t, row, _col(t, "Failure Param 1"))[0]
        assert "1st parameter of the Failure Dist" not in tip
        seen.add((block.fail_distro, tip.split("\n", 1)[1]))
    assert seen


def test_the_field_inside_a_widget_cell_shows_it_too(win):
    t = win._data_tables._blocks_table
    cell = t.cellWidget(0, _col(t, "Failure Param 1"))
    inner = cell.findChildren(QWidget)
    assert inner and all(w.toolTip() == cell.toolTip() for w in inner)
    assert t.cellWidget(0, _col(t, "Failure Dist")).toolTip()


def test_the_read_only_cells_explain_themselves_too(win):
    t = win._data_tables._blocks_table
    for name in ("ID", "# Failure Params", "Failure Mean"):
        assert "\n" in _tips(t, 0, _col(t, name))[0]


def test_triggers_and_phases_param_cells_say_what_the_distribution_uses(win):
    checked = 0
    for table in win._data_tables._keyed:
        heads = [table.horizontalHeaderItem(c).text()
                 for c in range(table.columnCount())]
        if "Dist" not in heads or "Param1" not in heads:
            continue
        for row in range(table.rowCount()):
            for n in (1, 2, 3):
                assert all(_tips(table, row, heads.index(f"Param{n}")))
                checked += 1
    assert checked, "no model row with a distribution to check"

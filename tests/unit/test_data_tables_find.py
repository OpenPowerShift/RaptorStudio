"""Data Tables: footer statistics and find/replace (WP-1.7)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp):
    window = MainWindow()
    window._load_into_app(load_rbd(str(GOLD / "TwoBlockRedundancy/input.rbd")),
                          "x")
    return window


def test_the_footer_shows_statistics_for_the_selected_column(win):
    tables = win._data_tables
    table = tables._blocks_table
    tables._tabs.setCurrentWidget(table)
    if table.rowCount() == 0:
        pytest.skip("no block rows in this fixture")
    for row in range(table.rowCount()):
        table.setItem(row, 3, QTableWidgetItem(str(row + 1)))
    table.setCurrentCell(0, 3)
    tables._update_stats()
    text = tables._stats.text()
    assert "value(s)" in text and "sum" in text and "mean" in text


def test_replace_changes_only_matching_editable_cells(win):
    tables = win._data_tables
    table = QTableWidget(3, 2)
    for row, (name, note) in enumerate((("Pump_A", "keep"),
                                        ("pump_B", "keep"),
                                        ("Valve", "keep"))):
        item = QTableWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, 0, item)
        table.setItem(row, 1, QTableWidgetItem(note))
    changed = tables._replace_cells(table, "pump", "Seal", False, False)
    assert changed == 2
    assert table.item(0, 0).text() == "Seal_A"
    assert table.item(1, 0).text() == "Seal_B"
    assert table.item(2, 0).text() == "Valve"
    assert table.item(0, 1).text() == "keep"


def test_a_replace_that_changes_nothing_leaves_no_undo_step(win):
    """G16: a replace with no match, or one that writes the same text back,
    pushed an empty "Replace in table" step onto the undo stack."""
    tables = win._data_tables
    table = QTableWidget(1, 1)
    item = QTableWidgetItem("Pump")
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
    table.setItem(0, 0, item)
    stack = win._undo_stack
    before = stack.count()
    assert tables._replace_cells(table, "valve", "Seal", False, False) == 0
    assert tables._replace_cells(table, "pump", "Pump", False, False) == 0
    assert stack.count() == before

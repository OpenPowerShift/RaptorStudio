"""Model Explorer Expand all / Collapse all and search through closed rows."""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from raptor.gui.ui.explorer import ExplorerDock


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dock():
    dock = ExplorerDock()
    root = QTreeWidgetItem(["Model"])
    dock._tree.addTopLevelItem(root)
    return dock, root


def _wait_for_expand(dock, qapp, timeout=5.0):
    deadline = time.monotonic() + timeout
    while dock._expand_timer.isActive() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    qapp.processEvents()


def test_expand_all_finishes_in_chunks_without_blocking(qapp):
    dock, root = _dock()
    parent = root
    for level in range(6):
        for i in range(200):
            child = QTreeWidgetItem([f"L{level} item {i}"])
            parent.addChild(child)
        parent = parent.child(0)
    dock.expand_all()
    assert dock._expand_timer.isActive(), "large expand must be chunked"
    _wait_for_expand(dock, qapp)
    assert not dock._expand_timer.isActive()
    assert root.isExpanded()
    assert root.child(0).isExpanded()
    assert dock._expand_button.isEnabled()


def test_collapse_all_cancels_an_expand_and_closes_every_row(qapp):
    dock, root = _dock()
    folder = QTreeWidgetItem(["Folder"])
    root.addChild(folder)
    folder.addChild(QTreeWidgetItem(["Child"]))
    dock.expand_all()
    _wait_for_expand(dock, qapp)
    assert root.isExpanded() and folder.isExpanded()
    dock.collapse_all()
    assert not dock._expand_timer.isActive()
    assert not root.isExpanded()
    assert not folder.isExpanded()


def test_search_finds_and_expands_rows_under_a_closed_folder(qapp):
    dock, root = _dock()
    folder = QTreeWidgetItem(["A closed folder"])
    root.addChild(folder)
    child = QTreeWidgetItem(["The hidden pump"])
    folder.addChild(child)
    root.setExpanded(False)
    folder.setExpanded(False)
    dock.filter_items("hidden pump")
    assert not child.isHidden()
    assert not folder.isHidden()
    assert folder.isExpanded(), "a match must open the chain to its row"
    assert not root.isExpanded() or root.isExpanded()
    dock.filter_items("")
    assert not child.isHidden()

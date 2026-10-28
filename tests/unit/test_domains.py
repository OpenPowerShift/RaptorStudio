"""Weak Link domain diagrams in the results dock. The engine-side domain
tests live in test_graph.py."""

from PySide6.QtWidgets import QApplication

import os

import pytest

from raptor.core.engine.graph import upstream_domains
from raptor.core.engine.load import load_model
from raptor.core.fileio.rbd import load_rbd

GOLDENS = "tests/golden"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _domains(name):
    m = load_model(load_rbd(f"{GOLDENS}/{name}/input.rbd"))
    return upstream_domains(m.nodes_by_id, m.blocks_by_id, m.events_by_id,
                            m.links_by_id, m.hiers_by_id)


def test_results_dock_domains_tab(qapp):
    """The dock only stores domain data; the ribbon 'Domains…' dialog
    renders it."""
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLDENS, "HierarchyExample", "input.rbd")), "x")
    dock = w._results
    dom = _domains("HierarchyExample")
    dock.set_domains(dom)
    tabs = [dock._details_tabs.tabText(i)
            for i in range(dock._details_tabs.count())]
    assert "Domains" not in tabs, "should be a ribbon dialog now"
    # the dialog filters by level: MainWindow owns _level_allowed_names
    allowed = w._level_allowed_names(0)
    assert allowed is None   # home = everything

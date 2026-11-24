"""A second result set in the same Results dock shows its own tab titles."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from raptor.gui import i18n
from raptor.gui.ui.results import ResultsDock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SMALL = os.path.join(REPO, "tests/golden/TwoBlockRedundancy/input.rbd")
LARGER = os.path.join(REPO, "tests/golden/HierarchyExample/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _feed(dock: ResultsDock, path: str) -> list[str]:
    rbd = load_rbd(path)
    reports: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=reports)
    dock.set_results(logs, eor, fin, report_data=reports, rbd=rbd)
    tabs = dock._details_tabs
    return [tabs.tabText(i) for i in range(tabs.count())]


@pytest.mark.parametrize("locale", ["en", "pt_BR"])
def test_details_tabs_match_their_pages_after_another_model(qapp, locale):
    """HierarchyExample adds a Resources tab TwoBlockRedundancy lacks; every
    title after it used to keep the first run's text (off by one)."""
    i18n.install(qapp, locale)
    try:
        fresh = _feed(ResultsDock(), LARGER)
        dock = ResultsDock()
        _feed(dock, SMALL)
        assert _feed(dock, LARGER) == fresh
    finally:
        i18n.install(qapp, "en")

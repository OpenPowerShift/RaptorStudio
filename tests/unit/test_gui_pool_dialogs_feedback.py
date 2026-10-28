"""Pool/trigger dialogs: unique names, greyed dependent rows, a default size that needs no scroll bar."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow
from raptor.gui.ui.pool_dialogs import _EntityDialog

pytestmark = pytest.mark.gui
GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(str(GOLD / "LadL6Full" / "input.rbd")), None)
    yield w
    w._dirty = False


def test_a_trigger_cannot_take_another_triggers_name(win):
    dlg = _EntityDialog(win, "TRIGGERS")
    dlg.listw.setCurrentRow(0)
    first = dlg.rows[0]["TriggerName"]
    dlg.listw.setCurrentRow(1)
    editor = dlg._editors["TriggerName"]
    before = editor.text()
    editor.setText(first)
    editor.editingFinished.emit()
    assert editor.text() == before
    assert dlg.rows[1]["TriggerName"] == before


def test_spare_pool_strategy_rows_are_greyed_until_their_toggle_is_on(win):
    dlg = _EntityDialog(win, "SPARE_POOL_DATA")
    deps = {"RSO": ["ArriveEvery"], "ESO": ["EmerTime", "emerShipCost"], "SLO": ["Level", "Quan", "SLOTime"]}
    for toggle, cols in deps.items():
        box = dlg._editors[toggle]
        for state in (False, True):
            box.setChecked(state)
            assert all(dlg._editors[c].isEnabled() == state for c in cols), (toggle, state)


def test_spare_pool_dialog_opens_tall_enough_for_its_form(win):
    dlg = _EntityDialog(win, "SPARE_POOL_DATA")
    dlg.resize(900, dlg.wanted_height())
    dlg.show()
    QApplication.processEvents()
    scroll = dlg.form.parentWidget().parentWidget().parentWidget()
    bar = scroll.verticalScrollBar()
    assert not bar.isVisible() or bar.maximum() == 0
    dlg.close()

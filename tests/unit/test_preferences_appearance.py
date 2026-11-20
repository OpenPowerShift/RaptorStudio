"""Preferences > Appearance: theme and the chart palette."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from raptor.gui.ui import settings

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean_settings():
    from raptor.api.insight import palette
    before = palette.active()
    QSettings().clear()
    yield
    QSettings().clear()
    palette.set_active(before)


def _open_prefs(qapp, monkeypatch, mutate):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")), "x")

    def fake_exec(self):
        QApplication.processEvents()
        mutate(self)
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    w._show_preferences()
    return w


def _appearance_page(dlg):
    from raptor.gui.ui.dialogs import _SectionPages
    tabs = dlg.findChild(_SectionPages)
    names = [tabs.tabText(i) for i in range(tabs.count())]
    return tabs.widget(names.index("Appearance")), names


def test_appearance_lists_every_palette_and_stores_the_choice(
        qapp, monkeypatch):
    from raptor.api.insight import palette
    seen = {}

    def mutate(dlg):
        page, names = _appearance_page(dlg)
        seen["sections"] = names
        boxes = [box for box in page.findChildren(QComboBox)
                 if box.findData("classic") >= 0]
        assert len(boxes) == 1
        box = boxes[0]
        seen["choices"] = [box.itemData(i) for i in range(box.count())]
        box.setCurrentIndex(box.findData("colourblind"))
    _open_prefs(qapp, monkeypatch, mutate)
    assert "Appearance" in seen["sections"]
    assert seen["choices"] == palette.names()
    assert settings.get("appearance/chart_palette") == "colourblind"
    assert palette.active() == "colourblind"


def test_an_unchanged_palette_is_still_the_default(qapp, monkeypatch):
    from raptor.api.insight import palette

    def mutate(dlg):
        page, _names = _appearance_page(dlg)
        box = next(box for box in page.findChildren(QComboBox)
                   if box.findData("classic") >= 0)
        assert box.currentData() == palette.DEFAULT
    _open_prefs(qapp, monkeypatch, mutate)
    assert settings.get("appearance/chart_palette") == palette.DEFAULT

"""The Extract Hierarchy command in the window (a port extension)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.fileio.rbd import load_rbd

HIER = Path(__file__).resolve().parents[1] / "golden" / "HierarchyExample" / "input.rbd"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1600, 900)
    w.show()
    w._load_into_app(load_rbd(str(HIER)), str(HIER))
    w._dirty = False
    qapp.processEvents()
    yield w
    w._dirty = False
    w.close()


def _select_hierarchy(win, name):
    v = win._diagram_dock.view
    v._scene.clearSelection()
    hid = next(h for h, hr in v.hiers.items() if hr.name == name)
    v._hier_items[hid].setSelected(True)      # the canvas's own signal refreshes the ribbon
    return hid


def test_the_command_needs_exactly_one_hierarchy_selected(win):
    v = win._diagram_dock.view
    v._scene.clearSelection()
    win._refresh_commands()
    assert not win._extract_hierarchy_action.isEnabled()
    assert "select one hierarchy" in win._extract_hierarchy_action.toolTip()
    _select_hierarchy(win, "650path")
    assert win._extract_hierarchy_action.isEnabled()


def test_the_dialog_lists_hazards_and_treatments_change_them(win):
    _select_hierarchy(win, "650path")
    win._on_extract_hierarchy()
    dlg = win._isolate_dialog
    text = dlg.report.toPlainText()
    assert "pool" in text and "stream" in text and "650path" in dlg.windowTitle()
    dlg.pools.setCurrentIndex(dlg.pools.findData("share"))
    assert "share" in dlg.report.toPlainText()
    dlg.reject()


def test_saving_writes_a_valid_rbd_and_opens_it(win, tmp_path, monkeypatch):
    from raptor.gui.ui import filedialogs as fdg
    out = tmp_path / "part.rbd"
    monkeypatch.setattr(fdg, "getSaveFileName", lambda *a, **k: (str(out), ""))
    _select_hierarchy(win, "650path")
    win._on_extract_hierarchy()
    win._isolate_dialog.accept()
    assert out.is_file()
    assert "650path" in out.with_suffix(".isolation.txt").read_text()
    assert win._current_file == str(out)
    kinds = sorted(n["Type"] for n in win._rbd.section("NODE_DATA").rows if n["Type"] in ("1", "3"))
    assert kinds == ["1", "3"]

"""Import Library: Update Matching takes several templates at once (#144)."""
from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QPushButton

from raptor.core.fileio.rbd import load_rbd

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _library(win, tmp_path, names, fparam1="777"):
    """A JSON library whose templates are copies of the named blocks with a
    changed failure parameter."""
    rows = win._rbd.sections["BLOCK_DATA"].rows
    templates = []
    for name in names:
        row = dict(next(r for r in rows if r.get("blockName") == name))
        row["FParam1"] = fparam1
        templates.append({"name": name, "row": row})
    path = tmp_path / "lib.json"
    path.write_text(json.dumps({"format": "raptor-studio-block-library",
                                "version": 1, "templates": templates}))
    return str(path)


def _open_dialog(win, monkeypatch, path, act):
    from raptor.gui.ui import dialogs
    monkeypatch.setattr(dialogs.fdg, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))

    def run(self):
        act(self)
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, "exec", run)
    dialogs.import_block_library(win)


def _fparam1(win, name) -> str:
    row = next(r for r in win._rbd.sections["BLOCK_DATA"].rows
               if r.get("blockName") == name)
    return row["FParam1"]


def _button(dlg, text) -> QPushButton:
    return next(b for b in dlg.findChildren(QPushButton)
                if b.text().startswith(text))


def test_several_selected_templates_update_in_one_go(win, monkeypatch, tmp_path):
    names = ["Attitude", "Thermal", "LowGain"]
    before = {n: _fparam1(win, n) for n in names}
    assert all(v != "777" for v in before.values())
    path = _library(win, tmp_path, names)

    def act(dlg):
        lst = dlg.findChild(QListWidget)
        lst.clearSelection()
        for i in range(lst.count()):
            lst.item(i).setSelected(True)
        btn = _button(dlg, "Update Matching")
        assert btn.text() == f"Update Matching ({len(names)})"
        btn.click()
    _open_dialog(win, monkeypatch, path, act)

    assert {n: _fparam1(win, n) for n in names} == {n: "777" for n in names}
    assert "3 templates" in win._statusbar.currentMessage()
    # One undo step puts them all back.
    win._diagram_dock.view._stack.undo()
    assert {n: _fparam1(win, n) for n in names} == before


def test_only_the_selected_templates_are_applied(win, monkeypatch, tmp_path):
    path = _library(win, tmp_path, ["Attitude", "Thermal"])
    other = _fparam1(win, "Thermal")

    def act(dlg):
        lst = dlg.findChild(QListWidget)
        lst.clearSelection()
        lst.item(0).setSelected(True)               # Attitude only
        _button(dlg, "Update Matching").click()
    _open_dialog(win, monkeypatch, path, act)

    assert _fparam1(win, "Attitude") == "777"
    assert _fparam1(win, "Thermal") == other


def test_select_all_selects_every_template(win, monkeypatch, tmp_path):
    path = _library(win, tmp_path, ["Attitude", "Thermal", "LowGain"])
    seen = {}

    def act(dlg):
        lst = dlg.findChild(QListWidget)
        _button(dlg, "Select All").click()
        seen["n"] = len(lst.selectedIndexes())
        seen["count"] = lst.count()
    _open_dialog(win, monkeypatch, path, act)
    assert seen["n"] == seen["count"] == 3


def test_nothing_selected_disables_the_buttons(win, monkeypatch, tmp_path):
    path = _library(win, tmp_path, ["Attitude"])
    seen = {}

    def act(dlg):
        dlg.findChild(QListWidget).clearSelection()
        seen["upd"] = _button(dlg, "Update Matching").isEnabled()
        seen["add"] = _button(dlg, "Add Block").isEnabled()
    _open_dialog(win, monkeypatch, path, act)
    assert seen == {"upd": False, "add": False}


def test_an_update_that_changes_nothing_does_not_dirty_the_model(
        win, monkeypatch, tmp_path):
    path = _library(win, tmp_path, ["Attitude"],
                    fparam1=_fparam1(win, "Attitude"))
    win._dirty = False
    _open_dialog(win, monkeypatch, path,
                 lambda dlg: _button(dlg, "Update Matching").click())
    assert not win._dirty


def test_add_block_adds_every_selected_template(win, monkeypatch, tmp_path):
    names = ["Attitude", "Thermal", "LowGain"]
    path = _library(win, tmp_path, names)
    view = win._diagram_dock.view
    before = set(view.blocks)
    seen = {}

    def act(dlg):
        lst = dlg.findChild(QListWidget)
        lst.clearSelection()
        lst.item(0).setSelected(True)
        seen["one"] = _button(dlg, "Add Block").text()
        _button(dlg, "Select All").click()
        btn = _button(dlg, "Add Block")
        seen["many"] = btn.text()
        btn.click()
    _open_dialog(win, monkeypatch, path, act)

    assert seen == {"one": "Add Block", "many": "Add Blocks (3)"}
    added = set(view.blocks) - before
    assert len(added) == 3
    assert sorted(view.blocks[i].name for i in added) == sorted(names)
    # laid out apart, not stacked
    spots = {(round(view._block_items[i].pos().x()),
              round(view._block_items[i].pos().y())) for i in added}
    assert len(spots) == 3
    assert "3 blocks" in win._statusbar.currentMessage()
    view._stack.undo()                      # one undo step removes all three
    assert set(view.blocks) == before

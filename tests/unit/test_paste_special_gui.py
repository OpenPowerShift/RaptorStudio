"""Paste Special on the canvas: carried comments, the dialog, one undo step."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from raptor.core import tags as tag_model
from raptor.core.edit import paste_special as ps
from raptor.core.fileio.comments import comment_text
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui import paste_special_dialog as psd
from raptor.gui.ui.main_window import MainWindow

pytestmark = pytest.mark.gui

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _window():
    window = MainWindow()
    window._load_into_app(load_rbd(str(GOLD / "SpaceStation/input.rbd")), "x")
    return window


@pytest.fixture()
def win():
    psd.forget_choices()
    QApplication.clipboard().clear()
    return _window()


def _blocks(window):
    return [r for r in window._rbd.section("BLOCK_DATA").rows
            if r.get("ID") not in ("0", "")]


def _select(view, *rows):
    view._scene.clearSelection()
    for row in rows:
        view._block_items[int(row["ID"])].setSelected(True)


def _comment(window, row):
    return comment_text(window._rbd.sections.get("COMMENTS"), "block",
                        int(row["ID"]), row["blockName"])


def _dress(window, src):
    """Give a block a tag, a tag colour and a comment."""
    view = window._diagram_dock.view
    view.set_element_comment("block", int(src["ID"]), "line one\nline two")
    rbd = window._rbd
    tag_model.set_tags(rbd, tag_model.element_ref("block", src["ID"]), ["hot"])
    tag_model.set_tag_info(rbd, "hot", colour="#ab1234", description="runs hot")
    return view


def test_a_plain_paste_carries_the_comment_and_tags(win):
    src = _blocks(win)[0]
    view = _dress(win, src)
    _select(view, src)
    view.copy_selection()
    before = {int(r["ID"]) for r in _blocks(win)}
    assert view.paste() == 1
    new = next(r for r in _blocks(win) if int(r["ID"]) not in before)
    assert _comment(win, new) == "line one~line two"
    assert tag_model.tags_for(win._rbd, f"block:{new['ID']}") == ["hot"]
    win._undo_stack.undo()
    assert {int(r["ID"]) for r in _blocks(win)} == before
    assert not any(_comment(win, r) == "line one~line two"
                   for r in _blocks(win) if r is not src)


def test_a_paste_into_another_window_carries_them_too(win):
    src = _blocks(win)[0]
    view = _dress(win, src)
    _select(view, src)
    view.copy_selection()
    other = _window()
    other_view = other._diagram_dock.view
    before = {int(r["ID"]) for r in _blocks(other)}
    assert other_view.paste() == 1
    new = next(r for r in _blocks(other) if int(r["ID"]) not in before)
    assert _comment(other, new) == "line one~line two"
    assert tag_model.tags_for(other._rbd, f"block:{new['ID']}") == ["hot"]
    assert tag_model.tag_colour(other._rbd, "hot").lower() == "#ab1234"


def test_paste_special_is_one_undo_step_for_data_tags_and_comment(win):
    rows = _blocks(win)
    src, dst = rows[0], rows[1]
    view = _dress(win, src)
    src["FParam1"] = "888.0"
    _select(view, src)
    view.copy_selection()
    _select(view, dst)
    old = (dst["FParam1"], _comment(win, dst),
           tag_model.tags_for(win._rbd, f"block:{dst['ID']}"))
    steps = win._undo_stack.count()
    n = view.paste_data(("Failure", "Tags", "Comment"))
    assert n == 1
    assert dst["FParam1"] == "888.0" and _comment(win, dst) == "line one~line two"
    assert tag_model.tags_for(win._rbd, f"block:{dst['ID']}") == ["hot"]
    assert win._undo_stack.count() == steps + 1    # three commands, one macro
    win._undo_stack.undo()
    assert (dst["FParam1"], _comment(win, dst),
            tag_model.tags_for(win._rbd, f"block:{dst['ID']}")) == old
    win._undo_stack.redo()
    assert dst["FParam1"] == "888.0"


def test_paste_special_tags_only_leaves_everything_else(win):
    rows = _blocks(win)
    src, dst = rows[0], rows[1]
    view = _dress(win, src)
    before = dict(dst)
    _select(view, src)
    view.copy_selection()
    _select(view, dst)
    assert view.paste_data(("Tags",)) == 1
    assert dict(dst) == before
    assert _comment(win, dst) == ""


def _dialog(win, **kw):
    src, dst = _blocks(win)[:2]
    view = win._diagram_dock.view
    _select(view, src)
    view.copy_selection()
    _select(view, dst)
    source, targets = view.special_context()
    applicable = ps.applicability(source, targets)
    applicable.update(kw)

    def preview(groups, tag_mode, comment_mode):
        plan = ps.plan_special(win._rbd, source, targets, groups,
                               tag_mode=tag_mode, comment_mode=comment_mode)
        return plan.summary(), plan.fields > 0
    return psd.PasteSpecialDialog(win, "heading", applicable, preview)


def test_the_dialog_greys_a_group_that_does_not_apply(win):
    dlg = _dialog(win, Phasing="the phase count differs")
    box = dlg.boxes["Phasing"]
    assert not box.isEnabled() and "phase count" in box.toolTip()
    box.setChecked(True)
    assert "Phasing" not in dlg.groups()
    assert dlg.boxes["Failure"].isEnabled()


def test_the_dialog_previews_and_remembers_the_ticks(win, monkeypatch):
    src = _blocks(win)[0]
    _dress(win, src)
    dlg = _dialog(win)
    assert dlg.groups() == ["Failure", "Repair"]       # the defaults
    dlg.boxes["Tags"].setChecked(True)
    dlg.tag_replace.setChecked(True)
    dlg.comment_combo.setCurrentIndex(1)
    dlg.boxes["Comment"].setChecked(True)
    assert "Will change" in dlg.preview_label.text()
    dlg.remember()
    again = _dialog(win)
    assert {"Tags", "Comment"} <= set(again.groups())
    assert again.tag_mode() == "replace" and again.comment_mode() == "append"
    for box in again.boxes.values():
        box.setChecked(False)
    assert not again.buttons.buttons()[0].isEnabled()


def test_the_menu_command_runs_the_dialog_then_pastes(win, monkeypatch):
    rows = _blocks(win)
    src, dst = rows[0], rows[1]
    view = _dress(win, src)
    _select(view, src)
    view.copy_selection()
    _select(view, dst)
    seen = {}

    def fake_exec(dlg):
        seen["groups"] = dlg.groups()
        seen["greyed"] = [g for g, b in dlg.boxes.items()
                          if not b.isEnabled()]
        dlg.boxes["Comment"].setChecked(True)
        dlg.remember()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr("raptor.gui.ui.modality.exec_dialog", fake_exec)
    win._edit_paste_data()
    assert seen["groups"] == ["Failure", "Repair"]
    assert _comment(win, dst) == "line one~line two"

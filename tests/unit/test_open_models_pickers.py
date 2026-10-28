"""The pickers that choose "another model" list the models open in other
windows (a11f2db4): Compare's Add models menu and Differences'
"An open or recent model".
"""
from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from raptor.gui import open_models

pytestmark = pytest.mark.gui

SOURCE = "new_docs/models/HospitalPower.rbds"


@pytest.fixture
def two(monkeypatch, tmp_path):
    """Two windows: `w1` (Hospital.rbds, clean) and `w2` (Other.rbds, with
    an unsaved edit: block 7 renamed)."""
    monkeypatch.setattr(open_models, "_WINDOWS", [])
    monkeypatch.setattr(open_models, "_KEPT", [])
    monkeypatch.setattr(open_models, "_SERIAL", [0])
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    paths = []
    for name in ("Hospital.rbds", "Other.rbds"):
        p = tmp_path / name
        shutil.copy(SOURCE, p)
        paths.append(str(p))
    w1 = MainWindow()
    w1._load_into_app(load_rbds(paths[0]), paths[0])
    w2 = w1._spawn_window()
    w2._load_into_app(load_rbds(paths[1]), paths[1])
    w2._diagram_dock.view.rename_block(7, "ATS_Renamed")
    w2._dirty = True
    monkeypatch.setattr(w1, "_recent_list", lambda: [])
    yield SimpleNamespace(w1=w1, w2=w2, paths=paths, tmp=tmp_path)
    for w in list(open_models.windows()):
        for attr in ("_compare_dialog", "_diff_dialog"):
            d = getattr(w, attr, None)
            if d is not None:
                d.close()
        w._dirty = False
        w.close()


def _menu_texts(dlg):
    dlg._fill_add_menu()
    return [(a.text() if not a.isSeparator() else "---")
            for a in dlg._add_menu.actions()]


def _rows(d):
    out = []
    root = d._tree.invisibleRootItem()
    for i in range(root.childCount()):
        g = root.child(i)
        for j in range(g.childCount()):
            e = g.child(j)
            for k in range(e.childCount()):
                f = e.child(k)
                out.append((e.text(0), f.text(1), f.text(2), f.text(3)))
    return out


# -- Compare ---------------------------------------------------------------

def test_the_add_menu_offers_a_file_and_each_open_model(two):
    dlg = two.w1._open_compare_dialog()
    assert _menu_texts(dlg) == [
        "From a file…", "---",
        "Other.rbds — open, with unsaved changes"]


def test_the_add_menu_offers_all_when_there_are_several(two):
    third = two.w1._spawn_window()
    from raptor.core.fileio.rbds import load_rbds
    p = str(two.tmp / "Third.rbds")
    shutil.copy(SOURCE, p)
    third._load_into_app(load_rbds(p), p)
    dlg = two.w1._open_compare_dialog()
    texts = _menu_texts(dlg)
    assert texts[:2] == ["From a file…", "---"]
    assert texts[-1] == "All open models" and len(texts) == 5
    # a menu with nothing open elsewhere has only the file entry
    solo = two.w2._open_compare_dialog()
    two.w1._dirty = False
    two.w1.close()
    third._dirty = False
    third.close()
    assert _menu_texts(solo) == ["From a file…"]


def test_the_menu_is_built_when_it_opens(two):
    dlg = two.w1._open_compare_dialog()
    assert len(_menu_texts(dlg)) == 3
    two.w2._dirty = False
    two.w2.close()
    assert _menu_texts(dlg) == ["From a file…"]


def test_adding_an_open_model_lists_it_by_name_with_open(two):
    dlg = two.w1._open_compare_dialog()
    om = open_models.others(two.w1)[0]
    dlg.add_open_models([om])
    assert dlg._files == [om]
    assert dlg._list.count() == 2
    assert dlg._list.item(1).text() == "Other.rbds (open)"
    assert dlg._list.item(1).toolTip() == two.paths[1]
    assert "2 runs" in dlg._plan.text()


def test_an_untitled_open_model_is_listed_by_its_window(two):
    two.w2._current_file = None
    dlg = two.w1._open_compare_dialog()
    om = open_models.others(two.w1)[0]
    dlg.add_open_models([om])
    assert dlg._list.item(1).text() == "Untitled (window 2) (open)"


def test_the_same_open_model_is_not_added_twice(two):
    dlg = two.w1._open_compare_dialog()
    dlg.add_open_models(open_models.others(two.w1))
    dlg.add_open_models(open_models.others(two.w1))    # a new OpenModel
    assert len(dlg._files) == 1 and dlg._list.count() == 2


def test_an_open_model_whose_file_is_already_listed_is_skipped(two):
    dlg = two.w1._open_compare_dialog()
    dlg.add_paths([two.paths[1]])
    assert dlg._files == [two.paths[1]]
    dlg.add_open_models(open_models.others(two.w1))
    assert dlg._files == [two.paths[1]]


def test_model_labels_names_open_models_and_keeps_the_folder_rule(two):
    from raptor.gui.ui.batch_dialogs import model_labels
    om = open_models.others(two.w1)[0]
    assert model_labels([om]) == ["Other.rbds (open)"]
    assert model_labels(["/a/x/M.rbds", om, "/a/y/M.rbds", "/a/z/Q.rbd"]) \
        == ["x/M.rbds", "Other.rbds (open)", "y/M.rbds", "Q.rbd"]
    assert model_labels([]) == []


def test_variants_run_an_open_model_as_it_is_now_with_unsaved_edits(two):
    dlg = two.w1._open_compare_dialog()
    dlg.add_open_models(open_models.others(two.w1))
    out = dlg._variants()
    assert [v[0] for v in out] == ["This model", "Other.rbds (open)"]
    assert out[1][2] == []
    names = {r.get("blockName")
             for r in out[1][1].section("BLOCK_DATA").rows}
    assert "ATS_Renamed" in names                  # the unsaved edit
    mine = {r.get("blockName") for r in out[0][1].section("BLOCK_DATA").rows}
    assert "ATS_Renamed" not in mine
    # and it is a copy: the other window's own model is not the one run
    assert out[1][1] is not two.w2._rbd


def test_variants_mix_files_and_open_models_in_order(two):
    other = two.tmp / "Third.rbds"
    shutil.copy(SOURCE, other)
    dlg = two.w1._open_compare_dialog()
    dlg.add_paths([str(other)])
    dlg.add_open_models(open_models.others(two.w1))
    out = dlg._variants()
    assert [v[0] for v in out] == ["This model", "Third.rbds",
                                   "Other.rbds (open)"]


def test_a_closed_open_model_is_reported_not_raised(two):
    from raptor.gui.ui.batch_dialogs import CompareDialog  # noqa: F401

    def gone():
        raise RuntimeError("Internal C++ object already deleted.")
    dlg = two.w1._open_compare_dialog()
    om = open_models.OpenModel(
        "Gone.rbds", None, False,
        SimpleNamespace(_model_for_engine=lambda copy=False: gone()))
    dlg._files.append(om)
    msg = dlg._variants()
    assert isinstance(msg, str) and msg.startswith("Gone.rbds was closed")


def test_whats_different_hands_an_open_model_straight_to_differences(two):
    dlg = two.w1._open_compare_dialog()
    om = open_models.others(two.w1)[0]
    dlg.add_open_models([om])
    dlg._list.item(1).setSelected(True)
    dlg._open_differences()
    diff = two.w1._diff_dialog
    assert diff._recent.isChecked()
    assert diff._recent_combo.currentData().window is two.w2
    assert any(r[1] == "(renamed)" and r[2] == "ATS_Renamed"
               for r in _rows(diff))


def test_whats_different_still_passes_a_file_path(two, monkeypatch):
    calls = []
    monkeypatch.setattr(two.w1, "_open_diff_dialog",
                        lambda *a, **k: calls.append((a, k)))
    dlg = two.w1._open_compare_dialog()
    dlg.add_paths([two.paths[1]])
    dlg._list.item(1).setSelected(True)
    dlg._open_differences()
    assert calls == [((two.paths[1],), {"open_model": None})]


# -- Differences -----------------------------------------------------------

def test_recent_lists_open_models_first_then_recent_files_not_open(two,
                                                                   monkeypatch):
    extra = two.tmp / "Old.rbds"
    shutil.copy(SOURCE, extra)
    monkeypatch.setattr(two.w1, "_recent_list", lambda: [
        two.paths[0],            # this window's own file: left out
        two.paths[1],            # open in another window: listed once, above
        str(extra)])
    d = two.w1._open_diff_dialog()
    combo = d._recent_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Other.rbds — open, with unsaved changes", "Old.rbds"]
    assert isinstance(combo.itemData(0), open_models.OpenModel)
    assert combo.itemData(1) == str(extra)
    assert d._recent.text() == "An open or recent model:"
    assert d._recent.isEnabled()


def test_recent_is_disabled_when_nothing_is_open_or_recent(two):
    two.w2._dirty = False
    two.w2.close()
    d = two.w1._open_diff_dialog()
    assert d._recent_combo.count() == 0 and not d._recent.isEnabled()


def test_set_open_model_selects_it_and_compares(two):
    d = two.w1._open_diff_dialog()
    om = open_models.others(two.w1)[0]
    d.set_open_model(om)
    assert d._recent.isChecked()
    assert d._recent_combo.currentData().window is two.w2
    assert d._target()[0].window is two.w2 and d._target()[1] == "auto"
    rows = _rows(d)
    # the other model is the "before", this one the "after"
    assert any(r[1] == "(renamed)" and r[2:] == ("ATS_Renamed", "ATS_B")
               for r in rows), rows
    assert "No differences" not in d._summary.text()


def test_set_open_model_adds_an_entry_that_is_not_listed(two):
    d = two.w1._open_diff_dialog()
    d._recent_combo.clear()
    om = open_models.others(two.w1)[0]
    d.set_open_model(om)
    assert d._recent_combo.count() == 1 and d._recent_combo.currentIndex() == 0
    assert d._recent.isEnabled() and d._recent.isChecked()


def test_an_open_model_with_the_same_content_has_no_differences(two):
    two.w2._diagram_dock.view._stack.undo()           # undo the rename
    d = two.w1._open_diff_dialog()
    d.set_open_model(open_models.others(two.w1)[0])
    assert "No differences" in d._summary.text()


def test_a_closed_open_model_says_so(two):
    d = two.w1._open_diff_dialog()
    om = open_models.others(two.w1)[0]

    def gone(copy=False):
        raise RuntimeError("deleted")
    om.window = SimpleNamespace(_model_for_engine=gone)
    d.set_open_model(om)
    assert d._summary.text() == "Other.rbds has been closed."


def test_open_diff_with_open_model_on_a_new_and_on_the_kept_dialog(two):
    from raptor.gui.ui.diff_dialog import open_diff
    om = open_models.others(two.w1)[0]
    d = open_diff(two.w1, open_model=om)               # a new dialog
    assert d._recent.isChecked()
    assert d._recent_combo.currentData().window is two.w2
    d._saved.setChecked(True)
    d2 = open_diff(two.w1, open_model=om)              # the kept one
    assert d2 is d and d._recent.isChecked()
    assert d._recent_combo.currentData().window is two.w2


def test_reopening_refills_the_list_as_windows_come_and_go(two):
    from raptor.gui.ui.diff_dialog import open_diff
    d = open_diff(two.w1)
    assert d._recent_combo.count() == 1
    two.w2._dirty = False
    two.w2.close()
    d2 = open_diff(two.w1)
    assert d2 is d and d._recent_combo.count() == 0
    assert not d._recent.isEnabled()

"""Model > Differences: this model against its saved file or another model."""
from __future__ import annotations

import shutil

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

SOURCE = "new_docs/models/HospitalPower.rbds"


@pytest.fixture
def win(tmp_path):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    path = tmp_path / "Hospital.rbds"
    shutil.copy(SOURCE, path)
    w = MainWindow()
    w._load_into_app(load_rbds(str(path)), str(path))
    yield w
    w._dirty = False
    w.close()


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


def test_an_unchanged_model_has_no_differences(win):
    d = win._open_diff_dialog()
    assert d._saved.isChecked()
    assert "No differences" in d._summary.text()


def test_unsaved_edits_show_against_the_saved_file(win):
    v = win._diagram_dock.view
    v.set_block_repair_params(7, [9.0, 5.0, 0])     # ATS_B
    v.rename_block(7, "ATS_Backup")
    new = v.add_block("New_Feeder", 30.0, 60.0)
    d = win._open_diff_dialog()
    rows = _rows(d)
    assert ("Block ATS_Backup", "repair", "Lognormal (mean 10, stdev 5)",
            "Lognormal (mean 9, stdev 5)") in rows
    assert any(r[0] == "Block New_Feeder" and r[1] == "new here"
               for r in rows)
    assert any(r[1] == "(renamed)" and r[2:] == ("ATS_B", "ATS_Backup")
               for r in rows)
    assert new in v.blocks


def test_double_click_finds_the_element_on_any_level(win):
    v = win._diagram_dock.view
    v.enter_level(100)
    gen = next(bid for bid, b in v.blocks.items() if b.name == "Generator_1")
    v.set_block_fail_params(gen, [2.0, 3000.0, 0])
    v.go_home()
    d = win._open_diff_dialog()
    root = d._tree.invisibleRootItem()
    item = next(root.child(0).child(j) for j in range(root.child(0).childCount())
                if "Generator_1" in root.child(0).child(j).text(0))
    d._locate(item, 0)
    assert v._current_parent == 100
    assert v._block_items[gen].isSelected()


def test_another_model_and_export(win, tmp_path, monkeypatch):
    from raptor.core.fileio.rbd import save_rbd
    from raptor.core.fileio.rbds import load_rbds
    other = load_rbds(SOURCE)
    for b in other.section("BLOCK_DATA").rows:
        if b.get("blockName") == "Utility_Grid":
            b["RParam1"] = "6.000000"
    legacy = tmp_path / "other.rbd"
    save_rbd(other, str(legacy))
    d = win._open_diff_dialog(str(legacy))
    assert d._other.isChecked()
    assert any(r[0] == "Block Utility_Grid" and r[1] == "repair"
               for r in _rows(d))
    out = tmp_path / "diff.csv"
    import raptor.gui.ui.filedialogs as fd
    monkeypatch.setattr(fd, "getSaveFileName",
                        lambda *a, **k: (str(out), "CSV (*.csv)"))
    d._export()
    assert "Utility_Grid" in out.read_text(encoding="utf-8")


def test_a_new_model_gets_a_fresh_window(win):
    """The window was kept across File > New, still offering the saved
    file the new model does not have, and comparing crashed."""
    from raptor.gui.ui import settings
    d = win._open_diff_dialog()
    d.close()
    win._dirty = False
    settings.set_value("files/open_in", "this")   # the same window
    try:
        win._new_file()
    finally:
        settings.set_value("files/open_in", "new")
    d2 = win._open_diff_dialog()
    assert d2 is not d
    assert not d2._saved.isEnabled()
    assert "Choose a model" in d2._summary.text()

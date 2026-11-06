"""Copy in one model, paste in another model or another window."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui import clipboard_payload as cp

pytestmark = pytest.mark.gui

MODEL_A = "tests/golden/Phasing3/input.rbd"
MODEL_B = "tests/golden/Phasing1/input.rbd"


def _window(path):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbd(path), path)
    QApplication.processEvents()
    return w


@pytest.fixture
def two():
    a, b = _window(MODEL_A), _window(MODEL_B)
    QApplication.clipboard().clear()
    yield a, b
    for w in (a, b):
        w._dirty = False
        w.close()


def _select(view, names):
    view._scene.clearSelection()
    for bid, it in view._block_items.items():
        if view.blocks[bid].name in names:
            it.setSelected(True)


def test_copy_in_one_model_pastes_in_another(two):
    a, b = two
    va, vb = a._diagram_dock.view, b._diagram_dock.view
    _select(va, {"Attitude", "Thermal"})
    assert va.copy_selection() == 2
    before = {i: x.name for i, x in vb.blocks.items()}
    assert vb.can_paste()
    assert vb.paste() == 2
    added = [x.name for i, x in vb.blocks.items() if i not in before]
    assert sorted(added) == ["Attitude", "Thermal"]
    vb._stack.undo()                         # one undo step
    assert {i: x.name for i, x in vb.blocks.items()} == before


def test_pasted_block_fits_the_target_model(two):
    a, b = two
    va, vb = a._diagram_dock.view, b._diagram_dock.view
    _select(va, {"Attitude"})
    row = next(r for r in a._rbd.sections["BLOCK_DATA"].rows
               if r.get("blockName") == "Attitude")
    row["DepNum"], row["DepType"] = "3", "RBDBlock"   # on a block not copied
    va.copy_selection()
    notes = []
    vb.status_message_sink = notes.append
    vb.paste()
    new = [r for r in b._rbd.sections["BLOCK_DATA"].rows
           if r.get("blockName") == "Attitude"][-1]
    assert new["DepNum"] == "0"
    assert set(new) == set(
        next(r for r in b._rbd.sections["BLOCK_DATA"].rows if r["ID"] == "0"))
    assert any("dependenc" in n for n in notes)


def test_a_block_with_an_internal_link_and_a_hierarchy_round_trip(two):
    a, b = two
    va, vb = a._diagram_dock.view, b._diagram_dock.view
    # everything on the level: blocks, regular nodes and the links among them
    va.select_all()
    n = va.copy_selection()
    assert n > 1
    links_before = len(b._rbd.sections["LINK_DATA"].rows)
    got = vb.paste()
    assert got == n
    internal = [r for k, r in va._paste_buffer if k == "link"]
    assert len(b._rbd.sections["LINK_DATA"].rows) == links_before + len(internal)


def test_paste_between_two_instances_through_the_clipboard_only(two):
    """No shared in-app buffer: only what is on the system clipboard."""
    a, b = two
    va, vb = a._diagram_dock.view, b._diagram_dock.view
    _select(va, {"Attitude"})
    va.copy_selection()
    vb._paste_buffer = []
    assert vb.can_paste() and vb.paste() == 1


def test_unrelated_clipboard_content_disables_paste(two):
    a, _b = two
    va = a._diagram_dock.view
    _select(va, {"Attitude"})
    va.copy_selection()
    assert va.can_paste()
    mime = QMimeData()
    mime.setText("just some text")
    QApplication.clipboard().setMimeData(mime)
    QApplication.processEvents()
    assert not va.can_paste() and va.paste() == 0
    assert not a._paste_action.isEnabled()


def test_garbage_or_future_payload_is_ignored(two):
    a, _b = two
    va = a._diagram_dock.view
    for data in (b"{not json", b'{"format":"raptor-studio-clip","version":9}'):
        mime = QMimeData()
        mime.setData(cp.MIME, data)
        QApplication.clipboard().setMimeData(mime)
        assert not va.can_paste()
        before = len(va.blocks)
        assert va.paste() == 0 and len(va.blocks) == before


def test_empty_clipboard_falls_back_to_the_views_own_copy(two):
    a, _b = two
    va = a._diagram_dock.view
    _select(va, {"Attitude"})
    va.copy_selection()
    QApplication.clipboard().clear()
    assert va.can_paste()
    assert va.paste() == 1


def test_the_text_flavour_is_a_summary(two):
    a, _b = two
    va = a._diagram_dock.view
    _select(va, {"Attitude", "Thermal"})
    va.copy_selection()
    assert QApplication.clipboard().text().startswith(
        "Raptor Studio: 2 blocks")


def test_same_model_paste_keeps_its_dependency(two):
    a, _b = two
    va = a._diagram_dock.view
    row = next(r for r in a._rbd.sections["BLOCK_DATA"].rows
               if r.get("blockName") == "Attitude")
    row["DepNum"], row["DepType"] = "-2", "None"
    _select(va, {"Attitude"})
    va.copy_selection()
    va.paste()
    new = [r for r in a._rbd.sections["BLOCK_DATA"].rows
           if r.get("blockName") == "Attitude"][-1]
    assert new["DepNum"] == "-2"


def test_a_hierarchy_with_its_contents_pastes_into_another_model():
    src = _window("tests/golden/CSColdPmProbe/input.rbd")
    dst = _window(MODEL_B)
    QApplication.clipboard().clear()
    try:
        vs, vd = src._diagram_dock.view, dst._diagram_dock.view
        vs._scene.clearSelection()
        item = next(iter(vs._hier_items.values()))
        item.setSelected(True)
        assert vs.copy_selection() >= 1
        rows = lambda w, s: len(w._rbd.sections[s].rows)   # noqa: E731
        before = {s: rows(dst, s) for s in
                  ("HIERARCHY_DATA", "BLOCK_DATA", "NODE_DATA", "LINK_DATA")}
        assert vd.paste() >= 1
        grew = {s: rows(dst, s) - before[s] for s in before}
        buf = vs._paste_buffer
        assert grew["HIERARCHY_DATA"] == sum(k == "hier" for k, _ in buf) >= 1
        assert grew["BLOCK_DATA"] == sum(k == "block" for k, _ in buf)
        assert grew["LINK_DATA"] == sum(k == "link" for k, _ in buf)
        # the copy is wired to its own new markers, not the source's ids
        new_h = dst._rbd.sections["HIERARCHY_DATA"].rows[-1]
        nodes = {r["ID"] for r in dst._rbd.sections["NODE_DATA"].rows}
        assert new_h["in"] in nodes and new_h["out"] in nodes
        vd._stack.undo()
        assert rows(dst, "HIERARCHY_DATA") == before["HIERARCHY_DATA"]
    finally:
        for w in (src, dst):
            w._dirty = False
            w.close()


def test_dependent_blocks_copied_together_stay_dependent_in_another_model(two):
    a, b = two
    va, vb = a._diagram_dock.view, b._diagram_dock.view
    rows = {r["blockName"]: r for r in a._rbd.sections["BLOCK_DATA"].rows}
    master, follower = rows["Attitude"], rows["Thermal"]
    follower["DepNum"], follower["DepType"] = master["ID"], "RBDBlock"
    _select(va, {"Attitude", "Thermal"})
    va.copy_selection()
    notes = []
    vb.status_message_sink = notes.append
    vb.paste()
    new = {r["blockName"]: r for r in b._rbd.sections["BLOCK_DATA"].rows
           if r["blockName"] in ("Attitude", "Thermal")}
    assert new["Thermal"]["DepNum"] == new["Attitude"]["ID"]
    assert not any("dependenc" in n for n in notes)


def test_a_partial_copy_drops_the_dependency_and_says_so(two):
    a, b = two
    va, vb = a._diagram_dock.view, b._diagram_dock.view
    rows = {r["blockName"]: r for r in a._rbd.sections["BLOCK_DATA"].rows}
    rows["Thermal"]["DepNum"] = rows["Attitude"]["ID"]
    rows["Thermal"]["DepType"] = "RBDBlock"
    _select(va, {"Thermal"})                       # the master stays behind
    va.copy_selection()
    notes = []
    vb.status_message_sink = notes.append
    vb.paste()
    new = [r for r in b._rbd.sections["BLOCK_DATA"].rows
           if r["blockName"] == "Thermal"][-1]
    assert new["DepNum"] == "0" and new["DepType"] == "None"
    assert any("dependenc" in n for n in notes)


def test_copy_paste_in_one_model_remaps_copied_pairs(two):
    a, _b = two
    va = a._diagram_dock.view
    rows = {r["blockName"]: r for r in a._rbd.sections["BLOCK_DATA"].rows}
    rows["Thermal"]["DepNum"] = rows["Attitude"]["ID"]
    rows["Thermal"]["DepType"] = "RBDBlock"
    _select(va, {"Attitude", "Thermal"})
    va.copy_selection()
    va.paste()
    news = {r["blockName"]: r for r in a._rbd.sections["BLOCK_DATA"].rows
            if r["ID"] not in (rows["Attitude"]["ID"], rows["Thermal"]["ID"])
            and r["blockName"] in ("Attitude", "Thermal")}
    assert news["Thermal"]["DepNum"] == news["Attitude"]["ID"] != rows["Attitude"]["ID"]

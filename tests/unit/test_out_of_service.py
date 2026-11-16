"""U27: take an element out of service (cut or linked) for a what-if."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio import out_of_service as oos
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import parse_rbds, render_rbds

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture
def rbd():
    return load_rbd(str(GOLD / "HierarchyExample" / "input.rbd"))


def _id(rbd, name):
    return int(next(r["ID"] for r in rbd.sections["BLOCK_DATA"].rows if r["blockName"] == name))


def _avail(rbd, n=10, **kw):
    sink: list = []
    run_simulation(rbd, n, _report_sink=sink, collect_logs=False, **kw)
    return sum(r.availability for r in sink) / len(sink)


def test_nothing_out_of_service_is_the_same_model(rbd):
    assert oos.apply(rbd) is rbd


def test_a_cut_series_block_brings_the_system_down_and_linked_never_fails(rbd):
    base = _avail(rbd)
    oos.set_state(rbd, "block", _id(rbd, "Attitude"), "cut")
    assert _avail(rbd) == 0.0
    oos.set_state(rbd, "block", _id(rbd, "Attitude"), "linked")
    assert _avail(rbd) > base
    oos.set_state(rbd, "block", _id(rbd, "Attitude"), None)
    assert rbd.out_of_service == {} and _avail(rbd) == base


def test_the_stored_element_is_unchanged(rbd):
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows if r["blockName"] == "Attitude")
    before = dict(row)
    oos.set_state(rbd, "block", _id(rbd, "Attitude"), "cut")
    oos.apply(rbd)
    assert row == before


def test_a_hierarchy_spreads_to_what_is_inside_and_own_state_wins(rbd):
    hid = int(next(r["ID"] for r in rbd.sections["HIERARCHY_DATA"].rows
                   if r["hierarchyName"] == "900path"))
    inside = [int(r["ID"]) for r in rbd.sections["BLOCK_DATA"].rows if r["ParentId"] == str(hid)]
    assert inside
    oos.set_state(rbd, "hier", hid, "cut")
    oos.set_state(rbd, "block", inside[0], "linked")
    blocks, _events = oos.effective(rbd)
    assert blocks[inside[0]] == "linked" and all(blocks[b] == "cut" for b in inside[1:])


def test_it_is_saved_in_rbds_and_runs_the_same_in_parallel(rbd):
    oos.set_state(rbd, "block", _id(rbd, "Attitude"), "cut")
    text = render_rbds(rbd)
    assert '"out_of_service"' in text
    again = parse_rbds(text)
    assert again.out_of_service == {f"block:{_id(rbd, 'Attitude')}": "cut"}
    assert _avail(again, 8, num_workers=2) == 0.0


def test_events_cut_and_linked():
    """SpfEventUnphasedProbe: one block and a 50 % event."""
    rbd = load_rbd(str(GOLD / "SpfEventUnphasedProbe" / "input.rbd"))
    eid = int(next(r["ID"] for r in rbd.sections["EVENT_DATA"].rows if r.get("ID") != "0"))
    oos.set_state(rbd, "event", eid, "cut")
    assert _avail(rbd) == 0.0
    oos.set_state(rbd, "event", eid, "linked")
    linked = _avail(rbd)
    oos.set_state(rbd, "event", eid, None)
    assert linked > _avail(rbd)


def test_bad_states_are_refused_and_malformed_file_entries_dropped(rbd):
    with pytest.raises(ValueError):
        oos.set_state(rbd, "node", 1, "cut")
    with pytest.raises(ValueError):
        oos.set_state(rbd, "block", 1, "off")
    rbd.out_of_service = {"block:x": "cut", "block:2": "maybe", "event:4": "linked"}
    assert oos.states(rbd) == {"event:4": "linked"}


def test_canvas_marks_cut_and_linked_and_undo_puts_them_back(rbd):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from raptor.gui.ui.diagram import DiagramView
    from raptor.gui.ui.diagram_out_of_service import set_states
    v = DiagramView()
    v.load_rbd(rbd)
    att, hg = _id(rbd, "Attitude"), _id(rbd, "HighGain")
    assert set_states(v, [("block", att)], "cut")
    assert set_states(v, [("block", hg)], "linked")
    assert v._block_items[att]._oos_mark is not None and v._block_items[att].opacity() < 1
    assert "CUT" in v._block_items[att].toolTip()
    assert v._block_items[hg].opacity() == 1 and "LINKED" in v._block_items[hg].toolTip()
    dashed = [link for link in v._links if link._oos_cut]
    assert dashed and all(v._block_items[att] in (link._from, link._to) for link in dashed)
    assert rbd.out_of_service == {f"block:{att}": "cut", f"block:{hg}": "linked"}
    v._stack.undo()
    v._stack.undo()
    assert rbd.out_of_service == {} and v._block_items[att]._oos_mark is None
    assert not any(link._oos_cut for link in v._links)
    assert "CUT" not in v._block_items[att].toolTip()
    assert not set_states(v, [("node", 1)], "cut")          # nodes cannot


def test_the_svg_marks_them_too(rbd):
    from raptor.render.svg import render_svg
    oos.set_state(rbd, "block", _id(rbd, "Attitude"), "cut")
    oos.set_state(rbd, "block", _id(rbd, "HighGain"), "linked")
    svg, meta = render_svg(rbd)
    assert meta["out_of_service"] == 2
    assert f'id="oos-block-{_id(rbd, "Attitude")}"' in svg and ">CUT<" in svg and ">LINKED<" in svg
    assert svg.count('stroke-dasharray="6 4"') >= 2 + 2      # two outlines, Attitude's two links
    plain, meta = render_svg(load_rbd(str(GOLD / "HierarchyExample" / "input.rbd")))
    assert "out_of_service" not in meta and "oos-" not in plain


def test_a_hierarchy_and_those_inside_it_carry_its_state(rbd):
    rows = rbd.sections["HIERARCHY_DATA"].rows
    hid = int(next(r["ID"] for r in rows if r["hierarchyName"] == "900path"))
    assert oos.hierarchies(rbd) == {}
    oos.set_state(rbd, "hier", hid, "cut")
    assert oos.hierarchies(rbd) == {hid: "cut"}
    # nest another hierarchy inside it: the inner one is cut too
    inner = next(r for r in rows if r["hierarchyName"] == "650path")
    inner["Parent"] = str(hid)
    assert oos.hierarchies(rbd) == {hid: "cut", int(inner["ID"]): "cut"}
    rbd.out_of_service = {**rbd.out_of_service, "hier:1": "maybe"}   # dropped
    assert oos.hierarchies(rbd)[hid] == "cut"


def test_the_canvas_marks_a_cut_hierarchy(rbd):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from raptor.gui.ui.diagram import DiagramView
    from raptor.gui.ui.diagram_out_of_service import set_states
    v = DiagramView()
    v.load_rbd(rbd)
    hid = int(next(r["ID"] for r in rbd.sections["HIERARCHY_DATA"].rows
                   if r["hierarchyName"] == "900path"))
    assert set_states(v, [("hier", hid)], "cut")
    oval = v._hier_items[hid]
    assert oval._oos_mark is not None and "CUT" in oval.toolTip()
    assert any(link._oos_cut for link in v._links if oval in (link._from, link._to))

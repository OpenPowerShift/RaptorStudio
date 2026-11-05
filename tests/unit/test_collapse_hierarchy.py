"""Collapse Selection with a hierarchy oval in the selection moves the
hierarchy in whole: its oval, everything inside it, one level deeper.

The oval used to be ignored: HospitalPower's UPS_System + Main_Switchboard
became a hierarchy holding only Main_Switchboard, and Supply_Split ..
Either_Source (around the Generator_Bank oval) was refused as "entered by 2
links and left by 2" because the oval's own links looked like boundary
crossings.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.api import build  # noqa: E402
from raptor.api.validate import issues  # noqa: E402
from raptor.api.workspace import Workspace  # noqa: E402
from raptor.core.engine.runner import run_simulation_with_reports  # noqa: E402
from raptor.core.fileio.model_file import load_model_file  # noqa: E402
from raptor.core.input_rules import hierarchy_depths  # noqa: E402
from raptor.gui.ui import msgbox  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.gui
ROOT = Path(__file__).resolve().parents[2]
HOSPITAL = ROOT / "new_docs" / "models" / "HospitalPower.rbds"
SIX_LEVELS = Path(__file__).parent / "data" / "hierarchy_six_levels.rbds"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def warnings(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(msgbox, "warning",
                        lambda _p, _t, text, *a, **k: shown.append(text))
    return shown


def _window(path=HOSPITAL):
    w = MainWindow()
    w._load_into_app(load_model_file(str(path)), None)
    return w, w._diagram_dock.view


def _select(v, names):
    v._scene.clearSelection()
    for items, objs, attr in ((v._block_items, v.blocks, "name"),
                              (v._node_items, v.nodes, "node_name"),
                              (v._hier_items, v.hiers, "name")):
        for oid, item in items.items():
            if getattr(objs[oid], attr) in names:
                item.setSelected(True)


def _metrics(rbd):
    rd: list = []
    run_simulation_with_reports(copy.deepcopy(rbd), num_runs=50,
                                report_sink=rd, date="fixed")
    return [round(r.availability, 12) for r in rd]


def _hiers(rbd):
    return {r["hierarchyName"]: (r["Parent"], r["level"])
            for r in rbd.sections["HIERARCHY_DATA"].rows if r["ID"] != "0"}


@pytest.mark.parametrize("names,moved", [
    (["UPS_System", "Main_Switchboard"], "UPS_System"),
    (["Supply_Split", "Utility_Grid", "Generator_Bank", "Either_Source"],
     "Generator_Bank"),
])
def test_a_selected_hierarchy_moves_in_whole(qapp, warnings, names, moved):
    w, v = _window()
    base = _metrics(w._rbd)
    _select(v, names)
    hid = v.collapse_selection(name="Wrapped")
    assert hid is not None and warnings == []
    v.write_back_to_rbd(w._rbd)
    assert _hiers(w._rbd)[moved] == (str(hid), "2")
    assert hierarchy_depths(w._rbd)[next(
        int(r["ID"]) for r in w._rbd.sections["HIERARCHY_DATA"].rows
        if r["hierarchyName"] == moved)] == 2
    assert [i for i in issues(w._rbd) if i.severity == "error"] == []
    assert _metrics(w._rbd) == base               # the picture, not the numbers
    v.enter_level(hid)
    assert any(v.hiers[h].name == moved for h in v._hier_items)
    v.go_home()
    v._stack.undo()
    v.write_back_to_rbd(w._rbd)
    assert _hiers(w._rbd)[moved] == ("0", "1")
    w._dirty = False
    w.close()


def test_parallel_paths_without_their_split_and_join_are_refused(
        qapp, warnings):
    """Utility_Grid and Generator_Bank are two paths: two links in, two out."""
    w, v = _window()
    _select(v, ["Utility_Grid", "Generator_Bank"])
    assert v.collapse_selection() is None
    assert warnings and "entered by 2 link(s) and left by 2" in warnings[-1]
    assert "Generator_Bank" in _hiers(w._rbd)
    w._dirty = False
    w.close()


def test_an_oval_alone_enables_collapse(qapp):
    w, v = _window()
    _select(v, ["UPS_System"])
    w._refresh_commands(w._command_state(reuse_validity=True),
                        only={"can_collapse"})
    assert w._collapse_action.isEnabled()
    w._dirty = False
    w.close()


def test_the_api_collapses_a_hierarchy_as_the_canvas_does(qapp, tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.open_model(str(HOSPITAL))["model_id"]
    res = build.collapse_selection(
        ws, mid, ["UPS_System", "Main_Switchboard"], name="Wrapped")
    api = ws.get(mid).rbd
    assert [i for i in issues(api) if i.severity == "error"] == []
    w, v = _window()
    _select(v, ["UPS_System", "Main_Switchboard"])
    hid = v.collapse_selection(name="Wrapped")
    v.write_back_to_rbd(w._rbd)
    assert int(res["ID"]) == hid
    assert _hiers(api) == _hiers(w._rbd)
    links = lambda rbd: sorted(tuple(r[c] for c in ("ID", "FromType", "FromID",  # noqa: E731
                                                     "ToType", "ToID", "pID"))
                               for r in rbd.sections["LINK_DATA"].rows)
    assert links(api) == links(w._rbd)
    w._dirty = False
    w.close()


def test_moving_a_subtree_past_eight_levels_is_refused(qapp, tmp_path,
                                                       warnings):
    """The six-level lantern plus two more is eight deep; wrapping its top
    hierarchy would push the deepest to nine."""
    ws = Workspace(tmp_path)
    mid = ws.open_model(str(SIX_LEVELS))["model_id"]
    h7 = build.add_hierarchy(ws, mid, "H7", parent=22)
    build.add_hierarchy(ws, mid, "H8", parent=int(h7["ID"]))
    with pytest.raises(build.BuildError, match="at most 8 deep"):
        build.collapse_selection(ws, mid, ["Hierarchy_1"], name="Top")
    w = MainWindow()
    w._load_into_app(ws.get(mid).rbd, None)
    v = w._diagram_dock.view
    _select(v, ["Hierarchy_1"])
    assert v.collapse_selection(name="Top") is None
    assert warnings and "at most 8 deep" in warnings[-1]
    w._dirty = False
    w.close()

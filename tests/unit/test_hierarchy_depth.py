"""Hierarchies nest at most `MAX_HIERARCHY_DEPTH` (8) deep, the same limit for
making a level and for entering one.

The canvas refused, silently, to enter a level more than five deep, while
Collapse and Add Hierarchy happily made one: `hierarchy_six_levels.rbds`
(the lantern collapsed six times) had a sixth level that could not be
opened.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.api import build  # noqa: E402
from raptor.api.validate import issues  # noqa: E402
from raptor.api.workspace import Workspace  # noqa: E402
from raptor.core.fileio.model_file import load_model_file  # noqa: E402
from raptor.core.input_rules import (  # noqa: E402
    MAX_HIERARCHY_DEPTH, InputRuleViolation, check, hierarchy_depths)
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.gui
MODEL = Path(__file__).parent / "data" / "hierarchy_six_levels.rbds"
#: The chain of hierarchy ids from the home level down.
CHAIN = (5, 8, 11, 16, 19, 22)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def deep(tmp_path):
    """The lantern with two more levels: eight deep."""
    ws = Workspace(tmp_path)
    mid = ws.open_model(str(MODEL))["model_id"]
    h7 = build.add_hierarchy(ws, mid, "Hierarchy_7", parent=CHAIN[-1])
    h8 = build.add_hierarchy(ws, mid, "Hierarchy_8", parent=int(h7["ID"]))
    return ws, mid, (*CHAIN, int(h7["ID"]), int(h8["ID"]))


def test_depth_is_counted_from_the_parent_chain():
    assert MAX_HIERARCHY_DEPTH == 8
    depths = hierarchy_depths(load_model_file(str(MODEL)))
    assert [depths[h] for h in CHAIN] == [1, 2, 3, 4, 5, 6]


def test_every_existing_level_can_be_entered(qapp):
    w = MainWindow()
    w._load_into_app(load_model_file(str(MODEL)), None)
    v = w._diagram_dock.view
    for hid in CHAIN:
        v.enter_level(hid)
        assert v._current_parent == hid
    w._dirty = False
    w.close()


def test_the_api_refuses_a_ninth_level(deep):
    ws, mid, chain = deep
    with pytest.raises(InputRuleViolation, match="at most 8 deep"):
        build.add_hierarchy(ws, mid, "Hierarchy_9", parent=chain[-1])
    assert max(hierarchy_depths(ws.get(mid).rbd).values()) == 8


def test_at_the_limit_the_commands_are_off_and_say_why(qapp, deep):
    ws, mid, chain = deep
    w = MainWindow()
    w._load_into_app(ws.get(mid).rbd, None)
    v = w._diagram_dock.view
    for hid in chain[:-1]:
        v.enter_level(hid)
    assert w._add_hier_tool.isEnabled()            # depth 7: one more fits
    v.enter_level(chain[-1])
    assert not w._add_hier_tool.isEnabled()
    assert "at most 8 deep" in w._add_hier_tool.toolTip()
    assert not w._collapse_action.isEnabled()
    v.go_home()
    assert w._add_hier_tool.isEnabled()
    w._dirty = False
    w.close()


def test_a_canvas_edit_that_nests_too_deep_is_refused(qapp, deep, monkeypatch):
    """Even past the disabled button (a paste of a hierarchy, here), the
    rule gate refuses a ninth level, copied content included."""
    from raptor.gui.ui import msgbox
    shown = []
    monkeypatch.setattr(msgbox, "warning",
                        lambda _p, title, text, *a, **k: shown.append(text))
    ws, mid, chain = deep
    w = MainWindow()
    w._load_into_app(ws.get(mid).rbd, None)
    v = w._diagram_dock.view
    for hid in chain[:-2]:
        v.enter_level(hid)
    v._scene.clearSelection()
    v._hier_items[chain[-2]].setSelected(True)      # holds Hierarchy_8
    assert v.copy_selection() > 0
    v.enter_level(chain[-2])
    v.enter_level(chain[-1])                        # depth 8
    v.paste(at_model=(10.0, 10.0))
    assert shown and "at most 8 deep" in shown[-1]
    assert max(hierarchy_depths(v._rbd_ref).values()) == 8
    w._dirty = False
    w.close()


def test_import_too_deep_is_refused_with_a_reason(qapp, deep):
    ws, mid, chain = deep
    w = MainWindow()
    w._load_into_app(ws.get(mid).rbd, None)
    v = w._diagram_dock.view
    for hid in chain[:-1]:
        v.enter_level(hid)                         # depth 7
    with pytest.raises(ValueError, match="at most 8 are allowed"):
        v.import_rbd_as_hierarchy(str(MODEL))      # 7 + 1 + 6
    w._dirty = False
    w.close()


def test_a_file_nested_too_deep_is_reported_on_load(deep):
    ws, mid, chain = deep
    rbd = ws.get(mid).rbd
    row = dict(next(r for r in rbd.sections["HIERARCHY_DATA"].rows
                    if r["ID"] == str(chain[-1])))
    row.update(ID="99", Parent=str(chain[-1]), hierarchyName="TooDeep")
    rbd.sections["HIERARCHY_DATA"].rows.append(row)
    assert [v.element for v in check(rbd) if v.rule == "hier.depth"] == [
        "Hier 99 'TooDeep'"]
    assert any("at most 8 deep" in str(i) for i in issues(rbd))


def test_mcp_says_how_many_levels_remain(tmp_path):
    """Adding hierarchies over MCP reports each one's depth and the levels
    left, and the ninth is refused with the limit in the message."""
    import shutil
    from raptor.mcp.server import mcp
    from raptor.mcp.tools_model import add_hierarchy, open_model
    shutil.copy(MODEL, tmp_path / "six.rbds")
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    mid = open_model("six.rbds").structured_content["model_id"]
    seventh = add_hierarchy(mid, "Seventh", parent=CHAIN[-1]).structured_content
    assert (seventh["depth"], seventh["levels_below"]) == (7, 1)
    eighth = add_hierarchy(mid, "Eighth", parent=int(seventh["hier_id"]))
    data = eighth.structured_content
    assert (data["depth"], data["levels_below"]) == (8, 0)
    assert "the deepest allowed" in eighth.content[0].text
    ninth = add_hierarchy(mid, "Ninth", parent=int(data["hier_id"]))
    assert ninth.is_error and "at most 8 deep" in ninth.content[0].text


def test_build_model_refuses_a_spec_nested_too_deep(deep):
    from raptor.api.build import BuildError, build_from_spec
    from raptor.core.fileio.rbds import render_rbds
    ws, mid, chain = deep
    rbd = ws.get(mid).rbd
    assert build_from_spec({"rbds_text": render_rbds(rbd)}) is not None
    row = dict(next(r for r in rbd.sections["HIERARCHY_DATA"].rows
                    if r["ID"] == str(chain[-1])))
    row.update(ID="99", Parent=str(chain[-1]), hierarchyName="TooDeep")
    rbd.sections["HIERARCHY_DATA"].rows.append(row)
    with pytest.raises(BuildError, match="at most 8 deep"):
        build_from_spec({"rbds_text": render_rbds(rbd)})

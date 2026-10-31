"""Nothing moves an element except an explicit Auto Layout (#4 item 15).

Saving, registering and exporting used to re-lay out a model whose spacing
was finer than Raptor 7's grid, which moved hand-placed figures and left
scene-positioned annotations behind. A crowded layout is now a validation
warning, and annotations go with the diagram when Auto Layout does run."""
from __future__ import annotations


import pytest

from raptor.api import build
from raptor.api.annotations import add_annotation, follow_layout, list_annotations, update_annotation
from raptor.api.validate import LAYOUT_WARNING, issues, layout_warning
from raptor.api.workspace import Workspace

SPEC = {"blocks": [{"name": f"B{i}", "x": 2.0 + 0.5 * i, "y": 90.0} for i in range(4)],
        "nodes": [{"name": "s", "type": 1, "x": 1.0, "y": 90.0},
                  {"name": "e", "type": 3, "x": 5.0, "y": 90.0}],
        "links": [{"from": "s", "to": "B0"}, {"from": "B0", "to": "B1"},
                  {"from": "B1", "to": "B2"}, {"from": "B2", "to": "B3"},
                  {"from": "B3", "to": "e"}]}


def _xs(rbd):
    return [r["xPos"] for r in rbd.section("BLOCK_DATA").rows if r["ID"] != "0"]


@pytest.fixture
def crowded(tmp_path):
    ws = Workspace(tmp_path / "ws")
    rbd = build.build_from_spec(SPEC, auto_layout=False)
    xs = _xs(rbd)
    mid = ws.register_model(rbd, name="m")["model_id"]
    return ws, mid, xs


def test_registering_and_saving_keep_hand_placed_positions(crowded):
    ws, mid, xs = crowded
    assert _xs(ws.get(mid).rbd) == xs
    add_annotation(ws, mid, kind="text", text="note", adjacent_to="B2", side="above")
    assert _xs(ws.get(mid).rbd) == xs
    reread = ws._load_any(str(ws.get(mid).path))
    assert _xs(reread) == xs


def test_a_crowded_layout_is_a_validation_warning(crowded):
    ws, mid, _ = crowded
    assert layout_warning(ws.get(mid).rbd) == LAYOUT_WARNING
    assert any(i.element == "layout" and "Auto Layout" in i.message for i in issues(ws.get(mid).rbd))


def test_the_rbd_export_keeps_the_layout_and_says_so(crowded, tmp_path):
    from raptor.core.fileio.rbd import load_rbd
    ws, mid, xs = crowded
    res = ws.save_model_as(mid, str(tmp_path / "ws" / "out.rbd"), fmt="rbd")
    assert res["layout_warning"] == LAYOUT_WARNING
    assert [float(x) for x in _xs(load_rbd(res["path"]))] == [float(x) for x in xs]


def test_cli_convert_prints_the_layout_warning(crowded, tmp_path, capsys):
    from raptor.cli.main import main
    ws, mid, _ = crowded
    src = tmp_path / "in.rbds"
    ws.save_model_as(mid, str(tmp_path / "ws" / "copy.rbds"))
    src.write_text((tmp_path / "ws" / "copy.rbds").read_text())
    assert main(["convert", str(src), str(tmp_path / "out.rbd"), "--allow-lossy"]) == 0
    assert "layout" in capsys.readouterr().err


def test_mcp_auto_layout_moves_anchored_annotations_and_keeps_free_ones(tmp_path):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    from raptor.mcp import context
    from raptor.mcp.tools_model import auto_layout
    ws = context.workspace()
    mid = ws.register_model(build.build_from_spec(SPEC, auto_layout=False), name="m")["model_id"]
    note = add_annotation(ws, mid, kind="text", text="n", adjacent_to="B2", side="above")
    free = add_annotation(ws, mid, kind="rect", x=10.0, y=10.0, w=40.0, h=20.0)
    before = {a["id"]: (a["x"], a["y"]) for a in ws.get(mid).rbd.annotations}
    data = auto_layout(mid).structured_content
    assert data["moved"] and data["annotations_moved"] >= 1
    after = {a["id"]: (a["x"], a["y"]) for a in ws.get(mid).rbd.annotations}
    assert after[note["annotation_id"]] != before[note["annotation_id"]]
    assert after[free["annotation_id"]] == before[free["annotation_id"]]


def test_follow_layout_keeps_a_free_annotation_at_its_model_position():
    from raptor.core.fileio import view_transform
    rbd = build.build_from_spec(SPEC, auto_layout=False)
    old = view_transform.ensure(rbd).as_dict()
    from raptor.api.annotations import place_annotation
    entry = place_annotation(rbd, kind="rect", x=3.0, y=91.0, w=40.0, h=20.0, units="model")
    model_xy = view_transform.ViewTransform.from_dict(old).to_model(entry["x"], entry["y"])
    rbd.view_transform = {**old, "scale": old["scale"] * 2}
    follow_layout(rbd, old)
    now = view_transform.ViewTransform.from_dict(rbd.view_transform).to_model(entry["x"], entry["y"])
    assert now == pytest.approx(model_xy, abs=0.01)


def test_model_units_place_where_an_element_would_be():
    from raptor.api.annotations import place_annotation
    from raptor.core.fileio import view_transform
    rbd = build.build_from_spec(SPEC, auto_layout=False)
    entry = place_annotation(rbd, kind="rect", x=2.5, y=90.0, w=40, h=20, units="model")
    assert (entry["x"], entry["y"]) == pytest.approx(view_transform.ensure(rbd).to_scene(2.5, 90.0), abs=0.01)
    with pytest.raises(Exception, match="units"):
        place_annotation(rbd, kind="rect", x=1, y=1, units="inches")


def test_moving_by_coordinates_or_null_anchor_removes_the_anchor(crowded):
    ws, mid, _ = crowded
    aid = add_annotation(ws, mid, kind="text", text="n", adjacent_to="B1")["annotation_id"]
    update_annotation(ws, mid, aid, {"x": 5.0, "y": 5.0})
    assert "anchor" not in next(a for a in ws.get(mid).rbd.annotations if a["id"] == aid)
    aid2 = add_annotation(ws, mid, kind="text", text="m", adjacent_to="B1")["annotation_id"]
    before = next(a for a in ws.get(mid).rbd.annotations if a["id"] == aid2)
    update_annotation(ws, mid, aid2, {"anchor": None})
    after = next(a for a in ws.get(mid).rbd.annotations if a["id"] == aid2)
    assert "anchor" not in after and (after["x"], after["y"]) == (before["x"], before["y"])


def test_an_anchor_records_the_element_id(crowded):
    ws, mid, _ = crowded
    add_annotation(ws, mid, kind="text", text="n", adjacent_to="B1")
    anchor = list_annotations(ws, mid)["annotations"][0]["anchor"]
    assert anchor["ref"].startswith("Block:") and anchor["target"] == "B1"

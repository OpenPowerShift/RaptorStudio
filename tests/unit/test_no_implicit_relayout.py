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
    add_annotation(ws, mid, kind="text", text="note", adjacent_to="B2", location="above")
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
    note = add_annotation(ws, mid, kind="text", text="n", adjacent_to="B2", location="above")
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


def test_real_auto_layout_moves_a_chain_consistently_and_is_stable(tmp_path):
    """End to end through the MCP tool: a block note, a note anchored to
    that note and an arrow between two blocks end up clear of every
    element and of each other, the arrow on its targets, a second layout changes nothing, and a rename afterwards keeps the
    stored names in step."""
    from raptor.api.annotations import _anchor_for, refresh_anchor_names
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    from raptor.mcp import context
    from raptor.mcp.tools_model import auto_layout
    ws = context.workspace()
    mid = ws.register_model(build.build_from_spec(SPEC, auto_layout=False), name="m")["model_id"]
    first = add_annotation(ws, mid, kind="text", text="first", adjacent_to="B2",
                           location="above")
    second = add_annotation(ws, mid, kind="text", text="second",
                            adjacent_to=f"Annotation:{first['annotation_id']}",
                            location="below")
    arrow = add_annotation(ws, mid, kind="arrow", from_="B0", to="B3")
    rbd = ws.get(mid).rbd

    def by_id(i):
        return next(a for a in rbd.annotations if a["id"] == i)

    def check():
        from raptor.api.annotations import _element_boxes
        boxes = {}
        for aid in (first["annotation_id"], second["annotation_id"]):
            note = by_id(aid)
            boxes[aid] = (note["x"], note["y"], note["x"] + note["w"], note["y"] + note["h"])
            own = note["anchor"]["ref"]
            for _parent, ref, l, t, r, btm in _element_boxes(rbd):
                if ref == own:
                    continue
                assert not (boxes[aid][0] < r and boxes[aid][2] > l
                            and boxes[aid][1] < btm and boxes[aid][3] > t), \
                    f"note {aid} overlaps {ref}"
        f, g = boxes[first["annotation_id"]], boxes[second["annotation_id"]]
        assert not (f[0] < g[2] and f[2] > g[0] and f[1] < g[3] and f[3] > g[1])
        a = by_id(arrow["annotation_id"])
        tail = _anchor_for(rbd, a["anchor"]["from_ref"])
        head = _anchor_for(rbd, a["anchor"]["to_ref"])
        # The arrow stops at each target's outline, not at its centre.
        from raptor.core.annotations import arrow_points
        want = arrow_points(a, tail, head)
        assert (a["x1"], a["y1"], a["x2"], a["y2"]) == pytest.approx(
            (want["x1"], want["y1"], want["x2"], want["y2"]))
        assert (a["x1"], a["y1"]) != (round(tail["cx"], 2), round(tail["cy"], 2))

    assert auto_layout(mid).structured_content["moved"]
    check()
    snapshot = [(a["id"], a.get("x"), a.get("y"), a.get("x2"), a.get("y2"))
                for a in rbd.annotations]
    auto_layout(mid)
    check()
    assert snapshot == [(a["id"], a.get("x"), a.get("y"), a.get("x2"), a.get("y2"))
                        for a in rbd.annotations]

    row = next(r for r in rbd.section("BLOCK_DATA").rows if r["blockName"] == "B2")
    row["blockName"] = "B2_renamed"
    refresh_anchor_names(rbd)
    assert by_id(first["annotation_id"])["anchor"]["target"] == "B2_renamed"
    auto_layout(mid)
    check()


def test_mcp_auto_layout_makes_room_for_notes_between_stacked_blocks(tmp_path):
    """Two blocks in a 2-of-2 split, two chained notes above the lower one:
    after Auto Layout both notes sit clear of the block above, not on it."""
    from raptor.api.annotations import _element_boxes
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    from raptor.mcp import context
    from raptor.mcp.tools_model import auto_layout
    spec = {"blocks": [{"name": "A", "x": 2.0, "y": 80.0}, {"name": "B", "x": 2.0, "y": 70.0}],
            "nodes": [{"name": "s", "type": 1, "x": 1.0, "y": 75.0},
                      {"name": "j", "type": 2, "n": 2, "k": 2, "x": 3.0, "y": 75.0},
                      {"name": "e", "type": 3, "x": 4.0, "y": 75.0}],
            "links": [{"from": "s", "to": "A"}, {"from": "s", "to": "B"},
                      {"from": "A", "to": "j"}, {"from": "B", "to": "j"},
                      {"from": "j", "to": "e"}]}
    ws = context.workspace()
    mid = ws.register_model(build.build_from_spec(spec, auto_layout=False), name="m")["model_id"]
    rbd = ws.get(mid).rbd
    auto_layout(mid)                      # decide which block is lower
    ys = {r["blockName"]: float(r["yPos"]) for r in rbd.section("BLOCK_DATA").rows if r["ID"] != "0"}
    lower = min(ys, key=ys.get)
    one = add_annotation(ws, mid, kind="text", text="one", adjacent_to=lower, location="above")
    add_annotation(ws, mid, kind="text", text="two", location="above",
                         adjacent_to=f"Annotation:{one['annotation_id']}")
    # Squeeze them together again, then lay out with the notes in place.
    for r in rbd.section("BLOCK_DATA").rows:
        if r["ID"] != "0":
            r["yPos"] = "81.250000" if r["blockName"] != lower else "78.750000"
    data = auto_layout(mid).structured_content
    assert data["moved"]
    for note in rbd.annotations:
        for _p, ref, l, t, r, b in _element_boxes(rbd):
            if ref == note["anchor"]["ref"]:
                continue
            assert not (note["x"] < r and note["x"] + note["w"] > l
                        and note["y"] < b and note["y"] + note["h"] > t), (note["text"], ref)

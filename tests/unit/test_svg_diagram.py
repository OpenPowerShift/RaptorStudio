"""The Qt-free SVG drawing (`raptor.render.svg`), its MCP tool and `raptor render x.svg`.

The drawing is checked as data: it parses as XML, has one group per element
of the level drawn, one line per link between them, and puts each element at
the scene position the GUI uses (`view_transform`)."""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from raptor.core.fileio import view_transform
from raptor.core.fileio.rbd import load_rbd
from raptor.render.svg import SvgRenderError, render_svg

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "tests" / "golden"
NS = {"svg": "http://www.w3.org/2000/svg"}
FIXTURES = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(str(GOLD / "*" / "input.rbd")))

_SECTIONS = {"block": ("BLOCK_DATA", "ParentId"), "node": ("NODE_DATA", "parent"),
             "event": ("EVENT_DATA", "ParentId"), "hier": ("HIERARCHY_DATA", "Parent")}


def _level_ids(rbd, level):
    out = {}
    for kind, (section, parent_col) in _SECTIONS.items():
        sec = rbd.sections.get(section)
        out[kind] = {int(r["ID"]) for r in (sec.rows if sec else [])
                     if r.get("ID") != "0" and int(float(r.get(parent_col) or 0)) == level}
    return out


def _groups(svg):
    root = ET.fromstring(svg)
    return {g.get("id") for g in root.iter("{http://www.w3.org/2000/svg}g") if g.get("id")}


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_level_of_every_golden_fixture_draws_each_element(fixture):
    rbd = load_rbd(str(GOLD / fixture / "input.rbd"))
    hiers = rbd.sections.get("HIERARCHY_DATA")
    levels = [0] + [int(r["ID"]) for r in (hiers.rows if hiers else []) if r.get("ID") != "0"]
    for level in levels:
        svg, meta = render_svg(rbd, level=level)
        expected = {f"{kind}-{i}" for kind, ids in _level_ids(rbd, level).items() for i in ids}
        assert _groups(svg) == expected, (fixture, level)
        assert meta["elements_drawn"] == len(expected)
        lines = ET.fromstring(svg).find("svg:g[@class='links']", NS)
        assert len(list(lines)) == meta["links_drawn"]


def test_elements_sit_at_the_gui_scene_position():
    rbd = load_rbd(str(GOLD / "FactoryDemo" / "input.rbd"))
    vt = view_transform.ensure(rbd)
    svg, _ = render_svg(rbd)
    root = ET.fromstring(svg)
    for row in rbd.sections["BLOCK_DATA"].rows:
        if row["ID"] == "0":
            continue
        rect = root.find(f".//svg:g[@id='block-{row['ID']}']/svg:rect", NS)
        cx = float(rect.get("x")) + view_transform.BLOCK_W / 2
        cy = float(rect.get("y")) + view_transform.BLOCK_H / 2
        assert cx == pytest.approx(vt.x_to_scene(float(row["xPos"])), abs=0.06)
        assert cy == pytest.approx(vt.y_to_scene(float(row["yPos"])), abs=0.06)


def test_zoom_frames_and_highlight_outlines_the_selected_element():
    rbd = load_rbd(str(GOLD / "FactoryDemo" / "input.rbd"))
    _, whole = render_svg(rbd)
    svg, meta = render_svg(rbd, zoom_to=["ArcticAir_10000"], highlight=["ArcticAir_10000"])
    assert meta["width"] < whole["width"] and meta["highlighted"] == ["ArcticAir_10000"]
    assert meta["elements_in_view"] < whole["elements_in_view"]
    assert ET.fromstring(svg).find("svg:g[@class='highlights']", NS) is not None


@pytest.mark.parametrize("kwargs, message", [
    ({"level": 12345}, "not 0 or a hierarchy id"),
    ({"highlight": ["NoSuchBlock"]}, "highlight selector"),
    ({"theme": "neon"}, "theme"),
])
def test_bad_arguments_are_reported(kwargs, message):
    rbd = load_rbd(str(GOLD / "FactoryDemo" / "input.rbd"))
    with pytest.raises(SvgRenderError, match=message):
        render_svg(rbd, **kwargs)


def test_annotations_of_every_kind_are_drawn(tmp_path):
    from raptor.api.annotations import add_annotation
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "FactoryDemo" / "input.rbd")["model_id"]
    add_annotation(ws, mid, kind="text", text="Check <this> & that", adjacent_to="ArcticAir_10000")
    add_annotation(ws, mid, kind="rect", adjacent_to="XR-15_15000", border_colour="#E03131", border_width=2)
    add_annotation(ws, mid, kind="ellipse", x=10.0, y=10.0, w=60.0, h=30.0, fill_colour="#FFEC99")
    add_annotation(ws, mid, kind="arrow", from_="start", to="end")
    svg, meta = render_svg(ws.get(mid).rbd)
    assert meta["annotations_drawn"] == 4
    group = ET.fromstring(svg).find("svg:g[@class='annotations']", NS)
    tags = [child.tag.split("}")[1] for child in group]
    assert sorted(tags) == ["ellipse", "g", "line", "rect"]
    assert "Check &lt;this&gt; &amp; that" in svg
    _, bare = render_svg(ws.get(mid).rbd, include_annotations=False)
    assert bare["annotations_drawn"] == 0


def test_drawing_does_not_load_qt():
    code = ("import sys; from raptor.core.fileio.rbd import load_rbd; "
            "from raptor.render.svg import render_svg; "
            f"render_svg(load_rbd({str(GOLD / 'Phasing3' / 'input.rbd')!r})); "
            "print('PySide6' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, check=True)
    assert out.stdout.strip() == "False"


def test_the_mcp_tool_returns_the_svg_and_writes_the_file(tmp_path):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_svg import render_diagram_svg
    mid = open_model("FactoryDemo").structured_content["model_id"]
    result = render_diagram_svg(mid, highlight=["ArcticAir_10000"])
    data = result.structured_content
    assert data["inline"] and Path(data["svg_path"]).read_text().startswith("<svg")
    assert "```svg" in result.content[0].text
    bad = render_diagram_svg(mid, level=999).structured_content
    assert bad["error"]["code"] == "invalid_argument"


def test_raptor_render_writes_an_svg(tmp_path, capsys):
    from raptor.cli.main import main
    out = tmp_path / "d.svg"
    assert main(["render", str(GOLD / "FactoryDemo" / "input.rbd"), "-o", str(out)]) == 0
    assert ET.fromstring(out.read_text()).tag.endswith("svg")


# ---- result layers -------------------------------------------------------------

PROBE32 = ROOT / "tests" / "fixtures" / "ReleaseProbe32" / "input.rbd"


def _layer_run(tmp_path, layer: str, trials: int = 6):
    """ReleaseProbe32 with capacity analysis on, run with attribution."""
    from raptor.api.insight.canvas import run_layer
    from raptor.api.run import run
    from raptor.api.workspace import Workspace
    from raptor.mcp.server import mcp
    ws = Workspace((tmp_path / "ws").resolve())
    mid = ws.register_model(load_rbd(str(PROBE32)), name="p32")["model_id"]
    result = run(ws, mid, num_runs=trials, attribution=True, confirm_long_run=True)
    # the MCP tools use this same workspace (context.workspace keeps it
    # while its root matches)
    mcp._raptor_args = type("A", (), {"workspace": str(ws.root)})()
    mcp._raptor_ws = ws
    return ws, mid, result.run_id, run_layer(ws, result.run_id, layer)


def test_the_downtime_layer_marks_rolls_up_and_explains(tmp_path):
    ws, mid, _run_id, (layer, flow) = _layer_run(tmp_path, "downtime-share")
    assert flow is None
    svg, meta = render_svg(ws.get(mid).rbd, layer=layer)
    root = ET.fromstring(svg)
    marks = root.findall(".//svg:g[@class='result-layer']/svg:g[@class='mark']", NS)
    assert meta["layer"]["elements_marked"] == len(marks) > 0
    texts = ["".join(t.itertext()) for t in root.iter(f"{{{NS["svg"]}}}text")]
    assert any("Σ " in t for t in texts) == (meta["layer"]["hierarchies_rolled_up"] > 0)
    assert any(t.startswith("Colour: share of system downtime") for t in texts)
    # the largest share is filled with the "high" band
    top = layer["elements"][0]
    group = root.find(f".//svg:g[@id='{top['kind']}-{top['id']}']", NS)
    if group is not None:
        shape = next(iter(group.findall("svg:rect", NS) + group.findall("svg:polygon", NS)))
        assert shape.get("fill") == "#FFC9C9"
    # no layer, no marks: the drawing is unchanged
    plain, plain_meta = render_svg(ws.get(mid).rbd)
    assert "result-layer" not in plain and "layer" not in plain_meta


def test_the_lost_capacity_layer_widens_links_by_node_flow(tmp_path):
    ws, mid, _run_id, (layer, flow) = _layer_run(tmp_path, "lost-capacity")
    assert flow and all(value >= 0 for value in flow.values())
    svg, meta = render_svg(ws.get(mid).rbd, layer=layer, flow=flow)
    widths = {float(e.get("stroke-width")) for e in ET.fromstring(svg).iter()
              if e.get("marker-end") == "url(#arrow-fixed)"}
    assert max(widths) == pytest.approx(10.0) and min(widths) == pytest.approx(1.0)
    assert meta["layer"]["links_by_flow"] is True
    assert "Link width: mean flow" in svg


def test_node_flows_are_stored_with_the_run(tmp_path):
    import json
    from raptor.api.run import run_dir
    ws, _mid, run_id, _ = _layer_run(tmp_path, "downtime-share", trials=3)
    stored = json.loads((run_dir(ws, run_id) / "node_flow.json").read_text())
    assert stored["generated"] > 0 and str(stored["system_node"]) in stored["nodes"]


def test_the_mcp_tool_draws_a_run_layer_and_refuses_a_layer_without_a_run(tmp_path):
    from raptor.mcp.tools_svg import render_diagram_svg
    _ws, mid, run_id, _ = _layer_run(tmp_path, "downtime-share", trials=3)
    result = render_diagram_svg(mid, run_id=run_id, layer="lost-capacity")
    assert not result.is_error
    assert result.structured_content["layer"]["links_by_flow"] is True
    refused = render_diagram_svg(mid, layer="downtime-share")
    assert refused.structured_content["error"]


def test_raptor_render_draws_a_layer(tmp_path, capsys):
    from raptor.cli.main import main
    out = tmp_path / "layer.svg"
    code = main(["render", str(PROBE32), "-o", str(out), "--layer", "downtime-share",
                 "-n", "3"])
    assert code == 0 and "result-layer" in out.read_text()
    assert main(["render", str(PROBE32), "-o", str(tmp_path / "x.png"),
                 "--layer", "downtime-share"]) != 0

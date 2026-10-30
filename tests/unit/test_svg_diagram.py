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

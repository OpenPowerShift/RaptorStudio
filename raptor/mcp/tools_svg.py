"""MCP tools, part 3b: a Qt-free SVG drawing of the diagram.

Registers ``render_diagram_svg``. Unlike ``render_diagram`` (a PNG from the
GUI's canvas, which needs PySide6 and a separate worker process), it draws
in-process from the model alone, so it is available on a headless install.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import READ_ONLY, guarded, ok
from raptor.mcp.server import mcp

#: Largest SVG returned inline in the tool result; a bigger one is only
#: written to renders/ (draw a hierarchy level or use zoom_to instead).
INLINE_SVG_BYTES = 200_000


@mcp.tool(annotations=READ_ONLY("Render diagram as SVG"))
@guarded
def render_diagram_svg(
    model_id: str,
    level: int = 0,
    zoom_to: list[str] | None = None,
    margin: float = 12.0,
    highlight: list[str] | None = None,
    theme: Literal["light", "dark"] = "light",
    include_annotations: bool = True,
) -> CallToolResult:
    """Draw one level of the diagram as SVG text, without Qt.

    For checking layout, connections and annotations when `render_diagram`
    (a PNG of the GUI canvas) is unavailable, or when SVG text is easier to
    read than an image. It draws blocks, nodes (S/E, HI/HO, k/n), events,
    hierarchy ovals, links with arrowheads and annotations at the GUI's
    scene positions; it does not measure fonts (long names are shortened,
    the full name is in each element's `<title>`) or show state colours.

    `level` is 0 for the top level or a **hierarchy id** (not a depth).
    `zoom_to` frames the named elements on that level, padded by `margin`
    scene units; `highlight` outlines them. A selector is a name, a bare id
    or `"Kind:id"`.

    Returns the SVG in the text result (up to 200 KB; larger drawings are
    only written to the file) and structured {svg_path, width, height,
    elements_drawn, elements_in_view, links_drawn, annotations_drawn,
    highlighted, inline}. The file is written to renders/ in the workspace.
    """
    ws = context.workspace()
    handle = ws.get(model_id)

    from raptor.api.run import model_hash as _model_hash
    from raptor.render.svg import render_svg

    svg, meta = render_svg(handle.rbd, level=level, highlight=highlight or [],
                           zoom_to=zoom_to or [], margin=margin, theme=theme,
                           include_annotations=include_annotations)
    digest = hashlib.sha256(
        f"svg|{_model_hash(handle.rbd)}|{level}|{theme}|{margin}|"
        f"{include_annotations}|{','.join(sorted(highlight or []))}|"
        f"{','.join(sorted(zoom_to or []))}".encode()).hexdigest()[:12]
    render_dir = ws.root / "renders"
    render_dir.mkdir(parents=True, exist_ok=True)
    path = render_dir / f"{digest}.svg"
    path.write_text(svg, encoding="utf-8")

    inline = len(svg.encode("utf-8")) <= INLINE_SVG_BYTES
    data = {"svg_path": str(path), **meta, "zoom_to": list(zoom_to or []),
            "inline": inline, "resource_link": f"file://{path}"}
    md = (f"SVG of **{handle.name}** level {level}: {meta['width']}×{meta['height']} "
          f"scene px, {meta['elements_in_view']} element(s) in view, "
          f"{meta['links_drawn']} link(s). File: `{path}`.")
    if inline:
        md += "\n\n```svg\n" + svg + "```"
    else:
        md += ("\n\nToo large to return inline; draw one hierarchy level, or "
               "frame part of this one with `zoom_to`.")
    return ok(data, markdown=md)

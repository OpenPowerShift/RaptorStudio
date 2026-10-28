"""MCP tools, part 3: diagram rendering.

Registers ``render_diagram`` — the **only** tool that returns an image.
It goes through the separate render-worker process, never in-process, and
is imported only when rendering is available (otherwise it simply does not
appear in ``tools/list``).
"""
from __future__ import annotations

import hashlib
from typing import Literal

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import READ_ONLY, guarded, image
from raptor.mcp.server import mcp


@mcp.tool(annotations=READ_ONLY("Render diagram"))
@guarded
def render_diagram(
    model_id: str,
    level: int = 0,
    zoom_to: list[str] | None = None,
    margin: float = 12.0,
    highlight: list[str] | None = None,
    theme: Literal["light", "dark"] = "light",
    max_px: int = 1600,
    include_annotations: bool = True,
) -> CallToolResult:
    """Render the model diagram as a PNG image.

    `level` is a **hierarchy id**, not a depth index: 0 renders the whole
    model (home level); to render inside a hierarchy pass its id, e.g. the
    hierarchy `id=900` is `level=900` (read hierarchy ids from
    `describe_model`).  Passing `1`/`2` is a common mistake and reports
    the available ids.

    `zoom_to` frames a **close-up** on one or more named/id'd elements
    (block/node/event/hierarchy) **within the current `level`** (it does
    not change which level is shown), padded by `margin` scene units — the
    way to render a large model legibly in sections.  A selector is a
    name, a bare id, or `"Kind:id"` (e.g. `"Block:3"`); a selector that
    matches nothing, or a bare id matching two element kinds, is an error.
    The structured result carries `px_per_unit`, `effective_label_px` and
    `legible` (a heuristic: `effective_label_px >= 7`), so a caller can
    tell whether the text is readable and zoom in further if not.

    Returns image content plus structured {width, height, elements_drawn,
    elements_in_view, px_per_unit, effective_label_px, legible, png_path,
    resource_link}.  `highlight` outlines named elements (e.g. the single
    points of failure).  `theme` is 'light' or 'dark'.
    `include_annotations` draws the model's text/rect/ellipse/arrow
    annotations (default true).

    This is the only RAPTOR tool that returns a picture: results are always
    Markdown/data.  The PNG is capped at 1600 px on the long edge and is
    also written to renders/ in the workspace.
    """
    ws = context.workspace()
    handle = ws.get(model_id)

    from raptor.api.render import render_diagram as _render
    from raptor.api.run import model_hash as _model_hash

    # Content-addressed: any edit to the model (or a different render
    # option) yields a different filename, so a render can never be stale.
    content_hash = _model_hash(handle.rbd)
    # Order-insensitive (sets, not lists) and covering EVERY render option
    # including include_annotations, so no two distinct renders collide.
    digest = hashlib.sha256(
        f"{content_hash}|{level}|{theme}|{max_px}|{margin}|"
        f"{include_annotations}|"
        f"{','.join(sorted(highlight or []))}|"
        f"{','.join(sorted(zoom_to or []))}".encode()
    ).hexdigest()[:12]
    render_dir = ws.root / "renders"
    render_dir.mkdir(parents=True, exist_ok=True)
    png_path = render_dir / f"{digest}.png"

    rendered_path, meta = _render(
        handle.effective_path,
        png_path,
        level=level,
        zoom_to=zoom_to or [],
        margin=margin,
        highlight=highlight or [],
        theme=theme,
        max_px=max_px,
        include_annotations=include_annotations,
    )

    from pathlib import Path
    png = Path(rendered_path).read_bytes()
    if len(png) > 1.5 * 1024 * 1024:
        raise ValueError(
            f"rendered image is {len(png) / 1024 / 1024:.1f} MB, over the "
            "1.5 MB cap; lower max_px."
        )

    data = {
        "png_path": str(rendered_path),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "elements_drawn": meta.get("elements_drawn"),
        "elements_in_view": meta.get("elements_in_view"),
        "px_per_unit": meta.get("px_per_unit"),
        "effective_label_px": meta.get("effective_label_px"),
        "legible": meta.get("legible"),
        "zoom_to": list(zoom_to or []),
        "highlighted": meta.get("highlighted"),
        "resource_link": f"file://{rendered_path}",
    }
    md = (
        f"Diagram of **{handle.name}** — {data['width']}×{data['height']} px, "
        f"{data['elements_in_view']} element(s) in view. "
        f"Label text ≈ {data['effective_label_px']} px "
        f"({'legible' if data['legible'] else 'NOT legible — zoom in on a '
           'subsystem with zoom_to'}). Full-resolution PNG: "
        f"`{data['png_path']}`."
    )
    return image(png, md, data)

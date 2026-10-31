"""A plain SVG drawing of one diagram level, without Qt.

The PNG renderer (`raptor.render.worker`) builds the GUI's own canvas, so it
needs PySide6. This module draws the same level from the model alone, for a
headless install or an assistant that reads SVG text: blocks, nodes, events,
hierarchies, links and annotations, at the positions the GUI uses (the shared
`view_transform` and the GUI's element sizes). It is a sketch for checking
layout and connections, not a copy of the canvas: no fonts are measured, long
names are shortened (the full name is in each element's `<title>`), and no
state colours or badges are drawn.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from raptor.core.fileio import view_transform
from raptor.core.fileio.typed import block_from_row, event_from_row, hier_from_row, link_from_row, node_from_row

#: Hierarchy oval and event hexagon sizes, as the GUI draws them
#: (`diagram_items._HierItem.W/H`, `_EventItem`'s `NODE_R + 2`).
HIER_W, HIER_H = 150.0, 56.0
EVENT_R = view_transform.NODE_R + 2.0
#: Characters of a name drawn inside a block or hierarchy.
_NAME_CHARS = {"block": 16, "hier": 20}

_THEMES = {
    "light": {"bg": "#FFFFFF", "fill": "#F4F6F8", "stroke": "#2F4858", "text": "#132238",
              "hier": "#E3ECF4", "link": "#5D7080", "highlight": "#E8590C",
              "start": "#2FB344", "end": "#E03131"},
    "dark": {"bg": "#1B1F24", "fill": "#2A3038", "stroke": "#A9B8C6", "text": "#E6EDF3",
             "hier": "#24313F", "link": "#8FA3B5", "highlight": "#FF922B",
             "start": "#2FB344", "end": "#E03131"},
}

_NODE_LABEL = {1: "S", 3: "E", 4: "HI", 5: "HO"}
_LINE_DASH = {"dash": "8 4", "dot": "2 3", "dash-dot": "8 3 2 3", "dash-dot-dot": "8 3 2 3 2 3"}


class SvgRenderError(ValueError):
    """A level or selector that does not exist in the model."""


@dataclass
class _Shape:
    kind: str       # "block" | "node" | "event" | "hier"
    id: int
    name: str
    cx: float
    cy: float
    label: str = ""

    @property
    def half(self) -> tuple[float, float]:
        if self.kind == "block":
            return view_transform.BLOCK_W / 2.0, view_transform.BLOCK_H / 2.0
        if self.kind == "hier":
            return HIER_W / 2.0, HIER_H / 2.0
        if self.kind == "event":
            return EVENT_R, EVENT_R
        return float(view_transform.NODE_R), float(view_transform.NODE_R)

    def bounds(self) -> tuple[float, float, float, float]:
        """The outline's box, plus the name drawn above a node or below an event."""
        hw, hh = self.half
        top = self.cy - hh - (16.0 if self.kind == "node" and self.name else 0.0)
        bottom = self.cy + hh + (14.0 if self.kind == "event" and self.name else 0.0)
        return self.cx - hw, top, self.cx + hw, bottom

    def port(self, tx: float, ty: float) -> tuple[float, float]:
        """Where a line towards `(tx, ty)` leaves this shape's outline."""
        dx, dy = tx - self.cx, ty - self.cy
        if dx == 0 and dy == 0:
            return self.cx, self.cy
        hw, hh = self.half
        if self.kind == "block":
            t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
        elif self.kind == "hier":
            t = 1.0 / math.hypot(dx / hw, dy / hh)
        else:
            t = hw / math.hypot(dx, dy)
        return self.cx + dx * t, self.cy + dy * t


def _rows(rbd: Any, section: str) -> list[dict]:
    sec = rbd.sections.get(section)
    return [r for r in (sec.rows if sec is not None else []) if str(r.get("ID", "0")) != "0"]


def _shorten(name: str, limit: int) -> str:
    return name if len(name) <= limit else name[:limit - 1] + "…"


def _node_shapes(rbd: Any, level: int, vt: view_transform.ViewTransform) -> list[_Shape]:
    incoming: dict[int, int] = {}
    for row in _rows(rbd, "LINK_DATA"):
        link = link_from_row(row)
        if link.connect_t_ref.endswith("Node"):
            incoming[link.connect_to_id] = incoming.get(link.connect_to_id, 0) + 1
    shapes = []
    for row in _rows(rbd, "NODE_DATA"):
        n = node_from_row(row)
        if n.parent_id != level:
            continue
        label = _NODE_LABEL.get(n.type_node, "")
        n_in = incoming.get(n.id, 0)
        if n.type_node == 2 and n_in > 1 and 1 <= n.good_paths <= n_in:
            label = f"{n.good_paths}/{n_in}"   # the canvas's k-of-n badge
        shapes.append(_Shape("node", n.id, n.node_name or "", vt.x_to_scene(n.x_position),
                             vt.y_to_scene(n.y_position), label))
    return shapes


def _shapes(rbd: Any, level: int, vt: view_transform.ViewTransform) -> list[_Shape]:
    shapes: list[_Shape] = []
    for row in _rows(rbd, "BLOCK_DATA"):
        b = block_from_row(row)
        if b.parent_id == level:
            shapes.append(_Shape("block", b.id, b.name, vt.x_to_scene(b.x_position), vt.y_to_scene(b.y_position)))
    shapes += _node_shapes(rbd, level, vt)
    for row in _rows(rbd, "EVENT_DATA"):
        e = event_from_row(row)
        if e.parent_id == level:
            shapes.append(_Shape("event", e.id, e.name, vt.x_to_scene(e.x_position), vt.y_to_scene(e.y_position)))
    for row in _rows(rbd, "HIERARCHY_DATA"):
        h = hier_from_row(row)
        if h.parent_id == level:
            shapes.append(_Shape("hier", h.id, h.name, vt.x_to_scene(h.x_position), vt.y_to_scene(h.y_position)))
    return shapes


_KIND_OF_REF = {"Block": "block", "Node": "node", "Event": "event", "Hier": "hier"}


def _links(rbd: Any, by_key: dict[tuple[str, int], _Shape]) -> list[tuple[_Shape, _Shape]]:
    out = []
    for row in _rows(rbd, "LINK_DATA"):
        link = link_from_row(row)
        src = by_key.get((_KIND_OF_REF.get(link.connect_f_ref.replace("RBD", "", 1), "node"), link.connect_from_id))
        dst = by_key.get((_KIND_OF_REF.get(link.connect_t_ref.replace("RBD", "", 1), "node"), link.connect_to_id))
        if src is not None and dst is not None:
            out.append((src, dst))
    return out


_SECTION_KIND = {"BLOCK_DATA": "block", "NODE_DATA": "node", "EVENT_DATA": "event", "HIERARCHY_DATA": "hier"}


def _resolve(rbd: Any, selectors: list[Any], by_key: dict[tuple[str, int], _Shape],
             what: str) -> list[_Shape]:
    from raptor.api.build import BuildError, resolve_element
    found = []
    for sel in selectors:
        try:
            section, row = resolve_element(rbd, str(sel))
        except BuildError as exc:
            raise SvgRenderError(f"{what} selector {sel!r}: {exc}") from exc
        shape = by_key.get((_SECTION_KIND.get(section, ""), int(float(row.get("ID", 0)))))
        if shape is None:
            raise SvgRenderError(f"{what} selector {sel!r} is not on the level being drawn")
        found.append(shape)
    return found


def _levels(rbd: Any) -> list[int]:
    return [0] + sorted(hier_from_row(r).id for r in _rows(rbd, "HIERARCHY_DATA"))


def _frame(boxes: list[tuple[float, float, float, float]],
           margin: float) -> tuple[float, float, float, float]:
    """`(x, y, width, height)` around `boxes`, padded by `margin`."""
    if not boxes:
        return 0.0, 0.0, 200.0, 100.0
    x0 = min(b[0] for b in boxes) - margin
    y0 = min(b[1] for b in boxes) - margin
    x1 = max(b[2] for b in boxes) + margin
    y1 = max(b[3] for b in boxes) + margin
    return x0, y0, max(x1 - x0, 1.0), max(y1 - y0, 1.0)


def render_svg(rbd: Any, *, level: int = 0, highlight: list[Any] | None = None,
               zoom_to: list[Any] | None = None, margin: float = 12.0, theme: str = "light",
               include_annotations: bool = True) -> tuple[str, dict[str, Any]]:
    """Draw one level of `rbd` as SVG. Returns `(svg_text, meta)`.

    `level` is 0 for the top level or a hierarchy id, as for the PNG
    renderer. `zoom_to` frames the named elements, padded by `margin` scene
    units; `highlight` outlines them. Coordinates are the GUI's scene
    pixels, so annotation positions match the canvas."""
    if theme not in _THEMES:
        raise SvgRenderError(f"theme must be 'light' or 'dark', got {theme!r}")
    if level not in _levels(rbd):
        raise SvgRenderError(f"level {level} is not 0 or a hierarchy id; available: {_levels(rbd)}")
    if not math.isfinite(float(margin)) or margin < 0:
        raise SvgRenderError(f"margin must be a finite value >= 0, got {margin}")
    c = _THEMES[theme]
    vt = view_transform.ensure(rbd)
    shapes = _shapes(rbd, level, vt)
    by_key = {(s.kind, s.id): s for s in shapes}
    links = _links(rbd, by_key)
    lit = _resolve(rbd, list(highlight or []), by_key, "highlight")
    zoomed = _resolve(rbd, list(zoom_to or []), by_key, "zoom_to")
    annos = ([a for a in (getattr(rbd, "annotations", None) or [])
              if int(a.get("parent", 0) or 0) == level] if include_annotations else [])

    x0, y0, w, h = _frame([s.bounds() for s in (zoomed or shapes)]
                          + ([] if zoomed else [_anno_bounds(a) for a in annos]), margin)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}" '
           f'width="{w:.0f}" height="{h:.0f}" font-family="sans-serif">',
           f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" '
           f'markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
           f'fill="{c["link"]}"/></marker></defs>',
           f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{c["bg"]}"/>']
    out.append('<g class="links">')
    for src, dst in links:
        ax, ay = src.port(dst.cx, dst.cy)
        bx, by = dst.port(src.cx, src.cy)
        out.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{c["link"]}" '
                   f'stroke-width="1.5" marker-end="url(#arrow)"/>')
    out.append("</g>")
    out.append('<g class="elements">')
    for s in shapes:
        out.append(_shape_svg(s, c))
    out.append("</g>")
    if lit:
        out.append('<g class="highlights">')
        for s in lit:
            bx0, by0, bx1, by1 = s.bounds()
            out.append(f'<rect x="{bx0 - 4:.1f}" y="{by0 - 4:.1f}" width="{bx1 - bx0 + 8:.1f}" '
                       f'height="{by1 - by0 + 8:.1f}" fill="none" stroke="{c["highlight"]}" stroke-width="3"/>')
        out.append("</g>")
    if annos:
        out.append('<g class="annotations">')
        for a in sorted(annos, key=lambda a: a.get("z", 0) or 0):
            out.append(_anno_svg(a, c))
        out.append("</g>")
    out.append("</svg>")

    in_view = sum(1 for s in shapes
                  if s.cx >= x0 and s.cx <= x0 + w and s.cy >= y0 and s.cy <= y0 + h)
    meta = {
        "renderer": "svg", "level": level, "width": round(w), "height": round(h),
        "elements_drawn": len(shapes), "elements_in_view": in_view, "links_drawn": len(links),
        "annotations_drawn": len(annos), "highlighted": [s.name or f"{s.kind}:{s.id}" for s in lit],
    }
    return "\n".join(out) + "\n", meta


def _title(s: _Shape) -> str:
    kind = {"block": "Block", "node": "Node", "event": "Event", "hier": "Hierarchy"}[s.kind]
    return f"<title>{escape(f'{kind} {s.id}' + (f' {s.name}' if s.name else ''))}</title>"


def _shape_svg(s: _Shape, c: dict[str, str]) -> str:
    attrs = f'stroke="{c["stroke"]}" stroke-width="1.5"'
    text = f'fill="{c["text"]}" text-anchor="middle" dominant-baseline="central"'
    if s.kind == "block":
        x0, y0, _x1, _y1 = s.bounds()
        return (f'<g id="block-{s.id}">{_title(s)}<rect x="{x0:.1f}" y="{y0:.1f}" '
                f'width="{view_transform.BLOCK_W}" height="{view_transform.BLOCK_H}" rx="3" fill="{c["fill"]}" {attrs}/>'
                f'<text x="{s.cx:.1f}" y="{s.cy:.1f}" font-size="10" {text}>'
                f'{escape(_shorten(s.name, _NAME_CHARS["block"]))}</text></g>')
    if s.kind == "hier":
        return (f'<g id="hier-{s.id}">{_title(s)}<ellipse cx="{s.cx:.1f}" cy="{s.cy:.1f}" rx="{HIER_W / 2}" '
                f'ry="{HIER_H / 2}" fill="{c["hier"]}" {attrs}/>'
                f'<text x="{s.cx:.1f}" y="{s.cy:.1f}" font-size="10" font-weight="bold" {text}>'
                f'{escape(_shorten(s.name, _NAME_CHARS["hier"]))}</text></g>')
    if s.kind == "event":
        r = EVENT_R
        pts = " ".join(f"{s.cx + r * math.cos(math.radians(a)):.1f},{s.cy + r * math.sin(math.radians(a)):.1f}"
                       for a in range(0, 360, 60))
        return (f'<g id="event-{s.id}">{_title(s)}<polygon points="{pts}" fill="{c["fill"]}" {attrs}/>'
                f'<text x="{s.cx:.1f}" y="{s.cy + r + 10:.1f}" font-size="9" {text}>'
                f'{escape(_shorten(s.name, _NAME_CHARS["block"]))}</text></g>')
    r = view_transform.NODE_R
    # Start and End are filled, as on the canvas; their letter is white.
    fill = {"S": c["start"], "E": c["end"]}.get(s.label, c["fill"])
    letter = 'fill="#FFFFFF"' if s.label in ("S", "E") else f'fill="{c["text"]}"'
    name = (f'<text x="{s.cx:.1f}" y="{s.cy - r - 8:.1f}" font-size="10" {text}>'
            f'{escape(_shorten(s.name, _NAME_CHARS["block"]))}</text>' if s.name else "")
    return (f'<g id="node-{s.id}">{_title(s)}<circle cx="{s.cx:.1f}" cy="{s.cy:.1f}" r="{r}" '
            f'fill="{fill}" {attrs}/><text x="{s.cx:.1f}" y="{s.cy:.1f}" font-size="9" '
            f'font-weight="bold" {letter} text-anchor="middle" dominant-baseline="central">'
            f'{escape(s.label)}</text>{name}</g>')


def _num(a: dict, key: str, default: float = 0.0) -> float:
    try:
        value = float(a.get(key, default) or 0.0)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _anno_bounds(a: dict) -> tuple[float, float, float, float]:
    if a.get("kind") == "arrow":
        xs, ys = (_num(a, "x1"), _num(a, "x2")), (_num(a, "y1"), _num(a, "y2"))
        return min(xs), min(ys), max(xs), max(ys)
    x, y = _num(a, "x"), _num(a, "y")
    w = _num(a, "w") or 8.0 * len(str(a.get("text") or "")) * _num(a, "font_size", 10.0) / 10.0
    h = _num(a, "h") or 1.6 * _num(a, "font_size", 10.0)
    return x, y, x + w, y + h


def _anno_svg(a: dict, c: dict[str, str]) -> str:
    kind = a.get("kind")
    border = a.get("border_colour")
    width = _num(a, "border_width", 2.0 if kind == "arrow" else 0.0)
    stroke = (f'stroke={quoteattr(str(border))} stroke-width="{width:g}"'
              if border and width > 0 else 'stroke="none"')
    dash = _LINE_DASH.get(str(a.get("line_style") or ""), "")
    if dash and border and width > 0:
        stroke += f' stroke-dasharray="{dash}"'
    fill = quoteattr(str(a["fill_colour"])) if a.get("fill_colour") else '"none"'
    if kind == "arrow":
        colour = quoteattr(str(border or c["link"]))
        return (f'<line x1="{_num(a, "x1"):.1f}" y1="{_num(a, "y1"):.1f}" x2="{_num(a, "x2"):.1f}" '
                f'y2="{_num(a, "y2"):.1f}" stroke={colour} stroke-width="{max(width, 1.0):g}" '
                f'marker-end="url(#arrow)"/>')
    if kind == "rect":
        return (f'<rect x="{_num(a, "x"):.1f}" y="{_num(a, "y"):.1f}" width="{_num(a, "w"):.1f}" '
                f'height="{_num(a, "h"):.1f}" fill={fill} {stroke}/>')
    if kind == "ellipse":
        w, h = _num(a, "w"), _num(a, "h")
        return (f'<ellipse cx="{_num(a, "x") + w / 2:.1f}" cy="{_num(a, "y") + h / 2:.1f}" rx="{w / 2:.1f}" '
                f'ry="{h / 2:.1f}" fill={fill} {stroke}/>')
    x0, y0, x1, y1 = _anno_bounds(a)
    size = _num(a, "font_size", 10.0)
    weight = ' font-weight="bold"' if a.get("font_bold") else ""
    style = ' font-style="italic"' if a.get("font_italic") else ""
    lines = str(a.get("text") or "").splitlines() or [""]
    colour = quoteattr(str(a.get("text_colour") or c["text"]))
    spans = "".join(f'<tspan x="{x0 + 3:.1f}" dy="{size * (1.0 if i == 0 else 1.25):.1f}">{escape(line)}</tspan>'
                    for i, line in enumerate(lines))
    frame = (f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" fill={fill} {stroke}/>'
             if (a.get("fill_colour") or (border and width > 0)) else "")
    return (f'<g>{frame}<text x="{x0 + 3:.1f}" y="{y0 + 2:.1f}" font-size="{size:g}" fill={colour}'
            f'{weight}{style}>{spans}</text></g>')

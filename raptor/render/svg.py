"""A plain SVG drawing of one diagram level, without Qt.

The PNG renderer (`raptor.render.worker`) builds the GUI's own canvas, so it
needs PySide6. This module draws the same level from the model alone, for a
headless install or an assistant that reads SVG text: blocks, nodes, events,
hierarchies, links and annotations, at the positions the GUI uses (the shared
`view_transform` and the GUI's element sizes). It is a sketch for checking
layout and connections, not a copy of the canvas: no fonts are measured, long
names are shortened (the full name is in each element's `<title>`), and no
state colours are drawn.

A *result layer* (`raptor.api.insight.canvas.canvas_layer`: share of downtime
or of lost capacity) draws what the canvas's Show on canvas layers draw:
each block and event filled by its band (relative to the largest share), its
share printed under it, the letter of its main cause in the corner, a dashed
ring when the runs disagree, each hierarchy oval with the sum of what is
inside it, links into capacity nodes as wide as the node's mean flow, and a
legend saying how to read it.

The Advisor layer (`raptor.api.advisor.canvas.advisor_layer`) is drawn the
same way: each element a finding is about filled by its worst severity, the
number of findings under it, the findings in its `<title>`, and each
hierarchy oval with the count of what is flagged inside it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from raptor.core.fileio import view_transform
from raptor.core.routing import ROUTING_MODES, Router, angled, fan_hint, path_d
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

#: Band fills of a result layer (Red / Yellow / Green relative to the largest
#: share), light enough that the dark text on them stays readable.
_BANDS = {
    "light": {"high": "#FFC9C9", "mid": "#FFE8A3", "low": "#C8F0D0"},
    "dark": {"high": "#6B2424", "mid": "#5E4C14", "low": "#1F4D2B"},
}

#: The band an Advisor finding of each severity is filled with; advice and
#: notes take the "note" fill.
_SEVERITY_BAND = {"error": "high", "warning": "mid", "advice": "note", "info": "note"}
_NOTE_FILL = {"light": "#D0E4FF", "dark": "#1E3A5F"}

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


_Link = tuple[_Shape, _Shape, "list[tuple[float, float]]"]


def _link_points(rbd: Any, level: int) -> dict[int, list[tuple[float, float]]]:
    """The active routing points of each link at `level`, in file order.
    The GUI draws a link through them as straight segments."""
    out: dict[int, list[tuple[float, float]]] = {}
    for g in getattr(rbd, "link_grips", None) or []:
        if g.get("active", True) and int(g.get("parent", 0) or 0) == level:
            out.setdefault(int(g.get("link", 0) or 0), []).append(
                (float(g["x"]), float(g["y"])))
    return out


def _links(rbd: Any, by_key: dict[tuple[str, int], _Shape],
           level: int = 0) -> list[_Link]:
    out: list[_Link] = []
    points = _link_points(rbd, level)
    for row in _rows(rbd, "LINK_DATA"):
        link = link_from_row(row)
        src = by_key.get((_KIND_OF_REF.get(link.connect_f_ref.replace("RBD", "", 1), "node"), link.connect_from_id))
        dst = by_key.get((_KIND_OF_REF.get(link.connect_t_ref.replace("RBD", "", 1), "node"), link.connect_to_id))
        if src is not None and dst is not None:
            out.append((src, dst, points.get(int(float(row.get("ID", 0) or 0)), [])))
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


def _routed(src: _Shape, dst: _Shape, boxes: list[tuple[float, float, float, float]],
            router: Router, mode: str,
            targets: list[_Shape], sources: list[_Shape]):
    """A right-angled route from `src` to `dst`, or None (draw it straight).
    Leaves along the page flow: sideways when the target is more across than
    up/down, otherwise top/bottom."""
    if src is dst:
        return None
    dx, dy = dst.cx - src.cx, dst.cy - src.cy
    if abs(dx) >= 0.5 * abs(dy):
        out_side, in_side = ("right", "left") if dx >= 0 else ("left", "right")
    else:
        out_side, in_side = ("bottom", "top") if dy >= 0 else ("top", "bottom")

    def port(s: _Shape, side: str) -> tuple[float, float]:
        hw, hh = s.half
        return {"right": (s.cx + hw, s.cy), "left": (s.cx - hw, s.cy),
                "bottom": (s.cx, s.cy + hh), "top": (s.cx, s.cy - hh)}[side]

    if mode == "angled":
        return angled(port(src, out_side), out_side, port(dst, in_side), in_side)
    p0, p3 = port(src, out_side), port(dst, in_side)
    hint = fan_hint(out_side, in_side, p0, p3,
                    [t.bounds() for t in targets], [s.bounds() for s in sources])
    return router.route(p0, out_side, p3, in_side, boxes, hint)


def _links_svg(links: list[_Link], shapes: list[_Shape],
               routing: str, c: dict[str, str],
               widths: dict[int, float] | None = None) -> tuple[list[str], int]:
    """The SVG for every link, and how many were drawn routed (not as a
    straight line). `widths` (capacity node id -> stroke width) draws each
    link into such a node at that width, and every other link thin."""
    router = Router(per_route_s=0.05, total_s=3.0, window_s=1e9)
    fans_out: dict[int, list[_Shape]] = {}
    fans_in: dict[int, list[_Shape]] = {}
    for s, d, _pts in links:
        fans_out.setdefault(id(s), []).append(d)
        fans_in.setdefault(id(d), []).append(s)
    # One obstacle snapshot for the whole render: rebuilding it per link
    # made a large drawing's routing quadratic before the router's own
    # time budget could even start.
    boxes = [s.bounds() for s in shapes] if routing != "straight" else []
    out: list[str] = []
    routed = 0
    for src, dst, pts in links:
        if widths is None:
            stroke = 'stroke-width="1.5" marker-end="url(#arrow)"'
        else:
            width = widths.get(dst.id, 1.0) if dst.kind == "node" else 1.0
            # a fixed-size arrowhead: the default one grows with the stroke
            stroke = f'stroke-width="{width:.2f}" marker-end="url(#arrow-fixed)"'
        if pts:
            # Pinned by routing points (a band hand-over): straight segments
            # from the sides facing the first and last point.
            a = src.port(*pts[0])
            b = dst.port(*pts[-1])
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y in [a, *pts, b])
            out.append(f'<polyline points="{path}" fill="none" stroke="{c["link"]}" '
                       f'{stroke}/>')
            routed += 1
            continue
        route = (_routed(src, dst, boxes, router, routing,
                         fans_out[id(src)], fans_in[id(dst)])
                 if routing != "straight" else None)
        if route is not None:
            routed += 1
            out.append(f'<path d="{path_d(route)}" fill="none" stroke="{c["link"]}" '
                       f'{stroke}/>')
            continue
        ax, ay = src.port(dst.cx, dst.cy)
        bx, by = dst.port(src.cx, src.cy)
        out.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{c["link"]}" '
                   f'{stroke}/>')
    return out, routed


def render_svg(rbd: Any, *, level: int = 0, highlight: list[Any] | None = None,
               zoom_to: list[Any] | None = None, margin: float = 12.0, theme: str = "light",
               include_annotations: bool = True,
               routing: str = "straight",
               layer: dict[str, Any] | None = None,
               flow: dict[int, float] | None = None) -> tuple[str, dict[str, Any]]:
    """Draw one level of `rbd` as SVG. Returns `(svg_text, meta)`.

    `level` is 0 for the top level or a hierarchy id, as for the PNG
    renderer. `zoom_to` frames the named elements, padded by `margin` scene
    units; `highlight` outlines them. `routing` is "straight" or
    "angled" (short stubs, then a straight run) or "orthogonal" (right
    angles only, round the other elements; a link that cannot be routed
    within the time budget stays straight). Coordinates are the GUI's scene
    pixels, so annotation positions match the canvas.

    `layer` is a result layer from `raptor.api.insight.canvas.canvas_layer`;
    `flow` (capacity node id -> mean flow) widens the links into capacity
    nodes. Both are optional and change nothing else in the drawing."""
    _check(rbd, level=level, margin=margin, theme=theme, routing=routing)
    c = _THEMES[theme]
    vt = view_transform.ensure(rbd)
    shapes = _shapes(rbd, level, vt)
    by_key = {(s.kind, s.id): s for s in shapes}
    links = _links(rbd, by_key, level)
    lit = _resolve(rbd, list(highlight or []), by_key, "highlight")
    zoomed = _resolve(rbd, list(zoom_to or []), by_key, "zoom_to")
    annos = ([a for a in (getattr(rbd, "annotations", None) or [])
              if int(a.get("parent", 0) or 0) == level] if include_annotations else [])

    x0, y0, w, h = _frame([s.bounds() for s in (zoomed or shapes)]
                          + ([] if zoomed else [_anno_bounds(a) for a in annos]), margin)
    painted: dict[tuple[str, int], dict[str, Any]] = {}
    rolled: dict[int, tuple[float, int, list[dict[str, Any]]]] = {}
    legend: list[str] = []
    advisor = layer is not None and layer.get("layer") == "advisor"
    if layer is not None:
        painted, rolled = _layer_marks(rbd, layer, by_key)
        legend = (_advisor_legend(layer, w) if advisor
                  else _legend_lines(layer, bool(flow), w))
        h += 16.0 * len(legend) + 10.0

    widths = _flow_widths(flow) if layer is not None and flow and not advisor else None
    # the fixed-size arrow is only for links drawn by flow, so a plain
    # diagram carries just the one marker
    fixed = (f'<marker id="arrow-fixed" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="9" '
             f'markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">'
             f'<path d="M0,0 L10,5 L0,10 z" fill="{c["link"]}"/></marker>') if widths else ""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}" '
           f'width="{w:.0f}" height="{h:.0f}" font-family="sans-serif">',
           f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" '
           f'markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
           f'fill="{c["link"]}"/></marker>{fixed}</defs>',
           f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{c["bg"]}"/>']
    out.append('<g class="links">')
    link_svg, routed = _links_svg(links, shapes, routing, c, widths)
    out.extend(link_svg)
    out.append("</g>")
    out.append('<g class="elements">')
    bands = {**_BANDS[theme], "note": _NOTE_FILL[theme]}
    out.extend(_elements_svg(shapes, painted, rolled, layer, c, bands))
    out.append("</g>")
    if advisor:
        out.extend(_advisor_svg(shapes, painted, rolled, legend, c, (x0 + margin, y0 + h - 12)))
    elif layer is not None:
        pooled = {int(row["id"]): row for row in layer.get("hierarchies") or []}
        out.extend(_layer_svg(shapes, painted, rolled, legend, c,
                              (x0 + margin, y0 + h - 12), pooled))
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
        "elements_drawn": len(shapes), "elements_in_view": in_view, "links_drawn": len(links), "routing": routing,
        "links_routed": routed,
        "annotations_drawn": len(annos), "highlighted": [s.name or f"{s.kind}:{s.id}" for s in lit],
    }
    if layer is not None:
        meta["layer"] = _layer_meta(layer, advisor, len(painted), len(rolled), bool(widths))
    return "\n".join(out) + "\n", meta


def _layer_meta(layer: dict[str, Any], advisor: bool, marked: int, rolled: int,
                by_flow: bool) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "name": layer.get("layer"),
        "measure": layer.get("measure") or ("Advisor findings" if advisor else None),
        "elements_marked": marked, "hierarchies_rolled_up": rolled, "links_by_flow": by_flow}
    if advisor:
        meta["findings"] = int(layer.get("findings") or 0)
        meta["off_canvas"] = int(layer.get("off_canvas") or 0)
    return meta


def _check(rbd: Any, *, level: int, margin: float, theme: str, routing: str) -> None:
    if theme not in _THEMES:
        raise SvgRenderError(f"theme must be 'light' or 'dark', got {theme!r}")
    if level not in _levels(rbd):
        raise SvgRenderError(f"level {level} is not 0 or a hierarchy id; available: {_levels(rbd)}")
    if not math.isfinite(float(margin)) or margin < 0:
        raise SvgRenderError(f"margin must be a finite value >= 0, got {margin}")
    if routing not in ROUTING_MODES:
        raise SvgRenderError(f"routing must be one of {ROUTING_MODES}, got {routing!r}")


def _elements_svg(shapes: list[_Shape], painted: dict[tuple[str, int], dict[str, Any]],
                  rolled: dict[int, tuple[float, int, list[dict[str, Any]]]], layer: dict[str, Any] | None,
                  c: dict[str, str], bands: dict[str, str]) -> list[str]:
    """Every element, filled by its band where a result layer marks it."""
    out = []
    advisor = layer is not None and layer.get("layer") == "advisor"
    for s in shapes:
        mark = painted.get((s.kind, s.id))
        if mark is not None:
            band = _SEVERITY_BAND[mark["severity"]] if advisor else mark["band"]
            out.append(_shape_svg(s, {**c, "fill": bands[band], "hier": bands[band]}))
        elif s.kind == "hier" and s.id in rolled:
            band = (_SEVERITY_BAND[_worst(rolled[s.id][2])] if advisor
                    else _band_of(rolled[s.id][0], layer))
            out.append(_shape_svg(s, {**c, "hier": bands[band]}))
        else:
            out.append(_shape_svg(s, c))
    return out


def _layer_svg(shapes: list[_Shape], painted: dict[tuple[str, int], dict[str, Any]],
               rolled: dict[int, tuple[float, int, list[dict[str, Any]]]], legend: list[str],
               c: dict[str, str], legend_at: tuple[float, float],
               pooled: dict[int, dict[str, Any]] | None = None) -> list[str]:
    """The marks, the hierarchy totals and the legend (its last line at `legend_at`)."""
    out = ['<g class="result-layer">']
    for s in shapes:
        mark = painted.get((s.kind, s.id))
        if mark is not None:
            out.append(_mark_svg(s, mark, c))
        elif s.kind == "hier" and s.id in rolled:
            total, count, inside = rolled[s.id]
            own = (pooled or {}).get(s.id)
            spread = (f"; ±2 SE {100 * own['low']:.3g} to {100 * own['high']:.3g} %, pooled over the runs"
                      if own is not None and own.get("low") is not None else "")
            out.append(f'<text x="{s.cx:.1f}" y="{s.cy + HIER_H / 2 + 12:.1f}" font-size="10" '
                       f'font-weight="bold" fill="{c["text"]}" text-anchor="middle">'
                       f'<title>{escape(f"{count} element(s) inside{spread}")}</title>'
                       f'Σ {100 * total:.3g} %</text>')
            out.extend(_scent_svg(s, inside, total, c))
    x, bottom = legend_at
    for i, line in enumerate(legend):
        out.append(f'<text x="{x:.1f}" y="{bottom - 16 * (len(legend) - 1 - i):.1f}" '
                   f'font-size="11" fill="{c["text"]}">{escape(line)}</text>')
    out.append("</g>")
    return out


#: Width of the bar of a hierarchy's top contributors, as on the canvas.
SCENT_W = 72.0


def _scent_svg(s: _Shape, inside: list[dict[str, Any]], total: float,
               c: dict[str, str]) -> list[str]:
    """Under a hierarchy's Σ, a small bar of its top three contributors,
    each as wide as its part of the total and coloured by its main cause,
    so a problem can be followed down a level (plan 7.6.1)."""
    from raptor.render.charts_svg import CAUSE_COLOURS
    if total <= 0 or not inside:
        return []
    top3 = sorted(inside, key=lambda r: -float(r.get("value") or 0.0))[:3]
    x0, y = s.cx - SCENT_W / 2, s.cy + HIER_H / 2 + 16
    words = ", ".join(f"{r.get('name')} {100 * float(r.get('value') or 0.0) / total:.3g} %"
                      for r in top3)
    out = [f'<g class="scent"><title>{escape(f"Inside: {words} of the total; colour = main cause")}</title>',
           f'<rect x="{x0:.1f}" y="{y:.1f}" width="{SCENT_W:.1f}" height="5" fill="{c["link"]}" '
           'fill-opacity="0.25"/>']
    x = x0
    for r in top3:
        width = SCENT_W * min(1.0, max(0.0, float(r.get("value") or 0.0) / total))
        colour = CAUSE_COLOURS.get(str(r.get("main_cause") or "other"), CAUSE_COLOURS["other"])
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="5" fill="{colour}"/>')
        x += width
    out.append("</g>")
    return out


def _worst(rows: list[dict[str, Any]]) -> str:
    from raptor.api.advisor.finding import SEVERITY_RANK
    return max((str(r["severity"]) for r in rows), key=lambda v: SEVERITY_RANK[v])


def _advisor_svg(shapes: list[_Shape], painted: dict[tuple[str, int], dict[str, Any]],
                 rolled: dict[int, tuple[float, int, list[dict[str, Any]]]], legend: list[str],
                 c: dict[str, str], legend_at: tuple[float, float]) -> list[str]:
    """The Advisor's marks: under each flagged element a warning sign (or a
    note sign for advice) and how many findings concern it, with the
    findings in its title; under a hierarchy the count of what is flagged
    inside it; then the legend."""
    out = ['<g class="result-layer advisor-layer">']
    for s in shapes:
        row = painted.get((s.kind, s.id))
        rows = [row] if row is not None else (rolled[s.id][2] if s.kind == "hier" and s.id in rolled
                                              else [])
        if not rows:
            continue
        worst = _worst(rows)
        count = sum(int(r.get("count") or 0) for r in rows)
        sign = "⚠" if worst in ("error", "warning") else "ⓘ"
        if row is not None:
            title = f"{row.get('name')}: {count} Advisor finding(s)\n" + "\n".join(
                f"• {f['severity']}: {f['title']} ({f['rule']})" for f in row.get("findings") or [])
            label = f"{sign} {count}"
        else:
            title = (f"{count} Advisor finding(s) about {len(rows)} element(s) inside; worst: {worst}\n"
                     + "\n".join(f"• {r.get('name')}: {r.get('count')}" for r in rows[:8]))
            label = f"Σ {sign} {count}"
        _hw, hh = s.half
        y = (s.cy + HIER_H / 2 + 12 if s.kind == "hier"
             else s.cy + hh + (24 if s.kind in ("event", "node") else 11))
        out.append(f'<g class="mark"><title>{escape(title)}</title>'
                   f'<text x="{s.cx:.1f}" y="{y:.1f}" font-size="10" font-weight="bold" '
                   f'fill="{c["text"]}" text-anchor="middle">{escape(label)}</text></g>')
    x, bottom = legend_at
    for i, line in enumerate(legend):
        out.append(f'<text x="{x:.1f}" y="{bottom - 16 * (len(legend) - 1 - i):.1f}" '
                   f'font-size="11" fill="{c["text"]}">{escape(line)}</text>')
    out.append("</g>")
    return out


def _advisor_legend(layer: dict[str, Any], width: float) -> list[str]:
    off = int(layer.get("off_canvas") or 0)
    text = ("Advisor findings: red = an error, yellow = a warning, blue = advice or a note; "
            "⚠ n / ⓘ n = how many findings concern the element (hover for them). "
            "Σ = what is flagged inside a hierarchy.")
    if off:
        text += (f" {off} finding(s) about settings rather than elements (pools, crews, "
                 "the run) are not drawn: see the Advisor's full list.")
    return _wrap(text, width)


def _band_of(value: float, layer: dict[str, Any] | None) -> str:
    largest = float((layer or {}).get("largest") or 0.0)
    fraction = value / largest if largest > 0 else 0.0
    return "high" if fraction >= 2 / 3 else "mid" if fraction >= 1 / 3 else "low"


def _layer_marks(rbd: Any, layer: dict[str, Any], by_key: dict[tuple[str, int], _Shape]
                 ) -> tuple[dict[tuple[str, int], dict[str, Any]],
                            dict[int, tuple[float, int, list[dict[str, Any]]]]]:
    """The layer's rows on this level, and each hierarchy oval on this level
    with the sum of the rows inside it (at any depth)."""
    parents: dict[tuple[str, int], int] = {}
    for row in _rows(rbd, "BLOCK_DATA"):
        b = block_from_row(row)
        parents[("block", b.id)] = b.parent_id
    for row in _rows(rbd, "EVENT_DATA"):
        e = event_from_row(row)
        parents[("event", e.id)] = e.parent_id
    for row in _rows(rbd, "NODE_DATA"):
        n = node_from_row(row)
        parents[("node", n.id)] = n.parent_id
    hier_parent = {h.id: h.parent_id for h in (hier_from_row(r) for r in _rows(rbd, "HIERARCHY_DATA"))}
    for hid, parent in hier_parent.items():
        parents[("hier", hid)] = parent
    painted: dict[tuple[str, int], dict[str, Any]] = {}
    rolled: dict[int, tuple[float, int, list[dict[str, Any]]]] = {}
    for row in layer.get("elements") or []:
        key = (str(row.get("kind")).lower(), int(row["id"]))
        if key in by_key:
            painted[key] = row
            continue
        parent = parents.get(key, 0)
        for _depth in range(16):           # the oval on this level holding it
            if not parent or ("hier", parent) in by_key:
                break
            parent = hier_parent.get(parent, 0)
        if parent and ("hier", parent) in by_key:
            total, count, rows = rolled.get(parent, (0.0, 0, []))
            rolled[parent] = (total + float(row.get("value") or 0.0), count + 1, [*rows, row])
    return painted, rolled


def _flow_widths(flow: dict[int, float]) -> dict[int, float]:
    """Stroke widths relative to the busiest node, as the canvas draws them."""
    busiest = max(flow.values(), default=0.0) or 1.0
    return {int(nid): 1.5 + 8.5 * max(0.0, float(value)) / busiest
            for nid, value in flow.items()}


def _legend_lines(layer: dict[str, Any], flow: bool, width: float) -> list[str]:
    from raptor.api.insight.canvas import CAUSE_GLYPHS
    from raptor.api.insight.attribution import CAUSE_LABELS
    largest = 100 * float(layer.get("largest") or 0.0)
    measure = str(layer.get("measure") or "share")
    lines = [f"Colour: {measure}, red from 2/3 of the largest ({largest:.3g} %), "
             "yellow from 1/3, green below. Σ = the elements inside a hierarchy.",
             "Letter: main cause (" + ", ".join(
                 f"{glyph} {CAUSE_LABELS.get(cause, cause).lower()}"
                 for cause, glyph in CAUSE_GLYPHS.items()) + "). "
             "Dashed ring and ±: the runs disagree; read it as rough."]
    if flow:
        lines.append("Link width: mean flow through the capacity node it feeds, "
                     "relative to the busiest node (flow is recorded per node, not per link).")
    return [part for line in lines for part in _wrap(line, width)]


def _wrap(line: str, width: float) -> list[str]:
    """`line` wrapped to the drawing's width (about 6 px a character at 11 px)."""
    out: list[str] = []
    per_line = max(40, int(width / 6.2))
    current = ""
    for word in line.split(" "):
        if current and len(current) + 1 + len(word) > per_line:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    out.append(current)
    return out


def _mark_svg(s: _Shape, row: dict[str, Any], c: dict[str, str]) -> str:
    """The share under the element, the cause letter in its corner and the
    dashed ring of an uncertain share."""
    from raptor.render.charts_svg import CAUSE_COLOURS
    hw, hh = s.half
    value = 100 * float(row.get("value") or 0.0)
    label = f"{value:.3g} %" + (" ±" if row.get("uncertain") else "")
    spread = (f" (±2 SE {100 * row['low']:.3g} to {100 * row['high']:.3g} %)"
              if row.get("low") is not None and row.get("high") is not None else "")
    title = escape(f"{row.get('name')}: {value:.4g} %{spread}; main cause "
                   f"{row.get('cause_label') or 'none recorded'}")
    y = s.cy + hh + (24 if s.kind == "event" else 11)
    out = [f'<g class="mark"><title>{title}</title>'
           f'<text x="{s.cx:.1f}" y="{y:.1f}" font-size="10" font-weight="bold" '
           f'fill="{c["text"]}" text-anchor="middle">{escape(label)}</text>']
    glyph = str(row.get("glyph") or "")
    if glyph:
        bx, by = s.cx + hw - 1, s.cy - hh + 1
        colour = CAUSE_COLOURS.get(str(row.get("main_cause") or "other"), CAUSE_COLOURS["other"])
        if row.get("uncertain"):
            out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="10.5" fill="none" '
                       f'stroke="{c["text"]}" stroke-width="1.2" stroke-dasharray="3 2"/>')
        out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="7" fill="{colour}"/>'
                   f'<text x="{bx:.1f}" y="{by:.1f}" font-size="9" font-weight="bold" fill="#FFFFFF" '
                   f'text-anchor="middle" dominant-baseline="central">{escape(glyph)}</text>')
    out.append("</g>")
    return "".join(out)


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

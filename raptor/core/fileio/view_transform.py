"""The model-to-canvas transform: origin, y-flip and scale.

Legacy files place elements on a grid in model units; the canvas renders
them at one model-wide scale, derived once from the model's own closest
spacing (so nothing overlaps at the authored density) and then frozen on
`RbdFile.view_transform`, so moving one element never rescales the canvas
or shifts every untouched element on reopen.

The GUI's `DiagramView` and the API's annotation anchors both need exactly
this, and used to keep two hand-synchronised copies (which had already
drifted: the API copy stored a zero grid pitch). This is the one source.
No Qt.
"""
from __future__ import annotations

import itertools
import logging
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

log = logging.getLogger("raptor.fileio.view_transform")

#: Rendered element sizes, in scene pixels; the scale must keep neighbours
#: at least MIN_ELEMENT_GAP apart at the model's closest spacing.
BLOCK_W = 108
BLOCK_H = 46
NODE_R = 18
MIN_ELEMENT_GAP = 52
#: Between two NODES a link needs room for its arrowhead only.
NODE_GAP = 40
LEGACY_SCALE = 8.0

#: Half the taller of a block and a node -- the radius an element in a
#: stacked column occupies around its own centre.
HALF_ELEMENT_H = max(BLOCK_H / 2.0, float(NODE_R))

_SECTIONS = ("BLOCK_DATA", "NODE_DATA", "HIERARCHY_DATA")


@dataclass(frozen=True)
class ViewTransform:
    scale: float
    origin_x: float
    flip_base: float
    dx_min: float      # authored grid pitch in model units; 0 if not uniform
    dy_min: float

    def as_dict(self) -> dict[str, float]:
        d = asdict(self)
        return {k: float(v) for k, v in d.items()}

    # -- coordinate conversion ------------------------------------------
    # The one place the model->scene rule is written. The GUI's
    # DiagramView and the API's annotation anchors both need it; before
    # these methods each kept its own inline arithmetic (and the API copy
    # had already drifted once).

    def x_to_scene(self, x: float) -> float:
        return (x - self.origin_x) * self.scale

    def y_to_scene(self, y: float) -> float:
        # File y increases upward, Qt's scene axis down-positive.
        return (self.flip_base - y) * self.scale

    def x_to_model(self, x: float) -> float:
        return x / self.scale + self.origin_x

    def y_to_model(self, y: float) -> float:
        return self.flip_base - y / self.scale

    def to_scene(self, x: float, y: float) -> tuple[float, float]:
        return self.x_to_scene(x), self.y_to_scene(y)

    def to_model(self, x: float, y: float) -> tuple[float, float]:
        return self.x_to_model(x), self.y_to_model(y)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ViewTransform:
        return cls(scale=float(d["scale"]), origin_x=float(d["origin_x"]),
                   flip_base=float(d["flip_base"]), dx_min=float(d["dx_min"]),
                   dy_min=float(d["dy_min"]))


def collect_positions(rbd: Any) -> tuple[list[float], list[float]]:
    """x and y of every positioned block, node and hierarchy (the ID=0
    default-template rows carry no real position)."""
    xs: list[float] = []
    ys: list[float] = []
    for name in _SECTIONS:
        sec = rbd.sections.get(name)
        if not sec:
            continue
        for r in sec.rows:
            try:
                if int(float(r.get("ID", 0))) == 0:
                    continue
            except (TypeError, ValueError):
                continue
            try:
                xs.append(float(r["xPos"]))
                ys.append(float(r["yPos"]))
            except (TypeError, ValueError, KeyError):
                continue
    return xs, ys


#: Each section's parent-level column: elements on different hierarchy
#: levels are never on screen together.
_PARENT_COL = {"BLOCK_DATA": "ParentId", "NODE_DATA": "parent",
               "HIERARCHY_DATA": "Parent"}


def collect_levels(rbd: Any) -> dict[int, tuple[list[float], list[float]]]:
    """`collect_positions`, split by hierarchy level (the parent id)."""
    levels: dict[int, tuple[list[float], list[float]]] = {}
    for name in _SECTIONS:
        sec = rbd.sections.get(name)
        if not sec:
            continue
        for r in sec.rows:
            try:
                if int(float(r.get("ID", 0))) == 0:
                    continue
                x, y = float(r["xPos"]), float(r["yPos"])
                parent = int(float(r.get(_PARENT_COL[name], 0) or 0))
            except (TypeError, ValueError, KeyError):
                continue
            xs, ys = levels.setdefault(parent, ([], []))
            xs.append(x)
            ys.append(y)
    return levels


def _column_pitch_scale(rbd: Any) -> float:
    """The scale that keeps every pair of horizontally ADJACENT columns on
    a level clear: their widest elements' half-widths plus the gap.

    It used to charge every closest pair as two blocks (108 px each plus
    52), so a run of nodes -- a split, an In marker, a vote -- had to sit a
    full block-width apart and drew as long empty links. A node is 36 px
    across; two of them need 36 + NODE_GAP. Hierarchy ovals count as
    blocks, as before."""
    needed = 0.0
    for cols in _levels_by_kind(rbd).values():
        for a, b in itertools.pairwise(sorted(cols)):
            ha, hb = cols[a], cols[b]
            gap = NODE_GAP if max(ha, hb) <= NODE_R else MIN_ELEMENT_GAP
            needed = max(needed, (ha + hb + gap) / (b - a))
    return needed


def _levels_by_kind(rbd: Any) -> dict[int, dict[float, float]]:
    """Per level, per column x: the widest element's half-width there."""
    out: dict[int, dict[float, float]] = {}
    for name in _SECTIONS:
        sec = rbd.sections.get(name)
        if not sec:
            continue
        hw = float(NODE_R) if name == "NODE_DATA" else BLOCK_W / 2.0
        for r in sec.rows:
            try:
                if int(float(r.get("ID", 0))) == 0:
                    continue
                x = float(r["xPos"])
                parent = int(float(r.get(_PARENT_COL[name], 0) or 0))
            except (TypeError, ValueError, KeyError):
                continue
            cols = out.setdefault(parent, {})
            cols[x] = max(cols.get(x, 0.0), hw)
    return out


def _level_min_diff(levels: dict[int, tuple[list[float], list[float]]],
                    axis: int) -> float:
    """The closest spacing on `axis` WITHIN a level. Spacing across levels
    is no constraint -- they are never drawn together -- and charging it
    blew a hierarchy up to the 300x ceiling: Van1's Engine_sub oval sits at
    x = 6.25 on the home level and Tire_FR at 6.5 inside Tires_sub, so the
    file-wide x minimum was 0.25 and a 3.5-unit link drew 1,050 px long."""
    gaps = [_min_diff(vals[axis]) for vals in levels.values()]
    return min((g for g in gaps if g > 0), default=0.0)


def _min_diff(vals: Iterable[float]) -> float:
    unique = sorted(set(vals))
    return min((b - a for a, b in itertools.pairwise(unique)), default=0.0)


#: x bucket width for "do these two elements share a column?", half the
#: layout engine's column pitch (legacy_layout.LAYER_PITCH). Mirrored
#: rather than imported to keep this module free of the layout engine.
_COLUMN_BUCKET = 1.25


def collect_columns(rbd: Any) -> list[list[float]]:
    """The positioned y values, grouped by column (x bucketed to
    `_COLUMN_BUCKET`) on each hierarchy level."""
    buckets: dict[tuple[int, int], list[float]] = {}
    for parent, (xs, ys) in collect_levels(rbd).items():
        for x, y in zip(xs, ys):
            buckets.setdefault((parent, round(x / _COLUMN_BUCKET)), []).append(y)
    return list(buckets.values())


def _column_pitch(rbd: Any) -> float:
    """The smallest vertical gap between elements that could actually
    collide -- i.e. the smallest within-a-column y gap, 0 when every
    column holds one element.

    The vertical spacing that matters is PER COLUMN. Two elements in
    different columns are already held apart horizontally by the layout
    (adjacent layers are `LAYER_PITCH` = 2.5 apart, wider than two
    block half-widths), so a y difference *between* columns is not a
    constraint -- but a global `_min_diff(ys)` treats it as one.

    That over-charge is what made Auto Layout render tiny boxes strung
    along very long links: on PWR1000_FullStation's EMERGENCY hierarchy
    the global y minimum was 1.25 (an incidental 1.25-unit offset between
    neighbouring columns, from the layout's whole-column refine shift)
    against a real 2.5 row pitch, so the scale came out 107.2 instead of
    64 -- a 268 px link between 108 px blocks, 160 px of it empty.
    """
    gaps = [_min_diff(ys) for ys in collect_columns(rbd)]
    gaps = [g for g in gaps if g > 0]
    return min(gaps, default=0.0)


def _uniform_grid_unit(vals: Sequence[float], origin: float,
                       unit: float) -> float:
    """`unit` (the smallest observed gap) if every distinct value lands on
    a multiple of it within 1 %, else 0.

    The smallest gap is not proof of a grid: a file whose positions span
    several hierarchy levels, each authored independently, can have a tiny
    gap that is an incidental clash between two unrelated levels."""
    if unit <= 0:
        return 0.0
    tol = max(unit * 0.01, 1e-6)
    for v in set(vals):
        k = round((v - origin) / unit)
        if abs((v - origin) - k * unit) > tol:
            return 0.0
    return unit


def derive(rbd: Any) -> ViewTransform:
    """Derive a transform from the positions currently in the file rows."""
    xs, ys = collect_positions(rbd)
    origin_x = min(xs) if xs else 0.0
    flip_base = max(ys) if ys else 0.0
    levels = collect_levels(rbd)
    dx_min = _level_min_diff(levels, 0)
    dy_min = _level_min_diff(levels, 1)
    # Scale driver only (the frozen dx_min/dy_min grid pitch is reported
    # unchanged below). The vertical budget is charged on the WITHIN-A-
    # COLUMN pitch, not the global y minimum -- see `_column_pitch`.
    dy_col = _column_pitch(rbd) or dy_min
    grid_x = _uniform_grid_unit(xs, origin_x, dx_min)
    grid_y = _uniform_grid_unit(ys, flip_base, dy_min)
    needed = max(float(LEGACY_SCALE), _column_pitch_scale(rbd))
    if dy_col > 0:
        # Centre-to-centre budget for two elements ADJACENT in a column:
        # half the taller one plus the gap. The term used to be
        # `BLOCK_H + MIN_ELEMENT_GAP + NODE_R * 2` (118px + gap), which
        # is the room a block needs stacked on a block with no gap, not
        # what neighbouring rows need -- and it was charged against the
        # tightest pitch in the file, so it inflated the scale by ~1.7x
        # and every link with it (a 268 px link between 108 px blocks,
        # most of it empty: "auto layout makes the boxes tiny").
        needed = max(needed, (HALF_ELEMENT_H + MIN_ELEMENT_GAP) / dy_col)
    # Clamp on the scene's pixel size, not the scale number: a huge scene
    # segfaults real display drivers, while a compact file can safely use a
    # much higher scale than a sprawling one. SpaceStation.rbd's 2.5-unit
    # spacing needs ~64x, so a flat cap would be too conservative. 20000 px
    # is comfortably within what drivers handle for one scene; 300x is a
    # ceiling for a near-degenerate span (two elements a hair apart).
    span = max(max(xs) - min(xs) if xs else 0.0,
               max(ys) - min(ys) if ys else 0.0, 1.0)
    ceiling = min(max(20000.0 / span, 60.0), 300.0)
    scale = max(4.0, min(min(needed, ceiling), ceiling))
    log.debug("derive: %d xy pairs, origin_x=%s flip_base=%s dx_min=%s "
              "dy_min=%s dy_col=%s grid=(%s, %s) span=%s ceiling=%s scale=%s",
              len(xs), origin_x, flip_base, dx_min, dy_min, dy_col, grid_x,
              grid_y, span, ceiling, scale)
    return ViewTransform(scale, origin_x, flip_base, grid_x, grid_y)


def frozen(rbd: Any) -> ViewTransform | None:
    """The transform already frozen on the file, if it holds a usable one."""
    vt = getattr(rbd, "view_transform", None)
    try:
        if vt and float(vt.get("scale", 0) or 0) > 0:
            return ViewTransform.from_dict(vt)
    except (KeyError, TypeError, ValueError):
        return None
    return None


def ensure(rbd: Any) -> ViewTransform:
    """The file's frozen transform, deriving and freezing one if it has
    none. The single entry point for both the GUI and the API."""
    vt = frozen(rbd)
    if vt is None:
        vt = derive(rbd)
        rbd.view_transform = vt.as_dict()
    return vt

"""Legacy-grid layout normalization for .rbd export.

The Raptor 7 canvas is 120x80 DISPLAY grid units (GridObj in
 vertical + 81 horizontal lines), y increasing UPWARD,
and the file stores every coordinate as display*1.25 (OpenFile does
`DisplayAt(x/1.25)`). Every Raptor 7 fixture's positions are exact
multiples of 1.25 -- elements sit ON grid lines, one row typically at
y stored 90-95 (display 72-76, the band every view window looks at,
since views anchor at xOrigin=0/yOrigin=80 and zoom the top band).

Models authored in THIS app (.rbds) carry arbitrary fractional
coordinates on their own canvas transform; exported verbatim they
render in Raptor 7 as a fraction-of-a-grid-square cluster with
overlapping boxes. This module re-lays such a model out on the REAL grid:

- per container (home + each hierarchy), longest-path layering from the
  container's entry node, columns 2.5 stored units apart, rows spread
  symmetrically 2.5 apart around y=92.5 (wide columns wrap into chunks
  of 12 rows rather than leaving the 120x80 canvas), everything snapped
  to 1.25 so elements sit on grid-line centres;
- view zooms derived from each level's own span (matching the real
  files' convention: zoom ~= visible display width, e.g. UniformProbe
  zoom 24 for a ~21-unit model, Hierarchy1-7 zoom 101 for ~93);
- every view origin pinned to the Raptor 7 files' 0/80.

Never touches a file that is already legacy-clean: the gate (see
`needs_legacy_normalization`) requires BOTH a mostly-off-grid element
set AND off-grid spacing finer than the grid itself, so Raptor 7 fixtures
(even mildly off-grid ones like Capacity1's single 3.333 coordinate)
pass through untouched and stay byte-stable round trips.
"""
from __future__ import annotations

import logging
import itertools
from bisect import bisect_left, bisect_right

log = logging.getLogger(__name__)

#: stored units per legacy display grid line (OpenFile: displayAt(x/1.25))
GRID = 1.25
#: column pitch between topological layers, stored units (Raptor 7 files:
#: adjacent blocks/nodes 1.25-2.5 apart; 2.5 keeps icons clear)
LAYER_PITCH = 2.5
#: column pitch between two columns that hold only nodes, stored units
NODE_PITCH = 1.25
#: row pitch within a column, stored units
ROW_PITCH = 2.5
#: rows per wrapped column chunk before x advances
ROW_CHUNK = 12
#: centre line every level's content hangs from, stored units (real
#: single-row files: 90.0-93.75; display 72-75, inside the view band)
ROW_CENTRE = 92.5
#: first grid line content may occupy (Raptor 7 files start at 1.25)
FIRST_X = 1.25
#: view-window anchoring (Raptor 7 files: every xOrigin/yOrigin is 0/80)
VIEW_ORIGIN_X = 0.0
VIEW_ORIGIN_Y = 80.0
#: view zoom limits (SetWorld clamps the canvas at 120 display units)
ZOOM_MIN, ZOOM_MAX = 8.0, 120.0
#: zoom padding beyond a level's own span, display units
ZOOM_PAD = 5.0

#: above this off-grid fraction the file is considered authored off-grid
OFF_GRID_FRAC = 0.25
#: below this min spacing the layout is crammed finer than the grid
#: itself (Raptor 7 fixtures' worst min spacing is exactly 1.25)
CRAMPED_PITCH = 1.24

_POSITIONED_SECTIONS = ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                        "HIERARCHY_DATA")

_PARENT_COL = {"BLOCK_DATA": "ParentId", "NODE_DATA": "parent",
               "EVENT_DATA": "ParentId", "HIERARCHY_DATA": "Parent"}


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


# ---------------------------------------------------------------------
# shared layered-layout engine (used by .rbds/.rbd save AND the GUI's
# Auto Layout button so both produce IDENTICAL coordinates)
# ---------------------------------------------------------------------

#: collision half-extents per element kind, stored units (an edge's
#: straight centre-to-centre segment must not enter this box)
_HALF = {"block": (0.9, 0.45), "hier": (1.3, 0.6),
         "event": (0.7, 0.35), "node": (0.45, 0.45)}
#: extra clearance margin around the boxes above
_CLEAR = 0.15
#: vertical band rows may occupy (canvas 80..100, keep clear of edges)
_BAND_LO, _BAND_HI = 81.0, 99.0
#: above this element count the greedy collision refine is skipped: it is
#: quadratic and a very large level is unreadable at that density anyway
_REFINE_MAX_ELEMENTS = 400


def _seg_rect_pen(x1, y1, x2, y2, rx, ry, hw, hh) -> float:
    """Penetration (overlap length along the segment) of the segment
    into the box centred (rx,ry) half-extents (hw,hh); 0 when clear.
    Liang-Barsky clip."""
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - (rx - hw)), (dx, (rx + hw) - x1),
                 (-dy, y1 - (ry - hh)), (dy, (ry + hh) - y1)):
        if p == 0:
            if q < 0:
                return 0.0
        else:
            r = q / p
            if p < 0:
                if r > t1:
                    return 0.0
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    return 0.0
                if r < t1:
                    t1 = r
    if t1 <= t0:
        return 0.0
    return max((t1 - t0) * ((dx * dx + dy * dy) ** 0.5), 0.01)


def _half(kind: str) -> tuple[float, float]:
    hw, hh = _HALF.get(kind, (0.9, 0.45))
    return hw + _CLEAR, hh + _CLEAR


def _seg_seg(p, q, r, s) -> bool:
    """True when segments p-q and r-s properly cross."""
    def o(a, b, c):
        v = ((b[0] - a[0]) * (c[1] - a[1]) -
             (b[1] - a[1]) * (c[0] - a[0]))
        return (v > 1e-9) - (v < -1e-9)
    d1, d2 = o(p, q, r), o(p, q, s)
    if d1 == d2:
        return False
    d3, d4 = o(r, s, p), o(r, s, q)
    return d3 != d4


#: centrality weight per kind: how hard an element is pulled to the
#: centre row during refinement (in/out nodes hardest -- the level's in/out
#: markers should sit exactly on the centre line)
_CENTRAL_W = {"node": 1.0}


def layout_container_positions(members, edges, entries, pins=frozenset(),
                               base=None):
    """Layered layout for ONE container, collision-refined.

    members: iterable of hashable keys ("kind", id); edges: (a, b)
    successor pairs; entries: keys where flow starts; pins: keys that
    item-level refinement may not move (whole-column shifts still
    apply). Returns {key: (x, y)} snapped to the 1.25 grid.

    Pipeline: longest-path layering -> rows centred pitch ROW_PITCH ->
    barycenter sweeps (crossing reduction) -> greedy collision refine
    (shift columns / slide rows within a column / nudge a lone block
    one column sideways) so no straight edge runs through a block."""
    member_set = sorted(set(members))      # deterministic order (ids are unique)
    if not member_set:
        return {}
    succ: dict = {}
    pred: dict = {}
    for a, b in edges:
        if a in member_set and b in member_set and a != b:
            succ.setdefault(a, set()).add(b)
            pred.setdefault(b, set()).add(a)
    layer: dict = {}
    queue = [(e, 0) for e in entries if e in member_set]
    visits: dict = {}
    while queue:
        k, d = queue.pop(0)
        if d <= layer.get(k, -1):
            continue
        # A cyclic LINK_DATA graph would otherwise enqueue forever at
        # increasing depths; bound each node to the graph size.
        visits[k] = visits.get(k, 0) + 1
        if visits[k] > len(member_set):
            continue
        layer[k] = d
        for nxt in succ.get(k, ()):
            queue.append((nxt, d + 1))
    for k in member_set:
        layer.setdefault(k, len(layer) + 1)
    by_layer: dict = {}
    for k in member_set:
        by_layer.setdefault(layer[k], []).append(k)
    def kind_of(k):
        return k[0] if isinstance(k, tuple) else "block"

    # Spatial scoring runs once per candidate move and, at a thousand
    # elements, is the whole cost of the layout pass.  Resolve each
    # element's kind and collision box ONCE instead of on every visit.
    kinds = {k: kind_of(k) for k in member_set}
    boxes = {k: _half(kinds[k]) for k in member_set}
    near_boxes = {k: (boxes[k][0] + 0.75, boxes[k][1] + 0.75) for k in member_set}

    # Column index per element, then each column's x. Two neighbouring
    # columns that hold only nodes sit NODE_PITCH apart rather than a
    # full LAYER_PITCH: a run of nodes (an In marker and its split, a vote
    # and the next split) is 36 px wide on screen, and at block pitch it
    # drew as long empty links (`view_transform` charges the scale per
    # adjacent pair of columns to match).
    col_of: dict = {}
    row_of: dict = {}
    for L in sorted(by_layer):
        ks = sorted(by_layer[L])
        for chunk, start in enumerate(range(0, len(ks), ROW_CHUNK)):
            group = ks[start:start + ROW_CHUNK]
            n = len(group)
            for i, k in enumerate(group):
                col_of[k] = L + chunk
                row_of[k] = ROW_CENTRE + (i - (n - 1) / 2.0) * ROW_PITCH
    node_only: dict = {}
    for k, c in col_of.items():
        node_only[c] = node_only.get(c, True) and kind_of(k) == "node"
    col_x: dict = {}
    x = FIRST_X
    prev = None
    for c in range(max(col_of.values()) + 1):
        if prev is not None:
            both_nodes = node_only.get(prev, True) and node_only.get(c, True)
            x += NODE_PITCH if both_nodes else LAYER_PITCH
        col_x[c] = x
        prev = c
    pos: dict = {k: [col_x[col_of[k]], row_of[k]] for k in col_of}

    # --- barycenter sweeps: order rows by neighbours' mean row so
    # connected elements align and edges stop zig-zagging ----------
    def reassign(L, keyval):
        ks = by_layer[L]
        n = len(ks)
        order = sorted(range(n),
                       key=lambda i: (-keyval(ks[i]), ks[i]))
        for i, _k in enumerate(order):
            pos[ks[i]][1] = ROW_CENTRE + (i - (n - 1) / 2.0) * ROW_PITCH

    for _ in range(3):
        for L in sorted(by_layer):
            reassign(L, lambda k: (sum(pos[p][1] for p in pred.get(k, ()))
                                    / len(pred[k])) if pred.get(k)
                      else pos[k][1])
        for L in sorted(by_layer, reverse=True):
            reassign(L, lambda k: (sum(pos[s][1] for s in succ.get(k, ()))
                                    / len(succ[k])) if succ.get(k)
                      else pos[k][1])

    # in/out markers sit exactly on the centre line (the level's in and
    # out nodes are centre aligned)
    for k in list(entries) + list(pins):
        if k in pos:
            pos[k][1] = ROW_CENTRE

    # --- greedy collision refine -----------------------------------
    edge_list = [(a, b) for a in member_set for b in succ.get(a, ())]
    #: hard layout faults from the most recent `score()` (overlap, edge
    #: through a block, crossing, out of band). A one-item list so the
    #: nested function can report without a function attribute.
    hard_state = [0]

    def score():
        s = 0.0
        hard = 0
        # One x-ordered copy of the elements, used by all three spatial
        # scans below.  The greedy refine calls this O(elements) times, so
        # every scan has to touch only the neighbourhood of the edge it is
        # scoring: a model with a thousand blocks cannot afford a full
        # element sweep (or an all-pairs edge sweep) per candidate move.
        items = sorted(pos.items(), key=lambda kv: (kv[1][0], kv[1][1]))
        xs = [xv for _k, (xv, _yv) in items]
        for a, b in edge_list:
            ax, ay = pos[a]
            bx, by = pos[b]
            lo, hi = min(ax, bx), max(ax, bx)
            # Only elements in a column strictly between the endpoints
            # can lie on this edge.
            for k, (x, y) in items[bisect_right(xs, lo):bisect_left(xs, hi)]:
                if k in (a, b):
                    continue
                hw, hh = boxes[k]
                if _seg_rect_pen(ax, ay, bx, by, x, y, hw, hh) > 0:
                    s += 100                      # hard: through a block
                    hard += 1
                else:
                    # near-miss gradient: softly push edges off blocks
                    w = _seg_rect_pen(ax, ay, bx, by, x, y, *near_boxes[k])
                    if w > 0:
                        s += 4 * w
        # element-element overlap
        for i, (ka, (xa, ya)) in enumerate(items):
            hwa, hha = boxes[ka]
            for kb, (xb, yb) in items[i + 1:]:
                if xb - xa > 2.6:
                    break
                hwb, hhb = boxes[kb]
                if abs(xb - xa) < hwa + hwb and abs(yb - ya) < hha + hhb:
                    s += 500
                    hard += 1
        for k, (x, y) in pos.items():
            bx0, by0 = base[k] if base else (x, y)
            # keep rows near the shared centre line AND near the
            # barycenter suggestion -- extreme rows are ugly; in/out
            # markers are pulled much harder (the level's in and
            # out nodes should sit exactly on the centre line)
            w = _CENTRAL_W.get(kinds[k], 0.25)
            if k in entries or k in pins:
                w = 5.0
            s += w * (y - ROW_CENTRE) ** 2
            s += 0.1 * (y - by0) ** 2 + 0.1 * (x - bx0) ** 2
            if not (_BAND_LO <= y <= _BAND_HI):
                s += 500
                hard += 1
            if not (FIRST_X <= x <= 118.75):
                s += 500
                hard += 1
        # edge-edge crossings (fan-in/fan-out lines that share an
        # endpoint are excluded -- converging on one node is fine).
        # Sorted by left x, so the inner loop stops as soon as the next
        # edge begins to the right of this one: only pairs whose x spans
        # overlap (or touch) can possibly cross.
        intervals = [(min(pos[a][0], pos[b][0]), max(pos[a][0], pos[b][0]), i)
                     for i, (a, b) in enumerate(edge_list)]
        intervals.sort()
        for ii, (_lo1, hi1, i1) in enumerate(intervals):
            a1, b1 = edge_list[i1]
            p1, q1 = pos[a1], pos[b1]
            for _lo2, _hi2, i2 in intervals[ii + 1:]:
                if _lo2 > hi1:
                    break
                a2, b2 = edge_list[i2]
                if a2 in (a1, b1) or b2 in (a1, b1):
                    continue
                if _seg_seg(p1, q1, pos[a2], pos[b2]):
                    s += 60
                    hard += 1
        hard_state[0] = hard
        return s

    cur = score()
    # A layered layout with no hard fault (no overlap, no edge through a
    # block, no crossing, everything in band) needs no greedy search: the
    # search can only trade aesthetic tiny terms, at O(n) score calls per
    # element. On a long clean chain that is the whole cost of the pass.
    #
    # Two layouts are skipped outright:
    #   * wider than the legacy canvas -- no column move can bring x back
    #     inside 118.75, so the search would spend every candidate on an
    #     unfixable fault (a 100-block series needs ~250 stored units);
    #   * more than _REFINE_MAX_ELEMENTS -- the search is quadratic in
    #     elements, and a very large model's schematic is unreadable at
    #     that density anyway; layering and the barycenter sweeps stand.
    too_wide = any(x > 118.75 + 1e-9 for x, _y in pos.values())
    if hard_state[0] and not too_wide and len(member_set) <= _REFINE_MAX_ELEMENTS:
        for _pass in range(8 if len(member_set) > 150 else 25):
            best = None          # (score, apply_fn)
            columns: dict = {}
            for k, (x, _y) in pos.items():
                columns.setdefault(x, []).append(k)
            def consider(s2, apply, cur=cur):
                nonlocal best
                if s2 < cur - 1e-9 and (best is None or s2 < best[0]):
                    best = (s2, apply)

            # whole-column vertical shifts (never for a column holding an
            # in/out marker -- those must stay on the centre line)
            for ks in columns.values():
                if any(k in entries or k in pins for k in ks):
                    continue
                for dy in (-GRID, GRID):
                    saved = [(k, pos[k][1]) for k in ks]
                    for k in ks:
                        pos[k][1] += dy
                    consider(score(), lambda ks=ks, dy=dy:
                             [pos[k].__setitem__(1, pos[k][1] + dy) for k in ks])
                    for k, yv in saved:
                        pos[k][1] = yv
            # slide one item within its column to a free grid row (keeping
            # the column's own ROW_PITCH clear -- 1.25-stacked rows touch)
            for k, (x, y) in list(pos.items()):
                if kind_of(k) == "node" or k in entries or k in pins:
                    continue
                col_ys = [pos[o][1] for o in columns[x] if o != k]
                for gy in _grid_rows():
                    if any(abs(gy - oy) < ROW_PITCH - 1e-6 for oy in col_ys):
                        continue
                    old = y
                    pos[k][1] = gy
                    consider(score(), lambda k=k, gy=gy: pos[k].__setitem__(1, gy))
                    pos[k][1] = old
            # nudge a block one column sideways (same row, only into an
            # EMPTY column -- sliding into an occupied column stacks blocks
            # on top of each other and pierces their own edges)
            for k, (x, _y) in list(pos.items()):
                if kind_of(k) != "block" or k in pins:
                    continue
                for nx in (x - LAYER_PITCH, x + LAYER_PITCH):
                    if not (FIRST_X <= nx <= 118.75):
                        continue
                    if any(ox == nx for o, (ox, _) in pos.items() if o != k):
                        continue
                    old = x
                    pos[k][0] = nx
                    consider(score(), lambda k=k, nx=nx: pos[k].__setitem__(0, nx))
                    pos[k][0] = old
            # swap two adjacent rows within a column
            for ks in columns.values():
                if any(k in entries or k in pins for k in ks):
                    continue
                by_row = sorted(ks, key=lambda k: pos[k][1])
                for i in range(len(by_row) - 1):
                    a, b = by_row[i], by_row[i + 1]
                    ya, yb = pos[a][1], pos[b][1]
                    pos[a][1], pos[b][1] = yb, ya
                    consider(score(), lambda a=a, b=b, ya=ya, yb=yb:
                             (pos[a].__setitem__(1, yb),
                              pos[b].__setitem__(1, ya)))
                    pos[a][1], pos[b][1] = ya, yb
            if best is None:
                break
            cur, apply_fn = best
            apply_fn()
    # snap everything to the grid
    return {k: (_snap(x), _snap(y)) for k, (x, y) in pos.items()}


def _grid_rows():
    g = _BAND_LO
    while g <= _BAND_HI + 1e-9:
        yield round(g, 6)
        g += GRID


def _coord(row) -> tuple[float, float] | None:
    try:
        return float(row["xPos"]), float(row["yPos"])
    except (TypeError, ValueError, KeyError):
        return None


def _content_rows(f):
    """(section, row) pairs carrying real content (ID != 0 template row)."""
    out = []
    for sec in _POSITIONED_SECTIONS:
        s = f.sections.get(sec)
        if not s:
            continue
        for r in s.rows:
            if str(r.get("ID", "0")) == "0":
                continue
            out.append((sec, r))
    return out


def _min_pitch(values: list[float]) -> float:
    """Smallest gap between *distinct* coordinate values, else GRID.

    Coincident values are collapsed first: a straight series chain puts
    every element on one y, and that is normal authoring, not crowding.
    """
    unique = sorted(set(values))
    return min((b - a for a, b in itertools.pairwise(unique)), default=GRID)


def _container_is_crammed(coords: list[tuple[float, float]]) -> bool:
    """Whether one container's own elements are crammed together.

    BOTH conditions must hold so already-openable files (Raptor 7 fixtures
    with a stray fractional coordinate, or fine but unusual spacing) are
    never silently rearranged: >=25% of the container's elements off-grid
    AND its closest spacing finer than one grid line.
    """
    if not coords:
        return False
    off = sum(1 for x, y in coords
              if abs(x / GRID - round(x / GRID)) > 1e-6
              or abs(y / GRID - round(y / GRID)) > 1e-6)
    if off / len(coords) < OFF_GRID_FRAC:
        return False
    return min(_min_pitch([x for x, _ in coords]),
               _min_pitch([y for _, y in coords])) < CRAMPED_PITCH


def needs_legacy_normalization(f) -> bool:
    """True when some *container* was authored off the legacy grid badly
    enough to render crammed/overlapping in Raptor 7.

    The check is per-container, not global.  A flat model has one
    container, so this is the original test.  A model with sub-hierarchies
    has several, and their coordinates legitimately interleave -- a block
    at x=4.0 inside one oval and one at x=3.75 inside the next is normal
    authoring, not crowding.  Measuring the minimum pitch across all
    levels therefore read every hierarchical model as crammed, and the
    save path quietly re-laid-out hand-authored figures on every write
    (three of the shipped `new_docs/models` tripped it).
    """
    per_container: dict[int, list[tuple[float, float]]] = {}
    for parent, kinds in _container_children(f).items():
        for rows in kinds.values():
            bucket = per_container.setdefault(parent, [])
            for row in rows:
                coord = _coord(row)
                if coord is not None:
                    bucket.append(coord)
    # Elements are grouped by their parent hierarchy; a top-level block
    # and a block inside an oval are never crowded by each other.
    return any(_container_is_crammed(c) for c in per_container.values())


def _hier_in_out(hier_row) -> tuple[int, int]:
    try:
        return (int(float(hier_row.get("in", 0))),
                int(float(hier_row.get("out", 0))))
    except (TypeError, ValueError):
        return 0, 0


def _container_children(f):
    """parent id -> {'block'/'node'/'event'/'hier': [row, ...]}."""
    kids: dict[int, dict[str, list]] = {}
    for sec, kind in (("BLOCK_DATA", "block"), ("NODE_DATA", "node"),
                      ("EVENT_DATA", "event"), ("HIERARCHY_DATA", "hier")):
        s = f.sections.get(sec)
        if not s:
            continue
        col = _PARENT_COL[sec]
        for r in s.rows:
            if str(r.get("ID", "0")) == "0":
                continue
            try:
                pid = int(float(r.get(col, 0)))
            except (TypeError, ValueError):
                continue
            kids.setdefault(pid, {}).setdefault(kind, []).append(r)
    return kids


def _link_endpoints(f):
    """[(from_kind, from_id, to_kind, to_id)] with RBD prefixes stripped."""
    out: list[tuple[str, int, str, int]] = []
    s = f.sections.get("LINK_DATA")
    if not s:
        return out
    for r in s.rows:
        try:
            out.append((
                str(r.get("FromType", "")).replace("RBD", "", 1).lower(),
                int(float(r.get("FromID", 0))),
                str(r.get("ToType", "")).replace("RBD", "", 1).lower(),
                int(float(r.get("ToID", 0))),
            ))
        except (TypeError, ValueError):
            continue
    return out


def _place_container(children: dict[str, list], links, entries, outs,
                     id_of) -> None:
    """Layered layout for one container; writes snapped positions back
    into the rows. `outs` (extra pinned keys, e.g. the container's own
    out node) may not move during collision refinement."""
    members: dict[tuple[str, int], dict] = {}
    for kind, rows in children.items():
        for r in rows:
            members[(kind, id_of(kind, r))] = r
    if not members:
        return
    edges = []
    for fk, fid, tk, tid in links:
        a, b = (fk, fid), (tk, tid)
        if a in members and b in members and a != b:
            edges.append((a, b))
    pos = layout_container_positions(members.keys(), edges,
                                     entries, pins=outs)
    for k, (x, y) in pos.items():
        members[k]["xPos"] = f"{x:.6f}"
        members[k]["yPos"] = f"{y:.6f}"


def _level_zoom(coords) -> float:
    if not coords:
        return 24.0
    xs = [x for x, _ in coords]
    ys = [y for _, y in coords]
    span_x = (max(xs) - min(xs)) / GRID
    span_y = (max(ys) - min(ys)) / GRID
    zoom = max(span_x, span_y) + ZOOM_PAD
    return float(min(max(round(zoom), ZOOM_MIN), ZOOM_MAX))


def repair_link_parents(f) -> int:
    """Fix LINK_DATA rows whose `pID` (owning window) disagrees with both
    endpoints' shared parent.

    Raptor 7 draws a link inside the window its `pID` names, so a
    link between two elements both parented to hierarchy 11 but tagged
    pID=0 belongs to the HOME window: inside HSB3's view the two
    elements render unconnected, and on home the link dangles off
    nothing (our GUI draws by topology, the legacy app trusts pID). When
    both endpoints resolve to the same parent, that parent IS the owning
    window; repair to it. Cross-boundary links (endpoints in different
    containers) have no inferable owner and are left alone. Safe to run
    unconditionally — consistent files change zero rows. Returns the
    number of repaired rows."""
    parent_of: dict[tuple[str, int], int] = {}
    for sec, kind in (("BLOCK_DATA", "block"), ("NODE_DATA", "node"),
                      ("EVENT_DATA", "event"), ("HIERARCHY_DATA", "hier")):
        s = f.sections.get(sec)
        if not s:
            continue
        idcol = "ID"
        for r in s.rows:
            try:
                parent_of[(kind, int(float(r[idcol])))] = \
                    int(float(r.get(_PARENT_COL[sec], 0)))
            except (TypeError, ValueError, KeyError):
                continue
    fixed = 0
    s = f.sections.get("LINK_DATA")
    if not s:
        return 0
    for r in s.rows:
        try:
            fp = parent_of.get((r["FromType"].replace("RBD", "", 1).lower(),
                                int(float(r["FromID"]))))
            tp = parent_of[(r["ToType"].replace("RBD", "", 1).lower(),
                            int(float(r["ToID"])))]
        except (TypeError, ValueError, KeyError):
            continue
        if fp is not None and fp == tp:
            pid = int(r["pID"]) if str(r.get("pID", "")).strip() else -1
            if pid != fp:
                r["pID"] = str(fp)
                fixed += 1
    if fixed:
        log.info("repair_link_parents: fixed %d link(s) whose pID "
                 "disagreed with their endpoints' shared parent", fixed)
    return fixed


def normalize_node_kofn(f) -> int:
    """Declare an explicit required-good count for multi-input nodes on
    .rbd export.

    Our `.rbds` convention stores `k=-1` on parallel/OR merge nodes
    ("at least one good path"; `typed.node_from_row` maps it to 1 at
    load). The legacy format uses the same `-1` for a DIFFERENT thing --
    "the user never opened the K-of-N dialog for this node"
   -- and it is only ever seen on nodes with
    `n=1`, where there is no K-of-N concept to declare: every real
    fixture's multi-input node carries an explicit k (Capacity1's Power
    2/3, Comm 1/2, ComplexModel's join 2/3, ...), and the real node
    dialog REJECTS anything outside 1..connectIntoNum, so Raptor 7 opens such a node as "paths connected but
    the required number was never declared".

    Coerce `k=-1` to `1` for any node with more than one incoming link
    (the same semantics our engine gives the sentinel). Single-input
    nodes keep `-1` -- the Raptor 7 reader itself converts that to
    goodPaths=1. Safe to run unconditionally:
    zero rows change on files whose multi-input nodes already declare
    k (every Raptor 7 fixture). Returns the number of rows changed."""
    incoming: dict[int, int] = {}
    s = f.sections.get("LINK_DATA")
    if s:
        for r in s.rows:
            if str(r.get("ToType", "")).replace("RBD", "", 1).lower() \
                    != "node":
                continue
            try:
                nid = int(float(r["ToID"]))
            except (TypeError, ValueError, KeyError):
                continue
            incoming[nid] = incoming.get(nid, 0) + 1
    fixed = 0
    ns = f.sections.get("NODE_DATA")
    if not ns:
        return 0
    for r in ns.rows:
        if str(r.get("k", "")).strip() != "-1":
            continue
        try:
            nid = int(float(r["ID"]))
        except (TypeError, ValueError, KeyError):
            continue
        if incoming.get(nid, 0) > 1:
            r["k"] = "1"
            fixed += 1
    if fixed:
        log.info("normalize_node_kofn: declared k=1 for %d multi-input "
                 "node(s) that carried the unspecified -1 sentinel", fixed)
    return fixed


#: Raptor 7 authored element names within 20 characters (the report
#: writers' SUBSTR(1,20,...) and the dialogs' field widths assume it).
#: Our own results reports no longer truncate (see reports.py), but the
#: legacy file format is where the cap belongs.
LEGACY_NAME_CAP = 20


def _truncate_unique(name: str, taken: set[str]) -> str:
    """`name` cut to the legacy 20-char cap; when that collides with an
    already-assigned name (or another truncated twin), append `~n`
    (n = 2, 3, ...) and re-cut so every legacy name stays unique --
    Raptor 7 keys selections by name in several dialogs, and a
    distinguisher beats a silent merge."""
    base = name[:LEGACY_NAME_CAP]
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"~{n}"
        candidate = base[:LEGACY_NAME_CAP - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1


def cap_legacy_names(f) -> int:
    """Cap element names at the legacy 20-character limit on .rbd save
    (display-only: the .rbds keeps the full names; IDs are untouched).
    Only names that EXCEED the cap are touched -- duplicates among
    already-legal names are real data and must round-trip unchanged
    (mass rename to one name is a supported flow, IDs remain the key).
    A truncated name that lands on an already-taken string gets a `~n`
    distinguisher so every legacy name stays unique. No-op for names
    already within the cap (every Raptor 7 fixture). Returns the number of
    rows changed."""
    sections = (
        ("BLOCK_DATA", "blockName"),
        ("EVENT_DATA", "eventName"),
        ("NODE_DATA", "Name"),
        ("HIERARCHY_DATA", "hierarchyName"),
        ("COMMENTS", "Name"),
    )
    fixed = 0
    seen: set[str] = set()
    for sec_name, col in sections:
        s = f.sections.get(sec_name)
        if not s:
            continue
        for r in s.rows:
            name = r.get(col, "")
            if not name:
                continue
            if len(name) <= LEGACY_NAME_CAP:
                # Within the cap: keep verbatim (duplicates included --
                # renaming them would rewrite user data on every save).
                seen.add(name)
                continue
            new = _truncate_unique(name, seen)
            if new != name:
                fixed += 1
            r[col] = new
            seen.add(new)
    if fixed:
        log.info("cap_legacy_names: capped %d element name(s) to %d "
                 "chars (uniqueness preserved)", fixed, LEGACY_NAME_CAP)
    return fixed


def normalize_legacy_layout(f, force: bool = False) -> bool:
    """Re-layout an off-grid model onto the legacy grid (in place).
    Returns True when anything was moved. `force` skips the gate (an
    explicit Auto Layout pass over every container)."""
    if not needs_legacy_normalization(f) and not force:
        return False
    kids = _container_children(f)
    links = _link_endpoints(f)

    hier_rows: dict[int, dict] = {}
    for sec, r in _content_rows(f):
        if sec == "HIERARCHY_DATA":
            try:
                hier_rows[int(float(r["ID"]))] = r
            except (TypeError, ValueError, KeyError):
                pass

    def id_of(kind, r):
        try:
            return int(float(r.get("ID", 0)))
        except (TypeError, ValueError):
            return 0

    # events hang off their parent block/hier: snapshot every positioned
    # element's coord so each event can follow its parent's delta
    before: dict[int, tuple[float, float]] = {}
    for sec, r in _content_rows(f):
        if sec in ("BLOCK_DATA", "HIERARCHY_DATA"):
            try:
                i = int(float(r["ID"]))
            except (TypeError, ValueError):
                continue
            if c := _coord(r):
                before[i] = c

    for parent in sorted(kids):
        children = kids[parent]
        if parent == 0:
            entries = [("node", id_of("node", r))
                       for r in children.get("node", [])
                       if str(r.get("Type", "")) == "1"]
            # Pin only the real terminal marker(s) to the centre line. A
            # regular node (Type 2) must not be pinned: pinning every
            # regular node would force same-layer nodes (e.g. two group
            # `*_in`/`*_out` nodes) onto identical coordinates and render
            # them stacked on top of each other.
            outs = {("node", id_of("node", r))
                    for r in children.get("node", [])
                    if str(r.get("Type", "")) in ("3", "5")}
        else:
            inh, outh = _hier_in_out(hier_rows.get(parent, {}))
            entries = [("node", inh)] if inh else []
            outs = {("node", outh)} if outh else set()
        _place_container(children, links, entries, outs, id_of)

    # events follow their parent element's movement delta
    moved: dict[int, tuple[float, float]] = {}
    for sec, r in _content_rows(f):
        if sec in ("BLOCK_DATA", "HIERARCHY_DATA"):
            try:
                i = int(float(r["ID"]))
            except (TypeError, ValueError):
                continue
            if c := _coord(r):
                if i in before and before[i] != c:
                    moved[i] = (c[0] - before[i][0],
                                c[1] - before[i][1])
    for sec, r in _content_rows(f):
        if sec != "EVENT_DATA":
            continue
        try:
            pid = int(float(r.get("ParentId", 0)))
        except (TypeError, ValueError):
            continue
        d = moved.get(pid)
        if d:
            c = _coord(r)
            if c:
                r["xPos"] = f"{_snap(c[0] + d[0]):.6f}"
                r["yPos"] = f"{_snap(c[1] + d[1]):.6f}"

    # view zooms/origins: home from SYSTEM_DATA, each hierarchy from its
    # own content
    source_row = f.sections["SYSTEM_DATA"].rows[0]
    home = [c for kind in ("block", "node", "event")
            for r in kids.get(0, {}).get(kind, [])
            if (c := _coord(r)) is not None]
    source_row["zoomVal"] = f"{_level_zoom(home):.6f}"
    source_row["xOrigin"] = f"{VIEW_ORIGIN_X:.6f}"
    source_row["yOrigin"] = f"{VIEW_ORIGIN_Y:.6f}"
    for hid, children in kids.items():
        h = hier_rows.get(hid)
        if h is None:
            continue
        coords = [c for kind in ("block", "node", "event")
                  for r in children.get(kind, [])
                  if (c := _coord(r)) is not None]
        h["zoom"] = f"{_level_zoom(coords):.6f}"
        h["xOrigin"] = f"{VIEW_ORIGIN_X:.6f}"
        h["yOrigin"] = f"{VIEW_ORIGIN_Y:.6f}"
    log.info("normalize_legacy_layout: re-laid %d container(s) onto the "
             "1.25 Raptor 7 grid", len(kids))
    return True

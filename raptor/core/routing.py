"""Orthogonal (right-angled) link routing -- deliberately simple.

Given the two ports of a link, the side each leaves/enters by, and the
boxes of the other elements, find a polyline made only of horizontal and
vertical segments that stays clear of those boxes. It never moves an
element and it is not a general router:

* it tries a short, fixed list of candidate shapes (straight, L, Z and a
  five-segment detour) whose bends lie on a handful of "lanes" -- the
  midpoint between the ports and the edges of nearby boxes -- and takes the
  first-best one that hits nothing;
* if none is clear, or the time budget is spent, it returns ``None`` and the
  caller draws its ordinary straight line. A link is never left unrouted.

Qt-free, so the GUI canvas and the SVG renderer share it.
"""
from __future__ import annotations

import time
from collections.abc import Sequence
import itertools

Point = tuple[float, float]
Rect = tuple[float, float, float, float]       # left, top, right, bottom

#: The link styles. "straight" is port to port; "angled" leaves and enters
#: along the port's normal for a short stub and runs straight between the
#: stubs; "orthogonal" is right angles only, clear of other elements.
ROUTING_MODES = ("straight", "angled", "orthogonal")

#: Outward unit vector of each side of an element.
OUT = {"left": (-1.0, 0.0), "right": (1.0, 0.0),
       "top": (0.0, -1.0), "bottom": (0.0, 1.0)}

STUB = 14.0          # distance a link runs straight out of a port
CLEARANCE = 8.0      # gap kept between a link and an element's box
MAX_LANES = 10       # lane coordinates tried per axis (nearest first)


def _hits(a: Point, b: Point, boxes: Sequence[Rect]) -> bool:
    """Does the axis-aligned segment a-b cross the interior of any box?"""
    x0, x1 = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
    y0, y1 = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
    for left, top, right, bottom in boxes:
        if x1 > left and x0 < right and y1 > top and y0 < bottom:
            return True
    return False


def _tidy(pts: list[Point]) -> list[Point] | None:
    """Drop repeated and collinear points. None if the route doubles back
    on itself (a 180-degree turn), which reads as a glitch, not a route."""
    out: list[Point] = []
    for p in pts:
        if out and abs(p[0] - out[-1][0]) < 1e-6 and abs(p[1] - out[-1][1]) < 1e-6:
            continue
        out.append(p)
    i = 1
    while i < len(out) - 1:
        a, b, c = out[i - 1], out[i], out[i + 1]
        d1 = (b[0] - a[0], b[1] - a[1])
        d2 = (c[0] - b[0], c[1] - b[1])
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(cross) < 1e-6:
            if d1[0] * d2[0] + d1[1] * d2[1] < 0:
                return None
            del out[i]
        else:
            i += 1
    return out if len(out) >= 2 else None


def _length(pts: list[Point]) -> float:
    return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in itertools.pairwise(pts))


def _clear(pts: list[Point], boxes: Sequence[Rect]) -> bool:
    return all(not _hits(a, b, boxes) for a, b in itertools.pairwise(pts))


def _lanes(mid: float, lo: float, hi: float, edges: list[float]) -> list[float]:
    """Candidate coordinates for a bend: the midpoint first, then box edges
    (pushed out by the clearance) nearest the midpoint."""
    cand = {round(mid, 1)}
    for e in edges:
        cand.add(round(e - CLEARANCE, 1))
        cand.add(round(e + CLEARANCE, 1))
    ordered = sorted(cand, key=lambda v: abs(v - mid))
    return ordered[:MAX_LANES]


class Router:
    """Orthogonal router with a time budget.

    ``per_route_s`` caps one link; ``window_s`` / ``total_s`` cap the sum over
    a burst (a full redraw of a big model): once ``total_s`` is spent inside
    one ``window_s`` every further link falls back to a straight line until
    the window rolls over, so a slow layout can never freeze the canvas."""

    def __init__(self, per_route_s: float = 0.02, total_s: float = 0.4,
                 window_s: float = 1.0):
        self.per_route_s = per_route_s
        self.total_s = total_s
        self.window_s = window_s
        self._window_start = 0.0
        self._spent = 0.0
        self.fallbacks = 0            # links drawn straight (any reason)

    def route(self, p0: Point, exit_side: str, p3: Point, entry_side: str,
              obstacles: Sequence[Rect],
              hint: tuple[str, float] | None = None) -> list[Point] | None:
        """The polyline p0..p3 (including both ports), or None to fall back.

        `hint` is a preferred bend lane `("x" | "y", coordinate)` -- see
        `fan_hint` -- tried before the router's own choice."""
        t0 = time.monotonic()
        if t0 - self._window_start > self.window_s:
            self._window_start, self._spent = t0, 0.0
        if self._spent >= self.total_s:
            self.fallbacks += 1
            return None
        try:
            result = self._search(p0, exit_side, p3, entry_side, obstacles,
                                  t0 + self.per_route_s, hint)
        finally:
            self._spent += time.monotonic() - t0
        if result is None:
            self.fallbacks += 1
        return result

    def _search(self, p0, exit_side, p3, entry_side, obstacles, deadline, hint=None):
        d0, d3 = OUT[exit_side], OUT[entry_side]
        s0 = (p0[0] + d0[0] * STUB, p0[1] + d0[1] * STUB)
        s3 = (p3[0] + d3[0] * STUB, p3[1] + d3[1] * STUB)
        boxes = [(l - CLEARANCE / 2, t - CLEARANCE / 2, r + CLEARANCE / 2, b + CLEARANCE / 2)
                 for l, t, r, b in obstacles
                 # the two linked elements are not obstacles: their own
                 # outline is where the ports sit
                 if not (l - 1 <= p0[0] <= r + 1 and t - 1 <= p0[1] <= b + 1)
                 and not (l - 1 <= p3[0] <= r + 1 and t - 1 <= p3[1] <= b + 1)]

        # Only boxes near the link's span matter for lane choice.
        xlo, xhi = sorted((s0[0], s3[0]))
        ylo, yhi = sorted((s0[1], s3[1]))
        near = [b for b in boxes
                if b[2] > xlo - 200 and b[0] < xhi + 200
                and b[3] > ylo - 200 and b[1] < yhi + 200]
        xedges = [v for b in near for v in (b[0], b[2])]
        yedges = [v for b in near for v in (b[1], b[3])]
        xs = _lanes((s0[0] + s3[0]) / 2, xlo, xhi, xedges)
        ys = _lanes((s0[1] + s3[1]) / 2, ylo, yhi, yedges)
        if hint is not None:
            # A fan's shared lane wins whenever it is clear -- even over an
            # L shape, which has fewer bends but bends beside the block
            # rather than on the lane the rest of the fan uses.
            lane = round(hint[1], 1)
            z = ([(lane, s0[1]), (lane, s3[1])] if hint[0] == "x"
                 else [(s0[0], lane), (s3[0], lane)])
            pts = _tidy([p0, s0, *z, s3, p3])
            if pts is not None and _clear(pts, boxes):
                return pts
        mids: list[list[Point]] = []
        if abs(s0[0] - s3[0]) < 1e-6 or abs(s0[1] - s3[1]) < 1e-6:
            mids.append([])                                  # already in line
        mids += [[(s0[0], s3[1])], [(s3[0], s0[1])]]         # L shapes
        mids += [[(x, s0[1]), (x, s3[1])] for x in xs]       # Z, vertical lane
        mids += [[(s0[0], y), (s3[0], y)] for y in ys]       # Z, horizontal lane
        mids += [[(x, s0[1]), (x, y), (s3[0], y)]            # detours
                 for x in xs[:4] for y in ys[:4]]

        best: tuple[float, list[Point]] | None = None
        for _i, mid in enumerate(mids):
            if time.monotonic() > deadline:
                break
            pts = _tidy([p0, s0, *mid, s3, p3])
            if pts is None or not _clear(pts, boxes):
                continue
            # fewest bends, then shortest; earlier candidates win ties
            cost = len(pts) * 1000.0 + _length(pts)
            if best is None or cost < best[0]:
                best = (cost, pts)
        return best[1] if best else None


def fan_hint(exit_side: str, entry_side: str, p0: Point, p3: Point,
             targets: Sequence[Rect], sources: Sequence[Rect]) -> tuple[str, float] | None:
    """A shared bend lane for a link that is one of a fan.

    Every link out of one element (`targets`: the boxes it feeds) or into one
    element (`sources`: the boxes that feed it) should bend on the same line,
    midway between the facing edges of the two columns, rather than each at
    the midpoint of its own two ports -- which, with blocks of different
    widths, staggers the vertical runs. The larger fan decides (ties: the
    fan-out). None if the link is not part of a fan, or does not run along a
    single axis (the L shapes need no lane)."""
    if exit_side in ("left", "right") and entry_side in ("left", "right"):
        axis, i = "x", 0
    elif exit_side in ("top", "bottom") and entry_side in ("top", "bottom"):
        axis, i = "y", 1
    else:
        return None
    lo, hi = i, i + 2                  # a Rect's near and far index on this axis
    sign = OUT[exit_side][i]
    a, b = p0[i], p3[i]
    if len(targets) >= len(sources) and len(targets) > 1:
        # the nearest target edge ahead of the source
        edges = [(t[lo] if sign > 0 else t[hi]) for t in targets]
        ahead = [e for e in edges if (e - a) * sign > 0]
        return (axis, (a + min(ahead, key=lambda e: e * sign)) / 2) if ahead else None
    if len(sources) > 1:
        edges = [(s[hi] if sign > 0 else s[lo]) for s in sources]
        behind = [e for e in edges if (b - e) * sign > 0]
        return (axis, (max(behind, key=lambda e: e * sign) + b) / 2) if behind else None
    return None


def angled(p0: Point, exit_side: str, p3: Point, entry_side: str) -> list[Point] | None:
    """Port, short stub, straight run, short stub, port. None when the stubs
    would point back at each other (the target is behind the source), where
    a plain straight line reads better."""
    d0, d3 = OUT[exit_side], OUT[entry_side]
    s0 = (p0[0] + d0[0] * STUB, p0[1] + d0[1] * STUB)
    s3 = (p3[0] + d3[0] * STUB, p3[1] + d3[1] * STUB)
    pts = _tidy([p0, s0, s3, p3])
    return pts if pts is not None and len(pts) > 2 else None


def path_d(points: Sequence[Point]) -> str:
    """SVG path data for a polyline."""
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)

"""Routing points for wrapped-band hand-overs, without Qt.

The headless counterpart of what the GUI's Auto Layout does after laying a
level out into bands: clear the routing points of the levels it laid out,
then pin each hand-over with two points. The decision itself (which links,
which sides, where the points go) is `raptor.core.fileio.band_handover`,
the same code the GUI calls, so the two cannot disagree.

Points are stored in ``rbd.link_grips`` in scene pixels, like the GUI's.
"""
from __future__ import annotations

from typing import Any

from raptor.api.annotations import _element_records
from raptor.core.fileio.band_handover import handover_points

_LINK_KIND = {"block": "Block", "node": "Node", "event": "Event",
              "hier": "Hier", "hierarchy": "Hier"}


def _kind(value: Any) -> str:
    """A link end type ("RBDBlock", "Block", ...) as an element ref prefix."""
    text = str(value or "").strip()
    if text.startswith("RBD"):
        text = text[3:]
    return _LINK_KIND.get(text.lower(), text)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _next_ids(rbd) -> Any:
    """Ids not used by an annotation or a routing point (they share one
    space in the GUI)."""
    used = {_int(a.get("id")) for a in getattr(rbd, "annotations", []) or []}
    used |= {_int(g.get("id")) for g in getattr(rbd, "link_grips", []) or []}
    i = 1
    while True:
        if i not in used:
            used.add(i)
            yield i
        i += 1


def _scene_boxes(rbd) -> dict[tuple[str, int], tuple[float, float, float, float]]:
    """``(prefix, id) -> (cx, cy, half_w, half_h)`` of every drawn element."""
    boxes = {}
    for rec in _element_records(rbd):
        prefix, _, ident = rec["ref"].partition(":")
        left, top, right, bottom = rec["box"]
        boxes[(prefix, int(ident))] = (
            (left + right) / 2.0, (top + bottom) / 2.0,
            (right - left) / 2.0, (bottom - top) / 2.0)
    return boxes


def _links_touching(link_rows, touching: set[tuple[str, int]]) -> set[int]:
    return {_int(r.get("ID")) for r in link_rows
            if (_kind(r.get("FromType")), _int(r.get("FromID"))) in touching
            or (_kind(r.get("ToType")), _int(r.get("ToID"))) in touching}


def _pin_handovers(rbd, link_rows, direction: str,
                   levels: set[int] | None) -> int:
    boxes = _scene_boxes(rbd)
    ids = _next_ids(rbd)
    added = 0
    for row in link_rows:
        parent = _int(row.get("pID"))
        if levels is not None and parent not in levels:
            continue
        src = boxes.get((_kind(row.get("FromType")), _int(row.get("FromID"))))
        dst = boxes.get((_kind(row.get("ToType")), _int(row.get("ToID"))))
        points = (handover_points(src, dst, direction)
                  if src is not None and dst is not None else None)
        for x, y in points or ():
            rbd.link_grips.append({
                "kind": "route_grip", "id": next(ids), "parent": parent,
                "link": _int(row.get("ID")), "x": x, "y": y, "active": True})
            added += 1
    return added


def refresh_handover_points(rbd, *, direction: str, max_columns: int | None,
                            levels: set[int] | None = None,
                            touching: set[tuple[str, int]] | None = None
                            ) -> dict[str, int]:
    """Replace the routing points of the laid-out scope; pin hand-overs.

    `levels` is the set of hierarchy levels that were laid out (0 = home),
    None for every level. `touching` is a set of ``(kind, id)`` elements
    for a selection-scoped layout: routing points on links that touch one
    are removed and none are added (a selection is not wrapped into bands).

    Points are added only when `max_columns` is set (an explicit band
    wrap); the automatic snake wrap has no hand-overs. Returns
    ``{"removed": n, "added": n}``."""
    if not isinstance(getattr(rbd, "link_grips", None), list):
        rbd.link_grips = []
    sec = rbd.sections.get("LINK_DATA")
    link_rows = [r for r in (sec.rows if sec else []) if _int(r.get("ID")) > 0]
    if touching is not None:
        touched = _links_touching(link_rows, touching)
        drop = [g for g in rbd.link_grips if _int(g.get("link")) in touched]
    else:
        drop = [g for g in rbd.link_grips
                if levels is None or _int(g.get("parent")) in levels]
    dropped = {id(g) for g in drop}
    rbd.link_grips = [g for g in rbd.link_grips if id(g) not in dropped]
    added = 0
    if touching is None and max_columns is not None:
        added = _pin_handovers(rbd, link_rows, direction, levels)
    return {"removed": len(drop), "added": added}

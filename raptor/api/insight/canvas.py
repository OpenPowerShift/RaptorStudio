"""Result layers for the diagram: each element's share, cause and certainty.

Qt-free data for the canvas's "Show on canvas" layers (plan 7.6.1), built
from an attribution dict (`raptor.api.insight.attribution.aggregate`):

* ``downtime-share``: each block's or event's share of system downtime
  (DTCI), the cause that dominates it, and how sure the runs are of it.
* ``lost-capacity``: each element's share of the lost flow, with the same
  cause and certainty.

Every element row carries a band relative to the largest share (``high``
from two thirds of it, ``mid`` from a third, ``low`` below; a ranking,
like the cost and importance layers, not a health threshold), a one-letter
cause glyph, and ``uncertain`` when the ±2 standard-error range is wider
than the share itself (the runs disagree: read the colour as rough).
"""
from __future__ import annotations

from typing import Any

from raptor.api.insight.attribution import CAUSE_LABELS

LAYERS = ("downtime-share", "lost-capacity")

#: One letter per cause for the glyph in the element's corner.
CAUSE_GLYPHS = {
    "repair": "R", "wait_spare": "S", "wait_resource": "P", "logistics": "L",
    "pm": "M", "pm_wait": "W", "dependency": "H", "done": "X", "other": "O",
}

#: Half-width of the ±2 SE range, relative to the share, above which an
#: element is marked uncertain.
UNCERTAIN = 0.5

_TITLES = {"downtime-share": "share of system downtime",
           "lost-capacity": "share of the lost flow"}


def _band(value: float, largest: float) -> str:
    fraction = value / largest if largest > 0 else 0.0
    return "high" if fraction >= 2 / 3 else "mid" if fraction >= 1 / 3 else "low"


def _row(kind: str, oid: int, name: str, stat: dict[str, Any],
         cause: str | None, causes: dict[str, float], largest: float
         ) -> dict[str, Any]:
    value = float(stat.get("mean") or 0.0)
    sem = stat.get("sem")
    half = 2 * float(sem) if isinstance(sem, (int, float)) else None
    low = max(0.0, value - half) if half is not None else None
    high = min(1.0, value + half) if half is not None else None
    return {
        "kind": kind, "id": oid, "name": name, "value": value, "sem": sem,
        "low": low, "high": high, "band": _band(value, largest),
        "main_cause": cause, "cause_label": CAUSE_LABELS.get(cause or "", ""),
        "glyph": CAUSE_GLYPHS.get(cause or "", ""),
        "causes": dict(causes),
        # one run, or every run the same: no spread to judge, so not marked
        "uncertain": bool(half is not None and value > 0
                          and half / value > UNCERTAIN),
    }


def run_layer(workspace: Any, run_id: str, layer: str
              ) -> tuple[dict[str, Any], dict[int, float] | None]:
    """A stored run's layer and, for ``lost-capacity``, each capacity node's
    mean flow (None when the run did not store it).  Raises
    `raptor.api.insight.drivers.NoAttribution` for a run made without
    attribution, and ValueError as `canvas_layer` does."""
    import json

    from raptor.api.insight import drivers
    from raptor.api.run import run_dir
    data = canvas_layer(drivers.read(workspace, run_id), layer)
    flow = None
    path = run_dir(workspace, run_id) / "node_flow.json"
    if layer == "lost-capacity" and path.is_file():
        stored = json.loads(path.read_text())
        system = stored.get("system_node")
        flow = {int(nid): float(row["ave_flow"])
                for nid, row in (stored.get("nodes") or {}).items()
                if int(nid) != system}
    return data, flow


def canvas_layer(attribution: dict[str, Any], layer: str) -> dict[str, Any]:
    """One layer's per-element data, largest share first.

    Raises ValueError for an unknown layer, and for ``lost-capacity`` when
    the attribution has no capacity split (run with capacity analysis)."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}; known layers: {', '.join(LAYERS)}")
    elements = attribution.get("elements") or []
    downtime = {(e["kind"], e["id"]): e for e in elements}
    if layer == "downtime-share":
        source = [(e["kind"], e["id"], e["name"], e.get("dtci") or {}) for e in elements]
    else:
        capacity = (attribution.get("capacity") or {}).get("elements")
        if not capacity:
            raise ValueError("this layer needs the lost-capacity split: run with "
                             "capacity analysis")
        source = [(r["kind"], r["id"], r["name"], r.get("share") or {}) for r in capacity]
    shares = [float(stat.get("mean") or 0.0) for *_rest, stat in source]
    largest = max(shares, default=0.0)
    rows = []
    for kind, oid, name, stat in source:
        if not float(stat.get("mean") or 0.0) > 0:
            continue
        own = downtime.get((kind, oid), {})
        rows.append(_row(kind, oid, name, stat, own.get("main_cause"),
                         own.get("causes") or {}, largest))
    rows.sort(key=lambda r: (-r["value"], r["name"]))
    return {"layer": layer, "measure": _TITLES[layer], "largest": largest,
            "runs": attribution.get("runs", 0), "elements": rows,
            "uncertain_above": UNCERTAIN}

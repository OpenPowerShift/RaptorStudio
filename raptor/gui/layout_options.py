"""Pure decisions behind Auto Layout (no Qt, no view state).

`DiagramView.auto_layout` mixes these with scene side effects. Keeping the decisions
here makes each one testable on plain data.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

DIRECTIONS = ("LR", "RL", "TB", "BT")
LABEL_SIDES = ("left", "right")

#: Layout element kind -> the model section its rows live in.
SECTION_OF = {"block": "BLOCK_DATA", "node": "NODE_DATA",
              "event": "EVENT_DATA", "hier": "HIERARCHY_DATA"}


def normalise_layout_options(
        direction: str | None, max_columns: int | None, spacing: float | None,
        label_side: str | None, get: Callable[[str, Any], Any],
) -> tuple[str, int | None, float, str | None]:
    """Fill unset options from the saved settings (``get(key, default)``) and make
    every value safe: an unknown direction becomes ``LR``, a non-positive column cap
    means no cap, a non-finite or non-positive spacing becomes 1.0, and a label side
    other than left/right means "choose automatically" (None)."""
    if direction is None:
        direction = str(get("layout/direction", "LR"))
    if direction not in DIRECTIONS:
        direction = "LR"
    if max_columns is None:
        try:
            configured = int(get("layout/max_columns", 0) or 0)
        except (TypeError, ValueError):
            configured = 0
        max_columns = configured if configured > 0 else None
    if spacing is None:
        try:
            spacing = float(get("layout/spacing", 1.0) or 1.0)
        except (TypeError, ValueError):
            spacing = 1.0
    if label_side is None:
        label_side = str(get("layout/label_side", "auto"))
    side = label_side if label_side in LABEL_SIDES else None
    spacing = float(spacing)
    if not math.isfinite(spacing) or spacing <= 0.0:
        spacing = 1.0
    return direction, max_columns, spacing, side


def layout_seeds(current_parent: int, hiers: Mapping[int, Any],
                 nodes: Mapping[int, Any]) -> tuple[list[tuple[str, int]], set[tuple[str, int]]]:
    """Where the longest-path layering starts, and which nodes are pinned to the
    centre line.

    Inside a hierarchy the flow enters through its own In node and leaves through its
    Out node. At the top level it starts at the Start markers (``type_node`` 1) and only
    the terminal markers (3 and 5) are pinned: pinning every regular node forced two
    same-layer nodes onto identical coordinates and rendered them stacked."""
    if current_parent and current_parent in hiers:
        h = hiers[current_parent]
        return [("node", h.in_id)], {("node", h.out_id)}
    starts = [("node", nid) for nid, n in nodes.items() if n.type_node == 1]
    pins = {("node", nid) for nid, n in nodes.items() if n.type_node in (3, 5)}
    return starts, pins


def grid_gap(values: Iterable[float], fallback: float) -> float:
    """The smallest positive gap between neighbouring values, or ``fallback``."""
    gaps = [b - a for a, b in itertools.pairwise(sorted(values)) if b - a > 1e-6]
    return min(gaps) if gaps else fallback


def element_extents(
        sizes_px: Mapping[tuple[str, int], tuple[float, float]], reference_scale: float,
) -> tuple[dict[tuple[str, int], tuple[float, float]],
           dict[tuple[str, str], tuple[float, float]]]:
    """From each element's drawn (width, height) in pixels: its half-size in model
    units at the FIXED reference scale (a floor of 0.1 so a degenerate item still
    occupies space), and the drawn half-extents keyed by (section, id).

    The reference scale, not the current view scale, because dividing by the current
    scale would feed one pass's output back into the next and ratchet the diagram."""
    sizes: dict[tuple[str, int], tuple[float, float]] = {}
    render: dict[tuple[str, str], tuple[float, float]] = {}
    for key, (w, h) in sizes_px.items():
        sizes[key] = (max(w / 2.0 / reference_scale, 0.1),
                      max(h / 2.0 / reference_scale, 0.1))
        section = SECTION_OF.get(key[0])
        if section is not None:
            render[(section, str(key[1]))] = (w / 2.0, h / 2.0)
    return sizes, render

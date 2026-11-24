"""One vertical layout for the lost-capacity Sankey, shared by Qt and SVG.

Rules: a single scale `k` (flow units to pixels) serves every node and
link, so the busiest column fills the height and flow is visibly
conserved.  A node is as tall as the larger of its own value and the flow
through it (in or out); links leaving a node are stacked top to bottom in
the order of their targets' vertical position, links arriving are stacked
in the order of their sources', so each link owns its own slot.  Shorter
columns hang near the mean height of the flow coming in.  Links and nodes of at least one pixel each: a tiny
link is drawn 1 px thick and the node grows by the difference.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

MIN_LINK = 1.0
MIN_NODE = 2.0


def _col(node: dict[str, Any]) -> int:
    return int(node.get("column") or 0)


def layout(nodes: Sequence[dict[str, Any]],  # noqa: C901, PLR0912, PLR0915 - one pass each
           links: Sequence[dict[str, Any]],
           height: float, gap: float = 6.0
           ) -> tuple[dict[str, tuple[float, float]], list[tuple[float, float, float] | None]]:
    """Return ({node id: (top, height)}, per-link (thickness, y_source_top,
    y_target_top) or None for a link whose end is missing).  y values are
    relative to the top of the chart area."""
    ids = {str(n["id"]) for n in nodes}
    valid = [i for i, ln in enumerate(links)
             if str(ln.get("source")) in ids and str(ln.get("target")) in ids]
    columns = sorted({_col(n) for n in nodes})
    members = {c: [n for n in nodes if _col(n) == c] for c in columns}
    value = {str(n["id"]): float(n.get("value") or 0.0) for n in nodes}
    flow_in = dict.fromkeys(ids, 0.0)
    flow_out = dict.fromkeys(ids, 0.0)
    for i in valid:
        ln = links[i]
        v = float(ln.get("value") or 0.0)
        flow_out[str(ln["source"])] += v
        flow_in[str(ln["target"])] += v
    flow = {i: max(value[i], flow_in[i], flow_out[i]) for i in ids}

    def heights(k: float) -> dict[str, float]:
        thick_in = dict.fromkeys(ids, 0.0)
        thick_out = dict.fromkeys(ids, 0.0)
        for i in valid:
            ln = links[i]
            t = max(MIN_LINK, float(ln.get("value") or 0.0) * k)
            thick_out[str(ln["source"])] += t
            thick_in[str(ln["target"])] += t
        return {i: max(MIN_NODE, flow[i] * k, thick_in[i], thick_out[i])
                for i in ids}

    def fits(k: float) -> float:
        h = heights(k)
        worst = 1.0
        for c in columns:
            need = sum(h[str(n["id"])] for n in members[c])
            avail = max(1.0, height - gap * max(0, len(members[c]) - 1))
            worst = max(worst, need / avail)
        return worst

    totals = [sum(flow[str(n["id"])] for n in members[c]) for c in columns]
    k = min((max(1.0, height - gap * max(0, len(members[c]) - 1)) / t
             for c, t in zip(columns, totals, strict=True) if t > 0), default=1.0)
    for _ in range(12):  # the 1 px floors can overflow a column: shrink
        over = fits(k)
        if over <= 1.0 + 1e-9:
            break
        k /= over
    h = heights(k)

    # order: first column as given; later columns by the mean position of
    # their sources (stable, so ties keep the order the data came in)
    centre: dict[str, float] = {}

    def place(c: int, order: list[dict[str, Any]]) -> None:
        used = sum(h[str(n["id"])] for n in order) + gap * max(0, len(order) - 1)
        y = max(0.0, (height - used) / 2)
        ids_here = {str(n["id"]) for n in order}
        pulls = [(centre[str(links[i]["source"])],
                  float(links[i].get("value") or 0.0))
                 for i in valid if str(links[i]["target"]) in ids_here
                 and str(links[i]["source"]) in centre]
        weight = sum(w for _, w in pulls)
        if c != columns[0] and weight > 0:
            # a short column hangs near where its flow comes from, not
            # centred: it reads as flowing out of its source
            want = sum(p * w for p, w in pulls) / weight - used / 2
            y = min(max(0.0, want), max(0.0, height - used))
        for n in order:
            centre[str(n["id"])] = y + h[str(n["id"])] / 2
            y += h[str(n["id"])] + gap

    order_of: dict[int, list[dict[str, Any]]] = {}
    for c in columns:
        order = list(members[c])
        if c != columns[0]:
            def bary(n: dict[str, Any]) -> float:
                ws = [(centre[str(links[i]["source"])],
                       float(links[i].get("value") or 0.0))
                      for i in valid if str(links[i]["target"]) == str(n["id"])
                      and str(links[i]["source"]) in centre]
                tot = sum(w for _, w in ws)
                return sum(p * w for p, w in ws) / tot if tot > 0 else 0.0
            order.sort(key=bary)
        order_of[c] = order
        place(c, order)

    top = {i: centre[i] - h[i] / 2 for i in ids}
    node_box = {i: (top[i], h[i]) for i in ids}
    thick = {i: max(MIN_LINK, float(links[i].get("value") or 0.0) * k)
             for i in valid}
    out_y: dict[int, float] = {}
    in_y: dict[int, float] = {}
    for nid in ids:
        outs = sorted((i for i in valid if str(links[i]["source"]) == nid),
                      key=lambda i: centre[str(links[i]["target"])])
        y = top[nid]
        for i in outs:
            out_y[i] = y
            y += thick[i]
        ins = sorted((i for i in valid if str(links[i]["target"]) == nid),
                     key=lambda i: centre[str(links[i]["source"])])
        y = top[nid]
        for i in ins:
            in_y[i] = y
            y += thick[i]
    bands: list[tuple[float, float, float] | None] = [None] * len(links)
    for i in valid:
        bands[i] = (thick[i], out_y[i], in_y[i])
    return node_box, bands


def spread_labels(entries: Sequence[tuple[str, float, float]], lo: float,
                  hi: float) -> dict[str, float]:
    """Vertical label centres for one column, pushed apart so they never
    overlap and kept inside [lo, hi].  `entries` are (id, wanted centre,
    label height); the order of the centres is kept."""
    items = sorted(entries, key=lambda e: e[1])
    pos = [e[1] for e in items]
    size = [e[2] for e in items]
    for i, p in enumerate(pos):
        pos[i] = max(p, lo + size[i] / 2)
        if i:
            pos[i] = max(pos[i], pos[i - 1] + (size[i - 1] + size[i]) / 2)
    for i in range(len(pos) - 1, -1, -1):
        limit = hi - size[i] / 2
        if i + 1 < len(pos):
            limit = min(limit, pos[i + 1] - (size[i] + size[i + 1]) / 2)
        pos[i] = min(pos[i], limit)
    return {items[i][0]: pos[i] for i in range(len(items))}

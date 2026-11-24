"""Hierarchy-scoped views over a finished attribution.

The attribution stores every hierarchy's roll-up (``attribution
["hierarchies"]``) and, since the hierarchy work, each row's members,
starters and per-run shares.  This module turns those rows into the data
the hierarchy charts need: resolving a path, filtering an attribution to a
subtree, cumulative downtime curves, a state ribbon and the nodes of a
sunburst.

It is Qt-free and does not compute a measure differently from the tables:
every number starts as an element's DTCI or an outage's red interval.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from raptor.api.insight.attribution import CAUSE_LABELS

#: Categorical colours for hierarchy identity, in reading order.  The hues
#: follow Okabe-Ito where its lighter entries still met 3:1 on the light
#: surface; the amber, blue, purple and olive are darker or lighter versions
#: of the same hues so every mark also reads on the dark surface.  Cause
#: colours stay separate: identity here, cause in the element charts.
SERIES_COLOURS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7",
                  "#A66A00", "#2A7396", "#7A6BA3", "#8A6B14")


def path_text(row: dict[str, Any]) -> str:
    """`Intake > Pump house`, or the name when the path is missing."""
    path = [str(part) for part in row.get("path") or []]
    return " > ".join(path) if path else str(row.get("name") or "")


def member_keys(row: dict[str, Any]) -> set[tuple[str, int]]:
    """The (kind, id) keys inside one hierarchy row."""
    return {(str(item.get("kind")), int(item.get("id")))
            for item in row.get("members") or []
            if item.get("kind") is not None and item.get("id") is not None}


def rows(attribution: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every hierarchy row, largest share first (zero rows included)."""
    return list((attribution or {}).get("hierarchies") or [])


def top_rows(attribution: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The top-level hierarchies, in model order (the scorecard's order)."""
    found = [row for row in rows(attribution) if int(row.get("parent") or 0) == 0]
    return sorted(found, key=lambda row: (str(row.get("name") or "")))


def resolve(attribution: dict[str, Any] | None,  # noqa: C901
            ref: int | str | None) -> dict[str, Any]:
    """One hierarchy row by id, name or path.

    A string may be a numeric id, an exact name, a case-insensitive name, or
    a path written `A > B` or `A / B`.  Raises ValueError naming what is
    available, because a silent system-wide chart after a bad selector is
    the ambiguity this work exists to remove.
    """
    if ref is None or ref == "" or ref == 0 or ref == "0":
        raise ValueError("no hierarchy selected")
    available = rows(attribution)
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        wanted = int(ref)
        for row in available:
            if int(row.get("id") or 0) == wanted:
                return row
    text = str(ref).strip()

    def pick(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            paths = ", ".join(path_text(row) for row in matches[:8])
            raise ValueError(f"ambiguous hierarchy {ref!r}: {paths}. "
                             "Use the full path.")
        return None

    found = pick([row for row in available
                  if text == str(row.get("name") or "")])
    if found is not None:
        return found
    low = text.lower()
    found = pick([row for row in available
                  if low == str(row.get("name") or "").lower()])
    if found is not None:
        return found
    for row in available:
        if low == path_text(row).lower():
            return row
    wanted_parts = [part.strip().lower() for part in
                    text.replace("/", ">").split(">")]
    for row in available:
        parts = [part.strip().lower() for part in
                 path_text(row).replace("/", ">").split(">")]
        if wanted_parts == parts:
            return row
    names = ", ".join(path_text(row) for row in available[:8]) or "none"
    raise ValueError(f"unknown hierarchy {ref!r}; known hierarchies: {names}")


def resolve_many(attribution: dict[str, Any] | None,
                 refs: Iterable[int | str] | None) -> list[dict[str, Any]]:
    """Rows for a selection, without duplicates, in the order asked for."""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for ref in refs or ():
        row = resolve(attribution, ref)
        hid = int(row.get("id") or 0)
        if hid not in seen:
            seen.add(hid)
            out.append(row)
    return out


def descendants(attribution: dict[str, Any] | None,
                row: dict[str, Any]) -> list[dict[str, Any]]:
    """The row and every hierarchy below it, depth first."""
    out = [row]
    changed = True
    while changed:
        changed = False
        for item in rows(attribution):
            if item in out:
                continue
            if int(item.get("parent") or 0) in {int(r.get("id") or 0) for r in out}:
                out.append(item)
                changed = True
    return out


def scope_meta(attribution: dict[str, Any] | None,
               row: dict[str, Any] | None) -> dict[str, Any]:
    """The scope object a ChartSpec carries and every surface prints."""
    if row is None:
        return {"kind": "system", "id": 0, "name": "System",
                "path": ["System"], "members": 0, "starters": 0, "share": None}
    dtci = row.get("dtci") or {}
    return {
        "kind": "hierarchy",
        "id": int(row.get("id") or 0),
        "name": str(row.get("name") or ""),
        "path": list(row.get("path") or [row.get("name") or ""]),
        "members": int(row.get("member_count") or 0),
        "starters": int(row.get("starter_count") or 0),
        "share": dtci.get("mean"),
        "sem": dtci.get("sem"),
        "main_cause": row.get("main_cause"),
    }


def depth_text(depth: int | None, *, nested: bool = False) -> str:
    """`top level`, `level 2`, or `every level`, for a chart subtitle."""
    if not depth or int(depth) <= 1:
        return "every level" if nested and not depth else "top level"
    return (f"top {int(depth)} levels" if nested else f"level {int(depth)}")


def scope_label(meta: dict[str, Any] | None) -> str:
    """`System`, `Intake > Pump house`, or `Intake + Disinfection`."""
    if not meta:
        return "System"
    path = [str(part) for part in meta.get("path") or []]
    if meta.get("kind") == "selection":
        return " + ".join(path) if path else "selection"
    return " > ".join(path) or "System"


def annotate(spec: Any, meta: dict[str, Any] | None,
             *, note: str = "") -> Any:
    """Put the scope on a ChartSpec, its subtitle and its provenance.

    The title stays short; the subtitle and the caption carry the scope.
    This is deliberately on the spec, not in a GUI-only label, so a saved
    PNG, a CSV row and a report figure cannot lose it.
    """
    if meta is None or spec.scope is not None:
        return spec
    spec.scope = meta
    scope = scope_label(meta)
    if spec.subtitle:
        if "scope:" not in spec.subtitle:
            spec.subtitle = f"{spec.subtitle} · scope: {scope}"
    else:
        spec.subtitle = f"scope: {scope}"
    if note:
        spec.caption = f"{spec.caption} {note}".strip()
    spec.provenance = {**(spec.provenance or {}), "scope": meta}
    return spec



def direct_keys(row: dict[str, Any]) -> set[tuple[str, int]]:
    """The elements whose ParentId is this hierarchy (not a child's)."""
    items = row.get("direct_members")
    if items is None:
        return member_keys(row)
    return {(str(item.get("kind")), int(item.get("id")))
            for item in items
            if item.get("kind") is not None and item.get("id") is not None}


#: (the hierarchies list, its length, parent id -> rows below it).  One
#: entry: the treemap asks for the children of every row of one attribution,
#: and scanning all rows for each made it quadratic (2,760 hierarchies took
#: half a minute).  Held by identity, so another attribution rebuilds it.
_CHILD_INDEX: tuple[list[Any], int, dict[int, list[dict[str, Any]]]] | None = None


def _children_of(attribution: dict[str, Any] | None
                 ) -> dict[int, list[dict[str, Any]]]:
    global _CHILD_INDEX
    held = (attribution or {}).get("hierarchies") or []
    cached = _CHILD_INDEX
    if cached is not None and cached[0] is held and cached[1] == len(held):
        return cached[2]
    index: dict[int, list[dict[str, Any]]] = {}
    for item in held:
        index.setdefault(int(item.get("parent") or 0), []).append(item)
    _CHILD_INDEX = (held, len(held), index)
    return index


def child_rows(attribution: dict[str, Any] | None,
               row: dict[str, Any]) -> list[dict[str, Any]]:
    """The hierarchies directly below one row, largest share first."""
    hid = int(row.get("id") or 0)
    found = _children_of(attribution).get(hid, [])
    return sorted(found, key=lambda item: -(
        float((item.get("dtci") or {}).get("mean") or 0.0)))


def max_depth(attribution: dict[str, Any] | None) -> int:
    return max((int(row.get("depth") or 1) for row in rows(attribution)), default=1)


def unassigned_share(attribution: dict[str, Any] | None) -> float:
    """Share of system downtime that no top-level hierarchy holds.

    Treemap and sunburst draw this as ``Not in a hierarchy``.  The ranking
    charts keep one row per named hierarchy, so they quote the remainder in
    the caption instead of inventing a hierarchy row.
    """
    assigned = sum(float((row.get("dtci") or {}).get("mean") or 0.0)
                   for row in top_rows(attribution))
    system = (attribution or {}).get("system") or {}
    total = float((system.get("red_time") or {}).get("mean") or 0.0)
    return max(0.0, min(1.0, 1.0 - assigned)) if total > 0 else 0.0


def rows_at_depth(attribution: dict[str, Any] | None,
                  depth: int | None) -> list[dict[str, Any]]:
    """Hierarchy rows at one level (1 = top level); no depth: every row."""
    found = rows(attribution)
    if depth is None or depth <= 0:
        return list(found)
    picked = [row for row in found if int(row.get("depth") or 1) == int(depth)]
    return picked or [row for row in found if int(row.get("depth") or 1) == 1]


def child_parts(attribution: dict[str, Any] | None,
                row: dict[str, Any]) -> list[dict[str, Any]]:
    """The direct contents of one hierarchy: child hierarchies and elements.

    Values are fractions of the parent's downtime, so a parent row is never
    added to its children in one chart.  A child hierarchy stays one block
    (its own children are a deeper view); its elements are not repeated at
    this level.  An ``other`` part keeps the children from pretending to sum
    to the parent when the roll-up includes something not listed.
    """
    parent = float((row.get("dtci") or {}).get("mean") or 0.0)
    if parent <= 0:
        return []
    by_key = {(str(item.get("kind")), int(item.get("id") or 0)): item
              for item in (attribution or {}).get("elements") or []}
    parts: list[dict[str, Any]] = []
    for child in child_rows(attribution, row):
        parts.append({"name": str(child.get("name") or ""),
                      "path": path_text(child),
                      "value": float((child.get("dtci") or {}).get("mean") or 0.0),
                      "kind": "hierarchy", "row": child,
                      "causes": child.get("causes") or {},
                      "main_cause": child.get("main_cause"),
                      "cause": child.get("main_cause")})
    own = direct_keys(row) if row.get("direct_members") is not None else (
        member_keys(row) - {key for child in child_rows(attribution, row)
                            for key in member_keys(child)})
    for key in sorted(own):
        item = by_key.get(key)
        if item is None:
            continue
        parts.append({"name": str(item.get("name") or ""),
                      "path": f"{path_text(row)} > {item.get('name', '')}",
                      "value": float((item.get("dtci") or {}).get("mean") or 0.0),
                      "kind": "element", "row": None, "element": item,
                      "causes": item.get("causes") or {},
                      "main_cause": item.get("main_cause"),
                      "cause": item.get("main_cause")})
    total = sum(float(part["value"]) for part in parts)
    for part in parts:
        part["share"] = float(part["value"]) / parent
    if parent > total + 1e-12:
        parts.append({"name": "other", "path": path_text(row) + " > other",
                      "value": parent - total, "share": (parent - total) / parent,
                      "kind": "other", "row": None, "element": None,
                      "causes": {}, "main_cause": None})
    parts.sort(key=lambda part: -float(part["share"]))
    return parts

def scoped(attribution: dict[str, Any] | None,
           row: dict[str, Any] | None) -> dict[str, Any]:
    """A copy of the attribution with only one hierarchy's elements.

    The per-element DTCI stays a share of **system** downtime: the maps and
    captions keep saying "of system downtime", and a scope that hides the
    other elements does not silently renormalise every number.  Outage-path
    shares are renormalised to the selected subtree, because an outage
    anatomy of a scope has to fill its own canvas.
    """
    source = attribution or {}
    if row is None:
        return dict(source)
    keys = member_keys(row)
    elements = [item for item in source.get("elements") or []
                if (str(item.get("kind")), int(item.get("id") or 0)) in keys]
    # Causes are re-weighted by red time; the element rows already carry
    # their own cause split, and a plain mean would weigh a element with a
    # minute of downtime like one with a day.
    weighted: dict[str, float] = {}
    for item in elements:
        weight = float((item.get("red_time") or {}).get("mean") or 0.0)
        for cause, share in (item.get("causes") or {}).items():
            weighted[cause] = weighted.get(cause, 0.0) + weight * float(share)
    total = sum(weighted.values())
    causes = {cause: value / total for cause, value in weighted.items()
              if value > 0} if total else {}
    allowed = {int(item.get("id") or 0) for item in descendants(source, row)}
    sequences = [seq for seq in source.get("sequences") or []
                 if (seq.get("steps") and int(
                     seq["steps"][0].get("hierarchy_id") or 0) in allowed)]
    seq_total = sum(float(seq.get("share") or 0.0) for seq in sequences)
    if seq_total > 0:
        sequences = [{**seq, "share": float(seq.get("share") or 0.0) / seq_total}
                     for seq in sequences]
    capacity = source.get("capacity")
    if capacity:
        cap = dict(capacity)
        cap["elements"] = [item for item in capacity.get("elements") or []
                           if (str(item.get("kind")),
                               int(item.get("id") or 0)) in keys]
        capacity = cap
    return {**source, "elements": elements, "causes": causes,
            "sequences": sequences, "capacity": capacity,
            "scope": scope_meta(source, row)}


def per_run(attribution: dict[str, Any] | None,
            row: dict[str, Any] | None) -> list[dict[str, float]]:
    """Per-run hierarchy shares, in the shape the dot charts expect.

    The field is called ``availability`` so the existing dot plot formats
    it as a fraction (1 = 100 %); the chart is labelled "share of system
    downtime", never availability.
    """
    if row is None:
        return []
    out = []
    for item in row.get("trials") or []:
        # A run with no system outage has no share to average: keep its red
        # time in the profile only when the denominator exists.
        if "system_red_time" in item and not item["system_red_time"]:
            continue
        out.append({"availability": float(item.get("dtci") or 0.0),
                    "hierarchy_red_time": float(item.get("red_time") or 0.0)})
    return out


def cost_rows(costs: Sequence[dict[str, Any]],
              row: dict[str, Any] | None,
              elements: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """The cost rows whose element is inside one hierarchy.

    Cost rows hold names, not (kind, id); the names come from the scoped
    element rows, which carry both.
    """
    if row is None:
        return list(costs)
    names = {str(item.get("name")) for item in elements}
    return [item for item in costs if str(item.get("name")) in names]


def importance_rows(importance: dict[str, Any] | None,
                    row: dict[str, Any] | None,
                    elements: Sequence[dict[str, Any]] = ()
                    ) -> dict[str, Any] | None:
    """The same importance table with rows outside the hierarchy removed."""
    if importance is None or row is None:
        return importance
    names = {str(item.get("name")) for item in elements}
    out = {**importance}
    metrics = {}
    for metric, entry in (importance.get("metrics") or {}).items():
        metrics[metric] = {**entry,
                           "rows": [item for item in entry.get("rows") or []
                                    if str(item.get("block")) in names]}
    out["metrics"] = metrics
    return out


def _merge_pairs(flat: Sequence[float]) -> list[tuple[float, float]]:
    pairs = sorted((float(flat[i]), float(flat[i + 1]))
                   for i in range(0, len(flat) - 1, 2)
                   if float(flat[i + 1]) > float(flat[i]))
    merged: list[tuple[float, float]] = []
    for a, b in pairs:
        if merged and a <= merged[-1][1] + 1e-12:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _cumulative(pairs: list[tuple[float, float]], t: float) -> float:
    """Downtime in `pairs` up to `t`, with no pass over the whole list."""
    total = 0.0
    for a, b in pairs:
        if t <= a:
            break
        total += min(b, t) - a
    return total


def cumulative_layers(attribution: dict[str, Any] | None,
                      selected: Sequence[dict[str, Any]],
                      *, points: int = 160) -> tuple[list[dict[str, Any]], float]:
    """Mean cumulative system downtime caused by each hierarchy over time.

    A hierarchy's outage intervals are the system-down intervals whose
    outage it started.  The curve is cumulative, so its slope is the
    hierarchy's contribution to downtime and its end value is that
    hierarchy's red time.  Runs shorter than the longest are held at their
    final value, not dropped.
    """
    record = (attribution or {}).get("outages") or {}
    runs = list(record.get("runs") or [])
    if not runs or not any("hier" in run for run in runs):
        return [], 0.0
    end = max(float(run.get("end") or 0.0) for run in runs)
    if end <= 0:
        return [], 0.0
    grid = [end * i / max(1, points - 1) for i in range(max(2, points))]
    layers: list[dict[str, Any]] = []
    for index, row in enumerate(selected):
        hid = str(row.get("id"))
        curves = []
        for run in runs:
            start = float(run.get("start") or 0.0)
            finish = float(run.get("end") or 0.0)
            pairs = _merge_pairs((run.get("hier") or {}).get(hid) or [])
            pairs = [(max(a, start), min(b, finish)) for a, b in pairs
                     if min(b, finish) > max(a, start)]
            points_out = []
            for t in grid:
                if t > finish:
                    points_out.append(_cumulative(pairs, finish))
                else:
                    points_out.append(_cumulative(pairs, t))
            curves.append(points_out)
        if not curves:
            continue
        mean = [sum(curve[i] for curve in curves) / len(curves)
                for i in range(len(grid))]
        layers.append({
            "name": path_text(row), "hierarchy_id": int(row.get("id") or 0),
            "t": grid, "y": mean, "role": "line",
            "colour": SERIES_COLOURS[index % len(SERIES_COLOURS)],
            "read": "value", "width": 2.0,
        })
    # A quiet reference line for the whole system, so a hierarchy is never
    # compared with nothing.
    system = []
    for run in runs:
        start = float(run.get("start") or 0.0)
        finish = float(run.get("end") or 0.0)
        pairs = [(max(a, start), min(b, finish)) for a, b in
                 _merge_pairs(run.get("red") or [])
                 if min(b, finish) > max(a, start)]
        system.append([_cumulative(pairs, min(t, finish)) for t in grid])
    if system:
        layers.append({
            "name": "system", "hierarchy_id": 0, "t": grid,
            "y": [sum(curve[i] for curve in system) / len(system)
                  for i in range(len(grid))],
            "role": "line", "tone": "muted", "read": "value",
            "dash": True, "width": 1.2,
        })
    return layers, end


def ribbon_rows(attribution: dict[str, Any] | None,
                selected: Sequence[dict[str, Any]] | None = None,
                run: int = 1) -> list[dict[str, Any]]:
    """One system-down ribbon row per hierarchy for one run."""
    record = (attribution or {}).get("outages") or {}
    runs = list(record.get("runs") or [])
    if not runs or not (1 <= int(run) <= len(runs)):
        return []
    one = runs[int(run) - 1]
    start, end = float(one.get("start") or 0.0), float(one.get("end") or 0.0)
    if end <= start:
        return []
    chosen = list(selected or rows(attribution))
    out = []
    for row in chosen:
        flat = (one.get("hier") or {}).get(str(row.get("id"))) or []
        pairs = _merge_pairs(flat)
        path = path_text(row)
        out.append({
            "name": path,
            "label": path.split(" > ")[-1],
            "hierarchy_id": int(row.get("id") or 0),
            "start": start, "end": end,
            "red": [[a, b] for a, b in pairs],
            "downtime": sum(b - a for a, b in pairs),
            "span": max(end - start, 1e-12),
        })
    return out


def _element_causes(elements: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Lost flow by cause, summed over the capacity elements."""
    causes: dict[str, float] = {}
    for item in elements:
        lost = float((item.get("lost_energy") or {}).get("mean") or 0.0)
        for cause, share in (item.get("causes") or {}).items():
            causes[cause] = causes.get(cause, 0.0) + lost * float(share)
    return causes


def sankey(attribution: dict[str, Any] | None,  # noqa: C901, PLR0912, PLR0915 - one layout, two shapes
           selected: Sequence[dict[str, Any]] | None = None, *,
           by_hierarchy: bool = True,
           show_delivered: bool = True
           ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Nodes and links for the capacity Sankey.

    Generated flow splits into delivered and lost; lost splits into
    hierarchies (unless `by_hierarchy` is off) and then into causes.
    `show_delivered=False` starts at the lost flow, so a small loss is not
    squeezed by the delivered branch.  Every path adds up to
    ``lost_energy``, so a reader can follow a link from the source to a
    cause without double counting.
    """
    capacity = (attribution or {}).get("capacity") or {}
    elements = list(capacity.get("elements") or [])
    total_lost = float((capacity.get("lost_energy") or {}).get("mean") or 0.0)
    delivered = float((capacity.get("delivered_energy") or {}).get("mean") or 0.0)
    # `generated` is a flow rate; the Sankey carries energy, so its source
    # is what was actually delivered plus what was lost.
    generated = delivered + total_lost
    if total_lost <= 0 or (by_hierarchy and not elements):
        return [], []

    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    if show_delivered:
        nodes.append({"id": "generated", "name": "Generated energy", "column": 0,
                      "value": generated})
        nodes.append({"id": "delivered", "name": "Delivered", "column": 1,
                      "value": delivered})
        nodes.append({"id": "lost", "name": "Lost", "column": 1,
                      "value": total_lost})
        links = [{"source": "generated", "target": "delivered", "value": delivered,
                  "cause": None},
                 {"source": "generated", "target": "lost", "value": total_lost,
                  "cause": None}]
        lost_column = 1
    else:
        nodes.append({"id": "lost", "name": "Lost capacity", "column": 0,
                      "value": total_lost})
        lost_column = 0

    if not by_hierarchy:
        cause_flow = {cause: float(stat.get("mean") or 0.0)
                      for cause, stat in (capacity.get("causes") or {}).items()}
        if not cause_flow:
            cause_flow = _element_causes(elements)
        remainder = max(0.0, total_lost - sum(cause_flow.values()))
        if remainder > 1e-9 * max(1.0, total_lost):  # not float noise
            cause_flow["unattributed"] = remainder
        for cause, amount in cause_flow.items():
            if amount <= 0:
                continue
            cause_id = f"cause-{cause}"
            name = ("Not attributed" if cause == "unattributed"
                    else CAUSE_LABELS.get(str(cause), str(cause)))
            nodes.append({"id": cause_id, "name": name,
                          "column": lost_column + 1, "value": amount})
            # the legend reads `cause`: the remainder has no cause colour
            links.append({"source": "lost", "target": cause_id,
                          "value": amount,
                          "cause": None if cause == "unattributed" else cause})
        return nodes, links

    chosen = list(selected or rows(attribution))
    by_key = {(str(item.get("kind")), int(item.get("id") or 0)): item
              for item in elements}
    lost_by_hier: dict[int, float] = {}
    for row in chosen:
        keys = member_keys(row)
        lost_by_hier[int(row.get("id") or 0)] = sum(
            float((by_key[key].get("lost_energy") or {}).get("mean") or 0.0)
            for key in keys if key in by_key)
    if not any(lost_by_hier.values()):
        return [], []
    hier_column = lost_column + 1
    cause_column = hier_column + 1
    selected_total = sum(lost_by_hier.values())
    remainder = max(0.0, total_lost - selected_total)
    if remainder > 1e-9 * max(1.0, total_lost):  # not float noise
        nodes.append({"id": "unattributed", "name": "Not in this selection",
                      "column": hier_column, "value": remainder})
        links.append({"source": "lost", "target": "unattributed",
                      "value": remainder, "cause": None})
    for row in chosen:
        hid = int(row.get("id") or 0)
        value = lost_by_hier.get(hid, 0.0)
        if value <= 0:
            continue
        node_id = f"hier-{hid}"
        nodes.append({"id": node_id, "name": path_text(row), "column": hier_column,
                      "value": value})
        links.append({"source": "lost", "target": node_id, "value": value,
                      "cause": None})
        # The cause split follows the lost flow, not the downtime shares:
        # a cause that throttles capacity without stopping the system must
        # still appear under the lost flow it caused.
        causes: dict[str, float] = {}
        for key in member_keys(row):
            item = by_key.get(key)
            if item is None:
                continue
            lost = float((item.get("lost_energy") or {}).get("mean") or 0.0)
            for cause, share in (item.get("causes") or {}).items():
                causes[cause] = causes.get(cause, 0.0) + lost * float(share)
        if not causes:
            causes = {cause: value * float(share)
                      for cause, share in (row.get("causes") or {}).items()}
        for cause, amount in causes.items():
            if amount <= 0:
                continue
            cause_id = f"cause-{cause}"
            if cause_id not in {node["id"] for node in nodes}:
                nodes.append({"id": cause_id,
                              "name": CAUSE_LABELS.get(str(cause), str(cause)),
                              "column": cause_column,
                              "value": 0.0})
            next(node for node in nodes if node["id"] == cause_id)["value"] += amount
            links.append({"source": node_id, "target": cause_id,
                          "value": amount, "cause": cause})
    return nodes, links


def sunburst(attribution: dict[str, Any] | None,
             selected: Sequence[dict[str, Any]] | None = None
             ) -> list[dict[str, Any]]:
    """Nodes for a sunburst that follows every hierarchy level.

    Ring 0 is each top-level hierarchy's share; each next ring is that
    node's direct children (a child hierarchy or an element) normalised
    within the parent.  A parent's children never include a grandchild
    twice, so the rings stay comparable.
    """
    chosen = list(selected or top_rows(attribution))
    assigned = sum(float((row.get("dtci") or {}).get("mean") or 0.0)
                   for row in chosen)
    # At system level the whole circle is 100 % of system downtime; a
    # hierarchy roll-up need not cover every element, so the remainder is a
    # root of its own rather than inflating the assigned shares.
    system = (attribution or {}).get("system") or {}
    whole = (selected is None and
             float((system.get("red_time") or {}).get("mean") or 0.0) > 0)
    total = 1.0 if whole else (assigned or 1.0)
    nodes: list[dict[str, Any]] = []

    def layout(row: dict[str, Any], parent: str, depth: int,
               parent_value: float, node_id: str) -> None:
        if depth > 8:
            return
        nodes.append({"id": node_id, "name": path_text(row), "parent": parent,
                      "depth": depth, "value": parent_value,
                      "kind": "hierarchy"})
        for part in child_parts(attribution, row):
            child_value = parent_value * float(part.get("share") or 0.0)
            if child_value <= 0:
                continue
            child = (part.get("row") if part["kind"] == "hierarchy"
                     else part.get("element") if part["kind"] == "element" else None)
            child = child or {}
            child_id = (f"{node_id}-h-{child.get('id')}"
                        if part["kind"] == "hierarchy"
                        else f"{node_id}-e-{child.get('kind')}-{child.get('id')}"
                        if part["kind"] == "element" else f"{node_id}-other")
            if part["kind"] == "hierarchy":
                layout(part["row"], node_id, depth + 1, child_value, child_id)
            else:
                nodes.append({"id": child_id, "name": part["name"],
                              "parent": node_id, "depth": depth + 1,
                              "value": child_value, "kind": part["kind"]})

    for row in chosen:
        share = float((row.get("dtci") or {}).get("mean") or 0.0) / total
        if share <= 0:
            continue
        layout(row, "", 0, share, str(row.get("id") or 0))
    if whole and assigned < total - 1e-9:
        nodes.append({"id": "unassigned", "name": "Not in a hierarchy",
                      "parent": "", "depth": 0, "value": total - assigned,
                      "kind": "other"})
    return nodes


def sunburst_layout(nodes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every sunburst node angular start/end and a top-level colour.

    A child's angle is a share of its parent's angle, so a five-level model
    stays concentric and each ring fills its parent instead of restarting.
    """
    if not nodes:
        return []
    items = [dict(node) for node in nodes]
    ids = {str(node.get("id")) for node in items}
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for node in items:
        parent = str(node.get("parent") or "")
        if parent and parent in ids:
            children.setdefault(parent, []).append(node)
        else:
            roots.append(node)
    roots.sort(key=lambda node: -float(node.get("value") or 0.0))
    root_index = {str(node.get("id")): i for i, node in enumerate(roots)}
    import math
    total = sum(float(node.get("value") or 0.0) for node in roots)
    if total <= 0:
        return items

    def place(group: list[dict[str, Any]], start: float, end: float,
              depth: int, root_id: str) -> None:
        group_total = sum(float(node.get("value") or 0.0) for node in group)
        if group_total <= 0:
            return
        cursor = start
        for node in sorted(group, key=lambda item: -float(item.get("value") or 0.0)):
            span = (end - start) * float(node.get("value") or 0.0) / group_total
            node["start"] = cursor
            node["end"] = cursor + span
            node["depth"] = depth
            node["root_id"] = root_id
            node["root_index"] = root_index.get(root_id, 0)
            cursor += span
            kids = children.get(str(node.get("id")), [])
            if kids:
                place(kids, node["start"], node["end"], depth + 1, root_id)

    start = -math.pi / 2
    cursor = start
    for node in roots:
        share = float(node.get("value") or 0.0) / total
        place([node], cursor, cursor + 2 * math.pi * share, 0, str(node.get("id")))
        cursor += 2 * math.pi * share
    return items


def overlaps(chosen: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    """Pairs where one hierarchy contains the other, by path prefix."""
    found: list[tuple[str, str]] = []
    for i, left in enumerate(chosen):
        lp = [str(part) for part in left.get("path") or []]
        for right in chosen[i + 1:]:
            rp = [str(part) for part in right.get("path") or []]
            if len(lp) < len(rp) and rp[:len(lp)] == lp:
                found.append((str(left.get("name")), str(right.get("name"))))
            elif len(rp) < len(lp) and lp[:len(rp)] == rp:
                found.append((str(right.get("name")), str(left.get("name"))))
    return found

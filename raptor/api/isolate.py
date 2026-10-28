"""Run one hierarchy on its own: a port extension (Raptor 7 has no such feature).

`scan` lists what crosses the hierarchy's boundary; `extract` builds an ordinary
standalone model from the hierarchy and applies a documented treatment to each
hazard. The extract runs on the normal engine, can be saved, and opens in Raptor 7.
Nothing here changes the source model.

The extract treats the hierarchy's input as always available: its In node becomes
the Start node and its Out node the End node. Everything outside is dropped.

Treatments (the defaults first):

* outside dependencies: `up` (the master is assumed always up), `system` (follow
  the extract's own End node), `refuse`. There is no `proxy` treatment: a stand-in
  block with the master's own distributions has to sit on a Start-to-End path in
  Raptor (a dead-end block hangs Raptor 7's path analysis at 4%, found
  2026-10-10), and every such placement changes the system's state;
* shared spare and resource pools: `full` (the whole pool), `share` (scaled by the
  share of users inside, rounded down, at least 1), `refuse`; individual values
  can be set with `items`;
* shared random streams: `keep` (information only), `separate` (each shared draw
  gets its own unused stream 1..110).

No treatment can make an isolated run draw what the hierarchy would have drawn
inside the whole model.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from raptor.core.fileio import builder
from raptor.core.fileio.rbd import RbdFile

DEP_TREATMENTS = ("up", "system", "refuse")
POOL_TREATMENTS = ("full", "share", "refuse")
STREAM_TREATMENTS = ("keep", "separate")

#: A stream number above this selects a shared system stream (`GetStream`).
_SHARED_STREAM = 200
_DEDICATED_STREAMS = range(1, 111)


class IsolationError(ValueError):
    """The hierarchy cannot be isolated (not found, the top level, ...)."""


class IsolationRefused(IsolationError):
    """A hazard was found and the chosen treatment is `refuse`."""


@dataclass(frozen=True)
class Hazard:
    kind: str                 # dependency | pool | resource | stream | note
    elements: tuple[str, ...]
    detail: str
    treatment: str = ""
    effect: str = ""          # optimistic | pessimistic | neutral | approximate | information

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "elements": list(self.elements),
                "detail": self.detail, "treatment": self.treatment,
                "effect": self.effect}


@dataclass
class IsolationRecord:
    """Carried by every result of an isolated run (see "Labelling the results")."""
    hierarchy: str
    hierarchy_id: int
    treatments: dict[str, Any]
    hazards: list[Hazard] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def banner(self) -> str:
        return (f"Isolated run of {self.hierarchy} — a port extension, not a "
                f"whole-model result")

    def to_dict(self) -> dict[str, Any]:
        return {"extension": "isolated-hierarchy", "banner": self.banner,
                "hierarchy": self.hierarchy, "hierarchy_id": self.hierarchy_id,
                "treatments": self.treatments,
                "hazards": [h.to_dict() for h in self.hazards],
                "notes": list(self.notes)}

    def text(self) -> str:
        out = [self.banner, "",
               f"Hierarchy: {self.hierarchy} (id {self.hierarchy_id})",
               "The hierarchy's input is assumed always available.", ""]
        t = self.treatments
        out.append(f"Treatments: dependencies={t.get('deps')}, pools={t.get('pools')}, "
                   f"streams={t.get('streams')}")
        for k, v in sorted((t.get("items") or {}).items()):
            out.append(f"  {k} = {v}")
        out.append("")
        for h in self.hazards:
            out.append(f"[{h.kind}] {', '.join(h.elements)}: {h.detail}"
                       + (f" -> {h.treatment} ({h.effect})" if h.treatment else ""))
        out += [""] + self.notes
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# The hierarchy's contents
# ---------------------------------------------------------------------------

def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _rows(rbd: RbdFile, section: str) -> list[dict]:
    sec = rbd.sections.get(section)
    return sec.rows if sec else []


@dataclass
class _Parts:
    hier: dict
    hier_ids: set[int]            # the hierarchy and every hierarchy inside it
    blocks: list[dict]
    events: list[dict]
    nodes: list[dict]
    hiers: list[dict]             # nested hierarchies only
    links: list[dict]
    in_id: int
    out_id: int

    def block_ids(self) -> set[int]:
        return {_int(b["ID"]) for b in self.blocks}


def _find_hierarchy(rbd: RbdFile, selector: str | int) -> dict:
    rows = [r for r in _rows(rbd, "HIERARCHY_DATA") if _int(r.get("ID")) != 0]
    sel = str(selector).strip()
    if ":" in sel:
        kind, value = sel.split(":", 1)
        if kind.strip().lower() in ("hier", "hierarchy"):
            sel = value.strip()
    if sel.isdigit():
        hits = [r for r in rows if _int(r["ID"]) == int(sel)]
    else:
        hits = [r for r in rows if r.get("hierarchyName") == sel]
    if not hits:
        names = ", ".join(r.get("hierarchyName", "?") for r in rows) or "none"
        raise IsolationError(
            f"no hierarchy {selector!r} in this model (hierarchies: {names}). "
            "The top level is the whole model: use an ordinary run")
    if len(hits) > 1:
        raise IsolationError(
            f"{len(hits)} hierarchies are called {selector!r}; use the id")
    return hits[0]


def _parts(rbd: RbdFile, hier: dict) -> _Parts:
    hrows = [r for r in _rows(rbd, "HIERARCHY_DATA") if _int(r.get("ID")) != 0]
    ids = {_int(hier["ID"])}
    grew = True
    while grew:
        grew = False
        for r in hrows:
            if _int(r.get("Parent")) in ids and _int(r["ID"]) not in ids:
                ids.add(_int(r["ID"]))
                grew = True
    inside = {str(i) for i in ids}
    return _Parts(
        hier=hier, hier_ids=ids,
        blocks=[r for r in _rows(rbd, "BLOCK_DATA")
                if _int(r.get("ID")) != 0 and str(_int(r.get("ParentId"))) in inside],
        events=[r for r in _rows(rbd, "EVENT_DATA")
                if str(_int(r.get("ParentId"))) in inside],
        nodes=[r for r in _rows(rbd, "NODE_DATA")
               if str(_int(r.get("parent"))) in inside],
        hiers=[r for r in hrows if _int(r["ID"]) in ids and _int(r["ID"]) != _int(hier["ID"])],
        links=[r for r in _rows(rbd, "LINK_DATA") if str(_int(r.get("pID"))) in inside],
        in_id=_int(hier.get("in")), out_id=_int(hier.get("out")))


def _label(kind: str, row: dict) -> str:
    name = row.get("blockName") or row.get("eventName") or row.get("Name") \
        or row.get("hierarchyName") or ""
    return f"{kind} {name or row.get('ID')}"


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def _outside_dependencies(rbd: RbdFile, p: _Parts) -> list[tuple[str, dict, str, dict | None]]:
    """(label, row, kind, master row | None) for every element inside that follows
    something outside, or the whole system. `kind` is `system`, `block`, `node`,
    `event`, `hierarchy` or `self` (the enclosing hierarchy itself)."""
    blocks = {_int(r["ID"]): r for r in _rows(rbd, "BLOCK_DATA")}
    nodes = {_int(r["ID"]): r for r in _rows(rbd, "NODE_DATA")}
    events = {_int(r["ID"]): r for r in _rows(rbd, "EVENT_DATA")}
    hiers_by_out = {_int(r.get("out")): r for r in _rows(rbd, "HIERARCHY_DATA")
                    if _int(r.get("ID")) != 0}
    inside_blocks = p.block_ids()
    inside_nodes = {_int(r["ID"]) for r in p.nodes} - {p.in_id}
    inside_events = {_int(r["ID"]) for r in p.events}
    inside_out = {_int(r.get("out")) for r in p.hiers}
    found: list[tuple[str, dict, str, dict | None]] = []

    def classify(num: int, dtype: str):
        if num == -2:
            return "system", None
        if num <= 0:
            return None
        if dtype == "RBDBlock":
            return ("block", blocks.get(num)) if num not in inside_blocks else None
        if dtype == "RBDEvent":
            return ("event", events.get(num)) if num not in inside_events else None
        if dtype == "RBDHier":
            if num == p.out_id:
                return "self", None
            return ("hierarchy", hiers_by_out.get(num)) if num not in inside_out else None
        if dtype == "RBDNode":
            if num == p.out_id:
                return "self", None
            return ("node", nodes.get(num)) if num not in inside_nodes else None
        return None

    for b in p.blocks:
        c = classify(_int(b.get("DepNum")), b.get("DepType") or "None")
        if c:
            found.append((_label("Block", b), b, c[0], c[1]))
    for n in p.nodes:
        if "depNum" not in n:
            continue
        c = classify(_int(n.get("depNum")), n.get("depType") or "None")
        if c:
            found.append((_label("Node", n), n, c[0], c[1]))
    return found


def _pool_users(rbd: RbdFile, p: _Parts) -> dict[tuple[str, str], tuple[int, int]]:
    """{(kind, pool name): (users inside, users in the whole model)}."""
    inside = p.block_ids()
    users: dict[tuple[str, str], list[int]] = {}
    for b in _rows(rbd, "BLOCK_DATA"):
        if _int(b.get("ID")) == 0:
            continue
        i = _int(b["ID"]) in inside
        keys = []
        if b.get("SpType") == "Pooled" and b.get("SparePoolName"):
            keys.append(("pool", b["SparePoolName"]))
        if _int(b.get("NumRes")) > 0 and b.get("Resource1Name") not in (None, "", "none", "None"):
            keys.append(("resource", b["Resource1Name"]))
        for k in keys:
            u = users.setdefault(k, [0, 0])
            u[1] += 1
            u[0] += 1 if i else 0
    return {k: (v[0], v[1]) for k, v in users.items()}


def _stream_users(rbd: RbdFile, p: _Parts) -> tuple[list[tuple[str, str, int]], set[int]]:
    """([(label, column, stream)] for inside draws on a shared stream,
    the set of dedicated stream numbers used anywhere inside)."""
    shared: list[tuple[str, str, int]] = []
    used: set[int] = set()
    for b in p.blocks:
        for col in ("FStream", "RStream"):
            s = _int(b.get(col))
            if abs(s) > _SHARED_STREAM:
                shared.append((_label("Block", b), col, s))
            elif s:
                used.add(abs(s))
    for e in p.events:
        s = _int(e.get("failStream"))
        if abs(s) > _SHARED_STREAM:
            shared.append((_label("Event", e), "failStream", s))
        elif s:
            used.add(abs(s))
    return shared, used


def scan(rbd: RbdFile, hierarchy: str | int, treatments: dict[str, Any] | None = None) -> list[Hazard]:
    """Every hazard at the hierarchy's boundary, with the treatment that would be
    applied (`treatments` as for `extract`)."""
    t = _normalise(treatments)
    p = _parts(rbd, _find_hierarchy(rbd, hierarchy))
    return _hazards(rbd, p, t)


def _normalise(treatments: dict[str, Any] | None) -> dict[str, Any]:
    supplied = {k: v for k, v in (treatments or {}).items()
                if v is not None}
    unknown = set(supplied) - {"deps", "pools", "streams", "items"}
    if unknown:
        raise IsolationError(
            f"unknown treatment key(s) {', '.join(sorted(unknown))}; use "
            "deps, pools, streams or items")
    t: dict[str, Any] = {"deps": "up", "pools": "full", "streams": "keep", "items": {}}
    t.update(supplied)
    t["items"] = dict(t.get("items") or {})
    for key, allowed in (("deps", DEP_TREATMENTS), ("pools", POOL_TREATMENTS),
                         ("streams", STREAM_TREATMENTS)):
        if t[key] not in allowed:
            raise IsolationError(
                f"unknown {key} treatment {t[key]!r}; use one of {', '.join(allowed)}")
    for key, value in t["items"].items():
        suffix = str(key).rsplit(".", 1)[-1].lower()
        if suffix == "dep":
            if value not in DEP_TREATMENTS:
                raise IsolationError(
                    f"unknown dependency treatment {value!r} for {key}")
        elif suffix in ("stock", "newsp", "units"):
            try:
                int(value)
            except (TypeError, ValueError):
                raise IsolationError(
                    f"{key} must be an integer, got {value!r}") from None
        else:
            raise IsolationError(
                f"unknown item treatment {key!r}; use <element>.dep or "
                "<pool|resource>:<name>.stock|newsp|units")
    return t


def _dep_treatment(t: dict[str, Any], kind: str, row: dict, key: str) -> str:
    chosen = t["items"].get(f"{key}.dep")
    if chosen:
        if chosen not in DEP_TREATMENTS:
            raise IsolationError(f"unknown dependency treatment {chosen!r} for {key}")
        return chosen
    if kind == "system":
        return "system" if t["deps"] == "up" else t["deps"]
    return t["deps"]


def _item_key(kind: str, row: dict) -> str:
    return f"{kind}:{row.get('ID')}"


def _hazards(rbd: RbdFile, p: _Parts, t: dict[str, Any]) -> list[Hazard]:
    out: list[Hazard] = []
    effects = {"up": "optimistic: no idle, maintenance or failure from outside",
               "system": "neutral if this hierarchy brings the system down, else optimistic",
               "refuse": ""}
    for label, row, kind, master in _outside_dependencies(rbd, p):
        elem = "block" if "blockName" in row else "node"
        tr = _dep_treatment(t, kind, row, _item_key(elem, row))
        if kind == "self":
            out.append(Hazard(
                "dependency", (label,),
                "follows the enclosing hierarchy itself; it will follow the extract's End node",
                "system", "neutral"))
            continue
        follows = ("the whole system" if kind == "system"
                   else _label(kind.capitalize(), master) if master else f"a {kind} outside")
        out.append(Hazard("dependency", (label,),
                          f"follows {follows}, which is outside the hierarchy",
                          tr, effects.get(tr, "")))
    for (kind, name), (inside, total) in sorted(_pool_users(rbd, p).items()):
        if inside == 0 or inside == total:
            continue
        tr = t["pools"]
        item = [k for k in t["items"] if k.startswith(f"{kind}:{name}.")]
        out.append(Hazard(
            kind, (f"{kind} {name}",),
            f"{inside} of its {total} users are inside the hierarchy",
            "set" if item else tr,
            "as chosen" if item else
            ("optimistic: no competition from outside blocks" if tr == "full"
             else "approximate: a fair split, not real contention")))
    shared, _used = _stream_users(rbd, p)
    if shared:
        names = tuple(sorted({s[0] for s in shared}))
        out.append(Hazard(
            "stream", names,
            f"{len(shared)} draws use a shared system stream (stream numbers above "
            f"{_SHARED_STREAM}); inside the whole model they are drawn in model-wide event order",
            t["streams"], "information"))
    if _int(p.hier.get("Phs", p.hier.get("Phasing", 0))):
        out.append(Hazard("note", (_label("Hierarchy", p.hier),),
                          "the hierarchy is itself phased; an isolated run ignores its own Cut/Linked state",
                          "", "information"))
    return out


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

def extract(rbd: RbdFile, hierarchy: str | int,
            treatments: dict[str, Any] | None = None) -> tuple[RbdFile, IsolationRecord]:
    """Build the standalone model. `treatments`:
    ``{"deps": "up|system|refuse", "pools": "full|share|refuse",
    "streams": "keep|separate", "items": {"block:12.dep": "system",
    "pool:Spares.stock": 2, ...}}``. The source model is not modified."""
    t = _normalise(treatments)
    hier = _find_hierarchy(rbd, hierarchy)
    p = _parts(rbd, hier)
    hazards = _hazards(rbd, p, t)
    refused = [h for h in hazards if h.treatment == "refuse"]
    if refused or (t["pools"] == "refuse" and any(h.kind in ("pool", "resource") for h in hazards)):
        lines = "\n".join(f"  {h.kind}: {', '.join(h.elements)}: {h.detail}" for h in hazards
                          if h.treatment == "refuse" or (t["pools"] == "refuse" and h.kind in ("pool", "resource")))
        raise IsolationRefused(
            f"hierarchy {hier.get('hierarchyName')!r} crosses the model boundary and the "
            f"treatment is 'refuse':\n{lines}")
    if p.in_id == 0 or p.out_id == 0:
        raise IsolationError(f"hierarchy {hier.get('hierarchyName')!r} has no In/Out node")

    out = copy.deepcopy(rbd)
    notes: list[str] = []
    level_cut = _int(hier.get("level"), 1)
    hid = _int(hier["ID"])

    def keep(section: str, rows: list[dict]) -> None:
        sec = out.sections[section]
        sec.rows = [copy.deepcopy(r) for r in rows]

    zero_blocks = [r for r in _rows(rbd, "BLOCK_DATA") if _int(r.get("ID")) == 0]
    zero_hiers = [r for r in _rows(rbd, "HIERARCHY_DATA") if _int(r.get("ID")) == 0]
    keep("BLOCK_DATA", zero_blocks + p.blocks)
    keep("EVENT_DATA", p.events)
    keep("NODE_DATA", p.nodes)
    keep("HIERARCHY_DATA", zero_hiers + p.hiers)
    keep("LINK_DATA", p.links)

    # Re-parent: the hierarchy's direct contents become top level.
    for sec, col in (("BLOCK_DATA", "ParentId"), ("EVENT_DATA", "ParentId"),
                     ("NODE_DATA", "parent"), ("LINK_DATA", "pID"),
                     ("HIERARCHY_DATA", "Parent")):
        for r in out.sections[sec].rows:
            if _int(r.get(col)) == hid:
                r[col] = "0"
    for r in out.sections["HIERARCHY_DATA"].rows:
        if _int(r.get("ID")) != 0 and "level" in r:
            r["level"] = str(max(1, _int(r["level"]) - level_cut))

    # In -> Start, Out -> End. Ids are kept so every link still resolves.
    for n in out.sections["NODE_DATA"].rows:
        nid = _int(n["ID"])
        if nid == p.in_id:
            builder.set_fields(n, Name="start", Type=1, k=0, n=0, Cold=0, PR=1, Autos=1,
                               **{"k*": 0}, Phases=0)
        elif nid == p.out_id:
            builder.set_fields(n, Name="end", Type=3, Out=0, Cold=0, PR=1, Autos=1, Phases=0)

    _filter_comments(out)
    _apply_dependencies(out, rbd, p, t, hazards, notes)
    _apply_pools(out, rbd, p, t, notes)
    if t["streams"] == "separate":
        _separate_streams(out, notes)

    builder.sync_counts(out)
    _name_the_extract(out, hier)
    notes.append("Costs, weak-link and capacity results describe the extract only. "
                 "FailSim and CycleSim count the hierarchy's failures, not the system's.")
    if _int(out.sections["SYSTEM_DATA"].rows[0].get("capacityAnalysis")):
        notes.append("Capacity analysis: the extract's Start generates the model's flowGenerated "
                     "unless it is changed (system.flow); the hierarchy's real input flow may be less.")
    if not (p.blocks or p.events or p.hiers):
        raise IsolationError(f"hierarchy {hier.get('hierarchyName')!r} contains no blocks, events or hierarchies")
    record = IsolationRecord(hierarchy=hier.get("hierarchyName", ""), hierarchy_id=hid,
                             treatments=t, hazards=hazards, notes=notes)
    return out, record


def _filter_comments(out: RbdFile) -> None:
    """Drop comments about elements the extract no longer has. Raptor 7 looks each
    comment's element up by (kind, id) when it opens a file; a dangling one crashes
    it (found by opening the first extract: "MODSIM Application ... Click OK to exit")."""
    sec = out.sections.get("COMMENTS")
    if sec is None:
        return
    have = {
        "block": {_int(r["ID"]) for r in out.sections["BLOCK_DATA"].rows if _int(r["ID"]) != 0},
        "node": {_int(r["ID"]) for r in out.sections["NODE_DATA"].rows},
        "event": {_int(r["ID"]) for r in out.sections["EVENT_DATA"].rows},
        "hier": {_int(r["ID"]) for r in out.sections["HIERARCHY_DATA"].rows if _int(r["ID"]) != 0},
    }
    sec.rows = [r for r in sec.rows
                if str(r.get("Type", "")).lower() == "system"
                or _int(r.get("ID")) in have.get(str(r.get("Type", "")).lower(), set())]


def _name_the_extract(out: RbdFile, hier: dict) -> None:
    out.file_name = f"{hier.get('hierarchyName', 'hierarchy')}_isolated.rbd"


def _apply_dependencies(out: RbdFile, src: RbdFile, p: _Parts, t: dict[str, Any],
                        hazards: list[Hazard], notes: list[str]) -> None:
    by_label = {}
    for _, row, kind, master in _outside_dependencies(src, p):
        by_label[("block" if "blockName" in row else "node", _int(row["ID"]))] = (kind, master)
    rows = ([("block", r) for r in out.sections["BLOCK_DATA"].rows if _int(r.get("ID")) != 0]
            + [("node", r) for r in out.sections["NODE_DATA"].rows if "depNum" in r])
    for elem, row in rows:
        if elem == "node" and _int(row["ID"]) in (p.in_id, p.out_id):
            continue
        key = by_label.get((elem, _int(row["ID"])))
        if key is None:
            continue
        kind, master = key
        num_col, type_col = ("DepNum", "DepType") if elem == "block" else ("depNum", "depType")
        if kind == "self":
            builder.set_fields(row, **{num_col: -2, type_col: "None"})
            continue
        tr = _dep_treatment(t, kind, row, _item_key(elem, row))
        if tr == "system" or (tr == "up" and kind == "system" and t["deps"] == "up"):
            builder.set_fields(row, **{num_col: -2, type_col: "None"})
        else:
            builder.set_fields(row, **{num_col: 0, type_col: "None"})


def _apply_pools(out: RbdFile, src: RbdFile, p: _Parts, t: dict[str, Any], notes: list[str]) -> None:
    users = _pool_users(src, p)
    inside_pools = {(k, n) for (k, n), (i, _tot) in users.items() if i > 0}
    for sec, kind, name_col in (("SPARE_POOL_DATA", "pool", "PoolName"),
                                ("RESOURCE_DATA", "resource", "PoolName")):
        s = out.sections.get(sec)
        if s is None:
            continue
        kept = []
        for r in s.rows:
            key = (kind, r.get(name_col))
            if key not in inside_pools:
                continue
            inside, total = users[key]
            if t["pools"] == "share" and inside < total:
                share = inside / total
                cols = ("Stock", "NewSp", "Level", "Quan") if kind == "pool" else ("NumIn",)
                for c in cols:
                    v = _int(r.get(c))
                    if v > 0:
                        r[c] = str(max(1, math.floor(v * share)))
                notes.append(f"{kind} {key[1]} scaled by {inside}/{total} (rounded down, at least 1).")
            for item, val in t["items"].items():
                if item.startswith(f"{kind}:{key[1]}."):
                    field_name = item.split(".", 1)[1].lower()
                    col = {"stock": "Stock", "newsp": "NewSp", "units": "NumIn"}.get(field_name)
                    if col is None or col not in r:
                        raise IsolationError(f"cannot set {item}: no such field")
                    r[col] = str(int(val))
                    notes.append(f"{item} set to {val}.")
            kept.append(r)
        s.rows = kept


def _separate_streams(out: RbdFile, notes: list[str]) -> None:
    used = set()
    wanted: list[tuple[dict, str]] = []
    for kind, section, cols in (("block", "BLOCK_DATA", ("FStream", "RStream")),
                                ("event", "EVENT_DATA", ("failStream",))):
        for r in sorted(out.sections[section].rows, key=lambda r: _int(r.get("ID"))):
            if kind == "block" and _int(r.get("ID")) == 0:
                continue
            for c in cols:
                s = _int(r.get(c))
                if abs(s) > _SHARED_STREAM:
                    wanted.append((r, c))
                elif s:
                    used.add(abs(s))
    free = [n for n in _DEDICATED_STREAMS if n not in used]
    if len(wanted) > len(free):
        raise IsolationError(
            f"{len(wanted)} draws share a stream but only {len(free)} dedicated streams "
            f"(1..110) are unused; use the 'keep' treatment")
    for (r, c), n in zip(wanted, free):
        old = _int(r.get(c))
        r[c] = str(-n if old < 0 else n)
    if wanted:
        notes.append(f"{len(wanted)} shared-stream draws moved to their own streams "
                     f"({free[0]}..{free[len(wanted) - 1]}).")

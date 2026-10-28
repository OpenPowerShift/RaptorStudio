"""Read one element of a model back in friendly, named form.

``get_element`` is the read counterpart of ``update_element``: every field
it reports uses the same friendly name ``update_element`` accepts, grouped
by what it does (failure, repair, sparing, dependency, ...), with
distributions decoded to a name, named parameters and a mean.  No raw
storage columns are exposed.
"""
from __future__ import annotations

from typing import Any

from raptor.api.build import (
    _as_bool, _as_float, _as_int, resolve_element,
)
from raptor.api.distributions import describe_dist
from raptor.api.workspace import Workspace

_KIND = {"BLOCK_DATA": "Block", "NODE_DATA": "Node", "EVENT_DATA": "Event",
         "HIERARCHY_DATA": "Hierarchy", "LINK_DATA": "Link"}
_NAME_COL = {"Block": "blockName", "Node": "Name", "Event": "eventName",
             "Hierarchy": "hierarchyName", "Hier": "hierarchyName"}
_SECTION = {"Block": "BLOCK_DATA", "Node": "NODE_DATA", "Event": "EVENT_DATA",
            "Hierarchy": "HIERARCHY_DATA", "Hier": "HIERARCHY_DATA"}
_NODE_TYPES = {1: "start", 2: "regular", 3: "end", 4: "in", 5: "out"}

#: What a dependency's ``dep_type`` means, in plain words.
_DEP_TARGET = {"RBDBlock": "block", "RBDNode": "node", "RBDEvent": "event",
               "RBDHier": "hierarchy"}


def _name_of(rbd: Any, kind: str, elem_id: Any) -> str | None:
    col = _NAME_COL.get(kind)
    sec = _SECTION.get(kind)
    if not col or not sec:
        return None
    for row in rbd.section(sec).rows:
        if str(row.get("ID")) == str(elem_id):
            return row.get(col) or None
    return None


def _ref(rbd: Any, kind: str, elem_id: Any) -> dict[str, Any]:
    kind = "Hierarchy" if kind == "Hier" else kind
    return {"kind": kind, "id": _as_int(elem_id),
            "name": _name_of(rbd, kind, elem_id)}


def _connections(rbd: Any, kind: str, elem_id: str) -> dict[str, list[dict]]:
    kind_key = "Hier" if kind == "Hierarchy" else kind
    up: list[dict] = []
    down: list[dict] = []
    for link in rbd.section("LINK_DATA").rows:
        if link.get("ToType") in (kind, kind_key) and str(link.get("ToID")) == str(elem_id):
            up.append({**_ref(rbd, link.get("FromType", ""), link.get("FromID")),
                       "link_id": _as_int(link.get("ID"))})
        if link.get("FromType") in (kind, kind_key) and str(link.get("FromID")) == str(elem_id):
            down.append({**_ref(rbd, link.get("ToType", ""), link.get("ToID")),
                         "link_id": _as_int(link.get("ID"))})
    return {"upstream": up, "downstream": down}


def _params(row: dict, cols: tuple[str, ...]) -> list[float]:
    return [_as_float(row.get(c)) or 0.0 for c in cols]


def _dist(row: dict, dist_col: str, cols: tuple[str, ...],
          n_col: str | None = None) -> dict[str, Any]:
    code = _as_int(row.get(dist_col))
    if code is None:
        return {}
    params = _params(row, cols)
    if n_col is not None:
        n = _as_int(row.get(n_col))
        if n is not None and 0 <= n <= len(params):
            params = params[:n]
    return describe_dist(code, params)


def dependency(rbd: Any, row: dict) -> dict[str, Any]:
    """Decode a block's dependency into who it follows and what happens.

    ``dep_num`` positive names an element of ``dep_type``; ``-1`` means the
    block follows its *own* node ("local"), ``-2`` the whole system.  When
    the thing it follows goes down, the block does one of four things, drawn
    at random using the ``effects_percent`` split.
    """
    dep_type = row.get("DepType") or "None"
    num = _as_int(row.get("DepNum")) or 0
    if num == 0 and dep_type in ("None", ""):
        return {"dependent": False,
                "meaning": "Independent: this block answers only to its own "
                           "failure and repair draws."}
    effects = {
        "nothing": _as_float(row.get("DepNothing")) or 0.0,
        "idle": _as_float(row.get("DepIdle")) or 0.0,
        "pm": _as_float(row.get("DepPM")) or 0.0,
        "failure": _as_float(row.get("DepFail")) or 0.0,
    }
    out: dict[str, Any] = {"dependent": True, "effects_percent": effects}
    if num == -1:
        out["follows"] = {"kind": "local",
                          "description": "its own node's up/down status"}
        target = "its own node"
    elif num == -2:
        out["follows"] = {"kind": "system",
                          "description": "the whole system's up/down status"}
        target = "the whole system"
    else:
        kind = _DEP_TARGET.get(dep_type, dep_type)
        if kind == "hierarchy":
            # An RBDHier dependency stores its OUT marker's node id, not
            # the hierarchy id (Raptor 7 convention).
            node = next((n for n in rbd.section("NODE_DATA").rows
                         if _as_int(n.get("ID")) == num), None)
            hid = _as_int(node.get("parent")) if node else None
            hier_row = next((h for h in rbd.section("HIERARCHY_DATA").rows
                         if _as_int(h.get("ID")) == hid), None)
            name = (hier_row or {}).get("hierarchyName")
            out["follows"] = {"kind": kind, "id": num, "name": name}
            target = f"hierarchy {name or num}"
        else:
            ref = _ref(rbd, {"block": "Block", "node": "Node",
                             "event": "Event"}.get(kind, kind), num)
            out["follows"] = {"kind": kind, "id": num,
                              "name": ref.get("name")}
            target = f"{kind} {ref.get('name') or num}"
    parts = []
    if effects["idle"]:
        parts.append(f"{effects['idle']:g}% forced Idle (paused: its failure "
                     "clock stops and resumes when the target is back)")
    if effects["nothing"]:
        parts.append(f"{effects['nothing']:g}% no effect")
    if effects["pm"]:
        parts.append(f"{effects['pm']:g}% sent for preventive maintenance")
    if effects["failure"]:
        parts.append(f"{effects['failure']:g}% induced to fail")
    out["meaning"] = (f"When {target} goes down, this block is: "
                      + "; ".join(parts or ["unaffected"]) + ".")
    return out


def _block(rbd: Any, row: dict, full: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "failure": {**_dist(row, "FDist", ("FParam1", "FParam2", "FParam3"),
                            "#FParam"),
                    "stream": _as_int(row.get("FStream"))},
        "repair": {**_dist(row, "RDist", ("RParam1", "RParam2", "RParam3"),
                           "#RParam"),
                   "stream": _as_int(row.get("RStream"))},
        "dependency": dependency(rbd, row),
    }
    pre = _dist(row, "Pre-LDTDist", ("Param1#1", "Param2#1", "Param3#1"))
    post = _dist(row, "Post-LDTDist", ("Param1#2", "Param2#2", "Param3#2"))
    if pre or post:
        out["logistics_delay"] = {"before_repair": pre, "after_repair": post}

    sp_type = row.get("SpType") or ""
    sparing: dict[str, Any] = {"type": sp_type}
    if sp_type and sp_type != "Infinite":
        sparing.update({
            "pool": row.get("SparePoolName"),
            "stock": _as_int(row.get("Stock")),
            "new_spares": _as_int(row.get("NewSp")),
            "restock_every": _as_float(row.get("ArriveEvery")),
            "rso": _as_int(row.get("RSO")),
            "eso": _as_int(row.get("ESO")),
            "emer_time": _as_float(row.get("EmerTime")),
            "slo": _as_int(row.get("SLO")),
            "level": _as_int(row.get("Level")),
            "quan": _as_int(row.get("Quan")),
            "slo_time": _as_float(row.get("SLOTime")),
        })
    out["sparing"] = sparing

    uses_pm = _as_bool(row.get("usesPM"))
    pm: dict[str, Any] = {"enabled": uses_pm}
    if uses_pm or full:
        pm.update({
            "pm_freq": _as_float(row.get("PMFreq")),
            "pm_duration": _dist(row, "Dist",
                                 ("Param1#3", "Param2#3", "Param3#3")),
            "triggered": _as_bool(row.get("pmTriggered")),
            "trigger": row.get("pmTrig"),
            "pm_stagger_time": _as_float(row.get("PMStaggerTime")),
        })
    out["maintenance"] = pm

    resource_count = _as_int(row.get("NumRes")) or 0
    if resource_count or full:
        out["resources"] = {"num_res": resource_count,
                            "resource1_name": row.get("Resource1Name")}
    out["phasing"] = _as_bool(row.get("Phasing"))
    if full:
        out["standby_stress"] = _as_float(row.get("SBStress"))
        out["growth_decay"] = {
            "type": _as_int(row.get("GDType")),
            "rate": _as_float(row.get("GDRate")),
            "limit": _as_float(row.get("GDLimit")),
        }
        out["costs"] = {k: _as_float(row.get(c)) for k, c in (
            ("initial", "initial"), ("running", "Running"),
            ("standby", "standby"), ("idle", "idle"),
            ("rep_hold", "repHold"), ("rep_per_time", "repPerTime"),
            ("rep_fixed", "repFixed"), ("done_per_time", "donePerTime"),
            ("done_fixed", "doneFixed"), ("pm_hold_cost", "pmHoldCost"),
            ("pm_cost", "pmCost"), ("pm_fixed_cost", "pmFixedCost"),
            ("per_spare", "perSpare"), ("emer_shipping", "emerShipping"),
        )}
    return out


def _node(row: dict) -> dict[str, Any]:
    t = _as_int(row.get("Type"))
    return {
        "node_type": _NODE_TYPES.get(t, t) if t is not None else None,
        "k": _as_int(row.get("k")),
        "n": _as_int(row.get("n")),
        "full_flow": _as_int(row.get("fullflow")),
        "div_flow": _as_int(row.get("divFlow")),
    }


def _event(row: dict) -> dict[str, Any]:
    return {"prob": _as_float(row.get("failVal")),
            "fail_stream": _as_int(row.get("failStream")),
            "phasing": _as_bool(row.get("Phasing"))}


def get_element(workspace: Workspace, model_id: str, selector: str,
                detail: str = "summary") -> dict[str, Any]:
    """Return one element (block, node, event, hierarchy or link) by name or
    ``Kind:N`` selector, in friendly named form.

    ``detail="full"`` adds the groups that are usually defaults (costs,
    growth/decay, standby stress, inactive maintenance settings).
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    section, row = resolve_element(rbd, selector)
    kind = _KIND.get(section, section)
    full = detail == "full"
    eid = row.get("ID")

    if kind == "Link":
        return {
            "kind": "Link", "id": _as_int(eid),
            "from": _ref(rbd, row.get("FromType", ""), row.get("FromID")),
            "to": _ref(rbd, row.get("ToType", ""), row.get("ToID")),
            "nom_flow": _as_int(row.get("nomFlow")),
            "max_flow": _as_int(row.get("MaxFlow")),
            "cap_pri": _as_int(row.get("CapPri")),
            "cold_pri": _as_int(row.get("ColdPri")),
            "a_prob": _as_float(row.get("AProb")),
            "auto_time": _as_float(row.get("AutoTime")),
            "man_time": _as_float(row.get("ManTime")),
        }

    out: dict[str, Any] = {
        "kind": kind, "id": _as_int(eid),
        "name": row.get(_NAME_COL.get(kind, "Name")),
        "parent": _as_int(row.get("ParentId", row.get("parent", row.get("Parent")))),
        "position": {"x": _as_float(row.get("xPos")),
                     "y": _as_float(row.get("yPos"))},
        **_connections(rbd, kind, str(eid)),
    }
    if kind == "Block":
        out.update(_block(rbd, row, full))
    elif kind == "Node":
        out.update(_node(row))
    elif kind == "Event":
        out.update(_event(row))
    return out

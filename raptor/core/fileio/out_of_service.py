"""Elements taken out of service for a what-if (U27).

A modeller often wants to ask "what if this unit were gone?" or "what if
this unit never gave trouble?" without deleting it or retyping its data.
Each element can carry one of two states, kept in the .rbds `out_of_service`
key (legacy .rbd has no place for it, so it is not written there):

* ``"cut"``    — the element is down for the whole run: its path is broken.
* ``"linked"`` — the element never fails: its path is as good as a wire.

Neither changes the stored element. `apply(rbd)` returns a COPY of the
model with the states turned into ordinary data the engine already runs
(no new engine behaviour), and every run path (GUI, CLI, MCP, the studies)
goes through `raptor.core.engine.load.load_model`, which calls it:

* a linked block: a Fixed time to failure of 10^15 (Raptor has no
  "never fails" failure distribution), no preventive maintenance, no
  dependency, no phasing, starts new;
* a cut block: starts failed with no repair (None) and no sparing, no
  preventive maintenance, no dependency, no phasing, so it is Done
  ("No_repair") from time zero;
* an event: probability of success 1 (linked) or 0 (cut), no phasing;
* a hierarchy: every block and event inside it, at any depth.

A cut block still counts in costs that accrue while it is down, and its
own results (failures, downtime) describe the state it was put in, not
its normal behaviour.
"""
from __future__ import annotations

import copy
from typing import Any

STATES = ("cut", "linked")
KINDS = ("block", "event", "hier")

#: Fixed (19) time to failure far beyond any run: Raptor has no "never fails"
#: failure distribution (None is refused for failure, only allowed for repair).
NEVER = "1e15"
_LINKED_BLOCK = {"FDist": "19", "#FParam": "1", "FParam1": NEVER, "FParam2": "0",
                 "FParam3": "0", "usesPM": "F", "DepNum": "0", "DepType": "",
                 "Phasing": "F", "SimStart": "3", "AmtExhausted": "0"}
_CUT_BLOCK = {"RDist": "18", "SpType": "None", "usesPM": "F", "DepNum": "0",
              "DepType": "", "Phasing": "F", "SimStart": "6"}


def key(kind: str, oid: int) -> str:
    return f"{kind}:{int(oid)}"


def states(rbd: Any) -> dict[str, str]:
    """{"block:3": "cut", ...}: only well-formed entries."""
    raw = getattr(rbd, "out_of_service", None) or {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        kind, _, oid = str(k).partition(":")
        if kind in KINDS and oid.isdigit() and v in STATES:
            out[f"{kind}:{int(oid)}"] = str(v)
    return out


def set_state(rbd: Any, kind: str, oid: int, state: str | None) -> None:
    """Put an element in `state` ("cut"/"linked"), or back in service (None)."""
    if kind not in KINDS:
        raise ValueError(f"only a block, event or hierarchy can be taken out of service, not {kind!r}")
    if state is not None and state not in STATES:
        raise ValueError(f"state must be 'cut', 'linked' or None, not {state!r}")
    current = dict(getattr(rbd, "out_of_service", None) or {})
    if state is None:
        current.pop(key(kind, oid), None)
    else:
        current[key(kind, oid)] = state
    rbd.out_of_service = current


def _id(row: dict) -> int:
    try:
        return int(float(row.get("ID") or 0))
    except (TypeError, ValueError):
        return 0


def _parent(row: dict, column: str) -> int:
    try:
        return int(float(row.get(column) or 0))
    except (TypeError, ValueError):
        return 0


def effective(rbd: Any) -> tuple[dict[int, str], dict[int, str]]:
    """({block id: state}, {event id: state}) after hierarchies are spread
    over what is inside them. An element's own state wins over a
    hierarchy's; the nearer hierarchy wins over a farther one."""
    own = states(rbd)
    if not own:
        return {}, {}
    parents = {_id(r): _parent(r, "Parent") for r in rbd.sections["HIERARCHY_DATA"].rows if _id(r)}
    hier_state = {int(k[5:]): v for k, v in own.items() if k.startswith("hier:")}

    def from_hierarchy(parent: int) -> str | None:
        seen: set[int] = set()
        while parent and parent not in seen:
            if parent in hier_state:
                return hier_state[parent]
            seen.add(parent)
            parent = parents.get(parent, 0)
        return None

    out: list[dict[int, str]] = []
    for section, kind in (("BLOCK_DATA", "block"), ("EVENT_DATA", "event")):
        found: dict[int, str] = {}
        for row in rbd.sections[section].rows:
            oid = _id(row)
            if not oid:
                continue
            state = own.get(key(kind, oid)) or from_hierarchy(_parent(row, "ParentId"))
            if state:
                found[oid] = state
        out.append(found)
    return out[0], out[1]


def apply(rbd: Any) -> Any:
    """`rbd` itself when nothing is out of service, else a copy with the
    out-of-service elements turned into plain data (see module docstring)."""
    blocks, events = effective(rbd)
    if not blocks and not events:
        return rbd
    out = copy.deepcopy(rbd)
    for row in out.sections["BLOCK_DATA"].rows:
        state = blocks.get(_id(row))
        if state:
            row.update(_LINKED_BLOCK if state == "linked" else _CUT_BLOCK)
    for row in out.sections["EVENT_DATA"].rows:
        state = events.get(_id(row))
        if state:
            row.update({"failVal": "1" if state == "linked" else "0", "Phasing": "F"})
    return out


def describe(rbd: Any) -> list[dict[str, str]]:
    """[{kind, id, name, state}] for the elements the user put out of
    service (not the ones inside a hierarchy), for the CLI, MCP and GUI."""
    names = {"block": ("BLOCK_DATA", "blockName"), "event": ("EVENT_DATA", "eventName"),
             "hier": ("HIERARCHY_DATA", "hierarchyName")}
    out = []
    for k, state in sorted(states(rbd).items()):
        kind, _, oid = k.partition(":")
        section, column = names[kind]
        row = next((r for r in rbd.sections[section].rows if _id(r) == int(oid)), None)
        out.append({"kind": kind, "id": oid, "state": state,
                    "name": str(row.get(column) or oid) if row else f"(missing {kind} {oid})"})
    return out


def note(rbd: Any) -> str:
    """One line for a result made with elements out of service ("" when
    none are), so it is not taken for the model as built."""
    outs = describe(rbd)
    if not outs:
        return ""
    listed = ", ".join(f"{o['name']} ({o['state']})" for o in outs[:6])
    more = f" and {len(outs) - 6} more" if len(outs) > 6 else ""
    return (f"Out of service: {listed}{more}. These results are for that what-if, "
            "not the model as built; put them back in service to compare.")

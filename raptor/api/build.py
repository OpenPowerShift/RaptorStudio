"""Model building, editing, and structural operations.

Provides build_from_spec, add_block, add_node, add_event, connect,
update_element, delete_element, configure_sparing, configure_maintenance,
configure_phases, configure_system, describe, and templates.

Everything goes through ``core/fileio/builder.py`` or ``rbdlib`` row
constructors -- no hand-written ``.rbd`` rows.

Friendly field names come from ``core/fileio/rbds.py``'s own maps
(``_BLOCK_MAP``/``_NODE_MAP``/``_EVENT_MAP``/``_SYSTEM_MAP``/...); the
supplementary maps in this module extend them with fields the JSON
writer does not need to know about (sparing, PM, dependency, standby
stress, costs, growth/decay, capacity flows).  Every numeric field also
accepts an expression string (``"0.00015 * 20000 / 8760  # basis"`` or
``"@Highway * 1.4"``); the resolved float is stored in the row and the
provenance recorded in ``RbdFile.formulas``.
"""
from __future__ import annotations

import math
import re
from typing import Any

from raptor.api.limits import (
    MAX_ASSIGNMENT_CELLS,
    MAX_PHASES,
    MAX_RATES,
    MAX_SPEC_ELEMENTS,
)
from raptor.api.workspace import Workspace, _BLOCK_DEFAULT_FIELDS
from raptor.core import run_experiment
from raptor.core.fileio import rbds as _rbds
from raptor.core.fileio.rbd import RbdFile, RbdSection, store_distribution_values
from raptor.core.fileio.builder import (
    get_row, set_fields, add_row, next_id, used_ids, validate, sync_counts,
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class BuildError(Exception):
    """Base error for build operations."""


class ElementNotFound(BuildError):
    """Referenced element does not exist."""


class AmbiguousName(BuildError):
    """Name matches multiple elements."""


# ---------------------------------------------------------------------------
# Friendly field maps (rbds.py's documented surface plus this module's extras)
# ---------------------------------------------------------------------------

#: Node ``type`` may arrive as a name or a number.
_NODE_TYPE_MAP = {
    "start": 1, "regular": 2, "end": 3, "in": 4, "out": 5,
    "startnode": 1, "node": 2, "endnode": 3, "innode": 4, "outnode": 5,
}

#: Columns that hold free text, never an expression (a numeric-looking
#: string in one of these is still stored verbatim).
_STRING_COLUMNS = {
    "blockName", "ID", "ParentId", "FStream", "RStream",
    "SparePoolName", "SpType", "Resource1Name", "pmTrig", "DepType",
    "Name", "parent", "depType",
    "eventName", "failStream",
    "hierarchyName", "Parent",
    "FromType", "ToType", "pID",
    "imageString", "TerminationType", "units", "graphicsUse",
    "nodeAnalType", "SysStreamsArray",
    "PhaseName", "TriggerName", "PoolName", "Comment",
}

_BLOCK_EXTRA: dict[str, Any] = {
    # constructor-style aliases
    "fdist": "FDist", "fparams": ("FParam1", "FParam2", "FParam3"),
    "fstream": "FStream",
    "rdist": "RDist", "rparams": ("RParam1", "RParam2", "RParam3"),
    "rstream": "RStream",
    # general
    "sim_start": "SimStart", "simstart": "SimStart",
    "amt_exhausted": "AmtExhausted", "amount_exhausted": "AmtExhausted",
    "standby_stress": "SBStress", "sb_stress": "SBStress",
    "growth_decay_type": "GDType", "gd_type": "GDType",
    "growth_decay_rate": "GDRate", "gd_rate": "GDRate",
    "growth_decay_limit": "GDLimit", "gd_limit": "GDLimit",
    # sparing
    "spare_pool": "SparePoolName", "spare_pool_name": "SparePoolName",
    "sp_type": "SpType", "sparing_type": "SpType", "spare_type": "SpType",
    "rso": "RSO", "stock": "Stock",
    "new_spares": "NewSp", "new_sp": "NewSp", "newsp": "NewSp",
    "arrive": "ArriveEvery", "arrive_every": "ArriveEvery",
    "restock_every": "ArriveEvery",
    "eso": "ESO", "emertime": "EmerTime", "emer_time": "EmerTime",
    "emergency": "EmerTime",
    "slo": "SLO", "level": "Level", "quan": "Quan", "quantity": "Quan",
    "slotime": "SLOTime", "slo_time": "SLOTime",
    # resources
    "num_res": "NumRes", "num_resources": "NumRes",
    "res1_rep": "#Res1Rep", "res1_pm_rep": "#Res1pmRep",
    "resource1_name": "Resource1Name", "resource_name": "Resource1Name",
    # preventive maintenance
    "uses_pm": "usesPM", "usespm": "usesPM",
    "pm_spare": "PMSpare", "pm_refresh": "PMRefresh",
    "pm_mis_defer": "PMMisDefer", "pm_fail_reset": "PMFailReset",
    "pm_req_defer": "PMReqDefer", "pm_stagger_time": "PMStaggerTime",
    "pm_freq": "PMFreq", "pmfreq": "PMFreq",
    "pm_triggered": "pmTriggered", "pm_trigger": "pmTrig",
    "pm_trig": "pmTrig",
    "pm_dist": "Dist", "pm_duration_dist": "Dist",
    "pm_params": ("Param1#3", "Param2#3", "Param3#3"),
    # pre-LDT
    "pre_ldt_dist": "Pre-LDTDist", "pre_dist": "Pre-LDTDist",
    "pre_ldt_params": ("Param1#1", "Param2#1", "Param3#1"),
    "pre_params": ("Param1#1", "Param2#1", "Param3#1"),
    # post-LDT
    "post_ldt_dist": "Post-LDTDist", "post_dist": "Post-LDTDist",
    "post_ldt_params": ("Param1#2", "Param2#2", "Param3#2"),
    "post_params": ("Param1#2", "Param2#2", "Param3#2"),
    # dependency
    "dep_type": "DepType", "dependency_type": "DepType",
    "dep_num": "DepNum", "dependency_num": "DepNum",
    "dep_nothing": "DepNothing", "dep_idle": "DepIdle",
    "dep_pm": "DepPM", "dep_fail": "DepFail",
    "dep_pm_thresh": "DepPMThresh",
    "def_dep_state_idle": "defDepStateIdle",
    # costs
    "initial": "initial", "initial_cost": "initial",
    "running_cost": "Running", "running": "Running",
    "standby_cost": "standby", "standby": "standby",
    "idle_cost": "idle", "idle": "idle",
    "rep_hold": "repHold", "rep_hold_cost": "repHold",
    "rep_per_time": "repPerTime", "rep_per_time_cost": "repPerTime",
    "rep_fixed": "repFixed", "rep_fixed_cost": "repFixed",
    "done_per_time": "donePerTime", "done_fixed": "doneFixed",
    "pm_hold_cost": "pmHoldCost", "pm_cost": "pmCost",
    "pm_fixed_cost": "pmFixedCost",
    "per_spare": "perSpare", "per_spare_cost": "perSpare",
    "emer_shipping": "emerShipping", "emer_ship_cost": "emerShipping",
    "add_done": "addDone",
}

_NODE_EXTRA: dict[str, Any] = {
    "ntype": "Type",
    "n": "n", "out": "Out", "cold": "Cold", "pr": "PR", "autos": "Autos",
    "k_star": "k*", "kstar": "k*", "phases": "Phases",
    "dep_num": "depNum", "dep_type": "depType",
    "div_flow": "divFlow", "full_flow": "fullflow",
    "rep_cap": "repCap", "rep_na": "repNA",
}

_EVENT_EXTRA: dict[str, Any] = {
    "distro": "Distro", "dist": "Distro",
    "fail_stream": "failStream",
    "init_cost": "InitCost", "op_cost": "OpCost", "rep_cost": "RepCost",
    "phasing": "Phasing",
}

_HIER_EXTRA: dict[str, Any] = {
    "in_id": "in", "out_id": "out", "level": "level", "phs": "Phs",
}

_LINK_EXTRA: dict[str, Any] = {
    "from_type": "FromType", "to_type": "ToType",
    "cold_pri": "ColdPri", "coldpri": "ColdPri",
    "a_prob": "AProb", "aprob": "AProb", "probability": "AProb",
    "auto_time": "AutoTime", "auto": "AutoTime",
    "man_time": "ManTime", "man": "ManTime",
    "cap_pri": "CapPri", "cappri": "CapPri",
    "nom_flow": "nomFlow", "nomflow": "nomFlow",
    "max_flow": "MaxFlow", "maxflow": "MaxFlow",
}

_SYSTEM_EXTRA: dict[str, Any] = {
    "run_length": "TermCondition", "term_condition": "TermCondition",
    "term": "TermCondition", "termination": "TerminationType",
    "term_type": "TerminationType",
    "weak_link": "weakAnalysis", "weak_analysis": "weakAnalysis",
    "cost": "costAnalysis", "cost_analysis": "costAnalysis",
    "capacity": "capacityAnalysis", "capacity_analysis": "capacityAnalysis",
    "system_streams": "SysStreamsArray", "sys_streams": "SysStreamsArray",
    "red_cost": "systemRedCost", "lost_cost": "sysLostCost",
    "gy_threshold": "GYthreshold", "yr_threshold": "YRthreshold",
    "start_stats": "StartStats", "num_runs": "NumRuns",
    # capacity demand (`flowGenerated`): the amount of flow injected at the
    # Start node.  `flow_generated` is the friendly name; `flowGenerated`
    # and `flowgenerated` are accepted aliases.
    "flow_generated": "flowGenerated", "flowgenerated": "flowGenerated",
    "flowGenerated": "flowGenerated",
}

_EXT_MAPS: dict[str, dict[str, Any]] = {
    "BLOCK_DATA": _BLOCK_EXTRA,
    "NODE_DATA": _NODE_EXTRA,
    "EVENT_DATA": _EVENT_EXTRA,
    "HIERARCHY_DATA": _HIER_EXTRA,
    "LINK_DATA": _LINK_EXTRA,
    "SYSTEM_DATA": _SYSTEM_EXTRA,
}

#: PM boolean flags keyed by the friendly names `defer_flags` uses.
_PM_FLAG_MAP = {
    "misdefer": "PMMisDefer", "mis_defer": "PMMisDefer",
    "pmmisdefer": "PMMisDefer",
    "reqdefer": "PMReqDefer", "req_defer": "PMReqDefer",
    "pmreqdefer": "PMReqDefer",
    "failreset": "PMFailReset", "fail_reset": "PMFailReset",
    "pmfailreset": "PMFailReset",
    "refresh": "PMRefresh", "pmrefresh": "PMRefresh",
    "spare": "PMSpare", "pmspare": "PMSpare",
}


def combined_map(section: str) -> dict[str, Any]:
    """rbds.py's documented friendly map for *section* plus this module's
    own extras."""
    merged: dict[str, Any]
    if section == "SYSTEM_DATA":
        merged = dict(_rbds._SYSTEM_MAP)
    elif section == "LINK_DATA":
        merged = dict(_rbds._LINK_MAP)
    else:
        merged = dict(_rbds._MAPS.get(section, {}))
    merged.update(_EXT_MAPS.get(section, {}))
    return merged


def settable_fields() -> dict[str, list[str]]:
    """Every friendly field name `update_element` / `raptor set` accepts, by element kind (Block, Node, Event, Hier,
    Link, System), straight from the maps that resolve them. A legacy column name and an indexed form such as
    `repair_params[0]` are accepted too. The MCP `patch` description and `raptor help set-paths` list these, so
    what is advertised cannot drift from what works."""
    out = {}
    for section, kind in (("BLOCK_DATA", "Block"), ("NODE_DATA", "Node"), ("EVENT_DATA", "Event"),
                          ("HIERARCHY_DATA", "Hier"), ("LINK_DATA", "Link"), ("SYSTEM_DATA", "System")):
        out[kind] = sorted(combined_map(section))
    return out


_INDEX_RE = re.compile(r"^([A-Za-z_]\w*)\[(\d+)\]$")

#: Column -> decimal places for the legacy writer's own formatting
#: conventions (costs are 2dp, standby stress is 3dp, dependency
#: percentages are 2dp).
_COL_FMT: dict[str, int] = {
    "initial": 2, "Running": 2, "standby": 2, "idle": 2,
    "repHold": 2, "repPerTime": 2, "repFixed": 2, "donePerTime": 2,
    "doneFixed": 2, "pmHoldCost": 2, "pmCost": 2, "pmFixedCost": 2,
    "perSpare": 2, "emerShipping": 2,
    "DepNothing": 2, "DepIdle": 2, "DepPM": 2, "DepFail": 2,
    "DepPMThresh": 2,
    "SBStress": 3,
}

_SYSTEM_FLOAT_COLS = {
    "TermCondition", "StartStats", "GYthreshold", "YRthreshold",
    "systemRedCost", "sysLostCost",
}


# ---------------------------------------------------------------------------
# Name → id resolution (case-insensitive)
# ---------------------------------------------------------------------------

_NAME_COLS: dict[str, tuple[str, ...]] = {
    "BLOCK_DATA": ("blockName",),
    "NODE_DATA": ("Name",),
    "EVENT_DATA": ("eventName",),
    "HIERARCHY_DATA": ("hierarchyName",),
    "SPARE_POOL_DATA": ("PoolName",),
    "RESOURCE_DATA": ("PoolName",),
    "TRIGGERS": ("TriggerName",),
    "PHASES": ("PhaseName",),
}

_SECTION_KIND = {
    "BLOCK_DATA": "block",
    "NODE_DATA": "node",
    "EVENT_DATA": "event",
    "HIERARCHY_DATA": "hier",
    "LINK_DATA": "link",
    "SYSTEM_DATA": "system",
    "SPARE_POOL_DATA": "pool",
    "RESOURCE_DATA": "resource",
    "TRIGGERS": "trigger",
    "PHASES": "phase",
}
#: Sections that are addressed by NAME only (they carry no numeric id in
#: the Raptor 7 tables; their ID column is always 0).
_NAMED_ONLY_SECTIONS = ("SPARE_POOL_DATA", "RESOURCE_DATA", "TRIGGERS",
                        "PHASES")
_SECTION_KIND_TITLE = {
    "BLOCK_DATA": "Block",
    "NODE_DATA": "Node",
    "EVENT_DATA": "Event",
    "HIERARCHY_DATA": "Hier",
    "LINK_DATA": "Link",
    "SPARE_POOL_DATA": "Pool",
    "RESOURCE_DATA": "Resource",
    "TRIGGERS": "Trigger",
    "PHASES": "Phase",
}


def _resolve_name(rbd: RbdFile, section: str, name: str) -> dict:
    """Case-insensitive name lookup.  Raises ElementNotFound or
    AmbiguousName."""
    rows = rbd.section(section).rows
    name_cols = _NAME_COLS.get(section, ("Name",))
    name_lower = name.strip().lower()
    matches = []
    for row in rows:
        for col in name_cols:
            val = row.get(col, "")
            if val.strip().lower() == name_lower:
                matches.append(row)
                break
    if len(matches) == 0:
        all_names: list[str] = []
        for row in rows:
            for col in name_cols:
                val = row.get(col, "")
                if val:
                    all_names.append(val)
        near = [n for n in all_names
                if name_lower in n.lower() or n.lower() in name_lower]
        if not near:
            import difflib
            near = difflib.get_close_matches(name, all_names, n=5, cutoff=0.5)
        hint = f"  Near matches: {near}" if near else ""
        raise ElementNotFound(
            f"No element named {name!r} in {section}.{hint}"
        )
    if len(matches) > 1:
        raise AmbiguousName(
            f"Name {name!r} matches {len(matches)} elements in {section}"
        )
    return matches[0]


def resolve_element(rbd: RbdFile,
                      selector: str | int) -> tuple[str, dict]:
    """Resolve a selector (a name, or ``"Block:N"`` / ``"Node:N"`` /
    ``"Event:N"`` / ``"Hier:N"`` / ``"Link:N"``, or a bare integer id) to
    ``(section_name, row)``.  Link rows carry no name, so a link must be
    selected by id.

    ``selector`` is ``str | int`` because the body has always accepted
    both -- the first branch is an ``isinstance(selector, int)`` test. The
    annotation used to claim ``str``, which forced callers holding a
    public ``str | int`` selector to wrap it in ``str()`` (see
    ``analyse.py`` and ``mcp/tools_model.py``) purely to satisfy the
    checker, rather than because ``str()`` was needed. Behaviour is
    unchanged; an int selector resolves to the same ``Block:N`` row it
    always did.
    """
    if isinstance(selector, int) or (
            isinstance(selector, str) and selector.strip().isdigit()):
        selector = f"Block:{int(selector)}"
    if ":" in str(selector):
        kind, rest = str(selector).split(":", 1)
        section_map = {
            "Block": "BLOCK_DATA",
            "Node": "NODE_DATA",
            "Event": "EVENT_DATA",
            "Hier": "HIERARCHY_DATA",
            "Link": "LINK_DATA",
            "Pool": "SPARE_POOL_DATA",
            "SparePool": "SPARE_POOL_DATA",
            "Resource": "RESOURCE_DATA",
            "Trigger": "TRIGGERS",
            "Phase": "PHASES",
        }
        section = section_map.get(kind) or section_map.get(kind.capitalize())
        if section is None:
            raise BuildError(f"Unknown element kind {kind!r}")
        if rest.strip().isdigit():
            try:
                row = get_row(rbd, section, id=int(rest))
            except (KeyError, ValueError):
                raise ElementNotFound(
                    f"No {kind} with ID {rest} in {section}") from None
            return section, row
        # Named auxiliary rows (`Pool:PumpPool`, `Resource:Crew`, ...).
        if section in _NAMED_ONLY_SECTIONS:
            return section, _resolve_name(rbd, section, rest)
        raise BuildError(f"{kind}:{rest} is not an id; use an integer id")
    for sec_name in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                     "HIERARCHY_DATA", *_NAMED_ONLY_SECTIONS):
        try:
            row = _resolve_name(rbd, sec_name, str(selector))
            return sec_name, row
        except AmbiguousName:
            raise
        except ElementNotFound:
            continue
    all_names: list[str] = []
    for sec_name in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                     "HIERARCHY_DATA", *_NAMED_ONLY_SECTIONS):
        for col in _NAME_COLS.get(sec_name, ("Name",)):
            for row in rbd.section(sec_name).rows:
                val = row.get(col, "")
                if val:
                    all_names.append(val)
    low = str(selector).strip().lower()
    near = [n for n in all_names if low in n.lower() or n.lower() in low]
    if not near:
        import difflib
        near = difflib.get_close_matches(str(selector), all_names,
                                         n=5, cutoff=0.5)
    hint = f"  Near matches: {near}" if near else ""
    raise ElementNotFound(f"No element named {selector!r}.{hint}")


# ---------------------------------------------------------------------------
# Friendly-field patching + expression provenance
# ---------------------------------------------------------------------------

def _column_targets(section: str, row: dict, field: str) -> list[str]:
    """Resolve a friendly field (possibly ``"repair_params[0]"``) to the
    legacy column name(s) it writes.  Raises BuildError for unknown
    fields."""
    m = _INDEX_RE.match(field)
    base = field
    idx: int | None = None
    if m:
        base, idx = m.group(1), int(m.group(2))
    if field in row:
        return [field]
    field_map = combined_map(section)
    if base in field_map:
        target = field_map[base]
        if isinstance(target, tuple):
            if idx is None:
                return list(target)
            if 0 <= idx < len(target):
                return [target[idx]]
            raise BuildError(
                f"{base!r} has {len(target)} values; index {idx} is out "
                f"of range")
        if idx is not None:
            raise BuildError(f"{base!r} is not an indexed field")
        return [target]
    known = ", ".join(sorted(set(field_map) | set(row)))
    raise BuildError(
        f"Unknown field {field!r} for {section}.  Known friendly fields: "
        f"{known}")


def _record_formula(rbd: RbdFile, kind: str, elem_id: Any, col: str,
                    text: str, value: float) -> None:
    """Record the expression/rate behind a stored float (see
    ``RbdFile.formulas``)."""
    from raptor.gui.ui.expr_eval import is_bare_rate_ref
    if rbd.formulas is None:
        rbd.formulas = {}
    key = f"{kind}:{elem_id}:{col}"
    bare = is_bare_rate_ref(text)
    if bare is not None:
        rbd.formulas[key] = {"rate": bare, "value": value}
    else:
        rbd.formulas[key] = {"expr": text, "value": value}


#: Columns that hold a distribution code, and the ones where 0 is allowed (a
#: delay's stored 0 is Fixed).
_DIST_CODE_COLS = frozenset({"FDist", "RDist", "Pre-LDTDist", "Post-LDTDist",
                             "Dist", "Distro"})
_DIST_ZERO_OK = frozenset({"Pre-LDTDist", "Post-LDTDist"})

#: Longest text a single cell may hold, and most Empirical data points.
MAX_TEXT_CHARS = 10_000
MAX_EMPIRICAL_POINTS = 1_000


def _check_scalar(col: str, value: Any) -> None:
    """The root type check for every field edit: a cell holds one number, one
    flag or one piece of text. A nested object, a list, a control character or
    a huge string is refused here, whichever front end sent it."""
    if value is None or isinstance(value, (bool, int, float)):
        if value is None:
            raise BuildError(f"{col} cannot be null")
        return
    if not isinstance(value, str):
        raise BuildError(
            f"{col} must be a number or text, not {type(value).__name__}")
    if len(value) > MAX_TEXT_CHARS:
        raise BuildError(
            f"{col} is too long ({len(value)} characters); the limit is "
            f"{MAX_TEXT_CHARS}")
    from raptor.core.input_rules import control_problem
    why = control_problem(value)
    if why:
        raise BuildError(f"{col} {why}")


def _require_dist_code(col: str, value: Any) -> None:
    """A distribution column holds a whole number the engine knows."""
    try:
        x = float(value)
        code = int(x)
    except (TypeError, ValueError, OverflowError):
        raise BuildError(f"{col} {value!r} is not a distribution code") from None
    if x != code or not (1 <= code <= 22 or (code == 0 and col in _DIST_ZERO_OK)):
        raise BuildError(
            f"{col} {value!r} is not a distribution the engine knows: use a "
            "whole number from 1 to 22 or a name (see list_distributions)")


def _set_field(rbd: RbdFile, section: str, row: dict, kind: str,
               col: str, value: Any) -> None:
    if col not in row:
        raise BuildError(
            f"{section} row has no column {col!r} -- known columns: "
            f"{sorted(row)}")
    _check_scalar(col, value)
    if isinstance(value, str) and _is_plain_number(value):
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, ValueError):
            finite = False
        if not finite:
            raise BuildError(f"{col} must be a finite number; got {value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, ValueError):
            finite = False
        if not finite:
            raise BuildError(
                f"{col} must be a finite number; got {value!r}")
    if col == "Type" and section == "NODE_DATA":
        if isinstance(value, str):
            key = value.strip().lower()
            if key in _NODE_TYPE_MAP:
                value = _NODE_TYPE_MAP[key]
            else:
                raise BuildError(f"unknown node type {value!r}")
        set_fields(row, **{col: value})
        return
    if isinstance(value, bool):
        set_fields(row, **{col: value})
        return
    if col in _SYSTEM_FLOAT_COLS and isinstance(value, (int, float)):
        set_fields(row, **{col: float(value)})
        return
    if col in ("FDist", "RDist", "Dist", "Distro") and isinstance(value, str) \
            and not _is_plain_number(value):
        from raptor.core.fileio.rbds import dist_code
        try:
            value = dist_code(value)
        except ValueError as exc:
            raise BuildError(str(exc)) from None
    if col in _DIST_CODE_COLS and not isinstance(value, bool):
        _require_dist_code(col, value)
    elif col in _DIST_CODE_COLS:
        raise BuildError(f"{col} must be a distribution code, not a flag")
    if col == "SpType" and isinstance(value, str):
        value = _SPARING_MODE_TO_FILE.get(value.strip().lower(), value)
    if isinstance(value, str) and col not in _STRING_COLUMNS \
            and not _is_plain_number(value):
        from raptor.api.rates import resolve_value
        resolved = resolve_value(value, rbd.rates)
        store_numeric(row, col, resolved)
        _record_formula(rbd, kind, row.get("ID", "0"), col, value, resolved)
        return
    if col in _COL_FMT and not isinstance(value, str):
        store_numeric(row, col, value)
        return
    if isinstance(value, int) and section != "SYSTEM_DATA":
        # (SYSTEM_DATA has its own explicit `_SYSTEM_FLOAT_COLS` list above.)
        # A JSON caller's whole number (`"fail_params": [40]`) arrives as an
        # int, and `set_fields` formats by Python type -- so a REAL column
        # got written `40` instead of the `40.000000` every real-authored
        # file uses. Keep the column's own decimal convention: if its
        # current value is a decimal, write the int the same way.
        cur = str(row.get(col, ""))
        if "." in cur and _is_plain_number(cur):
            row[col] = f"{float(value):.{len(cur.split('.', 1)[1])}f}"
            return
    set_fields(row, **{col: value})


def store_numeric(row: dict, col: str, value: Any) -> None:
    """Store a number, honouring the legacy writer's own decimal
    convention for the handful of columns that are not 6dp."""
    fmt = _COL_FMT.get(col)
    if fmt is not None:
        try:
            row[col] = f"{float(value):.{fmt}f}"
            return
        except (TypeError, ValueError):
            pass
    set_fields(row, **{col: value})


def _is_plain_number(text: str) -> bool:
    try:
        float(text.strip())
        return True
    except ValueError:
        return False


def _check_value_count(row: dict, patch: dict[str, Any], field: str,
                       targets: list[str], seq: list[Any]) -> None:
    """A list longer than the columns it fills used to be cut off silently.
    Only an Empirical distribution takes more than its three parameters, and
    then only up to ``MAX_EMPIRICAL_POINTS``."""
    if len(seq) <= len(targets):
        return
    which = targets[0][:1] if targets[0][1:6] == "Param" else ""
    if which in ("F", "R"):
        dist_keys = (("fail_dist", "fdist", "FDist") if which == "F"
                     else ("repair_dist", "rdist", "RDist"))
        dist = next((patch[k] for k in dist_keys if k in patch), row.get(dist_keys[-1]))
        try:
            empirical = int(float(str(dist))) == 16 or str(dist).strip().lower() == "empirical"
        except (TypeError, ValueError):
            empirical = str(dist).strip().lower() == "empirical"
        if empirical:
            if len(seq) > MAX_EMPIRICAL_POINTS:
                raise BuildError(
                    f"{field!r} has {len(seq)} data points; the limit is "
                    f"{MAX_EMPIRICAL_POINTS}")
            return
    raise BuildError(
        f"{field!r} takes at most {len(targets)} values; got {len(seq)}")


def _apply_patch(rbd: RbdFile, section: str, row: dict, kind: str,
                 patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a friendly/legacy patch to *row*, resolving expression
    strings and recording provenance.  Returns the changed columns."""
    changed: dict[str, Any] = {}
    for field, value in patch.items():
        targets = _column_targets(section, row, field)
        if len(targets) == 1:
            seq = [value]
        else:
            if isinstance(value, (list, tuple)):
                seq = list(value)
                _check_value_count(row, patch, field, targets, seq)
            else:
                seq = [value] * len(targets)
            if len(seq) < len(targets):
                seq = seq + [0] * (len(targets) - len(seq))
            seq = seq[:len(targets)]
        for col, item in zip(targets, seq):
            _set_field(rbd, section, row, kind, col, item)
            changed[col] = row.get(col)
    # Keep the PM "triggered" flag in step when only the trigger name was
    # patched (the MCP update_element path sets friendly fields one at a
    # time).
    if section == "BLOCK_DATA" and "pmTrig" in changed \
            and "pmTriggered" not in changed:
        trig = str(row.get("pmTrig", "")).strip().lower()
        set_fields(row, pmTriggered=0 if trig in ("", "none") else 1)
        changed["pmTriggered"] = row["pmTriggered"]
    return changed


def _require_dist_params_on_new_block(patch: dict[str, Any]) -> None:
    """A brand-new block row starts with `make_block_row`'s own defaults --
    Exponential `fdist` and `fparams=(100.0, 0.0, 0.0)`. Setting only
    `fail_dist`/`repair_dist` in the spec silently keeps those Exponential
    param VALUES under the new distribution's meaning; e.g. Gamma reads
    them as (shape=100, scale=0, location=0), and scale=0 is invalid --
    `validate()` catches it, but only after the model has already
    "built". A distribution and its params always arrive together on a
    new block, so require both here instead of letting a mismatched
    default through.  (Not enforced on `update_element`, where changing
    just the distribution code on an *existing* row -- e.g. Normal ->
    Lognormal, which share a mean/stdev meaning -- is a legitimate,
    narrower edit.)"""
    for dist_keys, param_keys in (
            (("fail_dist", "fdist"), ("fail_params", "fparams")),
            (("repair_dist", "rdist"), ("repair_params", "rparams"))):
        has_dist = any(k in patch for k in dist_keys)
        has_params = any(k in patch for k in param_keys)
        if has_dist and not has_params:
            dist_key = next(k for k in dist_keys if k in patch)
            raise BuildError(
                f"{dist_key}={patch[dist_key]!r} given with no "
                f"{param_keys[0]} -- a new block's distribution and its "
                f"parameters must be given together, or it silently "
                f"keeps a new block's Exponential-shaped default parameters under the "
                f"new distribution's meaning (e.g. a Gamma with "
                f"scale=0, which fails validation)"
            )


def _fix_dist_param_counts(row: dict, provided: dict[str, Any]) -> None:
    """Keep ``#FParam``/``#RParam`` in step with the distribution that is
    actually stored (a friendly patch may change ``FDist``/``FParam*``
    without going through ``rbdlib.make_block_row``)."""
    from raptor.core.fileio.rbdlib import DIST_NPARAMS, DIST_NPARAMS_DISK
    fp = provided.get("fail_params", provided.get("fparams"))
    rp = provided.get("repair_params", provided.get("rparams"))
    for dist_col, n_col, points in (("FDist", "#FParam", fp),
                                 ("RDist", "#RParam", rp)):
        if dist_col not in row or n_col not in row:
            continue
        try:
            dist = int(float(row.get(dist_col, "0")))
        except (TypeError, ValueError):
            continue
        if dist == 16:
            # Empirical: the points go on the continuation line, sorted, with
            # #params = their number (`_apply_patch` only filled the three
            # parameter columns, so the rest of a longer list was lost and an
            # exported .rbd claimed points it did not hold).
            if points is not None:
                store_distribution_values(row, dist_col[0], 16,
                                          [float(p) for p in points])
            continue
        row.pop(f"{dist_col[0]}Emp", None)
        row[n_col] = str(DIST_NPARAMS_DISK.get(
            dist, DIST_NPARAMS.get(dist, 3)))


# ---------------------------------------------------------------------------
# build_from_spec
# ---------------------------------------------------------------------------

def _coerce_id(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise BuildError(f"element id must be an integer, got {value!r}") from None


def _norm_kind(kind: Any) -> str:
    k = str(kind).strip().lower().replace("rbd", "")
    return {"block": "Block", "node": "Node", "event": "Event",
            "hier": "Hier", "hierarchy": "Hier"}.get(k, str(kind))


def _endpoint_to_id(rbd: RbdFile, value: Any, maps: dict[str, dict],
                    default: str = "Node") -> tuple[str, int]:
    """Resolve a link endpoint -- ``["Block", 3]``, ``"Block:3"``, a bare
    name, or an integer -- to ``(kind, id)``."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        kind = _norm_kind(value[0])
        return kind, _ref_to_id(value[1], maps.get(kind.lower(), {}))
    if isinstance(value, bool):
        raise BuildError(f"cannot resolve link endpoint {value!r}")
    if isinstance(value, int):
        return default, value
    s = str(value).strip()
    if ":" in s:
        section, row = resolve_element(rbd, s)
        return _SECTION_KIND_TITLE.get(section, default), int(row["ID"])
    low = s.lower()
    for kind in ("Block", "Node", "Event", "Hier"):
        m = maps.get(kind.lower(), {})
        if low in m:
            return kind, m[low]
    try:
        return default, int(s)
    except ValueError:
        raise BuildError(f"cannot resolve link endpoint {value!r}") from None


def _ref_to_id(ref: Any, name_map: dict) -> int:
    if isinstance(ref, bool):
        raise BuildError(f"invalid element reference {ref!r}")
    if isinstance(ref, int):
        return ref
    s = str(ref).strip()
    if s.lower() in name_map:
        return name_map[s.lower()]
    try:
        return int(float(s))
    except ValueError:
        raise BuildError(f"cannot resolve element reference {ref!r}") from None


def _apply_system_patch(rbd: RbdFile, row: dict,
                        spec: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in spec.items()
             if k not in ("display_unit", "system_streams", "sys_streams")}
    for key in list(patch):
        if key in _SYSTEM_FLOAT_COLS and isinstance(patch[key], (int, float)) \
                and not isinstance(patch[key], bool):
            patch[key] = float(patch[key])
    changed = _apply_patch(rbd, "SYSTEM_DATA", row, "system", patch) \
        if patch else {}
    streams = spec.get("system_streams", spec.get("sys_streams"))
    if streams is not None:
        if isinstance(streams, (list, tuple)):
            row["SysStreamsArray"] = " ".join(str(int(x)) for x in streams)
        else:
            row["SysStreamsArray"] = str(streams)
    return changed


#: Friendly spec key -> the rbdlib row constructor for the four "auxiliary"
#: sections the builder used to create empty and silently drop.
def _add_aux_sections(rbd: RbdFile, spec: dict[str, Any]) -> None:
    """Populate SPARE_POOL_DATA / RESOURCE_DATA / TRIGGERS / PHASES from
    the spec, and the .rbds ``annotations`` overlay.

    ``build_model`` used to accept these keys, report ``runnable: true``,
    save empty sections and then crash at ``simulate`` with an opaque
    internal error.  ``spare_pools``/``resources``/``triggers``/``phases``
    are built with the same ``rbdlib`` row constructors the GUI and
    ``configure_*`` use, never hand-written rows.
    """
    from raptor.core.fileio.rbdlib import make_spare_pool_row, make_resource_row, make_phase_row, make_trigger_row

    def _section(name: str) -> RbdSection:
        return rbd.sections.setdefault(
            name, RbdSection(name=name, columns=[], rows=[]))

    def _set_cols(sec: RbdSection) -> None:
        cols: list[str] = []
        for row in sec.rows:
            for key in row:
                if key not in cols:
                    cols.append(key)
        sec.columns = cols

    pools = spec.get("spare_pools")
    if pools is not None:
        sec = _section("SPARE_POOL_DATA")
        sec.rows.clear()
        for p in pools:
            sec.rows.append(make_spare_pool_row(
                name=p.get("name", p.get("pool_name", f"Pool{len(sec.rows) + 1}")),
                rso=int(p.get("rso", 0)),
                stock=int(p.get("stock", 0)),
                newsp=int(p.get("new_spares", p.get("newsp", 1))),
                arrive=float(p.get("arrive_every", p.get("arrive", 720.0))),
                eso=int(p.get("eso", 0)),
                emertime=float(p.get("emer_time", p.get("emertime", 24.0))),
                slo=int(p.get("slo", 0)),
                level=int(p.get("level", 0)),
                quan=int(p.get("quantity", p.get("quan", 1))),
                slotime=float(p.get("slo_time", p.get("slotime", 24.0))),
            ))
        _set_cols(sec)

    resources = spec.get("resources")
    if resources is not None:
        sec = _section("RESOURCE_DATA")
        sec.rows.clear()
        for r in resources:
            sec.rows.append(make_resource_row(
                name=r.get("name", r.get("pool_name",
                                         f"Resource{len(sec.rows) + 1}")),
                numin=int(r.get("num_in", r.get("numin", 1))),
                sparecost=float(r.get("spare_cost", r.get("sparecost", 1.0))),
                fixed=float(r.get("fixed_per_use", r.get("fixed", 1.0))),
                perunit=float(r.get("per_time_unit", r.get("perunit", 1.0))),
                phases=int(r.get("phases", 0)),
            ))
        _set_cols(sec)

    triggers = spec.get("triggers")
    if triggers is not None:
        sec = _section("TRIGGERS")
        sec.rows.clear()
        for t in triggers:
            sec.rows.append(make_trigger_row(
                name=t.get("name", t.get("trigger_name",
                                         f"Trigger{len(sec.rows) + 1}")),
                initused=float(t.get("init_used", t.get("initused", 0.0))),
                dist=int(t.get("dist", t.get("duration_dist", 8))),
                params=tuple(t.get("params", (288.0, 1.0, 0.0))),
                repeats=int(t.get("repeats", 1)),
            ))
        _set_cols(sec)

    phases = spec.get("phases")
    if phases is not None:
        sec = _section("PHASES")
        sec.rows.clear()
        for i, p in enumerate(phases, 1):
            sec.rows.append(make_phase_row(
                name=p.get("name", f"Phase{i}"),
                num=i,
                mission=bool(int(p.get("mission", 1))),
                dist=int(p.get("duration_dist", p.get("dist", 8))),
                params=tuple(p.get("params", (5.0, 0.1, 0.0))),
            ))
        _set_cols(sec)

def _add_spec_comments(rbd: RbdFile, spec: dict[str, Any]) -> None:
    """A spec's ``comments``: ``[{"element": "<block/node/event/hier name>" | "System",
    "comment": text}]`` become COMMENTS rows (the file's `Type ID Name Comment`). A block commented
    `maintDefer` is what the defer trigger acts on; the system comment `The defer trigger is <name>`
    names it."""
    from raptor.core.fileio.rbdlib import COMMENT_COLS
    entries = spec.get("comments")
    if not entries:
        return
    section = rbd.sections.setdefault(
        "COMMENTS", RbdSection(name="COMMENTS", columns=list(COMMENT_COLS), rows=[]))
    if not section.columns:
        section.columns = list(COMMENT_COLS)
    for entry in entries:
        who = entry.get("element", "System")
        text = entry.get("comment", "")
        if str(who).lower() == "system":
            section.rows.append({"Type": "System", "ID": "0", "Name": "model.rbd", "Comment": text})
            continue
        kind, row = resolve_element(rbd, who)
        type_name = {"BLOCK_DATA": "block", "NODE_DATA": "node", "EVENT_DATA": "event",
                     "HIERARCHY_DATA": "hier"}[kind]
        name = row.get("blockName") or row.get("Name") or row.get("eventName") \
            or row.get("hierarchyName", "")
        section.rows.append({"Type": type_name, "ID": str(row["ID"]), "Name": name, "Comment": text})


def _add_spec_phase_states(rbd: RbdFile, spec: dict[str, Any]) -> None:
    """Per-element phase states from a spec's ``phase_states``: ``{element
    name or "Block:N": [one state per phase]}``.  Needs ``phases``; each
    value goes through ``_apply_phase_assignment`` (the same validation and
    ``P NNN`` / ``P00N`` column handling as ``configure_phases``)."""
    states = spec.get("phase_states")
    if not states:
        return
    n_phases = len(rbd.section("PHASES").rows)
    if not n_phases:
        raise BuildError("phase_states needs a non-empty 'phases' list")
    for selector, values in states.items():
        section, row = resolve_element(rbd, selector)
        _apply_phase_assignment(rbd, section, row, values, n_phases)


def _add_spec_annotations(rbd: RbdFile, spec: dict[str, Any]) -> None:
    """Place spec ``annotations`` *after* auto-layout, so name-anchored
    entries (``adjacent_to``/``from_``/``to``) resolve to real positions."""
    annotations = spec.get("annotations")
    if not annotations:
        return
    from raptor.api.annotations import place_annotation
    rbd.annotations = []
    for a in annotations:
        fields = dict(a)
        fields.pop("id", None)
        kind = fields.pop("kind", "text")
        try:
            place_annotation(rbd, kind=kind, **fields)
        except TypeError as exc:
            raise BuildError(
                f"annotation {a!r} has an unknown field: {exc}") from None


#: Top-level keys `build_from_spec` understands.  Anything else is a
#: typo: silently ignoring it built a valid-looking model from fewer
#: elements than the caller declared.
_SPEC_KEYS = frozenset({
    "rbds_text", "name", "blocks", "nodes", "events", "hierarchies",
    "links", "system", "experiment", "rates", "spare_pools", "resources",
    "triggers", "phases", "phase_states", "annotations", "comments",
    "display_unit",
})

#: Keys accepted in each auxiliary section.  Kept inclusive of the legacy
#: aliases the builder itself reads, so this only rejects real typos.
_AUX_SPEC_KEYS: dict[str, frozenset[str]] = {
    "spare_pools": frozenset({
        "name", "pool_name", "rso", "stock", "new_spares", "newsp",
        "arrive_every", "arrive", "eso", "emer_time", "emertime", "slo",
        "level", "quantity", "quan", "slo_time", "slotime",
    }),
    "resources": frozenset({
        "name", "pool_name", "num_in", "numin", "spare_cost", "sparecost",
        "fixed_per_use", "fixed", "per_time_unit", "perunit", "phases",
    }),
    "triggers": frozenset({
        "name", "trigger_name", "init_used", "initused", "dist",
        "duration_dist", "params", "repeats",
    }),
    "phases": frozenset({
        "name", "mission", "duration_dist", "dist", "params",
    }),
    "rates": frozenset({
        "name", "expr", "value", "description", "comment",
    }),
    "comments": frozenset({
        "element", "comment", "type", "id", "name", "text",
    }),
}


def _reject_non_finite(value: Any, path: str = "spec") -> None:
    """Reject NaN/Infinity anywhere in a JSON-like spec."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, ValueError):
            finite = False
        if not finite:
            raise BuildError(f"{path} must be a finite number; got {value!r}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _reject_non_finite(item, f"{path}[{i}]")


def _validate_spec_keys(spec: dict[str, Any]) -> None:
    _reject_non_finite(spec)
    unknown = set(spec) - _SPEC_KEYS
    if unknown:
        raise BuildError(
            "unknown spec key(s): " + ", ".join(sorted(unknown))
            + ". Known keys: " + ", ".join(sorted(_SPEC_KEYS)))
    for key, allowed in _AUX_SPEC_KEYS.items():
        entries = spec.get(key)
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise BuildError(f"spec[{key!r}] must be a list")
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise BuildError(f"spec[{key!r}][{i}] must be an object")
            extra = set(entry) - allowed
            if extra:
                raise BuildError(
                    f"spec[{key!r}][{i}] has unknown field(s): "
                    + ", ".join(sorted(extra))
                    + ". Known: " + ", ".join(sorted(allowed)))


def build_from_spec(spec: dict[str, Any],
                    auto_layout: bool = True) -> RbdFile:
    """Build an ``RbdFile`` from a friendly specification dict.

    The spec may contain:
    - ``rbds_text``: raw .rbds text to parse (everything else ignored)
    - ``blocks``, ``nodes``, ``events``, ``links``: lists of dicts
    - ``hierarchies``, ``spare_pools``, ``resources``, ``triggers``,
      ``phases``: lists of dicts (built via ``rbdlib`` row constructors)
    - ``phase_states``: ``{element name or "Block:N": [one state per phase]}``
      for a phased model built from scratch (needs ``phases``); see
      ``_apply_phase_assignment`` for the cell formats
    - ``annotations``: drawing overlays (see ``raptor.api.annotations``)
    - ``system``: system config dict
    - ``rates``: rate definitions (``{name, expr, description}``)

    Friendly names (``fail_params``, ``pm_freq``, ``stock``, …) are
    accepted; ids are assigned where omitted; links are wired by name;
    counts are synced and the result is passed through
    ``builder.validate()`` (raising ``BuildError`` on structural ERRORs).

    ``auto_layout`` (default True) re-places every element on the shared
    layered grid (Start left → End right) via
    :func:`raptor.core.fileio.legacy_layout.normalize_legacy_layout`.
    Without it a spec-built model has every element at (0, 0), so its
    diagram opens as one overlapping blob — the view scale is derived
    from the saved spacing, so no later Auto Layout can rescue it.
    """
    if not isinstance(spec, dict):
        raise BuildError("spec must be a dict")
    _validate_spec_keys(spec)
    section_lists = ("blocks", "nodes", "events", "hierarchies", "links",
                     "spare_pools", "resources", "triggers", "phases",
                     "annotations", "comments")
    total = 0
    for key in section_lists:
        value = spec.get(key)
        if value is not None:
            if not isinstance(value, list):
                raise BuildError(f"spec[{key!r}] must be a list")
            total += len(value)
    if total > MAX_SPEC_ELEMENTS:
        raise BuildError(
            f"spec has {total} elements; the limit is {MAX_SPEC_ELEMENTS}")
    if len(spec.get("phases") or []) > MAX_PHASES:
        raise BuildError(
            f"spec has {len(spec.get('phases') or [])} phases; "
            f"the limit is {MAX_PHASES}")
    if len(spec.get("rates") or []) > MAX_RATES:
        raise BuildError(
            f"spec has {len(spec.get('rates') or [])} rates; "
            f"the limit is {MAX_RATES}")
    states = spec.get("phase_states") or {}
    if not isinstance(states, dict):
        raise BuildError("spec['phase_states'] must be an object")
    if sum(len(v or []) for v in states.values()) > MAX_ASSIGNMENT_CELLS:
        raise BuildError(
            "spec['phase_states'] has too many cells; the limit is "
            f"{MAX_ASSIGNMENT_CELLS}")
    if "rbds_text" in spec:
        from raptor.core.fileio.rbds import parse_rbds
        return parse_rbds(spec["rbds_text"])

    from raptor.core.fileio.rbdlib import (
        make_system_row, make_block_row, make_node_row, make_event_row, make_hierarchy_row, make_link_row,
    )

    rbd = RbdFile(
        version="rbds",
        app_name="RAPTOR",
        file_name=spec.get("name", "Untitled"),
        timestamp="",
        counts={},
        sections={},
    )

    # --- system -----------------------------------------------------------
    sys_spec = spec.get("system") or {}
    source_row = make_system_row()
    _apply_system_patch(rbd, source_row, sys_spec)
    exp_spec = spec.get("experiment")
    if exp_spec is not None:
        if not isinstance(exp_spec, dict):
            raise BuildError("spec['experiment'] must be an object")
        experiment = run_experiment.normalise(exp_spec)
        for column, value in run_experiment.to_system_row(experiment):
            source_row[column] = value
        rbd.experiment = experiment
    else:
        rbd.experiment = run_experiment.normalise(
            run_experiment.from_system_row(source_row))
    rbd.sections["SYSTEM_DATA"] = RbdSection(
        name="SYSTEM_DATA", columns=list(source_row.keys()), rows=[source_row])
    if sys_spec.get("display_unit"):
        rbd.display_unit = sys_spec["display_unit"]

    # --- rates ------------------------------------------------------------
    if spec.get("rates"):
        from raptor.api.rates import make_rate_entry
        rbd.rates = []
        for r in spec["rates"]:
            expr = r.get("expr", r.get("value"))
            desc = r.get("description", r.get("comment", ""))
            rbd.rates.append(
                make_rate_entry(r.get("name"), expr, desc, rbd.rates))

    # --- elements ---------------------------------------------------------
    # Every section must exist before next_id() looks at it.
    for sec_name in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                     "HIERARCHY_DATA", "LINK_DATA", "SPARE_POOL_DATA",
                     "RESOURCE_DATA", "TRIGGERS", "PHASES"):
        rbd.sections.setdefault(
            sec_name, RbdSection(name=sec_name, columns=[], rows=[]))

    blocks_sec = rbd.sections["BLOCK_DATA"]
    # One ID set for the whole build: `next_id` otherwise re-scans every
    # section for each of thousands of rows (quadratic; a 5000-block spec
    # spent 46 s in it alone). Same for the duplicate-name scan below.
    used = used_ids(rbd)

    def _fresh_id(section: str) -> int:
        new = next_id(rbd, section, used=used)
        used.add(new)
        return new

    block_seen: dict[str, str] = {}
    block_map: dict[str, int] = {}
    for b in spec.get("blocks") or []:
        bid = _coerce_id(b.get("id", b.get("ID")))
        if bid is None:
            bid = _fresh_id("BLOCK_DATA")
        used.add(bid)
        name = b.get("name", b.get("blockName", f"Block{bid}"))
        _reject_duplicate_name(rbd, "BLOCK_DATA", name, seen=block_seen)
        row = make_block_row(bid, name=name)
        patch = {k: v for k, v in b.items()
                 if k not in ("id", "ID", "name", "blockName")}
        _require_dist_params_on_new_block(patch)
        _apply_patch(rbd, "BLOCK_DATA", row, "block", patch)
        _fix_dist_param_counts(row, patch)
        blocks_sec.rows.append(row)
        block_seen[name] = str(bid)
        block_map[str(name).lower()] = bid
        block_map[str(bid)] = bid
    blocks_sec.rows.insert(0, make_block_row(0))

    nodes_sec = rbd.sections["NODE_DATA"]
    node_seen: dict[str, str] = {}
    node_map: dict[str, int] = {}
    for n in spec.get("nodes") or []:
        nid = _coerce_id(n.get("id", n.get("ID")))
        if nid is None:
            nid = _fresh_id("NODE_DATA")
        used.add(nid)
        name = n.get("name", n.get("Name", f"Node{nid}"))
        _reject_duplicate_name(rbd, "NODE_DATA", name, seen=node_seen)
        row = make_node_row(nid, name, 0, 0)
        patch = {k: v for k, v in n.items()
                 if k not in ("id", "ID", "name", "Name")}
        _apply_patch(rbd, "NODE_DATA", row, "node", patch)
        nodes_sec.rows.append(row)
        node_seen[name] = str(nid)
        node_map[str(name).lower()] = nid
        node_map[str(nid)] = nid

    events_sec = rbd.sections["EVENT_DATA"]
    event_seen: dict[str, str] = {}
    event_map: dict[str, int] = {}
    for e in spec.get("events") or []:
        eid = _coerce_id(e.get("id", e.get("ID")))
        if eid is None:
            eid = _fresh_id("EVENT_DATA")
        used.add(eid)
        name = e.get("name", e.get("eventName", f"Event{eid}"))
        _reject_duplicate_name(rbd, "EVENT_DATA", name, seen=event_seen)
        row = make_event_row(eid, name, 0, 0)
        patch = {k: v for k, v in e.items()
                 if k not in ("id", "ID", "name", "eventName")}
        _apply_patch(rbd, "EVENT_DATA", row, "event", patch)
        events_sec.rows.append(row)
        event_seen[name] = str(eid)
        event_map[str(name).lower()] = eid
        event_map[str(eid)] = eid

    hiers_sec = rbd.sections["HIERARCHY_DATA"]
    hier_seen: dict[str, str] = {}
    for h in spec.get("hierarchies") or []:
        hid = _coerce_id(h.get("id", h.get("ID")))
        if hid is None:
            hid = _fresh_id("HIERARCHY_DATA")
        used.add(hid)
        name = h.get("name", h.get("hierarchyName", f"Hier{hid}"))
        _reject_duplicate_name(rbd, "HIERARCHY_DATA", name, seen=hier_seen)
        row = make_hierarchy_row(hid, name, 0, 0)
        patch = {k: v for k, v in h.items()
                 if k not in ("id", "ID", "name", "hierarchyName")}
        _apply_patch(rbd, "HIERARCHY_DATA", row, "hier", patch)
        hiers_sec.rows.append(row)
        hier_seen[name] = str(hid)

    # --- links ------------------------------------------------------------
    maps = {"block": block_map, "node": node_map, "event": event_map}
    links_sec = rbd.sections["LINK_DATA"]
    for link in spec.get("links") or []:
        lid = _coerce_id(link.get("id", link.get("ID")))
        if lid is None:
            lid = _fresh_id("LINK_DATA")
        used.add(lid)
        from_raw = link.get("from", link.get("From"))
        to_raw = link.get("to", link.get("To"))
        if from_raw is None and ("from_id" in link or "FromID" in link):
            ftype = _norm_kind(link.get("from_type", link.get("FromType",
                                                           "Node")))
            fid = _ref_to_id(link.get("from_id", link.get("FromID")),
                             maps.get(ftype.lower(), {}))
        else:
            ftype, fid = _endpoint_to_id(rbd, from_raw, maps, default="Node")
        if to_raw is None and ("to_id" in link or "ToID" in link):
            to_kind = _norm_kind(link.get("to_type", link.get("ToType", "Node")))
            tid = _ref_to_id(link.get("to_id", link.get("ToID")),
                             maps.get(to_kind.lower(), {}))
        else:
            to_kind, tid = _endpoint_to_id(rbd, to_raw, maps, default="Node")
        # A link named straight at a hierarchy's In/Out marker node from
        # outside that hierarchy is redirected to address the hierarchy
        # itself -- see `_redirect_hierarchy_boundary`.
        ftype, fid, to_kind, tid = _redirect_hierarchy_boundary(
            rbd, ftype, fid, to_kind, tid)
        row = make_link_row(lid, ftype, fid, to_kind, tid)
        patch = {k: v for k, v in link.items()
                 if k not in ("id", "ID", "from", "to", "From", "To",
                              "from_type", "to_type", "from_id", "to_id",
                              "FromID", "ToID")}
        _apply_patch(rbd, "LINK_DATA", row, "link", patch)
        links_sec.rows.append(row)

    # Rows built through `rbdlib` carry their columns; sections created
    # empty above do not. Without this, get_table/export_tables saw
    # header-less Nodes/Blocks tables for a freshly built model.
    for sec in rbd.sections.values():
        if not sec.columns and sec.rows:
            cols: list[str] = []
            for row in sec.rows:
                for key in row:
                    if key not in cols:
                        cols.append(key)
            sec.columns = cols
    _fix_link_counts(rbd)
    _add_aux_sections(rbd, spec)
    _add_spec_comments(rbd, spec)
    _add_spec_phase_states(rbd, spec)
    sync_counts(rbd)

    errors = [i for i in validate(rbd) if i.startswith("ERROR")]
    if errors:
        raise BuildError(
            "built model failed validation:\n" + "\n".join(errors))
    if auto_layout:
        from raptor.core.fileio.legacy_layout import normalize_legacy_layout
        normalize_legacy_layout(rbd, force=True)
    _add_spec_annotations(rbd, spec)
    return rbd


def _fix_link_counts(rbd: RbdFile) -> None:
    """Recompute In/Out counts from actual links."""
    blocks = rbd.section("BLOCK_DATA").rows
    nodes = rbd.section("NODE_DATA").rows
    events = rbd.section("EVENT_DATA").rows
    links = rbd.section("LINK_DATA").rows
    from raptor.core.fileio.rbdlib import fix_link_counts
    fix_link_counts(blocks, nodes, events, links)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _default_block(name: str) -> dict[str, Any]:
    return {"name": name,
            "fdist": 8, "fparams": (1000.0, 100.0, 0.0),
            "rdist": 8, "rparams": (10.0, 2.0, 0.0)}


def series(n: int) -> dict[str, Any]:
    """Build a spec for n blocks in series (Start→B1→…→Bn→End)."""
    blocks = [_default_block(f"Block{i+1}") for i in range(n)]
    nodes = [{"name": "Start", "type": 1, "n": 0, "out": 1}]
    for i in range(n):
        nodes.append({"name": f"Mid{i+1}", "type": 2, "n": 1, "out": 1})
    nodes.append({"name": "End", "type": 3, "n": 1, "out": 0})
    links = [{"from": "Start", "to": "Block1"}]
    for i in range(n):
        links.append({"from": f"Block{i+1}", "to": f"Mid{i+1}"})
        if i < n - 1:
            links.append({"from": f"Mid{i+1}", "to": f"Block{i+2}"})
    links.append({"from": f"Mid{n}", "to": "End"})
    return {"blocks": blocks, "nodes": nodes, "links": links}


def parallel(n: int) -> dict[str, Any]:
    """Build a spec for n blocks in parallel: any one path suffices."""
    blocks = [_default_block(f"Block{i+1}") for i in range(n)]
    nodes = [
        {"name": "Start", "type": 1, "n": 0, "out": 1},
        {"name": "Split", "type": 2, "n": 1, "out": n, "k": 1},
        {"name": "Join", "type": 2, "n": n, "out": 1, "k": 1},
        {"name": "End", "type": 3, "n": 1, "out": 0},
    ]
    # Start takes one link out (Raptor 7's CheckValidLink), so the paths
    # leave through a node, as Raptor 7's own sample models do.
    links = [{"from": "Start", "to": "Split"}]
    for i in range(n):
        links.append({"from": "Split", "to": f"Block{i+1}"})
        links.append({"from": f"Block{i+1}", "to": "Join"})
    links.append({"from": "Join", "to": "End"})
    return {"blocks": blocks, "nodes": nodes, "links": links}


def k_of_n(k: int, n: int) -> dict[str, Any]:
    """Build a spec for k-of-n voting: n parallel blocks, k-out-of-n node."""
    blocks = [_default_block(f"Block{i+1}") for i in range(n)]
    nodes = [
        {"name": "Start", "type": 1, "n": 0, "out": 1},
        {"name": "Split", "type": 2, "n": 1, "out": n, "k": 1},
        {"name": "Vote", "type": 2, "n": n, "out": 1, "k": k},
        {"name": "End", "type": 3, "n": 1, "out": 0},
    ]
    # Start takes one link out (Raptor 7's CheckValidLink), so the paths
    # leave through a node, as Raptor 7's own sample models do.
    links = [{"from": "Start", "to": "Split"}]
    for i in range(n):
        links.append({"from": "Split", "to": f"Block{i+1}"})
        links.append({"from": f"Block{i+1}", "to": "Vote"})
    links.append({"from": "Vote", "to": "End"})
    return {"blocks": blocks, "nodes": nodes, "links": links}


def template_spec(template: str, *, n: int = 2, k: int = 1) -> RbdFile:
    """Return a built ``RbdFile`` for a named template.

    ``template`` is one of ``blank``, ``series``, ``parallel``,
    ``k_of_n``.  ``n`` is the block count; ``k`` is the vote threshold
    (``k_of_n`` only).
    """
    name = template.strip().lower()
    if name in ("blank", "new", ""):
        from raptor.gui.new_model import build_new_model
        return build_new_model()
    if name == "series":
        return build_from_spec(series(n))
    if name == "parallel":
        return build_from_spec(parallel(n))
    if name in ("k_of_n", "kofn", "k-out-of-n", "koutofn"):
        return build_from_spec(k_of_n(k, n))
    raise BuildError(
        f"Unknown template {template!r} -- use blank/series/parallel/k_of_n")


def build_template(template: str, *, n: int = 2, k: int = 1) -> RbdFile:
    """Alias of :func:`template_spec` (same signature)."""
    return template_spec(template, n=n, k=k)


# ---------------------------------------------------------------------------
# add_block / add_node / add_event
# ---------------------------------------------------------------------------

#: Sections whose file format mandates an ``ID=0`` template row carrying the
#: model-wide defaults for that element kind.  A new element must copy THAT
#: row -- not whichever row happens to come first.
_TEMPLATE_ROW_SECTIONS = ("BLOCK_DATA", "EVENT_DATA", "HIERARCHY_DATA")


def _template_row_for(rbd: RbdFile, section: str) -> dict | None:
    """Return the section's ``ID=0`` template row, or None if it has none.

    ``.rbd``/``.rbds`` both require a zero-id row in BLOCK/EVENT/HIERARCHY
    data; it is what carries a model's default block/event properties.
    It is emphatically *not* guaranteed to be the first row -- a saved file
    can order the section any way round -- which is why this looks the row
    up by id instead of taking ``rows[0]``.
    """
    if section not in _TEMPLATE_ROW_SECTIONS:
        return None
    for row in rbd.section(section).rows:
        try:
            if int(float(row.get("ID", -1))) == 0:
                return row
        except (TypeError, ValueError):
            continue
    return None


def _place_new_element(rbd: RbdFile, section: str, row: dict) -> None:
    """Give a freshly added element a usable position.

    The ``ID=0`` template row sits at (0, 0) by definition, so cloning it
    drops every new element on the origin -- which stretches the model's
    render extent and puts the block in a far corner until `auto_layout`
    runs.  Put it one grid step right of the right-most element already in
    the same container instead, so it lands on-canvas and in the right
    sub-hierarchy.  `auto_layout` is still what produces the final tidy
    diagram; this just keeps the interim state sane.
    """
    try:
        if float(row.get("xPos", 0) or 0) or float(row.get("yPos", 0) or 0):
            return          # already has a real position
    except (TypeError, ValueError):
        return

    parent_col = "parent" if section == "NODE_DATA" else "ParentId"
    try:
        parent = int(float(row.get(parent_col, 0) or 0))
    except (TypeError, ValueError):
        return

    siblings: list[dict] = []
    for sec in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA", "HIERARCHY_DATA"):
        col = "parent" if sec == "NODE_DATA" else "ParentId"
        for other in rbd.section(sec).rows:
            if str(other.get("ID", "")) == "0":
                continue
            try:
                if int(float(other.get(col, 0) or 0)) != parent:
                    continue
                x = float(other.get("xPos", 0) or 0)
                y = float(other.get("yPos", 0) or 0)
            except (TypeError, ValueError):
                continue
            siblings.append({"x": x, "y": y})
    if not siblings:
        return
    rightmost = max(siblings, key=lambda s: s["x"])
    row["xPos"] = f"{rightmost['x'] + _PLACE_STEP:.6f}"
    row["yPos"] = f"{rightmost['y']:.6f}"


#: How far right of its container's right-most element a new one starts.
_PLACE_STEP = 2.5


def _append_new_row(rbd: RbdFile, section: str, ctor, **fields) -> dict:
    """Append a new element row seeded from the section's ``ID=0`` template
    row when the format has one, else from *ctor* (an rbdlib row
    constructor, whose values are the documented defaults).
    ``fields`` are applied through the builder's own formatter.

    Seeding from ``rows[0]`` instead -- which is what this used to do --
    silently copied a *real* element's data whenever the template row was
    not first: its ``ParentId`` (dropping the new block inside somebody
    else's sub-hierarchy) and its failure/repair distributions.
    """
    for key, value in fields.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                finite = math.isfinite(float(value))
            except (OverflowError, ValueError):
                finite = False
            if not finite:
                raise BuildError(
                    f"{key} must be a finite number; got {value!r}")
    template = _template_row_for(rbd, section)
    if template is not None:
        try:
            row = add_row(rbd, section, **fields)
        except (ValueError, KeyError):
            row = None
        if row is not None:
            _place_new_element(rbd, section, row)
            return row
    row = ctor()
    for key, value in fields.items():
        if key in row:
            set_fields(row, **{key: value})
    rows = rbd.section(section).rows
    rows.append(row)
    _place_new_element(rbd, section, row)
    return row


def _hierarchy_markers(rbd: RbdFile) -> dict[int, tuple[int, str]]:
    """``{marker node id: (hierarchy id, "in"|"out")}`` for every
    hierarchy's In/Out node, read straight off `HIERARCHY_DATA`."""
    markers: dict[int, tuple[int, str]] = {}
    for hier_row in rbd.section("HIERARCHY_DATA").rows:
        try:
            hid = int(hier_row["ID"])
        except (TypeError, ValueError):
            continue
        for side, field in (("in", "in"), ("out", "out")):
            try:
                mid = int(hier_row.get(field, 0) or 0)
            except (TypeError, ValueError):
                continue
            if mid:
                markers[mid] = (hid, side)
    return markers


def _element_parent_id(rbd: RbdFile, kind: str, oid: int) -> int:
    """The `parent`/`Parent` column of the given Block/Node/Event/Hier --
    0 (top level / no hierarchy) if it has none or can't be resolved."""
    section = {"Block": "BLOCK_DATA", "Node": "NODE_DATA",
               "Event": "EVENT_DATA", "Hier": "HIERARCHY_DATA"}.get(kind)
    if section is None:
        return 0
    try:
        row = get_row(rbd, section, id=oid)
    except (KeyError, ValueError):
        return 0
    raw = row.get("Parent" if kind == "Hier" else "parent", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _redirect_hierarchy_boundary(
    rbd: RbdFile, from_type: str, from_id: int, to_type: str, to_id: int,
) -> tuple[str, int, str, int]:
    """Raptor 7 addresses a link crossing INTO a hierarchy as
    ``(..., "Hier", <hierarchy id>)`` -- never ``(..., "Node", <the In
    marker's own id>)`` -- and a link crossing OUT of one as
    ``("Hier", <hierarchy id>, ...)`` -- never ``("Node", <the Out
    marker's own id>, ...)``.  Confirmed against every real hierarchy
    fixture (`tests/golden/HierarchyExample`: link 19 is "Node 16 -> Hier
    23", link 22 is "Hier 23 -> Node 19" -- nothing ever names node
    24/25, the In/Out markers, directly from outside; the In marker's
    OWN outgoing links (25: "Node 24 -> Block 1") and the Out marker's
    OWN incoming links (26: "Node 20 -> Node 25") stay plain Node-
    addressed, since those are internal to the hierarchy).

    `connect()`/`add_block(after=...)` resolve a name to whatever
    section it lives in, so naming a hierarchy's marker node directly
    (from outside) builds the wrong, direct-Node form -- accepted by
    `validate()` (nothing about the row itself is malformed) but left
    unpopulated in `graph.py`'s `in_paths_array` for a Type-4 In node
    (it only recognises the Hier-addressed form), so the hierarchy
    silently stops reacting to upstream failures -- `single_point_
    failures` then flags every element behind it, `simulate` ignores
    upstream changes reaching it.  This redirects the endpoint to the
    hierarchy itself instead, whenever the OTHER endpoint is not a
    member of that same hierarchy (an internal marker<->member link is
    left exactly as given, matching link 25/26 above)."""
    markers = _hierarchy_markers(rbd)
    if to_type == "Node" and to_id in markers:
        hid, side = markers[to_id]
        if side == "in" and _element_parent_id(rbd, from_type, from_id) != hid:
            to_type, to_id = "Hier", hid
    if from_type == "Node" and from_id in markers:
        hid, side = markers[from_id]
        if side == "out" and _element_parent_id(rbd, to_type, to_id) != hid:
            from_type, from_id = "Hier", hid
    return from_type, from_id, to_type, to_id


def _link_from(rbd: RbdFile, selector: str, to_kind: str, to_id: int) -> None:
    """Wire a link from the element *selector* to (to_kind, to_id)."""
    from raptor.core.fileio.rbdlib import make_link_row
    section, row = resolve_element(rbd, selector)
    ftype = _SECTION_KIND_TITLE.get(section)
    if ftype is None:
        raise BuildError(f"cannot link from {selector!r}")
    from_id = int(row["ID"])
    ftype, from_id, to_kind, to_id = _redirect_hierarchy_boundary(
        rbd, ftype, from_id, to_kind, to_id)
    lid = next_id(rbd, "LINK_DATA")
    rbd.section("LINK_DATA").rows.append(
        make_link_row(lid, ftype, from_id, to_kind, to_id))


#: Friendly block fields `add_block` will fill from the workspace's
#: configured block defaults when the caller omits them.  Canonical list
#: lives in `workspace` so both sides agree without a circular import.
BLOCK_DEFAULT_FIELDS = _BLOCK_DEFAULT_FIELDS


def _apply_block_defaults(workspace: Workspace, kwargs: dict) -> dict:
    """Fill omitted distribution fields from ``workspace.block_defaults``.

    Precedence is: the caller's own arguments, then the workspace defaults,
    then whatever the model's own ``ID=0`` template row says (which is what
    `_append_new_row` seeds from), then `rbdlib`'s canonical values.  The
    workspace defaults exist so a caller can set their house values once --
    the same thing the GUI's "Block Defaults" does, which the MCP tools had
    no equivalent of.
    """
    defaults = getattr(workspace, "block_defaults", None) or {}
    for field in BLOCK_DEFAULT_FIELDS:
        if kwargs.get(field) is None and defaults.get(field) is not None:
            kwargs[field] = defaults[field]
    return kwargs


def _reject_duplicate_name(rbd: RbdFile, section: str, name: str,
                           *, exclude_id: str | None = None,
                           seen: dict[str, str] | None = None) -> None:
    """Refuse a new element whose name is already taken in its section.

    A duplicate does not just look untidy: every later name-based lookup
    (`get_element`, `connect`, `delete_element`, `configure_*`) then
    matches two rows and refuses with "matches 2 elements".  So creating
    the collision is what breaks the model; refuse it at creation.

    `seen` is a bulk-build cache (name -> existing id string). Pass it when
    creating thousands of rows -- the scan over every existing row is
    otherwise quadratic in the spec size.
    """
    cols = _NAME_COLS.get(section)
    if not cols:
        return
    kind = _SECTION_KIND.get(section, section)

    def _raise(existing_id):
        raise BuildError(
            f"A {kind} named {name!r} already exists (id {existing_id}). "
            "Names must be unique -- they are how every other tool "
            "addresses an element. Pick a different name, or address "
            "the existing one directly."
        )

    if seen is not None:
        if name in seen:
            _raise(seen[name])
        return
    for row in rbd.section(section).rows:
        if exclude_id is not None and str(row.get("ID", "")) == exclude_id:
            continue
        if any(row.get(col) == name for col in cols):
            _raise(row.get("ID"))


def _checkdist_code(kind: str, code: Any) -> None:
    """Refuse a distribution code the engine has no rule for.

    An unknown code is stored verbatim and the block then has no failure
    law at all (mean/stdev come back null, so it can never fail), while
    every tool reports success.  Codes come from `list_distributions`.
    """
    from raptor.api.distributions import PARAM_INFO

    if code is None:
        return
    if isinstance(code, str) and not _is_plain_number(code):
        from raptor.core.fileio.rbds import dist_code
        try:
            code = dist_code(code)
        except ValueError as exc:
            raise BuildError(str(exc)) from None
    try:
        as_int = int(code)
    except (TypeError, ValueError):
        raise BuildError(
            f"{kind}_dist must be a distribution name (e.g. 'Weibull') or "
            f"code from list_distributions, got {code!r}"
        ) from None
    if as_int not in PARAM_INFO:
        valid = ", ".join(str(c) for c in sorted(PARAM_INFO))
        raise BuildError(
            f"Unknown {kind} distribution code {as_int}. "
            f"Valid codes: {valid}. Call list_distributions for each "
            "code's name, parameter names and valid ranges."
        )


def check_new_block(rbd: RbdFile, row: dict) -> list[str]:
    """Soft problems with a just-placed block, as human-readable strings.

    For the things that are recoverable (a `parent` naming no hierarchy,
    a distribution whose parameters make it inert) -- as opposed to
    `_reject_duplicate_name` / `_checkdist_code`, which are hard errors.
    """
    warnings: list[str] = []
    parent = row.get("ParentId", 0)
    try:
        parent_id = int(float(parent or 0))
    except (TypeError, ValueError):
        parent_id = 0
    if parent_id:
        hier_ids = {str(r.get("ID")) for r in rbd.section("HIERARCHY_DATA").rows}
        if str(parent_id) not in hier_ids:
            warnings.append(
                f"parent={parent_id} does not name a hierarchy in this model "
                f"(available: {', '.join(sorted(hier_ids)) or 'none'}), so "
                "this block is placed at an orphaning level -- it will not "
                "appear in the diagram at this level and may not resolve. "
                "Read get_element on the hierarchy to get its id."
            )
    for kind, code_col in (("fail", "FDist"), ("repair", "RDist")):
        try:
            code = int(float(row.get(code_col, 0) or 0))
        except (TypeError, ValueError):
            code = 0
        if code == 3 and _param(row, "FParam2" if kind == "fail" else "RParam2") == 0:
            warnings.append(
                f"{kind} distribution is Binomial (code 3) with probability "
                "0, so this block can never fail/repair. Binomial takes "
                "(trials, probability) -- call list_distributions to check "
                "a code's parameter order."
            )
    return warnings


def _param(row: dict, col: str) -> float:
    try:
        return float(row.get(col, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _check_parent(rbd: RbdFile, parent: Any) -> None:
    """`parent` must be an integer hierarchy id (0 = top level).

    Existence is deliberately *not* enforced here -- a dangling id is a
    warning from `check_new_block`, not a failed placement.
    """
    try:
        int(parent or 0)
    except (TypeError, ValueError):
        raise BuildError(f"parent must be a hierarchy id, got {parent!r}") from None
    # A `parent` naming no hierarchy is recoverable: the element is placed at
    # an orphaning level and `check_new_block` reports it, so the caller sees
    # the warning instead of losing an otherwise valid placement.
    return


def add_block(
    workspace: Workspace,
    model_id: str,
    name: str,
    *,
    after: str | None = None,
    **kwargs,
) -> dict:
    """Add a block to the model.  ``kwargs`` may use any friendly .rbds
    field (or a legacy column); numeric values may be expression strings.
    ``after`` wires a link from that element into the new block.  Returns
    the new block's row dict.

    Raises `BuildError` on a duplicate name or an unknown distribution
    code; recoverable problems are reported by `check_new_block`.
    """
    from raptor.core.fileio.rbdlib import make_block_row
    handle = workspace.get(model_id)
    rbd = handle.rbd
    kwargs = _apply_block_defaults(workspace, kwargs)
    _check_parent(rbd, kwargs.get("parent", 0))
    _reject_duplicate_name(rbd, "BLOCK_DATA", name)
    _checkdist_code("fail", kwargs.get("fail_dist"))
    _checkdist_code("repair", kwargs.get("repair_dist"))
    bid = next_id(rbd, "BLOCK_DATA")
    row = _append_new_row(rbd, "BLOCK_DATA", lambda: make_block_row(bid, name=name),
                          ID=bid, blockName=name)
    _require_dist_params_on_new_block(kwargs)
    _apply_patch(rbd, "BLOCK_DATA", row, "block", kwargs)
    _fix_dist_param_counts(row, kwargs)
    if after is not None:
        _link_from(rbd, after, "Block", bid)
    _fix_link_counts(rbd)
    sync_counts(rbd)
    workspace.save(model_id)
    return row


def add_node(
    workspace: Workspace,
    model_id: str,
    name: str,
    *,
    type: Any = None,
    ntype: Any = None,
    k: int = -1,
    parent: int = 0,
    **kwargs,
) -> dict:
    """Add a node to the model.  ``type``/``ntype`` accept a name
    ("start"/"regular"/"end"/"in"/"out") or a number."""
    from raptor.core.fileio.rbdlib import make_node_row
    handle = workspace.get(model_id)
    rbd = handle.rbd
    _check_parent(rbd, parent)
    _reject_duplicate_name(rbd, "NODE_DATA", name)
    nid = next_id(rbd, "NODE_DATA")
    node_type = ntype if ntype is not None else type
    if node_type is None:
        node_type = 2
    if isinstance(node_type, str):
        key = node_type.strip().lower()
        if key not in _NODE_TYPE_MAP:
            raise BuildError(f"unknown node type {node_type!r}")
        node_type = _NODE_TYPE_MAP[key]
    node_type = int(node_type)
    if node_type in (4, 5):
        raise BuildError(
            "In/Out markers belong to a hierarchy; use add_hierarchy")
    row = _append_new_row(
        rbd, "NODE_DATA", lambda: make_node_row(nid, name, 0, 0),
        ID=nid, Name=name, Type=node_type, k=k, parent=parent)
    _apply_patch(rbd, "NODE_DATA", row, "node", kwargs)
    _fix_link_counts(rbd)
    sync_counts(rbd)
    workspace.save(model_id)
    return row


def add_event(
    workspace: Workspace,
    model_id: str,
    name: str,
    *,
    prob: float | str = 1.0,
    **kwargs,
) -> dict:
    """Add an event to the model, storing its failure probability."""
    from raptor.core.fileio.rbdlib import make_event_row
    handle = workspace.get(model_id)
    rbd = handle.rbd
    _reject_duplicate_name(rbd, "EVENT_DATA", name)
    eid = next_id(rbd, "EVENT_DATA")
    row = _append_new_row(
        rbd, "EVENT_DATA", lambda: make_event_row(eid, name, 0, 0),
        ID=eid, eventName=name, failVal=prob)
    _apply_patch(rbd, "EVENT_DATA", row, "event", kwargs)
    _fix_link_counts(rbd)
    sync_counts(rbd)
    workspace.save(model_id)
    return row


# ---------------------------------------------------------------------------
# Hierarchies
# ---------------------------------------------------------------------------

#: Node `Type` codes for a hierarchy's boundary markers, and the k/n/Out
#: triple each needs.  Mirrors `DiagramView._build_hierarchy_rows` so a
#: hierarchy created here is byte-for-byte the shape the GUI produces.
_HIER_MARKERS = (
    ("in", 4, "1", "0", "1", -10.0),
    ("out", 5, "-1", "1", "0", 10.0),
)


def _container_centre(rbd: RbdFile, parent: int) -> tuple[float, float] | None:
    """Mean position of the elements already in container *parent*."""
    points: list[tuple[float, float]] = []
    for sec in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA", "HIERARCHY_DATA"):
        col = "parent" if sec == "NODE_DATA" else "ParentId"
        for row in rbd.section(sec).rows:
            if str(row.get("ID", "")) == "0":
                continue
            try:
                if int(float(row.get(col, 0) or 0)) != parent:
                    continue
                points.append((float(row.get("xPos", 0) or 0),
                            float(row.get("yPos", 0) or 0)))
            except (TypeError, ValueError):
                continue
    if not points:
        return None
    return (sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points))


def _build_hierarchy(rbd: RbdFile, name: str, parent: int,
                     x: float, y: float) -> tuple[int, int, int]:
    """Append a hierarchy row plus its In/Out marker nodes.

    Returns ``(hier_id, in_id, out_id)``.  Each marker's own ``parent`` is
    the *hierarchy's* id, not the enclosing level -- that is the file
    format's convention and getting it wrong crashes graph building with
    an unhelpful KeyError, so it is set here rather than left to the
    caller.
    """
    from raptor.core.fileio.rbdlib import make_hierarchy_row, make_node_row

    _reject_duplicate_name(rbd, "HIERARCHY_DATA", name)
    hid = next_id(rbd, "HIERARCHY_DATA")

    # `next_id` reads the file to find the lowest unused id and does not
    # reserve it, so each id has to be *inserted* before the next is asked
    # for -- otherwise the hierarchy and both markers all get the same one.
    hier_row = _append_new_row(rbd, "HIERARCHY_DATA",
                           lambda: make_hierarchy_row(hid, name, x, y, parent=parent),
                           ID=hid, hierarchyName=name, Parent=parent,
                           In=1, Out=1, isCNd=1, **{"in": 0, "out": 0})
    # `level` is the hierarchy's own nesting depth: 1 at the top, one more than its container's otherwise (Raptor 7's
    #`NestedHierarchyCost`: a hierarchy inside `500path` has level 2). Left at 1, a nested hierarchy's costs landed
    # in the wrong report rows (`LadL8A`).
    parent_row = next((r for r in rbd.section("HIERARCHY_DATA").rows if str(r.get("ID")) == str(parent)), None)
    set_fields(hier_row, level=(int(float(parent_row.get("level", 1) or 1)) + 1) if parent_row is not None else 1)
    marker_ids: list[int] = []
    for marker, ntype, k, n, out, dx in _HIER_MARKERS:
        mid = next_id(rbd, "NODE_DATA")
        _append_new_row(
            rbd, "NODE_DATA",
            lambda mid=mid, marker=marker, ntype=ntype, k=k, n=n, out=out, dx=dx:
                make_node_row(mid, marker, x + dx, y + 1.25),
            ID=mid, Name=marker, Type=ntype, k=k, n=n, Out=out, parent=hid,
        )
        marker_ids.append(mid)
    in_id, out_id = marker_ids
    # Every other column in the file is a string; writing ints here would
    # leave the validator comparing `4` against `'4'` and reporting the
    # marker as missing.
    set_fields(hier_row, **{"in": in_id, "out": out_id})
    return int(float(hier_row["ID"])), in_id, out_id


def add_hierarchy(
    workspace: Workspace,
    model_id: str,
    name: str,
    *,
    parent: int = 0,
    x: float | None = None,
    y: float | None = None,
) -> dict:
    """Create an empty hierarchy (a sub-model boundary) in the model.

    Creates the oval *and* its In/Out marker nodes, which is the fiddly
    part: each marker's `parent` must be the new hierarchy's own id, and
    both markers' `n` must match their real link counts or the engine
    crashes on load.  Doing it by hand through `build_model` is possible
    but easy to get wrong.

    The hierarchy starts empty -- add elements inside it with
    `add_block(parent=<id>)` etc.  Its boundary is not wired to anything
    until you `connect` to the In marker; `collapse_selection` is the
    one-call way to build a hierarchy *around* elements you already have.
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    _check_parent(rbd, parent)
    if x is None or y is None:
        centre = _container_centre(rbd, parent)
        x, y = centre if centre is not None else (0.0, 0.0)
    hid, in_id, out_id = _build_hierarchy(rbd, name, parent, x, y)
    _fix_link_counts(rbd)
    sync_counts(rbd)
    workspace.save(model_id)
    return {"ID": str(hid), "hierarchyName": name, "in": in_id,
            "out": out_id, "Parent": parent}


def _as_int_id(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return -1


def collapse_selection(
    workspace: Workspace,
    model_id: str,
    selectors: list[str],
    name: str | None = None,
    *,
    parent: int = 0,
) -> dict:
    """Move existing elements into a new hierarchy, rewiring the boundary.

    The API counterpart of the GUI's *Collapse Selection*.  Elements named
    by *selectors* become the new hierarchy's contents; links wholly inside
    them are untouched, and links that crossed the boundary are rewritten
    to address the hierarchy instead -- which is how the file format
    represents a sub-model's edges (``Node -> Hier`` / ``Hier -> Node`` on
    the enclosing level).

    Start/End nodes and existing hierarchies are refused: a hierarchy
    cannot contain the model's own terminators, and nesting one hierarchy
    inside another needs the boundary rewiring done by hand.

    Returns ``{hier_id, in_id, out_id, name, moved}``.
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    if not selectors:
        raise BuildError(
            "collapse_selection needs at least one element to move. Pass "
            "the elements (by name or Kind:id) that should become the new "
            "hierarchy's contents."
        )

    chosen: list[tuple[str, dict]] = []
    for selector in selectors:
        section, row = resolve_element(rbd, selector)
        if section == "HIERARCHY_DATA":
            raise BuildError(
                f"{selector!r} is a hierarchy; collapse_selection moves "
                "blocks, nodes and events. Use add_hierarchy to make an "
                "empty one, or address its members individually."
            )
        if section == "NODE_DATA" and str(row.get("Type", "2")) != "2":
            raise BuildError(
                f"{selector!r} is a Start/End/In/Out node, which a "
                "hierarchy cannot contain. Pass regular nodes, blocks or "
                "events."
            )
        chosen.append((section, row))
    if len({id(r) for _, r in chosen}) != len(chosen):
        raise BuildError("collapse_selection: the same element was named twice")

    keys = {(_SECTION_KIND_TITLE.get(sec, sec), str(r.get("ID"))) for sec, r in chosen}
    crossing = [((link.get("FromType", ""), str(link.get("FromID", ""))) in keys,
                 (link.get("ToType", ""), str(link.get("ToID", ""))) in keys)
                for link in rbd.section("LINK_DATA").rows
                if _as_int_id(link.get("pID")) == parent]
    from raptor.core.input_rules import collapse_boundary_problem
    problem = collapse_boundary_problem(sum(1 for f, t in crossing if t and not f),
                                        sum(1 for f, t in crossing if f and not t))
    if problem:
        raise BuildError(problem)

    label = name or f"Hierarchy_{len(rbd.section('HIERARCHY_DATA').rows) + 1}"
    centre = _container_centre(rbd, parent)
    hid, in_id, out_id = _build_hierarchy(
        rbd, label, parent, *(centre if centre is not None else (0.0, 0.0)))

    # Re-parent the chosen elements, and record where each came from.
    owner = {(_SECTION_KIND_TITLE.get(sec, sec), str(r.get("ID"))): r
             for sec, r in chosen}
    inside = {(_SECTION_KIND_TITLE.get(sec, sec), str(r.get("ID")))
              for sec, r in chosen}
    col = {"NODE_DATA": "parent"}
    for sec, row in chosen:
        row[col.get(sec, "ParentId")] = str(hid)

    # Rewire every link on the enclosing level that touches the selection,
    # exactly as the GUI's Collapse Selection does: an internal link just
    # moves down a level, and a boundary link SPLITS -- keeping its id on
    # the outside half and adding an inside half off the In/Out marker, so
    # the sub-model's own graph stays complete.
    link_sec = rbd.section("LINK_DATA")
    used_link = {_as_int_id(link.get("ID")) for link in link_sec.rows}

    def fresh_link_id() -> str:
        n = (max(used_link) + 1) if used_link else 1
        while n in used_link:
            n += 1
        used_link.add(n)
        return str(n)

    def on_this_level(link: dict) -> bool:
        return _as_int_id(link.get("pID")) == parent

    for link in list(link_sec.rows):
        if not on_this_level(link):
            continue
        from_key = (link.get("FromType", ""), str(link.get("FromID", "")))
        to_key = (link.get("ToType", ""), str(link.get("ToID", "")))
        f_in, t_in = from_key in inside, to_key in inside
        if not (f_in or t_in):
            continue
        if f_in and t_in:
            link["pID"] = str(hid)            # travels with the selection
        elif t_in:
            # X -> S   becomes   X -> Hier   (outside, keeps its id)
            #                     In -> S    (inside,  new id)
            inside_link = dict(link)
            link["ToType"], link["ToID"] = "Hier", str(hid)
            link["pID"] = str(parent)
            inside_link["FromType"], inside_link["FromID"] = "Node", str(in_id)
            inside_link["pID"] = str(hid)
            inside_link["ID"] = fresh_link_id()
            link_sec.rows.append(inside_link)
        else:
            # S -> X   becomes   S -> Out    (inside,  new id)
            #                     Hier -> X  (outside, keeps its id)
            inside_link = dict(link)
            link["FromType"], link["FromID"] = "Hier", str(hid)
            link["pID"] = str(parent)
            inside_link["ToType"], inside_link["ToID"] = "Node", str(out_id)
            inside_link["pID"] = str(hid)
            inside_link["ID"] = fresh_link_id()
            link_sec.rows.append(inside_link)

    _fix_link_counts(rbd)
    sync_counts(rbd)
    workspace.save(model_id)
    return {"ID": str(hid), "hierarchyName": label, "in": in_id,
            "out": out_id, "Parent": parent,
            "moved": sorted(owner.values(), key=lambda r: str(r.get("ID")))}


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------

def connect(
    workspace: Workspace,
    model_id: str,
    from_type: str,
    from_id: int,
    to_type: str,
    to_id: int,
    *,
    replace: bool = False,
    **kwargs,
) -> dict:
    """Add a link between two elements.

    ``replace=True`` first removes any existing outgoing link from the
    source and any existing incoming link to the target, so a wrong or
    stale connection can be corrected in one call instead of deleting an
    element (the old workaround, which also destroyed its other links).

    Naming a hierarchy's In or Out marker node directly, from outside
    that hierarchy, is redirected to address the hierarchy itself
    instead (``"Hier"``/its id) -- see `_redirect_hierarchy_boundary`
    for why: it's how Raptor 7 represents a link crossing a
    hierarchy boundary on disk, and the direct-node form silently
    breaks that hierarchy's own status propagation.
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    from_type, from_id, to_type, to_id = _redirect_hierarchy_boundary(
        rbd, from_type, int(from_id), to_type, int(to_id))
    if str(from_type) == str(to_type) and int(from_id) == int(to_id):
        raise BuildError("a link cannot connect an element to itself")
    if replace:
        sec = rbd.section("LINK_DATA")
        sec.rows = [
            link for link in sec.rows
            if not ((str(link.get("FromType")) == str(from_type)
                     and str(link.get("FromID")) == str(from_id))
                    or (str(link.get("ToType")) == str(to_type)
                        and str(link.get("ToID")) == str(to_id)))]
    lid = next_id(rbd, "LINK_DATA")
    from raptor.core.fileio.rbdlib import make_link_row
    row = make_link_row(lid, from_type, from_id, to_type, to_id, **kwargs)
    rbd.section("LINK_DATA").rows.append(row)
    _fix_link_counts(rbd)
    sync_counts(rbd)
    workspace.save(model_id)
    return row


def delete_link(workspace: Workspace, model_id: str,
                link_id: int | str) -> dict:
    """Delete one link by id, leaving both elements in place.

    Until this existed the only way to remove a single connection was to
    delete one of its elements, which also removed every other link on
    that element.
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    sec = rbd.section("LINK_DATA")
    target = str(link_id).strip()
    if target.lower().startswith("link:"):
        target = target.split(":", 1)[1]
    for row in list(sec.rows):
        if str(row.get("ID")) == target:
            sec.rows.remove(row)
            _fix_link_counts(rbd)
            sync_counts(rbd)
            workspace.save(model_id)
            return {"deleted": f"Link:{target}", "kind": "Link",
                    "id": target}
    raise ElementNotFound(f"No link with ID {target}")


def move_element(workspace: Workspace, model_id: str, selector: str,
                 parent: int) -> dict:
    """Move a block, node, event or hierarchy to another hierarchy level.

    `update_element` refuses `parent` because it is structural; the
    alternative was deleting the element and recreating it (losing its
    links and spare/PM settings).  A hierarchy cannot be moved into
    itself or one of its own descendants.
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    section, row = resolve_element(rbd, selector)
    if section not in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                       "HIERARCHY_DATA"):
        raise BuildError(
            f"{_SECTION_KIND_TITLE.get(section, section)} rows are not "
            f"placed on a hierarchy level")
    target = int(parent)
    if target == 0:
        pass
    else:
        try:
            get_row(rbd, "HIERARCHY_DATA", id=target)
        except (KeyError, ValueError):
            raise BuildError(
                f"hierarchy {target} does not exist") from None

    if section == "HIERARCHY_DATA":
        moving = int(float(row.get("ID", "0")))
        if target == moving:
            raise BuildError("a hierarchy cannot be moved into itself")
        ancestor = target
        seen: set[int] = set()
        while ancestor not in (0, moving) and ancestor not in seen:
            seen.add(ancestor)
            try:
                parent_row = get_row(rbd, "HIERARCHY_DATA", id=ancestor)
            except (KeyError, ValueError):
                break
            ancestor = int(float(parent_row.get("Parent", "0")))
        if ancestor == moving:
            raise BuildError(
                "a hierarchy cannot be moved into one of its own "
                "descendants")
        row["Parent"] = str(target)
        # Keep the stored nesting depth in step; the writer derives it,
        # but an in-memory read straight after this call should agree.
        depth = 1
        cursor = target
        guard = 0
        while cursor not in (0,) and guard < 64:
            try:
                cursor = int(float(get_row(rbd, "HIERARCHY_DATA",
                                           id=cursor).get("Parent", "0")))
            except (KeyError, ValueError):
                break
            depth += 1
            guard += 1
        row["level"] = str(depth)
    elif section == "NODE_DATA":
        row["parent"] = str(target)
    else:
        row["ParentId"] = str(target)

    _fix_link_counts(rbd)
    sync_counts(rbd)
    workspace.save(model_id)
    return row


def _aux_add(workspace: Workspace, model_id: str, section: str,
             row_factory, name: str, duplicates: tuple[str, ...]) -> dict:
    """Append one row to an auxiliary table, refusing a duplicate name."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    from raptor.core.fileio.rbdlib import (RES_COLS, SPARE_COLS,
                                           TRIGGER_COLS)
    cols = {"SPARE_POOL_DATA": SPARE_COLS, "RESOURCE_DATA": RES_COLS,
            "TRIGGERS": TRIGGER_COLS}[section]
    existing = rbd.sections.get(section)
    if existing is None or not existing.columns:
        rbd.sections[section] = RbdSection(
            name=section, columns=list(cols), rows=[])
        existing = rbd.sections[section]
    name_col = duplicates[0]
    wanted = str(name).strip()
    if not wanted:
        raise BuildError("name must not be empty")
    for row in existing.rows:
        if str(row.get(name_col, "")).strip().lower() == wanted.lower():
            raise BuildError(
                f"{_SECTION_KIND_TITLE[section].lower()} {wanted!r} already "
                f"exists")
    row = row_factory()
    existing.rows.append(row)
    workspace.save(model_id)
    return row


def add_spare_pool(workspace: Workspace, model_id: str, name: str, *,
                   stock: int = 0, new_spares: int = 1,
                   arrive_every: float = 720.0, eso: int = 0,
                   emer_time: float = 24.0, slo: int = 0, level: int = 0,
                   quantity: int = 1, slo_time: float = 24.0, rso: int = 0,
                   spare_cost: float = 1.0,
                   emer_ship_cost: float = 1.0) -> dict:
    """Create a spare pool (the table `configure_sparing(mode="pool")`
    draws from)."""
    from raptor.core.fileio.rbdlib import make_spare_pool_row
    return _aux_add(
        workspace, model_id, "SPARE_POOL_DATA",
        lambda: make_spare_pool_row(name=name, rso=int(rso), stock=int(stock),
                        newsp=int(new_spares), arrive=float(arrive_every),
                        eso=int(eso), emertime=float(emer_time),
                        slo=int(slo), level=int(level), quan=int(quantity),
                        slotime=float(slo_time), spcost=float(spare_cost),
                        emercost=float(emer_ship_cost)),
        name, ("PoolName",))


def add_resource(workspace: Workspace, model_id: str, name: str, *,
                 num_in: int = 1, spare_cost: float = 1.0,
                 fixed_per_use: float = 1.0, per_time_unit: float = 1.0,
                 phases: int = 0) -> dict:
    """Create a resource pool (shared crew/equipment)."""
    from raptor.core.fileio.rbdlib import make_resource_row
    return _aux_add(
        workspace, model_id, "RESOURCE_DATA",
        lambda: make_resource_row(name=name, numin=int(num_in),
                       sparecost=float(spare_cost),
                       fixed=float(fixed_per_use),
                       perunit=float(per_time_unit), phases=int(phases)),
        name, ("PoolName",))


def add_trigger(workspace: Workspace, model_id: str, name: str, *,
                init_used: float = 0.0, dist: Any = 8,
                params: tuple[float, ...] | list[float] = (288.0, 1.0, 0.0),
                repeats: int = 1) -> dict:
    """Create a trigger (used by triggered preventive maintenance)."""
    from raptor.core.fileio.rbdlib import make_trigger_row
    code = dist
    if isinstance(code, str) and not _is_plain_number(code):
        from raptor.core.fileio.rbds import dist_code
        try:
            code = dist_code(code)
        except ValueError as exc:
            raise BuildError(str(exc)) from None
    return _aux_add(
        workspace, model_id, "TRIGGERS",
        lambda: make_trigger_row(name=name, initused=float(init_used),
                           dist=int(code), params=tuple(params),
                           repeats=int(repeats)),
        name, ("TriggerName",))


def delete_named_row(workspace: Workspace, model_id: str,
                     selector: str) -> dict:
    """Delete a spare pool, resource or trigger by name.

    Refused while a block still names it -- a dangling reference is what
    the reference checks exist to prevent.
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    section, row = resolve_element(rbd, selector)
    if section not in _NAMED_ONLY_SECTIONS:
        raise BuildError(
            f"{_SECTION_KIND_TITLE.get(section, section)} rows are deleted "
            f"with delete_element, not this tool")
    kind = _SECTION_KIND_TITLE[section]
    name = row.get(_NAME_COLS[section][0], "")
    users: list[str] = []
    for block in rbd.section("BLOCK_DATA").rows:
        label = block.get("blockName") or f"Block {block.get('ID')}"
        if section == "SPARE_POOL_DATA" and \
                str(block.get("SpType", "")).lower() == "pooled" and \
                block.get("SparePoolName") == name:
            users.append(label)
        elif section == "RESOURCE_DATA" and \
                block.get("Resource1Name") == name:
            users.append(label)
        elif section == "TRIGGERS" and block.get("pmTrig") == name:
            users.append(label)
    if users:
        raise BuildError(
            f"{kind.lower()} {name!r} is still used by {len(users)} "
            f"block(s) ({', '.join(users[:5])}"
            f"{', ...' if len(users) > 5 else ''}); clear those references "
            f"first")
    rbd.section(section).rows.remove(row)
    sync_counts(rbd)
    workspace.save(model_id)
    return {"deleted": name, "kind": kind}


# ---------------------------------------------------------------------------
# update_element / delete_element
# ---------------------------------------------------------------------------

#: Columns that define an element's identity or the graph, not its
#: properties.  Letting `update_element` write these was a referential-
#: integrity hole: changing a Block's ID was accepted and saved, leaving
#: every link pointing at an id that no longer existed.  Structural changes
#: go through the dedicated tools (add/delete/connect/collapse, phases).
_STRUCTURAL_COLUMNS = frozenset({
    "ID", "parent", "ParentId", "Parent",
    "FromType", "FromID", "ToType", "ToID", "pID",
    "in", "out", "Phasing",
})
_PHASE_COLUMN_RE = re.compile(r"^P ?\d{3}$")


def _reject_structural_patch(section: str, row: dict,
                             patch: dict[str, Any]) -> None:
    """Refuse a patch that would rewrite identity/graph/phase columns."""
    bad: list[str] = []
    for field in patch:
        for col in _column_targets(section, row, field):
            if col in _STRUCTURAL_COLUMNS or _PHASE_COLUMN_RE.match(col):
                bad.append(f"{field} -> {col}")
    if bad:
        raise BuildError(
            "these fields define identity, topology or phasing and cannot "
            "be patched here: " + ", ".join(sorted(set(bad)))
            + ". Use add/delete/connect for topology, configure_phases for "
              "phase cells, and the dedicated tools for hierarchy moves."
        )


def _node_type_code(value: Any) -> int:
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _NODE_TYPE_MAP:
            return int(_NODE_TYPE_MAP[key])
        raise BuildError(f"unknown node type {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BuildError(f"unknown node type {value!r}") from None


def _check_node_type_change(rbd: RbdFile, row: dict, new_type: int) -> None:
    """Refuse a node-type change that would break the Start/End rules.

    `update_element` used to accept `{"type": "end"}` on a regular node,
    producing a second End node: the model saved, then validation refused
    it.  A hierarchy's In/Out markers are owned by their hierarchy and
    cannot become ordinary nodes either.
    """
    marker_owner = None
    for hier in rbd.section("HIERARCHY_DATA").rows:
        if str(row.get("ID")) in (str(hier.get("in", "")),
                                  str(hier.get("out", ""))):
            marker_owner = hier
            break
    old = int(float(row.get("Type", "2")))
    if new_type == old:
        return
    if marker_owner is not None:
        raise BuildError(
            f"node {row.get('Name') or row.get('ID')!r} is the In/Out marker "
            f"of hierarchy {marker_owner.get('hierarchyName')!r}; its type "
            f"cannot be changed")
    if new_type in (1, 3):
        label = "Start" if new_type == 1 else "End"
        clash = [r for r in rbd.section("NODE_DATA").rows
                 if r is not row and str(r.get("Type")) == str(new_type)]
        if clash:
            raise BuildError(
                f"only one {label} node is supported; node "
                f"{clash[0].get('Name') or clash[0].get('ID')!r} already is "
                f"the {label} node")
    if new_type in (4, 5):
        raise BuildError(
            "In/Out markers belong to a hierarchy; create one with "
            "add_hierarchy instead of converting a node")


def _check_named_references(rbd: RbdFile, section: str, row: dict) -> None:
    """Validate the by-name references a block row carries.

    Until now only triggers were checked (inside `configure_maintenance`);
    a block could name a spare pool or a resource that does not exist and
    the model still validated and simulated.  Reported with the available
    names so the fix is obvious.
    """
    if section != "BLOCK_DATA":
        return
    name = row.get("blockName") or f"Block {row.get('ID')}"

    def _names(sec: str, col: str) -> list[str]:
        section_rows = rbd.sections.get(sec)
        return sorted({r.get(col, "") for r in (section_rows.rows if section_rows else [])
                       if r.get(col, "")})

    if str(row.get("SpType", "")).strip().lower() == "pooled":
        pool = str(row.get("SparePoolName", "")).strip()
        available = _names("SPARE_POOL_DATA", "PoolName")
        if pool not in available:
            raise BuildError(
                f"block {name!r} uses spare pool {pool!r}, which is not "
                f"defined; available pools: "
                f"{', '.join(available) or 'none'}")

    resource = str(row.get("Resource1Name", "")).strip()
    if resource not in ("", "None"):
        available = _names("RESOURCE_DATA", "PoolName")
        if resource not in available:
            raise BuildError(
                f"block {name!r} uses resource {resource!r}, which is not "
                f"defined; available resources: "
                f"{', '.join(available) or 'none'}")

    trigger = str(row.get("pmTrig", "")).strip()
    if trigger not in ("", "None", "none") and \
            str(row.get("pmTriggered", "0")) not in ("0", ""):
        available = _names("TRIGGERS", "TriggerName")
        # A model with no triggers table predates this check: leave the
        # legacy "name it now, add the trigger later" workflow alone.
        # configure_maintenance still refuses an unknown trigger.
        if available and trigger not in available:
            raise BuildError(
                f"block {name!r} uses trigger {trigger!r}, which is not "
                f"defined; available triggers: "
                f"{', '.join(available) or 'none'}")


def update_element(
    workspace: Workspace,
    model_id: str,
    selector: str,
    patch: dict[str, Any],
) -> dict:
    """Update fields on an element identified by *selector* (name or
    ``Block:N``).

    *patch* accepts any friendly ``.rbds`` field (including sparing, PM,
    dependency, standby stress, costs, growth/decay and indexed forms
    like ``repair_params[0]``) or a legacy column name.  Numeric values
    may be expression strings; the resolved float is stored and its
    provenance recorded in ``RbdFile.formulas``.
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    section, row = resolve_element(rbd, selector)
    kind = _SECTION_KIND.get(section, section)
    _reject_structural_patch(section, row, patch)
    for key in ("name", "blockName", "eventName", "Name", "hierarchyName"):
        if key in patch:
            _reject_duplicate_name(
                rbd, section, str(patch[key]),
                exclude_id=str(row.get("ID")))
            break
    before = dict(row)
    if section == "NODE_DATA":
        for key in ("type", "ntype"):
            if key in patch:
                _check_node_type_change(rbd, row, _node_type_code(patch[key]))
    _apply_patch(rbd, section, row, kind, patch)
    try:
        _check_named_references(rbd, section, row)
    except BuildError:
        row.clear()
        row.update(before)
        raise
    if section == "BLOCK_DATA":
        _fix_dist_param_counts(row, patch)
        from raptor.core.distro_rules import validate_distro_params
        for prefix, use in (("F", "fail"), ("R", "repair")):
            try:
                dist = int(float(row.get(f"{prefix}Dist", "0")))
                n = int(float(row.get(f"#{prefix}Param", "0")))
                params = [float(row.get(f"{prefix}Param{i}", "0"))
                          for i in (1, 2, 3)]
            except (TypeError, ValueError):
                continue
            if dist == 0 or n <= 0:
                continue
            err = validate_distro_params(
                dist, params[:n],
                f"Block '{row.get('blockName', row.get('ID'))}' {use}")
            if err:
                row.clear()
                row.update(before)
                raise BuildError(err)
    sync_counts(rbd)
    workspace.save(model_id)
    return row


def delete_element(
    workspace: Workspace,
    model_id: str,
    selector: str,
    *,
    reconnect: bool = False,
) -> dict:
    """Delete an element.  If *reconnect=True*, remove its links and
    merge its first incoming link with its first outgoing link so the
    graph stays connected."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    section, row = resolve_element(rbd, selector)

    elem_id = row.get("ID")
    kind = _SECTION_KIND_TITLE.get(section, section)

    # A hierarchy owns its children. Removing only its HIERARCHY_DATA row
    # left every child pointing at a parent that no longer exists: the
    # model validated as runnable, then the engine failed with a bare
    # numeric KeyError. Refuse until the contents are moved/deleted.
    if section == "HIERARCHY_DATA" and str(elem_id) != "0":
        hid = str(elem_id)
        owned = sum(
            1 for section_name, parent_col in (
                ("BLOCK_DATA", "ParentId"), ("NODE_DATA", "parent"),
                ("EVENT_DATA", "ParentId"), ("HIERARCHY_DATA", "Parent"))
            for child in rbd.section(section_name).rows
            if str(child.get("ID")) != hid
            and str(child.get(parent_col, "0")) == hid)
        if owned:
            raise BuildError(
                f"hierarchy {row.get('hierarchyName', hid)!r} still "
                f"contains {owned} element(s); move or delete them first")

    links_before = len(rbd.section("LINK_DATA").rows)
    if reconnect:
        _reconnect_on_delete(rbd, kind, str(elem_id))

    # An empty hierarchy's in/out boundary nodes belong to it; leaving
    # them behind turns a delete into two orphan nodes.
    orphan_nodes: set[str] = set()
    if section == "HIERARCHY_DATA" and str(elem_id) != "0":
        orphan_nodes = {
            str(row.get(col)) for col in ("in", "out")
            if str(row.get(col, "0")) not in ("", "0", "None")}

    # Remove links referencing this element (and its boundary nodes)
    links = rbd.section("LINK_DATA").rows
    rbd.section("LINK_DATA").rows = [
        link for link in links
        if not ((link.get("FromType") == kind and link.get("FromID") == elem_id)
                or (link.get("ToType") == kind and link.get("ToID") == elem_id)
                or (link.get("FromType") == "Node"
                    and str(link.get("FromID")) in orphan_nodes)
                or (link.get("ToType") == "Node"
                    and str(link.get("ToID")) in orphan_nodes))
    ]
    links_removed = links_before - len(rbd.section("LINK_DATA").rows)

    # Remove the element itself
    sec_rows = rbd.section(section).rows
    rbd.section(section).rows = [
        r for r in sec_rows if r.get("ID") != elem_id]
    if orphan_nodes:
        rbd.section("NODE_DATA").rows = [
            n for n in rbd.section("NODE_DATA").rows
            if str(n.get("ID")) not in orphan_nodes]

    _fix_link_counts(rbd)
    sync_counts(rbd)
    workspace.save(model_id)
    return {"deleted": selector, "kind": kind, "id": elem_id,
            "links_removed": links_removed}


def _reconnect_on_delete(rbd: RbdFile, kind: str, elem_id: str) -> None:
    """When deleting with reconnect, merge the first incoming link with
    the first outgoing link (the merged link survives the caller's own
    link filter because it references the neighbours, not the deleted
    element)."""
    links = rbd.section("LINK_DATA").rows
    incoming = [link for link in links
                if link.get("ToType") == kind and link.get("ToID") == elem_id]
    outgoing = [link for link in links
                if link.get("FromType") == kind and link.get("FromID") == elem_id]
    if incoming and outgoing:
        from raptor.core.fileio.rbdlib import make_link_row as _linkrow
        lid = next_id(rbd, "LINK_DATA")
        links.append(_linkrow(
            lid,
            incoming[0]["FromType"], incoming[0]["FromID"],
            outgoing[0]["ToType"], outgoing[0]["ToID"],
        ))


# ---------------------------------------------------------------------------
# configure_sparing / configure_maintenance / configure_phases /
# configure_system
# ---------------------------------------------------------------------------

_SPARING_MODE_TO_FILE = {
    "infinite": "Infinite",
    "custom": "Custom",
    "pool": "Pooled",
    "pooled": "Pooled",
    "sparepool": "Pooled",
    "spare_pool": "Pooled",
    "resource": "Resource",
    "coldpool": "ColdPool",
    "cold_pool": "ColdPool",
    "none": "None",
}


def configure_sparing(
    workspace: Workspace,
    model_id: str,
    selector: str,
    *,
    mode: str = "infinite",
    spare_pool: str | None = None,
    stock: int | None = None,
    rso: int | None = None,
    new_spares: int | None = None,
    arrive: float | None = None,
    restock_every: float | None = None,
    emergency: float | None = None,
    emertime: float | None = None,
    slo: int | None = None,
    eso: int | None = None,
    level: int | None = None,
    quantity: int | None = None,
    slo_time: float | None = None,
    pool_name: str | None = None,
    **kwargs,
) -> dict:
    """Configure sparing for a block.

    ``mode`` is ``infinite``/``custom``/``pool`` (mapped to the real
    on-disk ``SpType`` strings).  ``spare_pool`` is accepted as the MCP
    tool's mode argument; if it is not a known mode it is treated as a
    pool name.  Emergency/slo values are stored on the block."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    section, row = resolve_element(rbd, selector)
    if section != "BLOCK_DATA":
        raise BuildError(f"{selector!r} is not a block")

    pool = pool_name
    if spare_pool is not None:
        low = spare_pool.strip().lower()
        if low in _SPARING_MODE_TO_FILE:
            mode = low
        else:
            pool = spare_pool

    updates: dict[str, Any] = {
        "SpType": _SPARING_MODE_TO_FILE.get(mode.strip().lower(), "Infinite"),
    }
    if pool is not None:
        updates["SparePoolName"] = pool
    def _count(name: str, value: int) -> int:
        if value < 0:
            raise BuildError(f"{name} must be at least 0")
        return value

    def _interval(name: str, value: float) -> float:
        if not 1e-6 <= value <= 999_999_999.999999:
            raise BuildError(
                f"{name} must be between 0.000001 and 999,999,999.999999")
        return value

    if stock is not None:
        updates["Stock"] = _count("stock", stock)
    if new_spares is not None:
        updates["NewSp"] = _count("new_spares", new_spares)
    restock = arrive if arrive is not None else restock_every
    if restock is not None:
        updates["ArriveEvery"] = _interval("restock_every", restock)
        # An interval with the enabling flag off is silently inert.
        if rso is None:
            updates["RSO"] = 1
    if rso is not None:
        updates["RSO"] = rso
    emer = emertime if emertime is not None else emergency
    if emer is not None:
        updates["EmerTime"] = _interval("emergency", emer)
        if eso is None:
            updates["ESO"] = 1
    if eso is not None:
        updates["ESO"] = eso
    if slo is not None:
        # `SLO` is the enable flag; the reorder point is `Level`.
        updates["SLO"] = 1
        updates["Level"] = _count("slo", slo)
    if level is not None:
        updates["Level"] = _count("level", level)
        if slo is None:
            # Setting a reorder level with the SLO flag off is inert.
            updates["SLO"] = 1
    if quantity is not None:
        updates["Quan"] = _count("quantity", quantity)
    if slo_time is not None:
        updates["SLOTime"] = _interval("slo_time", slo_time)
    updates.update(kwargs)

    _apply_patch(rbd, "BLOCK_DATA", row, "block", updates)
    sync_counts(rbd)
    workspace.save(model_id)
    return row


def configure_maintenance(
    workspace: Workspace,
    model_id: str,
    selector: str,
    *,
    uses_pm: int = 1,
    pm_freq: float | str | None = None,
    pm_dist: int = 19,
    pm_duration_dist: int | None = None,
    pm_params: tuple[float, ...] | list[float] | None = None,
    trigger_name: str | None = None,
    pm_trigger: str | None = None,
    defer_flags: dict[str, bool] | None = None,
    **kwargs,
) -> dict:
    """Configure preventive maintenance for a block.

    Wires ``usesPM``, ``PMFreq``, the PM duration distribution/params,
    the trigger (``pmTrig``/``pmTriggered``) and the defer/flag booleans
    (``defer_flags`` keys: ``mis_defer``, ``req_defer``, ``fail_reset``,
    ``refresh``, ``spare``)."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    section, row = resolve_element(rbd, selector)
    if section != "BLOCK_DATA":
        raise BuildError(f"{selector!r} is not a block")

    trigger = trigger_name if trigger_name is not None else pm_trigger
    if trigger and trigger.strip().lower() not in ("none", ""):
        names = {r.get("TriggerName", "")
                 for r in rbd.section("TRIGGERS").rows}
        if trigger not in names:
            raise BuildError(
                f"trigger {trigger!r} is not defined in the triggers table; "
                f"available: {', '.join(sorted(n for n in names if n)) or 'none'}")
    updates: dict[str, Any] = {
        "usesPM": uses_pm,
        "pmTriggered": 1 if trigger else 0,
        "pmTrig": trigger or "none",
        "Dist": pm_duration_dist if pm_duration_dist is not None else pm_dist,
    }
    if pm_freq is not None:
        updates["PMFreq"] = pm_freq
    if pm_params is not None:
        updates["pm_params"] = list(pm_params)
    if defer_flags:
        for flag, value in defer_flags.items():
            col = _PM_FLAG_MAP.get(flag.strip().lower().replace(" ", "_"))
            if col is None:
                raise BuildError(
                    f"unknown defer flag {flag!r} -- use one of "
                    f"{sorted(set(_PM_FLAG_MAP.values()))}")
            updates[col] = value
    updates.update(kwargs)

    _apply_patch(rbd, "BLOCK_DATA", row, "block", updates)
    sync_counts(rbd)
    workspace.save(model_id)
    return row


def _apply_phase_assignment(rbd: RbdFile, section: str, row: dict,
                            values: list, n_phases: int) -> None:
    """Write per-phase cells for one element.  Block/Event cells are
    ``"<state>: <value>"``; Node cells are a single token (``C``/``L`` or
    an integer k).  Columns are added to the row and the section header
    through the builder, never as raw text.

    Every phase needs a real value: a phased golden fixture (Phasing1)
    never has a blank ``P NNN`` cell on an element with ``Phasing=1`` --
    an unphased element simply omits the column instead. Padding a short
    ``values`` list with ``None`` used to write an empty-string cell,
    which the engine's phase parser can't turn into a float and crashes
    on at simulate time with a bare ``could not convert string to float:
    ''``.  Raise here instead, at configure time, with a message that
    says what's missing."""
    values = list(values)
    if len(values) != n_phases:
        raise BuildError(
            f"phase assignment for {section} row {row.get('ID')} needs "
            f"exactly {n_phases} value(s) (one per phase), got "
            f"{len(values)} -- an unfilled phase cell crashes the engine "
            f"at simulate time rather than failing here"
        )
    if any(v is None for v in values):
        raise BuildError(
            f"phase assignment for {section} row {row.get('ID')} has a "
            f"None value -- every phased element needs a state for "
            f"every phase, never a blank cell"
        )
    if section in ("BLOCK_DATA", "EVENT_DATA"):
        cell_cols = [f"P {i:03d}" for i in range(1, n_phases + 1)]
        flag_col = "Phasing"
        default_state = "A"
    elif section == "NODE_DATA":
        cell_cols = [f"P{i:03d}" for i in range(1, n_phases + 1)]
        flag_col = "Phases"
        default_state = None
    else:
        raise BuildError(f"phases cannot be assigned to {section}")

    sec = rbd.section(section)
    if not sec.columns:
        # A spec-built section has no header list yet (the writer derives
        # it from the rows); materialise it first, or appending the P
        # columns below would leave a header holding only those.
        for r in sec.rows:
            for key in r:
                if key not in sec.columns:
                    sec.columns.append(key)
    for col in cell_cols:
        if col not in row:
            row[col] = ""
        if col not in sec.columns:
            sec.columns.append(col)

    if flag_col in row:
        set_fields(row, **{flag_col: 1})

    for col, value in zip(cell_cols, values):
        if isinstance(value, str):
            v = value.strip()
            if section in ("BLOCK_DATA", "EVENT_DATA"):
                # `typed.py:_block_event_phase_columns` unconditionally
                # does ``float(value.partition(":")[2])`` on load -- a
                # bare code with no colon (e.g. "Cut" instead of "Cut:
                # 1.000") partitions to an empty value and crashes the
                # engine identically to an empty cell.  Reject it here,
                # at configure time, with a message that says the fix.
                code, sep, num = v.partition(":")
                if not sep:
                    raise BuildError(
                        f"phase value {value!r} for {section} row "
                        f"{row.get('ID')} needs a \"<code>: <value>\" "
                        f"format (e.g. \"A: 1.000\") -- a bare code with "
                        f"no colon crashes the engine's phase parser the "
                        f"same way a blank cell does"
                    )
                try:
                    float(num.strip())
                except ValueError:
                    raise BuildError(
                        f"phase value {value!r} for {section} row "
                        f"{row.get('ID')} has a non-numeric value after "
                        f"the colon"
                    ) from None
            elif section == "NODE_DATA" and v not in ("C", "L"):
                # `typed.py:_node_phase_column` treats anything that
                # isn't exactly "C" or "L" as ``int(token)``.
                try:
                    int(v)
                except ValueError:
                    raise BuildError(
                        f"phase value {value!r} for NODE_DATA row "
                        f"{row.get('ID')} must be \"C\", \"L\", or an "
                        f"integer k-of-n threshold"
                    ) from None
            row[col] = v
        elif isinstance(value, (list, tuple)) and len(value) == 2:
            code, val = value
            if section == "NODE_DATA":
                row[col] = str(code)
            else:
                row[col] = f"{code}: {float(val):.3f}"
        elif section == "NODE_DATA":
            row[col] = str(value)
        else:
            row[col] = f"{default_state}: {float(value):.3f}"


def _refuse_stale_phase_tokens(rbd: RbdFile, new_count: int, assignments: dict | None) -> None:
    """Changing the number of phases leaves every already-phased element with the wrong number of tokens unless it
    is reassigned in the same call, and Raptor 7 crashes reading such a file. Refuse before changing anything;
    `raptor.core.fileio.phase_edit` inserts and deletes phases while carrying the elements' cells with them."""
    from raptor.core.fileio.phase_edit import phase_count
    if new_count == phase_count(rbd):
        return
    covered = set()
    for selector in (assignments or {}):
        section, row = resolve_element(rbd, selector)
        covered.add((section, row.get("ID")))
    stale = []
    for section, flag in (("BLOCK_DATA", "Phasing"), ("EVENT_DATA", "Phasing"), ("NODE_DATA", "Phases")):
        for r in rbd.section(section).rows:
            if r.get("ID") != "0" and r.get(flag, "") not in ("0", "") and (section, r.get("ID")) not in covered:
                stale.append(r.get("blockName") or r.get("eventName") or r.get("Name") or r.get("ID"))
    if stale:
        raise BuildError(
            f"changing the phase count to {new_count} would leave {len(stale)} phased element(s) with the wrong number "
            f"of per-phase values ({', '.join(map(str, stale[:5]))}{', ...' if len(stale) > 5 else ''}); pass "
            "`assignments` for each of them, or delete phases with configure_phases(mode='remove')")


def configure_phases(
    workspace: Workspace,
    model_id: str,
    phases: list[dict[str, Any]] | None = None,
    *,
    assignments: dict[str, list] | None = None,
    mode: str = "replace",
    remove: list[str] | None = None,
    unassign: list[str] | None = None,
) -> dict:
    """Configure mission phases.

    ``phases`` is ``[{name, duration_dist, params, mission}]`` and is
    always built through ``rbdlib.make_phase_row`` (never hand-written).
    ``assignments`` maps an element name/id to its per-phase states.

    ``mode`` selects how the phase list is applied:

    * ``"replace"`` (default) -- the list becomes the whole phase list; the
      count may not change while elements are phased (pass assignments).
    * ``"add"`` -- the list is appended to the existing phases, so a new
      phase can be introduced without restating the ones already there
      (per-element cells are filled with the Raptor default).
    * ``"remove"`` -- ``remove`` names the phases to delete; the remaining
      phases and every element's tokens shift down as RAPTOR 7 does.

    ``unassign`` names elements whose phasing is cleared entirely (flag
    off and per-phase cells dropped), which previously had no MCP route.
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    from raptor.core.fileio.phase_edit import (delete_phase,
                                               insert_phase, phase_count)
    mode = str(mode or "replace").lower()
    if mode not in ("replace", "add", "remove"):
        raise BuildError(
            f"unknown phase mode {mode!r}; use replace, add or remove")
    phases = list(phases or [])
    if mode != "remove" and not phases and not unassign:
        raise BuildError(f"mode={mode!r} needs at least one phase")
    if mode == "remove" and not remove:
        raise BuildError("mode='remove' needs the phase names in `remove`")
    if len(phases) > MAX_PHASES:
        raise BuildError(
            f"too many phases ({len(phases)}); the limit is {MAX_PHASES}")
    _reject_non_finite(phases, "phases")
    if assignments:
        _reject_non_finite(assignments, "assignments")
        cells = sum(len(v or []) for v in assignments.values())
        if cells > MAX_ASSIGNMENT_CELLS:
            raise BuildError(
                f"phase assignments have too many cells ({cells}); the "
                f"limit is {MAX_ASSIGNMENT_CELLS}")

    removed = 0
    if mode == "remove":
        current = [r.get("PhaseName", "")
                   for r in rbd.section("PHASES").rows]
        for name in remove or []:
            if name not in current:
                raise BuildError(
                    f"no phase named {name!r}; present: "
                    f"{', '.join(current) or 'none'}")
            position = current.index(name) + 1
            delete_phase(rbd, position)
            current.pop(position - 1)
            removed += 1
    elif mode == "add":
        base = phase_count(rbd)
        for i, p in enumerate(phases, 1):
            code = p.get("duration_dist", p.get("dist", 8))
            if isinstance(code, str) and not _is_plain_number(code):
                from raptor.core.fileio.rbds import dist_code
                try:
                    code = dist_code(code)
                except ValueError as exc:
                    raise BuildError(str(exc)) from None
            insert_phase(
                rbd, base + i,
                name=p.get("name", f"Phase{base + i}"),
                dist=int(code),
                params=tuple(p.get("params", (5.0, 0.1, 0.0))),
                mission=bool(int(p.get("mission", 1))),
            )
    elif phases:
        phases_sec = rbd.section("PHASES")
        _refuse_stale_phase_tokens(rbd, len(phases), assignments)
        phases_sec.rows.clear()
        from raptor.core.fileio.rbdlib import make_phase_row
        for i, p in enumerate(phases, 1):
            phases_sec.rows.append(make_phase_row(
                name=p.get("name", f"Phase{i}"),
                num=i,
                mission=p.get("mission", 1),
                dist=p.get("duration_dist", p.get("dist", 8)),
                params=p.get("params", (5.0, 0.1, 0.0)),
            ))

    assigned = 0
    if assignments:
        count = phase_count(rbd)
        for selector, values in assignments.items():
            section, row = resolve_element(rbd, selector)
            _apply_phase_assignment(rbd, section, row, values, count)
            assigned += 1

    unassigned = 0
    for selector in (unassign or []):
        section, row = resolve_element(rbd, selector)
        if section not in ("BLOCK_DATA", "EVENT_DATA", "NODE_DATA"):
            raise BuildError(
                f"{selector!r} cannot carry phase cells; only blocks, "
                f"events and nodes can")
        flag = {"NODE_DATA": "Phases"}.get(section, "Phasing")
        row[flag] = "0"
        for col in list(row):
            if _PHASE_COLUMN_RE.match(col):
                row.pop(col, None)
        unassigned += 1

    sync_counts(rbd)
    workspace.save(model_id)
    return {"phases": phase_count(rbd), "assigned": assigned,
            "removed": removed, "unassigned": unassigned}


#: The SYSTEM_DATA columns that are ALSO run-experiment settings. When any of
#: these is patched, the experiment block must be re-derived or the GUI's run
#: panel (which prefers `rbd.experiment`) and the engine (which reads these
#: columns) disagree about what the next run will do. The set is the inverse
#: of `core.run_experiment.to_system_row`, which writes exactly these seven.
_EXPERIMENT_COLUMNS = frozenset({
    "NumRuns", "TerminationType", "TermCondition", "StartStats",
    "weakAnalysis", "costAnalysis", "capacityAnalysis",
})


def configure_system(
    workspace: Workspace,
    model_id: str,
    *,
    run_length: float | None = None,
    num_runs: int | None = None,
    termination: str | None = None,
    term_type: str | None = None,
    start_stats: float | None = None,
    units: str | None = None,
    weak_link: bool | None = None,
    cost: bool | None = None,
    capacity: bool | None = None,
    flow_generated: float | None = None,
    gy_threshold: float | None = None,
    yr_threshold: float | None = None,
    system_streams: str | list | None = None,
    display_unit: str | None = None,
    **kwargs,
) -> dict:
    """Configure system-level settings (using ``rbds._SYSTEM_MAP``).

    Returns ``{applied, previous}`` (plus ``ok`` for compatibility)."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    sys_sec = rbd.section("SYSTEM_DATA")
    if not sys_sec.rows:
        raise BuildError("model has no SYSTEM_DATA row")
    row = sys_sec.rows[0]

    updates: dict[str, Any] = {}
    if run_length is not None:
        updates["TermCondition"] = float(run_length)
    if num_runs is not None:
        updates["NumRuns"] = float(num_runs)   # Raptor 7 writes "50.000000"
    term = termination if termination is not None else term_type
    if term is not None:
        updates["TerminationType"] = term
    if start_stats is not None:
        updates["StartStats"] = float(start_stats)
    if units is not None:
        updates["units"] = units
    if weak_link is not None:
        updates["weakAnalysis"] = weak_link
    if cost is not None:
        updates["costAnalysis"] = cost
    if capacity is not None:
        updates["capacityAnalysis"] = capacity
    if flow_generated is not None:
        updates["flowGenerated"] = int(flow_generated)
    if gy_threshold is not None:
        updates["GYthreshold"] = float(gy_threshold)
    if yr_threshold is not None:
        updates["YRthreshold"] = float(yr_threshold)
    updates.update(kwargs)

    previous = {col: row.get(col) for col in updates}
    _apply_patch(rbd, "SYSTEM_DATA", row, "system", updates)

    # Mirror the run settings into the experiment block as well.
    #
    # Both destinations are written because both are READ. The GUI's run
    # panel prefers `rbd.experiment` whenever it is non-None
    # (`run_experiment_mixin.py:116-124`) and derives one from this row only
    # when it is None; the engine reads the legacy SYSTEM_DATA columns. Writing
    # only the row -- as this function used to -- meant an MCP
    # `configure_system(num_runs=200)` was invisible to the GUI (which showed
    # the experiment's stale value) and the next GUI edit of ANY run setting
    # silently wrote the old value back to NumRuns, reverting the change.
    #
    # The GUI's own comment (`run_experiment_mixin.py:161-168`) names this
    # exact hazard: "the failure mode that had the run count editable in
    # three places". `core.run_experiment` is Qt-free and was built for
    # precisely this; the API simply was not calling it.
    #
    # `to_system_row` is the inverse of what the caller just wrote, so the
    # experiment is rebuilt FROM the patched row rather than assembled from
    # the arguments -- that keeps a partial update (one kwarg) correct
    # instead of resetting the fields it did not mention to defaults.
    if any(k in updates for k in _EXPERIMENT_COLUMNS):
        rbd.experiment = run_experiment.normalise(
            run_experiment.from_system_row(row))

    if system_streams is not None:
        if isinstance(system_streams, (list, tuple)):
            row["SysStreamsArray"] = " ".join(
                str(int(x)) for x in system_streams)
        else:
            row["SysStreamsArray"] = str(system_streams)
    if display_unit is not None:
        # `display_unit` lives on the RbdFile, not SYSTEM_DATA; keep it
        # in `applied`/`previous` so a caller can see the change.
        previous.setdefault("display_unit",
                            getattr(rbd, "display_unit", None))
        rbd.display_unit = display_unit
        updates["display_unit"] = display_unit

    sync_counts(rbd)
    workspace.save(model_id)
    return {"ok": True, "applied": sorted(updates),
            "previous": previous}


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------

def describe(
    workspace: Workspace,
    model_id: str,
    *,
    detail: bool | str = False,
    level: str = "summary",
) -> dict:
    """Describe the model structure.

    ``detail`` accepts a bool (``True`` == full) or the MCP tool's own
    string (``"full"`` / ``"summary"``).  ``detail="full"`` adds the
    existing ``block_names`` / ``node_names`` lists (the latter now also
    carrying each node's ``type`` / ``k`` / ``n`` / ``full_flow`` /
    ``div_flow``), a ``links`` list (endpoints plus the capacity columns
    ``nom_flow`` / ``max_flow`` / ``cap_pri``), and the system capacity
    settings (``capacity_analysis`` / ``flow_generated`` / ``units`` /
    ``term_condition`` / ``num_runs``).  ``link_count`` is always present;
    in summary detail ``links`` itself is the link count.  ``nodes_detail``
    is an alias of the enriched ``node_names``.
    """
    handle = workspace.get(model_id)
    rbd = handle.rbd
    try:
        level_id = int(str(level))
    except (TypeError, ValueError):
        level_id = 0
    if level_id:
        hiers = rbd.section("HIERARCHY_DATA").rows
        if not any(_as_int(h.get("ID")) == level_id for h in hiers):
            ids = sorted(i for i in (_as_int(h.get("ID")) for h in hiers)
                         if i is not None)
            raise BuildError(
                f"hierarchy level {level_id} not found; available ids: "
                f"{', '.join(str(i) for i in ids) or 'none'}")

    def at_level(rows, col):
        if not level_id:
            return rows
        return [r for r in rows if _as_int(r.get(col)) == level_id]

    full = detail is True or (
        isinstance(detail, str)
        and detail.strip().lower() in ("full", "true", "1", "yes"))

    result: dict[str, Any] = {
        "name": handle.name,
        "model_id": model_id,
    }

    blocks = at_level(rbd.section("BLOCK_DATA").rows, "ParentId")
    real_blocks = [b for b in blocks if b.get("ID") != "0"]
    result["blocks"] = len(real_blocks)
    result["nodes"] = len(at_level(rbd.section("NODE_DATA").rows, "parent"))
    result["events"] = len(at_level(rbd.section("EVENT_DATA").rows, "ParentId"))
    result["link_count"] = len(at_level(rbd.section("LINK_DATA").rows, "pID"))
    result["links"] = result["link_count"]

    if full:
        result["block_names"] = [
            {"id": b.get("ID"), "name": b.get("blockName", "")}
            for b in real_blocks
        ]
        result["event_names"] = [
            {"id": e.get("ID"), "name": e.get("eventName", "")}
            for e in at_level(rbd.section("EVENT_DATA").rows, "ParentId")
            if e.get("ID") != "0"
        ]
        # Sub-hierarchies used to be invisible here, which left two dead
        # ends: `add_block(parent=...)` takes a hierarchy id you had no way
        # to discover, and `render_diagram`'s "read the hierarchy ids from
        # describe_model" hint pointed at an output that never listed them.
        result["hierarchies"] = [
            {
                "id": h.get("ID"),
                "name": h.get("hierarchyName", ""),
                "parent": _as_int(h.get("Parent")),
                "in_id": _as_int(h.get("in")),
                "out_id": _as_int(h.get("out")),
                "selector": f"Hier:{h.get('ID')}",
            }
            for h in at_level(rbd.section("HIERARCHY_DATA").rows, "Parent")
        ]
        result["node_names"] = [
            {
                "id": n.get("ID"),
                "name": n.get("Name", ""),
                "type": _as_int(n.get("Type")),
                "k": _as_int(n.get("k")),
                "n": _as_int(n.get("n")),
                "full_flow": _as_int(n.get("fullflow")),
                "div_flow": _as_int(n.get("divFlow")),
            }
            for n in at_level(rbd.section("NODE_DATA").rows, "parent")
        ]
        # Alias the same enriched list under the key the MCP tool's
        # markdown renderer looks for, so `describe_model` can print the
        # node table without a second call.
        result["nodes_detail"] = result["node_names"]
        result["links"] = [
            {
                "id": link.get("ID"),
                "from_type": link.get("FromType", ""),
                "from_id": _as_int(link.get("FromID")),
                "to_type": link.get("ToType", ""),
                "to_id": _as_int(link.get("ToID")),
                "nom_flow": _as_int(link.get("nomFlow")),
                "max_flow": _as_int(link.get("MaxFlow")),
                "cap_pri": _as_int(link.get("CapPri")),
            }
            for link in at_level(rbd.section("LINK_DATA").rows, "pID")
        ]
        sys_rows = rbd.section("SYSTEM_DATA").rows
        sys_row = sys_rows[0] if sys_rows else {}
        result["system"] = {
            "capacity_analysis": _as_bool(sys_row.get("capacityAnalysis")),
            "flow_generated": _as_int(sys_row.get("flowGenerated")),
            "units": sys_row.get("units", ""),
            "term_condition": _as_float(sys_row.get("TermCondition")),
            "num_runs": _as_int(sys_row.get("NumRuns")),
        }

    return result


def _as_int(value: Any) -> int | None:
    """Legacy rows store ints as strings; return a clean int (or None)."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    """The legacy boolean convention: ``1``/``T``/``TRUE`` are true."""
    return str(value).strip().upper() in ("1", "T", "TRUE", "YES")

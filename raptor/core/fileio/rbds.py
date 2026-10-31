"""`.rbds` — RAPTOR Studio JSON model format (see docs/RBDS_FORMAT.md).

Design in one paragraph: a `.rbds` file is ONE JSON document. Each element
object carries its common settings as friendly snake_case fields and every
other Raptor 7 column under `"legacy"`; **each value is stored once**. The
run settings (trials, termination, statistics start, analyses) live only in
`"experiment"`. Reading starts from defaults, applies `legacy`, then the
friendly fields, so either layer can be edited by hand. Unknown keys are
preserved verbatim for extensibility. Values that follow from the rest of
the model (link counts, parameter counts, rate values...) are never stored;
the reader computes them (`_derive`).
"""
from __future__ import annotations

import json
import math
from typing import Any

from raptor.core.fileio.rbd import RbdFile, RbdSection
from raptor.core.run_experiment import from_system_row, normalise

FORMAT_ID = "raptor-studio"
VERSION = 1

# ---------------------------------------------------------------------------
# friendly -> legacy column maps (the documented surface)
# ---------------------------------------------------------------------------

#: friendly key -> legacy column (or a column triple for a parameter list).
ColumnMap = dict[str, str | tuple[str, ...]]

_BLOCK_MAP: ColumnMap = {
    "id": "ID", "name": "blockName", "parent": "ParentId",
    "x": "xPos", "y": "yPos",
    "fail_dist": "FDist", "fail_params": ("FParam1", "FParam2", "FParam3"),
    "fail_stream": "FStream",
    "repair_dist": "RDist", "repair_params": ("RParam1", "RParam2",
                                              "RParam3"),
    "repair_stream": "RStream",
    "uses_phasing": "Phasing",
    "dep_type": "DepType", "dep_num": "DepNum",
    "dep_nothing": "DepNothing", "dep_idle": "DepIdle",
    "dep_pm": "DepPM", "dep_fail": "DepFail",
}
_NODE_MAP: ColumnMap = {
    "id": "ID", "name": "Name", "type": "Type", "parent": "parent",
    "x": "xPos", "y": "yPos", "k": "k",
    "full_flow": "fullflow", "div_flow": "divFlow",
}
_EVENT_MAP: ColumnMap = {
    "id": "ID", "name": "eventName", "parent": "ParentId",
    "x": "xPos", "y": "yPos", "prob": "failVal",
}
_HIER_MAP: ColumnMap = {
    "id": "ID", "name": "hierarchyName", "parent": "Parent",
    "in_id": "in", "out_id": "out", "x": "xPos", "y": "yPos",
}
#: `from` / `to` are handled separately (two columns each).
_LINK_MAP: ColumnMap = {
    "id": "ID", "parent": "pID",
    "cold_pri": "ColdPri", "a_prob": "AProb", "auto_time": "AutoTime",
    "man_time": "ManTime", "cap_pri": "CapPri",
    "nom_flow": "nomFlow", "max_flow": "MaxFlow",
}
_LINK_ENDPOINT_COLS = ("FromType", "FromID", "ToType", "ToID")

#: The API's friendly names for SYSTEM_DATA (`api.build`, `raptor set`).
#: The file itself writes the run settings in `experiment`, not here.
_SYSTEM_MAP = {
    "units": "units",
    "termination_type": "TerminationType",
    "term_condition": "TermCondition",
    "start_stats": "StartStats",
    "num_runs": "NumRuns",
    "graphics": "graphicsUse",
    "weak_analysis": "weakAnalysis",
    "cost_analysis": "costAnalysis",
    "capacity_analysis": "capacityAnalysis",
    "node_anal_type": "nodeAnalType",
    "gy_threshold": "GYthreshold",
    "yr_threshold": "YRthreshold",
    "red_cost": "systemRedCost",
    "lost_cost": "sysLostCost",
}
#: SYSTEM_DATA columns owned by `experiment` (see `run_experiment`).
_RUN_COLUMNS = ("NumRuns", "TerminationType", "TermCondition", "StartStats",
                "weakAnalysis", "costAnalysis", "capacityAnalysis")
#: What the file's `system` object carries.
_SYSTEM_FILE_MAP: ColumnMap = {
    fk: col for fk, col in _SYSTEM_MAP.items() if col not in _RUN_COLUMNS}
_SYSTEM_FILE_MAP["flow_generated"] = "flowGenerated"

_SECTION_KEY = {          # json key -> RbdFile section name
    "blocks": "BLOCK_DATA",
    "nodes": "NODE_DATA",
    "events": "EVENT_DATA",
    "hierarchies": "HIERARCHY_DATA",
    "links": "LINK_DATA",
    "spare_pools": "SPARE_POOL_DATA",
    "resources": "RESOURCE_DATA",
    "triggers": "TRIGGERS",
    "phases": "PHASES",
}
_ELEMENT_KEYS = ("blocks", "nodes", "events", "hierarchies")

#: Columns the reader computes, so the file never stores them (a stored
#: copy could only disagree after a hand edit): link counts from the links,
#: parameter counts from the distribution. Not for the ID-0 defaults rows.
#: A link's `pID` is computed too where its two ends share a level.
_DERIVED = {
    "NODE_DATA": ("n", "Out"),
    "BLOCK_DATA": ("In", "Out", "#FParam", "#RParam"),
    "EVENT_DATA": ("In", "Out"),
    # `level` is a hierarchy's nesting depth, computed from `Parent`
    # (`_derive`). A stored value goes stale when a hierarchy is moved
    # under another one; exporting the stale value crashes Raptor 7 on
    # Simulate (see `_derive`'s own comment).
    "HIERARCHY_DATA": ("level",),
}
_EMPIRICAL = 16       # its parameter count is its number of points: kept
_KEYED_SECTIONS = ("SPARE_POOL_DATA", "RESOURCE_DATA", "TRIGGERS", "PHASES")

#: Default LINK row for hand-authored files whose base template has no
#: links (a brand-new model ships zero). Values match every fixture's
#: plain-series conventions.
DEFAULT_LINK_ROW = {
    "ID": "0", "FromType": "Node", "FromID": "0",
    "ToType": "Block", "ToID": "0", "pID": "0",
    "ColdPri": "50", "AProb": "1.000000", "AutoTime": "0.000000",
    "ManTime": "0.000000", "CapPri": "50",
    "nomFlow": "-2", "MaxFlow": "-1",
}

#: Friendly names for the four keyed auxiliary sections.  They are written
#: keyed (`{"name": ..., "stock": ...}`) rather than as raw columns, so the
#: same names must work on read, on write and in `update_element`.
_POOL_MAP: ColumnMap = {
    "name": "PoolName", "stock": "Stock", "new_spares": "NewSp",
    "arrive_every": "ArriveEvery", "eso": "ESO", "emer_time": "EmerTime",
    "slo": "SLO", "level": "Level", "quantity": "Quan",
    "slo_time": "SLOTime", "rso": "RSO", "spare_cost": "SpCost",
    "emer_ship_cost": "emerShipCost",
}
_RESOURCE_MAP: ColumnMap = {
    "name": "PoolName", "num_in": "NumIn", "spare_cost": "SpareCost",
    "fixed_per_use": "FixedPerUse", "per_time_unit": "PerTimeUnit",
    "phases": "Phases",
}
_TRIGGER_MAP: ColumnMap = {
    "name": "TriggerName", "repeats": "Repeats", "init_used": "InitUsed",
    "dist": "Dist", "params": ("Param1", "Param2", "Param3"),
}
_PHASE_MAP: ColumnMap = {
    "name": "PhaseName", "num": "Num", "mission": "Mission",
    "duration_dist": "Dist", "params": ("Param1", "Param2", "Param3"),
}

_MAPS: dict[str, ColumnMap] = {
    "BLOCK_DATA": _BLOCK_MAP,
    "NODE_DATA": _NODE_MAP,
    "EVENT_DATA": _EVENT_MAP,
    "HIERARCHY_DATA": _HIER_MAP,
    "LINK_DATA": _LINK_MAP,
    "SYSTEM_DATA": _SYSTEM_FILE_MAP,
    "SPARE_POOL_DATA": _POOL_MAP,
    "RESOURCE_DATA": _RESOURCE_MAP,
    "TRIGGERS": _TRIGGER_MAP,
    "PHASES": _PHASE_MAP,
}

# -- value types of the friendly fields ------------------------------------

_INT_KEYS = frozenset({
    "id", "parent", "type", "k", "n", "out", "in_id", "out_id",
    "fail_dist", "repair_dist", "fail_stream", "repair_stream", "dep_num",
    "full_flow", "div_flow", "cold_pri", "cap_pri", "nom_flow", "max_flow",
    "flow_generated", "num_runs", "from_id", "to_id",
    # Spare-pool columns the .rbd writer also emits as whole numbers
    # (rbd._LEGACY_INT_COLUMNS). ESO/RSO/SLO are flags: "1.000000" is not
    # one of the spellings typed.cell_truthy reads as set.
    "eso", "rso", "slo", "stock", "new_spares", "level", "quantity"})
_BOOL_KEYS = frozenset({"uses_phasing", "weak_analysis", "cost_analysis",
                        "capacity_analysis"})
_STR_KEYS = frozenset({"name", "dep_type", "units", "graphics",
                       "node_anal_type", "termination_type", "from_type",
                       "to_type"})
_DIST_KEYS = frozenset({"fail_dist", "repair_dist"})
_TRUE = ("1", "T", "TRUE")
_FALSE = ("0", "F", "FALSE")


def dist_code(value: Any) -> Any:
    """A distribution given by name ("Weibull", "uniform real") -> its code.
    Numbers pass through."""
    if not isinstance(value, str) or _is_number(value):
        return value
    from raptor.core.rng import Distribution
    key = value.strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {"PEARSON_5": "PEARSON5", "PEARSON_6": "PEARSON6",
               "PEARSON_V": "PEARSON5", "PEARSON_VI": "PEARSON6",
               "UNIFORM_INT": "UNIFORM_INTEGER", "UNIFORM": "UNIFORM_REAL",
               "CHISQUARE": "CHI_SQUARE", "POINT_ESTIMATE": "BERNOULLI",
               "EXP_POWER": "EXPONENTIAL_POWER"}
    try:
        return int(Distribution[aliases.get(key, key)])
    except KeyError:
        raise ValueError(f"unknown distribution {value!r}") from None


def _is_number(s: Any) -> bool:
    try:
        float(s)
    except (TypeError, ValueError):
        return False
    return True


def _from_cell(key: str, text: str) -> Any:
    """A file cell -> the friendly field's JSON value; raises ValueError
    when the cell does not hold that type (it then stays in `legacy`)."""
    if key in _BOOL_KEYS:
        t = str(text).strip().upper()
        if t in _TRUE:
            return True
        if t in _FALSE:
            return False
        raise ValueError(text)
    if key in _STR_KEYS:
        return str(text)
    f = float(text)
    if key in _INT_KEYS:
        if not f.is_integer():
            raise ValueError(text)
        return int(f)
    if not math.isfinite(f):
        raise ValueError(text)
    return f


def _same(cell: str | None, key: str, value: Any) -> bool:
    """Does `cell` already hold `value`? Numerically for numbers, so
    "8760" and "8760.000000" agree."""
    if cell is None:
        return False
    if key in _BOOL_KEYS:
        t = str(cell).strip().upper()
        return (t in _TRUE) == bool(value) and (t in _TRUE or t in _FALSE)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return math.isclose(float(cell), float(value),
                                rel_tol=1e-12, abs_tol=1e-12)
        except (TypeError, ValueError):
            return False
    return str(cell) == str(value)


def _to_cell(old: str | None, key: str, value: Any) -> str:
    """Format `value` for a column, in the style the column already uses
    (so a model edited through the friendly layer still writes the `.rbd`
    cells Raptor 7 writes)."""
    if key in _BOOL_KEYS or isinstance(value, bool):
        tf = old is not None and str(old).strip().upper() in ("T", "F")
        return ("T" if value else "F") if tf else ("1" if value else "0")
    if key in _STR_KEYS or isinstance(value, str):
        return str(value)
    v = float(value)
    if key in _INT_KEYS and key != "num_runs":
        # Every integer-friendly key except num_runs maps to a column the
        # legacy reader takes with ReadInt, so it must be a whole-number
        # token. Formatting a fractional value ("133.760000") produced a
        # .rbd that crashes Raptor 7 on open -- ReadInt stops at the point
        # and shifts every later token. Validation refuses a fractional
        # capacity flow on entry (core.input_rules); this is the formatter
        # backstop for any other path. Truncation matches typed.row_int and
        # the engine. num_runs is ReadReal on disk (real files write
        # "10.000000"), hence the exclusion.
        return str(int(v))
    if old is not None and _is_number(old):
        places = len(old.split(".", 1)[1]) if "." in old else 0
        if places == 0 and v.is_integer():
            return str(int(v))
        return f"{v:.{places or 6}f}"
    if key in _INT_KEYS and v.is_integer():
        return str(int(v))
    return f"{v:.6f}"


def _put(row: dict, col: str, key: str, value: Any) -> None:
    """Set one column from a friendly value, unless it already holds it."""
    if not _same(row.get(col), key, value):
        row[col] = _to_cell(row.get(col), key, value)


def _apply_friendly(row: dict, obj: dict, field_map: ColumnMap) -> None:
    for fk, col in field_map.items():
        if fk not in obj:
            continue
        value = obj[fk]
        if fk in _DIST_KEYS:
            value = dist_code(value)
        if isinstance(col, tuple):            # parameter triple
            vals = (list(value) + [0, 0, 0])[:3]
            for c, v in zip(col, vals):
                _put(row, c, fk, float(v))
        else:
            _put(row, col, fk, value)


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def _row_id(row: dict) -> int:
    try:
        return int(float(row.get("ID") or row.get("objectNum") or 0))
    except (TypeError, ValueError):
        return 0


def _rebuilt_exactly(cell: str | None, key: str, value: Any,
                     start_cell: str | None) -> bool:
    """Would the `.rbd` writer rebuild this cell verbatim from `value`?

    For integer columns it does NOT reproduce a decimal spelling: the
    legacy reader takes them with `ReadInt`, so `render_rbd` writes `-2`,
    never `-2.000000` (`rbd._LEGACY_INT_COLUMNS`). Comparing such a cell
    numerically keeps the value in the friendly layer alone instead of also
    copying the redundant legacy cell."""
    if key in _INT_KEYS and key != "num_runs" and not isinstance(value, str):
        return _same(cell, key, value)
    return _to_cell(start_cell, key, value) == cell


def _split_row(row: dict, field_map: ColumnMap,
               start: dict | None = None) -> tuple[dict, dict]:
    """(friendly fields, legacy columns) for one row.

    A column goes to the friendly layer; it ALSO stays in `legacy` only
    when the reader would not rebuild its exact text from the value (Raptor
    7 spells the same column "1" in one file and "1.000000" in another), so
    a `.rbd` -> `.rbds` -> `.rbd` round trip is byte-exact. `start` is the
    row the reader starts from. Integer columns compare numerically (see
    `_rebuilt_exactly`) because the writer normalises their spelling."""
    start = start or {}
    friendly: dict[str, Any] = {}
    taken: set[str] = set()
    for fk, col in field_map.items():
        cols = col if isinstance(col, tuple) else (col,)
        if not all(c in row for c in cols):
            continue
        try:
            if isinstance(col, tuple):
                vals = [float(row[c] or 0) for c in col]
                if not all(math.isfinite(v) for v in vals):
                    continue
                friendly[fk] = vals
                pairs = list(zip(cols, vals))
            else:
                friendly[fk] = _from_cell(fk, row[col])
                pairs = [(col, friendly[fk])]
        except (TypeError, ValueError):
            continue                          # stays in legacy, verbatim
        taken.update(c for c, v in pairs
                     if _rebuilt_exactly(row[c], fk, v, start.get(c)))
    legacy = {k: v for k, v in row.items()
              if k not in taken and not k.startswith("_")}
    return friendly, legacy


def _without_derived(row: dict, section: str, inferred_pid: int | None
                     ) -> dict:
    if _row_id(row) == 0:
        return row
    drop = set(_DERIVED.get(section, ()))
    if section == "BLOCK_DATA":
        for dist_col, count_col in (("FDist", "#FParam"),
                                    ("RDist", "#RParam")):
            if str(row.get(dist_col, "")).strip() == str(_EMPIRICAL):
                drop.discard(count_col)
    if section == "LINK_DATA" and inferred_pid is not None and \
            _same(row.get("pID"), "parent", inferred_pid):
        drop.add("pID")
    return {k: v for k, v in row.items() if k not in drop}


def _emit_element(row: dict, section: str,
                  inferred_pid: int | None = None) -> dict:
    field_map = _MAPS.get(section, {})
    row = _without_derived(row, section, inferred_pid)
    friendly, legacy = _split_row(row, field_map, _starts().get(section))
    obj: dict[str, Any] = {}
    if "id" in friendly:
        obj["id"] = friendly.pop("id")
    if section == "LINK_DATA" and all(c in row for c in _LINK_ENDPOINT_COLS):
        try:
            obj["from"] = [str(row["FromType"]).replace("RBD", "", 1),
                           int(float(row["FromID"]))]
            obj["to"] = [str(row["ToType"]).replace("RBD", "", 1),
                         int(float(row["ToID"]))]
            for c in _LINK_ENDPOINT_COLS:
                legacy.pop(c, None)
        except (TypeError, ValueError):
            obj.pop("from", None)
    obj.update(friendly)
    if legacy:
        obj["legacy"] = legacy
    extra = row.get("_rbds_extra")
    if isinstance(extra, dict):
        for k, v in extra.items():
            obj.setdefault(k, v)
    return obj


def _emit_keyed(row: dict) -> dict:
    """Spare pools, resources, triggers, phases: named rows, all columns
    in `legacy`."""
    name = (row.get("PoolName") or row.get("TriggerName")
            or row.get("PhaseName") or next(iter(row.values()), ""))
    obj: dict[str, Any] = {"key": name, "id": _row_id(row),
                           "legacy": {k: v for k, v in row.items()
                                      if not k.startswith("_")}}
    extra = row.get("_rbds_extra")
    if isinstance(extra, dict):
        for k, v in extra.items():
            obj.setdefault(k, v)
    return obj


def _emit_system(rbd: RbdFile) -> tuple[dict, dict]:
    """(system object, experiment). The run settings are written once, in
    the experiment; the SYSTEM_DATA row is their authority in memory
    (it is what the engine reads)."""
    sec = rbd.sections.get("SYSTEM_DATA")
    source_row = dict(sec.rows[0]) if sec and sec.rows else {}
    experiment = normalise(rbd.experiment) if rbd.experiment else from_system_row({})
    if source_row:
        from_row = from_system_row(source_row)
        for k in ("trials", "termination_type", "term_condition",
                  "start_stats", "analyses"):
            experiment[k] = from_row[k]
        experiment = normalise(experiment)
    # `imageString` (Raptor 7's system-status picture) is not stored: this
    # product shows the system colour in the status bar instead (owner
    # decision, 2026-10-09). Reading fills it from the template's default
    # so a `.rbd` written from this file still opens in Raptor 7.
    source_row.pop("imageString", None)
    base = _starts()["SYSTEM_DATA"]
    run, _ = _split_row({c: source_row[c] for c in _RUN_COLUMNS if c in source_row},
                        {fk: c for fk, c in _SYSTEM_MAP.items()
                         if c in _RUN_COLUMNS}, base)
    for fk, col in _SYSTEM_MAP.items():   # spelled as the reader would?
        if col in _RUN_COLUMNS and fk in run and \
                _rebuilt_exactly(source_row.get(col), fk, run[fk], base.get(col)):
            source_row.pop(col)
    system, legacy = _split_row(source_row, _SYSTEM_FILE_MAP, base)
    streams = str(legacy.get("SysStreamsArray", "")).split()
    if streams and all(s.lstrip("-").isdigit() for s in streams):
        system["sys_streams"] = [int(s) for s in streams]
        legacy.pop("SysStreamsArray")
    if legacy:
        system["legacy"] = legacy
    return system, experiment


def render_rbds(rbd: RbdFile, meta: dict | None = None) -> str:
    """Serialize an in-memory model (the same `RbdFile` the legacy
    writer consumes) into .rbds JSON text."""
    from raptor import __version__

    def rows_of(section: str) -> list[dict]:
        sec = rbd.sections.get(section)
        return sec.rows if sec else []

    # `meta.file_name` is the model's name where the caller has one (a
    # workspace stores models under its own file names); without it the
    # reader uses the file's own name.
    doc: dict[str, Any] = {"format": FORMAT_ID, "version": VERSION,
                           "meta": {"generator": f"RAPTOR Studio {__version__}",
                                    **(meta or {})}}
    system, experiment = _emit_system(rbd)
    doc["system"] = system
    doc["experiment"] = experiment

    # The ID-0 rows are the defaults a new element starts from (Raptor 7's
    # file keeps them as a hidden first row), not elements.
    defaults: dict[str, Any] = {}
    elements: dict[str, list] = {}
    parents = _parents_of(rbd.sections)
    for jkey in _ELEMENT_KEYS + ("links",):
        section = _SECTION_KEY[jkey]
        out = []
        for i, row in enumerate(rows_of(section)):
            obj = _emit_element(row, section,
                                _shared_parent(row, parents)
                                if jkey == "links" else None)
            if _row_id(row) == 0 and jkey != "links":
                if i:              # where Raptor 7 keeps it varies; keep it
                    obj["at"] = i
                defaults[jkey] = obj
            else:
                out.append(obj)
        if jkey == "links":
            doc["links"] = out
        else:
            elements[jkey] = out
    doc["elements"] = elements
    if defaults:
        doc["defaults"] = defaults
    for jkey in ("spare_pools", "resources", "triggers", "phases"):
        doc[jkey] = [_emit_keyed(r) for r in rows_of(_SECTION_KEY[jkey])]

    if getattr(rbd, "annotations", None):
        anns = []
        for src in rbd.annotations:
            out_a = {k: v for k, v in src.items() if k != "_extra"}
            extra = src.get("_extra")
            if isinstance(extra, dict):     # preserve unknown keys verbatim
                for k2, v2 in extra.items():
                    out_a.setdefault(k2, v2)
            anns.append(out_a)
        doc["annotations"] = anns
    # A rate's value follows from its expression, and a linked field's
    # value is the field itself: neither is stored (the reader computes
    # them), so editing an expression by hand cannot leave a stale number.
    if getattr(rbd, "rates", None):
        doc["rates"] = [{k: v for k, v in r.items()
                         if not (k == "value" and r.get("expr") not in
                                 (None, ""))} for r in rbd.rates]
    if getattr(rbd, "formulas", None):
        doc["formulas"] = {k: {kk: vv for kk, vv in v.items()
                               if kk != "value"}
                           for k, v in rbd.formulas.items()}
    if getattr(rbd, "display_unit", None):
        doc["display_unit"] = rbd.display_unit
    if getattr(rbd, "project_meta", None):
        doc["project_meta"] = dict(rbd.project_meta or {})
    if rbd.view_transform:
        doc["view_transform"] = dict(rbd.view_transform)

    comment_section = rbd.sections.get("COMMENTS")
    doc["comments"] = [
        {"type": r.get("Type", ""), "id": r.get("ID", ""),
         "name": r.get("Name", ""), "text": r.get("Comment", "")}
        for r in (comment_section.rows if comment_section else [])
    ]
    return json.dumps(doc, indent=1, ensure_ascii=False)


def save_rbds(rbd: RbdFile, path: str, meta: dict | None = None) -> None:
    # Keep LINK_DATA's owning-window column (pID) consistent with the
    # endpoints' shared parent on every save — Raptor 7 trusts pID
    # for which window draws a link, our GUI draws by topology, so a
    # stale pID is invisible here but breaks connections in the legacy
    # app. Unconditional, cheap, zero rows changed on consistent models —
    # see legacy_layout.repair_link_parents.
    from raptor.core.fileio.legacy_layout import repair_link_parents
    repair_link_parents(rbd)
    # Saving never moves an element. It used to re-lay out a model whose
    # spacing was finer than Raptor 7's grid, which moved hand-placed
    # figures and left scene-positioned annotations behind; a crowded
    # layout is now a validation warning, and only an explicit Auto Layout
    # changes positions (`raptor.api.validate.layout_warning`).
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_rbds(rbd, meta))


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

def _base_model() -> RbdFile:
    from raptor.core.fileio.rbd import parse_rbd
    from raptor.gui.new_model import NEW_MODEL_TEXT  # template source
    return parse_rbd(NEW_MODEL_TEXT.replace(
        "NewModel.rbd        Thu Jan 01 00:00:00 2026",
        "model.rbds         Thu Jan 01 00:00:00 2026"))


_STARTS: dict[str, dict] | None = None


def _starts() -> dict[str, dict]:
    """The rows a read starts from (cached; callers copy, never mutate)."""
    global _STARTS
    if _STARTS is None:
        base = _base_model()
        _STARTS = _default_rows(base)
        _STARTS["SYSTEM_DATA"] = dict(base.sections["SYSTEM_DATA"].rows[0])
    return _STARTS


def _default_rows(base: RbdFile) -> dict[str, dict]:
    """The row a hand-written element starts from, per section: the blank
    model's own template where it has one, else the builder's defaults."""
    from raptor.core.fileio import rbdlib
    blocks = [r for r in base.sections["BLOCK_DATA"].rows
              if str(r.get("ID", "0")) in ("0", "")]
    return {
        "BLOCK_DATA": dict(blocks[0]) if blocks else rbdlib.make_block_row(0),
        "NODE_DATA": rbdlib.make_node_row(0, "", 0.0, 0.0),
        "EVENT_DATA": rbdlib.make_event_row(0, "", 0.0, 0.0),
        "HIERARCHY_DATA": rbdlib.make_hierarchy_row(0, "", 0.0, 0.0),
        "LINK_DATA": dict(DEFAULT_LINK_ROW),
    }


def _build_row(obj: dict, section: str, start: dict | None) -> dict:
    """defaults <- legacy <- friendly (a friendly value only where it
    differs from what is already there)."""
    legacy = obj.get("legacy") or {}
    row = {**(start or {}), **legacy}
    _apply_friendly(row, obj, _MAPS.get(section, {}))
    if section == "LINK_DATA" and "from" in obj and "to" in obj:
        ft, fi = obj["from"]
        tt, ti = obj["to"]
        _put(row, "FromType", "from_type", str(ft).replace("RBD", "", 1))
        _put(row, "FromID", "from_id", int(fi))
        _put(row, "ToType", "to_type", str(tt).replace("RBD", "", 1))
        _put(row, "ToID", "to_id", int(ti))
    known = ({"id", "legacy", "from", "to", "key", "at"}
             | set(_MAPS.get(section, {})))
    extras = {k: v for k, v in obj.items() if k not in known}
    if extras:
        row["_rbds_extra"] = extras
    return row


_PARENT_COL = {"BLOCK_DATA": "ParentId", "NODE_DATA": "parent",
               "EVENT_DATA": "ParentId", "HIERARCHY_DATA": "Parent"}
_KIND_SECTION = {"Block": "BLOCK_DATA", "Node": "NODE_DATA",
                 "Event": "EVENT_DATA", "Hier": "HIERARCHY_DATA"}


def sync_derived(rbd: RbdFile) -> None:
    """Recompute every value `.rbds` never stores (link counts, parameter
    counts) from the model itself. The `.rbd` export calls this: Raptor 7
    trusts those columns, and a node whose `n` disagrees with its links
    crashes it -- after a link added on the canvas, it did."""
    _derive(rbd.sections, [])


def _parents_of(sections: dict) -> dict[tuple[str, str], str]:
    out = {}
    for kind, sec in _KIND_SECTION.items():
        for r in getattr(sections.get(sec), "rows", []):
            out[(kind, str(r.get("ID")))] = str(r.get(_PARENT_COL[sec], "0"))
    return out


def _shared_parent(link: dict, parents: dict) -> int | None:
    """The level both ends of a link sit on, or None when they differ (a
    link into or out of a hierarchy: its level cannot be inferred)."""
    ends = [parents.get((str(link.get(t, "")).replace("RBD", "", 1),
                         str(link.get(i))))
            for t, i in (("FromType", "FromID"), ("ToType", "ToID"))]
    if ends[0] is None or ends[0] != ends[1]:
        return None
    try:
        return int(float(ends[0]))
    except ValueError:
        return None


def _derive(sections: dict, link_pid_missing: list[dict]) -> None:
    """Fill every computed column (see `_DERIVED`) from the model itself."""
    from raptor.core.fileio.rbdlib import dist_param_count
    links = [r for r in getattr(sections.get("LINK_DATA"), "rows", [])]
    n_in: dict[tuple[str, str], int] = {}
    n_out: dict[tuple[str, str], int] = {}
    for ln in links:
        to = (str(ln.get("ToType", "")).replace("RBD", "", 1),
              str(ln.get("ToID")))
        fr = (str(ln.get("FromType", "")).replace("RBD", "", 1),
              str(ln.get("FromID")))
        n_in[to] = n_in.get(to, 0) + 1
        n_out[fr] = n_out.get(fr, 0) + 1
    for sec, kind, cin in (("NODE_DATA", "Node", "n"),
                           ("BLOCK_DATA", "Block", "In"),
                           ("EVENT_DATA", "Event", "In")):
        for row in getattr(sections.get(sec), "rows", []):
            if _row_id(row) == 0:
                continue
            key = (kind, str(row.get("ID")))
            row[cin] = str(n_in.get(key, 0))
            row["Out"] = str(n_out.get(key, 0))
    for row in getattr(sections.get("BLOCK_DATA"), "rows", []):
        if _row_id(row) == 0:
            continue
        for dist_col, count_col in (("FDist", "#FParam"),
                                    ("RDist", "#RParam")):
            try:
                dist = int(float(row.get(dist_col, "")))
            except (TypeError, ValueError):
                continue
            if dist != _EMPIRICAL:
                row[count_col] = str(dist_param_count(dist))
    parents = _parents_of(sections)
    for ln in link_pid_missing:
        pid = _shared_parent(ln, parents)
        ln["pID"] = str(pid if pid is not None else 0)

    # A hierarchy's `level` is its nesting depth (top level = 1). It is not
    # cosmetic: `RunEFPA` walks down with `mamaLevel := hier.level + 1` and
    # `TestFail` decides how far to descend from that number, so a row that
    # claims level 1 while `Parent` nests it three deep makes the walk skip
    # the subtree. The node then ends up with an empty downstream set, the
    # EFPA/NRBD analysis allocates `NEW(array, 1..0)`, and Raptor 7 dies on
    # Simulate with "MODSIM Runtime error: Array allocation: high bound is
    # less than low bound" (2026-10-27, the 3,470-block BESS model, whose
    # .rbds carried level=1 on all 217 hierarchies including depth-2 and
    # depth-3 children). Derive it from `Parent` on both read and save; a
    # cycle (invalid, but a hand-edited file can hold one) is capped at 1.
    hier_rows = [r for r in getattr(sections.get("HIERARCHY_DATA"), "rows", [])
                 if _row_id(r) != 0]
    by_id = {str(r.get("ID")): r for r in hier_rows}
    levels: dict[str, int] = {}

    def _level(hid: str, seen: frozenset[str] = frozenset()) -> int:
        if hid in levels:
            return levels[hid]
        row = by_id[hid]
        parent = str(row.get("Parent", "0"))
        if parent in ("0", "", hid) or parent not in by_id or parent in seen:
            levels[hid] = 1
        else:
            levels[hid] = _level(parent, seen | {hid}) + 1
        return levels[hid]

    for row in hier_rows:
        row["level"] = str(_level(str(row.get("ID"))))


def _read_system(doc: dict, base: RbdFile) -> tuple[RbdSection, dict]:
    system_section = base.sections["SYSTEM_DATA"]
    source_row = dict(next(iter(system_section.rows)))
    sysd = doc.get("system") or {}
    source_row.update(sysd.get("legacy") or {})
    _apply_friendly(source_row, sysd, _SYSTEM_FILE_MAP)
    if "sys_streams" in sysd:
        text = " ".join(str(int(x)) for x in sysd["sys_streams"])
        if str(source_row.get("SysStreamsArray", "")).split() != text.split():
            source_row["SysStreamsArray"] = text
    # The run settings are the experiment's. A file without one (written
    # before it existed) has them in its system row; read them from there,
    # or it would open with one trial and no analyses.
    if "experiment" in doc:
        experiment = normalise(doc.get("experiment"))
        _apply_experiment(source_row, experiment)
    else:
        experiment = from_system_row(source_row)
    return (RbdSection(name="SYSTEM_DATA", columns=system_section.columns, rows=[source_row]),
            experiment)


def _apply_experiment(source_row: dict, experiment: dict) -> None:
    """The experiment's run settings into the SYSTEM_DATA row. A cell that
    already holds the value keeps its spelling; a new one is spelled like
    the column's current cell (Raptor 7 writes "10.000000" trials), so a
    `.rbd` round trip through `.rbds` stays byte-exact."""
    analyses = experiment.get("analyses") or []
    _put(source_row, "NumRuns", "num_runs", int(experiment["trials"]))
    _put(source_row, "TerminationType", "termination_type",
         experiment["termination_type"])
    _put(source_row, "TermCondition", "term_condition", float(experiment["term_condition"]))
    _put(source_row, "StartStats", "start_stats", float(experiment["start_stats"]))
    for name in ("weak", "cost", "capacity"):
        fk = f"{name}_analysis"
        _put(source_row, _SYSTEM_MAP[fk], fk, name in analyses)


def _read_annotations(doc: dict) -> list[dict]:
    known_ann = {"kind", "id", "parent", "x", "y", "w", "h", "text",
                 "border_colour", "border_width", "fill_colour",
                 "text_colour", "z", "font_family", "font_size",
                 "font_bold", "font_italic", "line_style",
                 "x1", "y1", "x2", "y2"}
    cleaned = []
    for a in doc.get("annotations") or []:
        entry = dict(a)
        extras = {k: v for k, v in entry.items() if k not in known_ann}
        if extras:
            entry["_extra"] = extras
        cleaned.append(entry)
    return cleaned


def _read_project_meta(doc: dict) -> dict | None:
    # Print title block. Kept to the three known string fields so a
    # hand-edited file cannot inject anything unexpected into a dict the
    # GUI round-trips verbatim.
    pm = doc.get("project_meta") or {}
    return ({k: str(pm[k]) for k in ("name", "revision", "date")
             if isinstance(pm, dict) and pm.get(k) is not None} or None)


def parse_rbds(text: str, default_name: str = "model.rbds") -> RbdFile:
    """Parse .rbds JSON into the same `RbdFile` structure every other
    subsystem already consumes. `default_name` is the model's name when
    the file does not carry one."""
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get("format") != FORMAT_ID:
        fmt = doc.get("format") if isinstance(doc, dict) else None
        raise ValueError(f"not an {FORMAT_ID} file (format={fmt!r})")
    version = int(doc.get("version", 1))
    if version > VERSION:
        raise ValueError(f".rbds version {version} newer than supported "
                         f"{VERSION}")
    base = _base_model()
    starts = _starts()
    elements = doc.get("elements") or {}
    defaults = doc.get("defaults") or {}
    sections: dict[str, RbdSection] = {}
    pid_missing: list[dict] = []

    for jkey, sec in _SECTION_KEY.items():
        objs = (doc.get("links") if jkey == "links"
                else elements.get(jkey) or doc.get(jkey)) or []
        if jkey in defaults:
            d = defaults[jkey]
            at = max(0, min(int(d.get("at", 0) or 0), len(objs)))
            objs = [*objs[:at], d, *objs[at:]]
        if not objs and jkey not in ("blocks", "nodes", "links"):
            # keep whatever template the base model carries
            sections[sec] = base.sections[sec]
            continue
        start = None if sec in _KEYED_SECTIONS else starts.get(sec)
        rows = [_build_row(o, sec, start) for o in objs]
        if sec == "LINK_DATA":
            pid_missing = [r for o, r in zip(objs, rows)
                           if "parent" not in o
                           and "pID" not in (o.get("legacy") or {})]
        cols = base.sections[sec].columns if sec in base.sections else []
        sections[sec] = RbdSection(name=sec, columns=cols, rows=rows)
    _derive(sections, pid_missing)

    sections["SYSTEM_DATA"], experiment = _read_system(doc, base)

    comments = doc.get("comments")
    if comments is not None:
        comment_section = base.sections.get("COMMENTS")
        sections["COMMENTS"] = RbdSection(
            name="COMMENTS", columns=comment_section.columns if comment_section else [],
            rows=[{"Type": c.get("type", ""), "ID": str(c.get("id", "")),
                   "Name": c.get("name", ""), "Comment": c.get("text", "")}
                  for c in comments])

    merged = dict(base.sections)
    merged.update(sections)
    out = RbdFile(version=base.version, app_name=base.app_name,
                  file_name=(doc.get("meta") or {}).get("file_name")
                  or default_name,
                  timestamp=base.timestamp, counts=dict(base.counts),
                  sections=merged, annotations=_read_annotations(doc),
                  rates=[dict(r) for r in (doc.get("rates") or [])],
                  formulas={k: dict(v) for k, v in
                            (doc.get("formulas") or {}).items()},
                  display_unit=doc.get("display_unit") or None,
                  view_transform=doc.get("view_transform") or None,
                  project_meta=_read_project_meta(doc),
                  experiment=experiment)
    # The header counts and the phase columns are file-format bookkeeping
    # that follow from the rows.
    from raptor.core.fileio.builder import sync_counts
    sync_counts(out)
    if out.rates or out.formulas:
        # Rate values from their expressions, linked fields from their
        # rates: the file stores neither number.
        from raptor.api.rates import propagate_rates
        propagate_rates(out)
    return out


def load_rbds(path: str) -> RbdFile:
    import os
    with open(path, encoding="utf-8") as f:
        return parse_rbds(f.read(), default_name=os.path.basename(path))

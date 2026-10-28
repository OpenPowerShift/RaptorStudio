"""What is different between two models?

``diff_models(a, b)`` reads two in-memory models (``RbdFile``, from a ``.rbd``
or a ``.rbds`` alike -- the rows are the same) and reports their differences in
friendly terms: elements added, removed, renamed or changed (parameters and
settings), connections added, removed or changed, hierarchy moves, k-of-n
changes, and the rest of the model (system settings, rates, spare pools,
resources, triggers, phases, project details).  It runs nothing; it is not the
results comparison (``compare_models``).

Matching.  Elements are matched by name (within a kind; a hierarchy's own
In/Out and a model's Start/End nodes by their type, so every hierarchy's
``in`` node does not collide); what is left is matched by ID, and an ID match
with a different name is reported as *renamed*.  ``match="id"`` matches on ID
alone (the right choice for "this model against its own file on disk").
Connections are compared as edges between matched elements, so a renamed
block's links still match.

Values are compared numerically (``"10"`` equals ``"10.000000"``), and a
distribution is reported once, as text, rather than once per parameter column.

Categories.  Every field change carries a ``category`` and ``ignore`` drops
whole categories.  The default, ``DEFAULT_IGNORE = ("layout", "derived")``, hides
screen positions (``layout``) and the columns the file reader computes itself
rather than reads (``derived``: link counts such as a node's ``n``/``Out`` and a
block's ``In``/``Out``, the ``#FParam``/``#RParam`` counts of a non-Empirical
distribution), neither of which is a user edit.  ``ignore=()`` shows everything.

The result is plain JSON-safe data (see ``SCHEMA``); every list is sorted so the
output can be diffed and tested.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA = "raptor.model-diff/1"

CATEGORIES = (
    "layout", "derived", "identity", "failure", "repair", "streams",
    "sparing", "maintenance", "logistics", "dependency", "cost", "phasing",
    "capacity", "structure", "system", "project", "other",
)
DEFAULT_IGNORE = ("layout", "derived")

#: The groups of a result, in the order they are shown.
GROUPS = ("elements", "links", "structure", "system", "rates", "spare_pools",
          "resources", "triggers", "phases", "project")

# ---------------------------------------------------------------------------
# column -> category
# ---------------------------------------------------------------------------


def _cats(category: str, *cols: str) -> dict[str, str]:
    return {c: category for c in cols}


#: Every legacy column the models carry, by what it is.  A column that is not
#: here is reported under ``other`` (a test fails when a fixture has one).
COLUMN_CATEGORY: dict[str, str] = {
    **_cats("identity", "ID", "blockName", "Name", "eventName",
            "hierarchyName", "ParentId", "parent", "Parent", "FromType",
            "FromID", "ToType", "ToID", "pID"),
    **_cats("layout", "xPos", "yPos", "zoom", "xCenter", "yCenter",
            "xOrigin", "yOrigin", "imageString", "graphTimeSlice",
            "graphYmin", "graphYmax", "zoomVal", "graphicsUse"),
    # computed by the reader (see rbds._DERIVED) or references to other IDs
    **_cats("derived", "In", "Out", "n", "#FParam", "#RParam", "in", "out",
            "level", "myDepth", "isCNd"),
    **_cats("failure", "FDist", "FParam1", "FParam2", "FParam3", "FEmp",
            "SBStress", "GDType", "GDRate", "GDLimit", "Distro", "failVal"),
    **_cats("repair", "RDist", "RParam1", "RParam2", "RParam3", "REmp"),
    **_cats("streams", "FStream", "RStream", "failStream", "SysStreamsArray"),
    **_cats("sparing", "SparePoolName", "SpType", "RSO", "Stock", "NewSp",
            "ArriveEvery", "ESO", "EmerTime", "SLO", "Level", "Quan",
            "SLOTime"),
    **_cats("maintenance", "usesPM", "PMSpare", "PMRefresh", "PMMisDefer",
            "PMFailReset", "PMReqDefer", "PMStaggerTime", "PMFreq",
            "pmTriggered", "pmTrig", "Dist", "Param1#3", "Param2#3",
            "Param3#3"),
    **_cats("logistics", "Pre-LDTDist", "Post-LDTDist", "Param1#1",
            "Param2#1", "Param3#1", "Param1#2", "Param2#2", "Param3#2",
            "NumRes", "#Res1Rep", "#Res1pmRep", "Resource1Name"),
    **_cats("dependency", "DepNum", "DepNothing", "DepIdle", "DepPM",
            "DepFail", "DepPMThresh", "defDepStateIdle", "DepType", "depNum",
            "depType"),
    **_cats("cost", "initial", "Running", "standby", "idle", "repHold",
            "repPerTime", "repFixed", "donePerTime", "doneFixed",
            "pmHoldCost", "pmCost", "pmFixedCost", "perSpare", "emerShipping",
            "addDone", "InitCost", "OpCost", "RepCost", "systemRedCost",
            "sysLostCost"),
    **_cats("phasing", "Phasing", "Phs", "Phases"),
    **_cats("capacity", "divFlow", "fullflow", "repCap", "repNA", "nomFlow",
            "MaxFlow", "CapPri"),
    **_cats("structure", "Type", "k", "k*", "Cold", "Autos", "PR", "ColdPri",
            "AProb", "AutoTime", "ManTime"),
    **_cats("system", "TerminationType", "TermCondition", "StartStats",
            "NumRuns", "units", "weakAnalysis", "costAnalysis",
            "capacityAnalysis", "nodeAnalType", "GYthreshold", "YRthreshold",
            "ShowNegErrMsg", "IgnoreStartFail", "flowGenerated", "NotUsed"),
    **_cats("other", "SimStart", "AmtExhausted"),
}

#: Sections whose every column belongs to one category (their columns reuse
#: names such as ``Dist``/``Param1`` that mean something else on a block).
_SECTION_CATEGORY = {
    "SPARE_POOL_DATA": "sparing", "RESOURCE_DATA": "logistics",
    "TRIGGERS": "maintenance", "PHASES": "phasing",
}
_PATTERN_CATEGORY = (
    (re.compile(r"^P ?\d{3}$"), "phasing"),
    (re.compile(r"^extra(Int|Real)\d+$"), "layout"),
)

#: Columns the reader computes (mirrors ``rbds._DERIVED``) plus a hierarchy's
#: internal bookkeeping; reported, if at all, under ``derived``.
DERIVED_COLUMNS = {
    "NODE_DATA": ("n", "Out"),
    "BLOCK_DATA": ("In", "Out", "#FParam", "#RParam"),
    "EVENT_DATA": ("In", "Out"),
    "HIERARCHY_DATA": ("In", "Out", "in", "out", "level", "myDepth", "isCNd"),
}


def explicit_category(section: str, column: str) -> str | None:
    """The category of a column, or ``None`` when it is not mapped."""
    if section in _SECTION_CATEGORY:
        return _SECTION_CATEGORY[section]
    if column in COLUMN_CATEGORY:
        return COLUMN_CATEGORY[column]
    for pattern, category in _PATTERN_CATEGORY:
        if pattern.match(column):
            return category
    return None


def category_of(section: str, column: str) -> str:
    return explicit_category(section, column) or "other"


# ---------------------------------------------------------------------------
# value comparison and display
# ---------------------------------------------------------------------------

def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return " ".join(str(value).split())


def _float(text: str) -> float | None:
    try:
        f = float(text)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _same(a: Any, b: Any) -> bool:
    """Equal as numbers when both are numbers, else as trimmed text."""
    na, nb = _norm(a), _norm(b)
    fa, fb = _float(na), _float(nb)
    if fa is not None and fb is not None:
        return math.isclose(fa, fb, rel_tol=1e-9, abs_tol=1e-12)
    return na == nb


def _show(value: Any) -> str:
    """A value as the user should read it (``8760.000000`` -> ``8760``)."""
    text = _norm(value)
    f = _float(text)
    if f is not None and re.fullmatch(r"[-+]?\d*\.?\d+([eE][-+]?\d+)?", text):
        return format(f, ".10g")
    return text


def _g(value: float) -> str:
    return format(value, ".6g")


def _inverse_map(section: str) -> dict[str, str]:
    """Legacy column -> friendly field name (``fail_params[0]`` for a triple)."""
    from raptor.api.build import combined_map
    inv: dict[str, str] = {}
    for friendly, target in combined_map(section).items():
        if isinstance(target, tuple):
            for i, col in enumerate(target):
                inv.setdefault(col, f"{friendly}[{i}]")
        else:
            inv.setdefault(target, friendly)
    return inv


def _label(field_name: str) -> str:
    text = field_name.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _change(section: str, column: str, inv: dict[str, str], a: Any,
            b: Any) -> dict[str, Any]:
    name = inv.get(column, column)
    return {"field": name, "label": _label(name),
            "category": category_of(section, column),
            "a": _show(a), "b": _show(b)}


def _ordered_columns(ra: dict, rb: dict) -> list[str]:
    cols = [c for c in ra if not c.startswith("_")]
    cols += [c for c in rb if c not in ra and not c.startswith("_")]
    return cols


def _plain_changes(section: str, ra: dict, rb: dict,
                   skip: Iterable[str] = ()) -> list[dict[str, Any]]:
    """One change per column that differs, minus *skip*."""
    skip = set(skip)
    inv = _inverse_map(section)
    return [_change(section, col, inv, ra.get(col), rb.get(col))
            for col in _ordered_columns(ra, rb)
            if col not in skip and not _same(ra.get(col), rb.get(col))]


# ---------------------------------------------------------------------------
# distributions
# ---------------------------------------------------------------------------

#: (field, label, category, code column, parameter columns, empirical points
#: column, point-count column) for each distribution a block carries.
_DIST_GROUPS = (
    ("failure", "Failure distribution", "failure", "FDist",
     ("FParam1", "FParam2", "FParam3"), "FEmp", "#FParam"),
    ("repair", "Repair distribution", "repair", "RDist",
     ("RParam1", "RParam2", "RParam3"), "REmp", "#RParam"),
    ("logistics_delay_before", "Logistics delay before repair", "logistics",
     "Pre-LDTDist", ("Param1#1", "Param2#1", "Param3#1"), None, None),
    ("logistics_delay_after", "Logistics delay after repair", "logistics",
     "Post-LDTDist", ("Param1#2", "Param2#2", "Param3#2"), None, None),
    ("pm_duration", "PM duration", "maintenance",
     "Dist", ("Param1#3", "Param2#3", "Param3#3"), None, None),
)
_EMPIRICAL = 16


def _dist_state(row: dict, group: tuple) -> tuple[int, list[float], list[float]] | None:
    _f, _l, _c, code_col, param_cols, emp_col, _n = group
    code = _float(_norm(row.get(code_col)))
    if code is None:
        return None
    params = [_float(_norm(row.get(c))) or 0.0 for c in param_cols]
    empirical_points = [x for x in (_float(t) for t in _norm(row.get(emp_col)).split())
           if x is not None] if emp_col else []
    return int(code), params, empirical_points


def _effective(state: tuple[int, list[float], list[float]]) -> tuple:
    """What actually draws numbers: unused parameter slots are ignored."""
    from raptor.api.distributions import PARAM_INFO
    code, params, emp = state
    if code == _EMPIRICAL:
        return (code, tuple(emp))
    n = len(PARAM_INFO[code]["params"]) if code in PARAM_INFO else len(params)
    return (code, tuple(params[:n]))


def _same_effective(a: tuple, b: tuple) -> bool:
    if a[0] != b[0] or len(a[1]) != len(b[1]):
        return False
    return all(math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12)
               for x, y in zip(a[1], b[1]))


def _dist_text(state: tuple[int, list[float], list[float]] | None) -> str:
    if state is None:
        return "(none)"
    from raptor.api.distributions import describe_dist
    code, params, emp = state
    if code == _EMPIRICAL:
        return f"Empirical, {len(emp)} points"
    d = describe_dist(code, params)
    named = d.get("params")
    if isinstance(named, dict):
        inner = ", ".join(f"{k} {_g(v)}" for k, v in named.items())
    else:
        inner = ", ".join(_g(v) for v in (named or []))
    return f"{d['name']} ({inner})" if inner else str(d["name"])


def _raw(row: dict, group: tuple) -> dict[str, str]:
    cols = [group[3], *group[4]] + [c for c in (group[5],) if c]
    return {c: _norm(row.get(c)) for c in cols if c in row}


def _dist_changes(section: str, ra: dict, rb: dict
                  ) -> tuple[list[dict[str, Any]], set[str]]:
    """The distribution changes of a block, and the columns they consumed."""
    if section != "BLOCK_DATA":
        return [], set()
    changes: list[dict[str, Any]] = []
    consumed: set[str] = set()
    for group in _DIST_GROUPS:
        sa, sb = _dist_state(ra, group), _dist_state(rb, group)
        consumed.update([group[3], *group[4]] + [c for c in (group[5],) if c])
        if group[6] and _EMPIRICAL in [s[0] for s in (sa, sb) if s]:
            consumed.add(group[6])
        if sa is None and sb is None:
            continue
        if sa is None or sb is None or not _same_effective(
                _effective(sa), _effective(sb)):
            changes.append({
                "field": group[0], "label": group[1], "category": group[2],
                "a": _dist_text(sa), "b": _dist_text(sb),
                "a_raw": _raw(ra, group), "b_raw": _raw(rb, group)})
    return changes, consumed


# ---------------------------------------------------------------------------
# elements
# ---------------------------------------------------------------------------

#: kind, section, name column, parent column
_KINDS = (
    ("Block", "BLOCK_DATA", "blockName", "ParentId"),
    ("Node", "NODE_DATA", "Name", "parent"),
    ("Event", "EVENT_DATA", "eventName", "ParentId"),
    ("Hierarchy", "HIERARCHY_DATA", "hierarchyName", "Parent"),
)
_SECTION_OF = {k: s for k, s, _n, _p in _KINDS}
_NAME_COL = {k: n for k, _s, n, _p in _KINDS}
_PORT_NODE = {1: "<start>", 3: "<end>", 4: "<in>", 5: "<out>"}
_FORMULA_KIND = {"Block": "block", "Node": "node", "Event": "event",
                 "Hierarchy": "hier"}
_ID_REFERENCES = {"HIERARCHY_DATA": ("in", "out")}
_LINK_SKIP = ("ID", "pID", "FromType", "FromID", "ToType", "ToID")


def _end_kind(type_text: Any) -> str:
    text = str(type_text or "").strip()
    if text.startswith("RBD"):
        text = text[3:]
    return "Hierarchy" if text in ("Hier", "Hierarchy") else text


def _is_zero(row_id: Any) -> bool:
    f = _float(_norm(row_id))
    return f is None or f == 0


@dataclass(eq=False)
class _Elem:
    kind: str
    id: str
    name: str
    nkey: str
    parent: str
    row: dict
    path: tuple[str, ...] = ()

    @property
    def full_path(self) -> str:
        return " / ".join((*self.path, self.name))

    @property
    def container(self) -> str:
        return " / ".join(self.path)


@dataclass
class _Side:
    rbd: Any
    elems: list[_Elem]
    by_id: dict[tuple[str, str], _Elem]
    links: list[dict]
    indeg: Counter
    outdeg: Counter

    def label(self, kind: str, elem_id: str) -> str:
        e = self.by_id.get((kind, elem_id))
        return f"{kind} {e.name if e else '#' + elem_id}"


def _rows(rbd: Any, section: str) -> list[dict]:
    sec = rbd.sections.get(section)
    return list(sec.rows) if sec else []


def _norm_id(value: Any) -> str:
    f = _float(_norm(value))
    return str(int(f)) if f is not None else _norm(value)


def _make_elem(kind: str, name_col: str, parent_col: str, row: dict) -> _Elem:
    name = _norm(row.get(name_col))
    nkey = name
    if kind == "Node":
        t = _float(_norm(row.get("Type")))
        nkey = _PORT_NODE.get(int(t), name) if t is not None else name
    return _Elem(kind, _norm_id(row.get("ID")), name, nkey,
                 _norm_id(row.get(parent_col)), row)


def _assign_paths(elements: list[_Elem], by_id: dict) -> None:
    for e in elements:
        names: list[str] = []
        seen: set[str] = set()
        pid = e.parent
        while pid not in ("0", "") and pid not in seen:
            seen.add(pid)
            h = by_id.get(("Hierarchy", pid))
            if h is None:
                break
            names.append(h.name)
            pid = h.parent
        e.path = tuple(reversed(names))


def _build_side(rbd: Any) -> _Side:
    elements: list[_Elem] = []
    for kind, section, name_col, parent_col in _KINDS:
        elements += [_make_elem(kind, name_col, parent_col, r)
                  for r in _rows(rbd, section) if not _is_zero(r.get("ID"))]
    by_id = {(e.kind, e.id): e for e in elements}
    _assign_paths(elements, by_id)
    links = [r for r in _rows(rbd, "LINK_DATA")]
    indeg: Counter = Counter()
    outdeg: Counter = Counter()
    for r in links:
        outdeg[(_end_kind(r.get("FromType")), _norm_id(r.get("FromID")))] += 1
        indeg[(_end_kind(r.get("ToType")), _norm_id(r.get("ToID")))] += 1
    return _Side(rbd, elements, by_id, links, indeg, outdeg)


def _pass(left_a: list[_Elem], left_b: list[_Elem], keyfn,
          pairs: list[tuple[_Elem, _Elem]]) -> tuple[list[_Elem], list[_Elem]]:
    ga: dict[Any, list[_Elem]] = defaultdict(list)
    gb: dict[Any, list[_Elem]] = defaultdict(list)
    for e in left_a:
        ga[keyfn(e)].append(e)
    for e in left_b:
        gb[keyfn(e)].append(e)
    used: set[int] = set()
    for key, la in ga.items():
        lb = gb.get(key)
        if len(la) == 1 and lb is not None and len(lb) == 1:
            pairs.append((la[0], lb[0]))
            used.update((id(la[0]), id(lb[0])))
    return ([e for e in left_a if id(e) not in used],
            [e for e in left_b if id(e) not in used])


_PASSES = {
    "auto": (lambda e: e.nkey, lambda e: (e.path, e.nkey), lambda e: e.id),
    "name": (lambda e: e.nkey, lambda e: (e.path, e.nkey),
             lambda e: (e.id, e.nkey)),
    "id": (lambda e: e.id,),
}


def _match_kind(ea: list[_Elem], eb: list[_Elem], mode: str
                ) -> tuple[list[tuple[_Elem, _Elem]], list[_Elem], list[_Elem]]:
    pairs: list[tuple[_Elem, _Elem]] = []
    left_a, left_b = ea, eb
    for keyfn in _PASSES[mode]:
        left_a, left_b = _pass(left_a, left_b, keyfn, pairs)
    return pairs, left_a, left_b


def _duplicate_warnings(side: _Side, which: str, mode: str) -> list[str]:
    if mode == "id":
        return []
    counts = Counter((e.kind, e.path, e.nkey) for e in side.elems
                     if not e.nkey.startswith("<"))
    out = []
    for (kind, path, nkey), n in counts.items():
        if n > 1:
            where = f" in {' / '.join(path)}" if path else ""
            out.append(f"duplicate name {nkey!r} ({kind}{where}) in model "
                       f"{which}: matched by id where possible")
    return out


# ---------------------------------------------------------------------------
# the comparison
# ---------------------------------------------------------------------------

@dataclass
class _Diff:
    a: Any
    b: Any
    mode: str
    ignore: frozenset
    sa: _Side = field(init=False)
    sb: _Side = field(init=False)
    pairs: list[tuple[_Elem, _Elem]] = field(default_factory=list)
    only_a: list[_Elem] = field(default_factory=list)
    only_b: list[_Elem] = field(default_factory=list)
    tok: dict[tuple, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.sa, self.sb = _build_side(self.a), _build_side(self.b)
        for kind, *_ in _KINDS:
            ea = [e for e in self.sa.elems if e.kind == kind]
            eb = [e for e in self.sb.elems if e.kind == kind]
            pairs, oa, ob = _match_kind(ea, eb, self.mode)
            self.pairs += pairs
            self.only_a += oa
            self.only_b += ob
        for i, (x, y) in enumerate(self.pairs):
            self.tok[(0, x.kind, x.id)] = i
            self.tok[(1, y.kind, y.id)] = i
        self.warnings = sorted(set(
            _duplicate_warnings(self.sa, "A", self.mode)
            + _duplicate_warnings(self.sb, "B", self.mode)))

    def token(self, side: int, kind: str, elem_id: str) -> tuple:
        i = self.tok.get((side, kind, elem_id))
        return ("pair", i) if i is not None else ("only", side, kind, elem_id)

    def keep(self, changes: list[dict]) -> list[dict]:
        return [c for c in changes if c["category"] not in self.ignore]

    # -- elements ----------------------------------------------------------

    def dependency_change(self, section: str, x: _Elem, y: _Elem
                          ) -> tuple[list[dict], set[str]]:
        num_col, type_col = (("DepNum", "DepType") if section == "BLOCK_DATA"
                             else ("depNum", "depType"))
        if num_col not in x.row and num_col not in y.row:
            return [], set()

        def state(side: int, e: _Elem) -> tuple[tuple, str]:
            num = _float(_norm(e.row.get(num_col))) or 0
            kind = _end_kind(e.row.get(type_col))
            if num == 0 or kind in ("", "None"):
                return ("none",), "(none)"
            if num < 0:
                return ("special", num), ("its own node" if num == -1
                                          else "the whole system")
            sd = self.sa if side == 0 else self.sb
            target = str(int(num))
            if (kind, target) not in sd.by_id:   # not a listed element: raw id
                return ("raw", kind, target), sd.label(kind, target)
            return self.token(side, kind, target), sd.label(kind, target)
        (ka, ta), (kb, tb) = state(0, x), state(1, y)
        if ka == kb:
            return [], {num_col, type_col}
        return ([{"field": "dependency_target", "label": "Follows",
                  "category": "dependency", "a": ta, "b": tb}],
                {num_col, type_col})

    def formula_changes(self, x: _Elem, y: _Elem) -> list[dict]:
        prefix = _FORMULA_KIND[x.kind]
        fa = _formulas_for(self.a, prefix, x.id)
        fb = _formulas_for(self.b, prefix, y.id)
        out = []
        for rest in sorted(set(fa) | set(fb)):
            ta, tb = fa.get(rest, "(none)"), fb.get(rest, "(none)")
            if ta != tb:
                out.append({"field": f"link:{rest}",
                            "label": f"Rate link {rest}",
                            "category": _formula_category(rest),
                            "a": ta, "b": tb})
        return out

    def element_changes(self, x: _Elem, y: _Elem) -> list[dict]:
        section = _SECTION_OF[x.kind]
        skip = {"ID", _NAME_COL[x.kind], *_parent_cols(section),
                *_ID_REFERENCES.get(section, ())}
        dists, consumed = _dist_changes(section, x.row, y.row)
        deps, dep_cols = self.dependency_change(section, x, y)
        plain = _plain_changes(section, x.row, y.row,
                               skip | consumed | dep_cols)
        changes = [*dists, *deps, *plain, *self.formula_changes(x, y)]
        return sorted(self.keep(changes), key=lambda c: (c["field"]))

    def parent_token(self, side: int, e: _Elem) -> tuple | None:
        if e.parent in ("0", ""):
            return None
        return self.token(side, "Hierarchy", e.parent)

    def moved(self, x: _Elem, y: _Elem) -> bool:
        return self.parent_token(0, x) != self.parent_token(1, y)

    def elements(self) -> tuple[dict, list[dict]]:
        added = [_elem_ref(e) for e in self.only_b]
        removed = [_elem_ref(e) for e in self.only_a]
        renamed, changed, structure = [], [], []
        for x, y in self.pairs:
            if x.name != y.name:
                renamed.append({"kind": y.kind, "id": _int(y.id),
                                "from": x.name, "to": y.name})
            changes = self.element_changes(x, y)
            if changes:
                changed.append({**_elem_ref(y), "changes": changes})
            entry = self.structure_entry(x, y)
            if entry:
                structure.append(entry)
        result = {"added": sorted(added, key=_elem_sort),
                  "removed": sorted(removed, key=_elem_sort),
                  "renamed": sorted(renamed, key=lambda r: (r["kind"],
                                                            r["from"])),
                  "changed": sorted(changed, key=_elem_sort)}
        return result, sorted(structure, key=_elem_sort)

    # -- structure ---------------------------------------------------------

    def structure_entry(self, x: _Elem, y: _Elem) -> dict | None:
        changes = []
        if self.moved(x, y):
            changes.append({"field": "hierarchy", "label": "Hierarchy",
                            "category": "identity",
                            "a": x.container or "(top level)",
                            "b": y.container or "(top level)"})
        if x.kind == "Node":
            changes += self.node_structure(x, y)
        changes = self.keep(changes)
        if not changes:
            return None
        return {**_elem_ref(y), "moved": any(c["field"] == "hierarchy"
                                             for c in changes),
                "changes": changes}

    def node_structure(self, x: _Elem, y: _Elem) -> list[dict]:
        ia, ib = self.sa.indeg[("Node", x.id)], self.sb.indeg[("Node", y.id)]
        oa, ob = self.sa.outdeg[("Node", x.id)], self.sb.outdeg[("Node", y.id)]
        out = []
        if _norm(x.row.get("Type")) == "2" and _norm(y.row.get("Type")) == "2":
            # k=-1 is Raptor 7's "never set", which the engine (and Raptor 7's
            # own reader) runs as 1: compare what the node needs, not the
            # stored sentinel.
            ka, kb = (("1" if k == "-1" else k)
                      for k in (_norm_id(x.row.get("k")), _norm_id(y.row.get("k"))))
            if (ka, ia) != (kb, ib):
                out.append({"field": "k_of_n", "label": "Voting",
                            "category": "structure",
                            "a": f"needs {ka} of {ia}",
                            "b": f"needs {kb} of {ib}"})
        elif ia != ib:
            out.append({"field": "inputs", "label": "Inputs",
                        "category": "structure", "a": str(ia), "b": str(ib)})
        if oa != ob:
            out.append({"field": "outputs", "label": "Outputs",
                        "category": "structure", "a": str(oa), "b": str(ob)})
        return out

    # -- links -------------------------------------------------------------

    def edges(self, side: int) -> dict[tuple, list[dict]]:
        sd = self.sa if side == 0 else self.sb
        out: dict[tuple, list[dict]] = defaultdict(list)
        for r in sorted(sd.links, key=lambda r: _float(_norm(r.get("ID"))) or 0):
            fk, tk = (_end_kind(r.get("FromType")), _norm_id(r.get("FromID"))), \
                (_end_kind(r.get("ToType")), _norm_id(r.get("ToID")))
            out[(self.token(side, *fk), self.token(side, *tk))].append(r)
        return out

    def link_ref(self, side: int, row: dict) -> dict[str, str]:
        sd = self.sa if side == 0 else self.sb
        fk = (_end_kind(row.get("FromType")), _norm_id(row.get("FromID")))
        e = sd.by_id.get(fk)
        return {"from": sd.label(*fk),
                "to": sd.label(_end_kind(row.get("ToType")),
                               _norm_id(row.get("ToID"))),
                "in": e.container if e else ""}

    def links(self) -> dict[str, list]:
        ea, eb = self.edges(0), self.edges(1)
        added, removed, changed = [], [], []
        for key in set(ea) | set(eb):
            la, lb = ea.get(key, []), eb.get(key, [])
            n = min(len(la), len(lb))
            added += [self.link_ref(1, r) for r in lb[n:]]
            removed += [self.link_ref(0, r) for r in la[n:]]
            for ra, rb in zip(la[:n], lb[:n]):
                changes = self.keep(_plain_changes("LINK_DATA", ra, rb,
                                                   _LINK_SKIP))
                if changes:
                    changed.append({**self.link_ref(1, rb),
                                    "changes": changes})
        k = lambda d: (d["in"], d["from"], d["to"])  # noqa: E731
        return {"added": sorted(added, key=k), "removed": sorted(removed, key=k),
                "changed": sorted(changed, key=k)}

    # -- everything else ---------------------------------------------------

    def system(self) -> list[dict]:
        ra = (_rows(self.a, "SYSTEM_DATA") or [{}])[0]
        rb = (_rows(self.b, "SYSTEM_DATA") or [{}])[0]
        changes = _plain_changes("SYSTEM_DATA", ra, rb)
        ea, eb = _experiment(self.a, ra), _experiment(self.b, rb)
        for key in sorted(set(ea) | set(eb)):
            if not _same(ea.get(key), eb.get(key)):
                changes.append({"field": key, "label": _label(key),
                                "category": "system",
                                "a": _show(ea.get(key)), "b": _show(eb.get(key))})
        changes += _other_formula_changes(self.a, self.b)
        return sorted(self.keep(changes), key=lambda c: c["field"])

    def rates(self) -> dict[str, list]:
        ra = {_norm(r.get("name")): r for r in (self.a.rates or [])}
        rb = {_norm(r.get("name")): r for r in (self.b.rates or [])}
        changed = []
        for name in sorted(set(ra) & set(rb)):
            changes = [{"field": f, "label": _label(f), "category": "system",
                        "a": _show(ra[name].get(f)), "b": _show(rb[name].get(f))}
                       for f in ("expr", "value", "description")
                       if not _same(ra[name].get(f), rb[name].get(f))]
            if changes:
                changed.append({"name": name, "changes": changes})
        return {"added": sorted(set(rb) - set(ra)),
                "removed": sorted(set(ra) - set(rb)), "changed": changed}

    def keyed(self, section: str, name_col: str) -> dict[str, list]:
        ra = _keyed_rows(self.a, section, name_col)
        rb = _keyed_rows(self.b, section, name_col)
        changed = []
        for name in sorted(set(ra) & set(rb)):
            changes = self.keep(_plain_changes(section, ra[name], rb[name],
                                               (name_col,)))
            if changes:
                changed.append({"name": name, "changes": changes})
        return {"added": sorted(set(rb) - set(ra)),
                "removed": sorted(set(ra) - set(rb)), "changed": changed}

    def project(self) -> list[dict]:
        a, b = self.a, self.b
        pairs: list[tuple[str, str, Any, Any]] = [
            ("display_unit", "project", a.display_unit, b.display_unit),
            ("annotations", "layout", len(a.annotations or []),
             len(b.annotations or [])),
            ("comments", "layout", len(_rows(a, "COMMENTS")),
             len(_rows(b, "COMMENTS"))),
            ("view_transform", "layout", _json(a.view_transform),
             _json(b.view_transform)),
        ]
        ma, mb = a.project_meta or {}, b.project_meta or {}
        pairs += [(f"project_meta.{k}", "project", ma.get(k), mb.get(k))
                  for k in sorted(set(ma) | set(mb))]
        changes = [{"field": f, "label": _label(f), "category": c,
                    "a": _show(x), "b": _show(y)}
                   for f, c, x, y in pairs if not _same(x, y)]
        return self.keep(changes)


# -- helpers for _Diff -------------------------------------------------------

def _int(text: str) -> int | str:
    return int(text) if text.lstrip("-").isdigit() else text


def _elem_ref(e: _Elem) -> dict[str, Any]:
    return {"kind": e.kind, "name": e.name, "path": e.full_path,
            "id": _int(e.id)}


def _elem_sort(d: dict) -> tuple:
    return (d["kind"], d["path"], d["name"])


def _parent_cols(section: str) -> tuple[str, ...]:
    return tuple(p for _k, s, _n, p in _KINDS if s == section)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True) if value else ""


def _formula_text(entry: Any) -> str:
    if isinstance(entry, dict):
        if entry.get("rate"):
            return f"@{entry['rate']}"
        if entry.get("expr") is not None:
            return str(entry["expr"])
    return _norm(entry)


def _formulas_for(rbd: Any, prefix: str, elem_id: str) -> dict[str, str]:
    start = f"{prefix}:{elem_id}:"
    return {k[len(start):]: _formula_text(v)
            for k, v in (rbd.formulas or {}).items() if k.startswith(start)}


_FORMULA_PREFIX_CATEGORY = (
    ("PreLDT", "logistics"), ("PostLDT", "logistics"), ("PM", "maintenance"),
    ("F", "failure"), ("R", "repair"),
)


def _formula_category(rest: str) -> str:
    for prefix, category in _FORMULA_PREFIX_CATEGORY:
        if rest.startswith(prefix):
            return category
    return "other"


def _other_formula_changes(a: Any, b: Any) -> list[dict]:
    known = set(_FORMULA_KIND.values())

    def pick(rbd: Any) -> dict[str, str]:
        return {k: _formula_text(v) for k, v in (rbd.formulas or {}).items()
                if k.split(":", 1)[0] not in known}
    fa, fb = pick(a), pick(b)
    return [{"field": f"link:{k}", "label": f"Rate link {k}",
             "category": "system", "a": fa.get(k, "(none)"),
             "b": fb.get(k, "(none)")}
            for k in sorted(set(fa) | set(fb))
            if fa.get(k) != fb.get(k)]


#: experiment keys that mirror SYSTEM_DATA columns (already compared there)
_MIRRORED = ("trials", "termination_type", "term_condition", "start_stats",
             "analyses")


def _experiment(rbd: Any, system_row: dict) -> dict[str, Any]:
    from raptor.core.run_experiment import from_system_row
    experiment = rbd.experiment or from_system_row(system_row)
    out: dict[str, Any] = {}
    for key, value in experiment.items():
        if key in _MIRRORED:
            continue
        if isinstance(value, dict):
            out.update({f"{key}.{k}": v for k, v in value.items()})
        else:
            out[key] = value
    return out


def _keyed_rows(rbd: Any, section: str, name_col: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in _rows(rbd, section):
        name = _norm(row.get(name_col))
        if not name:
            continue
        base, n = name, 1
        while name in out:
            n += 1
            name = f"{base} ({n})"
        out[name] = row
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

_ELEMENT_KEYS = ("added", "removed", "renamed", "changed")
_KEYED = (("spare_pools", "SPARE_POOL_DATA", "PoolName"),
          ("resources", "RESOURCE_DATA", "PoolName"),
          ("triggers", "TRIGGERS", "TriggerName"),
          ("phases", "PHASES", "PhaseName"))


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _connectivity(s: dict[str, int]) -> str:
    parts = []
    if s["elements_added"]:
        parts.append(_plural(s["elements_added"], "element") + " added")
    if s["elements_removed"]:
        parts.append(_plural(s["elements_removed"], "element") + " removed")
    if s["links_added"]:
        parts.append(_plural(s["links_added"], "link") + " added")
    if s["links_removed"]:
        parts.append(_plural(s["links_removed"], "link") + " removed")
    return ", ".join(parts) or "same wiring"


def _group_count(group: Any) -> int:
    if isinstance(group, list):
        return len(group)
    return sum(len(v) for v in group.values())


def _summary(result: dict) -> dict[str, Any]:
    el, link = result["elements"], result["links"]
    s: dict[str, Any] = {
        "elements_added": len(el["added"]),
        "elements_removed": len(el["removed"]),
        "renamed": len(el["renamed"]),
        "elements_changed": len(el["changed"]),
        "elements_moved": sum(1 for e in result["structure"] if e["moved"]),
        "links_added": len(link["added"]), "links_removed": len(link["removed"]),
        "links_changed": len(link["changed"]),
        "rates_changed": _group_count(result["rates"]),
        "system_changed": len(result["system"]),
    }
    for name, _sec, _col in _KEYED:
        s[f"{name}_changed"] = _group_count(result[name])
    s["project_changed"] = len(result["project"])
    s["connectivity"] = _connectivity(s)
    return s


def has_differences(result: dict) -> bool:
    """True when any group of *result* (as filtered) is non-empty."""
    return any(_group_count(result[g]) for g in GROUPS)


def _check(match: str, ignore: Iterable[str]) -> frozenset:
    if match not in _PASSES:
        raise ValueError(f"match must be one of {sorted(_PASSES)}, "
                         f"not {match!r}")
    ign = frozenset(ignore)
    unknown = sorted(ign - set(CATEGORIES))
    if unknown:
        raise ValueError(f"unknown categor{'y' if len(unknown) == 1 else 'ies'} "
                         f"{', '.join(unknown)}; known: {', '.join(CATEGORIES)}")
    return ign


def _side_info(rbd: Any, label: str, path: str | None) -> dict[str, Any]:
    from raptor.api.run import model_hash
    return {"label": label, "name": getattr(rbd, "file_name", None),
            "path": path, "digest": model_hash(rbd)}


def diff_models(a: Any, b: Any, *,
                match: Literal["auto", "name", "id"] = "auto",
                ignore: Iterable[str] = DEFAULT_IGNORE,
                labels: tuple[str, str] = ("A", "B"),
                paths: tuple[str | None, str | None] = (None, None)) -> dict:
    """Differences between two ``RbdFile`` models (A is the reference, B the
    changed one).  See the module docstring for matching and categories.

    ``match``: "auto" (name, then ID), "name" (name only) or "id" (ID only).
    ``ignore``: categories to leave out; default ``("layout", "derived")``.
    ``labels`` / ``paths``: names for the two sides in the output.
    """
    ign = _check(match, ignore)
    d = _Diff(a, b, match, ign)
    elements, structure = d.elements()
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "a": _side_info(a, labels[0], paths[0]),
        "b": _side_info(b, labels[1], paths[1]),
        "identical": False,
        "ignored": sorted(ign),
        "elements": elements, "links": d.links(), "structure": structure,
        "system": d.system(), "rates": d.rates(),
    }
    for name, section, col in _KEYED:
        result[name] = d.keyed(section, col)
    result["project"] = d.project()
    result["summary"] = _summary(result)
    result["identical"] = not has_differences(result)
    result["warnings"] = d.warnings
    return result


def diff(ws: Any, model_a_id: str, model_b_id: str, **kw: Any) -> dict:
    """``diff_models`` over two models open in a workspace."""
    ha, hb = ws.get(model_a_id), ws.get(model_b_id)
    kw.setdefault("paths", (getattr(ha, "source", None) or None,
                            getattr(hb, "source", None) or None))
    return diff_models(ha.rbd, hb.rbd, **kw)


def restrict(result: dict, groups: Iterable[str]) -> dict:
    """A copy of *result* showing only the named groups (the others emptied);
    ``identical`` is recomputed for what is left."""
    keep = set(groups)
    unknown = sorted(keep - set(GROUPS))
    if unknown:
        raise ValueError(f"unknown group {', '.join(unknown)}; "
                         f"known: {', '.join(GROUPS)}")
    out = dict(result)
    for g in GROUPS:
        if g not in keep:
            out[g] = _empty_like(result[g])
    out["identical"] = not has_differences(out)
    return out


class _Budget:
    """A count-down of how many field changes may still be kept."""

    def __init__(self, limit: int | None) -> None:
        self.left = limit

    def take(self, changes: list[dict]) -> list[dict]:
        if self.left is None:
            return changes
        kept = changes[:self.left]
        self.left -= len(kept)
        return kept

    def entries(self, entries: list[dict]) -> list[dict]:
        """Entries that carry ``changes``; an entry that loses all of its
        changes to the budget is dropped."""
        out = []
        for e in entries:
            changes = self.take(e["changes"])
            if changes:
                out.append({**e, "changes": changes})
        return out


def count_changes(result: dict) -> int:
    """How many field changes *result* lists (``flatten`` rows that are
    field changes, not whole elements added or removed)."""
    return sum(1 for r in flatten(result) if not r["field"].startswith("("))


def limit_changes(result: dict, limit: int | None) -> dict:
    """A copy of *result* keeping at most *limit* field changes (in display
    order; ``None`` keeps all), plus ``total_changes`` and ``truncated``.
    Added, removed and renamed elements and links are always kept."""
    total = count_changes(result)
    budget = _Budget(limit)
    out = dict(result)
    out["elements"] = {**result["elements"],
                       "changed": budget.entries(result["elements"]["changed"])}
    out["links"] = {**result["links"],
                    "changed": budget.entries(result["links"]["changed"])}
    out["structure"] = budget.entries(result["structure"])
    out["system"] = budget.take(result["system"])
    for name in ("rates", "spare_pools", "resources", "triggers", "phases"):
        out[name] = {**result[name],
                     "changed": budget.entries(result[name]["changed"])}
    out["project"] = budget.take(result["project"])
    out["total_changes"] = total
    out["truncated"] = limit is not None and total > limit
    return out


def _empty_like(group: Any) -> Any:
    if isinstance(group, list):
        return []
    return {k: [] for k in group}


# ---------------------------------------------------------------------------
# flattening (CSV, Markdown, text, GUI)
# ---------------------------------------------------------------------------

def _row(group: str, kind: str, path: str, field_name: str, category: str,
         a: str, b: str) -> dict[str, str]:
    return {"group": group, "kind": kind, "path": path, "field": field_name,
            "category": category, "a": a, "b": b}


def _flat_elements(result: dict) -> Iterator[dict[str, str]]:
    el = result["elements"]
    for e in el["added"]:
        yield _row("elements", e["kind"], e["path"], "(added)", "", "", "present")
    for e in el["removed"]:
        yield _row("elements", e["kind"], e["path"], "(removed)", "", "present", "")
    for r in el["renamed"]:
        yield _row("elements", r["kind"], r["from"], "(renamed)", "identity",
                   r["from"], r["to"])
    for e in el["changed"]:
        for c in e["changes"]:
            yield _row("elements", e["kind"], e["path"], c["field"],
                       c["category"], c["a"], c["b"])


def _flat_links(result: dict) -> Iterator[dict[str, str]]:
    link = result["links"]

    def where(e: dict) -> str:
        return f"{e['from']} -> {e['to']}" + (f" (in {e['in']})" if e["in"] else "")
    for e in link["added"]:
        yield _row("links", "Link", where(e), "(added)", "", "", "present")
    for e in link["removed"]:
        yield _row("links", "Link", where(e), "(removed)", "", "present", "")
    for e in link["changed"]:
        for c in e["changes"]:
            yield _row("links", "Link", where(e), c["field"], c["category"],
                       c["a"], c["b"])


def _flat_named(result: dict, group: str, kind: str) -> Iterator[dict[str, str]]:
    g = result[group]
    for name in g["added"]:
        yield _row(group, kind, name, "(added)", "", "", "present")
    for name in g["removed"]:
        yield _row(group, kind, name, "(removed)", "", "present", "")
    for e in g["changed"]:
        for c in e["changes"]:
            yield _row(group, kind, e["name"], c["field"], c["category"],
                       c["a"], c["b"])


_NAMED_KIND = {"rates": "Rate", "spare_pools": "Spare pool",
               "resources": "Resource", "triggers": "Trigger",
               "phases": "Phase"}


def flatten(result: dict) -> list[dict[str, str]]:
    """One row per difference, ``group, kind, path, field, category, a, b``,
    in display order."""
    rows: list[dict[str, str]] = []
    rows += _flat_elements(result)
    rows += _flat_links(result)
    for e in result["structure"]:
        rows += [_row("structure", e["kind"], e["path"], c["field"],
                      c["category"], c["a"], c["b"]) for c in e["changes"]]
    rows += [_row("system", "System", "", c["field"], c["category"], c["a"],
                  c["b"]) for c in result["system"]]
    for group, kind in _NAMED_KIND.items():
        rows += _flat_named(result, group, kind)
    rows += [_row("project", "Project", "", c["field"], c["category"], c["a"],
                  c["b"]) for c in result["project"]]
    return rows

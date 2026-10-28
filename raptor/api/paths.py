"""The path grammar for reading and changing model fields from text.

One grammar, shared by the command line (`raptor run --set`, `raptor get`,
`raptor set`), and anything else that wants to name a field in a string::

    block:Pump A.fail_dist=lognormal
    block:12.fail_params=4,0.5
    block:Pump A.repair_params[0]=36
    node:N3.k=2
    event:Gate.prob=0.9
    hier:Landing.name=Gear
    system.runs=200            (also: length, termination, stats_start, units,
                                weak, cost, capacity, flow, gy, yr)
    rate:MTBF=1200

``kind:selector.field``: the selector is an element name (it may contain
spaces and dots) or a numeric id; the field is the text after the LAST dot.
A selector that is all digits is an id. Field names are the friendly `.rbds`
names or legacy column names (`raptor.api.build` resolves them), and a
distribution may be given by name (`lognormal`) as well as by code.

Values: numbers become numbers, ``a,b,c`` becomes a list, ``true``/``false``
become booleans, anything else stays text (rate expressions like ``@MTBF*2``
are resolved by the builder).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from raptor.api.workspace import Workspace


class PathError(ValueError):
    """The path text is malformed, or names something that does not exist."""


#: `kind:` prefixes -> the kind name `raptor.api.build.resolve_element` knows.
_ELEMENT_KINDS = {
    "block": "Block", "node": "Node", "event": "Event",
    "hier": "Hier", "hierarchy": "Hier", "link": "Link",
}

#: `system.<name>` -> `configure_system` keyword.
SYSTEM_FIELDS = {
    "runs": "num_runs", "num_runs": "num_runs",
    "length": "run_length", "run_length": "run_length",
    "termination": "termination", "stats_start": "start_stats",
    "start_stats": "start_stats", "units": "units",
    "weak": "weak_link", "weak_link": "weak_link",
    "cost": "cost", "capacity": "capacity",
    "flow": "flow_generated", "flow_generated": "flow_generated",
    "gy": "gy_threshold", "yr": "yr_threshold",
}

_DIST_FIELDS = {"fail_dist", "repair_dist", "fdist", "rdist"}


@dataclass(frozen=True)
class ParsedPath:
    kind: str            # block|node|event|hier|link|system|rate
    selector: str        # element name or id ("" for system)
    field: str           # field name ("" for rate)


def parse_path(text: str) -> ParsedPath:
    """Split ``kind:selector.field`` / ``system.field`` / ``rate:NAME``."""
    text = text.strip()
    if not text:
        raise PathError("empty path")
    if text.startswith("system."):
        field = text[len("system."):]
        if not field:
            raise PathError("system. needs a field name")
        return ParsedPath("system", "", field)
    if ":" not in text:
        raise PathError(
            f"path {text!r} needs a kind prefix, e.g. block:NAME.field, "
            f"node:NAME.field, system.runs or rate:NAME")
    kind, rest = text.split(":", 1)
    kind = kind.strip().lower()
    if kind == "rate":
        if not rest:
            raise PathError("rate: needs a name")
        return ParsedPath("rate", rest, "")
    if kind not in _ELEMENT_KINDS:
        raise PathError(
            f"unknown kind {kind!r} in {text!r}; use one of "
            f"{', '.join(sorted(_ELEMENT_KINDS))}, system or rate")
    if "." not in rest:
        raise PathError(
            f"path {text!r} needs a field after the element, "
            f"e.g. {kind}:{rest}.name")
    selector, field = rest.rsplit(".", 1)
    if not selector or not field:
        raise PathError(f"path {text!r} has an empty selector or field")
    return ParsedPath(kind, selector, field)


def parse_value(text: str) -> Any:
    """Text to a value: bool, int, float, list of numbers, or the text."""
    t = text.strip()
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    if "," in t and not t.startswith("@"):
        parts = [p.strip() for p in t.split(",")]
        if all(_is_number(p) for p in parts):
            return [_number(p) for p in parts]
    if _is_number(t):
        return _number(t)
    return t


def _is_number(s: str) -> bool:
    return bool(re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", s))


def _number(s: str) -> int | float:
    f = float(s)
    return int(f) if re.fullmatch(r"[+-]?\d+", s) else f


def split_assignment(text: str) -> tuple[str, str]:
    """``PATH=VALUE`` -> (path, value). Splits at the first ``=`` that is
    not inside the selector (names cannot contain ``=``)."""
    if "=" not in text:
        raise PathError(f"{text!r} is not PATH=VALUE")
    path, value = text.split("=", 1)
    return path.strip(), value.strip()


def distribution_code(name: str) -> int:
    """A distribution name (`lognormal`, `weibull`, `exponential`) or code."""
    from raptor.core.rng import Distribution
    t = name.strip()
    if t.isdigit():
        return int(t)
    key = re.sub(r"[^a-z0-9]", "", t.lower())
    for d in Distribution:
        if re.sub(r"[^a-z0-9]", "", d.name.lower()) == key:
            return int(d)
    raise PathError(
        f"unknown distribution {name!r}; use a code 1-22 or a name such as "
        f"exponential, weibull, lognormal, normal, uniformreal")


def apply_assignment(workspace: Workspace, model_id: str, text: str) -> dict:
    """Apply one ``PATH=VALUE`` and return what changed.

    Raises PathError for anything the grammar cannot place; the builder's
    own errors (unknown field, ambiguous name) propagate as `BuildError`.
    """
    from raptor.api import build, rates

    path_text, value_text = split_assignment(text)
    p = parse_path(path_text)
    value = parse_value(value_text)

    if p.kind == "system":
        key = SYSTEM_FIELDS.get(p.field)
        if key is None:
            raise PathError(
                f"unknown system field {p.field!r}; use one of "
                f"{', '.join(sorted(SYSTEM_FIELDS))}")
        res = build.configure_system(workspace, model_id, **{key: value})
        return {"path": path_text, "value": value, "applied": res["applied"]}

    if p.kind == "rate":
        res = rates.update_rate(workspace, model_id, p.selector,
                                str(value_text))
        return {"path": path_text, "value": value_text, "applied": ["rate"]}

    selector = (f"{_ELEMENT_KINDS[p.kind]}:{p.selector}"
                if p.selector.isdigit() else p.selector)
    if p.field in _DIST_FIELDS and isinstance(value, str):
        value = distribution_code(value)
    if "[" not in p.field and p.field.endswith("params"):
        value = _merge_params(workspace, model_id, selector, p.field, value)
    build.update_element(workspace, model_id, selector, {p.field: value})
    return {"path": path_text, "value": value, "applied": [p.field]}


def _merge_params(workspace: Workspace, model_id: str, selector: str,
                  field: str, value: Any) -> Any:
    """`fail_params=50` or `=1.5,500` sets the LEADING parameters and keeps
    the rest as they are (zero-filling would turn Weibull(1.3, 823, 0) into
    an invalid Weibull(50, 0, 0))."""
    from raptor.api.build import combined_map, resolve_element
    new = value if isinstance(value, list) else [value]
    section, row = resolve_element(workspace.get(model_id).rbd, selector)
    target = combined_map(section).get(field)
    if not isinstance(target, tuple):
        return new
    current = []
    for col in target:
        try:
            current.append(float(row.get(col, 0)))
        except (TypeError, ValueError):
            current.append(0.0)
    return new + current[len(new):]


def apply_assignments(workspace: Workspace, model_id: str,
                      texts: list[str]) -> list[dict]:
    """Apply several, in order. Stops at the first error (earlier changes
    stay applied to the in-memory copy; callers that need all-or-nothing
    work on a copy, as `raptor run` does)."""
    return [apply_assignment(workspace, model_id, t) for t in texts]


def get_value(workspace: Workspace, model_id: str, path_text: str) -> Any:
    """Read one path's current value."""
    from raptor.api import rates
    from raptor.api.build import combined_map, resolve_element

    p = parse_path(path_text)
    rbd = workspace.get(model_id).rbd
    if p.kind == "system":
        key = SYSTEM_FIELDS.get(p.field)
        if key is None:
            raise PathError(f"unknown system field {p.field!r}")
        row = rbd.section("SYSTEM_DATA").rows[0]
        col = {"num_runs": "NumRuns", "run_length": "TermCondition",
               "termination": "TerminationType", "start_stats": "StartStats",
               "units": "units", "weak_link": "weakAnalysis",
               "cost": "costAnalysis", "capacity": "capacityAnalysis",
               "flow_generated": "flowGenerated",
               "gy_threshold": "GYthreshold",
               "yr_threshold": "YRthreshold"}[key]
        if key == "num_runs":         # stored Raptor-style, "9.000000"
            try:
                return int(float(row.get(col) or 0))
            except (TypeError, ValueError):
                pass
        return row.get(col)
    if p.kind == "rate":
        idx = rates.find_rate(rbd.rates or [], p.selector)
        if idx is None:
            raise PathError(f"no rate named {p.selector!r}")
        return rbd.rates[idx]
    selector = (f"{_ELEMENT_KINDS[p.kind]}:{p.selector}"
                if p.selector.isdigit() else p.selector)
    section, row = resolve_element(rbd, selector)
    base = p.field.split("[")[0]
    field_map = combined_map(section)
    target = field_map.get(base, p.field)
    if isinstance(target, tuple):
        vals = [row.get(c) for c in target]
        m = re.search(r"\[(\d+)\]$", p.field)
        return vals[int(m.group(1))] if m else vals
    if target not in row:
        raise PathError(f"{p.kind} has no field {p.field!r}")
    return row.get(target)


def _settable() -> dict[str, list[str]]:
    from raptor.api.build import settable_fields
    return settable_fields()


def help_text() -> str:
    """The `raptor help set-paths` page, generated from the grammar."""
    lines = [
        "PATH GRAMMAR",
        "",
        "  kind:selector.field=value",
        "",
        "  kinds     " + ", ".join(sorted(_ELEMENT_KINDS)) + ", system, rate",
        "  selector  an element name (spaces and dots allowed) or a numeric id",
        "  field     the friendly .rbds name or the .rbd column name; the text "
        "after the last dot",
        "",
        "EXAMPLES",
        "  --set 'block:Pump A.fail_dist=weibull' --set "
        "'block:Pump A.fail_params=1.5,500'",
        "  --set 'block:12.repair_params[0]=36'",
        "  --set node:N3.k=2",
        "  --set system.runs=200 --set system.length=8760",
        "  --set rate:MTBF=1200",
        "",
        "system FIELDS",
        "  " + ", ".join(sorted(SYSTEM_FIELDS)),
        "",
        "FIELDS BY KIND (friendly names; the .rbd file's column names and indexed forms such as repair_params[0] also work)",
        *(f"  {kind.lower():6}  " + ", ".join(names) for kind, names in _settable().items()),
        "",
        "VALUES",
        "  numbers, a,b,c lists, true/false, distribution names "
        "(exponential, weibull, lognormal, ...), or a rate expression "
        "such as @MTBF*2",
    ]
    return "\n".join(lines)

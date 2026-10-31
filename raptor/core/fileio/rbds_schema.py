"""The `.rbds` JSON Schema, generated from the format's own field maps, and
the check every load runs against it.

`build_schema()` is a standard JSON Schema (draft 2020-12), shipped as
`docs/rbds.schema.json` for editors and other validators. `check_document`
validates with `jsonschema` and turns its errors into findings that name the
element and the field.

A finding is an **error** when the file cannot be read as written (a field
of the wrong type, an element without an id, an unknown distribution name):
the load stops with a message naming the element and the field. It is a
**warning** when a key is unknown: the reader keeps it and writes it back,
but nothing uses it, so a misspelt field would otherwise be silently
ignored. Keys starting with ``x_`` are an extension namespace and never
warned about.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

from raptor.core.fileio import rbds

SCHEMA_ID = "urn:raptor-studio:rbds"
_EXTENSION = "^x_"


@dataclass(frozen=True)
class Finding:
    severity: str        # "error" | "warning"
    path: str            # e.g. 'elements.blocks[3] "Fan_A".pm_freq'
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class RbdsFormatError(ValueError):
    """A `.rbds` file that does not match the schema."""

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings
        shown = "\n".join(f"  {f}" for f in findings[:12])
        more = f"\n  ... and {len(findings) - 12} more" if len(findings) > 12 else ""
        super().__init__(f"not a valid .rbds file ({len(findings)} problem(s)):\n{shown}{more}")


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

_BOOL = {"enum": [False, True, 0, 1]}     # 0/1 as Raptor 7 writes them
_LEGACY = {"type": "object", "description": "Raptor 7 columns with no friendly name, verbatim",
           "additionalProperties": {"type": ["string", "number"]}}


#: Fields with a fixed set of values. Raptor 7 reads any other sparing type
#: as none, so a misspelt one would otherwise switch sparing off silently.
_ENUMS: dict[str, list[Any]] = {
    "sparing_type": ["Infinite", "Custom", "Pooled", "None", "Resource", "ColdPool"],
    "dep_type": ["None", "RBDBlock", "RBDNode", "RBDEvent", "RBDHier"],
}
_RANGES: dict[str, tuple[int, int]] = {"type": (1, 5)}


def _field(key: str, column: str | tuple[str, ...]) -> dict[str, Any]:
    if key in _ENUMS:
        return {"enum": list(_ENUMS[key])}
    if key in _RANGES:
        low, high = _RANGES[key]
        return {"type": "integer", "minimum": low, "maximum": high}
    if isinstance(column, tuple):
        return {"type": "array", "items": {"type": "number"}, "maxItems": len(column)}
    if key in rbds._DIST_KEYS:
        return {"type": ["integer", "string"],
                "description": "distribution code, or its name"}
    if key in rbds._BOOL_KEYS:
        return dict(_BOOL)
    if key in rbds._STR_KEYS:
        return {"type": "string"}
    if key in rbds._INT_KEYS:
        return {"type": "integer"}
    return {"type": "number"}


def _object(fields: dict[str, Any], *, required: tuple[str, ...] = (),
            extra: dict[str, Any] | None = None) -> dict[str, Any]:
    props = {**fields, **(extra or {})}
    out: dict[str, Any] = {"type": "object", "properties": props,
                           "patternProperties": {_EXTENSION: {}},
                           "additionalProperties": False}
    if required:
        out["required"] = list(required)
    return out


def _element(section: str, *, with_id: bool) -> dict[str, Any]:
    fields = {k: _field(k, c) for k, c in rbds._MAPS[section].items()}
    extra: dict[str, Any] = {"legacy": _LEGACY}
    required: tuple[str, ...] = ()
    if section == "LINK_DATA":
        end = {"type": "array", "prefixItems": [
            {"enum": ["Block", "Node", "Event", "Hier",
                      "RBDBlock", "RBDNode", "RBDEvent", "RBDHier"]},
            {"type": "integer"}], "minItems": 2, "maxItems": 2}
        extra.update({"from": end, "to": end})
        required = ("id", "from", "to")
    elif with_id:
        required = ("id",)
    else:
        extra["at"] = {"type": "integer", "minimum": 0}
    return _object(fields, required=required, extra=extra)


def _aux(section: str) -> dict[str, Any]:
    fields = {k: _field(k, c) for k, c in rbds._MAPS[section].items()}
    # `key` and `id` are how files written before 0.9 named these rows.
    return _object(fields, extra={"legacy": _LEGACY, "key": {"type": "string"},
                                  "id": {"type": "integer"}})


def build_schema() -> dict[str, Any]:
    """The JSON Schema of the current `.rbds` format."""
    from raptor.core.run_experiment import from_system_row, normalise
    exp = normalise(from_system_row({}))

    def kind(v: Any) -> dict[str, Any]:
        if isinstance(v, bool):
            return {"type": "boolean"}
        if isinstance(v, int):
            return {"type": "integer"}
        if isinstance(v, float):
            return {"type": "number"}
        if isinstance(v, list):
            return {"type": "array"}
        if isinstance(v, dict):
            return {"type": "object"}
        return {"type": "string"}

    elements = {jkey: {"type": "array", "items": _element(rbds._SECTION_KEY[jkey], with_id=True)}
                for jkey in rbds._ELEMENT_KEYS}
    defaults = _object({jkey: _element(rbds._SECTION_KEY[jkey], with_id=False)
                        for jkey in rbds._ELEMENT_KEYS})
    system = _object({k: _field(k, c) for k, c in rbds._SYSTEM_FILE_MAP.items()},
                     extra={"legacy": _LEGACY,
                            "sys_streams": {"type": "array", "items": {"type": "integer"}}})
    annotation = {"type": "object", "additionalProperties": True, "properties": {
        "kind": {"enum": ["text", "rect", "ellipse", "arrow"]}, "id": {"type": "integer"},
        "parent": {"type": "integer"}, "text": {"type": "string"},
        **{k: {"type": "number"} for k in ("x", "y", "w", "h", "x1", "y1", "x2", "y2", "z",
                                           "border_width", "font_size")}}}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "RAPTOR Studio model (.rbds)",
        "description": "Generated from raptor/core/fileio/rbds.py; see docs/RBDS_FORMAT.md.",
        "type": "object",
        "required": ["format", "version"],
        "properties": {
            "format": {"const": rbds.FORMAT_ID},
            "version": {"type": "integer", "minimum": 1},
            "meta": {"type": "object", "additionalProperties": True,
                     "properties": {"generator": {"type": "string"},
                                    "file_name": {"type": "string"}}},
            "system": system,
            "experiment": _object({k: kind(v) for k, v in exp.items()}),
            "elements": _object(elements),
            **elements,                          # an early layout's top-level lists
            "links": {"type": "array", "items": _element("LINK_DATA", with_id=True)},
            "defaults": defaults,
            **{jkey: {"type": "array", "items": _aux(rbds._SECTION_KEY[jkey])}
               for jkey in ("spare_pools", "resources", "triggers", "phases")},
            "annotations": {"type": "array", "items": annotation},
            "rates": {"type": "array", "items": {"type": "object", "required": ["name"],
                      "properties": {"name": {"type": "string"}, "expr": {"type": "string"},
                                     "value": {"type": "number"},
                                     "description": {"type": "string"}}}},
            "formulas": {"type": "object", "additionalProperties": {"type": "object"}},
            "display_unit": {"type": ["string", "null"]},
            "project_meta": {"type": ["object", "null"],
                             "additionalProperties": {"type": "string"}},
            "view_transform": {"type": ["object", "null"],
                               "additionalProperties": {"type": "number"}},
            "comments": {"type": "array", "items": {"type": "object", "properties": {
                "type": {"type": "string"}, "id": {"type": ["string", "integer"]},
                "name": {"type": "string"}, "text": {"type": "string"}}}},
        },
        "patternProperties": {_EXTENSION: {}},
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

_VALIDATOR: Any = None


def _validator() -> Any:
    global _VALIDATOR
    if _VALIDATOR is None:
        from jsonschema import Draft202012Validator
        _VALIDATOR = Draft202012Validator(build_schema())
    return _VALIDATOR


def _show(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _label(item: Any) -> str:
    if isinstance(item, dict):
        name = item.get("name") or item.get("key")
        if isinstance(name, str) and name:
            return f' "{name}"'
    return ""


def _path(doc: Any, parts: Any) -> str:
    """`elements.blocks[1] "Fuel_Day_Tank".pm_freq` for a jsonschema path."""
    out, node = "", doc
    for part in parts:
        if isinstance(part, int):
            node = node[part] if isinstance(node, list) and part < len(node) else None
            out += f"[{part}]{_label(node)}"
        else:
            node = node.get(part) if isinstance(node, dict) else None
            out += f".{part}" if out else str(part)
    return out


def _message(error: Any) -> str:
    kind, value, sub = error.validator, error.instance, error.schema
    if kind == "type":
        kinds = sub["type"] if isinstance(sub["type"], list) else [sub["type"]]
        want = " or ".join("a whole number" if k == "integer" else "a " + k for k in kinds)
        return f"must be {want}; got {_show(value)}"
    if kind == "enum":
        return f"must be one of {', '.join(repr(e) for e in sub['enum'])}; got {_show(value)}"
    if kind == "const":
        return f"must be {sub['const']!r}; got {_show(value)}"
    if kind == "required":
        missing = error.validator_value[0] if len(error.validator_value) == 1 else None
        for key in error.validator_value:
            if key not in value:
                missing = key
                break
        return f"is missing `{missing}`"
    if kind in ("minimum", "maximum"):
        word = "at least" if kind == "minimum" else "at most"
        return f"must be {word} {error.validator_value}; got {_show(value)}"
    if kind in ("maxItems", "minItems"):
        word = "at most" if kind == "maxItems" else "at least"
        return f"has {len(value)} items; {word} {error.validator_value}"
    return error.message


def check_document(doc: Any) -> list[Finding]:
    """Every schema finding for a parsed `.rbds` document, plus the check a
    schema cannot express (a distribution name that does not exist).

    An `additionalProperties` failure is a warning per unknown key (the
    reader keeps and rewrites it, but nothing uses it), naming the nearest
    real field; everything else is an error."""
    out: list[Finding] = []
    for error in sorted(_validator().iter_errors(doc), key=lambda e: list(map(str, e.absolute_path))):
        where = _path(doc, error.absolute_path)
        if error.validator == "additionalProperties":
            known = list(error.schema.get("properties") or {})
            for key in error.instance:
                if key in known or key.startswith(_EXTENSION.lstrip("^")):
                    continue
                near = difflib.get_close_matches(key, known, n=1, cutoff=0.75)
                hint = f"; did you mean `{near[0]}`?" if near else ""
                out.append(Finding("warning", f"{where}.{key}" if where else key,
                                   f"unknown key, kept but not used{hint}"))
        else:
            out.append(Finding("error", where or "file", _message(error)))
    if any(f.severity == "error" for f in out):
        return out
    for path, obj in _element_objects(doc):
        for key in rbds._DIST_KEYS:
            value = obj.get(key)
            if isinstance(value, str):
                try:
                    rbds.dist_code(value)
                except ValueError as exc:
                    out.append(Finding("error", f"{path}.{key}", str(exc)))
    return out


def _element_objects(doc: dict):
    elements = doc.get("elements") or {}
    for jkey in rbds._ELEMENT_KEYS:
        for i, obj in enumerate(elements.get(jkey) or doc.get(jkey) or []):
            if isinstance(obj, dict):
                yield f"elements.{jkey}[{i}]{_label(obj)}", obj
    for jkey in ("triggers", "phases"):
        for i, obj in enumerate(doc.get(jkey) or []):
            if isinstance(obj, dict):
                yield f"{jkey}[{i}]{_label(obj)}", obj

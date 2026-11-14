"""A part type and a library file (WP-4.3).

A part type is everything a block's data can hold except identity and
placement: failure, repair, logistics, sparing, PM, resources, costs,
restoration and dependency defaults.  A library is a plain JSON file of part
types (`.rbdlib`); several can be attached, and a model can carry copies of
the parts it uses so it stays self-contained.

Nothing here touches the engine: applying a part writes ordinary block
columns, so the simulation sees the block it always saw.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: Friendly block fields a part type carries, in a stable order.
PART_FIELDS: tuple[str, ...] = (
    "fail_dist", "fail_params", "repair_dist", "repair_params",
    "pre_ldt_dist", "pre_ldt_params", "post_ldt_dist", "post_ldt_params",
    "spare_pool", "sp_type", "stock", "rso", "new_spares", "arrive_every",
    "eso", "emer_time", "slo", "level", "quantity", "slo_time",
    "uses_pm", "pm_dist", "pm_params", "pm_freq", "pm_cost",
    "pm_fixed_cost", "pm_hold_cost", "pm_spare", "pm_refresh",
    "pm_mis_defer", "pm_fail_reset", "pm_req_defer", "pm_stagger_time",
    "pm_trigger",
    "resource1_name", "num_res", "res1_rep", "res1_pm_rep",
    "restoration_factor", "restoration_type", "duty_cycle", "start_age",
    "initial_cost", "running_cost", "standby_cost", "idle_cost",
    "rep_hold_cost", "rep_per_time_cost", "rep_fixed_cost",
    "done_per_time_cost", "done_fixed_cost", "per_spare_cost",
    "emer_ship_cost",
    "dep_type", "dep_num", "dep_idle", "dep_pm", "dep_fail",
    "dep_pm_thresh", "def_dep_state_idle",
)

#: Fields per element kind; identity, placement and names are never here.
KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "block": PART_FIELDS,
    "node": ("k", "full_flow", "div_flow", "report_node_anal",
             "report_capacity", "cold_standby", "priority_return",
             "check_autos_first", "k_star", "dep_num", "dep_type",
             "uses_phasing", "phs"),
    "event": ("dist", "prob", "fail_stream", "init_cost", "op_cost",
              "rep_cost", "phasing", "uses_phasing"),
    "hierarchy": ("level", "uses_phasing", "phs"),
    "annotation": ("kind", "text", "style", "w", "h"),
}
_SECTION_BY_KIND = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                    "event": "EVENT_DATA", "hierarchy": "HIERARCHY_DATA"}


def element_name(kind: str) -> str:
    """The table column that names an element of this kind."""
    return {"block": "blockName", "node": "Name", "event": "eventName",
            "hierarchy": "hierarchyName"}.get(kind, "name")


_PARAM_FIELDS = {
    "fail_params": ("#FParam", ("FParam1", "FParam2", "FParam3")),
    "repair_params": ("#RParam", ("RParam1", "RParam2", "RParam3")),
}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "part"


@dataclass(frozen=True)
class PartType:
    id: str
    name: str
    fields: dict[str, Any] = field(default_factory=dict)
    category: str = ""
    description: str = ""
    source: str = ""
    basis: str = "judgement"        # data | handbook | judgement
    updated: str = ""
    kind: str = "block"            # block | node | event | hierarchy | annotation

    @property
    def version(self) -> str:
        blob = json.dumps([self.kind, self.fields], sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["version"] = self.version
        return data

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> PartType:
        return cls(
            id=str(row.get("id") or _slug(str(row.get("name") or "part"))),
            name=str(row.get("name") or row.get("id") or "Part"),
            fields=dict(row.get("fields") or {}),
            category=str(row.get("category") or ""),
            description=str(row.get("description") or ""),
            source=str(row.get("source") or ""),
            basis=str(row.get("basis") or "judgement"),
            updated=str(row.get("updated") or ""),
            kind=str(row.get("kind") or "block"),
        )


@dataclass
class Library:
    name: str
    parts: dict[str, PartType] = field(default_factory=dict)
    path: str = ""

    def get(self, part_id: str) -> PartType | None:
        if part_id in self.parts:
            return self.parts[part_id]
        for part in self.parts.values():
            if part.name == part_id:
                return part
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"format": "raptor-studio-part-library", "version": 1,
                "name": self.name,
                "parts": [part.to_dict() for part in self.parts.values()]}

    @classmethod
    def from_dict(cls, doc: dict[str, Any], *, path: str = "") -> Library:
        if doc.get("format") != "raptor-studio-part-library":
            raise ValueError(
                "not a raptor-studio-part-library file; import .rbl/.json "
                "with 'raptor library import'")
        parts = {}
        for row in doc.get("parts") or ():
            part = PartType.from_dict(row)
            parts[part.id] = part
        return cls(name=str(doc.get("name") or Path(path).stem or "Library"),
                   parts=parts, path=path)


def block_field_map() -> dict[str, Any]:
    from raptor.api.build import combined_map
    return combined_map("BLOCK_DATA")


def _number(text: Any) -> Any:
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    try:
        return float(str(text))
    except (TypeError, ValueError):
        return text


def part_from_element(kind: str, row: dict[str, Any], *,
                      name: str | None = None, part_id: str | None = None,
                      category: str = "", description: str = "",
                      source: str = "", basis: str = "judgement") -> PartType:
    """Everything an element's row carries that belongs to a part type."""
    if kind == "annotation":
        return part_from_annotation(row, name=name, part_id=part_id,
                                    category=category, description=description,
                                    source=source, basis=basis)
    section = _SECTION_BY_KIND.get(kind)
    if section is None:
        raise ValueError(f"unknown part kind {kind!r}")
    from raptor.api.build import combined_map
    mapping = combined_map(section)
    fields: dict[str, Any] = {}
    for field_name in KIND_FIELDS[kind]:
        target = mapping.get(field_name)
        if target is None:
            continue
        if field_name in _PARAM_FIELDS:
            count_col, columns = _PARAM_FIELDS[field_name]
            try:
                count = int(float(row.get(count_col, len(columns)) or 0))
            except (TypeError, ValueError):
                count = len(columns)
            values = [_number(row.get(col, 0)) for col in columns[:max(1, count)]]
            fields[field_name] = values
            continue
        if isinstance(target, tuple):
            values = [_number(row.get(col, 0)) for col in target]
            if any(value not in (0, 0.0, "0", "") for value in values):
                fields[field_name] = values
            continue
        if target in row and row.get(target) not in (None, ""):
            fields[field_name] = _number(row.get(target))
    display = name or str(row.get(element_name(kind)) or row.get("ID")
                          or "Part")
    return PartType(id=part_id or _slug(display), name=display,
                    fields=fields, category=category, description=description,
                    source=source, basis=basis, kind=kind)


def part_from_block(row: dict[str, Any], *, name: str | None = None,
                    part_id: str | None = None, category: str = "",
                    description: str = "", source: str = "",
                    basis: str = "judgement") -> PartType:
    """Backward-compatible block part."""
    return part_from_element("block", row, name=name, part_id=part_id,
                             category=category, description=description,
                             source=source, basis=basis)


def part_from_annotation(entry: dict[str, Any], *, name: str | None = None,
                         part_id: str | None = None, category: str = "",
                         description: str = "", source: str = "",
                         basis: str = "judgement") -> PartType:
    """A canvas annotation as a reusable template (kind, text, style, size)."""
    fields = {key: entry[key] for key in KIND_FIELDS["annotation"]
              if key in entry and entry[key] not in (None, "")}
    display = name or str(entry.get("text") or entry.get("kind") or "Note")
    display = display.strip().splitlines()[0][:40] or "Note"
    return PartType(id=part_id or _slug(display), name=display,
                    fields=fields, category=category or "Annotations",
                    description=description, source=source, basis=basis,
                    kind="annotation")


def _legacy_part(entry: dict[str, Any]) -> PartType:
    """One `.rbl`/JSON template as a part type, without plot position."""
    row = dict(entry.get("row") or {})
    from raptor.api.build import resolve_element  # noqa: F401 - documents the map
    mapping = block_field_map()
    keep = {field: mapping[field] for field in PART_FIELDS
            if field in mapping}
    filtered: dict[str, Any] = {}
    for field_name, target in keep.items():
        if isinstance(target, tuple):
            filtered[field_name] = [_number(row.get(col, 0))
                                    for col in target]
        elif target in row:
            filtered[field_name] = _number(row.get(target))
    display = str(entry.get("name") or row.get("blockName") or "Part")
    return PartType(id=_slug(display), name=display, fields=filtered,
                    basis="handbook", source="imported block library",
                    kind="block")


def read_library(path: str | Path) -> Library:
    text = Path(path).read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not a library file ({exc})") from exc
    return Library.from_dict(doc, path=str(path))


def write_library(library: Library, path: str | Path) -> str:
    Path(path).write_text(json.dumps(library.to_dict(), indent=1) + "\n",
                          encoding="utf-8")
    return str(path)


def import_legacy(path: str | Path, *, name: str | None = None) -> Library:
    """Import today's `.rbl` or JSON block library into part types."""
    from raptor.core.fileio.block_library import load_library
    entries = load_library(str(path))
    parts = {}
    for entry in entries:
        part = _legacy_part(entry)
        parts[part.id] = part
    return Library(name=name or Path(path).stem, parts=parts,
                   path=str(path))

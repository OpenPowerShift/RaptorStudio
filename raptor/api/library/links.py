"""Links between blocks and part types (WP-4.3).

The link is bookkeeping in the model's `.rbds` extra data.  A linked block's
own columns still hold the real values, so the engine, `.rbd` export and
every existing tool see an ordinary block; the case of a part made from a
block and linked back to it is therefore simulation-identical.

An override is a field the analyst changed by hand after linking: updates
from the part type leave it alone until it is reset.
"""
from __future__ import annotations

from typing import Any

LINK_KEY = "part_links"


def _links(rbd: Any) -> dict[str, Any]:
    links = dict((getattr(rbd, "rbds_extra", None) or {}).get(LINK_KEY) or {})
    links.setdefault("blocks", {})
    links.setdefault("elements", {})
    links.setdefault("parts", {})
    return links


def _store(rbd: Any, links: dict[str, Any]) -> None:
    if getattr(rbd, "rbds_extra", None) is None:
        rbd.rbds_extra = {}
    rbd.rbds_extra[LINK_KEY] = links


def link_record(rbd: Any, block_id: str | int) -> dict[str, Any] | None:
    links = _links(rbd)
    return (links["blocks"].get(str(block_id))
            or links["elements"].get(f"block:{block_id}"))


def element_record(rbd: Any, kind: str,
                   element_id: str | int) -> dict[str, Any] | None:
    key = f"{kind}:{element_id}"
    links = _links(rbd)
    if kind == "block":
        return links["blocks"].get(str(element_id)) or links["elements"].get(key)
    return links["elements"].get(key)


def linked_blocks(rbd: Any) -> dict[str, dict[str, Any]]:
    return dict(_links(rbd)["blocks"])


def local_parts(rbd: Any) -> dict[str, Any]:
    from raptor.api.library.model import PartType
    return {part_id: PartType.from_dict(row)
            for part_id, row in _links(rbd)["parts"].items()}


_SECTION_KIND = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                 "event": "EVENT_DATA", "hierarchy": "HIERARCHY_DATA"}
_BUILD_KIND = {"block": "block", "node": "node", "event": "event",
               "hierarchy": "hier"}


def _element(rbd: Any, kind: str, selector: str | int
             ) -> tuple[dict[str, Any], str]:
    """The live row (and its id) for one element of `kind`."""
    from raptor.api.build import resolve_element
    if kind == "annotation":
        aid = int(selector)
        for entry in getattr(rbd, "annotations", []) or []:
            if int(entry.get("id", 0)) == aid:
                return entry, str(aid)
        raise ValueError(f"annotation {selector!r} not found")
    section_name = _SECTION_KIND.get(kind)
    if section_name is None:
        raise ValueError(f"unknown part kind {kind!r}")
    label = {"block": "Block", "node": "Node", "event": "Event",
             "hierarchy": "Hier"}[kind]
    section, row = resolve_element(
        rbd, f"{label}:{selector}" if str(selector).isdigit() else str(selector))
    if section != section_name:
        raise ValueError(f"{selector!r} is not a {kind}")
    return row, str(row.get("ID"))


def _block_row(rbd: Any, selector: str | int) -> dict[str, Any]:
    return _element(rbd, "block", selector)[0]


def _current(row: dict[str, Any], field_name: str, kind: str = "block") -> Any:
    from raptor.api.build import combined_map
    from raptor.api.library.model import _number
    if kind == "annotation":
        return _number(row.get(field_name)) if field_name in row else None
    target = combined_map(_SECTION_KIND.get(kind, "BLOCK_DATA")).get(field_name)
    if target is None:
        return None
    if isinstance(target, tuple):
        count = None
        if field_name == "fail_params":
            count = row.get("#FParam")
        elif field_name == "repair_params":
            count = row.get("#RParam")
        columns = list(target)
        if count is not None:
            try:
                columns = columns[:max(1, int(float(count)))]
            except (TypeError, ValueError):
                pass
        return [_number(row.get(col, 0)) for col in columns]
    if target not in row:
        return None
    return _number(row.get(target))


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, list) or isinstance(right, list):
        left_list = left if isinstance(left, list) else [left]
        right_list = right if isinstance(right, list) else [right]
        if len(left_list) != len(right_list):
            return False
        return all(_same(a, b) for a, b in zip(left_list, right_list,
                                               strict=True))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-9 * max(
            1.0, abs(float(left)), abs(float(right)))
    return str(left) == str(right)


def apply_part_to_rbd(rbd: Any, element: str | int, part: Any,
                      *, overrides: list[str] | None = None,
                      library: str = "") -> dict[str, Any]:
    """Apply a part to one element and record the link (the GUI path).

    `element` is a block id/name by default, or ``"kind:selector"`` (for
    example ``"node:Junction"``) for the other kinds.  `raptor.api.build`
    owns the column mapping and parameter-count repair, so a part writes
    exactly the columns a hand edit would.
    """
    from raptor.api import build
    from raptor.api.library.model import PartType
    part = part if isinstance(part, PartType) else PartType.from_dict(part)
    kind = part.kind
    selector = element
    if isinstance(element, str) and element.startswith(f"{kind}:"):
        selector = element.split(":", 1)[1]
    row, element_id = _element(rbd, kind, selector)
    overrides = list(overrides or ())
    patch = {field: value for field, value in part.fields.items()
             if field not in overrides}
    if patch:
        if kind == "annotation":
            row.update(patch)
        else:
            build._apply_patch(rbd, _SECTION_KIND[kind], row,  # noqa: SLF001
                               _BUILD_KIND[kind], patch)
            build._fix_dist_param_counts(row, patch)  # noqa: SLF001
            build.sync_counts(rbd)
    links = _links(rbd)
    links["parts"][part.id] = part.to_dict()
    record = {"part": part.id, "version": part.version,
              "overrides": overrides, "library": library, "kind": kind}
    if kind == "block":
        links["blocks"][element_id] = record
    else:
        links["elements"][f"{kind}:{element_id}"] = record
    _store(rbd, links)
    return {"block" if kind == "block" else "element": element_id,
            "kind": kind, "part": part.id, "version": part.version,
            "overrides": overrides, "fields": sorted(patch)}


def link_element(workspace: Any, model_id: str, kind: str,
                 element: str | int, part: Any, *, library: str = "",
                 overrides: list[str] | None = None) -> dict[str, Any]:
    """Apply a part type to any element, record the link and save."""
    result = apply_part_to_rbd(workspace.get(model_id).rbd,
                               element if kind == "block"
                               else f"{kind}:{element}", part,
                               overrides=overrides, library=library)
    workspace.save(model_id)
    return result


def link_block(workspace: Any, model_id: str, block: str | int, part: Any,
               *, library: str = "", overrides: list[str] | None = None
               ) -> dict[str, Any]:
    """Backward-compatible block link."""
    return link_element(workspace, model_id, "block", block, part,
                        library=library, overrides=overrides)


def detach_rbd(rbd: Any, block: str | int) -> dict[str, Any]:
    """Remove the link from a live model; the block keeps its values."""
    row = _block_row(rbd, block)
    block_id = str(row.get("ID"))
    links = _links(rbd)
    removed = links["blocks"].pop(block_id, None)
    _store(rbd, links)
    return {"block": block_id, "detached": bool(removed)}


def detach(workspace: Any, model_id: str, block: str | int) -> dict[str, Any]:
    """Remove the link, record it in the model and save."""
    result = detach_rbd(workspace.get(model_id).rbd, block)
    workspace.save(model_id)
    return result


def set_override(workspace: Any, model_id: str, block: str | int,
                 field_name: str) -> dict[str, Any]:
    """Mark a field as changed by hand; updates from the part leave it."""
    rbd = workspace.get(model_id).rbd
    row = _block_row(rbd, block)
    block_id = str(row.get("ID"))
    links = _links(rbd)
    record = links["blocks"].get(block_id)
    if record is None:
        raise ValueError(f"block {block_id} does not follow a part type")
    overrides = list(record.get("overrides") or ())
    if field_name not in overrides:
        overrides.append(field_name)
    record["overrides"] = overrides
    _store(rbd, links)
    workspace.save(model_id)
    return {"block": block_id, "overrides": overrides}


def reset_override(workspace: Any, model_id: str, block: str | int,
                   field_name: str, *, part: Any = None) -> dict[str, Any]:
    """Drop an override and put the part's value back."""
    rbd = workspace.get(model_id).rbd
    row = _block_row(rbd, block)
    block_id = str(row.get("ID"))
    links = _links(rbd)
    record = links["blocks"].get(block_id)
    if record is None:
        raise ValueError(f"block {block_id} does not follow a part type")
    overrides = [name for name in record.get("overrides") or ()
                 if name != field_name]
    record["overrides"] = overrides
    source = part
    if source is None:
        from raptor.api.library.model import PartType
        source = PartType.from_dict(links["parts"].get(record["part"]) or {})
    value = getattr(source, "fields", {}).get(field_name)
    if value is not None:
        from raptor.api import build
        build.update_element(workspace, model_id, f"Block:{block_id}",
                             {field_name: value})
    _store(workspace.get(model_id).rbd, links)
    workspace.save(model_id)
    return {"block": block_id, "overrides": overrides,
            "reset": field_name}


def pending_updates(rbd: Any, library: Any = None) -> list[dict[str, Any]]:
    """Blocks whose part type now differs from the block's own values."""
    from raptor.api.library.model import PartType
    links = _links(rbd)
    out: list[dict[str, Any]] = []
    records: list[tuple[str, str, dict[str, Any]]] = []
    records += [("block", key, record)
                for key, record in links["blocks"].items()]
    records += [(key.split(":", 1)[0], key.split(":", 1)[1], record)
                for key, record in links["elements"].items()]
    for kind, element_id, record in records:
        part = None
        if library is not None:
            part = library.get(record.get("part", ""))
        if part is None and record.get("part") in links["parts"]:
            part = PartType.from_dict(links["parts"][record["part"]])
        if part is None:
            continue
        try:
            row, resolved = _element(rbd, kind, element_id)
        except ValueError:
            continue
        overrides = set(record.get("overrides") or ())
        changes = {}
        for field_name, value in part.fields.items():
            if field_name in overrides:
                continue
            current = _current(row, field_name, kind)
            if current is not None and not _same(current, value):
                changes[field_name] = {"element": current, "part": value}
        if changes:
            from raptor.api.library.model import element_name
            out.append({"block": resolved if kind == "block" else "",
                        "element": resolved, "kind": kind,
                        "name": str(row.get(element_name(kind)) or resolved),
                        "part": part.id, "name_part": part.name,
                        "version": part.version,
                        "recorded_version": record.get("version", ""),
                        "changes": changes})
    return out


def apply_updates(workspace: Any, model_id: str, *, library: Any = None,
                  dry_run: bool = True) -> dict[str, Any]:
    """Apply pending part changes to linked blocks, respecting overrides."""
    from raptor.api import build
    from raptor.api.library.model import PartType
    rbd = workspace.get(model_id).rbd
    pending = pending_updates(rbd, library)
    applied = []
    if not dry_run:
        links = _links(rbd)
        for entry in pending:
            part = (library.get(entry["part"]) if library is not None
                    else None)
            if part is None:
                part = PartType.from_dict(links["parts"].get(entry["part"]) or {})
            patch = {field: value for field, value in part.fields.items()
                     if field not in set(
                         (links["blocks"].get(entry["block"]) or {})
                         .get("overrides") or ())}
            selector = (f"Block:{entry['block']}" if entry["kind"] == "block"
                        else entry["element"])
            if entry["kind"] == "annotation":
                rbd = workspace.get(model_id).rbd
                row, _id = _element(rbd, "annotation", entry["element"])
                row.update(patch)
            else:
                build.update_element(workspace, model_id, selector, patch)
            applied.append({"block": entry["block"],
                            "element": entry["element"],
                            "kind": entry["kind"], "part": part.id,
                            "fields": sorted(patch)})
        if pending:
            rbd = workspace.get(model_id).rbd
            links = _links(rbd)
            for entry in pending:
                part = (library.get(entry["part"]) if library is not None
                        else None)
                if part is not None:
                    links["parts"][part.id] = part.to_dict()
                    if entry["kind"] == "block":
                        links["blocks"][entry["block"]]["version"] = (
                            part.version)
                    else:
                        links["elements"][
                            f"{entry['kind']}:{entry['element']}"
                        ]["version"] = part.version
            _store(rbd, links)
            workspace.save(model_id)
    return {"dry_run": dry_run, "pending": pending, "applied": applied,
            "count": len(pending)}


def detach_element_rbd(rbd: Any, kind: str,
                       element: str | int) -> dict[str, Any]:
    """Remove one element's part link; values stay."""
    links = _links(rbd)
    if kind == "block":
        record = links["blocks"].pop(str(element), None)
        if record is None:
            record = links["elements"].pop(f"block:{element}", None)
    else:
        record = links["elements"].pop(f"{kind}:{element}", None)
    _store(rbd, links)
    return {"kind": kind, "element": str(element), "detached": bool(record)}


def detach_element(workspace: Any, model_id: str, kind: str,
                   element: str | int) -> dict[str, Any]:
    result = detach_element_rbd(workspace.get(model_id).rbd, kind, element)
    workspace.save(model_id)
    return result


def all_links(rbd: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """Every link as (kind, element id, record)."""
    links = _links(rbd)
    out = [("block", key, record)
           for key, record in links["blocks"].items()]
    out += [(key.split(":", 1)[0], key.split(":", 1)[1], record)
            for key, record in links["elements"].items()]
    return out

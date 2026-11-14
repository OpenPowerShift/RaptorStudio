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
    links.setdefault("parts", {})
    return links


def _store(rbd: Any, links: dict[str, Any]) -> None:
    if getattr(rbd, "rbds_extra", None) is None:
        rbd.rbds_extra = {}
    rbd.rbds_extra[LINK_KEY] = links


def link_record(rbd: Any, block_id: str | int) -> dict[str, Any] | None:
    return _links(rbd)["blocks"].get(str(block_id))


def linked_blocks(rbd: Any) -> dict[str, dict[str, Any]]:
    return dict(_links(rbd)["blocks"])


def local_parts(rbd: Any) -> dict[str, Any]:
    from raptor.api.library.model import PartType
    return {part_id: PartType.from_dict(row)
            for part_id, row in _links(rbd)["parts"].items()}


def _block_row(rbd: Any, selector: str | int) -> dict[str, Any]:
    from raptor.api.build import resolve_element
    section, row = resolve_element(rbd, f"Block:{selector}"
                                   if str(selector).isdigit() else str(selector))
    if section != "BLOCK_DATA":
        raise ValueError(f"{selector!r} is not a block")
    return row


def _current(row: dict[str, Any], field_name: str) -> Any:
    from raptor.api.library.model import _number, block_field_map
    target = block_field_map().get(field_name)
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


def apply_part_to_rbd(rbd: Any, block: str | int, part: Any,
                      *, overrides: list[str] | None = None,
                      library: str = "") -> dict[str, Any]:
    """Apply a part and record the link, without a workspace (the GUI path).

    `raptor.api.build` owns the column mapping and the parameter-count
    repair; this only calls them, so a part writes exactly the columns a
    hand edit would.
    """
    from raptor.api import build
    from raptor.api.library.model import PartType
    part = part if isinstance(part, PartType) else PartType.from_dict(part)
    overrides = list(overrides or ())
    patch = {field: value for field, value in part.fields.items()
             if field not in overrides}
    row = _block_row(rbd, block)
    block_id = str(row.get("ID"))
    if patch:
        build._apply_patch(rbd, "BLOCK_DATA", row, "block", patch)  # noqa: SLF001
        build._fix_dist_param_counts(row, patch)  # noqa: SLF001
        build.sync_counts(rbd)
    links = _links(rbd)
    links["parts"][part.id] = part.to_dict()
    links["blocks"][block_id] = {
        "part": part.id, "version": part.version,
        "overrides": overrides, "library": library}
    _store(rbd, links)
    return {"block": block_id, "part": part.id, "version": part.version,
            "overrides": overrides, "fields": sorted(patch)}


def link_block(workspace: Any, model_id: str, block: str | int, part: Any,
               *, library: str = "", overrides: list[str] | None = None
               ) -> dict[str, Any]:
    """Apply a part type to a block, record the link and save the model."""
    result = apply_part_to_rbd(workspace.get(model_id).rbd, block, part,
                               overrides=overrides, library=library)
    workspace.save(model_id)
    return result


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
    for block_id, record in links["blocks"].items():
        part = None
        if library is not None:
            part = library.get(record.get("part", ""))
        if part is None and record.get("part") in links["parts"]:
            part = PartType.from_dict(links["parts"][record["part"]])
        if part is None:
            continue
        row = next((row for row in rbd.section("BLOCK_DATA").rows
                    if str(row.get("ID")) == str(block_id)), None)
        if row is None:
            continue
        overrides = set(record.get("overrides") or ())
        changes = {}
        for field_name, value in part.fields.items():
            if field_name in overrides:
                continue
            current = _current(row, field_name)
            if current is not None and not _same(current, value):
                changes[field_name] = {"block": current, "part": value}
        if changes:
            out.append({"block": str(block_id),
                        "name": str(row.get("blockName") or block_id),
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
            build.update_element(workspace, model_id,
                                 f"Block:{entry['block']}", patch)
            applied.append({"block": entry["block"], "part": part.id,
                            "fields": sorted(patch)})
        if pending:
            rbd = workspace.get(model_id).rbd
            links = _links(rbd)
            for entry in pending:
                part = (library.get(entry["part"]) if library is not None
                        else None)
                if part is not None:
                    links["parts"][part.id] = part.to_dict()
                    links["blocks"][entry["block"]]["version"] = part.version
            _store(rbd, links)
            workspace.save(model_id)
    return {"dry_run": dry_run, "pending": pending, "applied": applied,
            "count": len(pending)}

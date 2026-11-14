"""Part types: library files, links, overrides and engine invisibility."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api import build
from raptor.api import run as run_api
from raptor.api.library import (
    Library, apply_updates, detach, link_block, link_record, part_from_block,
    pending_updates, read_library, reset_override, set_override, write_library,
)
from raptor.api.library.model import PartType, import_legacy
from raptor.api.workspace import Workspace
from raptor.core.fileio.rbd import load_rbd

GOLD = Path("tests/golden")


def _workspace(tmp_path, name="SpaceStation/input.rbd"):
    ws = Workspace(tmp_path)
    mid = ws.register_model(load_rbd(str(GOLD / name)), name="m")["model_id"]
    return ws, mid


def _first_block(rbd):
    return next(row for row in rbd.section("BLOCK_DATA").rows
                if row.get("ID") != "0")


def test_a_library_round_trips_through_its_file(tmp_path):
    ws, mid = _workspace(tmp_path)
    part = part_from_block(_first_block(ws.get(mid).rbd), name="Part A",
                           category="Rotating", source="site CMMS 2024")
    library = Library("Site", {part.id: part})
    path = tmp_path / "site.rbdlib"
    write_library(library, path)
    back = read_library(path)
    assert back.name == "Site"
    assert back.parts[part.id].to_dict() == part.to_dict()


def test_a_part_captures_the_block_data_but_not_identity(tmp_path):
    ws, mid = _workspace(tmp_path)
    row = _first_block(ws.get(mid).rbd)
    part = part_from_block(row)
    assert "fail_dist" in part.fields and "repair_params" in part.fields
    assert "name" not in part.fields
    assert part.version and len(part.version) == 12


def test_linking_a_block_to_its_own_part_changes_no_result(tmp_path):
    ws, mid = _workspace(tmp_path)
    before = run_api.run(ws, mid, num_runs=2, seed=5)
    rbd = ws.get(mid).rbd
    parts = [part_from_block(row) for row in rbd.section("BLOCK_DATA").rows
             if row.get("ID") != "0"]
    for part, row in zip(parts, [row for row in rbd.section("BLOCK_DATA").rows
                                 if row.get("ID") != "0"], strict=True):
        link_block(ws, mid, row["ID"], part)
    after = run_api.run(ws, mid, num_runs=2, seed=5)
    assert after.per_run == before.per_run


def test_pending_updates_and_apply_respect_an_override(tmp_path):
    ws, mid = _workspace(tmp_path)
    row = _first_block(ws.get(mid).rbd)
    part = part_from_block(row, name="Pump")
    link_block(ws, mid, row["ID"], part)
    assert pending_updates(ws.get(mid).rbd) == []

    changed = dict(part.fields)
    changed["repair_params"] = [3.0, 1.0, 0.0]
    changed["pm_freq"] = 123.0
    new_part = PartType.from_dict({**part.to_dict(), "fields": changed})
    library = Library("Site", {new_part.id: new_part})
    pending = pending_updates(ws.get(mid).rbd, library)
    assert pending and pending[0]["changes"]

    set_override(ws, mid, row["ID"], "repair_params")
    result = apply_updates(ws, mid, library=library, dry_run=False)
    applied_fields = result["applied"][0]["fields"]
    assert "pm_freq" in applied_fields
    assert "repair_params" not in applied_fields
    record = link_record(ws.get(mid).rbd, row["ID"])
    assert "repair_params" in record["overrides"]
    assert pending_updates(ws.get(mid).rbd) == []


def test_reset_override_puts_the_parts_value_back(tmp_path):
    ws, mid = _workspace(tmp_path)
    row = _first_block(ws.get(mid).rbd)
    part = part_from_block(row, name="Pump")
    link_block(ws, mid, row["ID"], part)
    set_override(ws, mid, row["ID"], "pm_freq")
    build.update_element(ws, mid, f"Block:{row['ID']}", {"pm_freq": 999.0})
    reset_override(ws, mid, row["ID"], "pm_freq", part=part)
    current = [r for r in ws.get(mid).rbd.section("BLOCK_DATA").rows
               if r.get("ID") == row["ID"]][0]
    assert float(current["PMFreq"]) == pytest.approx(
        float(part.fields["pm_freq"]))


def test_detach_keeps_the_values_and_drops_the_link(tmp_path):
    ws, mid = _workspace(tmp_path)
    row = _first_block(ws.get(mid).rbd)
    link_block(ws, mid, row["ID"], part_from_block(row, name="Pump"))
    assert detach(ws, mid, row["ID"])["detached"] is True
    assert link_record(ws.get(mid).rbd, row["ID"]) is None


def test_the_legacy_block_library_imports_to_part_types(tmp_path):
    from raptor.gui.ui.block_library import load_library
    source = GOLD / "SpaceStation/input.rbd"
    # export two blocks in the old JSON format, then import
    rows = [r for r in load_rbd(str(source)).section("BLOCK_DATA").rows
            if r.get("ID") != "0"][:2]
    from raptor.gui.ui.block_library import export_library
    path = tmp_path / "legacy.json"
    export_library(str(path), rows, "SpaceStation")
    library = import_legacy(path)
    assert len(library.parts) == len(load_library(str(path)))
    names = {part.name for part in library.parts.values()}
    assert {row.get("blockName") for row in rows} <= names


def test_a_missing_part_file_does_not_break_the_model(tmp_path):
    ws, mid = _workspace(tmp_path)
    row = _first_block(ws.get(mid).rbd)
    part = part_from_block(row, name="Pump")
    link_block(ws, mid, row["ID"], part, library=str(tmp_path / "gone.rbdlib"))
    with pytest.raises(FileNotFoundError):
        read_library(tmp_path / "gone.rbdlib")
    assert pending_updates(ws.get(mid).rbd) == []
    assert run_api.run(ws, mid, num_runs=1, seed=1).run_id

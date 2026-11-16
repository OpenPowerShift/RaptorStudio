"""Assemblies: a selection saved with its structure, inserted as a copy."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api.library.assembly import (
    add_local_part, assembly_from_elements, describe, insert_assembly,
    remove_local_part,
)
from raptor.api.library.links import local_parts
from raptor.api.library.model import Library, PartType, read_library, write_library
from raptor.api.validate import issues
from raptor.core.fileio.rbds import load_rbds

MODEL = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "HospitalPower.rbds"


@pytest.fixture
def rbd():
    return load_rbds(str(MODEL))


def _count(rbd, section):
    return len([r for r in rbd.section(section).rows if r.get("ID") != "0"])


def test_a_hierarchy_brings_everything_inside_it(rbd):
    part = assembly_from_elements(rbd, ["Generator_Bank"], name="Gen bank")
    assert part.kind == "assembly"
    assert describe(part) == "1 hierarchy, 4 blocks, 4 nodes, 9 links"


def test_insert_adds_a_renamed_unconnected_copy(rbd):
    part = assembly_from_elements(rbd, ["Generator_Bank"], name="Gen bank")
    before = {s: _count(rbd, s) for s in ("BLOCK_DATA", "NODE_DATA",
                                          "HIERARCHY_DATA", "LINK_DATA")}
    known = {(i.severity, i.message) for i in issues(rbd)}
    result = insert_assembly(rbd, part)
    assert result["count"] == 9
    assert _count(rbd, "BLOCK_DATA") == before["BLOCK_DATA"] + 4
    assert _count(rbd, "HIERARCHY_DATA") == before["HIERARCHY_DATA"] + 1
    assert _count(rbd, "LINK_DATA") == before["LINK_DATA"] + 9
    names = [r["hierarchyName"] for r in rbd.section("HIERARCHY_DATA").rows]
    assert "Generator_Bank_2" in names
    new = [(i.severity, i.message) for i in issues(rbd)
           if (i.severity, i.message) not in known]
    # the only new problems are that the copy is not linked in yet
    assert new and all("needs a link" in message for _s, message in new)


def test_the_copy_simulates_like_the_original_once_wired_in_series():
    """Insert a block assembly into an empty-ish model and run it: the
    engine sees an ordinary block with the original's data."""
    from raptor.api import build
    spec = {"blocks": [{"name": "Pump", "fail_dist": "Fixed",
                        "fail_params": [20, 0, 0], "repair_dist": "Fixed",
                        "repair_params": [2, 0, 0]}],
            "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
            "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "End"}],
            "experiment": {"trials": 2, "termination_type": "TimeSim",
                           "term_condition": 100.0}}
    source = build.build_from_spec(spec)
    part = assembly_from_elements(source, ["Pump"], name="Pump")
    target = build.build_from_spec(spec)
    insert_assembly(target, part)
    row = next(r for r in target.section("BLOCK_DATA").rows
               if r.get("blockName") == "Pump_2")
    original = next(r for r in source.section("BLOCK_DATA").rows
                    if r.get("blockName") == "Pump")
    for column in ("FDist", "FParam1", "RDist", "RParam1"):
        assert row[column] == original[column]


def test_assemblies_round_trip_through_a_library_file(rbd, tmp_path):
    part = assembly_from_elements(rbd, ["UPS_System"], name="UPS")
    path = tmp_path / "plant.rbdlib"
    write_library(Library("plant", {part.id: part}), path)
    back = read_library(path).get(part.id)
    assert back is not None and back.kind == "assembly"
    assert back.version == part.version
    json.loads(path.read_text())


def test_assemblies_live_in_the_model_part_list(rbd):
    part = assembly_from_elements(rbd, ["UPS_System"], name="UPS")
    add_local_part(rbd, part)
    assert local_parts(rbd)[part.id].kind == "assembly"
    assert remove_local_part(rbd, part.id) is True
    assert part.id not in local_parts(rbd)


def test_a_part_type_is_not_an_assembly(rbd):
    with pytest.raises(ValueError, match="not an assembly"):
        insert_assembly(rbd, PartType("x", "x", kind="block"))


def test_the_cli_saves_and_inserts_an_assembly(tmp_path, capsys):
    import shutil
    from raptor.cli.main import main
    model = tmp_path / "m.rbds"
    shutil.copy(MODEL, model)
    lib = tmp_path / "site.rbdlib"
    assert main(["library", "add", str(lib), str(model),
                 "--assembly", "Generator_Bank", "--name", "Gen bank"]) == 0
    assert read_library(lib).get("Gen bank").kind == "assembly"
    assert main(["library", "insert", str(model), "--library", str(lib),
                 "--part", "Gen bank", "--in-place", "-f", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 9
    assert any(r["hierarchyName"] == "Generator_Bank_2"
               for r in load_rbds(str(model)).section("HIERARCHY_DATA").rows)


def test_renamed_buffer_picks_names_the_model_does_not_use():
    from raptor.api.library.assembly import renamed_buffer

    class _Section:
        def __init__(self, rows):
            self.rows = rows

    class _Rbd:
        def __init__(self):
            self._s = {"BLOCK_DATA": _Section([{"blockName": "Pump"}]),
                       "EVENT_DATA": _Section([]),
                       "HIERARCHY_DATA": _Section([{"hierarchyName": "Bank"}])}

        def section(self, name):
            return self._s[name]

    out = renamed_buffer(_Rbd(), [("block", {"blockName": "Pump"}),
                                  ("block", {"blockName": "Pump"}),
                                  ("hier", {"hierarchyName": "Bank"}),
                                  ("link", {"ID": "1"})])
    assert [r.get("blockName") or r.get("hierarchyName") for _k, r in out
            if _k != "link"] == ["Pump_2", "Pump_3", "Bank_2"]

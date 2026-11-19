"""AWB project interchange: table store, RBD mapping and loss summary."""
from __future__ import annotations

import json
import struct

import pytest

from raptor.core.fileio.awb import (
    AwbFormatError, AwbTable, _SCHEMA, export_awb, import_awb, read_awb,
    write_awb,
)
from raptor.core.fileio.rbds import parse_rbds, save_rbds


def _model(**overrides):
    doc = {
        "format": "raptor-studio",
        "version": 4,
        "meta": {"generator": "test"},
        "system": {"units": "hours"},
        "experiment": {"trials": 10, "termination_type": "TimeSim",
                       "term_condition": 1000.0},
        "elements": {
            "blocks": [
                {"id": 1, "name": "Pump", "x": 0.0, "y": 0.0,
                 "fail_dist": 4, "fail_params": [1000.0, 0.0, 0.0],
                 "repair_dist": 19, "repair_params": [10.0, 0.0, 0.0]},
                {"id": 2, "name": "Valve", "x": 100.0, "y": 0.0,
                 "fail_dist": 15,
                 "fail_params": [2.0, 2000.0, 0.0],
                 "repair_dist": 19, "repair_params": [4.0, 0.0, 0.0]},
            ],
            "nodes": [
                {"id": 3, "name": "start", "type": 1, "x": -50.0, "y": 0.0},
                {"id": 4, "name": "end", "type": 3, "x": 150.0, "y": 0.0},
            ],
            "hierarchies": [],
        },
        "links": [
            {"id": 5, "from": ["Node", 3], "to": ["Block", 1]},
            {"id": 6, "from": ["Block", 1], "to": ["Block", 2]},
            {"id": 7, "from": ["Block", 2], "to": ["Node", 4]},
        ],
    }
    doc.update(overrides)
    return parse_rbds(json.dumps(doc), default_name="test")


def _rows(rbd, name):
    return [r for r in rbd.sections[name].rows
            if str(r.get("ID", "")) not in ("", "0")]


def test_table_store_round_trip(tmp_path):
    tables = {
        "Project": AwbTable("Project", _SCHEMA["Project"], [
            {c: ("" if t == 0 else (0 if t in (1, 3) else 0.0))
             for c, t in _SCHEMA["Project"]} | {"Id": "<PROJECTID>"}]),
        "RbdBlocks": AwbTable("RbdBlocks", _SCHEMA["RbdBlocks"], [
            {"Id": "B1", "Super": 0, "Page": 0, "XPosition": 1.5,
             "YPosition": 2.5}]),
    }
    path = tmp_path / "small.awb"
    write_awb(path, tables)
    back = read_awb(path)
    assert set(back) == {"Project", "RbdBlocks"}
    assert back["Project"].rows[0]["Id"] == "<PROJECTID>"
    assert back["RbdBlocks"].rows[0]["XPosition"] == 1.5
    assert back["RbdBlocks"].rows[0]["YPosition"] == 2.5
    # Every column is present, even ones the row did not name.
    assert set(back["RbdBlocks"].rows[0]) == {c for c, _t in _SCHEMA["RbdBlocks"]}


def test_export_writes_the_six_tables_and_the_model(tmp_path):
    rbd = _model()
    path = tmp_path / "model.awb"
    report = export_awb(rbd, path, source="model.rbds")
    assert path.exists()
    tables = read_awb(path)
    assert set(tables) == {
        "Project", "FailureModels", "FailureModelCorrectiveTasks",
        "RbdBlocks", "RbdNodes", "RbdConnections"}
    assert len(tables["RbdBlocks"].rows) == 3  # root + two blocks
    assert len(tables["RbdNodes"].rows) == 2
    assert len(tables["RbdConnections"].rows) == 3
    assert len(tables["FailureModels"].rows) == 2
    assert report.counts["blocks"] == 2
    assert report.counts["links"] == 3
    assert report.direction == "rbds-to-awb"


def test_round_trip_keeps_the_series_logic(tmp_path):
    path = tmp_path / "model.awb"
    export_awb(_model(), path)
    rbd, report = import_awb(path)
    blocks = _rows(rbd, "BLOCK_DATA")
    links = rbd.sections["LINK_DATA"].rows
    assert [b["blockName"] for b in blocks] == ["Pump", "Valve"]
    assert {b["FDist"] for b in blocks} == {"4", "15"}
    assert len(links) == 3
    assert report.counts["blocks"] == 2
    assert report.counts["links"] == 3


def test_exact_failure_laws_survive_the_round_trip(tmp_path):
    laws = [
        (4, [1000.0, 0.0, 0.0]),        # exponential
        (15, [2.0, 2000.0, 0.0]),       # Weibull
        (7, [300.0, 30.0, 0.0]),        # lognormal
        (8, [400.0, 40.0, 0.0]),        # normal
        (19, [500.0, 0.0, 0.0]),        # fixed
    ]
    blocks = [{"id": i + 1, "name": f"B{i + 1}", "x": i * 100.0, "y": 0.0,
               "fail_dist": d, "fail_params": p,
               "repair_dist": 19, "repair_params": [10.0, 0.0, 0.0]}
              for i, (d, p) in enumerate(laws)]
    links = [{"id": 100, "from": ["Node", 90], "to": ["Block", 1]}]
    for i in range(1, len(blocks)):
        links.append({"id": 100 + i, "from": ["Block", i],
                      "to": ["Block", i + 1]})
    links.append({"id": 200, "from": ["Block", len(blocks)],
                  "to": ["Node", 91]})
    doc = {
        "format": "raptor-studio", "version": 4,
        "system": {"units": "hours"},
        "experiment": {"trials": 10, "termination_type": "TimeSim",
                       "term_condition": 1000.0},
        "elements": {
            "blocks": blocks,
            "nodes": [{"id": 90, "name": "start", "type": 1, "x": -50.0,
                       "y": 0.0},
                      {"id": 91, "name": "end", "type": 3,
                       "x": len(blocks) * 100.0 + 50.0, "y": 0.0}],
            "hierarchies": [],
        },
        "links": links,
    }
    rbd = parse_rbds(json.dumps(doc))
    path = tmp_path / "laws.awb"
    export_awb(rbd, path)
    back, _report = import_awb(path)
    got = {int(r["FDist"]) for r in _rows(back, "BLOCK_DATA")}
    assert got == {4, 15, 7, 8, 19}


def test_export_report_names_raptor_features_awb_cannot_use(tmp_path):
    # Add an event, PM, dependency and a non-exponential repair law.
    rbd = parse_rbds(json.dumps({
        "format": "raptor-studio", "version": 4,
        "system": {"units": "days"},
        "experiment": {"trials": 10, "termination_type": "TimeSim",
                       "term_condition": 10.0, "analyses": ["capacity"]},
        "elements": {
            "blocks": [{"id": 1, "name": "Pump", "x": 0, "y": 0,
                        "fail_dist": 14, "fail_params": [0, 50, 100],
                        "repair_dist": 15, "repair_params": [2, 10, 0],
                        "uses_pm": True, "uses_phasing": True,
                        "dep_type": "RBDBlock", "dep_num": 2}],
            "nodes": [{"id": 2, "name": "start", "type": 1},
                      {"id": 3, "name": "end", "type": 3}],
            "events": [{"id": 4, "name": "quake", "prob": 1.0}],
            "hierarchies": [],
        },
        "links": [{"id": 5, "from": ["Node", 2], "to": ["Block", 1]},
                  {"id": 6, "from": ["Block", 1], "to": ["Node", 3]}],
        "annotations": [{"kind": "text", "id": 1, "parent": 0,
                         "x": 0, "y": 0, "w": 10, "h": 10, "text": "note"}],
        "rates": [{"name": "base", "expr": "1/1000", "value": 0.001}],
        "formulas": {"block:1:FParam1": {"rate": "base", "value": 0.001}},
        "triggers": [{"name": "T1"}],
        "spare_pools": [{"name": "pool", "stock": 2}],
        "resources": [{"name": "crew", "num": 1}],
        "phases": [{"name": "phase 1"}],
        "out_of_service": {"block:1": "cut"},
    }), default_name="lossy")
    path = tmp_path / "lossy.awb"
    report = export_awb(rbd, path, source="lossy.rbds")
    features = {loss.feature for loss in report.not_in_target}
    for expected in (
            "failure events", "rate definitions",
            "formula-linked fields", "triggers",
            "capacity analysis",
            "failure distribution simplified to exponential"):
        assert expected in features, (expected, sorted(features))
    assert "times converted from days to hours" in " ".join(report.notes)


def test_import_report_names_awb_features_raptor_cannot_use(tmp_path):
    path = tmp_path / "extra.awb"
    export_awb(_model(), path)
    tables = read_awb(path)
    # Add the non-RBD tables an AvSim project often carries.
    extra = {
        "Consequences": {"Id": "C", "Description": "cost"},
        "Phases": {"Id": "P1", "Duration": 100.0},
        "FailureModelScheduledTasks": {"FailureModel": 0, "TaskId": "PM1"},
        "Spares": {"Id": "S", "CapacityLevel1": 2},
        "Labor": {"Id": "L", "NoAvailable": 1},
        "RcmLocations": {"Id": "LOC"},
        "WeibullSets": {"Id": "W", "Description": "history"},
        "LibraryBitmaps": {"Id": "B"},
        "RbdBlockRuleAssignments": {"Block": 1},
    }
    for name, row in extra.items():
        tables[name] = AwbTable(name, list(row.keys()) and [
            (k, 0) for k in row], [row])
    tables["Project"].rows[0]["AvsimCapacityCalcsPerformed"] = 1
    write_awb(path, tables)
    rbd, report = import_awb(path)
    features = {loss.feature for loss in report.not_in_target}
    for expected in (
            "consequence and cost models", "RCM location hierarchy",
            "Weibull sets", "bitmaps", "capacity flow and capacity targets"):
        assert expected in features, (expected, sorted(features))
    assert rbd is not None


def test_import_synthesises_in_out_nodes_for_a_node_less_hierarchy(tmp_path):
    path = tmp_path / "hier.awb"
    export_awb(_model(), path)
    tables = read_awb(path)
    blocks = tables["RbdBlocks"].rows
    # Turn block 2 into a superblock with its own page, and put a new block
    # inside it.  This is the shape Example1 uses (no explicit nodes).
    blocks[2].update(Id="Sub", Super=1, FailureModel=-1, Page=0)
    blocks.append(dict(blocks[1], Id="Inner", Super=0, Page=2,
                       FailureModel=0))
    tables["RbdBlocks"].rows = blocks
    # The old series link now connects to the superblock; add the inner
    # page's own two-block chain.
    tables["RbdConnections"].rows = [
        dict(tables["RbdConnections"].rows[0], InputObjectIndex=0,
             OutputObjectIndex=1),
        dict(tables["RbdConnections"].rows[1], InputObjectIndex=1,
             OutputObjectIndex=2),
        dict(tables["RbdConnections"].rows[2], InputObjectIndex=2,
             OutputObjectIndex=1),
    ]
    write_awb(path, tables)
    rbd, report = import_awb(path)
    hiers = rbd.sections["HIERARCHY_DATA"].rows
    assert len(hiers) == 1
    assert int(hiers[0]["in"]) > 0 and int(hiers[0]["out"]) > 0
    assert report.counts["hierarchies"] == 1
    # The importer added an In and an Out node to the hierarchy page.
    assert any(n["Type"] == "4" for n in rbd.sections["NODE_DATA"].rows)
    assert any(n["Type"] == "5" for n in rbd.sections["NODE_DATA"].rows)


def test_truncated_file_is_a_clear_error(tmp_path):
    path = tmp_path / "bad.awb"
    path.write_bytes(struct.pack("<I", 1) + b"\x04Proj")
    with pytest.raises(AwbFormatError, match="truncated"):
        read_awb(path)


def test_cli_strict_refuses_to_write_a_lossy_awb(tmp_path, capsys):
    from raptor.cli.main import main
    doc = {
        "format": "raptor-studio", "version": 4,
        "system": {"units": "hours"},
        "experiment": {"trials": 1, "termination_type": "TimeSim",
                       "term_condition": 100.0},
        "elements": {
            "blocks": [{"id": 1, "name": "B", "fail_dist": 4,
                        "fail_params": [100, 0, 0]}],
            "nodes": [], "hierarchies": [],
        },
        "links": [],
        "annotations": [{"kind": "text", "id": 1, "parent": 0,
                         "x": 0, "y": 0, "w": 10, "h": 10, "text": "x"}],
    }
    src = tmp_path / "lossy.rbds"
    save_rbds(parse_rbds(json.dumps(doc)), str(src))
    dest = tmp_path / "lossy.awb"
    assert main(["convert", str(src), str(dest), "--strict",
                 "-f", "json"]) == 4
    assert not dest.exists()
    assert "not_in_target" in capsys.readouterr().out


def test_cli_report_writes_markdown(tmp_path):
    from raptor.cli.main import main
    src = tmp_path / "model.rbds"
    save_rbds(_model(), str(src))
    dest = tmp_path / "model.awb"
    report = tmp_path / "report.md"
    assert main(["convert", str(src), str(dest), "--report",
                 str(report)]) == 0
    text = report.read_text()
    assert "# RAPTOR -> AWB interchange summary" in text
    assert "Converted" in text


def test_cli_convert_awb_round_trip(tmp_path, capsys):
    from raptor.cli.main import main
    src = tmp_path / "model.rbds"
    save_rbds(_model(), str(src))
    awb = tmp_path / "model.awb"
    back = tmp_path / "back.rbds"
    assert main(["convert", str(src), str(awb), "-f", "json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["schema"] == "raptor-awb-report/1"
    assert payload["direction"] == "rbds-to-awb"
    assert main(["convert", str(awb), str(back), "-f", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["direction"] == "awb-to-rbds"
    assert back.exists()
    from raptor.core.fileio.model_file import load_model_file
    loaded = load_model_file(back)
    assert len(_rows(loaded, "BLOCK_DATA")) == 2


def test_pm_spares_resources_phases_and_out_of_service_round_trip(tmp_path):
    doc = {
        "format": "raptor-studio", "version": 4,
        "system": {"units": "hours"},
        "experiment": {"trials": 10, "termination_type": "TimeSim",
                       "term_condition": 1000.0},
        "elements": {
            "blocks": [
                {"id": 1, "name": "Pump", "x": 0, "y": 0,
                 "fail_dist": 4, "fail_params": [1000, 0, 0],
                 "repair_dist": 19, "repair_params": [10, 0, 0],
                 "uses_pm": True, "pm_freq": 100.0,
                 "pm_duration_dist": 19, "pm_params": [5, 0, 0],
                 "pm_refresh": True, "pm_spare": True,
                 "num_resources": 1, "resource_name": "Crew",
                 "repair_resources_needed": 2, "pm_resources_needed": 1,
                 "sparing_type": "Custom", "stock": 3, "new_spares": 1,
                 "quantity": 2, "arrive_every": 720.0, "emer_time": 48.0,
                 "uses_phasing": True,
                 "legacy": {"P 001": "C: 1.000", "P 002": "A: 1.000"}},
                {"id": 2, "name": "Valve", "x": 200, "y": 0,
                 "fail_dist": 4, "fail_params": [2000, 0, 0],
                 "repair_dist": 19, "repair_params": [20, 0, 0]},
            ],
            "nodes": [{"id": 3, "name": "start", "type": 1, "x": -100, "y": 0},
                      {"id": 4, "name": "end", "type": 3, "x": 300, "y": 0}],
            "hierarchies": [],
        },
        "links": [{"id": 5, "from": ["Node", 3], "to": ["Block", 1]},
                  {"id": 6, "from": ["Block", 1], "to": ["Block", 2]},
                  {"id": 7, "from": ["Block", 2], "to": ["Node", 4]}],
        "spare_pools": [{"name": "CrewSpares", "stock": 2, "new_spares": 1,
                         "arrive_every": 720, "eso": 1, "emer_time": 48,
                         "quantity": 1}],
        "resources": [{"name": "Crew", "num_in": 2, "spare_cost": 1,
                       "fixed_per_use": 10, "per_time_unit": 20, "phases": 0}],
        "phases": [{"name": "PhaseA", "num": 1, "mission": 1,
                    "duration_dist": 19, "params": [100, 0, 0]},
                   {"name": "PhaseB", "num": 2, "mission": 1,
                    "duration_dist": 19, "params": [200, 0, 0]}],
        "annotations": [{"kind": "text", "id": 1, "parent": 0, "x": 10,
                         "y": 10, "w": 100, "h": 20, "text": "note"}],
        "out_of_service": {"block:2": "cut"},
    }
    rbd = parse_rbds(json.dumps(doc))
    path = tmp_path / "stage12.awb"
    export_awb(rbd, path, source="stage12.rbds")
    tables = read_awb(path)
    for name in ("FailureModelScheduledTasks", "FailureModelSchedTaskLabor",
                 "FailureModelSchedTaskSpares", "FailureModelCorrTaskLabor",
                 "FailureModelCorrTaskSpares", "Spares", "Labor", "Phases",
                 "RbdBlockRuleAssignments", "RbdLabels"):
        assert tables[name].rows, name
    back, report = import_awb(path)
    assert len(back.sections["PHASES"].rows) == 2
    assert back.sections["RESOURCE_DATA"].rows
    assert back.sections["SPARE_POOL_DATA"].rows
    pump = next(r for r in _rows(back, "BLOCK_DATA")
                if r["blockName"] == "Pump")
    assert pump["usesPM"] == "1"
    assert pump["SpType"] == "Pooled"
    assert pump["NumRes"] == "1"
    assert pump["Resource1Name"]
    assert pump["P 001"].startswith("C:")
    assert back.out_of_service.get("block:2") == "cut"
    features = {loss.feature for loss in report.not_in_target}
    assert "non-maintainable blocks" not in features
    # The sidecar restores RAPTOR's own PM-spare setting on a round trip.
    assert pump["PMSpare"] == "1"
    # A plain AWB project (no sidecar): AWB delays only corrective tasks for
    # spares, so the imported PM must not wait on the pool.
    path.with_name(path.name + ".raptor.json").unlink()
    plain, plain_report = import_awb(path)
    plain_pump = next(r for r in _rows(plain, "BLOCK_DATA")
                      if r["blockName"] == "Pump")
    assert plain_pump.get("PMSpare", "0") in ("0", "")
    assert "planned-task spares" in {
        loss.feature for loss in plain_report.not_in_target}


def test_sidecar_preserves_raptor_only_metadata(tmp_path):
    doc = {
        "format": "raptor-studio", "version": 4,
        "system": {"units": "hours", "capacityAnalysis": "1"},
        "experiment": {"trials": 10, "termination_type": "TimeSim",
                       "term_condition": 1000.0},
        "elements": {
            "blocks": [{"id": 1, "name": "Pump", "x": 0, "y": 0,
                        "fail_dist": 4, "fail_params": [1000, 0, 0],
                        "dep_type": "RBDBlock", "dep_num": 2,
                        "rep_fixed_cost": 25.0}],
            "nodes": [{"id": 2, "name": "start", "type": 1},
                      {"id": 3, "name": "end", "type": 3}],
            "hierarchies": [],
        },
        "links": [{"id": 4, "from": ["Node", 2], "to": ["Block", 1]},
                  {"id": 5, "from": ["Block", 1], "to": ["Node", 3]}],
        "rates": [{"name": "base", "expr": "1/1000", "value": 0.001}],
        "formulas": {"block:1:FParam1": {"rate": "base", "value": 0.001}},
        "triggers": [{"name": "Storm", "repeats": 1, "init_used": 0.0,
                      "dist": 8, "params": [288, 1, 0]}],
        "annotations": [{"kind": "rect", "id": 1, "parent": 0,
                         "x": 0, "y": 0, "w": 20, "h": 20,
                         "text": ""}],
    }
    rbd = parse_rbds(json.dumps(doc))
    path = tmp_path / "sidecar.awb"
    report = export_awb(rbd, path)
    side = path.with_name(path.name + ".raptor.json")
    assert side.exists()
    assert report.sidecar == str(side)
    back, import_report = import_awb(path)
    assert back.rates and back.formulas
    assert len(back.sections["TRIGGERS"].rows) == 1
    assert any(a.get("kind") == "rect" for a in back.annotations)
    assert "sidecar" in " ".join(import_report.notes).lower()


def test_sidecar_can_be_disabled(tmp_path):
    rbd = _model()
    path = tmp_path / "no-sidecar.awb"
    export_awb(rbd, path, sidecar=False)
    assert not path.with_name(path.name + ".raptor.json").exists()


def test_dry_run_writes_nothing(tmp_path):
    rbd = _model()
    path = tmp_path / "dry.awb"
    report = export_awb(rbd, path, write=False)
    assert not path.exists()
    assert not path.with_name(path.name + ".raptor.json").exists()
    assert report.direction == "rbds-to-awb"


def test_normalize_replaces_unsupported_repair_and_clears_extras(tmp_path):
    doc = {
        "format": "raptor-studio", "version": 4,
        "system": {"units": "hours", "capacityAnalysis": "1"},
        "experiment": {"trials": 10, "termination_type": "TimeSim",
                       "term_condition": 1000.0},
        "elements": {
            "blocks": [{"id": 1, "name": "Pump", "x": 0, "y": 0,
                        "fail_dist": 14, "fail_params": [0, 50, 100],
                        "repair_dist": 15, "repair_params": [2, 10, 0],
                        "dep_type": "RBDBlock", "dep_num": 2}],
            "nodes": [{"id": 2, "name": "start", "type": 1},
                      {"id": 3, "name": "end", "type": 3}],
            "hierarchies": [],
        },
        "links": [{"id": 4, "from": ["Node", 2], "to": ["Block", 1]},
                  {"id": 5, "from": ["Block", 1], "to": ["Node", 3]}],
        "annotations": [{"kind": "text", "id": 1, "parent": 0,
                         "x": 0, "y": 0, "w": 10, "h": 10, "text": "x"}],
        "rates": [{"name": "base", "expr": "1/1000", "value": 0.001}],
    }
    from raptor.core.fileio.awb import normalize_for_awb
    rbd = parse_rbds(json.dumps(doc))
    norm, actions = normalize_for_awb(rbd)
    block = _rows(norm, "BLOCK_DATA")[0]
    assert block["FDist"] == "4"
    assert block["RDist"] == "19"
    assert block["DepType"] == "None"
    assert not norm.annotations and not norm.rates
    assert actions


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    from raptor.cli.main import main
    src = tmp_path / "model.rbds"
    save_rbds(_model(), str(src))
    dest = tmp_path / "preview.awb"
    assert main(["convert", str(src), str(dest), "--dry-run",
                 "-f", "json"]) == 0
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".raptor.json").exists()
    assert '"direction": "rbds-to-awb"' in capsys.readouterr().out


def test_cli_normalize_out_writes_reduced_model(tmp_path):
    from raptor.cli.main import main
    src = tmp_path / "model.rbds"
    save_rbds(_model(), str(src))
    dest = tmp_path / "model.awb"
    norm = tmp_path / "normalized.rbds"
    assert main(["convert", str(src), str(dest),
                 "--normalize-out", str(norm)]) == 0
    assert dest.exists() and norm.exists()
    from raptor.core.fileio.model_file import load_model_file
    loaded = load_model_file(norm)
    assert _rows(loaded, "BLOCK_DATA")


def test_cli_no_sidecar(tmp_path):
    from raptor.cli.main import main
    src = tmp_path / "model.rbds"
    save_rbds(_model(), str(src))
    dest = tmp_path / "model.awb"
    assert main(["convert", str(src), str(dest), "--no-sidecar"]) == 0
    assert not dest.with_name(dest.name + ".raptor.json").exists()


def test_dormant_failure_uses_inspection_detection_delay(tmp_path):
    from raptor.core.fileio import awb as awb_mod
    path = tmp_path / "inspection.awb"
    export_awb(_model(), path)
    tables = read_awb(path)
    fm = tables["FailureModels"].rows[0]
    fm["FmDormant"] = 1
    task = awb_mod._row(
        "FailureModelScheduledTasks", FailureModel=0, SubIndex=0, Type=1,
        Description="Inspect dormant pump", TaskId="IN1", Enabled=1,
        DetectionP=1.0, TaskDuration=1.0, RampTime=0.0,
        TaskInterval=1000.0, FixedInterval=0, Offset=0.0)
    tables["FailureModelScheduledTasks"] = AwbTable(
        "FailureModelScheduledTasks",
        _SCHEMA["FailureModelScheduledTasks"], [task])
    write_awb(path, tables)
    back, report = import_awb(path)
    pump = next(r for r in _rows(back, "BLOCK_DATA") if r["blockName"] == "Pump")
    assert pump["RDist"] != "18"
    assert float(pump["Pre-LDTDist"]) == 19
    assert 500.0 < float(pump["Param1#1"]) < 502.0
    assert "inspection detection delay" in " ".join(report.notes).lower()


def test_zero_stock_awb_spare_never_leaves_the_pool_dry(tmp_path):
    """AWB level 3 is unlimited: a zero-stock spare must still be ordered.

    Before the fix the pool came in with stock 0 and no ordering, so every
    block using it ended Done (No_spares) at its first failure.
    """
    from raptor.core.fileio import awb as awb_mod
    path = tmp_path / "spares.awb"
    export_awb(_model(), path)
    tables = read_awb(path)
    rows = [awb_mod._row("Spares", Id="ZERO", CapacityLevel1=0,
                         LogisticDelayLevel1=48.0, LogisticDelayLevel2=96.0,
                         LogisticDelayLevel3=480.0),
            awb_mod._row("Spares", Id="HELD", CapacityLevel1=3,
                         LogisticDelayLevel1=0.0, LogisticDelayLevel2=10.0,
                         LogisticDelayLevel3=20.0)]
    tables["Spares"] = AwbTable("Spares", _SCHEMA["Spares"], rows)
    write_awb(path, tables)
    back, _ = import_awb(path)
    pools = {r["PoolName"]: r for r in back.sections["SPARE_POOL_DATA"].rows}
    zero, held = pools["ZERO"], pools["HELD"]
    assert int(zero["Stock"]) == 0 and int(zero["ESO"]) == 1
    assert float(zero["EmerTime"]) == pytest.approx(624.0)
    assert int(held["Stock"]) == 3 and int(held["ESO"]) == 1
    assert int(held["SLO"]) == 1 and int(held["Level"]) == 2
    assert float(held["SLOTime"]) == pytest.approx(30.0)


def test_competing_weibull_fit_matches_the_minimum_of_the_modes():
    import math

    from raptor.core.fileio.awb import _competing_weibull_fit
    # Two equal exponentials compete as one exponential of half the mean.
    beta, eta, gamma = _competing_weibull_fit([(100.0, 1.0, 0.0),
                                               (100.0, 1.0, 0.0)])
    assert beta == pytest.approx(1.0, rel=1e-3)
    assert eta == pytest.approx(50.0, rel=1e-3)
    assert gamma == 0.0
    # An exponential and a wear-out mode: mean of min(T1, T2) by quadrature.
    modes = [(20000.0, 1.0, 0.0), (4472.1, 2.0, 1000.0)]
    beta, eta, _ = _competing_weibull_fit(modes)
    mean = eta * math.gamma(1.0 + 1.0 / beta)
    assert mean == pytest.approx(4316.0, rel=0.01)


def test_bi_weibull_ignores_the_unused_third_mode(tmp_path):
    path = tmp_path / "biweibull.awb"
    export_awb(_model(), path)
    tables = read_awb(path)
    fm = tables["FailureModels"].rows[0]
    fm.update(FmDistribution=2, FmEta1=200000.0, FmBeta1=1.0, FmGamma1=0.0,
              FmEta2=44721.0, FmBeta2=2.0, FmGamma2=0.0,
              FmEta3=8760.0, FmBeta3=1.0, FmGamma3=0.0)
    write_awb(path, tables)
    back, _ = import_awb(path)
    pump = next(r for r in _rows(back, "BLOCK_DATA") if r["blockName"] == "Pump")
    # The placeholder 8760 h mode would have dragged eta down to 8760.
    assert float(pump["FParam2"]) > 30000.0

# -- review fixes (#288) -------------------------------------------------------

def _twins(**extra):
    """Two blocks with the same failure and repair laws, plus *extra*
    per-block fields (a list of two dicts)."""
    doc = json.loads(json.dumps({
        "format": "raptor-studio", "version": 4,
        "system": {"units": "hours"},
        "experiment": {"trials": 10, "termination_type": "TimeSim",
                       "term_condition": 1000.0},
        "elements": {
            "blocks": [
                {"id": 1, "name": "A", "x": 0.0, "y": 0.0, "fail_dist": 4,
                 "fail_params": [1000.0, 0.0, 0.0], "repair_dist": 19,
                 "repair_params": [10.0, 0.0, 0.0]},
                {"id": 2, "name": "B", "x": 100.0, "y": 0.0, "fail_dist": 4,
                 "fail_params": [1000.0, 0.0, 0.0], "repair_dist": 19,
                 "repair_params": [10.0, 0.0, 0.0]},
            ],
            "nodes": [
                {"id": 3, "name": "start", "type": 1, "x": -50.0, "y": 0.0},
                {"id": 4, "name": "end", "type": 3, "x": 150.0, "y": 0.0},
            ],
            "hierarchies": [],
        },
        "links": [
            {"id": 5, "from": ["Node", 3], "to": ["Block", 1]},
            {"id": 6, "from": ["Block", 1], "to": ["Block", 2]},
            {"id": 7, "from": ["Block", 2], "to": ["Node", 4]},
        ],
    }))
    for block, more in zip(doc["elements"]["blocks"],
                           extra.pop("blocks", [{}, {}]), strict=True):
        block.update(more)
    doc.update(extra)
    return parse_rbds(json.dumps(doc), default_name="twins")


def _pool(name):
    return {"name": name, "stock": 2, "new_spares": 1, "arrive_every": 720.0,
            "eso": 1, "emer_time": 24.0, "slo": 0, "level": 0, "quantity": 1,
            "slo_time": 24.0, "rso": 0, "spare_cost": 1.0,
            "emer_ship_cost": 1.0}


def test_blocks_on_different_pools_keep_their_own_spares(tmp_path):
    """Sharing one failure model linked it to both pools."""
    rbd = _twins(spare_pools=[_pool("P1"), _pool("P2")], blocks=[
        {"sparing_type": "Pooled", "spare_pool": "P1", "quantity": 1},
        {"sparing_type": "Pooled", "spare_pool": "P2", "quantity": 1}])
    export_awb(rbd, tmp_path / "pools.awb", sidecar=False)
    tables = read_awb(tmp_path / "pools.awb")
    assert len(tables["FailureModels"].rows) == 2
    links = [(r["FailureModel"], r["Spare"])
             for r in tables["FailureModelCorrTaskSpares"].rows]
    assert sorted(links) == [(0, 0), (1, 1)]


def test_blocks_on_the_same_pool_still_share_one_model(tmp_path):
    rbd = _twins(spare_pools=[_pool("P1")], blocks=[
        {"sparing_type": "Pooled", "spare_pool": "P1", "quantity": 1},
        {"sparing_type": "Pooled", "spare_pool": "P1", "quantity": 1}])
    export_awb(rbd, tmp_path / "shared.awb", sidecar=False)
    tables = read_awb(tmp_path / "shared.awb")
    assert len(tables["FailureModels"].rows) == 1
    assert len(tables["FailureModelCorrTaskSpares"].rows) == 1


def test_a_different_pm_is_not_lost_to_a_shared_model(tmp_path):
    pm = {"uses_pm": True, "pm_duration_dist": 19,
          "pm_params": [2.0, 0.0, 0.0]}
    rbd = _twins(blocks=[dict(pm, pm_freq=500.0), dict(pm, pm_freq=900.0)])
    report = export_awb(rbd, tmp_path / "pm.awb", sidecar=False)
    tables = read_awb(tmp_path / "pm.awb")
    intervals = sorted(r["TaskInterval"]
                       for r in tables["FailureModelScheduledTasks"].rows)
    assert intervals == [500.0, 900.0]
    assert not [x for x in report.not_in_target if "PM" in x.feature]


def test_failure_model_descriptions_name_their_block(tmp_path):
    export_awb(_model(), tmp_path / "d.awb", sidecar=False)
    names = [r["Description"]
             for r in read_awb(tmp_path / "d.awb")["FailureModels"].rows]
    assert names == ["Failure model for Pump", "Failure model for Valve"]


def test_a_hierarchy_with_a_missing_parent_is_reported_not_a_crash(tmp_path):
    rbd = _model()
    from raptor.core.fileio.rbd import RbdSection
    rbd.sections["HIERARCHY_DATA"] = RbdSection(
        "HIERARCHY_DATA",
        ["ID", "hierarchyName", "Parent", "xPos", "yPos", "level"], rows=[
        {"ID": "10", "hierarchyName": "Lost", "Parent": "999",
         "xPos": "0", "yPos": "0", "level": "1"}])
    report = export_awb(rbd, tmp_path / "orphan.awb", sidecar=False)
    assert any("not in the model" in w for w in report.warnings)
    rows = read_awb(tmp_path / "orphan.awb")["RbdBlocks"].rows
    assert [r["Page"] for r in rows if r["Description"] == "Lost"] == [0]


def test_an_exponential_location_is_reported_as_dropped(tmp_path):
    rbd = _model()
    _rows(rbd, "BLOCK_DATA")[0]["FParam2"] = "50.0"
    report = export_awb(rbd, tmp_path / "loc.awb", sidecar=False)
    features = [x.feature for x in report.not_in_target]
    assert "exponential failure location (delay) dropped" in features
    assert not any("simplified to exponential" in f for f in features)


def test_a_short_file_cannot_claim_millions_of_rows(tmp_path):
    """20 bytes used to build 10 million empty rows."""
    from raptor.core.fileio.awb import _write_string
    out = bytearray(struct.pack("<I", 1))
    _write_string(out, "Project")
    out += struct.pack("<I", 0) + struct.pack("<I", 10_000_000)
    path = tmp_path / "bomb.awb"
    path.write_bytes(bytes(out))
    with pytest.raises(AwbFormatError, match="bad row count"):
        read_awb(path)


def test_format_errors_name_the_table_and_byte(tmp_path):
    path = tmp_path / "bad.awb"
    path.write_bytes(struct.pack("<I", 1) + b"\x07Project"
                     + struct.pack("<I", 1) + b"\x02Id" + struct.pack("<I", 9))
    with pytest.raises(AwbFormatError, match=r"table 'Project', byte \d+"):
        read_awb(path)


def test_cli_dry_run_does_not_write_the_reduced_model(tmp_path):
    """#288: the reduced model was written before the dry-run check."""
    from raptor.cli.main import main
    src = tmp_path / "model.rbds"
    save_rbds(_model(), str(src))
    dest = tmp_path / "model.awb"
    norm = tmp_path / "reduced.rbds"
    assert main(["convert", str(src), str(dest), "--dry-run",
                 "--normalize-out", str(norm), "-f", "json"]) == 0
    assert not norm.exists() and not dest.exists()


def test_awb_branches_without_nodes_get_junction_nodes(tmp_path):
    """AWB draws parallel paths with no node: several connections leave
    the start and meet at the end. RAPTOR splits and joins only at a node,
    the GUI flagged every such import, and the engine got the logic wrong
    (an AWB example came out more available than a block on every path)."""
    from raptor.api.validate import issues
    path = tmp_path / "parallel.awb"
    export_awb(_model(), path)
    tables = read_awb(path)
    template = tables["RbdConnections"].rows[0]
    blocks = [i for i, b in enumerate(tables["RbdBlocks"].rows) if b["Id"] in ("Pump", "Valve")]
    block_type = template["OutputObjectType"]
    node_type = next(c["InputObjectType"] for c in tables["RbdConnections"].rows
                     if c["InputObjectType"] != block_type)
    nodes = {n["Id"]: i for i, n in enumerate(tables["RbdNodes"].rows)}
    start, end = nodes[min(nodes)], nodes[max(nodes)]
    rows = []
    for b in blocks:
        rows.append(dict(template, Id=f"S{b}", InputObjectType=node_type, InputObjectIndex=start,
                         OutputObjectType=block_type, OutputObjectIndex=b))
        rows.append(dict(template, Id=f"E{b}", InputObjectType=block_type, InputObjectIndex=b,
                         OutputObjectType=node_type, OutputObjectIndex=end))
    tables["RbdConnections"].rows = rows
    write_awb(path, tables)
    rbd, _report = import_awb(path)
    # "Only one link can go from a Start node", "A block can only connect ..."
    assert [str(i) for i in issues(rbd)
            if "link" in i.message.lower() or "connect" in i.message.lower()] == []
    junctions = [n for n in rbd.sections["NODE_DATA"].rows if n["Type"] == "2"]
    assert len(junctions) == 2
    assert all(n["k"] == "1" for n in junctions)        # any one path through

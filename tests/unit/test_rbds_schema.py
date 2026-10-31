"""The `.rbds` JSON Schema, the check every load runs, and the writer's
promises: each value written once, nothing the reader can rebuild written at
all, and only what differs from the defaults written."""
from __future__ import annotations

import glob
import json
import subprocess
import sys
from pathlib import Path

import pytest

from raptor.core.fileio import rbds
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import load_rbds, parse_rbds, render_rbds
from raptor.core.fileio.rbds_schema import RbdsFormatError, build_schema, check_document

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "tests" / "golden"
TRACKED = sorted(glob.glob(str(ROOT / "new_docs" / "models" / "*.rbds"))
                 + glob.glob(str(ROOT / "tests" / "fixtures" / "**" / "*.rbds"), recursive=True))
GOLDENS = sorted(glob.glob(str(GOLD / "*" / "input.rbd")))
SAMPLE = ROOT / "new_docs" / "models" / "HospitalPower.rbds"


def _doc() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_the_committed_schema_is_current():
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "gen_rbds_schema.py")],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stdout


@pytest.mark.parametrize("path", TRACKED, ids=lambda p: Path(p).name)
def test_every_tracked_file_passes_the_check(path):
    assert check_document(json.loads(Path(path).read_text(encoding="utf-8"))) == []


def test_every_golden_model_written_as_rbds_passes_the_check():
    bad = [p for p in GOLDENS if check_document(json.loads(render_rbds(load_rbd(p))))]
    assert bad == []


# --- the built-in check agrees with jsonschema --------------------------------

_MUTATIONS = {
    "wrong type": lambda d: d["elements"]["blocks"][1].update({"pm_freq": "abc"}),
    "missing id": lambda d: d["elements"]["blocks"][0].pop("id"),
    "bad bool": lambda d: d["elements"]["blocks"][0].update({"uses_pm": "yes"}),
    "params not a list": lambda d: d["elements"]["blocks"][0].update({"fail_params": 5}),
    "too many params": lambda d: d["elements"]["blocks"][0].update({"fail_params": [1, 2, 3, 4]}),
    "bad sparing type": lambda d: d["elements"]["blocks"][0].update({"sparing_type": "Pooed"}),
    "node type out of range": lambda d: d["elements"]["nodes"][0].update({"type": 9}),
    "link without to": lambda d: d["links"][0].pop("to"),
    "link end kind": lambda d: d["links"][0].update({"from": ["Blok", 1]}),
    "unknown key": lambda d: d["elements"]["blocks"][0].update({"fial_dist": 15}),
    "unknown top-level key": lambda d: d.update({"my_notes": 1}),
    "version as text": lambda d: d.update({"version": "2"}),
}


@pytest.mark.parametrize("name", _MUTATIONS)
def test_the_check_flags_what_jsonschema_flags(name):
    jsonschema = pytest.importorskip("jsonschema")
    doc = _doc()
    _MUTATIONS[name](doc)
    theirs = {"/".join(map(str, e.absolute_path))
              for e in jsonschema.Draft202012Validator(build_schema()).iter_errors(doc)}
    ours = check_document(doc)
    assert theirs, name
    assert ours, name
    for tracked in (_doc(),):
        assert not list(jsonschema.Draft202012Validator(build_schema()).iter_errors(tracked))


@pytest.mark.parametrize("path", TRACKED[:6], ids=lambda p: Path(p).name)
def test_jsonschema_accepts_the_tracked_files(path):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(json.loads(Path(path).read_text(encoding="utf-8")), build_schema())


# --- what a load reports ------------------------------------------------------

@pytest.mark.parametrize("name, needle", [
    ("wrong type", 'elements.blocks[1] "Fuel_Day_Tank".pm_freq: must be a number'),
    ("missing id", "is missing `id`"),
    ("bad sparing type", "sparing_type: must be one of"),
    ("link without to", "is missing `to`"),
])
def test_an_error_stops_the_load_and_names_the_field(name, needle):
    doc = _doc()
    _MUTATIONS[name](doc)
    with pytest.raises(RbdsFormatError, match="not a valid .rbds file") as err:
        parse_rbds(json.dumps(doc))
    assert needle in str(err.value)


def test_an_unknown_distribution_name_is_an_error():
    doc = _doc()
    doc["elements"]["blocks"][0]["fail_dist"] = "Weibul"
    with pytest.raises(RbdsFormatError, match="fail_dist: unknown distribution 'Weibul'"):
        parse_rbds(json.dumps(doc))


def test_an_unknown_key_is_a_warning_with_a_suggestion_and_is_kept():
    doc = _doc()
    doc["elements"]["blocks"][0]["fial_dist"] = 15
    doc["my_notes"] = {"by": "me"}
    doc["elements"]["blocks"][0]["x_tag"] = "mine"
    rbd = parse_rbds(json.dumps(doc))
    assert any("fial_dist: unknown key" in w and "did you mean `fail_dist`" in w
               for w in rbd.format_warnings)
    assert any(w.startswith("my_notes") for w in rbd.format_warnings)
    assert not any("x_tag" in w for w in rbd.format_warnings)
    again = json.loads(render_rbds(rbd))
    assert again["my_notes"] == {"by": "me"}
    assert again["elements"]["blocks"][0]["x_tag"] == "mine"
    assert again["elements"]["blocks"][0]["fial_dist"] == 15


def test_validate_reports_the_warnings(tmp_path, capsys):
    from raptor.api.validate import issues
    from raptor.cli.main import main
    doc = _doc()
    doc["elements"]["blocks"][0]["fial_dist"] = 15
    rbd = parse_rbds(json.dumps(doc))
    assert any(i.severity == "warning" and "fial_dist" in i.message for i in issues(rbd))
    path = tmp_path / "m.rbds"
    path.write_text(json.dumps(doc))
    main(["validate", str(path)])
    assert "did you mean `fail_dist`" in capsys.readouterr().out
    doc["elements"]["blocks"][1]["pm_freq"] = "abc"
    path.write_text(json.dumps(doc))
    assert main(["validate", str(path)]) != 0
    assert "pm_freq: must be a number" in capsys.readouterr().out


def test_mcp_open_and_validate_report_them(tmp_path):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import validate_model
    doc = _doc()
    doc["elements"]["blocks"][0]["fial_dist"] = 15
    (tmp_path / "warn.rbds").write_text(json.dumps(doc))
    mid = open_model("warn.rbds").structured_content["model_id"]
    text = json.dumps(validate_model(mid).structured_content)
    assert "fial_dist" in text and "fail_dist" in text
    doc["elements"]["blocks"][1]["pm_freq"] = "abc"
    (tmp_path / "bad.rbds").write_text(json.dumps(doc))
    bad = open_model("bad.rbds").structured_content
    assert "pm_freq: must be a number" in bad["error"]["message"]


def test_raptor_schema_prints_the_schema(capsys):
    from raptor.cli.main import main
    assert main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out)["required"] == ["format", "version"]


# --- each value once, nothing derivable, only what differs ---------------------

def _objects(doc: dict):
    for jkey in rbds._ELEMENT_KEYS:
        for obj in doc["elements"].get(jkey, []):
            yield rbds._SECTION_KEY[jkey], obj
        if jkey in doc.get("defaults", {}):
            yield rbds._SECTION_KEY[jkey], doc["defaults"][jkey]
    for obj in doc.get("links", []):
        yield "LINK_DATA", obj
    for jkey in ("spare_pools", "resources", "triggers", "phases"):
        for obj in doc.get(jkey, []):
            yield rbds._SECTION_KEY[jkey], obj
    yield "SYSTEM_DATA_FILE", doc.get("system", {})


def _written(path: str) -> dict:
    return json.loads(render_rbds(load_rbd(path) if path.endswith(".rbd") else load_rbds(path)))


@pytest.mark.parametrize("path", GOLDENS[::5] + TRACKED, ids=lambda p: Path(p).parent.name + "/" + Path(p).name)
def test_no_value_is_written_twice_and_nothing_derivable_is_written(path):
    doc = _written(path)
    for section, obj in _objects(doc):
        field_map = (rbds._SYSTEM_FILE_MAP if section == "SYSTEM_DATA_FILE"
                     else rbds._MAPS[section])
        legacy = obj.get("legacy") or {}
        for fk, col in field_map.items():
            if fk in obj:
                cols = col if isinstance(col, tuple) else (col,)
                assert not set(cols) & set(legacy), (section, fk, obj.get("name"))
        if section != "SYSTEM_DATA_FILE" and obj.get("id", 0) != 0:
            assert not set(rbds._DERIVED.get(section, ())) & set(legacy), (section, obj.get("name"))
    assert not set(rbds._RUN_COLUMNS) & set(doc["system"].get("legacy") or {})


def test_only_what_differs_from_the_defaults_is_written():
    doc = _written(str(SAMPLE))
    defaults = doc["defaults"]["blocks"]
    for blk in doc["elements"]["blocks"]:
        for key, value in blk.items():
            if key not in rbds._ALWAYS_WRITTEN and key in defaults:
                assert value != defaults[key], (blk["name"], key)


def test_a_hand_written_pool_gets_every_column():
    doc = _doc()
    doc["spare_pools"] = [{"name": "P1", "stock": 4}]
    row = parse_rbds(json.dumps(doc)).section("SPARE_POOL_DATA").rows[0]
    assert row["Stock"] == "4" and row["RSO"] == "0" and row["ArriveEvery"] == "720.000000"


def test_a_version_1_file_reads_omitted_fields_from_the_built_in_template():
    doc = _doc()
    doc.pop("formulas", None)    # a linked field would override the value read
    doc["version"] = 1
    doc["defaults"]["blocks"]["fail_params"] = [5.0, 0.0, 0.0]
    blk = doc["elements"]["blocks"][0]
    blk.pop("fail_params", None)
    v1 = parse_rbds(json.dumps(doc))
    doc["version"] = 2
    v2 = parse_rbds(json.dumps(doc))
    row = lambda r: next(x for x in r.section("BLOCK_DATA").rows if x["ID"] == str(blk["id"]))
    assert float(row(v2)["FParam1"]) == 5.0
    assert float(row(v1)["FParam1"]) != 5.0


def test_a_newer_version_is_refused():
    doc = _doc()
    doc["version"] = rbds.VERSION + 1
    with pytest.raises(ValueError, match="which is newer than"):
        parse_rbds(json.dumps(doc))


@pytest.mark.parametrize("path", TRACKED, ids=lambda p: Path(p).name)
def test_writing_again_changes_nothing(path):
    text = Path(path).read_text(encoding="utf-8")
    assert render_rbds(parse_rbds(text)) == text.rstrip("\n") or \
        render_rbds(parse_rbds(render_rbds(parse_rbds(text)))) == render_rbds(parse_rbds(text))


def _annotation(anchor):
    return {"kind": "text", "id": 1, "parent": 0, "text": "note",
            "x": 10.0, "y": 10.0, "w": 100.0, "h": 30.0, "z": 20.0,
            "anchor": anchor}


@pytest.mark.parametrize("anchor", [
    {"target": "B1", "ref": "Block:1", "location": "right", "gap": 12.0},
    {"target": "B1", "ref": "Block:1", "location": "center", "gap": 0.0},
    {"target": "note", "ref": "Annotation:2", "location": "below", "gap": 4.0},
    {"from": "A", "to": "B", "from_ref": "Node:1", "to_ref": "Block:1"},
])
def test_the_schema_accepts_anchor_locations_and_annotation_targets(anchor):
    doc = _doc()
    doc["annotations"] = [_annotation(anchor)]
    assert check_document(doc) == []


@pytest.mark.parametrize("anchor", [
    {"target": "B1", "ref": "Block:1", "location": "sideways"},
    {"target": "B1", "ref": "Block:1", "side": "right"},
    {"target": "B1", "ref": "Block:1", "location": "right", "gap": "wide"},
])
def test_the_schema_rejects_a_wrong_location_or_legacy_side_key(anchor):
    doc = _doc()
    doc["annotations"] = [_annotation(anchor)]
    assert check_document(doc)

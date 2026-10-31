"""`.rbds` format: byte-exact round-trips, cross-format equivalence
(simulation results identical through either pathway), and minimal
hand-authored (AI-style) files."""

from __future__ import annotations

import json
import os
import pathlib

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import (_DERIVED, _RUN_COLUMNS, FORMAT_ID, load_rbds,
                                     parse_rbds, render_rbds)

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")

FIXTURES = sorted(d for d in os.listdir(GOLD)
                  if os.path.exists(os.path.join(GOLD, d, "input.rbd")))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return text


def _sections_equal(a, b):
    if set(a.sections) != set(b.sections):
        return False, "section set"
    for name, sec in a.sections.items():
        ra, rb = sec.rows, b.sections[name].rows
        if len(ra) != len(rb):
            return False, f"{name} row count {len(ra)}!={len(rb)}"
        for i, (x, y) in enumerate(zip(ra, rb)):
            if str(x.get("ID")) != "0":
                # Computed on read, never stored (link counts, parameter
                # counts): real files sometimes over-declare them
                # (Exponential with 3 parameters), which `.rbds` does not
                # keep. `test_computed_columns_follow_the_model` checks them.
                skip = set(_DERIVED.get(name, ()))
                if name == "BLOCK_DATA":
                    skip -= {c for d, c in (("FDist", "#FParam"),
                                            ("RDist", "#RParam"))
                             if x.get(d) == "16"}
                x = {k: v for k, v in x.items() if k not in skip}
                y = {k: v for k, v in y.items() if k not in skip}
            if name == "SYSTEM_DATA":
                # `imageString` is the one column `.rbds` does not carry (the system-status picture
                # is not part of this product); reading fills the template's default.
                x = {k: v for k, v in x.items() if k != "imageString"}
                y = {k: v for k, v in y.items() if k != "imageString"}
                # The run settings live once, in `experiment`, and are written
                # back in Raptor 7's own spelling ("1" comes back "1.000000"):
                # the same value, which Raptor 7 reads with ReadReal.
                for col in _RUN_COLUMNS:
                    if col in x and col in y and _num(x[col]) == _num(y[col]):
                        y = {**y, col: x[col]}
            if dict(x) != dict(y):
                diff = {k: (x.get(k), y.get(k))
                        for k in set(x) | set(y)
                        if x.get(k) != y.get(k)}
                return False, f"{name}[{i}]: {list(diff.items())[:3]}"
    return True, ""


@pytest.mark.parametrize("name", FIXTURES)
def test_rbds_roundtrip_byte_exact(name):
    rbd = load_rbd(os.path.join(GOLD, name, "input.rbd"))
    back = parse_rbds(render_rbds(rbd))
    ok, why = _sections_equal(rbd, back)
    assert ok, f"{name}: {why}"


@pytest.mark.parametrize("name", ["TwoBlockRedundancy",
                                  "HierarchyExample"])
def test_rbds_simulation_equivalence(name):
    """A model simulated from `.rbd` and from `.rbds` produces identical
    reports and logs (deterministic engine)."""
    rbd = load_rbd(os.path.join(GOLD, name, "input.rbd"))
    rbds_model = parse_rbds(render_rbds(rbd))
    rbds_model.file_name = rbd.file_name   # headers embed the name
    _, _, _fin_legacy, _rep_l = (*run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=[]), [])
    sink_a, sink_b = [], []
    la, _eoa, fia = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=sink_a)
    lb, _eob, fib = run_simulation_with_reports(
        rbds_model, num_runs=2, date="fixed", report_sink=sink_b)
    assert fia == fib
    assert la == lb
    assert [r.availability for r in sink_a] == \
        [r.availability for r in sink_b]


def test_minimal_ai_authored_file_loads_and_runs():
    """The spec's minimal example: a model authored from the markdown
    alone, with no legacy columns."""
    doc = {
        "format": FORMAT_ID,
        "version": 1,
        "elements": {
            "blocks": [
                {"id": 1, "name": "PSU", "fail_dist": 15,
                 "fail_params": [1200, 2.0, 0]},
                {"id": 2, "name": "Fan", "fail_dist": 4,
                 "fail_params": [800, 0, 0]},
            ],
            "nodes": [
                {"id": 3, "type": 1, "name": "start"},
                {"id": 4, "type": 2, "name": "join"},
                {"id": 5, "type": 3, "name": "end"},
            ],
        },
        "links": [
            {"id": 1, "from": ["Node", 3], "to": ["Block", 1]},
            {"id": 2, "from": ["Block", 1], "to": ["Block", 2]},
            {"id": 3, "from": ["Block", 2], "to": ["Node", 5]},
        ],
        "system": {"num_runs": 3, "units": "hours"},
    }
    text = json.dumps(doc)
    model = parse_rbds(text)
    ids = {r["ID"] for r in model.sections["BLOCK_DATA"].rows}
    assert {"1", "2"} <= ids
    logs, _eor, fin = run_simulation_with_reports(model, num_runs=1,
                                                 date="fixed")
    assert len(logs) == 1
    # availability parses out of the final results
    avail_line = next(l for l in fin if l.startswith("Availability"))
    float(avail_line.split()[1])


def test_unknown_keys_preserved():
    doc = {
        "format": FORMAT_ID, "version": 1,
        "elements": {"blocks": [
            {"id": 1, "name": "B", "future_field": {"a": [1, 2]},
             "legacy": {"ID": "1"}}]},
    }
    back_text = render_rbds(parse_rbds(json.dumps(doc)))
    assert "future_field" in back_text


def test_rejects_wrong_format_and_future_version():
    with pytest.raises(ValueError, match="not an"):
        parse_rbds(json.dumps({"format": "other", "version": 1}))
    with pytest.raises(ValueError, match="format version 99, which is newer than"):
        parse_rbds(json.dumps({"format": FORMAT_ID, "version": 99,
                               "meta": {"generator": "RAPTOR Studio 9.9.9"}}))
    with pytest.raises(ValueError, match="saved by RAPTOR Studio 9.9.9.*saved as .rbd"):
        parse_rbds(json.dumps({"format": FORMAT_ID, "version": 99,
                               "meta": {"generator": "RAPTOR Studio 9.9.9"}}))


def test_gui_save_path_writes_and_reads_rbds(qapp, tmp_path):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "legacy.rbd")
    target = tmp_path / "model.rbds"
    from raptor.core.fileio.rbds import save_rbds
    w._diagram_dock.view.write_back_to_rbd(w._rbd)
    save_rbds(w._rbd, str(target))
    reloaded = load_rbds(str(target))
    ok, why = _sections_equal(w._rbd, reloaded)
    assert ok, why


def test_rates_and_formulas_round_trip(qapp, tmp_path):
    """The Rates library and per-field formula links are `.rbds`-only,
    alongside annotations, and must survive a save/reload exactly like
    them."""
    from raptor.gui.ui.main_window import MainWindow
    from raptor.core.fileio.rbds import save_rbds
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "legacy.rbd")
    bid = next(iter(w._diagram_dock.view.blocks))
    addr = f"block:{bid}:FParam1"
    w._rbd.rates.append({
        "name": "Highway",
        "expr": "0.00015 * 20000 / 8760  # 0.00015 fail/km, 20000 km/yr",
        "value": 0.00015 * 20000 / 8760,
    })
    w._rbd.formulas[addr] = {"rate": "Highway",
                             "value": 0.00015 * 20000 / 8760}

    target = tmp_path / "rates.rbds"
    w._diagram_dock.view.write_back_to_rbd(w._rbd)
    save_rbds(w._rbd, str(target))
    reloaded = load_rbds(str(target))

    assert len(reloaded.rates) == 1
    assert reloaded.rates[0]["name"] == "Highway"
    assert abs(reloaded.rates[0]["value"] - 0.00015 * 20000 / 8760) < 1e-9
    assert reloaded.formulas.get(addr, {}).get("rate") == "Highway"


def test_legacy_save_confirms_and_converts_rates(qapp, tmp_path, monkeypatch):
    """Legacy `.rbd` cannot hold rates/formulas, so saving converts them
    after asking the user to confirm. Declining cancels the save outright
    (no file written); accepting writes normally. The field's own value is
    already a plain float in `sections`, so converting only drops the
    formula/rate record -- the saved numbers are unchanged."""
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "legacy.rbd")
    bid = next(iter(w._diagram_dock.view.blocks))
    w._rbd.rates.append({"name": "Highway", "expr": "1+1", "value": 2.0})
    w._rbd.formulas[f"block:{bid}:FParam1"] = {"rate": "Highway",
                                               "value": 2.0}

    # Every confirmation goes through dialogs.question_box, imported by
    # each caller at call time, so a headless test stubs that one
    # function. The confirm offers "Continue, save plain values" /
    # "Cancel" rather than bare Yes/No.
    legacy_path = tmp_path / "declined.rbd"
    monkeypatch.setattr("raptor.gui.ui.dialogs.question_box",
                        lambda *a, **kw: False)
    assert w._write_model(str(legacy_path)) is False
    assert not legacy_path.exists()

    accepted_path = tmp_path / "accepted.rbd"
    monkeypatch.setattr("raptor.gui.ui.dialogs.question_box",
                        lambda *a, **kw: True)
    assert w._write_model(str(accepted_path)) is True
    assert accepted_path.exists()
    reloaded_legacy = load_rbd(str(accepted_path))
    assert getattr(reloaded_legacy, "rates", []) == []
    assert getattr(reloaded_legacy, "formulas", {}) == {}


def test_display_unit_round_trips_rbds_and_drops_from_legacy(
        qapp, tmp_path, monkeypatch):
    """The display-unit override is `.rbds`-only, like
    rates/formulas/annotations: it round-trips through save/reload and is
    dropped with a non-blocking warning on legacy save, since losing a
    display preference is low-stakes compared with losing a formula."""
    from PySide6.QtWidgets import QMessageBox
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "legacy.rbd")
    w._inspector.set_display_unit("years")
    assert w._rbd.display_unit == "years"

    rbds_path = tmp_path / "units.rbds"
    assert w._write_model(str(rbds_path)) is True
    reloaded = load_rbds(str(rbds_path))
    assert reloaded.display_unit == "years"

    warns = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **kw: (warns.append(a[2] if len(a) > 2 else ""),
                          QMessageBox.Ok)[1])
    legacy_path = tmp_path / "units.rbd"
    assert w._write_model(str(legacy_path)) is True
    assert any("display unit" in msg.lower() for msg in warns), warns
    legacy_loaded = load_rbd(str(legacy_path))
    assert getattr(legacy_loaded, "display_unit", None) is None


def test_a_phased_model_keeps_its_phase_count_through_rbds_and_back_to_rbd():
    """The `.rbds` reader used to leave the header's `Phases=` at the template's 0, so a phased
    model rendered back to `.rbd` (as the parallel workers and File > Export do) claimed no phases
    over PHASES rows and real Raptor reads the file wrongly."""
    from raptor.core.fileio.rbd import load_rbd, parse_rbd, render_rbd
    original = load_rbd("tests/golden/Phasing1/input.rbd")
    text = render_rbd(parse_rbds(render_rbds(original)))
    assert "Phases=       3" in text
    again = parse_rbd(text)
    assert again.counts["Phases"] == 3
    assert [r["PhaseName"] for r in again.sections["PHASES"].rows] == \
        [r["PhaseName"] for r in original.sections["PHASES"].rows]


def test_render_rbd_counts_phases_from_the_rows_not_a_stale_header():
    from raptor.core.fileio.rbd import load_rbd, render_rbd
    original = load_rbd("tests/golden/Phasing1/input.rbd")
    original.counts["Phases"] = 0
    assert "Phases=       3" in render_rbd(original)


def test_rbds_does_not_store_the_status_picture_but_a_rbd_made_from_it_still_has_one():
    """`imageString` (the original's system-status picture) is dropped on write; reading puts the
    template's default back so a `.rbd` written from the file still opens in real Raptor. A legacy
    `.rbd` opened and saved as `.rbd` keeps whatever value it had."""
    import json
    rbd = load_rbd(os.path.join(GOLD, "MotorDemo", "input.rbd"))
    original = rbd.section("SYSTEM_DATA").rows[0]["imageString"]
    text = render_rbds(rbd)
    assert "imageString" not in text and original
    back = parse_rbds(text)
    restored = back.section("SYSTEM_DATA").rows[0]["imageString"]
    assert restored
    assert rbd.section("SYSTEM_DATA").rows[0]["imageString"] == original   # in memory, unchanged
    assert "imageString" not in json.loads(text)["system"]["legacy"]


# --- the layout: each value once, friendly fields editable ----------------

NEW_LAYOUT = REPO / "new_docs" / "models" / "HospitalPower.rbds"


def _rows(rbd, section):
    return [{k: v for k, v in r.items() if not k.startswith("_")}
            for r in rbd.section(section).rows]


def _block(rbd, name):
    return next(r for r in rbd.section("BLOCK_DATA").rows
                if r.get("blockName") == name)


def test_editing_a_friendly_field_by_hand_takes_effect():
    """The old reader let `legacy` win, so a hand or AI edit of a
    parameter, a name or the trials was silently thrown away."""
    doc = json.loads(NEW_LAYOUT.read_text(encoding="utf-8"))
    blk = next(b for b in doc["elements"]["blocks"]
               if b.get("name") == "Utility_Grid")
    blk["repair_params"][0] = 999.0       # (its failure mean follows a rate)
    blk["name"] = "Grid"
    doc["experiment"]["trials"] = 777
    rbd = parse_rbds(json.dumps(doc))
    row = _block(rbd, "Grid")
    assert float(row["RParam1"]) == 999.0
    assert int(float(rbd.section("SYSTEM_DATA").rows[0]["NumRuns"])) == 777
    assert rbd.experiment["trials"] == 777


def test_each_value_is_stored_once():
    doc = json.loads(render_rbds(load_rbds(str(NEW_LAYOUT))))
    blk = doc["elements"]["blocks"][0]
    for col in ("blockName", "ID", "FDist", "FParam1", "DepType", "xPos"):
        assert col not in blk.get("legacy", {}), col
    assert "NumRuns" not in doc["system"].get("legacy", {})
    assert "num_runs" not in doc["system"]           # the experiment owns it
    assert doc["experiment"]["trials"] > 0
    link = doc["links"][0]
    assert "FromID" not in link.get("legacy", {}) and "from" in link


def test_unmapped_columns_are_edited_in_legacy():
    doc = json.loads(NEW_LAYOUT.read_text(encoding="utf-8"))
    hier = doc["elements"]["hierarchies"][0]
    hier["legacy"]["zoom"] = "42.00"
    rows = parse_rbds(json.dumps(doc)).section("HIERARCHY_DATA").rows
    assert next(r for r in rows if r["ID"] == str(hier["id"]))["zoom"] == "42.00"


#: Maintenance, sparing and the other columns the API names, as a hand-written
#: file spells them, and the Raptor 7 column each must land in.
_API_NAMED = {
    "uses_pm": (True, "usesPM", "1"), "pm_freq": (500.0, "PMFreq", "500.000000"),
    "pm_stagger_time": (25.0, "PMStaggerTime", "25.000000"),
    "pm_duration_dist": ("Normal", "Dist", "8"), "pm_trigger": ("T1", "pmTrig", "T1"),
    "sparing_type": ("Custom", "SpType", "Custom"), "stock": (3, "Stock", "3"),
    "arrive_every": (100.0, "ArriveEvery", "100.000000"),
    "pre_ldt_dist": ("Exponential", "Pre-LDTDist", "4"),
    "resource_name": ("Crew", "Resource1Name", "Crew"),
    "standby_stress": (0.25, "SBStress", "0.250"), "initial_cost": (99.0, "initial", "99.00"),
}


def test_the_api_names_are_read_from_a_hand_written_block():
    doc = json.loads(NEW_LAYOUT.read_text(encoding="utf-8"))
    blk = next(b for b in doc["elements"]["blocks"] if b.get("name") == "Utility_Grid")
    blk.pop("legacy", None)
    for key, (value, _col, _cell) in _API_NAMED.items():
        blk[key] = value
    row = _block(parse_rbds(json.dumps(doc)), "Utility_Grid")
    for key, (_value, col, cell) in _API_NAMED.items():
        assert row[col] == cell, key


def test_maintenance_and_sparing_are_written_as_friendly_fields():
    rbd = load_rbds(str(NEW_LAYOUT))
    row = next(r for r in rbd.section("BLOCK_DATA").rows if r["ID"] != "0")
    row.update({"usesPM": "1", "PMFreq": "777.000000", "Stock": "7", "SpType": "Custom"})
    blk = json.loads(render_rbds(rbd))["elements"]["blocks"][0]
    assert "legacy" not in blk
    assert blk["uses_pm"] is True and blk["pm_freq"] == 777.0
    assert blk["stock"] == 7 and blk["sparing_type"] == "Custom"


def test_numbers_are_written_as_numbers():
    rbd = load_rbds(str(NEW_LAYOUT))
    row = next(r for r in rbd.section("BLOCK_DATA").rows if r["ID"] != "0")
    row.update({"FDist": "15", "RDist": "8", "FStream": "17", "DepNum": "3", "Phasing": "1"})
    blk = json.loads(render_rbds(rbd))["elements"]["blocks"][0]
    for key in ("id", "parent", "fail_dist", "repair_dist", "fail_stream", "dep_num"):
        assert isinstance(blk[key], int), key
    assert blk["uses_phasing"] is True
    assert json.loads(render_rbds(rbd))["meta"]["generator"].startswith("RAPTOR Studio")


def test_the_defaults_row_is_not_an_element_and_keeps_its_place():
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    ids = [r["ID"] for r in rbd.section("BLOCK_DATA").rows]
    doc = json.loads(render_rbds(rbd))
    assert all(b["id"] != 0 for b in doc["elements"]["blocks"])
    assert "blocks" in doc["defaults"]
    back = [r["ID"] for r in parse_rbds(json.dumps(doc)).section(
        "BLOCK_DATA").rows]
    assert back == ids


def test_a_hand_written_file_gets_its_path_counts_from_the_links():
    """Every node's `n` and `Out` must match its links or Raptor 7 crashes;
    a hand-written file need not spell them out."""
    from raptor.api.validate import issues
    doc = {"format": FORMAT_ID, "version": 1,
           "elements": {
               "blocks": [{"id": 1, "name": "A", "fail_dist": "Weibull",
                           "fail_params": [1000, 1.5, 0]},
                          {"id": 2, "name": "B", "dep_type": "RBDBlock",
                           "dep_num": 1, "dep_idle": 100}],
               "nodes": [{"id": 3, "type": 1, "name": "start"},
                         {"id": 4, "type": 2, "name": "split"},
                         {"id": 5, "type": 2, "name": "join", "k": 1,
                          "full_flow": 0},
                         {"id": 6, "type": 3, "name": "end"}]},
           "links": [{"id": 1, "from": ["Node", 3], "to": ["Node", 4]},
                     {"id": 2, "from": ["Node", 4], "to": ["Block", 1]},
                     {"id": 3, "from": ["Node", 4], "to": ["Block", 2]},
                     {"id": 4, "from": ["Block", 1], "to": ["Node", 5]},
                     {"id": 5, "from": ["Block", 2], "to": ["Node", 5],
                      "nom_flow": 5, "cap_pri": 7},
                     {"id": 6, "from": ["Node", 5], "to": ["Node", 6]}]}
    rbd = parse_rbds(json.dumps(doc))
    nodes = {r["Name"]: r for r in rbd.section("NODE_DATA").rows}
    assert (nodes["join"]["n"], nodes["split"]["Out"]) == ("2", "2")
    assert nodes["join"]["fullflow"] == "0"
    assert _block(rbd, "A")["FDist"] == "15"
    b = _block(rbd, "B")
    assert (b["DepType"], b["DepNum"]) == ("RBDBlock", "1")
    link5 = next(r for r in rbd.section("LINK_DATA").rows if r["ID"] == "5")
    assert (link5["nomFlow"], link5["CapPri"]) == ("5", "7")
    assert not [i for i in issues(rbd) if i.severity == "error"], issues(rbd)


def test_an_unknown_distribution_name_is_refused():
    doc = {"format": FORMAT_ID, "version": 1, "elements": {"blocks": [
        {"id": 1, "name": "A", "fail_dist": "Gaussian-ish"}]}}
    with pytest.raises(ValueError, match="unknown distribution"):
        parse_rbds(json.dumps(doc))


def test_non_ascii_names_are_written_readably(tmp_path):
    rbd = load_rbds(str(NEW_LAYOUT))
    _block(rbd, "Utility_Grid")["blockName"] = "Réseau_électrique"
    text = render_rbds(rbd)
    assert "Réseau_électrique" in text
    assert _block(parse_rbds(text), "Réseau_électrique")


def test_every_tracked_rbds_file_is_in_the_current_layout():
    """The repository's own models are written by the current writer, so
    they show the format as documented. The folders that hold them are
    listed rather than asked of git: tests run no program but Python
    (rule B0, test_no_legacy_binary)."""
    files = sorted(str(p.relative_to(REPO)) for folder in
                   ("new_docs/models", "tests/fixtures")
                   for p in (REPO / folder).rglob("*.rbds"))
    assert len(files) >= 20, files
    stale = []
    for f in files:
        if "rbds_before_experiment" in f:     # an old file, on purpose
            continue
        text = (REPO / f).read_text(encoding="utf-8")
        meta = {k: v for k, v in (json.loads(text).get("meta") or {}).items()
                if k != "generator"}
        fresh = render_rbds(parse_rbds(text), meta or None)
        old = json.loads(text)
        new = json.loads(fresh)
        old.get("meta", {}).pop("generator", None)
        new.get("meta", {}).pop("generator", None)
        if old != new:
            stale.append(f)
    assert not stale, f"rewrite with the current writer: {stale}"


def test_the_specs_minimal_example_validates_clean_and_runs():
    """docs/RBDS_FORMAT.md's example used to be an invalid model (no path
    counts, two blocks with no incoming link)."""
    import re
    from raptor.api.validate import issues
    spec = (REPO / "docs" / "RBDS_FORMAT.md").read_text(encoding="utf-8")
    section = spec[spec.index("## Minimal example"):]
    text = re.search(r"```json\n(.*?)```", section, re.S).group(1)
    rbd = parse_rbds(text)
    assert not [i for i in issues(rbd) if i.severity == "error"], issues(rbd)
    assert rbd.experiment["trials"] == 100
    logs, _eor, _fin = run_simulation_with_reports(rbd, num_runs=2)
    assert len(logs) == 2


def test_computed_columns_follow_the_model():
    """Link counts, parameter counts and a link's level are not stored:
    after a hand edit (a link added, a distribution changed) they are
    right, not stale."""
    doc = json.loads(NEW_LAYOUT.read_text(encoding="utf-8"))
    blk = doc["elements"]["blocks"][0]
    node = doc["elements"]["nodes"][0]
    assert not {"n", "Out"} & set(node.get("legacy", {})) and "n" not in node
    for key in ("In", "Out", "#FParam", "#RParam"):
        assert key not in blk.get("legacy", {}), key
    blk["fail_dist"] = "Weibull"
    blk["fail_params"] = [1.5, 1000, 0]
    rbd = parse_rbds(json.dumps(doc))
    row = next(r for r in rbd.section("BLOCK_DATA").rows
               if r["ID"] == str(blk["id"]))
    assert row["#FParam"] == "3"
    links = rbd.section("LINK_DATA").rows
    for node in rbd.section("NODE_DATA").rows:
        nid = node["ID"]
        assert int(node["n"]) == sum(
            1 for ln in links if ln["ToType"] == "Node" and ln["ToID"] == nid)


def test_hierarchy_level_is_derived_from_parent_and_never_stored():
    """`level` is a hierarchy's nesting depth. Raptor 7's pre-sim walk uses
    `hier.level + 1`; a flat stale value (every hierarchy level 1 while
    `Parent` nests three deep) left an empty downstream set and crashed it
    on Simulate with "Array allocation: high bound is less than low bound"."""
    rbd = load_rbd(str(GOLD + "/LadL8A/input.rbd"))
    hiers = [r for r in rbd.section("HIERARCHY_DATA").rows if r["ID"] != "0"]
    child = next(r for r in hiers if r["Parent"] not in ("0", ""))
    for row in hiers:
        row["level"] = "1"

    doc = json.loads(render_rbds(rbd))
    obj = next(h for h in doc["elements"]["hierarchies"]
               if h["id"] == int(child["ID"]))
    assert "level" not in obj.get("legacy", {}) and "level" not in obj

    back = parse_rbds(json.dumps(doc))
    levels = {r["ID"]: r["level"]
              for r in back.section("HIERARCHY_DATA").rows}
    assert levels[child["ID"]] == "2"
    assert levels[child["Parent"]] == "1"


def test_rate_values_are_computed_from_their_expressions():
    doc = json.loads(NEW_LAYOUT.read_text(encoding="utf-8"))
    assert all("value" not in r for r in doc["rates"] if r.get("expr"))
    assert all("value" not in f for f in doc["formulas"].values())
    rate = next(r for r in doc["rates"] if r["name"] == "UtilityMTBF")
    rate["expr"] = "1000"
    rbd = parse_rbds(json.dumps(doc))
    assert float(_block(rbd, "Utility_Grid")["FParam1"]) == 1000.0


def test_a_rbd_export_recomputes_the_counts_raptor_7_trusts(tmp_path):
    """A link added after loading left the node's `n` stale; Raptor 7
    crashes on a node whose `n` disagrees with its links."""
    from raptor.core.fileio.rbd import save_rbd
    rbd = load_rbds(str(NEW_LAYOUT))
    node = next(r for r in rbd.section("NODE_DATA").rows if r["n"] != "0")
    node["n"] = "9"
    save_rbd(rbd, str(tmp_path / "m.rbd"))
    back = load_rbd(str(tmp_path / "m.rbd"))
    row = next(r for r in back.section("NODE_DATA").rows
               if r["ID"] == node["ID"])
    links = back.section("LINK_DATA").rows
    assert int(row["n"]) == sum(1 for ln in links if ln["ToType"] == "Node"
                                and ln["ToID"] == node["ID"])


def test_a_file_from_before_experiment_keeps_its_run_settings():
    """Every .rbds written before the `experiment` section existed opened
    with one trial and no analyses: the run settings were in its system
    row and the reader ignored them."""
    old = load_rbds(str(REPO / "tests" / "fixtures" / "rbds_before_experiment"
                        / "HospitalPower.rbds"))
    new = load_rbds(str(NEW_LAYOUT))
    assert old.experiment["trials"] == new.experiment["trials"] == 200
    assert old.experiment["analyses"] == new.experiment["analyses"]
    assert old.experiment["term_condition"] == new.experiment["term_condition"]
    for sec in ("BLOCK_DATA", "NODE_DATA", "LINK_DATA"):
        assert len(old.section(sec).rows) == len(new.section(sec).rows)

"""`raptor.api.model_diff`: what is different between two models.

Variants are built in the test, in memory, from a loaded model (never by
editing a fixture on disk).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from raptor.api import model_diff as md
from raptor.api.markdown import diff_markdown
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import load_rbds, save_rbds

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import golden_fixtures as gf  # noqa: E402

TWO = REPO / "tests/golden/TwoBlockRedundancy/input.rbd"
DC2 = REPO / "new_docs/models/DC2.rbds"      # hierarchies, a spare pool, k-of-n


@pytest.fixture(scope="module")
def two():
    return load_rbd(str(TWO))


@pytest.fixture(scope="module")
def dc():
    return load_rbds(str(DC2))


def rows(rbd, section):
    return rbd.sections[section].rows


def row(rbd, section, col, value):
    return next(r for r in rows(rbd, section) if r.get(col) == value)


def only_change(result, group="elements"):
    changes = [c for e in result[group]["changed"] for c in e["changes"]]
    assert len(changes) == 1, changes
    return changes[0]


# --- identical / ignore ------------------------------------------------------

def test_identical_copy(dc):
    r = md.diff_models(dc, copy.deepcopy(dc))
    assert r["identical"] and r["summary"]["connectivity"] == "same wiring"
    assert r["schema"] == md.SCHEMA and r["warnings"] == []
    for g in ("added", "removed", "renamed", "changed"):
        assert r["elements"][g] == []
    assert r["links"] == {"added": [], "removed": [], "changed": []}
    assert md.flatten(r) == []


def test_layout_only_move(dc):
    b = copy.deepcopy(dc)
    row(b, "BLOCK_DATA", "blockName", "Generator")["xPos"] = "77.5"
    assert md.diff_models(dc, b)["identical"]
    r = md.diff_models(dc, b, ignore=())
    c = only_change(r)
    assert c["category"] == "layout" and c["field"] == "x"
    assert not r["identical"] and r["ignored"] == []


def test_derived_columns_ignored_by_default(dc):
    b = copy.deepcopy(dc)
    row(b, "BLOCK_DATA", "blockName", "Generator")["In"] = "7"
    row(b, "NODE_DATA", "Name", "ATS_swap")["Out"] = "9"
    assert md.diff_models(dc, b)["identical"]
    shown = md.diff_models(dc, b, ignore=("layout",))
    assert {c["category"] for e in shown["elements"]["changed"]
            for c in e["changes"]} == {"derived"}


def test_unknown_ignore_category_and_match(dc):
    with pytest.raises(ValueError, match="unknown category"):
        md.diff_models(dc, dc, ignore=("nonsense",))
    with pytest.raises(ValueError, match="match"):
        md.diff_models(dc, dc, match="fuzzy")


def test_number_formatting_is_not_a_change(dc):
    b = copy.deepcopy(dc)
    blk = row(b, "BLOCK_DATA", "blockName", "Generator")
    blk["FParam2"] = "0"                     # was "0.000000"
    blk["FParam1"] = "600"                   # was "600.000000"
    assert md.diff_models(dc, b)["identical"]


# --- parameters --------------------------------------------------------------

def test_failure_mean_changed(dc):
    b = copy.deepcopy(dc)
    row(b, "BLOCK_DATA", "blockName", "Generator")["FParam1"] = "700"
    c = only_change(md.diff_models(dc, b))
    assert c["field"] == "failure" and c["category"] == "failure"
    assert c["a"] != c["b"] and "600" in c["a"] and "700" in c["b"]
    assert c["a_raw"]["FParam1"] == "600.000000"


def test_distribution_type_change_is_one_change(dc):
    b = copy.deepcopy(dc)
    blk = row(b, "BLOCK_DATA", "blockName", "Generator")
    blk.update(FDist="15", FParam1="1.3", FParam2="823", FParam3="0")
    c = only_change(md.diff_models(dc, b))
    assert c["field"] == "failure"
    assert "Weibull" in c["b"] and "shape 1.3" in c["b"]


def test_unused_parameter_slot_is_not_a_change(dc):
    b = copy.deepcopy(dc)
    row(b, "BLOCK_DATA", "blockName", "Generator")["FParam3"] = "5"   # 2-param dist
    assert md.diff_models(dc, b)["identical"]


def test_empirical_points_are_one_change(two):
    a = copy.deepcopy(two)
    blk = rows(a, "BLOCK_DATA")[0]
    blk.update(FDist="16", FEmp="1 2 3", **{"#FParam": "3"})
    b = copy.deepcopy(a)
    rows(b, "BLOCK_DATA")[0].update(FEmp="1 2 3 4", **{"#FParam": "4"})
    c = only_change(md.diff_models(a, b))
    assert (c["a"], c["b"]) == ("Empirical, 3 points", "Empirical, 4 points")
    assert c["b_raw"]["FEmp"] == "1 2 3 4"


def test_rate_expression_changed(two):
    a = copy.deepcopy(two)
    a.rates = [{"name": "MTBF", "expr": "100", "value": 100.0, "description": ""},
               {"name": "Old", "expr": "1", "value": 1.0, "description": ""}]
    b = copy.deepcopy(a)
    b.rates = [{"name": "MTBF", "expr": "100 * 2", "value": 200.0, "description": ""},
               {"name": "New", "expr": "5", "value": 5.0, "description": ""}]
    r = md.diff_models(a, b)
    assert r["rates"]["added"] == ["New"] and r["rates"]["removed"] == ["Old"]
    fields = {c["field"] for c in r["rates"]["changed"][0]["changes"]}
    assert r["rates"]["changed"][0]["name"] == "MTBF" and fields == {"expr", "value"}


def test_formula_link_removed(dc):
    a = copy.deepcopy(dc)
    bid = row(a, "BLOCK_DATA", "blockName", "Generator")["ID"]
    a.formulas = {f"block:{bid}:FParam1": {"rate": "GenMTBF", "value": 600.0}}
    b = copy.deepcopy(a)
    b.formulas = {}
    c = only_change(md.diff_models(a, b))
    assert (c["field"], c["a"], c["b"]) == ("link:FParam1", "@GenMTBF", "(none)")


def test_system_trials_changed(dc):
    b = copy.deepcopy(dc)
    rows(b, "SYSTEM_DATA")[0]["NumRuns"] = "55"
    r = md.diff_models(dc, b)
    assert [c["field"] for c in r["system"]] == ["num_runs"]
    assert r["system"][0]["b"] == "55" and r["summary"]["system_changed"] == 1


def test_pool_stock_changed(dc):
    b = copy.deepcopy(dc)
    rows(b, "SPARE_POOL_DATA")[0]["Stock"] = "9"
    r = md.diff_models(dc, b)
    assert r["spare_pools"]["changed"][0]["changes"][0]["b"] == "9"
    assert r["summary"]["spare_pools_changed"] == 1 and not r["identical"]


def test_dependency_target_is_compared_by_matched_element(dc):
    a = copy.deepcopy(dc)
    gen = row(a, "BLOCK_DATA", "blockName", "Generator")
    ups = row(a, "BLOCK_DATA", "blockName", "UPS")
    gen.update(DepType="RBDBlock", DepNum=ups["ID"])
    b = copy.deepcopy(a)
    row(b, "BLOCK_DATA", "blockName", "UPS")["blockName"] = "UPS2"   # renamed target
    r = md.diff_models(a, b)
    assert r["elements"]["renamed"][0]["to"] == "UPS2"
    assert r["elements"]["changed"] == []          # still follows the same block
    row(b, "BLOCK_DATA", "blockName", "Generator")["DepNum"] = "0"
    c = only_change(md.diff_models(a, b))
    assert c["field"] == "dependency_target" and c["b"] == "(none)"


# --- connectivity ------------------------------------------------------------

def test_rename_keeps_links_matched(dc):
    b = copy.deepcopy(dc)
    row(b, "BLOCK_DATA", "blockName", "Generator")["blockName"] = "Gen_Set"
    r = md.diff_models(dc, b)
    assert r["elements"]["renamed"] == [
        {"kind": "Block", "id": 103, "from": "Generator", "to": "Gen_Set"}]
    assert r["links"] == {"added": [], "removed": [], "changed": []}
    assert r["summary"]["connectivity"] == "same wiring" and not r["identical"]


def test_rename_mode_name_does_not_match_by_id(dc):
    b = copy.deepcopy(dc)
    row(b, "BLOCK_DATA", "blockName", "Generator")["blockName"] = "Gen_Set"
    r = md.diff_models(dc, b, match="name")
    assert r["elements"]["renamed"] == []
    assert [e["name"] for e in r["elements"]["added"]] == ["Gen_Set"]
    assert [e["name"] for e in r["elements"]["removed"]] == ["Generator"]


def _add_linked_block(rbd):
    """A new block between the first Start and the join node of TwoBlockRedundancy."""
    blk = copy.deepcopy(rows(rbd, "BLOCK_DATA")[0])
    blk.update(ID="9", blockName="Extra")
    rows(rbd, "BLOCK_DATA").append(blk)
    for i, (ft, fi, tt, ti) in enumerate((("Node", "1", "Block", "9"),
                                          ("Block", "9", "Node", "5"))):
        lk = copy.deepcopy(rows(rbd, "LINK_DATA")[0])
        lk.update(ID=str(20 + i), FromType=ft, FromID=fi, ToType=tt, ToID=ti)
        rows(rbd, "LINK_DATA").append(lk)


def test_block_added_and_linked(two):
    b = copy.deepcopy(two)
    _add_linked_block(b)
    r = md.diff_models(two, b)
    assert [e["name"] for e in r["elements"]["added"]] == ["Extra"]
    assert r["summary"]["connectivity"] == "1 element added, 2 links added"
    ends = {(x["from"], x["to"]) for x in r["links"]["added"]}
    assert ends == {("Node start", "Block Extra"), ("Block Extra", "Node join")}
    # the join node now has three inputs: a structure entry says so
    entry = next(e for e in r["structure"] if e["name"] == "join")
    assert any(c["field"] == "k_of_n" and c["b"].endswith("of 3")
               for c in entry["changes"])


def test_link_removed(two):
    b = copy.deepcopy(two)
    gone = rows(b, "LINK_DATA").pop(0)
    r = md.diff_models(two, b)
    assert len(r["links"]["removed"]) == 1 and r["links"]["added"] == []
    assert r["summary"]["connectivity"] == "1 link removed"
    assert gone["FromType"] in r["links"]["removed"][0]["from"]


def test_link_attribute_changed(two):
    b = copy.deepcopy(two)
    rows(b, "LINK_DATA")[0]["AProb"] = "0.5"
    r = md.diff_models(two, b)
    assert r["summary"]["links_changed"] == 1
    assert r["links"]["changed"][0]["changes"][0]["b"] == "0.5"


def test_node_k_changed(dc):
    b = copy.deepcopy(dc)
    node = row(b, "NODE_DATA", "Name", "ATS_swap")
    node["k"] = "2"
    r = md.diff_models(dc, b)
    entry = next(e for e in r["structure"] if e["name"] == "ATS_swap")
    c = next(c for c in entry["changes"] if c["field"] == "k_of_n")
    assert c["a"].startswith("needs 1 of ") and c["b"].startswith("needs 2 of ")
    assert not r["identical"]


def test_block_moved_into_hierarchy(dc):
    b = copy.deepcopy(dc)
    blk = row(b, "BLOCK_DATA", "blockName", "Generator")
    old_parent = blk["ParentId"]
    new_parent = next(h["ID"] for h in rows(b, "HIERARCHY_DATA")
                      if h["ID"] != old_parent)
    blk["ParentId"] = new_parent
    r = md.diff_models(dc, b)
    entry = next(e for e in r["structure"] if e["name"] == "Generator")
    assert entry["moved"] and entry["changes"][0]["field"] == "hierarchy"
    assert entry["changes"][0]["a"] != entry["changes"][0]["b"]
    assert r["summary"]["elements_moved"] == 1 and not r["identical"]


def test_hierarchy_rename_does_not_move_children(dc):
    b = copy.deepcopy(dc)
    row(b, "HIERARCHY_DATA", "hierarchyName", "Power_sub")["hierarchyName"] = "Power"
    r = md.diff_models(dc, b)
    assert r["summary"]["elements_moved"] == 0
    assert [x["to"] for x in r["elements"]["renamed"]] == ["Power"]
    assert r["links"]["added"] == [] and r["links"]["removed"] == []


def test_id_mode_matches_by_id(dc):
    b = copy.deepcopy(dc)
    row(b, "BLOCK_DATA", "blockName", "Generator")["blockName"] = "Gen_Set"
    r = md.diff_models(dc, b, match="id")
    assert r["elements"]["renamed"][0]["to"] == "Gen_Set"
    assert r["elements"]["added"] == []


def test_duplicate_names_warn_and_do_not_crash(two):
    r = md.diff_models(two, copy.deepcopy(two))      # both blocks are "unnamed"
    assert r["identical"]
    assert any("duplicate name 'unnamed'" in w for w in r["warnings"])
    b = copy.deepcopy(two)
    rows(b, "BLOCK_DATA")[0]["FParam1"] = "123"
    assert md.diff_models(two, b)["elements"]["changed"]


# --- output ------------------------------------------------------------------

def test_json_safe_and_sorted(dc):
    b = copy.deepcopy(dc)
    for name, v in (("Generator", "1"), ("UPS", "2"), ("Utility_A", "3")):
        row(b, "BLOCK_DATA", "blockName", name)["FParam1"] = v
    row(b, "BLOCK_DATA", "blockName", "UPS")["Running"] = "9"
    r = md.diff_models(dc, b)
    json.dumps(r)
    keys = [(e["kind"], e["path"], e["name"]) for e in r["elements"]["changed"]]
    assert keys == sorted(keys) and len(keys) == 3
    for e in r["elements"]["changed"]:
        fields = [c["field"] for c in e["changes"]]
        assert fields == sorted(fields)
    assert set(r["a"]) == {"label", "name", "path", "digest"}


def test_diff_markdown(dc):
    b = copy.deepcopy(dc)
    row(b, "BLOCK_DATA", "blockName", "Generator")["FParam1"] = "700"
    text = diff_markdown(md.diff_models(dc, b, labels=("old", "new")))
    assert "**old** vs **new**" in text and "| Element | Field | old | new |" in text
    assert "Block Power_sub / Generator" in text
    same = diff_markdown(md.diff_models(dc, dc))
    assert "no differences" in same and "layout" in same


def test_restrict_and_has_differences(dc):
    b = copy.deepcopy(dc)
    rows(b, "SPARE_POOL_DATA")[0]["Stock"] = "9"
    r = md.diff_models(dc, b)
    assert md.restrict(r, ["elements"])["identical"]
    assert not md.restrict(r, ["spare_pools"])["identical"]
    with pytest.raises(ValueError):
        md.restrict(r, ["nope"])


def test_rbd_against_rbds_is_identical(two, tmp_path):
    f = tmp_path / "t.rbds"
    save_rbds(two, str(f))
    r = md.diff_models(two, load_rbds(str(f)))
    assert r["identical"], md.flatten(r)


def test_workspace_wrapper(tmp_path):
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path / "ws")
    a = ws.open_model(DC2)["model_id"]
    b = ws.open_model(DC2)["model_id"]
    r = md.diff(ws, a, b)
    assert r["identical"] and r["a"]["path"].endswith("DC2.rbds")


# --- every shipped fixture ---------------------------------------------------

_NAMES = [n for n in gf.all_fixture_names() if gf.resolve_rbd_path(n)]


def test_fixture_list_is_not_empty():
    assert len(_NAMES) > 50


@pytest.mark.parametrize("name", _NAMES)
def test_every_fixture_against_itself_and_its_columns(name):
    rbd = load_rbd(str(gf.resolve_rbd_path(name)))
    unmapped = {(sec, col) for sec, s in rbd.sections.items()
                if sec != "COMMENTS" for r in s.rows for col in r
                if md.explicit_category(sec, col) is None}
    assert not unmapped, f"columns without a category: {sorted(unmapped)}"
    assert md.diff_models(rbd, rbd)["identical"]
    assert md.diff_models(rbd, copy.deepcopy(rbd), match="id")["identical"]


def test_derived_columns_match_the_file_reader():
    from raptor.core.fileio import rbds
    theirs = getattr(rbds, "_DERIVED", None)
    if theirs is None:
        pytest.skip("this reader has no derived-column table")
    for section, cols in theirs.items():
        assert set(cols) <= set(md.DERIVED_COLUMNS[section])
        assert all(md.category_of(section, c) == "derived" for c in cols)


def test_an_unset_k_is_not_a_voting_change():
    """k=-1 is Raptor 7's "never set", run as 1: a multi-input node whose k
    went from -1 to 1 changed what the file stores, not what the node needs."""
    a = load_rbds(str(REPO / "new_docs/models/Water1.rbds"))
    b = copy.deepcopy(a)
    manifold = next(r for r in a.sections["NODE_DATA"].rows if r["Name"] == "Manifold")
    manifold["k"] = "-1"
    changes = [c for e in md.diff_models(a, b)["elements"]["changed"] for c in e["changes"]]
    assert [c for c in changes if c["field"] == "k_of_n"] == []
    assert [c["field"] for c in changes] == ["k"]

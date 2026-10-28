"""Tests for model building and editing.

Covers spec-driven builds that save, reload and validate cleanly, the
friendly `.rbds` field names (sparing, PM, dependency, costs,
growth/decay, ...), expression parameters with formula provenance, the
series/parallel/k_of_n templates, and the update/delete/configure helpers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.build import (
    build_from_spec, add_block, add_node, add_event, connect,
    update_element, delete_element, configure_system,
    configure_sparing, configure_maintenance, configure_phases,
    describe, series, parallel, k_of_n, template_spec, build_template,
    ElementNotFound, AmbiguousName, BuildError,
)
from raptor.api.workspace import Workspace
from raptor.api.validate import issues
from raptor.core.fileio.builder import validate
from raptor.core.fileio.rbds import load_rbds

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def motor_model(workspace):
    result = workspace.open_model(GOLDEN / "MotorDemo" / "input.rbd")
    return result["model_id"]


def _structural_errors(rbd):
    return [i for i in validate(rbd) if i.startswith("ERROR")]


def _api_errors(rbd):
    return [i for i in issues(rbd) if i.severity == "error"]


# ---------------------------------------------------------------------------
# build_from_spec
# ---------------------------------------------------------------------------

class TestBuildFromSpec:
    """Build a 2-of-3 model from a spec dict, save, reload, validate clean."""

    def test_k_of_n_builds_and_validates(self):
        rbd = build_from_spec(k_of_n(2, 3))
        assert not _structural_errors(rbd), _structural_errors(rbd)

    def test_save_and_reload(self, workspace):
        info = workspace.create_model("Test2of3", template="blank")
        model_id = info["model_id"]
        handle = workspace.get(model_id)
        handle.rbd = build_from_spec(k_of_n(2, 3))
        workspace.save(model_id)

        reloaded = load_rbds(str(handle.path))
        assert not _structural_errors(reloaded), _structural_errors(reloaded)

    def test_series_and_parallel_build(self):
        assert len([r for r in build_from_spec(series(3))
                    .section("BLOCK_DATA").rows if r.get("ID") != "0"]) == 3
        assert len([r for r in build_from_spec(parallel(4))
                    .section("BLOCK_DATA").rows if r.get("ID") != "0"]) == 4

    def test_friendly_names_and_string_node_type(self):
        spec = {
            "name": "Friendly",
            "system": {"run_length": 500, "termination": "TimeSim",
                       "num_runs": 4, "display_unit": "years"},
            "blocks": [{"name": "A", "fail_dist": 4,
                        "fail_params": [100.0, 0.0]}],
            "nodes": [{"name": "Start", "type": "start"},
                      {"name": "End", "type": "end"}],
            "links": [{"from": "Start", "to": "A"},
                      {"from": "A", "to": "End"}],
        }
        rbd = build_from_spec(spec)
        start = [n for n in rbd.section("NODE_DATA").rows
                 if n.get("Name") == "Start"][0]
        assert start["Type"] == "1"
        assert rbd.display_unit == "years"
        assert rbd.section("SYSTEM_DATA").rows[0]["TermCondition"] == "500.000000"
        assert not _structural_errors(rbd)

    def test_system_flow_generated_aliases(self):
        """`flow_generated` and its `flowGenerated` alias are both accepted
        in a spec's system dict."""
        for key in ("flow_generated", "flowGenerated"):
            spec = {
                "system": {"capacity_analysis": True, key: 250},
                "blocks": [{"name": "A"}],
                "nodes": [{"name": "Start", "type": 1},
                          {"name": "End", "type": 3}],
                "links": [{"from": "Start", "to": "A"},
                          {"from": "A", "to": "End"}],
            }
            rbd = build_from_spec(spec)
            row = rbd.section("SYSTEM_DATA").rows[0]
            assert row["flowGenerated"] == "250", key
            assert row["capacityAnalysis"] == "1", key

    def test_links_wire_by_name_and_ids_assigned(self):
        rbd = build_from_spec(k_of_n(2, 3))
        block_ids = {b["ID"] for b in rbd.section("BLOCK_DATA").rows
                     if b.get("ID") != "0"}
        assert block_ids == {"1", "2", "3"}
        # Every link references an existing element.
        assert not _structural_errors(rbd)

    def test_expression_in_spec_records_formula(self):
        spec = {
            "rates": [{"name": "Highway",
                       "expr": "0.00015 * 20000 / 8760  # basis",
                       "description": "highway"}],
            "blocks": [{"name": "A", "fail_dist": 4,
                        "fail_params": ["@Highway * 1.4", 0.0]}],
            "nodes": [{"name": "Start", "type": "start"},
                      {"name": "End", "type": "end"}],
            "links": [{"from": "Start", "to": "A"},
                      {"from": "A", "to": "End"}],
        }
        rbd = build_from_spec(spec)
        assert "block:1:FParam1" in rbd.formulas
        assert rbd.formulas["block:1:FParam1"]["expr"] == "@Highway * 1.4"

    def test_legacy_link_form(self):
        # IDs given explicitly rather than relying on auto-allocation order:
        # next_id() now pools IDs across every element kind (builder.py's
        # own defense against the raw-id-without-kind-check bug class this
        # session found twice elsewhere), so a Block and a Node no longer
        # both auto-assign to id 1 the way they used to -- this test's own
        # point is the legacy from_id/to_id link form, not auto-id order.
        spec = {
            "blocks": [{"name": "A", "id": 1}],
            "nodes": [{"name": "Start", "type": 1, "id": 1},
                      {"name": "End", "type": 3, "id": 2}],
            "links": [
                {"from_type": "Node", "from_id": 1,
                 "to_type": "Block", "to_id": 1},
                {"from_type": "Block", "from_id": 1,
                 "to_type": "Node", "to_id": 2},
            ],
        }
        rbd = build_from_spec(spec)
        assert not _structural_errors(rbd), _structural_errors(rbd)

    def test_invalid_spec_raises(self):
        with pytest.raises(BuildError):
            build_from_spec({"blocks": [{"name": "Lonely"}]})


class TestHierarchyBoundaryLinks:
    """the 2026-09-22 MCP functional test review issue 14 (SPOF+hierarchy, part 5b): a link named
    straight at a hierarchy's In/Out marker node from outside must be
    redirected to address the hierarchy itself ("Hier"/its id), matching
    how real Raptor represents it on disk (`tests/golden/HierarchyExample`:
    "Node 16 -> Hier 23" / "Hier 23 -> Node 19", never a link naming the
    marker node directly) -- the direct-Node form left the marker's
    `in_paths_array` unpopulated in `graph.py`, so `single_point_failures`
    flagged every block behind the hierarchy, including a fully redundant
    pair."""

    def _pair_spec(self, drop: tuple[tuple[str, str], ...] = ()) -> dict:
        """*drop* leaves out those (from, to) links, to free a boundary side
        for the incremental tests: a hierarchy takes one link in and one out
        (Raptor 7's CheckValidLink), so a second one would be refused."""
        spec = {
            "blocks": [
                {"id": 1, "name": "HeatExchanger", "fail_dist": 4, "fail_params": [2000],
                 "repair_dist": 4, "repair_params": [24]},
                {"id": 2, "name": "PumpA", "fail_dist": 4, "fail_params": [1000],
                 "repair_dist": 4, "repair_params": [10], "parent": 11},
                {"id": 3, "name": "PumpB", "fail_dist": 4, "fail_params": [1000],
                 "repair_dist": 4, "repair_params": [10], "parent": 11},
                {"id": 4, "name": "PowerSupply", "fail_dist": 4, "fail_params": [5000],
                 "repair_dist": 4, "repair_params": [4]},
            ],
            "nodes": [
                {"id": 5, "name": "Start", "type": "start"},
                {"id": 6, "name": "PairIn", "type": "in", "parent": 11},
                # An In marker takes one link out (Raptor 7), so the pair
                # fans out from a node, as Raptor 7's own models do.
                {"id": 12, "name": "PairSplit", "type": "regular", "k": 1, "parent": 11},
                {"id": 7, "name": "PairJoin", "type": "regular", "k": 1, "n": 2, "parent": 11},
                {"id": 8, "name": "PairOut", "type": "out", "parent": 11},
                {"id": 9, "name": "MidJoin", "type": "regular", "k": 1},
                {"id": 10, "name": "End", "type": "end"},
            ],
            "links": [
                {"from": "Start", "to": "HeatExchanger"},
                {"from": "HeatExchanger", "to": "PairIn"},
                {"from": "PairIn", "to": "PairSplit"},
                {"from": "PairSplit", "to": "PumpA"},
                {"from": "PairSplit", "to": "PumpB"},
                {"from": "PumpA", "to": "PairJoin"},
                {"from": "PumpB", "to": "PairJoin"},
                {"from": "PairJoin", "to": "PairOut"},
                {"from": "PairOut", "to": "PowerSupply"},
                {"from": "PowerSupply", "to": "MidJoin"},
                {"from": "MidJoin", "to": "End"},
            ],
            "hierarchies": [{"id": 11, "name": "PumpPair", "in_id": 6, "out_id": 8}],
        }
        spec["links"] = [lk for lk in spec["links"] if (lk["from"], lk["to"]) not in drop]
        return spec

    def test_boundary_links_redirected_to_hier(self):
        rbd = self._pair_spec()
        rbd = build_from_spec(rbd)
        links = {(lk["FromType"], lk["FromID"], lk["ToType"], lk["ToID"])
                 for lk in rbd.section("LINK_DATA").rows}
        assert ("Block", "1", "Hier", "11") in links
        assert ("Hier", "11", "Block", "4") in links
        # nothing crossing the boundary still names the marker node itself
        assert not any(lk[2] == "Node" and lk[3] == "6" for lk in links)
        assert not any(lk[0] == "Node" and lk[1] == "8" for lk in links)

    def test_internal_marker_links_stay_direct(self):
        rbd = build_from_spec(self._pair_spec())
        links = {(lk["FromType"], lk["FromID"], lk["ToType"], lk["ToID"])
                 for lk in rbd.section("LINK_DATA").rows}
        # the In marker's own outgoing (to members) and the Out marker's
        # own incoming (from PairJoin) are internal -- direct Node
        # addressing is exactly how real Raptor represents these.
        assert ("Node", "6", "Node", "12") in links
        assert ("Node", "12", "Block", "2") in links
        assert ("Node", "12", "Block", "3") in links
        assert ("Node", "7", "Node", "8") in links

    def test_marker_counts_match_real_convention(self):
        rbd = build_from_spec(self._pair_spec())
        nodes = {r["ID"]: r for r in rbd.section("NODE_DATA").rows}
        # matches tests/golden/HierarchyExample's In/Out markers exactly:
        # the In marker's declared incoming is 0 (its real incoming link
        # addresses the hierarchy, not the node), the Out marker's
        # declared outgoing is 0 (same, the other direction).
        assert nodes["6"]["n"] == "0"
        assert nodes["8"]["Out"] == "0"

    def test_spf_no_longer_flags_the_redundant_pair(self):
        from raptor.core.engine.spf_fev import single_point_failures
        rbd = build_from_spec(self._pair_spec())
        rows = {(r["kind"], r["id"]) for r in single_point_failures(rbd)}
        assert rows == {("Block", 1), ("Block", 4)}

    def _open_pair_model(self, workspace, drop=()) -> str:
        # `register_model` takes the already-built `RbdFile` directly --
        # no save/reload round trip, sidestepping an unrelated pre-existing
        # gap where a HIERARCHY_DATA row built from scratch (every field at
        # its template default) loses columns across a `.rbd` save +
        # `workspace.open_model` reload that a REAL captured file's row
        # (tests/golden/HierarchyExample) doesn't. Not this fix's concern.
        rbd = build_from_spec(self._pair_spec(drop))
        return workspace.register_model(rbd, name="pair")["model_id"]

    def test_connect_also_redirects(self, workspace):
        """The incremental `connect()` path (not just `build_from_spec`)
        gets the same redirect: naming "PairIn" directly from a new,
        unparented block crosses hierarchy 11's boundary."""
        model_id = self._open_pair_model(workspace, drop=(("HeatExchanger", "PairIn"),))
        new_row = add_block(workspace, model_id, "Extra",
                            fail_dist=4, fail_params=[1000.0],
                            repair_dist=4, repair_params=[10.0])
        row = connect(workspace, model_id, "Block", int(new_row["ID"]),
                      "Node", 6)  # "PairIn"
        assert row["ToType"] == "Hier" and row["ToID"] == "11"

    def test_add_block_after_marker_redirects(self, workspace):
        """`add_block(after="PairOut")` links FROM the Out marker; the new
        block has no `parent`, so it's outside hierarchy 11 and the link
        should address the hierarchy, not node 8 directly."""
        model_id = self._open_pair_model(workspace, drop=(("PairOut", "PowerSupply"),))
        row = add_block(workspace, model_id, "Extra",
                        after="PairOut",
                        fail_dist=4, fail_params=[1000.0],
                        repair_dist=4, repair_params=[10.0])
        links = workspace.get(model_id).rbd.section("LINK_DATA").rows
        assert any(lk["FromType"] == "Hier" and lk["FromID"] == "11"
                   and lk["ToID"] == row["ID"] for lk in links)


class TestDistParamsRequiredTogether:
    """A regression test for the 2026-09-22 MCP functional test review's "mixed distribution
    types" finding: giving `fail_dist` without `fail_params` on a new
    block silently kept `make_block_row`'s Exponential-shaped defaults
    (100.0, 0.0, 0.0) under the new distribution's meaning -- e.g. a
    Gamma read that as (shape=100, scale=0), which `validate()` flags
    but only after the model already "built" successfully."""

    def _spec(self, block: dict) -> dict:
        return {
            "blocks": [dict(block, id=1)],
            "nodes": [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1},
                      {"id": 2, "name": "End", "type": 3, "n": 1, "out": 0}],
            "links": [{"from": "Start", "to": block["name"]},
                      {"from": block["name"], "to": "End"}],
        }

    def test_fail_dist_without_fail_params_raises(self):
        with pytest.raises(BuildError, match="fail_dist.*no fail_params"):
            build_from_spec(self._spec({"name": "GammaBlock", "fail_dist": 6}))

    def test_repair_dist_without_repair_params_raises(self):
        with pytest.raises(BuildError, match="repair_dist.*no repair_params"):
            build_from_spec(self._spec({
                "name": "GammaBlock", "fail_dist": 6,
                "fail_params": [2.0, 500.0], "repair_dist": 15}))

    def test_dist_with_params_together_is_fine(self):
        rbd = build_from_spec(self._spec({
            "name": "GammaBlock", "fail_dist": 6,
            "fail_params": [2.0, 500.0]}))
        assert not _structural_errors(rbd)

    def test_neither_dist_nor_params_is_fine(self):
        """All-defaults (no distribution override at all) is unaffected --
        a new block's defaults are internally consistent."""
        rbd = build_from_spec(self._spec({"name": "PlainBlock"}))
        assert not _structural_errors(rbd)

    def test_mixed_gamma_and_weibull_blocks_build_and_simulate(self):
        """The reviewer's actual scenario, fully specified: Gamma and
        Weibull blocks in the same build_model call build and simulate
        cleanly once each carries its own params."""
        spec = {
            "blocks": [
                {"id": 1, "name": "GammaBlock", "fail_dist": 6,
                 "fail_params": [2.0, 500.0, 0.0],
                 "repair_dist": 8, "repair_params": [10.0, 2.0]},
                {"id": 2, "name": "WeibullBlock", "fail_dist": 15,
                 "fail_params": [2.0, 1000.0, 0.0],
                 "repair_dist": 8, "repair_params": [10.0, 2.0]},
            ],
            "nodes": [
                {"id": 1, "name": "Start", "type": 1, "n": 0, "out": 2},
                {"id": 2, "name": "End", "type": 3, "n": 2, "out": 0},
            ],
            "links": [
                {"from": "Start", "to": "GammaBlock"},
                {"from": "GammaBlock", "to": "End"},
                {"from": "Start", "to": "WeibullBlock"},
                {"from": "WeibullBlock", "to": "End"},
            ],
        }
        rbd = build_from_spec(spec)
        assert not _structural_errors(rbd)
        from raptor.core.engine.runner import run_simulation
        logs = run_simulation(rbd, num_runs=2, collect_logs=False)
        assert len(logs) == 2

    def test_add_block_tool_also_requires_params_together(self, workspace,
                                                           motor_model):
        from raptor.api.build import add_block
        with pytest.raises(BuildError, match="fail_dist.*no fail_params"):
            add_block(workspace, motor_model, "NewGamma", fail_dist=6)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

class TestTemplates:
    """Templates produce valid models and template_spec returns RbdFiles."""

    @pytest.mark.parametrize("spec", [series(3), parallel(2), k_of_n(2, 4)])
    def test_specs_validate(self, spec):
        rbd = build_from_spec(spec)
        assert not _structural_errors(rbd), _structural_errors(rbd)

    def test_template_spec_returns_rbdfile(self):
        for template, kwargs in [("series", {"n": 3}),
                                 ("parallel", {"n": 2}),
                                 ("k_of_n", {"n": 3, "k": 2})]:
            rbd = template_spec(template, **kwargs)
            assert not _structural_errors(rbd), (template,
                                                 _structural_errors(rbd))
        blank = template_spec("blank")
        assert blank.section("BLOCK_DATA").rows

    def test_build_template_alias(self):
        assert build_template("series", n=2).section("BLOCK_DATA") is not None

    def test_unknown_template_raises(self):
        with pytest.raises(BuildError):
            template_spec("does-not-exist")


# ---------------------------------------------------------------------------
# update_element
# ---------------------------------------------------------------------------

class TestUpdateElement:
    """Friendly fields, indexed forms and helpful errors."""

    def test_whole_number_keeps_real_column_format(self, workspace, motor_model):
        """A JSON caller's whole number (`[40]`) arrives as an int; it used to
        be written `40` into REAL columns every real-authored file writes as
        `40.000000`. Integer columns stay integers."""
        row = update_element(workspace, motor_model, "Armature",
                             {"fail_dist": 19, "fail_params": [40],
                              "repair_dist": 19, "repair_params": [6]})
        assert row["FParam1"] == "40.000000"
        assert row["RParam1"] == "6.000000"
        assert row["FDist"] == "19"

    def test_nonexistent_name_raises_with_near_match(self, workspace,
                                                     motor_model):
        with pytest.raises(ElementNotFound) as exc:
            update_element(workspace, motor_model, "Armatur",
                           {"blockName": "Renamed"})
        assert "Near matches" in str(exc.value)
        assert "Armature" in str(exc.value)

    def test_update_by_name(self, workspace, motor_model):
        row = update_element(workspace, motor_model, "Armature",
                             {"blockName": "ArmatureRenamed"})
        assert row.get("blockName") == "ArmatureRenamed"

    def test_update_by_selector(self, workspace, motor_model):
        row = update_element(workspace, motor_model, "Block:1",
                             {"blockName": "Block1Updated"})
        assert row.get("blockName") == "Block1Updated"

    def test_friendly_sparing_and_costs(self, workspace, motor_model):
        row = update_element(workspace, motor_model, "Armature", {
            "spare_pool": "Custom", "stock": 7, "rso": 2,
            "new_spares": 3, "arrive": 100.0, "emertime": 12.0,
            "slo": 4, "initial_cost": 11.0, "pm_cost": 5.0,
        })
        assert row["SpType"] == "Custom"
        assert row["Stock"] == "7"
        assert row["RSO"] == "2"
        assert row["NewSp"] == "3"
        assert row["ArriveEvery"] == "100.000000"
        assert row["EmerTime"] == "12.000000"
        assert row["SLO"] == "4"
        assert row["initial"] == "11.00"
        assert row["pmCost"] == "5.00"

    def test_friendly_pm_dependency_and_growth(self, workspace, motor_model):
        row = update_element(workspace, motor_model, "Armature", {
            "uses_pm": 1, "pm_freq": 50.0, "pm_trigger": "Trig1",
            "pm_duration_dist": 19, "pm_params": [2.0, 0.0, 0.0],
            "dep_type": "RBDBlock", "dep_num": 3,
            "standby_stress": 0.25, "gd_type": 1,
            "gd_rate": 0.5, "gd_limit": 300.0,   # rising: the limit is above the failure mean (Raptor 7)
        })
        assert row["usesPM"] == "1"
        assert row["PMFreq"] == "50.000000"
        assert row["pmTrig"] == "Trig1"
        assert row["pmTriggered"] == "1"
        assert row["Dist"] == "19"
        assert row["Param1#3"] == "2.000000"
        assert row["DepType"] == "RBDBlock"
        assert row["DepNum"] == "3"
        assert row["SBStress"] == "0.250"
        assert row["GDType"] == "1"
        assert row["GDRate"] == "0.500000"
        assert row["GDLimit"] == "300.000000"

    def test_indexed_repair_params(self, workspace, motor_model):
        row = update_element(workspace, motor_model, "Armature",
                             {"repair_params[0]": 42.0})
        assert row["RParam1"] == "42.000000"

    def test_expression_records_formula(self, workspace, motor_model):
        from raptor.api.rates import add_rate
        entry = add_rate(workspace, motor_model, "Highway",
                         "0.00015 * 20000 / 8760  # basis")
        expected = entry["value"] * 1.4
        # Keep the Weibull scale valid (FParam2); only the shape carries
        # the formula under test.
        row = update_element(workspace, motor_model, "Armature",
                             {"fail_params": ["@Highway * 1.4", 1.0, 0]})
        assert abs(float(row["FParam1"]) - expected) < 1e-6
        formulas = workspace.get(motor_model).rbd.formulas
        key = f"block:{row['ID']}:FParam1"
        assert formulas[key]["expr"] == "@Highway * 1.4"
        assert abs(formulas[key]["value"] - expected) < 1e-9

    def test_update_link_capacity_flow(self, workspace, motor_model):
        handle = workspace.get(motor_model)
        link = handle.rbd.section("LINK_DATA").rows[0]
        row = update_element(workspace, motor_model, f"Link:{link['ID']}",
                             {"nom_flow": 5, "max_flow": 25,
                              "cap_pri": 60})
        assert row["nomFlow"] == "5"
        assert row["MaxFlow"] == "25"
        assert row["CapPri"] == "60"

    def test_update_link_all_friendly_fields(self, workspace, motor_model):
        """Every friendly link field edits through a `Link:N` selector."""
        handle = workspace.get(motor_model)
        link = handle.rbd.section("LINK_DATA").rows[0]
        row = update_element(workspace, motor_model, f"Link:{link['ID']}", {
            "nom_flow": 5, "max_flow": 25, "cap_pri": 60,
            "cold_pri": 70, "a_prob": 0.9, "auto_time": 1.5,
            "man_time": 2.5,
        })
        assert row["nomFlow"] == "5"
        assert row["MaxFlow"] == "25"
        assert row["CapPri"] == "60"
        assert row["ColdPri"] == "70"
        assert row["AProb"] == "0.900000"
        assert row["AutoTime"] == "1.500000"
        assert row["ManTime"] == "2.500000"

        # ... and survives a save/reload through .rbds.
        reloaded = load_rbds(str(handle.path))
        back = [lk for lk in reloaded.section("LINK_DATA").rows
                if lk["ID"] == link["ID"]][0]
        assert back["nomFlow"] == "5"
        assert back["MaxFlow"] == "25"
        assert back["AProb"] == "0.900000"

    def test_unknown_field_raises(self, workspace, motor_model):
        with pytest.raises(BuildError):
            update_element(workspace, motor_model, "Armature",
                           {"not_a_field": 1})

    def test_ambiguous_name_raises(self, workspace, motor_model):
        # Two nodes sharing one unique name -> ambiguous.
        handle = workspace.get(motor_model)
        from raptor.core.fileio.rbdlib import make_node_row
        handle.rbd.section("NODE_DATA").rows.append(
            make_node_row(998, "DupNode", 0, 0))
        handle.rbd.section("NODE_DATA").rows.append(
            make_node_row(999, "DupNode", 0, 0))
        with pytest.raises(AmbiguousName):
            update_element(workspace, motor_model, "DupNode", {"n": 1})


# ---------------------------------------------------------------------------
# delete_element
# ---------------------------------------------------------------------------

class TestDeleteElement:
    """delete_element with reconnect=true leaves a valid graph."""

    def test_delete_block(self, workspace, motor_model):
        result = delete_element(workspace, motor_model, "Armature",
                                reconnect=False)
        assert result["kind"] == "Block"
        assert not _structural_errors(workspace.get(motor_model).rbd)

    def test_delete_node_reconnect_valid(self, workspace, motor_model):
        handle = workspace.get(motor_model)
        for n in list(handle.rbd.section("NODE_DATA").rows):
            if n.get("Type") == "2":
                result = delete_element(workspace, motor_model,
                                        f"Node:{n['ID']}", reconnect=True)
                assert result["kind"] == "Node"
                break
        assert not _structural_errors(handle.rbd), _structural_errors(handle.rbd)


# ---------------------------------------------------------------------------
# add_block / add_node / add_event / connect
# ---------------------------------------------------------------------------

class TestAddElement:
    def test_add_block_friendly_params(self, workspace, motor_model):
        row = add_block(workspace, motor_model, "NewBlock",
                        fail_dist=4, fail_params=[500.0, 0.0],
                        repair_dist=4, repair_params=[20.0, 0.0])
        assert row.get("blockName") == "NewBlock"
        assert row["FParam1"] == "500.000000"
        assert row["#FParam"] == "2"

    def test_add_block_after_wires_link(self, workspace, motor_model):
        # A block takes one link out (Raptor 7), so wire after a free one.
        add_block(workspace, motor_model, "Upstream")
        before = len(workspace.get(motor_model).rbd.section("LINK_DATA").rows)
        row = add_block(workspace, motor_model, "Downstream",
                        after="Upstream")
        links = workspace.get(motor_model).rbd.section("LINK_DATA").rows
        assert len(links) == before + 1
        assert any(lk["FromType"] == "Block"
                   and lk["ToType"] == "Block"
                   and lk["ToID"] == row["ID"] for lk in links)

    def test_add_node_type_name(self, workspace, motor_model):
        row = add_node(workspace, motor_model, "NewStart", type="start")
        assert row.get("Type") == "1"

    def test_add_event_stores_prob(self, workspace, motor_model):
        row = add_event(workspace, motor_model, "NewEvent", prob=0.25)
        assert row.get("eventName") == "NewEvent"
        assert row.get("failVal") == "0.250000"

    def test_connect(self, workspace, motor_model):
        handle = workspace.get(motor_model)
        free = add_block(workspace, motor_model, "Free")
        end = next(n for n in handle.rbd.section("NODE_DATA").rows if n["Type"] == "3")
        ends_in = [lk for lk in handle.rbd.section("LINK_DATA").rows
                   if lk["ToType"] == "Node" and lk["ToID"] == end["ID"]]
        assert ends_in   # MotorDemo's End already has its one link in
        node = add_node(workspace, motor_model, "Hub", type="regular")
        row = connect(workspace, motor_model, "Node", int(node["ID"]),
                      "Block", int(free["ID"]))
        assert "ID" in row

    def test_connect_refuses_what_raptor7_will_not_draw(self, workspace, motor_model):
        """A second link into the End marker: Raptor 7's CheckValidLink
        message, and the model is left as it was."""
        from raptor.core.input_rules import InputRuleViolation
        handle = workspace.get(motor_model)
        free = add_block(workspace, motor_model, "Free")
        end = next(n for n in handle.rbd.section("NODE_DATA").rows if n["Type"] == "3")
        links_before = len(handle.rbd.section("LINK_DATA").rows)
        with pytest.raises(InputRuleViolation, match="more than one connection to an End Node"):
            connect(workspace, motor_model, "Block", int(free["ID"]), "Node", int(end["ID"]))
        assert len(workspace.get(motor_model).rbd.section("LINK_DATA").rows) == links_before


# ---------------------------------------------------------------------------
# configure_* tools
# ---------------------------------------------------------------------------

class TestConfigureSparing:
    def test_mode_and_emergency_slo(self, workspace, motor_model):
        row = configure_sparing(
            workspace, motor_model, "Armature", spare_pool="custom",
            stock=5, restock_every=100.0, emergency=12.0, slo=2)
        assert row["SpType"] == "Custom"
        assert row["Stock"] == "5"
        assert row["ArriveEvery"] == "100.000000"
        assert row["EmerTime"] == "12.000000"
        # Supplying an interval enables the ordering policy; `slo` is the
        # reorder LEVEL, not the 0/1 SLO flag.
        assert row["RSO"] == "1"
        assert row["ESO"] == "1"
        assert row["SLO"] == "1"
        assert row["Level"] == "2"

    @pytest.mark.parametrize("mode,expected",
                             [("infinite", "Infinite"),
                              ("custom", "Custom"),
                              ("pool", "Pooled")])
    def test_mode_mapping(self, workspace, motor_model, mode, expected):
        row = configure_sparing(workspace, motor_model, "Armature",
                                mode=mode)
        assert row["SpType"] == expected


class TestConfigureMaintenance:
    def test_pm_runs_by_default(self, workspace, motor_model):
        row = configure_maintenance(workspace, motor_model, "Armature",
                                    pm_freq=50.0)
        assert row["usesPM"] == "1"
        assert row["PMFreq"] == "50.000000"

    def test_dist_params_trigger_and_flags(self, workspace, motor_model):
        # A block may only name a trigger the model defines.
        from raptor.core.fileio.rbdlib import make_trigger_row
        handle = workspace.get(motor_model)
        handle.rbd.section("TRIGGERS").rows.append(
            make_trigger_row("Trig1"))
        row = configure_maintenance(
            workspace, motor_model, "Armature", pm_freq=10.0,
            pm_duration_dist=19, pm_params=[2.0, 0.0, 0.0],
            trigger_name="Trig1",
            defer_flags={"mis_defer": True, "req_defer": False,
                         "fail_reset": False})
        assert row["Dist"] == "19"
        assert row["Param1#3"] == "2.000000"
        assert row["pmTrig"] == "Trig1"
        assert row["pmTriggered"] == "1"
        assert row["PMMisDefer"] == "1"
        assert row["PMReqDefer"] == "0"
        assert row["PMFailReset"] == "0"


class TestConfigurePhases:
    def _phases(self, n=3):
        return [{"name": f"P{i}", "duration_dist": 8,
                 "params": (5.0, 0.1, 0.0), "mission": 1}
                for i in range(1, n + 1)]

    def test_phases_load_and_simulate(self, workspace):
        model_id = workspace.open_model(
            GOLDEN / "Phasing1" / "input.rbd")["model_id"]
        result = configure_phases(workspace, model_id, self._phases(3))
        assert result["phases"] == 3

        handle = workspace.get(model_id)
        reloaded = load_rbds(str(handle.path))
        assert len(reloaded.section("PHASES").rows) == 3

        from raptor.core.engine.runner import run_simulation
        logs = run_simulation(reloaded, num_runs=1, collect_logs=False)
        assert len(logs) == 1

    def test_assignments_set_phase_cells(self, workspace):
        model_id = workspace.open_model(
            GOLDEN / "Phasing1" / "input.rbd")["model_id"]
        result = configure_phases(
            workspace, model_id, self._phases(3),
            assignments={"Attitude": ["A: 1.0", "A: 1.5", "A: 1.5"],
                         "500path": ["C", "1", "1"]})
        assert result["assigned"] == 2
        reloaded = load_rbds(str(workspace.get(model_id).path))
        att = [b for b in reloaded.section("BLOCK_DATA").rows
               if b.get("blockName") == "Attitude"][0]
        assert att["P 001"] == "A: 1.0"
        assert att["Phasing"] == "1"
        node = [n for n in reloaded.section("NODE_DATA").rows
                if n.get("Name") == "500path"][0]
        assert node["P001"] == "C"
        assert node["Phases"] == "1"

    def test_short_assignment_raises_instead_of_writing_blank_cell(
            self, workspace):
        """A regression test for a bug where an assignment with fewer
        values than phases silently wrote an empty-string cell, which
        the engine can't parse and crashed at `simulate` time with
        "could not convert string to float: ''" -- not at configure
        time, where the mistake is easy to fix. Must fail fast here."""
        from raptor.api.build import BuildError
        model_id = workspace.open_model(
            GOLDEN / "Phasing1" / "input.rbd")["model_id"]
        with pytest.raises(BuildError, match="needs exactly 3 value"):
            configure_phases(
                workspace, model_id, self._phases(3),
                assignments={"Attitude": ["A: 1.0", "A: 1.5"]})

    def test_bare_state_word_raises_instead_of_crashing_the_engine(
            self, workspace):
        """A regression test for the the 2026-09-22 MCP functional test review Test 23 crash:
        an assignment written as bare words ("Cut"/"Active", with no
        ": <value>") passes `configure_phases` silently, but
        `typed.py:_block_event_phase_columns` unconditionally does
        `float(cell.partition(":")[2])` on load -- a colon-less cell
        partitions to an empty value and crashes with the exact same
        "could not convert string to float: ''" as a blank cell. Must
        be rejected at configure time instead."""
        from raptor.api.build import BuildError
        model_id = workspace.open_model(
            GOLDEN / "Phasing1" / "input.rbd")["model_id"]
        with pytest.raises(BuildError, match="needs a .*<code>: <value>"):
            configure_phases(
                workspace, model_id, self._phases(3),
                assignments={"Attitude": ["Active", "Active", "Active"]})

    def test_correctly_formatted_state_still_simulates(self, workspace):
        """The properly-formatted equivalent of the above (colon +
        numeric value, matching real Phasing1 cells like "A: 1.000")
        is accepted and the model still simulates -- this isn't a case
        of the new checks rejecting valid input."""
        model_id = workspace.open_model(
            GOLDEN / "Phasing1" / "input.rbd")["model_id"]
        configure_phases(
            workspace, model_id, self._phases(3),
            assignments={"Attitude": ["A: 1.0", "A: 1.5", "A: 1.5"]})
        handle = workspace.get(model_id)
        reloaded = load_rbds(str(handle.path))
        from raptor.core.engine.runner import run_simulation
        logs = run_simulation(reloaded, num_runs=1, collect_logs=False)
        assert len(logs) == 1


class TestConfigureSystemMirrorsTheExperiment:
    """D-18: `configure_system` wrote ONLY the legacy SYSTEM_DATA columns.

    Both destinations are read: the engine reads the columns, and the
    GUI's run panel prefers `rbd.experiment` whenever it is non-None
    (`run_experiment_mixin.py:116-124`). So an MCP
    `configure_system(num_runs=200)` was invisible to the GUI, and the
    next GUI edit of ANY run setting silently wrote the stale value back
    to `NumRuns` -- reverting the MCP change without a word.

    The GUI's own docstring names this hazard: "the failure mode that had
    the run count editable in three places".
    """

    def test_the_experiment_block_is_populated(self, workspace, motor_model):
        configure_system(workspace, motor_model, num_runs=200,
                         weak_link=True)
        exp = workspace.get(motor_model).rbd.experiment
        assert exp is not None, (
            "configure_system left rbd.experiment unset, so the GUI's run "
            "panel will show a different trial count than the row"
        )
        assert exp["trials"] == 200
        assert "weak" in exp["analyses"]

    def test_a_partial_update_preserves_the_untouched_fields(self, workspace,
                                                            motor_model):
        """Rebuilding the experiment must come from the patched ROW, not
        from the arguments -- otherwise setting one field resets the rest
        to defaults."""
        configure_system(workspace, motor_model, num_runs=200,
                         termination="CycleSim", run_length=250.0)
        # Second call touches only the analyses.
        configure_system(workspace, motor_model, weak_link=True)
        exp = workspace.get(motor_model).rbd.experiment
        assert exp["trials"] == 200, "a partial update lost the trial count"
        assert exp["term_condition"] == 250.0, "a partial update lost the length"
        assert exp["termination_type"] == "CycleSim"
        assert "weak" in exp["analyses"]

    def test_the_two_destinations_agree_afterwards(self, workspace,
                                                   motor_model):
        """The invariant that matters: whatever the GUI reads and whatever
        the engine reads must be the same run."""
        configure_system(workspace, motor_model, num_runs=37, run_length=99.5,
                         start_stats=2.5, weak_link=True, cost=True,
                         capacity=True, termination="TimeSim")
        handle = workspace.get(motor_model)
        row = handle.rbd.section("SYSTEM_DATA").rows[0]
        exp = handle.rbd.experiment
        assert exp["trials"] == int(float(row["NumRuns"]))
        assert f"{exp['term_condition']:.6f}" == row["TermCondition"]
        assert exp["termination_type"] == row["TerminationType"]
        assert f"{exp['start_stats']:.6f}" == row["StartStats"]
        assert ("weak" in exp["analyses"]) == (row["weakAnalysis"] == "1")
        assert ("cost" in exp["analyses"]) == (row["costAnalysis"] == "1")
        assert ("capacity" in exp["analyses"]) == (row["capacityAnalysis"] == "1")

    def test_a_non_experiment_column_does_not_disturb_it(self, workspace,
                                                         motor_model):
        """`display_unit` and `flow_generated` are not run settings; they
        must not cause an experiment to be conjured from a row that was
        never written."""
        configure_system(workspace, motor_model, num_runs=12)
        before = dict(workspace.get(motor_model).rbd.experiment)
        configure_system(workspace, motor_model, display_unit="years")
        assert dict(workspace.get(motor_model).rbd.experiment) == before

    def test_the_gui_reads_back_the_value_that_was_set(self, workspace,
                                                       motor_model):
        """End-to-end: the GUI's own reader, not the raw dict."""
        from raptor.core import run_experiment
        configure_system(workspace, motor_model, num_runs=200)
        rbd = workspace.get(motor_model).rbd
        # This is `RunExperimentMixin._current_experiment`'s logic.
        if rbd.experiment is None:
            rbd.experiment = run_experiment.from_system_row(
                rbd.sections["SYSTEM_DATA"].rows[0])
        assert run_experiment.normalise(rbd.experiment)["trials"] == 200


class TestConfigureSystem:
    def test_full_system_config(self, workspace, motor_model):
        result = configure_system(
            workspace, motor_model, run_length=100.0, num_runs=50,
            termination="CycleSim", start_stats=1.0, units="years",
            weak_link=True, cost=True, capacity=True, gy_threshold=0.9,
            yr_threshold=0.8, system_streams=[1, 2, 3],
            display_unit="years")
        assert result["ok"] is True
        assert "TermCondition" in result["applied"]
        handle = workspace.get(motor_model)
        row = handle.rbd.section("SYSTEM_DATA").rows[0]
        assert row["TermCondition"] == "100.000000"
        assert row["NumRuns"] == "50.000000"
        assert row["TerminationType"] == "CycleSim"
        assert row["weakAnalysis"] == "1"
        assert row["SysStreamsArray"] == "1 2 3"
        assert handle.rbd.display_unit == "years"

    def test_flow_generated_roundtrips_through_rbds(self, workspace,
                                                    motor_model):
        """`capacity_analysis` and `flow_generated` both survive a .rbds
        save/reload."""
        result = configure_system(workspace, motor_model, capacity=True,
                                  flow_generated=100)
        assert "flowGenerated" in result["applied"]
        handle = workspace.get(motor_model)
        assert handle.rbd.section("SYSTEM_DATA").rows[0]["flowGenerated"] == "100"

        reloaded = load_rbds(str(handle.path))
        row = reloaded.section("SYSTEM_DATA").rows[0]
        assert row["flowGenerated"] == "100"
        assert row["capacityAnalysis"] == "1"


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------

class TestDescribe:
    def test_describe_summary(self, workspace, motor_model):
        desc = describe(workspace, motor_model)
        assert desc["blocks"] > 0
        assert desc["nodes"] > 0
        assert "links" in desc

    def test_describe_detail(self, workspace, motor_model):
        desc = describe(workspace, motor_model, detail=True)
        assert len(desc["block_names"]) > 0
        assert len(desc["node_names"]) > 0

    def test_describe_full_topology_and_capacity(self, workspace,
                                                 motor_model):
        """detail='full' exposes link endpoints and caps, node
        type/k/n/full_flow/div_flow, and the system capacity settings."""
        configure_system(workspace, motor_model, capacity=True,
                         flow_generated=100)
        desc = describe(workspace, motor_model, detail="full")

        assert isinstance(desc["links"], list) and desc["links"]
        link = desc["links"][0]
        for key in ("from_type", "from_id", "to_type", "to_id",
                    "nom_flow", "max_flow", "cap_pri"):
            assert key in link, key

        node = desc["node_names"][0]
        for key in ("type", "k", "n", "full_flow", "div_flow"):
            assert key in node, key

        system = desc["system"]
        assert system["capacity_analysis"] is True
        assert system["flow_generated"] == 100
        assert system["units"] == "hours"
        assert system["term_condition"] is not None
        assert system["num_runs"] is not None
        assert desc["link_count"] == len(desc["links"])

    def test_describe_summary_keeps_link_count(self, workspace, motor_model):
        desc = describe(workspace, motor_model)
        assert isinstance(desc["links"], int)
        assert desc["links"] == desc["link_count"]
        assert "system" not in desc



def test_every_advertised_settable_field_resolves():
    """`settable_fields()` feeds the MCP `patch` description and `raptor help set-paths`; each name must resolve."""
    from raptor.api.build import _column_targets, settable_fields
    sections = {"Block": "BLOCK_DATA", "Node": "NODE_DATA", "Event": "EVENT_DATA", "Hier": "HIERARCHY_DATA",
                "Link": "LINK_DATA", "System": "SYSTEM_DATA"}
    fields = settable_fields()
    assert set(fields) == set(sections) and all(fields.values())
    for kind, names in fields.items():
        for name in names:
            assert _column_targets(sections[kind], {}, name), (kind, name)
    assert "fail_params" in fields["Block"] and "num_runs" in fields["System"]

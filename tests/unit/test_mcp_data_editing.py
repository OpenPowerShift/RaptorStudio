"""Editing surface added for the MCP gap audit: distribution names, auxiliary
table CRUD (spare pools, resources, triggers), reference validation, link
deletion/replacement, element moves, phase add/remove/unassign and the
`experiment` spec block.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.build import (
    BuildError, add_block, add_resource, add_spare_pool, add_trigger,
    build_from_spec, configure_phases, connect,
    delete_link, delete_named_row, move_element, update_element,
)
from raptor.api.validate import issues
from raptor.api.workspace import Workspace

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def motor_model(workspace):
    return workspace.open_model(GOLDEN / "MotorDemo" / "input.rbd")["model_id"]


@pytest.fixture
def two_blocks(workspace):
    result = workspace.register_model(build_from_spec({
        "blocks": [
            {"id": 1, "name": "A", "fail_dist": 4, "fail_params": [100, 0, 0]},
            {"id": 2, "name": "B", "fail_dist": 4, "fail_params": [200, 0, 0]},
        ],
        "nodes": [{"id": 3, "type": 1, "name": "start"},
                  {"id": 4, "type": 3, "name": "end"}],
        "links": [{"id": 1, "from": ["Node", 3], "to": ["Block", 1]},
                  {"id": 2, "from": ["Block", 1], "to": ["Block", 2]},
                  {"id": 3, "from": ["Block", 2], "to": ["Node", 4]}],
    }, auto_layout=False), name="TwoBlocks")
    return result["model_id"]


def _errors(rbd):
    return [i for i in issues(rbd) if i.severity == "error"]


class TestDistributionNames:
    def test_add_block_accepts_names(self, workspace, motor_model):
        row = add_block(workspace, motor_model, "FanNamed",
                        fail_dist="Weibull", fail_params=[2, 1000, 0],
                        repair_dist="Fixed", repair_params=[5, 0, 0])
        assert row["FDist"] == "15"
        assert row["RDist"] == "19"

    def test_update_element_accepts_names(self, workspace, motor_model):
        row = update_element(workspace, motor_model, "Armature",
                             {"fail_dist": "lognormal"})
        assert row["FDist"] == "7"

    def test_unknown_name_is_refused(self, workspace, motor_model):
        with pytest.raises(BuildError):
            update_element(workspace, motor_model, "Armature",
                           {"fail_dist": "NotADistribution"})


class TestAuxiliaryTables:
    def test_pool_crud_and_reference(self, workspace, two_blocks):
        add_spare_pool(workspace, two_blocks, "Spares", stock=2)
        update_element(workspace, two_blocks, "A",
                       {"sparing_type": "pool", "spare_pool": "Spares"})
        rbd = workspace.get(two_blocks).rbd
        assert not _errors(rbd)

    def test_dangling_pool_is_refused(self, workspace, two_blocks):
        with pytest.raises(BuildError):
            update_element(workspace, two_blocks, "A",
                           {"sparing_type": "pool", "spare_pool": "Missing"})

    def test_resource_and_trigger_crud(self, workspace, two_blocks):
        add_resource(workspace, two_blocks, "Crew", num_in=2)
        add_trigger(workspace, two_blocks, "Storm", dist="Normal",
                    params=[100, 1, 0])
        update_element(workspace, two_blocks, "A",
                       {"resource_name": "Crew"})
        update_element(workspace, two_blocks, "A",
                       {"pm_freq": 100, "pm_trigger": "Storm"})
        rbd = workspace.get(two_blocks).rbd
        assert not _errors(rbd)

    def test_unknown_resource_is_refused_at_patch(self, workspace, two_blocks):
        with pytest.raises(BuildError):
            update_element(workspace, two_blocks, "A",
                           {"resource_name": "Ghost"})

    def test_delete_refuses_while_referenced(self, workspace, two_blocks):
        add_spare_pool(workspace, two_blocks, "Spares", stock=1)
        update_element(workspace, two_blocks, "A",
                       {"sparing_type": "pool", "spare_pool": "Spares"})
        with pytest.raises(BuildError):
            delete_named_row(workspace, two_blocks, "Spares")
        update_element(workspace, two_blocks, "A", {"sparing_type": "infinite"})
        delete_named_row(workspace, two_blocks, "Spares")

    def test_pool_row_is_patchable_by_name(self, workspace, two_blocks):
        add_spare_pool(workspace, two_blocks, "Spares", stock=1)
        row = update_element(workspace, two_blocks, "Spares", {"stock": 9})
        assert row["Stock"] == "9"


class TestLinksAndMoves:
    def test_delete_link_keeps_elements(self, workspace, two_blocks):
        rbd = workspace.get(two_blocks).rbd
        link_id = rbd.section("LINK_DATA").rows[1]["ID"]
        delete_link(workspace, two_blocks, link_id)
        rbd = workspace.get(two_blocks).rbd
        assert len(rbd.section("LINK_DATA").rows) == 2
        assert len(rbd.section("BLOCK_DATA").rows) == 3  # + default row

    def test_connect_replace(self, workspace, two_blocks):
        connect(workspace, two_blocks, "Node", 3, "Block", 2, replace=True)
        rbd = workspace.get(two_blocks).rbd
        rows = rbd.section("LINK_DATA").rows
        assert sum(1 for r in rows if r.get("FromID") == "3") == 1
        assert any(r.get("ToID") == "2" and r.get("ToType") == "Block"
                   for r in rows)

    def test_move_element_between_hierarchies(self, workspace, two_blocks):
        from raptor.api.build import add_hierarchy
        hier = add_hierarchy(workspace, two_blocks, "Sub")
        move_element(workspace, two_blocks, "B", int(hier["ID"]))
        rbd = workspace.get(two_blocks).rbd
        row = next(r for r in rbd.section("BLOCK_DATA").rows
                   if r.get("blockName") == "B")
        assert row["ParentId"] == str(hier["ID"])

    def test_node_type_change_guard(self, workspace, two_blocks):
        with pytest.raises(BuildError):
            update_element(workspace, two_blocks, "end", {"type": "start"})


class TestPhases:
    def test_add_remove_and_unassign(self, workspace, two_blocks):
        configure_phases(workspace, two_blocks,
                         [{"name": "Summer", "duration_dist": 19,
                           "params": [1000, 0, 0]},
                          {"name": "Winter", "duration_dist": 19,
                           "params": [1000, 0, 0]}],
                         assignments={"A": ["A: 1.000", "A: 0.500"]})
        result = configure_phases(
            workspace, two_blocks,
            [{"name": "Shoulder", "duration_dist": 19,
              "params": [500, 0, 0]}], mode="add")
        assert result["phases"] == 3
        result = configure_phases(workspace, two_blocks, None,
                                  mode="remove", remove=["Winter"])
        assert result["phases"] == 2 and result["removed"] == 1
        result = configure_phases(workspace, two_blocks, None,
                                  unassign=["A"])
        assert result["unassigned"] == 1
        rbd = workspace.get(two_blocks).rbd
        row = next(r for r in rbd.section("BLOCK_DATA").rows
                   if r.get("blockName") == "A")
        assert row.get("Phasing", "0") == "0"


class TestExperimentSpec:
    def test_experiment_is_applied(self):
        rbd = build_from_spec({
            "blocks": [{"id": 1, "name": "A", "fail_dist": 4,
                        "fail_params": [100, 0, 0]}],
            "nodes": [{"id": 2, "type": 1, "name": "start"},
                      {"id": 3, "type": 3, "name": "end"}],
            "links": [{"id": 1, "from": ["Node", 2], "to": ["Block", 1]},
                      {"id": 2, "from": ["Block", 1], "to": ["Node", 3]}],
            "experiment": {"trials": 7, "termination_type": "TimeSim",
                           "term_condition": 500.0},
        }, auto_layout=False)
        assert rbd.experiment["trials"] == 7
        assert rbd.experiment["term_condition"] == 500.0

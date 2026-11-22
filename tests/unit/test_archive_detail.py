"""An `.rbdz` keeps the structured capacity/cost/weak-link detail."""
from __future__ import annotations

import json
from types import SimpleNamespace

from raptor.api.run import archive_detail, restore_archive_detail
from raptor.core.engine.capacity import CapacityNodeStats, CapacityRunReport
from raptor.core.engine.reporting import (
    BlockCostRow, CostRunReport, EventCostRow, HierCostRow, PoolCostRow)


def _run() -> SimpleNamespace:
    nodes = {
        1: CapacityNodeStats(name="Start", node_id=1, min_flow=10,
                             ave_flow=8.5, max_flow=10, min_cap=10,
                             ave_cap=9.0, max_cap=10, usage=0.94,
                             capability=0.9, full_flow=False),
        2: CapacityNodeStats(name="Pump", node_id=2, min_flow=0,
                             ave_flow=8.0, max_flow=10, min_cap=0,
                             ave_cap=10.0, max_cap=10, usage=0.8,
                             capability=1.0, full_flow=False),
    }
    capacity = CapacityRunReport(units_produced=820.0, ave_lost_flow=1.5,
                                flow_generated=10, system_node_id=1,
                                nodes=nodes)
    cost = CostRunReport(
        blocks={2: BlockCostRow(name="Pump", init_spare_cost=1.0,
                                new_spares_cost=2.0, emer_ship_cost=3.0,
                                purchase=10.0, operating=20.0, sparing=6.0,
                                disposal=1.0, total=37.0)},
        spare_pools={"Spares": PoolCostRow(name="Spares", initial=1.0,
                                           new_spares=2.0, emer_ship=0.0,
                                           total=3.0)},
        resources={},
        events={1: EventCostRow(name="Operator", initial=1.0, success=2.0,
                                failure=3.0, total=6.0)},
        hiers={900: HierCostRow(name="Loop", hier_id=900, purchase=10.0,
                                operating=20.0, sparing=6.0, disposal=1.0,
                                events=6.0, total=43.0)},
        system_down_cost=5.0, system_lost_flow_cost=1.5, grand_total=52.5)
    weak_stats = {2: {"name": "Pump", "run": 80.0, "repair": 20.0,
                      "rep_hold": 0.0, "pm": 0.0, "pm_hold": 0.0,
                      "idle": 0.0, "standby": 0.0, "done": 0.0}}
    weak_nodes = {1: {"name": "Start", "type_node": 1, "green": 90.0,
                      "yellow": 0.0, "red": 10.0, "blue": 0.0,
                      "brown": 0.0, "orange": 0.0},
                  2: {"name": "Pump", "type_node": 2, "green": 80.0,
                      "yellow": 0.0, "red": 20.0, "blue": 0.0,
                      "brown": 0.0, "orange": 0.0}}
    weak_events = {1: {"name": "Operator", "run": 90.0, "standby": 0.0,
                       "repair": 10.0, "m_completed": 9}}
    weak_hiers = {900: {"name": "Loop", "out_node_id": 5, "green": 85.0,
                        "yellow": 0.0, "red": 15.0, "blue": 0.0,
                        "brown": 0.0, "orange": 0.0,
                        "down_event_count": 1, "m_completed": 9}}
    return SimpleNamespace(capacity=capacity, cost=cost,
                           weak_stats=weak_stats, weak_nodes=weak_nodes,
                           weak_events=weak_events, weak_hiers=weak_hiers,
                           hier_dsi={900: {"state": "Degraded", "nodes": 3,
                                           "down": 0, "degraded": 1}})


def test_the_detail_survives_json_and_reads_back_as_objects():
    detail = archive_detail(_run())
    # what the archive stores must be plain JSON
    row = json.loads(json.dumps({"archive_detail": detail}))["archive_detail"]
    assert row["capacity"]["flow_generated"] == 10
    assert row["cost"]["blocks"]["2"]["operating"] == 20.0

    back = restore_archive_detail(row)
    cap = back["capacity"]
    assert cap.nodes[1].name == "Start"
    assert cap.nodes[1].ave_flow == 8.5
    assert cap.flow_generated == 10
    assert cap.system_node_id == 1

    cost = back["cost"]
    assert cost.grand_total == 52.5
    assert cost.blocks[2].operating == 20.0
    assert cost.spare_pools["Spares"].total == 3.0
    assert cost.events[1].failure == 3.0
    assert cost.hiers[900].events == 6.0

    # weak-link maps stay dicts: the pages read them with `.get`
    assert back["weak_stats"][2]["run"] == 80.0
    assert back["weak_nodes"][2].get("type_node") == 2
    assert back["weak_events"][1]["m_completed"] == 9
    assert back["weak_hiers"][900]["red"] == 15.0
    assert back["hier_dsi"][900]["state"] == "Degraded"


def test_a_run_without_the_analyses_stores_nothing_extra():
    run = SimpleNamespace(capacity=None, cost=None, weak_stats={},
                          weak_nodes={}, weak_events={}, weak_hiers={},
                          hier_dsi={})
    assert archive_detail(run) == {}
    assert restore_archive_detail({}) == {}


def test_an_older_archive_row_without_detail_restores_nothing():
    assert restore_archive_detail({}) == {}
    assert restore_archive_detail({"availability": 0.99}) == {}


def test_a_hand_edited_detail_is_ignored_not_fatal():
    junk = {
        "capacity": {"nodes": {"pump": {}}, "system_node_id": 1},
        "cost": {"blocks": ["nope"], "spare_pools": "nope"},
        "weak_stats": "nope",
    }
    back = restore_archive_detail(junk)
    assert back["capacity"].nodes == {}
    assert back["cost"].blocks == {}
    assert "weak_stats" not in back

"""The live Descendant Status Indicator (`RBDHierObj.ChangeStatus`/`ChangeDSIStatus`): after every step of a stepped run, each hierarchy's `dsi_state` equals a
recomputation from the raw `status` of the hierarchies nested inside it."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.engine.step_session import StepSession
from raptor.core.fileio.rbd import load_rbd
from raptor.core.model import NodeStatus

GOLD = Path(__file__).resolve().parents[1] / "golden"
SEVERITY = [NodeStatus.ALL_UP, NodeStatus.STANDBY, NodeStatus.DEGRADED, NodeStatus.IDLE,
            NodeStatus.PM, NodeStatus.DOWN]


def _worst(states):
    return max(states, key=SEVERITY.index, default=NodeStatus.ALL_UP)


def _recomputed(hiers):
    children: dict[int, list[int]] = {}
    for h in hiers.values():
        if h.parent_id > 0:
            children.setdefault(h.parent_id, []).append(h.id)

    def dsi(hid):
        # The out node's own status is counted into its hierarchy's counters
        # so a hierarchy's indicator is never better than its own status.
        return _worst([hiers[hid].status, *(signal(c) for c in children.get(hid, ()))])

    def signal(hid):
        return dsi(hid)
    return {hid: dsi(hid) for hid in hiers}


@pytest.mark.parametrize("name,length", [("NestedHierarchyCost", "300"), ("Phasing3", "3"),
                                         ("HierarchyExample", "400")])
def test_live_indicator_matches_a_recomputation_at_every_step(name, length):
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    rbd.section("SYSTEM_DATA").rows[0]["TermCondition"] = length
    s = StepSession(rbd)
    hiers = s.model.hiers_by_id
    assert hiers, "the fixture has hierarchies"
    checked = 0
    while s.step_to_change():
        want = _recomputed(hiers)
        got = {hid: h.dsi_state for hid, h in hiers.items()}
        assert got == want, (s.time, got, want)
        checked += 1
    assert checked > 5

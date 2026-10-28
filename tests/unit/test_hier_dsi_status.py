"""Nested-hierarchy colour bookkeeping: `nodes._hier_change_status` and
`nodes._change_dsi_status`, ports of `RBDHierObj.ChangeStatus` /
`ChangeDSIStatus`. A hierarchy's DSI state is the
worst status among its descendants (Down > NodePM > NodeIdle > Degraded >
NodeStandby > AllUp); its signal to its parent is the worse of its own
status and that DSI state, by `NodeStatusType`'s declaration order."""
from __future__ import annotations

import pytest

from raptor.core.engine.nodes import _change_dsi_status, _hier_change_status
from raptor.core.model import NodeStatus as S, RBDHier


def _tree():
    """grandparent 1 <- parent 2 <- child 3"""
    hiers = {i: RBDHier(id=i, name=f"h{i}", parent_id=p) for i, p in ((1, 0), (2, 1), (3, 2))}
    return hiers


@pytest.mark.parametrize(("status", "counter"), [
    (S.DOWN, "dsi_reds"), (S.PM, "dsi_oranges"), (S.IDLE, "dsi_siennas"),
    (S.DEGRADED, "dsi_yellows"), (S.STANDBY, "dsi_blues"),
])
def test_child_status_rolls_up_to_every_ancestor(status, counter):
    h = _tree()
    _hier_change_status(h[3], status, h)
    assert h[3].status is status and h[3].dsi_signal is status
    assert getattr(h[2], counter) == 1 and h[2].dsi_state is status
    assert getattr(h[1], counter) == 1 and h[1].dsi_state is status
    _hier_change_status(h[3], S.ALL_UP, h)
    assert getattr(h[2], counter) == 0 and h[2].dsi_state is S.ALL_UP
    assert h[1].dsi_state is S.ALL_UP


def test_worst_descendant_wins():
    h = _tree()
    _change_dsi_status(h[1], {S.STANDBY: 1, S.DEGRADED: 1}, h)
    assert h[1].dsi_state is S.DEGRADED
    _change_dsi_status(h[1], {S.IDLE: 1}, h)
    assert h[1].dsi_state is S.IDLE
    _change_dsi_status(h[1], {S.PM: 1}, h)
    assert h[1].dsi_state is S.PM
    _change_dsi_status(h[1], {S.PM: -1, S.IDLE: -1}, h)
    assert h[1].dsi_state is S.DEGRADED


def test_own_status_worse_than_descendants_is_the_signal():
    h = _tree()
    _change_dsi_status(h[2], {S.DEGRADED: 1}, h)       # a descendant of 2 is Yellow
    assert h[2].dsi_signal is S.DEGRADED and h[1].dsi_yellows == 1
    _hier_change_status(h[2], S.DOWN, h)                # 2's own Out node goes Down
    assert h[2].dsi_signal is S.DOWN
    assert (h[1].dsi_yellows, h[1].dsi_reds) == (0, 1)
    _hier_change_status(h[2], S.STANDBY, h)             # own better than DSI again
    assert h[2].dsi_signal is S.DEGRADED
    assert (h[1].dsi_yellows, h[1].dsi_reds) == (1, 0)


def test_top_level_and_missing_hierarchies_are_no_ops():
    h = _tree()
    _hier_change_status(None, S.DOWN, h)
    _change_dsi_status(None, {S.DOWN: 1}, h)
    _hier_change_status(h[1], S.DOWN, h)                # top level: no parent to tell
    assert h[1].status is S.DOWN and h[1].dsi_state is S.ALL_UP
    orphan = RBDHier(id=9, name="h9", parent_id=42)     # parent not in the model
    _hier_change_status(orphan, S.DOWN, h)
    _change_dsi_status(orphan, {S.DOWN: 1}, h)
    assert orphan.status is S.DOWN and orphan.dsi_state is S.DOWN

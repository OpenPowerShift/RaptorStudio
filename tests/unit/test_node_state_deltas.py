"""`nodes._node_change_state_deltas` against Raptor 7's node-status table:
the RunChange/FailChange/IdleChange/StandbyChange/pmChange counts for every
combination of prior status, new status and old/new active status.

`tests/data/node_state_deltas.json` holds the table for all 324 combinations.
It was translated mechanically from Raptor 7's `ChangeNodeState` (not
transcribed by hand), so the port is checked against real's own table."""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from raptor.core.engine.nodes import _node_change_state_deltas
from raptor.core.model import ActiveStatus, NodeStatus

_DATA = json.loads((Path(__file__).resolve().parents[1] / "data" / "node_state_deltas.json")
                   .read_text(encoding="utf-8"))
_TABLE = {k: tuple(v) for k, v in _DATA["rows"].items()}


@pytest.mark.parametrize(("prior", "status", "old_active", "new_active"), list(itertools.product(
    NodeStatus, NodeStatus, ActiveStatus, ActiveStatus)))
def test_node_deltas_match_raptor7_table(prior, status, old_active, new_active):
    want = _TABLE[f"{prior.value}|{status.value}|{old_active.value}|{new_active.value}"]
    assert _node_change_state_deltas(prior, status, old_active, new_active) == want


def test_the_table_covers_every_combination_and_is_not_vacuous():
    assert len(_TABLE) == len(NodeStatus) ** 2 * len(ActiveStatus) ** 2
    assert _TABLE["AllUp|AllUp|Cut|Linked"] == (1, -1, 0, 0, 0)
    assert _TABLE["NodeIdle|NodePM|Active|Active"] == (0, 0, -1, 0, 1)
    assert _TABLE["Down|Down|Active|Active"] == (0, 0, 0, 0, 0)

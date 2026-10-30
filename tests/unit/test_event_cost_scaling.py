"""Per-event work that grew with the model size, and the answers it must keep.

A node-sourced back-flow used to ask every block and node whether its
dependency resolved to the changed node, and `red_change_line` rebuilt its
comment with a five-way status test per block. Both are now cheaper; the
log lines must be the same.
"""
from __future__ import annotations

import glob
import os
import pathlib
from types import SimpleNamespace

import pytest

from raptor.core.engine import orchestrator
from raptor.core.engine.dependency import index_node_dependents
from raptor.core.engine.runner import run_simulation
from raptor.core.engine.system_status import (
    SystemColor, SystemState, red_change_line,
)
from raptor.core.fileio.rbd import load_rbd
from raptor.core.model import ActiveStatus, BlockStatus

GOLD = pathlib.Path(__file__).resolve().parents[1] / "golden"
DEPENDENCY_FIXTURES = sorted(
    os.path.basename(os.path.dirname(p))
    for p in glob.glob(str(GOLD / "*" / "input.rbd"))
    if any(k in p for k in ("Dep", "Cold", "CS_", "Hier")))


def _block(i, op, active=ActiveStatus.ACTIVE):
    return SimpleNamespace(id=i, op_status=op, active_status=active)


def _reference_comment(blocks):
    """`red_change_line`'s comment as it was built before the lookup table."""
    comment = ""
    for block in blocks:
        if block.active_status == ActiveStatus.CUT:
            comment += f"_c{block.id}"
        elif block.op_status == BlockStatus.REPAIRING:
            comment += f"_r{block.id}"
        elif block.op_status in (BlockStatus.REP_HOLD, BlockStatus.PM_HOLD):
            comment += f"_h{block.id}"
        elif block.op_status == BlockStatus.DONE:
            comment += f"_d{block.id}"
        elif block.op_status == BlockStatus.PM:
            comment += f"_p{block.id}"
    return comment


def test_the_red_comment_names_each_block_state():
    blocks = [_block(i, st) for i, st in enumerate(BlockStatus, start=1)]
    blocks.append(_block(90, BlockStatus.REPAIRING, ActiveStatus.CUT))
    state = SystemState(color=SystemColor.RED, last_up_down_change=0.0)
    line = red_change_line(state, 5.0, blocks, events_file=True)
    assert state.red_string == _reference_comment(blocks)
    assert "_c90" in state.red_string       # CUT wins over the block's status
    assert "_c90" in line


def test_a_repeated_red_comment_is_not_logged_twice():
    blocks = [_block(1, BlockStatus.REPAIRING)]
    state = SystemState(color=SystemColor.RED, last_up_down_change=0.0)
    assert red_change_line(state, 5.0, blocks, events_file=True) is not None
    assert red_change_line(state, 6.0, blocks, events_file=True) is None


@pytest.mark.parametrize("fixture", DEPENDENCY_FIXTURES)
def test_indexed_node_dependents_match_the_scan(fixture, monkeypatch):
    rbd = load_rbd(str(GOLD / fixture / "input.rbd"))
    indexed = run_simulation(rbd, num_runs=2)
    monkeypatch.setattr(orchestrator, "index_node_dependents",
                        lambda *a, **k: (None, None))
    scanned = run_simulation(load_rbd(str(GOLD / fixture / "input.rbd")),
                             num_runs=2)
    assert indexed == scanned


def test_the_index_lists_dependents_in_model_order():
    def dep(i, num, kind="RBDNode", link=0):
        return SimpleNamespace(id=i, dependency_num=num, dep_type=kind,
                               downstream_link_id=link)
    blocks = [dep(1, 7), dep(2, -2), dep(3, 7), dep(4, 5, "RBDBlock"),
              dep(5, -1, link=9)]
    links = {9: SimpleNamespace(connect_to_id=7)}
    by_block, by_node = index_node_dependents(blocks, None, links, end_node_id=3)
    assert [b.id for b in by_block[7]] == [1, 3, 5]
    assert [b.id for b in by_block[3]] == [2]
    assert 5 not in by_block and by_node == {}

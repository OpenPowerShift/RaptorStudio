"""Tests for `raptor.core.engine.blocks` against captured engine output.

Golden source: `tests/golden/UniformProbe/legacy/rpt_uniform_event_log_2.txt.gz`.
Its first lines cover a block starting up ("Good"/"InitializeStress="),
failing ("Failed"/"Operating_time="), and entering repair ("Repairing"/
"No_resources_used").
"""
from __future__ import annotations

import gzip
import pathlib

import pytest

from raptor.core.engine.blocks import BlockStatistics, change_block_state, write_block_failed_line
from raptor.core.model import ActiveStatus, BlockStatus, RBDBlock

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _read_golden_raw_lines() -> list[bytes]:
    path = GOLDEN / "UniformProbe" / "legacy" / "rpt_uniform_event_log_2.txt.gz"
    if not path.exists():
        pytest.skip(f"golden fixture not present: {path}")
    with gzip.open(path, "rb") as f:
        return f.read().split(b"\r\n")


class TestChangeBlockStateAgainstGolden:
    def test_initial_good_state_stress_comment_concatenation(self) -> None:
        # Line 4: "0.000000 ... Good ... InitializeStress=1.000000", from
        # the source's `comment + "Stress=" + ...` concatenation
        # (comment="Initialize" + "Stress=1.000000").
        lines = _read_golden_raw_lines()
        expected = lines[4].decode("latin-1")
        block = RBDBlock(name="unnamed")
        stats = BlockStatistics()
        logs, _deltas = change_block_state(
            block, stats, BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize",
            sim_time=0.0, phase_stress=1.0, events_file=True,
        )
        assert logs == [expected]

    def test_repairing_transition(self) -> None:
        # Line 6: "0.924530 ... Repairing ... No_resources_used"
        lines = _read_golden_raw_lines()
        expected = lines[6].decode("latin-1")
        block = RBDBlock(name="unnamed")
        block.op_status = BlockStatus.RUNNING
        block.state_start_time = 0.0
        stats = BlockStatistics()
        logs, _deltas = change_block_state(
            block, stats, BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "No_resources_used",
            sim_time=0.924530, events_file=True,
        )
        assert logs == [expected]

    def test_status_string_mapping_matches_source(self) -> None:
        # Running maps to "Good"; every other status uses its own name or a
        # close variant.
        block = RBDBlock(name="x")
        stats = BlockStatistics()
        logs, *_ = change_block_state(
            block, stats, BlockStatus.RUNNING, ActiveStatus.ACTIVE, "c",
            sim_time=0.0, events_file=True,
        )
        assert "Good" in logs[0]


class TestWriteBlockFailedLine:
    def test_matches_golden_failed_line(self) -> None:
        # Line 5: "0.924530 ... Failed ... Operating_time=0.924530"
        lines = _read_golden_raw_lines()
        expected = lines[5].decode("latin-1")
        block = RBDBlock(name="unnamed")
        got = write_block_failed_line(block, sim_time=0.924530, operating_time=0.924530)
        assert got == expected


class TestCostAndWeakStats:
    def test_operating_cost_accrues_while_running(self) -> None:
        block = RBDBlock(name="x", operating_cost=2.0)
        block.op_status = BlockStatus.RUNNING
        block.state_start_time = 0.0
        stats = BlockStatistics()
        change_block_state(
            block, stats, BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "Failed",
            sim_time=10.0, cost_analysis=True,
        )
        assert stats.op_cost == pytest.approx(20.0)

    def test_run_time_accrues_for_weak_analysis(self) -> None:
        block = RBDBlock(name="x")
        block.op_status = BlockStatus.RUNNING
        block.state_start_time = 0.0
        stats = BlockStatistics()
        change_block_state(
            block, stats, BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "Failed",
            sim_time=7.5, weak_analysis=True,
        )
        assert stats.run_time == pytest.approx(7.5)


class TestChangeCascadeComputation:
    """RunChange/FailChange/etc. — not acted on (see module docstring) but
    computed faithfully; check a few known transitions from the source."""

    def test_running_to_repairing_is_run_minus_fail_plus(self) -> None:
        block = RBDBlock(name="x")
        block.op_status = BlockStatus.RUNNING
        block.active_status = ActiveStatus.ACTIVE
        stats = BlockStatistics()
        _logs, (run_change, fail_change, idle_change, standby_change, pm_change) = change_block_state(
            block, stats, BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "x", sim_time=0.0,
        )
        assert (run_change, fail_change, idle_change, standby_change, pm_change) == (-1, 1, 0, 0, 0)

    def test_repairing_to_running_is_run_plus_fail_minus(self) -> None:
        block = RBDBlock(name="x")
        block.op_status = BlockStatus.REPAIRING
        block.active_status = ActiveStatus.ACTIVE
        stats = BlockStatistics()
        _logs, (run_change, fail_change, *_rest) = change_block_state(
            block, stats, BlockStatus.RUNNING, ActiveStatus.ACTIVE, "x", sim_time=0.0,
        )
        assert (run_change, fail_change) == (1, -1)

    def test_no_change_when_linked_stays_linked(self) -> None:
        block = RBDBlock(name="x")
        block.op_status = BlockStatus.RUNNING
        block.active_status = ActiveStatus.LINKED
        stats = BlockStatistics()
        _logs, changes = change_block_state(
            block, stats, BlockStatus.RUNNING, ActiveStatus.LINKED, "x", sim_time=0.0,
        )
        assert changes == (0, 0, 0, 0, 0)

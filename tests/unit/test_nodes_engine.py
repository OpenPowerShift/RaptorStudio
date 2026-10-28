"""Tests for raptor.core.engine.nodes against real captured behavior.

Golden source: tests/golden/UniformProbe/legacy/rpt_uniform_event_log_2.txt.gz
— the "end" node's Bad/Good transitions as its one incoming link (from the
block, path id 2) goes down and back up.
"""
from __future__ import annotations

import gzip
import pathlib

import pytest

from raptor.core.engine.nodes import (
    IncomingLink, NodeStatistics, change_node_state, determine_node_status, reconfigure_node,
)
from raptor.core.model import ActiveStatus, Link, LinkStatus, NodeStatus, RBDNode

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _read_golden_raw_lines() -> list[bytes]:
    path = GOLDEN / "UniformProbe" / "legacy" / "rpt_uniform_event_log_2.txt.gz"
    if not path.exists():
        pytest.skip(f"golden fixture not present: {path}")
    with gzip.open(path, "rb") as f:
        return f.read().split(b"\r\n")


class TestDetermineNodeStatusAgainstGolden:
    def test_single_link_down_produces_bad(self) -> None:
        # Line 7: "0.924530 ... Node ... end ... Bad ... 0-1/1_df2"
        lines = _read_golden_raw_lines()
        expected = lines[7].decode("latin-1")
        node = RBDNode(node_name="end")
        result = determine_node_status(
            node, [IncomingLink(path_id=2, status=LinkStatus.DOWN)],
            good_paths_required=1, sim_time=0.924530, events_file=True,
        )
        assert result.log_line == expected
        assert result.new_status == NodeStatus.DOWN

    def test_single_link_up_produces_good(self) -> None:
        # Line 11: "1.109184 ... Node ... end ... Good ... 1-1/1_ur2"
        lines = _read_golden_raw_lines()
        expected = lines[11].decode("latin-1")
        node = RBDNode(node_name="end")
        result = determine_node_status(
            node, [IncomingLink(path_id=2, status=LinkStatus.UP)],
            good_paths_required=1, sim_time=1.109184, events_file=True,
        )
        assert result.log_line == expected
        assert result.new_status == NodeStatus.ALL_UP


class TestDetermineNodeStatusLogic:
    """K-out-of-N logic, not fixture-verified beyond the 1-of-1 case above
    — structural tests derived from reading the source's CASE structure."""

    def _links(self, *statuses: LinkStatus) -> list[IncomingLink]:
        return [IncomingLink(path_id=i + 1, status=s) for i, s in enumerate(statuses)]

    def test_more_required_than_connected_is_down(self) -> None:
        node = RBDNode(node_name="n")
        result = determine_node_status(node, self._links(LinkStatus.UP), good_paths_required=2, sim_time=0.0)
        assert result.new_status == NodeStatus.DOWN

    def test_all_up_when_every_link_runs(self) -> None:
        node = RBDNode(node_name="n")
        result = determine_node_status(
            node, self._links(LinkStatus.UP, LinkStatus.UP), good_paths_required=2, sim_time=0.0,
        )
        assert result.new_status == NodeStatus.ALL_UP
        assert result.num_good_paths == 2

    def test_degraded_when_some_but_not_all_run_above_threshold(self) -> None:
        # 2-of-3 required, 2 up + 1 down: runs(2) >= required(2), but
        # runs+standbys(2) != econnect(3) -> Degraded, not Good.
        node = RBDNode(node_name="n")
        result = determine_node_status(
            node, self._links(LinkStatus.UP, LinkStatus.UP, LinkStatus.DOWN),
            good_paths_required=2, sim_time=0.0,
        )
        assert result.new_status == NodeStatus.DEGRADED

    def test_standby_link_counted_when_runs_short(self) -> None:
        node = RBDNode(node_name="n")
        result = determine_node_status(
            node, self._links(LinkStatus.STANDBY), good_paths_required=1, sim_time=0.0,
        )
        assert result.new_status == NodeStatus.STANDBY

    def test_switching_link_counts_as_failed_not_up(self) -> None:
        node = RBDNode(node_name="n")
        link = IncomingLink(path_id=1, status=LinkStatus.UP, switching=True)
        result = determine_node_status(node, [link], good_paths_required=1, sim_time=0.0)
        assert result.new_status == NodeStatus.DOWN
        assert "_dsw1" in result.link_string


class TestReconfigureNodeAgainstGolden:
    """Drives reconfigure_node's link-status-update half (:
    2199-2261) through the same two real transitions used above, but via
    the delta-based entry point instead of pre-built IncomingLink lists —
    proves the accumulator bookkeeping produces the same result."""

    def _node_and_link(self) -> tuple[RBDNode, dict[int, Link]]:
        node = RBDNode(node_name="end", good_paths=1, in_paths_array=[2])
        link = Link(object_num=2, status=LinkStatus.UP)
        return node, {2: link}

    def test_fail_change_produces_bad(self) -> None:
        lines = _read_golden_raw_lines()
        expected = lines[7].decode("latin-1")
        node, links_by_id = self._node_and_link()
        result = reconfigure_node(
            node, links_by_id, run_change=-1, fail_change=1, idle_change=0,
            standby_change=0, pm_change=0, in_path=2, sim_time=0.924530, events_file=True,
        )
        assert result is not None
        assert result.log_line == expected
        assert links_by_id[2].status == LinkStatus.DOWN

    def test_recovery_after_fail_produces_good(self) -> None:
        lines = _read_golden_raw_lines()
        expected = lines[11].decode("latin-1")
        node, links_by_id = self._node_and_link()
        reconfigure_node(
            node, links_by_id, run_change=-1, fail_change=1, idle_change=0,
            standby_change=0, pm_change=0, in_path=2, sim_time=0.924530, events_file=True,
        )
        result = reconfigure_node(
            node, links_by_id, run_change=1, fail_change=-1, idle_change=0,
            standby_change=0, pm_change=0, in_path=2, sim_time=1.109184, events_file=True,
        )
        assert result is not None
        assert result.log_line == expected
        assert links_by_id[2].status == LinkStatus.UP

    def test_no_change_returns_none(self) -> None:
        # fails accumulates 1 then -1+1=1 again on a second failure delta
        # applied without an intervening recovery: link stays Down, so no
        # re-aggregation/log line is produced (mirrors the source's
        # linkStatusChanged guard).
        node, links_by_id = self._node_and_link()
        reconfigure_node(
            node, links_by_id, run_change=-1, fail_change=1, idle_change=0,
            standby_change=0, pm_change=0, in_path=2, sim_time=1.0, events_file=True,
        )
        result = reconfigure_node(
            node, links_by_id, run_change=0, fail_change=1, idle_change=0,
            standby_change=-1, pm_change=0, in_path=2, sim_time=2.0, events_file=True,
        )
        assert result is None

    def test_unknown_in_path_raises(self) -> None:
        node, links_by_id = self._node_and_link()
        with pytest.raises(ValueError):
            reconfigure_node(
                node, links_by_id, run_change=-1, fail_change=1, idle_change=0,
                standby_change=0, pm_change=0, in_path=999, sim_time=1.0, events_file=True,
            )


class TestChangeNodeStateWeakStats:
    def test_red_time_accumulates_from_down(self) -> None:
        node = RBDNode(node_name="n")
        node.status = NodeStatus.DOWN
        node.state_start_time = 0.0
        stats = NodeStatistics()
        change_node_state(
            node, stats, NodeStatus.ALL_UP, ActiveStatus.ACTIVE, sim_time=6.0, weak_analysis=True,
        )
        assert stats.red_time == pytest.approx(6.0)
        assert node.status == NodeStatus.ALL_UP

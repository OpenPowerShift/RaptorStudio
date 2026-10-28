"""Hand-calculated tests for the system-status classification functions.

Covers the two independently defined node-transition classifiers (which
intentionally disagree on NodeIdle), plus the event fail-count delta and
system-colour determination.
"""
from __future__ import annotations


from raptor.core.engine.system_status import (
    end_node_system_up_transition,
    event_fail_count_delta,
    analyze_system,
    SystemState,
    SystemColor,
)
from raptor.core.engine.dependency import node_dependency_trigger
from raptor.core.model import (
    EventStatus, NodeStatus,
)


# ============================================================
# end_node_system_up_transition -- Raptor 7
# ============================================================
# _UP_LIKE_NODE = {AllUp, Degraded, Standby}
# _DOWN_LIKE_NODE = {Down, Idle, PM}
#
# NodeIdle is down-like here (for Green/Yellow/Red purposes) but
# recovery-like in node_dependency_trigger below (for dependency
# dispatch). The disagreement is intentional and documented in both
# docstrings.

class TestEndNodeSystemUpDown:
    """Port of ChangeNodeState's typeNode=3 End-node trigger."""

    def test_up_to_down(self):
        assert end_node_system_up_transition(NodeStatus.ALL_UP, NodeStatus.DOWN) is False

    def test_up_to_idle(self):
        # Idle is down-like for system status.
        assert end_node_system_up_transition(NodeStatus.ALL_UP, NodeStatus.IDLE) is False

    def test_degraded_to_pm(self):
        assert end_node_system_up_transition(NodeStatus.DEGRADED, NodeStatus.PM) is False

    def test_standby_to_down(self):
        assert end_node_system_up_transition(NodeStatus.STANDBY, NodeStatus.DOWN) is False

    def test_down_to_up(self):
        assert end_node_system_up_transition(NodeStatus.DOWN, NodeStatus.ALL_UP) is True

    def test_pm_to_degraded(self):
        assert end_node_system_up_transition(NodeStatus.PM, NodeStatus.DEGRADED) is True

    def test_idle_to_all_up(self):
        # Idle->AllUp: Idle was down-like, so this is an up transition.
        assert end_node_system_up_transition(NodeStatus.IDLE, NodeStatus.ALL_UP) is True

    def test_no_edge_same_status(self):
        assert end_node_system_up_transition(NodeStatus.ALL_UP, NodeStatus.ALL_UP) is None

    def test_no_edge_both_up_like(self):
        # AllUp->Degraded: both up-like, no crossing.
        assert end_node_system_up_transition(NodeStatus.ALL_UP, NodeStatus.DEGRADED) is None

    def test_no_edge_both_down_like(self):
        # Down->Idle: both down-like, no crossing.
        assert end_node_system_up_transition(NodeStatus.DOWN, NodeStatus.IDLE) is None


# ============================================================
# node_dependency_trigger -- Raptor 7
# ============================================================
# _NODE_DOWN_LIKE = {Down, PM}
# _NODE_RECOVERY_LIKE = {AllUp, Degraded, Idle}
#
# NodeIdle is recovery-like here (dependency dispatch) but down-like in
# end_node_system_up_transition. The disagreement is intentional.

class TestNodeDependencyTrigger:

    def test_recovery_trigger(self):
        assert node_dependency_trigger(NodeStatus.DOWN, NodeStatus.ALL_UP) == "AllUp"

    def test_down_trigger(self):
        assert node_dependency_trigger(NodeStatus.ALL_UP, NodeStatus.DOWN) == "Down"

    def test_idle_is_recovery(self):
        # NodeIdle counts as a recovery trigger for dependency dispatch
        # (unlike end_node_system_up_transition's down-like mapping).
        assert node_dependency_trigger(NodeStatus.PM, NodeStatus.IDLE) == "AllUp"

    def test_no_change_returns_none(self):
        assert node_dependency_trigger(NodeStatus.ALL_UP, NodeStatus.ALL_UP) is None

    def test_degraded_to_all_up_within_recovery_no_trigger(self):
        # Both are recovery-like → no dependency dispatch fires.
        assert node_dependency_trigger(NodeStatus.DEGRADED, NodeStatus.ALL_UP) is None

    def test_all_up_to_pm(self):
        # PM is down-like
        assert node_dependency_trigger(NodeStatus.ALL_UP, NodeStatus.PM) == "Down"

    def test_degraded_stays_degraded_no_trigger(self):
        assert node_dependency_trigger(NodeStatus.DEGRADED, NodeStatus.DEGRADED) is None


# ============================================================
# The NodeIdle disagreement (documented design decision)
# ============================================================

class TestNodeIdleDisagreement:
    """NodeIdle is down-like in end_node_system_up_transition but
    recovery-like in node_dependency_trigger. Both are correct -- they
    serve different purposes."""

    def test_end_treats_idle_as_down(self):
        # For Green/Yellow/Red color tracking, Idle means degraded
        result = end_node_system_up_transition(NodeStatus.ALL_UP, NodeStatus.IDLE)
        assert result is False  # going down

    def test_dependency_treats_idle_as_recovery(self):
        # For dependency dispatch, Idle means "can recover dependents"
        result = node_dependency_trigger(NodeStatus.PM, NodeStatus.IDLE)
        assert result == "AllUp"  # going up


# ============================================================
# event_fail_count_delta -- Raptor 7
# ============================================================

class TestEventFailCountDelta:

    def test_armed_to_failure_plus1(self):
        assert event_fail_count_delta(EventStatus.ARMED, EventStatus.FAILURE) == 1

    def test_success_to_failure_plus1(self):
        assert event_fail_count_delta(EventStatus.SUCCESS, EventStatus.FAILURE) == 1

    def test_failure_to_armed_minus1(self):
        assert event_fail_count_delta(EventStatus.FAILURE, EventStatus.ARMED) == -1

    def test_failure_to_success_minus1(self):
        assert event_fail_count_delta(EventStatus.FAILURE, EventStatus.SUCCESS) == -1

    def test_no_change_zero(self):
        assert event_fail_count_delta(EventStatus.ARMED, EventStatus.ARMED) == 0
        assert event_fail_count_delta(EventStatus.SUCCESS, EventStatus.SUCCESS) == 0


# ============================================================
# analyze_system -- Raptor 7
# ============================================================

class TestAnalyzeSystem:

    def _state(self, failed: int, up: bool):
        s = SystemState()
        s.num_currently_failed = failed
        s.system_up = up
        return s

    def test_green_when_no_failures_and_up(self):
        s = self._state(failed=0, up=True)
        assert analyze_system(s) == SystemColor.GREEN

    def test_yellow_when_failures_and_up(self):
        s = self._state(failed=1, up=True)
        assert analyze_system(s) == SystemColor.YELLOW

    def test_red_when_not_up(self):
        s = self._state(failed=3, up=False)
        assert analyze_system(s) == SystemColor.RED

    def test_red_when_zero_failed_but_not_up(self):
        # Edge: zero failures but system not up → still Red
        s = self._state(failed=0, up=False)
        assert analyze_system(s) == SystemColor.RED

    def test_many_failures_still_yellow_if_up(self):
        s = self._state(failed=99, up=True)
        assert analyze_system(s) == SystemColor.YELLOW

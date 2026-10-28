""""Do not count failures which occur at time zero" (`IgnoreStartFail`, `dZeroFail`).

Source: when the system turns Red at SimTime 0 with
the option on, `CountFailure := FALSE` and no `SysTimeToFail` sample is taken;
the next recovery sets `CountFailure` back to TRUE instead of counting a FailSim
failure. Captured against real Raptor with
`ZeroFailTimeProbe` / `ZeroFailFailSimProbe` (a system that is Red at t=0 because
both blocks start failed); the report and event-log comparisons live in the
golden fidelity tests. These tests pin the rule itself.
"""
from __future__ import annotations

import pytest

from raptor.core.engine.system_status import (SystemColor, SystemState,
                                              change_system_status)
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from pathlib import Path

GOLD = Path(__file__).resolve().parents[1] / "golden"


def _go_red(state, t):
    change_system_status(state, SystemColor.RED, t, [], events_file=False)


@pytest.mark.parametrize("ignore,t,counted", [
    (True, 0.0, False),     # the one case that is skipped
    (False, 0.0, True),     # option off: counted as before
    (True, 0.000001, True),  # not time zero
    (True, 5.0, True),
])
def test_only_a_time_zero_failure_with_the_option_on_is_skipped(
        ignore, t, counted):
    s = SystemState(ignore_zero_time_failures=ignore)
    _go_red(s, t)
    assert (s.sys_fail_stat.count == 1) is counted
    assert s.count_failure is counted
    # LastUpDownChange moves either way
    assert s.last_up_down_change == t


def test_a_later_failure_does_not_touch_count_failure():
    s = SystemState(ignore_zero_time_failures=True)
    _go_red(s, 0.0)
    assert s.count_failure is False
    change_system_status(s, SystemColor.GREEN, 3.0, [], events_file=False)
    _go_red(s, 7.0)
    assert s.sys_fail_stat.count == 1      # the t=7 failure IS counted


def _final(name, **over):
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    for k, v in over.items():
        rbd.sections["SYSTEM_DATA"].rows[0][k] = v
    data = []
    run_simulation_with_reports(rbd, report_sink=data, date="x")
    return data


def test_the_option_removes_exactly_one_failure_per_run_in_a_timed_run():
    on = _final("ZeroFailTimeProbe")
    off = _final("ZeroFailTimeProbe", IgnoreStartFail="0")
    assert [d.num_system_failures for d in off] == \
        [d.num_system_failures + 1 for d in on]
    # availability is untouched: the option changes counting, not states
    assert [round(d.availability, 9) for d in on] == \
        [round(d.availability, 9) for d in off]


def test_a_failsim_run_needs_one_more_real_failure_with_the_option_on():
    on = _final("ZeroFailFailSimProbe")
    off = _final("ZeroFailFailSimProbe", IgnoreStartFail="0")
    # Run 1 draws identically either way until it stops; with the option off
    # the t=0 failure is one of the 3, so it stops earlier. (Later runs start
    # from different stream positions, so only run 1 is comparable.)
    assert on[0].ending_sim_time > off[0].ending_sim_time

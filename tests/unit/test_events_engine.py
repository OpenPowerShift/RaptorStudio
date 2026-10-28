"""Tests for `raptor.core.engine.events`.

The format tests compare against captured engine bytes
(`tests/golden/Events/legacy/rpt_events.txt`); the state tests replay the
same event configuration.
"""
from __future__ import annotations

import gzip
import pathlib

import pytest

from tests.unit.log_compare import read_capture_bytes

from raptor.core.engine.events import EventStatistics, change_event_state, conduct_event, format_event_log_line
from raptor.core.model import ActiveStatus, EventStatus, RBDEvent
from raptor.core.rng import RandomGen, RaptorStream, fetch_raptor_seed

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _read_text_maybe_gz(path: pathlib.Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="latin-1") as f:
            return f.read()
    return path.read_text(encoding="latin-1")


class TestFormatEventLogLine:
    def test_armed_initialize_line_byte_exact(self) -> None:
        raw_lines = read_capture_bytes(GOLDEN / "Events" / "legacy" / "rpt_events.txt").split(b"\r\n")
        # line index 4 (0-indexed) is "0.000000 ... Event ... P=0.91 ... Armed ... Initialize"
        expected = raw_lines[4].decode("latin-1")
        got = format_event_log_line(0.0, "Event", "P=0.91", "Armed", "Initialize")
        assert got == expected

    def test_success_fired_line_byte_exact(self) -> None:
        raw_lines = read_capture_bytes(GOLDEN / "Events" / "legacy" / "rpt_events.txt").split(b"\r\n")
        expected = raw_lines[5].decode("latin-1")
        got = format_event_log_line(0.0, "Event", "P=0.91", "Success", "Fired")
        assert got == expected

    def test_long_name_gets_truncated_to_20_chars(self) -> None:
        line = format_event_log_line(1.5, "Event", "a-very-long-event-name-indeed", "Armed", "x")
        # 20-char field width, plus 2 spaces, plus tab
        assert "a-very-long-event-na  \t" in line


class TestConductEventAgainstFixtureConfig:
    """Replays the exact block/scenario setup from tests/golden/Events —
    same distro/stream config, same starting conditions."""

    def _event(self, name: str, fail_mean: float) -> RBDEvent:
        # Mirrors tests/golden/Events/input.rbd's event rows: Distro=17
        # (Bernoulli), single param = probability of success.
        return RBDEvent(
            name=name, fail_distro=17, fail_mean=fail_mean,
            operating_cost=1.0, repairing_cost=1.0,
        )

    def test_probability_one_always_succeeds(self) -> None:
        # Bernoulli(1.0) = UniformReal(0,1) <= 1.0, always true regardless
        # of the draw — deterministic without needing to control the RNG.
        event = self._event("P=1.00", 1.0)
        stats = EventStatistics()
        rng = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
        logs, _rc, _fc = conduct_event(event, stats, rng, sim_time=0.0, events_file=True)
        assert event.op_status == EventStatus.SUCCESS
        assert event.active_status == ActiveStatus.ACTIVE
        assert len(logs) == 1
        assert "Success" in logs[0]
        assert logs[0].endswith("Fired")

    def test_probability_zero_always_fails(self) -> None:
        # Bernoulli(0.0) = UniformReal(0,1) <= 0.0 — true only if the draw
        # is exactly 0.0, which a continuous uniform draw essentially
        # never produces — deterministic failure without mocking the RNG.
        event = self._event("P=0.00", 0.0)
        stats = EventStatistics()
        rng = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
        logs, _rc, _fc = conduct_event(event, stats, rng, sim_time=0.0, events_file=True)
        assert event.op_status == EventStatus.FAILURE
        assert len(logs) == 1
        assert "Failed" in logs[0]

    def test_initialize_then_conduct_matches_fixture_shape(self) -> None:
        # Full Begin_Run_1 shape for one event, format-for-format against
        # the captured file's first two lines for P=0.91 (see
        # TestFormatEventLogLine). This checks the state machine produces
        # the right inputs to that formatter, not just the formatter.
        event = self._event("P=0.91", 0.91)
        stats = EventStatistics()
        armed_logs, _, _ = change_event_state(
            event, stats, EventStatus.ARMED, ActiveStatus.ACTIVE, "Initialize",
            sim_time=0.0, events_file=True,
        )
        assert armed_logs == [format_event_log_line(0.0, "Event", "P=0.91", "Armed", "Initialize")]

        rng = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
        fired_logs, _rc, _fc = conduct_event(event, stats, rng, sim_time=0.0, events_file=True)
        # action is either Success or Failed depending on the (real,
        # unmocked) draw — both are valid outcomes, just confirm the line
        # is well-formed and uses the real formatter.
        assert fired_logs[0] in (
            format_event_log_line(0.0, "Event", "P=0.91", "Success", "Fired"),
            format_event_log_line(0.0, "Event", "P=0.91", "Failed", "Fired"),
        )


class TestWeakLinkStats:
    def test_standby_time_accumulates_from_armed(self) -> None:
        event = RBDEvent(name="e", fail_distro=19, fail_mean=1.0)
        stats = EventStatistics()
        event.op_status = EventStatus.ARMED
        event.state_start_time = 0.0
        change_event_state(
            event, stats, EventStatus.SUCCESS, ActiveStatus.ACTIVE, "Fired",
            sim_time=5.0, weak_analysis=True,
        )
        assert stats.standby_time == pytest.approx(5.0)
        assert event.state_start_time == 5.0

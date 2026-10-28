"""`eventlog.LogLine` and `EventLog`: a run's event log as typed records an
observer can follow, with the text unchanged."""
from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from raptor.core.engine.driver import SingleRunDriver, build_engine_ctx
from raptor.core.engine.eventlog import EventLog, LogLine, format_event_log_line
from raptor.core.fileio.rbd import load_rbd

GOLDEN = Path(__file__).resolve().parents[1] / "golden"


def test_log_line_is_its_text_and_keeps_its_fields() -> None:
    line = format_event_log_line(12.5, "Block", "Pump", "Failed", "Hour meter")
    assert isinstance(line, str)
    assert line == ("12.500000         \tBlock    \tPump                  \t"
                    "Failed           \tHour meter")
    assert (line.time, line.type, line.name, line.action, line.comment) == (
        12.5, "Block", "Pump", "Failed", "Hour meter")


def test_log_line_survives_pickling() -> None:
    # Parallel workers return their runs' lines to the parent process.
    line = format_event_log_line(1.0, "Node", "Out", "State_change", "Down")
    back = pickle.loads(pickle.dumps(line))
    assert back == line and isinstance(back, LogLine)
    assert (back.time, back.name, back.comment) == (1.0, "Out", "Down")


def test_event_log_tells_observers_about_every_line_added() -> None:
    log = EventLog()
    seen: list[str] = []
    log.subscribe(seen.append)
    log.append("a")
    log.extend(["b", "c"])
    log += ["d"]
    log.insert(0, "e")
    assert seen == ["a", "b", "c", "d", "e"]
    assert log == ["e", "a", "b", "c", "d"]


def test_event_log_without_observers_is_a_plain_list() -> None:
    log = EventLog(["b"])
    log.append("c")
    log.insert(0, "a")
    log.extend(["d"])
    log += ["e"]
    assert log == ["a", "b", "c", "d", "e"]


@pytest.mark.parametrize("fixture", ["TwoBlockRedundancy", "CS_Probe", "Events"])
def test_a_run_reports_every_line_as_a_log_line(fixture: str) -> None:
    _, ctx = build_engine_ctx(load_rbd(str(GOLDEN / fixture / "input.rbd")), num_runs=1)
    driver = SingleRunDriver(ctx, 0)
    seen: list[str] = []
    driver.logs.subscribe(seen.append)
    driver.kickoff()
    driver.advance_to_end()
    driver.finalize()
    assert seen == list(driver.logs)
    assert seen and all(isinstance(line, LogLine) for line in seen)

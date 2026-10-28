"""The Avant-garde tab's event-log filter (boxes A-D and the one-component detail) against
real captures of `Phasing3` (2 cycles x 2 runs): box C only, box D only, box B plus the
component `Attitude` (the combo's text `Attitude block - 005`, cut at its first space)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.engine.eventlog import event_log_row_prints, format_event_log_file
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden" / "Phasing3"


def _logs():
    rbd = load_rbd(str(GOLD / "input.rbd"))
    row = rbd.section("SYSTEM_DATA").rows[0]
    row["NumRuns"] = "2"
    row["TermCondition"] = "2.000000"
    return run_simulation(rbd, num_runs=2)


@pytest.mark.parametrize("stem,kw", [
    ("system", {"system": True}),
    ("phases", {"phases": True}),
    ("nodes_component", {"nodes": True, "component": "Attitude"}),
])
def test_filtered_file_matches_real(stem, kw):
    real = (GOLD / "legacy" / f"eventlog_{stem}_Phasing3.txt").read_bytes().decode("latin-1")
    real = real.replace("\r", "").split("\n")
    ours = [str(x) for x in format_event_log_file(_logs(), "input.rbd", "DATE", **kw)]
    # The date in the header is the capture's; everything after it is exact.
    assert ours[1:] == real[1:len(ours)]
    assert real[len(ours):] in ([], [""])


def test_begin_run_only_with_system_box():
    for kw, expect in [({}, True), ({"system": True}, True), ({"phases": True}, False),
                       ({"blocks": True}, False), ({"nodes": True, "blocks": True}, False)]:
        assert event_log_row_prints("System", "Begin_Run_1", **kw) is expect


def test_all_off_prints_everything():
    for t in ("Block", "Node", "System", "Pool", "Trigger", "Hier", "Event"):
        assert event_log_row_prints(t, "x")


@pytest.mark.parametrize("a,b,c,d", [(a, b, c, d) for a in (0, 1) for b in (0, 1)
                                     for c in (0, 1) for d in (0, 1)])
def test_truth_table(a, b, c, d):
    kw = dict(blocks=bool(a), nodes=bool(b), system=bool(c), phases=bool(d))
    if not (a or b or c or d):
        assert event_log_row_prints("Event", "x", **kw)
        return
    assert event_log_row_prints("Block", "x", **kw) is bool(a)
    assert event_log_row_prints("Node", "x", **kw) is bool(b)
    assert event_log_row_prints("Pool", "x", **kw) is bool(c)
    assert event_log_row_prints("System", "Other", **kw) is bool(c)
    assert event_log_row_prints("System", "Phase_change", **kw) is bool(d or c)
    assert event_log_row_prints("Event", "x", **kw) is False


def test_component_matches_by_exact_name_across_types():
    assert event_log_row_prints("Event", "Attitude", blocks=True, component="Attitude")
    assert not event_log_row_prints("Event", "Attitude block", blocks=True, component="Attitude")


# --- reachability: the developer options change what `api.run` writes -------------------

def _events(tmp_path, monkeypatch, features=None, **kw):
    from raptor.api.run import run, section_path
    from raptor.api.workspace import Workspace
    if features is None:
        monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    else:
        monkeypatch.setenv("RAPTOR_DEV_FEATURES", features)
    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "input.rbd")["model_id"]
    res = run(ws, mid, num_runs=2, timeout_s=60, confirm_long_run=True,
              collect_events=True, **kw)
    return section_path(ws.root / "runs" / res.run_id, "events").read_text()


def test_api_run_writes_file_header_and_begin_run(tmp_path, monkeypatch):
    text = _events(tmp_path, monkeypatch)
    assert text.startswith("Event_Log")
    assert "Begin_Run_1" in text and "Begin_Run_2" in text


def test_api_run_filter_from_environment(tmp_path, monkeypatch):
    text = _events(tmp_path, monkeypatch, "event_log_filter=phase")
    body = [l for l in text.splitlines()[3:] if l.strip()]
    assert body and all("Phase_change" in l for l in body)


def test_api_run_filter_from_argument(tmp_path, monkeypatch):
    from raptor.core.dev_options import DeveloperOptions
    text = _events(tmp_path, monkeypatch,
                   developer=DeveloperOptions(event_log_component="Attitude"))
    body = [l for l in text.splitlines()[3:] if l.strip()]
    assert body and all("Attitude" in l.split("\t")[2] for l in body)


def test_component_is_cut_at_the_first_space():
    # The combo's text ("Attitude block - 005") is what the user picks.
    assert event_log_row_prints("Event", "Attitude", component="Attitude block - 005")
    # A name that itself contains a space can therefore never match (source-derived).
    assert not event_log_row_prints("Event", "Fuel Pump", component="Fuel Pump")
    assert event_log_row_prints("Event", "Fuel", component="Fuel Pump")  # it matches "Fuel"

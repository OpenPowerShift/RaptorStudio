"""`/d` diagnostics trace: the lines are the source's own and match a real capture."""
from pathlib import Path

from raptor.core import diagnostics as d

GOLD = Path(__file__).resolve().parents[1] / "golden"


def test_file_name_and_lines():
    today = "Sat Oct 10 12:05:11 2026"
    assert d.diagnostics_file_name(today) == "10Oct1205.TXT"
    assert d.diagnostics_file_name("Sat Oct  3 08:07:00 2026") == "3Oct0807.TXT"
    lines = d.startup_lines(today, install_path="C:\\R\\", file_to_open="m.rbd")
    assert lines[0] == "System today String =  Sat Oct 10 12:05:11 2026"
    assert lines[1] == "Raptor day month year = 10 Oct 2026"
    assert lines[2].startswith("devVersion ") and lines[4] == "fileToOpen = m.rbd"
    assert d.opened_lines("C:\\a.rbd") == ["", "Opened file C:\\a.rbd"]


def test_api_run_writes_the_trace_when_on(tmp_path):
    from raptor.api.run import run, section_path
    from raptor.api.workspace import Workspace
    from raptor.core.dev_options import DeveloperOptions
    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "MotorDemo" / "input.rbd")["model_id"]
    off = run(ws, mid, num_runs=1, timeout_s=60, confirm_long_run=True,
              developer=DeveloperOptions())
    assert not section_path(ws.root / "runs" / off.run_id, "diagnostics_log").exists()
    on = run(ws, mid, num_runs=1, timeout_s=60, confirm_long_run=True,
             developer=DeveloperOptions(diagnostics_log=True))
    text = section_path(ws.root / "runs" / on.run_id, "diagnostics_log").read_text()
    assert "StartSimulation  " in text and "EndSimulation  " in text


def test_every_developer_option_is_documented_once():
    import re
    from raptor.core.dev_options import DEV_FEATURES
    page = (Path(__file__).resolve().parents[2] / "new_docs" / "chapters"
            / "appendix-developer-options.adoc").read_text()
    for f in DEV_FEATURES:
        assert len(re.findall(rf"^\|.*`{f.key}`", page, re.M)) == 1, f.key


def test_matches_the_real_capture_line_for_line():
    """`legacy/diagnostics_21Oct0510.TXT` is real Raptor's file for TwoBlockRedundancy: only the
    clock text, the model path and the screen size differ from what the port writes."""
    real = (GOLD / "TwoBlockRedundancy" / "legacy" / "diagnostics_21Oct0510.TXT").read_text().replace("\r", "").splitlines()
    today = "Wed Oct 21 05:10:25 2026"
    ours = [*d.startup_lines(today, install_path=real[3].split(" = ", 1)[1],
                             file_to_open=real[4].split(" = ", 1)[1]),
            *d.opened_lines(real[14].removeprefix("Opened file ")),
            *d.pre_sim_lines("Wed Oct 21 05:11:56 2026", "Wed Oct 21 05:11:56 2026"),
            d.start_simulation_line("Wed Oct 21 05:12:04 2026"), d.end_simulation_line("Wed Oct 21 05:12:13 2026")]
    assert ours == real

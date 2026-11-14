"""The run report without the GUI: API, `raptor explain --report`, MCP
`export_report` (the decision maker's document on every surface)."""
from __future__ import annotations

import asyncio
import importlib.util

import pytest

from raptor.api import build
from raptor.core.fileio.rbds import save_rbds

SPEC = {
    "experiment": {"trials": 20, "termination_type": "TimeSim", "term_condition": 3000},
    "blocks": [{"name": "Pump", "fail_dist": 4, "fail_params": [300.0, 0.0],
                "repair_dist": 7, "repair_params": [10.0, 3.0]},
               {"name": "Valve", "fail_dist": 4, "fail_params": [500.0, 0.0],
                "repair_dist": 7, "repair_params": [5.0, 1.0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "Valve"},
              {"from": "Valve", "to": "End"}],
}
HAVE_PDF = importlib.util.find_spec("weasyprint") is not None


@pytest.fixture
def model_path(tmp_path):
    path = tmp_path / "plant.rbds"
    save_rbds(build.build_from_spec(SPEC), str(path))
    return str(path)


def _run(ws, path):
    from raptor.api import run as run_api
    from raptor.cli.common import open_model
    mid = open_model(ws, path)
    return run_api.run(ws, mid, attribution=True, seed=3, confirm_long_run=True).run_id


def test_html_report_is_one_file_with_the_story_and_no_repeated_ids(tmp_path, model_path):
    import re

    from raptor.api.report import write_report
    from raptor.cli.common import workspace_for
    with workspace_for() as ws:
        run_id = _run(ws, model_path)
        out = write_report(ws, run_id, tmp_path / "r.html")
    html = out.read_text()
    assert "Where the downtime comes from" in html and "Pump" in html
    assert "data:image/svg+xml;base64," in html        # the diagram travels inside
    assert "Summary" not in html                        # tables only on request
    ids = re.findall(r"id='([^']+)'", html)
    assert len(ids) == len(set(ids)), "pattern ids repeat across the charts"


def test_sections_are_checked_and_tables_come_on_request(tmp_path, model_path):
    from raptor.api.report import ReportError, write_report
    from raptor.cli.common import workspace_for
    with workspace_for() as ws:
        run_id = _run(ws, model_path)
        with pytest.raises(ReportError):
            write_report(ws, run_id, tmp_path / "r.docx")
        with pytest.raises(ReportError):
            write_report(ws, run_id, tmp_path / "r.html", sections=("story", "nope"))
        html = write_report(ws, run_id, tmp_path / "t.html",
                            sections=("results",)).read_text()
    assert "Availability" in html and "Where the downtime comes from" not in html


@pytest.mark.skipif(not HAVE_PDF, reason="weasyprint not installed")
def test_cli_explain_writes_a_pdf(tmp_path, model_path, capsys):
    from raptor.cli.main import main
    out = tmp_path / "r.pdf"
    assert main(["explain", model_path, "--seed", "3", "--report", str(out)]) == 0
    assert out.read_bytes()[:5] == b"%PDF-"
    assert "report:" in capsys.readouterr().err


def test_cli_rejects_an_unknown_suffix(tmp_path, model_path, capsys):
    from raptor.cli import exitcodes as X
    from raptor.cli.main import main
    assert main(["explain", model_path, "--report", str(tmp_path / "r.txt")]) == X.USAGE


@pytest.fixture
def server(tmp_path):
    pytest.importorskip("mcp")
    from raptor.mcp import server as srv
    from raptor.mcp import tools_model, tools_run, tools_tables  # noqa: F401
    srv.mcp._raptor_args = type("A", (), {"workspace": str(tmp_path), "read_only": False})()
    srv.mcp._raptor_ws = None
    yield srv
    srv.mcp._raptor_ws = None


def _ok(result):
    text = " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    assert not result.is_error, text
    return result.structured_content


def test_mcp_export_report(server, tmp_path):
    call = lambda tool, **a: asyncio.run(server.mcp.call_tool(tool, a))  # noqa: E731
    mid = _ok(call("build_model", name="plant", spec=SPEC, validate_model=False))["model_id"]
    run_id = _ok(call("simulate", model_id=mid, seed=3, attribution=True))["run_id"]
    data = _ok(call("export_report", run_id=run_id, path="Reports/plant.html"))
    assert data["sections"] == ["diagram", "story"] and data["bytes"] > 1000
    assert (tmp_path / "Reports" / "plant.html").is_file()
    refused = call("export_report", run_id=run_id, path="runs/x.html")
    assert refused.is_error

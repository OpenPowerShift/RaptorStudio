"""Tests for the MCP run and analysis tools.

Tools return a ``CallToolResult``: structured JSON plus a Markdown body.
"""
from __future__ import annotations

from pathlib import Path

import asyncio
import inspect

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _resolve(result):
    """Long-running tools are async (progress/cancel); drive them here."""
    return asyncio.run(result) if inspect.iscoroutine(result) else result


def _sc(result):
    result = _resolve(result)
    assert not result.is_error, _text(result)
    return result.structured_content


def _text(result) -> str:
    result = _resolve(result)
    return " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")


@pytest.fixture
def ws_with_motor(tmp_path):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    from raptor.mcp.tools_model import open_model
    result = open_model("MotorDemo")
    assert not result.is_error, _text(result)
    return result.structured_content["model_id"]


class TestValidateModel:
    def test_motor_demo_valid(self, ws_with_motor):
        from raptor.mcp.tools_run import validate_model
        result = _sc(validate_model(ws_with_motor))
        assert "errors" in result
        assert "warnings" in result
        assert "runnable" in result
        assert result["runnable"] is True


class TestEstimateRuntime:
    def test_estimate_returns_data(self, ws_with_motor):
        from raptor.mcp.tools_run import estimate_runtime
        result = _sc(estimate_runtime(ws_with_motor, num_runs=5))
        assert result["seconds_estimate"] > 0
        assert "per_run_ms" in result
        assert "advice" in result


class TestSimulate:
    def test_simulate_returns_summary(self, ws_with_motor):
        from raptor.mcp.tools_run import simulate
        result = _resolve(simulate(ws_with_motor, num_runs=2,
                          timeout_s=60, confirm_long_run=True))
        data = _sc(result)
        assert data["run_id"]
        assert "summary" in data
        assert data["runs"] == 2
        assert "| Metric |" in _text(result)

    def test_simulate_availability_in_markdown(self, ws_with_motor):
        from raptor.mcp.tools_run import simulate
        result = _resolve(simulate(ws_with_motor, num_runs=2,
                          timeout_s=60, confirm_long_run=True))
        assert "Availability" in _text(result)

    def test_simulate_enforces_max_runs(self, ws_with_motor):
        from raptor.mcp.server import mcp
        from raptor.mcp.tools_run import simulate
        mcp._raptor_args = type(
            "A", (), {"workspace": str(Path(mcp._raptor_ws.root)), "max_runs": 1}
        )()
        result = _resolve(simulate(ws_with_motor, num_runs=5, confirm_long_run=True))
        assert result.is_error
        assert "max" in _text(result).lower()


class TestGetReport:
    def test_get_report_limited(self, ws_with_motor):
        from raptor.mcp.tools_run import simulate, get_report
        sim = _sc(simulate(ws_with_motor, num_runs=2,
                           timeout_s=60, confirm_long_run=True))
        report = _sc(get_report(sim["run_id"], section="final_results",
                                offset=0, limit=5))
        assert len(report["lines"]) <= 5
        assert "total" in report
        assert "next_offset" in report

    def test_get_report_second_page(self, ws_with_motor):
        from raptor.mcp.tools_run import simulate, get_report
        sim = _sc(simulate(ws_with_motor, num_runs=2,
                           timeout_s=60, confirm_long_run=True))
        page1 = _sc(get_report(sim["run_id"], section="final_results",
                               offset=0, limit=5))
        if page1["next_offset"] is not None:
            page2 = _sc(get_report(sim["run_id"], section="final_results",
                                   offset=page1["next_offset"], limit=5))
            assert page2["lines"]

    def test_unknown_section_is_error(self, ws_with_motor):
        from raptor.mcp.tools_run import simulate, get_report
        sim = _sc(simulate(ws_with_motor, num_runs=2,
                           timeout_s=60, confirm_long_run=True))
        result = get_report(sim["run_id"], section="nonsense")
        assert result.is_error


class TestSinglePointFailures:
    def test_spf_motor_demo(self, ws_with_motor):
        from raptor.mcp.tools_run import single_point_failures
        result = _sc(single_point_failures(ws_with_motor))
        assert "spofs" in result
        assert "count" in result
        assert isinstance(result["spofs"], list)


class TestMarkdownRoundTrip:
    def test_markdown_table_parseable(self, ws_with_motor):
        from raptor.mcp.tools_run import simulate
        result = _resolve(simulate(ws_with_motor, num_runs=2,
                          timeout_s=60, confirm_long_run=True))
        md = _text(result)
        lines = [l.strip() for l in md.splitlines() if l.strip().startswith("|")]
        assert len(lines) >= 3
        assert "Metric" in lines[0]

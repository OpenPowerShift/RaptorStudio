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
        # MTBDE is a mean of per-run figures; the pooled rate comes with it
        assert data["outages"]["runs"] == 2
        assert set(data["outages"]) == {"outages", "runs", "up_time", "between", "few"}
        assert ("Time between outages, pooled" in _text(result)
                or "No system outage" in _text(result))

    def test_simulate_availability_in_markdown(self, ws_with_motor):
        from raptor.mcp.tools_run import simulate
        result = _resolve(simulate(ws_with_motor, num_runs=2,
                          timeout_s=60, confirm_long_run=True))
        assert "Availability" in _text(result)

    def test_simulate_precision_reports_its_interval(self, ws_with_motor):
        from raptor.mcp.tools_run import simulate
        result = _resolve(simulate(
            ws_with_motor, num_runs=5, timeout_s=60, confirm_long_run=True,
            precision={"relative": 0.5, "min_trials": 2, "max_trials": 4}))
        data = _sc(result)
        assert data["precision"]["trials"] <= 4
        assert data["precision"]["target"] == pytest.approx(0.5 / 1.5)
        assert data["truncated"] is False

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


class TestSensitivity:
    def test_it_returns_a_ranked_paired_tornado(self, ws_with_motor):
        from raptor.mcp.tools_run import sensitivity
        data = _sc(_resolve(sensitivity(
            ws_with_motor, top=2, trials=2, seed=1, timeout_s=60)))
        assert data["inputs"]
        assert data["batches"] == 1 + 2 * len(data["inputs"])
        assert data["metric"] == "availability"
        assert "swing" in data["inputs"][0]


class TestWhatIf:
    def test_no_change_is_refused(self, ws_with_motor):
        """An empty list ran Base against itself and "succeeded"."""
        from raptor.mcp.tools_run import what_if
        result = _resolve(what_if(ws_with_motor, changes=[], name="Same", trials=3,
                                  seed=2, timeout_s=60))
        assert result.is_error and "changes is empty" in _text(result)

    def test_a_bad_change_is_an_error_result_not_a_crash(self, ws_with_motor):
        """Reported as an is_error result saying why, never raised, and
        never a success: a pipeline checking is_error took one for a
        finished comparison."""
        from raptor.mcp.tools_run import what_if
        result = _resolve(what_if(
            ws_with_motor, changes=["no_such:path=1"], trials=2, seed=1,
            timeout_s=60))
        assert result.is_error
        assert "did not run" in _text(result)


class TestRecommendChanges:
    def test_each_row_states_change_gain_cost_and_verdict(self, ws_with_motor):
        from raptor.mcp.tools_run import recommend_changes
        result = _resolve(recommend_changes(
            ws_with_motor, top=1, trials=6, seed=2, timeout_s=120))
        assert not result.is_error
        data = _sc(result)
        assert data["schema"] == "raptor-recommend/1"
        assert data["attribution_run_id"]
        for row in data["rows"]:
            assert row["changes"] and all("=" in c for c in row["changes"])
            assert {"availability", "sem", "p_better"} <= set(row["gain"])
            assert "unit" in row["cost"] and row["verdict"]
        assert data["caption"] in _text(result)

    def test_top_out_of_range_is_refused(self, ws_with_motor):
        from raptor.mcp.tools_run import recommend_changes
        result = _resolve(recommend_changes(ws_with_motor, top=0, trials=2))
        assert result.is_error

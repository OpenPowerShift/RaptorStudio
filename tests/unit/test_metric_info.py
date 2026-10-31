"""Tests for metric definitions and the functional Advanced-results view."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _use_workspace(tmp_path: Path) -> None:
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None


def test_metric_definitions_cover_the_reported_fields():
    from raptor.api.metric_definitions import definitions

    rows = definitions()
    names = {row["metric"] for row in rows}
    assert {"Availability", "Reliability", "MTBDE", "MDT", "MTBM",
            "MMT", "System failures"} <= names
    # The definitions and equations live on the formulas page ONLY. This
    # module used to carry a second copy and the two had drifted, so a
    # reappearing `definition`/`formula` key here is a regression, not a
    # feature.
    # `analysis` marks the capacity-analysis metrics, shown only for a run
    # that has them.
    for row in rows:
        assert set(row) - {"analysis"} == {"key", "metric", "aggregate", "units"}, row
    assert {row["aggregate"] for row in rows if row.get("analysis") == "capacity"} == {
        "Units_Produced", "Mean_Flow", "Mean_Capacity",
        "Capacity_Weighted_Availability", "Lost_Flow"}


def test_mcp_describe_metrics_without_a_run():
    from raptor.mcp.tools_metrics import describe_metrics

    result = describe_metrics()
    assert not result.is_error, result.content
    data = result.structured_content
    assert data["metrics"]
    text = result.content[0].text
    assert "Availability" in text
    assert "read_doc('formulas')" in text
    # Not a second copy of the equations.
    assert "uptime /" not in text.lower()


def test_mcp_describe_metrics_includes_run_statistics(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import simulate
    from raptor.mcp.tools_metrics import describe_metrics

    mid = open_model("MotorDemo").structured_content["model_id"]
    run = asyncio.run(simulate(mid, num_runs=2, confirm_long_run=True))
    assert not run.is_error, run.content
    result = describe_metrics(
        run_id=run.structured_content["run_id"], model_id=mid)
    assert not result.is_error, result.content
    data = result.structured_content
    assert data["run_id"] == run.structured_content["run_id"]
    assert any(row["metric"] == "Availability" and row["mean"] is not None
               for row in data["metrics"])
    assert "Values for this run" in result.content[0].text


def test_mcp_describe_metrics_rejects_a_run_from_another_model(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import create_model, open_model
    from raptor.mcp.tools_run import simulate
    from raptor.mcp.tools_metrics import describe_metrics

    mid = open_model("MotorDemo").structured_content["model_id"]
    run = asyncio.run(simulate(mid, num_runs=1, confirm_long_run=True))
    other = create_model("other").structured_content["model_id"]
    result = describe_metrics(
        run_id=run.structured_content["run_id"], model_id=other)
    assert result.is_error
    assert result.structured_content["error"]["code"] == "invalid_argument"


def test_read_doc_has_the_standards_reference():
    from raptor.mcp.docs import read_doc

    doc = read_doc("iec61078")
    assert doc["name"] == "iec61078"
    assert "Clause coverage" in doc["text"]


def test_gui_importance_tab_ranks_blocks(qapp):
    """The Advanced tab is gone; what replaces it is the importance view.

    It must appear only with importance data, rank by Birnbaum, and leave an
    undefined measure as an em dash rather than 0 or N/A.
    """
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.results import ResultsDock
    from PySide6.QtWidgets import QTableWidget

    rows: list = []
    logs, eor, fin = run_simulation_with_reports(
        load_rbd(str(GOLDEN / "TwoBlockRedundancy" / "input.rbd")),
        num_runs=1, date="fixed", report_sink=rows)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=rows, units="hours")
    assert not [i for i in range(dock._details_tabs.count())
                if dock._details_tabs.tabText(i) == "Advanced"]

    data = {
        "mode": "both", "runs": 10, "batches": 3, "blocks": 2,
        "metrics": {"Availability": {
            "baseline": 0.9,
            "rows": [
                {"block": "A", "birnbaum": 0.02, "raw": 1.5, "rrw": 1.1,
                 "vesely_fussell": 0.2, "perfect": 0.92, "failed": 0.90,
                 "sem": 0.001},
                {"block": "B", "birnbaum": 0.30, "raw": None, "rrw": 1.4,
                 "vesely_fussell": 0.5, "perfect": 0.95, "failed": 0.65,
                 "sem": None},
            ]}},
        "notes": [],
    }
    dock.set_results(logs, eor, fin, report_data=rows, units="hours",
                     importance=data)
    idx = next((i for i in range(dock._details_tabs.count())
                if dock._details_tabs.tabText(i) == "Importance"), None)
    assert idx is not None
    page = dock._details_tabs.widget(idx)
    table = page.findChildren(QTableWidget)[0]
    assert table.rowCount() == 2
    # Ranked by Vesely-Fussell (the rule that separates blocks in a series
    # model, where Birnbaum saturates), so B leads on 0.50 against A's 0.20.
    assert table.item(0, 0).text() == "B"
    # An undefined measure reads as an em dash, never as 0.
    assert table.item(0, 2).text() == "\u2014"
    dock.deleteLater()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_metric_definitions_cover_all_summary_fields():
    from raptor.api.metric_definitions import definitions

    rows = definitions()
    # 17 reported for every run, plus 5 for a run with capacity analysis.
    assert len([r for r in rows if not r.get("analysis")]) == 17
    assert len(rows) == 22
    names = {row["metric"] for row in rows}
    assert {"Conditional reliability", "Dependability", "MTBMs", "MMTu",
            "Component failures", "Green %", "Yellow %", "Red %"} <= names


def test_mcp_describe_metrics_rejects_path_traversal(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_metrics import describe_metrics

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "summary.json").write_text(
        '{"model_id":"evil","metrics":{"Availability":{"mean":0.5}}}')
    result = describe_metrics(run_id="../../outside")
    assert result.is_error
    assert result.structured_content["error"]["code"] == "invalid_argument"


def test_mcp_describe_metrics_rejects_corrupt_summaries(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_metrics import describe_metrics

    for name, payload in (
            ("r_missing", '{"model_id":"m","num_runs":1}'),
            ("r_null", '{"metrics":null}'),
            ("r_list", "[]"),
            ("r_bad", "{not json")):
        run_dir = tmp_path / "runs" / name
        run_dir.mkdir(parents=True)
        (run_dir / "summary.json").write_text(payload)
        result = describe_metrics(run_id=name)
        assert result.is_error, name
        assert result.structured_content["error"]["code"] == \
            "precondition_failed", name


def test_mcp_describe_metrics_marks_censoring_and_isolation(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import describe_model, open_model
    from raptor.mcp.tools_run import simulate
    from raptor.mcp.tools_metrics import describe_metrics

    mid = open_model("HospitalPower.rbds").structured_content["model_id"]
    run = asyncio.run(simulate(mid, num_runs=1, confirm_long_run=True))
    result = describe_metrics(run_id=run.structured_content["run_id"])
    data = result.structured_content
    mtbde = next(row for row in data["metrics"] if row["metric"] == "MTBDE")
    assert mtbde["censored"] is True
    assert mtbde["stdev"] is None and mtbde["sem"] is None
    assert any("censored" in note.lower() for note in data["notes"])
    assert ">8,760" in result.content[0].text

    selector = describe_model(mid, detail="full").structured_content[
        "hierarchies"][0]["selector"]
    iso = asyncio.run(simulate(mid, num_runs=1, hierarchy=selector,
                               confirm_long_run=True))
    iso_result = describe_metrics(run_id=iso.structured_content["run_id"])
    assert iso_result.structured_content["isolation"]
    assert "Isolated hierarchy" in iso_result.content[0].text


def test_mcp_describe_metrics_omits_unknown_model_and_sanitises_nonfinite(
        tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_metrics import describe_metrics
    import json

    run_dir = tmp_path / "runs" / "r_odd"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "num_runs": 1,
        "units": "h",
        "metrics": {"Availability": {"min": float("nan"),
                                      "mean": float("inf"),
                                      "max": 1.0,
                                      "stdev": float("nan"),
                                      "sem": 0.0}},
    }))
    result = describe_metrics(run_id="r_odd")
    assert not result.is_error, result.content
    data = result.structured_content
    assert "model_id" not in data
    json.dumps(data, allow_nan=False)
    availability = next(row for row in data["metrics"]
                        if row["metric"] == "Availability")
    assert availability["min"] is None
    assert availability["mean"] is None


def test_the_advanced_tab_is_gone_and_importance_is_last_when_present(qapp):
    """The Advanced tab was removed (its definitions duplicated the formulas
    page and its statistics duplicated Summary/Logistics). Nothing may put it
    back, and with importance data the Importance tab takes its place at the
    end of the strip."""
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.results import ResultsDock

    rows: list = []
    logs, eor, fin = run_simulation_with_reports(
        load_rbds(str(REPO / "new_docs" / "models"
                      / "HospitalPower.rbds")),
        num_runs=1, date="fixed", report_sink=rows)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=rows, units="hours")
    tabs = [dock._details_tabs.tabText(i)
            for i in range(dock._details_tabs.count())]
    assert "Advanced" not in tabs
    dock._rebuild_weak_page()
    assert [dock._details_tabs.tabText(i)
            for i in range(dock._details_tabs.count())][-1] == "Weak Link"

    dock.set_results(logs, eor, fin, report_data=rows, units="hours",
                     importance={"mode": "improvement", "runs": 5,
                                 "batches": 3, "blocks": 1,
                                 "metrics": {"Availability": {
                                     "baseline": 0.9, "rows": []}}})
    assert [dock._details_tabs.tabText(i)
            for i in range(dock._details_tabs.count())][-1] == "Importance"
    dock.deleteLater()


def test_mcp_describe_metrics_uses_per_run_sample_counts(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import simulate
    from raptor.mcp.tools_metrics import describe_metrics
    import json

    mid = open_model("MotorDemo").structured_content["model_id"]
    run = asyncio.run(simulate(mid, num_runs=3, confirm_long_run=True))
    rid = run.structured_content["run_id"]
    result = describe_metrics(run_id=rid)
    data = result.structured_content
    per_run = json.loads(
        (tmp_path / "runs" / rid / "per_run.json").read_text())
    for row in data["metrics"]:
        expected = sum(1 for item in per_run
                       if isinstance(item.get(row["key"]), (int, float)))
        assert row["samples"] == expected, row["metric"]


def test_mcp_describe_metrics_missing_run_is_not_found(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_metrics import describe_metrics

    result = describe_metrics(run_id="r_missing")
    assert result.is_error
    assert result.structured_content["error"]["code"] == "not_found"


def test_mcp_describe_metrics_rejects_corrupt_isolation(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_metrics import describe_metrics
    import json

    run_dir = tmp_path / "runs" / "r_iso"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "model_id": "m", "num_runs": 1,
        "metrics": {"Availability": {"mean": 0.5}},
        "isolation": "oops",
    }))
    result = describe_metrics(run_id="r_iso")
    assert result.is_error
    assert result.structured_content["error"]["code"] == "precondition_failed"


def test_run_readers_refuse_path_traversal(tmp_path):
    _use_workspace(tmp_path)
    from raptor.api.run import get_report, get_timeseries

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "final_results.txt").write_text("SECRET\n")
    for call in (lambda: get_report(
                     __import__("raptor.mcp.context", fromlist=["workspace"])
                     .workspace(), "../../outside"),
                 lambda: get_timeseries(
                     __import__("raptor.mcp.context", fromlist=["workspace"])
                     .workspace(), "../../outside")):
        with pytest.raises(Exception) as exc:
            call()
        assert "not found" in str(exc.value).lower()


def test_run_table_bundle_refuses_path_traversal(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp import context
    from raptor.api.run_tables import bundle_from_run

    with pytest.raises(Exception) as exc:
        bundle_from_run(context.workspace(), "../../outside")
    assert "not found" in str(exc.value).lower()


def test_read_doc_accepts_documented_repo_paths():
    from raptor.mcp.docs import read_doc

    assert read_doc("docs/MCP.md")["text"].startswith("<!-- GENERATED")
    assert "RAPTOR from an AI Assistant" in read_doc(
        "new_docs/chapters/ch16-mcp.adoc")["text"]


def test_table_readers_refuse_path_traversal(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_tables import get_table, list_tables

    mid = open_model("MotorDemo").structured_content["model_id"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "isolated_model.rbds").write_text("not json")
    for result in (get_table(mid, "inputs.blocks", run_id="../../outside"),
                   list_tables(mid, run_id="../../outside")):
        assert result.is_error
        assert result.structured_content["error"]["code"] == "not_found"


def test_result_readers_reject_corrupt_summary(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp import context
    from raptor.api.run_tables import bundle_from_run
    from raptor.api.workspace import RunArtefactsError

    run_dir = tmp_path / "runs" / "r_corrupt"
    run_dir.mkdir(parents=True)
    (run_dir / "final_results.txt").write_text("data\n")
    (run_dir / "summary.json").write_text("[]")
    with pytest.raises(RunArtefactsError):
        bundle_from_run(context.workspace(), "r_corrupt")


def test_describe_metrics_rejects_truncated_per_run(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_metrics import describe_metrics
    import json

    run_dir = tmp_path / "runs" / "r_trunc"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "model_id": "m", "num_runs": 3, "run_id": "r_trunc",
        "metrics": {"Availability": {"mean": 0.9}},
    }))
    (run_dir / "per_run.json").write_text("[]")
    result = describe_metrics(run_id="r_trunc")
    assert result.is_error
    assert result.structured_content["error"]["code"] == "precondition_failed"


def test_exponential_power_lists_three_parameters():
    from raptor.mcp.server import list_distributions

    result = list_distributions()
    code20 = next(item for item in result.structured_content["distributions"]
                  if item["code"] == 20)
    assert code20["num_params"] == 3
    assert code20["param_names"] == ["shape", "scale", "location"]

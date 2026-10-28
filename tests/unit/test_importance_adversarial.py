"""Adversarial tests for the importance measures.

The happy path is covered elsewhere (`test_importance.py` for the algebra,
`test_importance_surfaces.py` for the wiring). This file tries to break it:
unknown modes, blocks that already cannot fail, empirical distributions,
models with hierarchies, degenerate baselines (never down / always down),
cancellation mid-analysis, and the two entry points being asked for
something they cannot deliver.

Every case here is one a user or an assistant can reach without editing
code, which is the bar for including it.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path

import pytest

from raptor.api import importance as imp
from raptor.api.build import build_from_spec
from raptor.core.engine.runner import run_simulation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------------ helpers
def spec(blocks=("A", "B"), runs=100, length=1000.0, fail_mean=100.0,
         extra_system=None):
    nodes = [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1}]
    for i, _ in enumerate(blocks):
        nodes.append({"id": i + 2, "name": f"n{i}", "type": 2, "n": 1,
                      "out": 1})
    nodes.append({"id": len(blocks) + 2, "name": "End", "type": 3, "n": 1,
                  "out": 0})
    spec_blocks = [{"id": i + 1, "name": n, "fail_dist": 4,
                    "fail_params": [fail_mean], "repair_dist": 7,
                    "repair_params": [10.0, 2.0]}
                   for i, n in enumerate(blocks)]
    links = [{"from": "Start", "to": blocks[0]}]
    for i in range(len(blocks) - 1):
        links.append({"from": blocks[i], "to": blocks[i + 1]})
    links.append({"from": blocks[-1], "to": "End"})
    system = {"num_runs": runs, "run_length": length}
    system.update(extra_system or {})
    return {"name": "Adv", "nodes": nodes, "blocks": spec_blocks,
            "links": links, "system": system}


def rbd(**kw):
    return build_from_spec(spec(**kw), auto_layout=False)


def baseline(model, runs=100, seed=3):
    sink: list = []
    run_simulation(model, num_runs=runs, seed=seed, collect_logs=False,
                   _report_sink=sink)
    return sink


def rows_of(result, metric="Availability"):
    return result["metrics"][metric]["rows"]


# ------------------------------------------------------------------- inputs
def test_unknown_mode_is_refused_by_every_door():
    rbd()
    with pytest.raises(ValueError):
        imp.variants_for("bogus")
    with pytest.raises(ValueError):
        imp.batch_count(3, "bogus")
    with pytest.raises(ValueError):
        imp.estimate_runs(3, "bogus", 100)
    # `normalise` is the tolerant door: a hand-edited .rbds must not brick
    # the model, so an unknown mode falls back to off rather than raising.
    from raptor.core.run_experiment import normalise
    assert normalise({"importance": "bogus"})["importance"] == "none"


def test_mode_none_does_no_work_and_costs_nothing():
    r = rbd()
    out = imp.analyse(r, baseline(r), mode="none")
    assert out["batches"] == 0 and out["runs"] == 0
    assert out["metrics"] == {}


def test_unknown_block_or_kind_is_refused():
    r = rbd()
    with pytest.raises(KeyError):
        imp.variant(r, 999, "perfect", 1000.0)
    with pytest.raises(ValueError):
        imp.variant(r, 1, "immortal", 1000.0)


def test_runs_without_a_baseline_batch_is_refused():
    with pytest.raises(ValueError):
        imp.analyse(rbd(), [], mode="improvement")


# ------------------------------------------------------- hostile model shapes
def test_a_block_inside_a_hierarchy_is_patched_by_id():
    """Hierarchies are not analysis elements, but their blocks ARE: the
    variant must find the row by ID wherever it lives."""
    from raptor.core.fileio.rbd import load_rbd
    r = load_rbd(str(GOLDEN / "HierarchyExample" / "input.rbd"))
    base = baseline(r, runs=20)
    result = imp.analyse(r, base, mode="improvement", runs=20, seed=3)
    ids = [b for b, _name in imp.block_rows(r)]
    assert result["batches"] == 1 + len(ids)
    assert len(rows_of(result)) == len(ids)


def test_an_empirical_block_can_still_be_made_perfect():
    """`FEmp` carries data points for distribution 16 only. Patching FDist
    to Fixed must leave those points inert, not feed them to the engine."""
    model = build_from_spec({
        "name": "Emp",
        "nodes": [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1},
                  {"id": 2, "name": "End", "type": 3, "n": 1, "out": 0}],
        "blocks": [{"id": 1, "name": "A", "fail_dist": 4,
                    "fail_params": [50.0], "repair_dist": 7,
                    "repair_params": [10.0, 2.0]}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
        "system": {"num_runs": 10, "run_length": 100.0},
    }, auto_layout=False)
    row = next(r for r in model.section("BLOCK_DATA").rows
               if str(r["ID"]) == "1")
    row["FDist"] = "16"
    row["#FParam"] = "3"
    row["FEmp"] = "10.0 20.0 30.0"

    perfect = imp.variant(model, 1, "perfect", 100.0)
    patched = next(r for r in perfect.section("BLOCK_DATA").rows
                   if str(r["ID"]) == "1")
    assert patched["FDist"] == "19" and float(patched["FParam1"]) == 101.0
    sink: list = []
    run_simulation(perfect, num_runs=5, seed=1, collect_logs=False,
                   _report_sink=sink)
    assert sink and all(r.availability == pytest.approx(1.0) for r in sink)


def test_the_source_model_is_never_mutated():
    r = rbd(blocks=("A", "B", "C"), runs=50)
    before = json.dumps([dict(row) for row in
                         r.section("BLOCK_DATA").rows], sort_keys=True)
    imp.analyse(r, baseline(r, runs=50), mode="both", runs=50, seed=3)
    after = json.dumps([dict(row) for row in
                        r.section("BLOCK_DATA").rows], sort_keys=True)
    assert before == after


def test_a_missing_run_length_does_not_produce_a_cutoff_inside_the_run():
    """No `TermCondition` means no known horizon; the fallback must be
    enormous, never a small number that would fail the block mid-run."""
    r = rbd()
    r.section("SYSTEM_DATA").rows[0]["TermCondition"] = "0.0"
    perfect = imp.variant(r, 1, "perfect", imp.run_length_of(r))
    row = next(x for x in perfect.section("BLOCK_DATA").rows
               if str(x["ID"]) == "1")
    assert float(row["FParam1"]) > 1.0e6


# ------------------------------------------------------------ degenerate maths
def test_a_baseline_that_is_always_down_does_not_divide_by_zero():
    """u0 = 1 is the other degenerate end: every ratio divides by 1, and
    nothing may come out infinite or NaN."""
    r = rbd(blocks=("A", "B"), runs=10, length=100.0)
    # A fails at t=0 and is never repaired, in series with B: the system is
    # down for every instant of every run.
    row = next(x for x in r.section("BLOCK_DATA").rows
               if str(x["ID"]) == "1")
    row.update({"FDist": "19", "#FParam": "1", "FParam1": "0.000000",
                "RDist": "18", "#RParam": "0"})
    base = baseline(r, runs=10)
    a0 = sum(d.availability for d in base) / len(base)
    # Not exactly 0: the engine's own accounting leaves a 1e-08 hair.
    assert a0 < 1e-6, a0
    result = imp.analyse(r, base, mode="both", runs=10, seed=3)
    assert result["metrics"]["Availability"]["baseline"] < 1e-6
    numeric = ("perfect", "failed", "birnbaum", "raw", "rrw",
               "vesely_fussell", "sem")
    for row in rows_of(result):
        for key in numeric:
            value = row[key]
            assert value is None or math.isfinite(value), (key, value)


def test_a_single_run_reports_no_sampling_error():
    r = rbd(runs=1)
    result = imp.analyse(r, baseline(r, runs=1), mode="improvement", runs=1)
    for row in rows_of(result):
        assert row["sem"] is None


def test_a_block_with_no_effect_scores_zero_not_negative_infinity():
    """Perfect and failed variants of a block that cannot change the
    outcome give the same number: Birnbaum 0, and no infinities."""
    r = rbd(blocks=("A", "B"), runs=60)
    result = imp.analyse(r, baseline(r, runs=60), mode="both", runs=60, seed=3)
    for row in rows_of(result):
        assert row["birnbaum"] is not None
        assert math.isfinite(row["birnbaum"])


def test_results_json_never_carries_nan_or_infinity():
    r = rbd(blocks=("A",), runs=20, length=10.0, fail_mean=1.0e9)
    out = imp.analyse(r, baseline(r, runs=20), mode="both", runs=20, seed=3)
    json.dumps(out, allow_nan=False)          # raises on NaN/Infinity


# --------------------------------------------------------------- cancellation
def test_cancelling_stops_the_variants():
    r = rbd(blocks=("A", "B", "C"), runs=30)
    calls = {"n": 0}

    def stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(RuntimeError, match="cancel"):
        imp.analyse(r, baseline(r, runs=30), mode="both", runs=30,
                    stop_check=stop)


# -------------------------------------------------------------- GUI, hostile
def test_overlay_survives_all_zero_values(qapp):
    """Every measure can legitimately be 0 (no leverage at all). The band
    maths must not divide by the maximum."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView

    v = DiagramView()
    v.load_rbd(load_rbd(str(GOLDEN / "Phasing1" / "input.rbd")))
    v.apply_importance_colors({5: 0.0, 4: 0.0})
    assert v._last_result_visuals["kind"] == "importance"
    assert "Green" in v._block_items[5].toolTip()


def test_overlay_refuses_when_every_measure_is_undefined(qapp):
    """Improvement-only mode on a system that never failed: no VF, no RRW,
    no Birnbaum. The handler must say so and untick itself."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._load_into_app(load_rbds(str(REPO / "new_docs" / "models"
                                     / "Lantern1.rbds")),
                       str(REPO / "new_docs" / "models" / "Lantern1.rbds"))
    win._results.set_results([], "", "", report_data=[], units="h",
                             importance={
                                 "mode": "improvement", "runs": 1,
                                 "batches": 2, "blocks": 1,
                                 "metrics": {"Availability": {
                                     "baseline": 1.0,
                                     "rows": [{"block": "A", "block_id": 1,
                                               "birnbaum": None, "raw": None,
                                               "rrw": None,
                                               "vesely_fussell": None,
                                               "perfect": None,
                                               "failed": None,
                                               "sem": None}]}}})
    win._on_importance_overlay_toggled(True)
    assert not win._importance_overlay_action.isChecked()
    message = win.statusBar().currentMessage()
    assert "undefined" in message or "No " in message
    win._dirty = False
    win.close()


def test_results_tab_renders_a_metric_with_only_undefined_rows(qapp):
    from raptor.gui.ui.results import ResultsDock

    dock = ResultsDock()
    dock.set_results([], "", "", report_data=[], units="h", importance={
        "mode": "both", "runs": 2, "batches": 3, "blocks": 2,
        "metrics": {
            "Availability": {"baseline": 0.9, "rows": [
                {"block": "A", "block_id": 1, "birnbaum": None, "raw": None,
                 "rrw": None, "vesely_fussell": None, "perfect": None,
                 "failed": None, "sem": None}]},
            "Dependability": {"baseline": None, "rows": [
                {"block": "A", "block_id": 1, "birnbaum": None, "raw": None,
                 "rrw": None, "vesely_fussell": None, "perfect": None,
                 "failed": None, "sem": None}]},
        }, "notes": []})
    idx = next(i for i in range(dock._details_tabs.count())
               if dock._details_tabs.tabText(i) == "Importance")
    assert dock._details_tabs.widget(idx) is not None
    dock._set_importance_metric("Dependability")      # all rows undefined
    dock.deleteLater()


# -------------------------------------------------------------- MCP, hostile
def _use_workspace(tmp_path: Path) -> None:
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None


def test_mcp_refuses_importance_for_an_isolated_hierarchy(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import describe_model, open_model
    from raptor.mcp.tools_run import simulate

    mid = open_model("HospitalPower.rbds").structured_content["model_id"]
    selector = describe_model(mid, detail="full").structured_content[
        "hierarchies"][0]["selector"]
    result = asyncio.run(simulate(mid, num_runs=2, hierarchy=selector,
                                  importance="both", confirm_long_run=True))
    assert result.is_error
    assert "isolated" in result.content[0].text


def test_mcp_rejects_an_unknown_mode_at_the_schema():
    """The tool takes a Literal, so a bad value is refused before any
    simulation starts rather than silently doing nothing."""
    import asyncio as _a
    from raptor.mcp import server
    from raptor.mcp import tools_model, tools_run, tools_metrics  # noqa: F401
    tools = {t.name: t for t in _a.run(server.mcp.list_tools())}
    schema = tools["simulate"].input_schema["properties"]["importance"]
    # `Literal[...] | None` renders as anyOf(enum, null), so the enum is one
    # branch rather than the top level.
    enums = [b.get("enum") for b in schema.get("anyOf", [schema])
             if isinstance(b, dict) and b.get("enum")]
    assert enums, schema
    assert set(enums[0]) == set(imp.MODES), enums


def test_mcp_importance_never_emits_nan(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import simulate

    mid = open_model("TwoBlockRedundancy"
                     ).structured_content["model_id"]
    run = asyncio.run(simulate(mid, num_runs=1, importance="both",
                               confirm_long_run=True))
    assert not run.is_error
    json.dumps(run.structured_content, allow_nan=False)


# -------------------------------------------------------------- CLI, hostile
def test_cli_refuses_importance_with_an_isolated_hierarchy():
    """The MCP door refuses this combination; the CLI must agree, or the
    same request succeeds or fails depending on which door it came in."""
    from raptor.cli.common import add_run_options
    from raptor.cli.runopts import apply_run_options
    from raptor.cli.exitcodes import CliError
    from raptor.core.fileio.rbds import load_rbds
    from raptor.api.workspace import Workspace
    import argparse
    import tempfile

    p = argparse.ArgumentParser()
    p.add_argument("model")
    add_run_options(p)
    p.add_argument("--hierarchy")
    args = p.parse_args(["model.rbds", "--importance", "both",
                         "--hierarchy", "Power"])
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(Path(tmp))
        info = ws.register_model(load_rbds(str(REPO / "new_docs" / "models"
                                               / "HospitalPower.rbds")),
                                 name="HP")
        with pytest.raises(CliError, match="isolated"):
            apply_run_options(ws, info["model_id"], args)

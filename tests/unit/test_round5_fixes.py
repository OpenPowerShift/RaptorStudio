"""Fifth round of new-user testing (charts, exported charts, what-if and the
MCP client): what the charts, the what-if study and the tools say about
empty, tiny, cut-short and no-effect results, and the schemas a strict
client checks."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from raptor.api.insight import charts
from raptor.api.insight.charts import text_lines

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]


def _element(name, dtci, causes=None):
    return {"name": name, "dtci": {"mean": dtci, "sem": 0.01}, "deci": {"mean": 0.1},
            "red_time": {"mean": 1.0}, "causes": causes or {"repair": 1.0},
            "outages": {"mean": 1.0}}


# --- MCP output schemas ---------------------------------------------------------

def test_no_output_schema_property_is_null_only():
    """A key seen only as null was typed "null": the client then refused
    the real value ("dot_target": 0.9)."""
    schemas = json.loads((ROOT / "raptor/mcp/output_schemas.json").read_text())

    def walk(node):
        if isinstance(node, dict):
            assert node.get("type") != "null", node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(schemas)


def test_parameter_descriptions_do_not_stop_at_eg():
    from raptor.mcp.param_docs import from_docstring
    doc = "`targets` gives the target, e.g. `[{...}]` (the first is the headline). Next."
    assert from_docstring(doc, "targets").endswith("(the first is the headline).")


# --- distribution parameters and requirements -----------------------------------

def test_a_scalar_sets_the_leading_parameter_only():
    from raptor.api.build import build_from_spec, series, update_element
    from raptor.api.workspace import Workspace
    ws = Workspace(tempfile.mkdtemp())
    mid = ws.register_model(build_from_spec(series(1)), name="m")["model_id"]
    update_element(ws, mid, "Block1", {"fail_dist": 4, "fail_params": [500.0, 0.0, 0.0]})
    from raptor.api import paths
    paths.apply_assignments(ws, mid, ["block:Block1.fail_params=1000"])
    rows = ws.get(mid).rbd.section("BLOCK_DATA").rows
    assert [float(r["FParam1"]) for r in rows].count(1000.0) == 1
    assert all(float(r["FParam2"]) == 0.0 for r in rows)


@pytest.mark.parametrize("req, match", [
    ({"metric": "system.ao", "op": ">=", "value": 99}, "0.99"),
    ({"metric": "system.ao", "op": "~", "value": 0.9}, "unknown comparison"),
    ({"metric": "system.mdt", "op": "<=", "value": "five"}, "must be a number"),
])
def test_a_bad_target_is_refused(req, match):
    from raptor.api.advisor.context import check_requirements
    with pytest.raises(ValueError, match=match):
        check_requirements([req])
    check_requirements([{"metric": "system.ao", "op": ">=", "value": 0.99}])


# --- sensitivity screening ------------------------------------------------------

def test_the_screen_says_how_many_inputs_it_tried():
    from raptor.api.build import build_from_spec, series
    from raptor.api.scenarios.sensitivity import default_inputs, screening
    spec = series(6)
    for i, block in enumerate(spec["blocks"]):
        block.update(fail_dist=4, fail_params=[1000.0 + i], repair_dist=4,
                     repair_params=[1.0 + 10 * i])
    rbd = build_from_spec(spec)
    inputs = default_inputs(rbd, limit=2)
    note = screening(rbd, inputs)
    assert note.startswith("Screened 2 of the") and "ask for more" in note
    # the blocks down the longest come first, not the first in the file
    assert "Block6" in inputs[0].name


# --- charts ---------------------------------------------------------------------

def test_an_empty_chart_says_why_in_the_middle():
    from raptor.render.charts_svg import render
    spec = charts.ChartSpec("pareto", "Who loses the capacity", "", "", "",
                            series=[], caption="No capacity was lost in these runs.")
    svg = render(spec)
    assert "No capacity was lost" in svg and "lost capacity" not in svg.lower().replace(
        "no capacity was lost", "")


def test_lost_capacity_with_none_lost_is_not_a_missing_analysis():
    attribution = {"runs": 3, "elements": [], "causes": {},
                   "capacity": {"elements": [], "causes": {}, "lost_energy": {"mean": 0.0}}}
    spec = charts.lost_capacity_pareto(attribution)
    assert spec.caption.startswith("No capacity was lost")


def test_chart_reasons_name_the_surface():
    from raptor.api.run_needs import how
    assert "configure_system" in how("capacity", surface="mcp")
    assert "--analysis capacity" in how("capacity", surface="cli")
    assert "configure_system" not in how("capacity", surface="gui")


def test_year_charts_refuse_a_minutes_model_by_its_unit():
    with pytest.raises(ValueError, match="minutes"):
        charts.chart_named("annual-fan", per_run=[{"availability": 1.0,
                                                  "ending_sim_time": 20000.0}],
                           attribution={"runs": 1, "elements": []}, units="minutes")


def test_a_pseudo_element_is_not_named_as_the_cause():
    spec = charts.downtime_pareto({"runs": 2, "elements": [
        {**_element("(no single element)", 0.7)}, _element("Pump", 0.3)]})
    assert spec.caption.startswith("70 % of the downtime was started by no single element")
    assert "Pump causes the largest share" in spec.caption


def test_the_pareto_fold_counts_the_rows_past_the_top():
    attribution = {"runs": 3, "elements": [_element("Big", 0.5)]
                   + [_element(f"E{i}", 0.5 / 20) for i in range(20)]}
    folded = charts.fold_small(charts.downtime_pareto(attribution), 0.05)
    assert folded.series[-1]["name"].startswith("20 others")


def test_few_runs_are_not_nine_in_ten():
    spec = charts.ecdf([{"availability": v} for v in (0.9, 0.95)])
    assert "9 runs in 10" not in spec.caption and "All 2 runs" in spec.caption


def test_one_outage_and_open_outages_are_said_plainly():
    one = {"runs": 5, "units": "hours", "outage_durations": {
        "count": 1, "open_at_end": 0, "p50": 2.139, "p90": 2.139,
        "hist": [{"lo": 2.139, "hi": 2.139, "count": 1.0}]}}
    spec = charts.outage_histogram(one)
    assert "one outage in 5 runs" in spec.caption and spec.marks == []
    assert "2.139 to 2.139" not in "\n".join(text_lines(spec))
    still = {"runs": 2, "units": "hours", "outage_durations": {
        "count": 4, "open_at_end": 4, "p50": 200.0, "p90": 300.0,
        "hist": [{"lo": 100.0, "hi": 300.0, "count": 4.0}]}}
    assert "still going when its run ended" in charts.outage_histogram(still).caption


def test_the_text_form_gives_numbers_for_every_kind():
    bubble = charts.frequency_duration({"runs": 2, "units": "hours",
                                        "elements": [_element("Pump", 0.3)]})
    assert any("Pump" in line for line in text_lines(bubble)[1:])
    dots = charts.annual_dots({"pooled": {"n": 3, "dots": [0.9, 0.95, 0.99]}})
    assert any("median" in line for line in text_lines(dots))


def test_equal_rungs_say_nothing_was_taken():
    spec = charts.availability_ladder({"runs": 2, "inherent": {"value": 0.99},
                                       "achieved": {"value": 0.99},
                                       "operational": {"value": 0.98}})
    assert "Planned maintenance took nothing away" in spec.caption


def test_a_floor_above_the_data_is_ignored_and_said():
    from raptor.api.insight import timecharts
    runs = [{"t": [10.0, 20.0, 30.0], "values": [0.5, 0.6, 0.7]} for _ in range(3)]
    spec = timecharts.availability_band(runs, units="hours", y_floor=0.99)
    assert "above every value" in spec.caption


def test_the_convergence_spread_is_in_points():
    spec = charts.convergence([{"availability": v} for v in (0.9, 0.95, 0.97)])
    assert "points" in spec.caption and "give or take" not in spec.caption


# --- the Story and the advice ---------------------------------------------------

def test_no_target_means_no_met_count():
    from raptor.api.insight.story import build
    story = build([{"availability": 0.99}] * 20)
    assert story.met is None and story.total is None


def test_the_tail_counts_only_runs_with_downtime():
    from raptor.api.advisor.context import RunContext
    from raptor.api.advisor.rules_run.quality import tail_runs
    from raptor.api.build import build_from_spec, series
    per_run = [{"availability": 1.0}] * 2900 + [{"availability": 0.5}] * 78
    found = list(tail_runs(RunContext(build_from_spec(series(1)), {}, per_run)))
    assert found and "from 78 of the 2,978 runs" in found[0].message


# --- what-if --------------------------------------------------------------------

def test_a_change_with_no_effect_says_so_and_cannot_be_applied():
    from raptor.api.build import build_from_spec, series
    from raptor.api.scenarios import compare
    from raptor.api.scenarios.runner import comparison_lines
    from raptor.api.workspace import Workspace
    ws = Workspace(tempfile.mkdtemp())
    spec = series(2)
    for block in spec["blocks"]:
        block.update(fail_dist=4, fail_params=[100.0], repair_dist=4, repair_params=[5.0])
    mid = ws.register_model(build_from_spec(spec), name="m")["model_id"]
    # the value it already has: every trial is the same as Base's
    result = compare(ws, mid, [{"name": "same", "changes": ["block:Block1.repair_params=5"]}],
                     trials=3, seed=7)
    assert result.rows[0]["no_effect"]
    assert "had no effect" in result.caption
    assert not result.variance_reduction
    text = "\n".join(comparison_lines(result))
    assert "1 core" in text and "identical to Base" in text


def test_errors_read_without_class_names():
    from raptor.api.batch import plain_error
    from raptor.api.paths import PathError
    assert plain_error(PathError("path 'x' needs a kind prefix")) == "path 'x' needs a kind prefix"
    assert plain_error(TypeError("bad")) == "TypeError: bad"


def test_a_word_is_not_a_number():
    from raptor.core.expr_eval import ExpressionError, eval_expression
    with pytest.raises(ExpressionError, match="the word 'abc'"):
        eval_expression("abc")


def test_whatif_refuses_zero_trials_and_two_sources():
    from raptor.cli.cmd_scenarios import _scenario
    from raptor.cli.exitcodes import CliError
    from raptor.cli.main import build_parser
    parse = build_parser().parse_args
    with pytest.raises(CliError, match="at least 1"):
        _scenario(parse(["whatif", "m.rbds", "--change", "system.runs=3", "-n", "0"]))
    with pytest.raises(CliError, match="not both"):
        _scenario(parse(["whatif", "m.rbds", "--change", "system.runs=3",
                         "--change-file", "x.json"]))


def test_the_window_numbers_are_never_e_notation():
    from raptor.gui.ui.whatif_dialog import _pts
    assert "e-" not in _pts(-4.27e-07, 1e-08)
    assert _pts(-4.27e-07) == "-0.0000427 pts"

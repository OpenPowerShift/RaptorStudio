"""The optimisers over MCP: each searches, leaves the model alone and hands
back its recommendation as PATH=VALUE changes the editing tools accept."""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from gen_mcp_output_schemas import OPT_SPEC  # noqa: E402


def _run(result):
    result = asyncio.run(result) if inspect.iscoroutine(result) else result
    text = " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    return result, text


@pytest.fixture
def opt_model(tmp_path):
    from raptor.mcp.server import mcp
    from raptor.mcp.tools_model import build_model
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    result = build_model(name="opt", spec=OPT_SPEC)
    assert not result.is_error
    return result.structured_content["model_id"]


def _rbds(model_id):
    from raptor.api.run import model_hash
    from raptor.mcp import context
    return model_hash(context.workspace().get(model_id).rbd)


def test_spares_names_the_stock_and_its_changes(opt_model):
    from raptor.mcp.tools_optimise import optimise_spares
    before = _rbds(opt_model)
    result, text = _run(optimise_spares(opt_model, target=0.5, max_evaluations=3,
                                        trials=3, seed=2))
    assert not result.is_error, text
    data = result.structured_content
    assert data["recommended"] is not None
    assert data["changes"] == data["recommended"]["changes"]
    assert all(c.startswith("pool:Seals.") and "=" in c for c in data["changes"])
    assert data["caption"] in text and "Changes to apply" in text
    assert _rbds(opt_model) == before          # the model is never edited


def test_pm_reports_the_screen_and_only_worthwhile_changes(opt_model):
    from raptor.mcp.tools_optimise import optimise_pm
    result, text = _run(optimise_pm(opt_model, trials=3, seed=2))
    assert not result.is_error, text
    data = result.structured_content
    (rec,) = data["recommendations"]
    assert rec["block"] == "Pump" and "| Pump |" in text
    assert rec["changes"] == ([] if not rec["worthwhile"] else rec["changes"])
    assert data["changes"] == rec["changes"]
    assert all(row["changes"] for row in data["rows"])


def test_resources_recommend_a_size_as_a_change(opt_model):
    from raptor.mcp.tools_optimise import optimise_resources
    result, text = _run(optimise_resources(opt_model, target=0.5, extra=1, trials=3, seed=2))
    assert not result.is_error, text
    data = result.structured_content
    (rec,) = data["recommendations"]
    assert data["changes"] == [f"resource:Crew.units={rec['units']}"]


def test_the_changes_apply_with_the_cli_path_grammar(opt_model):
    from raptor.api import paths
    from raptor.mcp import context
    from raptor.mcp.tools_optimise import optimise_resources
    result, _text = _run(optimise_resources(opt_model, target=0.5, extra=1, trials=3, seed=2))
    ws = context.workspace()
    for change in result.structured_content["changes"]:
        paths.apply_assignment(ws, opt_model, change)


def test_too_many_trials_is_refused():
    from raptor.mcp.server import mcp
    from raptor.mcp.tools_optimise import optimise_pm
    mcp._raptor_args = type("A", (), {"workspace": ".", "max_runs": 5})()
    result, text = _run(optimise_pm("m", trials=10 ** 9))
    assert result.is_error and "exceeds the server cap" in text


# ---- limits: a study is held to the same caps as one run ------------------------

def _refused(call):
    result, text = _run(call)
    assert result.is_error, text
    return text


@pytest.mark.parametrize("kwargs, said", [
    ({"trials": 0}, "trials"),
    ({"trials": 10**6}, "server cap"),
    ({"timeout_s": -1}, "timeout_s"),
    ({"timeout_s": 1e300}, "timeout_s"),
    ({"timeout_s": float("nan")}, "timeout_s"),
    ({"workers": 0}, "workers"),
])
def test_every_study_refuses_bad_trials_and_deadlines(opt_model, kwargs, said):
    from raptor.mcp import tools_optimise, tools_run
    calls = [
        lambda: tools_optimise.optimise_spares(opt_model, **kwargs),
        lambda: tools_optimise.optimise_pm(opt_model, **kwargs),
        lambda: tools_optimise.optimise_resources(opt_model, **kwargs),
        lambda: tools_run.what_if(opt_model, ["block:1.stock=2"], **kwargs),
        lambda: tools_run.sensitivity(opt_model, **kwargs),
        lambda: tools_run.recommend_changes(opt_model, **kwargs),
    ]
    for call in calls:
        assert said in _refused(call())


def test_leaving_trials_out_cannot_pass_the_cap(opt_model):
    from raptor.mcp import context, tools_optimise
    context.workspace().get(opt_model).rbd  # loaded
    from raptor.mcp.server import mcp
    mcp._raptor_args.max_runs = 1
    try:
        assert "NumRuns" in _refused(tools_optimise.optimise_pm(opt_model))
    finally:
        del mcp._raptor_args.max_runs


@pytest.mark.parametrize("tool, kwargs", [
    ("optimise_spares", {"max_evaluations": 10**6}),
    ("optimise_spares", {"max_evaluations": 0}),
    ("optimise_resources", {"extra": 10**6}),
    ("optimise_pm", {"blocks": ["b"] * 51}),
])
def test_study_sizes_are_bounded(opt_model, tool, kwargs):
    from raptor.mcp import tools_optimise
    _refused(getattr(tools_optimise, tool)(opt_model, trials=2, **kwargs))


def test_sensitivity_refuses_an_unknown_metric(opt_model):
    from raptor.mcp import tools_run
    text = _refused(tools_run.sensitivity(opt_model, top=1, trials=2, metric="bogus"))
    assert "bogus" in text and "availability" in text


def test_the_pm_caption_says_which_blocks_the_formula_ruled_out():
    """C14: a block with only "no PM" run read as a simulated verdict."""
    from raptor.api.optimise.pm import _caption
    recs = [{"block": "Pump", "option": "no PM", "worthwhile": False, "mean": 0.9,
             "options_simulated": 0},
            {"block": "Seal", "option": "1.00 x optimum", "worthwhile": False,
             "mean": 0.91, "options_simulated": 5}]
    text = _caption(recs, "availability")
    assert "No simulated interval beat running to failure" in text
    assert "Pump: the formula rules PM out" in text and "Seal:" not in text
    assert _caption(recs[:1], "availability").startswith("No PM interval was simulated.")

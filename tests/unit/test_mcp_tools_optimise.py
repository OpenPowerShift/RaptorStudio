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

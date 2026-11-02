"""Simulation oracle checks for the static solver.

Marked `long`: these run the engine as an independent second opinion. The
default suite skips them; CI's long-check job runs them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.analytical import analyse
from raptor.api.run import run
from raptor.api.workspace import Workspace
from raptor.core.analytical import build, evaluate
from raptor.core.engine.load import load_model
from raptor.core.fileio.model_file import load_model_file

MODELS = Path(__file__).resolve().parents[2] / "new_docs" / "models"


def _metric(result, name: str):
    for metric in result.metrics:
        if metric.name == name:
            return metric
    raise AssertionError(f"metric {name!r} absent")


@pytest.mark.long
def test_lantern_reliability_matches_simulation(tmp_path):
    workspace = Workspace(tmp_path)
    model_id = workspace.open_model(MODELS / "Lantern1.rbds")["model_id"]
    result = run(workspace, model_id, num_runs=2000, confirm_long_run=True)
    simulated = _metric(result, "Reliability")
    assert simulated.mean is not None and simulated.sem is not None

    built = build(load_model(load_model_file(MODELS / "Lantern1.rbds")),
                  mode="reliability")
    analytic = evaluate.system_probability(built, 48.0)
    assert abs(simulated.mean - analytic) <= 4.0 * simulated.sem + 1e-9


@pytest.mark.long
def test_vendo_availability_matches_simulation(tmp_path):
    workspace = Workspace(tmp_path)
    model_id = workspace.open_model(MODELS / "Vendo1.rbds")["model_id"]
    result = run(workspace, model_id, num_runs=300, confirm_long_run=True)
    simulated = _metric(result, "Availability")
    assert simulated.mean is not None and simulated.sem is not None

    analytic = analyse(workspace, model_id, mode="availability")["availability"]
    assert analytic is not None
    assert abs(simulated.mean - analytic) <= 4.0 * simulated.sem + 1e-9

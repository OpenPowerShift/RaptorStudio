"""API and CLI surface for the analytical solver."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api.analytical import SCHEMA, analyse
from raptor.api.workspace import Workspace
from raptor.cli import exitcodes as X
from raptor.cli.main import main
from raptor.core.analytical.errors import NotApplicable

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "new_docs" / "models"


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


def _open(workspace, name):
    return workspace.open_model(MODELS / f"{name}.rbds")["model_id"]


def test_api_result_shape_and_json_safety(workspace):
    model_id = _open(workspace, "Lantern1")
    result = analyse(workspace, model_id, times=[0.0, 10.0, 48.0],
                     equation=True)
    assert result["schema"] == SCHEMA
    assert result["mode"] == "reliability"
    assert result["applicable"] is True
    assert result["reliability"] == pytest.approx(0.38460369, rel=1e-6)
    assert result["mttf"] == pytest.approx(43.1426, rel=1e-4)
    assert result["bx"]["10"] == pytest.approx(9.153885, rel=1e-5)
    assert len(result["times"]) == 3
    assert result["components"]
    assert result["importance"]
    assert result["equation"].startswith("(R_")
    # No NaN or Infinity anywhere.
    json.dumps(result, allow_nan=False)


def test_api_availability_mode(workspace):
    model_id = _open(workspace, "Vendo1")
    result = analyse(workspace, model_id, mode="availability")
    assert result["availability"] == pytest.approx(0.9706853, abs=1e-6)
    assert result["unavailability"] == pytest.approx(0.0293147, abs=1e-6)
    assert {row["name"] for row in result["importance"]} == {
        "Compressor", "Keypad"}


def test_api_refuses_phased_model(workspace):
    model_id = _open(workspace, "AirlinerMission")
    with pytest.raises(NotApplicable, match="phased"):
        analyse(workspace, model_id)


def test_cli_text_output(capsys):
    code = main(["analytic", str(MODELS / "Lantern1.rbds"), "--at", "48"])
    captured = capsys.readouterr()
    assert code == X.OK
    assert "reliability:" in captured.out
    assert "MTTF:" in captured.out
    assert "Importance:" in captured.out
    assert "B10=" in captured.out


def test_cli_json_output(capsys):
    code = main(["analytic", str(MODELS / "Vendo1.rbds"),
                 "--mode", "availability", "-f", "json"])
    captured = capsys.readouterr()
    assert code == X.OK
    payload = json.loads(captured.out)
    assert payload["schema"] == SCHEMA
    assert payload["mode"] == "availability"
    assert payload["availability"] == pytest.approx(0.9706853, abs=1e-6)


def test_cli_refusal_exit_code(capsys):
    code = main(["analytic", str(MODELS / "AirlinerMission.rbds")])
    captured = capsys.readouterr()
    assert code == X.INVALID
    assert "phased" in captured.err


def test_cli_ignore_repairs(capsys):
    code = main(["analytic", str(MODELS / "CarCare0.rbds"),
                 "--ignore-repairs", "-f", "json"])
    captured = capsys.readouterr()
    assert code == X.OK
    payload = json.loads(captured.out)
    assert payload["assumptions"]


def test_api_rejects_non_finite_arguments(workspace):
    model_id = _open(workspace, "Lantern1")
    for kwargs in ({"at": float("nan")}, {"at": float("inf")},
                   {"times": [float("inf")]}, {"tau": float("nan")},
                   {"bx": [float("inf")]}):
        with pytest.raises(ValueError, match="finite"):
            analyse(workspace, model_id, **kwargs)


def test_availability_time_is_not_echoed(workspace):
    model_id = _open(workspace, "Vendo1")
    result = analyse(workspace, model_id, mode="availability")
    assert result["time"] is None


def test_api_caps_curve_and_bx_lists(workspace):
    model_id = _open(workspace, "Lantern1")
    with pytest.raises(ValueError, match="250"):
        analyse(workspace, model_id, times=[1.0] * 10_001)
    with pytest.raises(ValueError, match="20 BX"):
        analyse(workspace, model_id, bx=[10.0] * 101)


def test_api_rejects_out_of_range_bx(workspace):
    model_id = _open(workspace, "Lantern1")
    with pytest.raises(ValueError, match=r"\[0, 100\]"):
        analyse(workspace, model_id, bx=[101.0])

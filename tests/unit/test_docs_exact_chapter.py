"""The exact numbers new_docs/chapters/ch-exact.adoc quotes are what the
solver gives. The chapter's simulation numbers are examples with their SEM
and are not checked here; the quoted exact values are, because a reader is
told to reproduce them to the digit and to check them by hand."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.analytical import analyse_rbd
from raptor.core.fileio.rbds import load_rbds

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "new_docs" / "models"
CHAPTER = (ROOT / "new_docs" / "chapters" / "ch-exact.adoc").read_text()


def _solve(name: str, **options):
    return analyse_rbd(load_rbds(MODELS / f"{name}.rbds"), name, **options)


def test_lantern_summary_cards():
    result = _solve("Lantern1")
    assert f"{result['reliability']:.6f}" == "0.384604"
    assert f"{result['mttf']:.4f}" == "43.1426"
    assert f"{result['bx']['10']:.5f}" == "9.15389"
    for text in ("0.384604", "43.1426 hours", "9.15389 hours"):
        assert text in CHAPTER


def test_lantern_hand_check_and_importance():
    result = _solve("Lantern1")
    by_name = {c["name"]: c for c in result["components"]}
    assert f"{by_name['Battery_Pack']['reliability']:.4f}" == "0.4889"
    assert f"{by_name['Switch']['reliability']:.4f}" == "0.7866"
    assert by_name["LED_Panel"]["reliability"] > 0.9999998
    importance = {row["name"]: row for row in result["importance"]}
    assert f"{importance['Battery_Pack']['birnbaum']:.4f}" == "0.7866"
    assert f"{importance['Switch']['birnbaum']:.4f}" == "0.4889"
    assert f"{importance['Battery_Pack']['criticality']:.2f}" == "0.65"
    assert result["importance"][0]["name"] == "Battery_Pack"
    assert result["importance"][-1]["name"] == "LED_Panel"


def test_vendo_availability_and_its_hand_check():
    result = _solve("Vendo1", mode="availability")
    assert f"{result['availability']:.6f}" == "0.970685"
    by_name = {c["name"]: c for c in result["components"]}
    assert (by_name["Keypad"]["mttf"], by_name["Keypad"]["mttr"]) == (600, 12)
    assert (by_name["Compressor"]["mttf"],
            by_name["Compressor"]["mttr"]) == (400, 4)
    assert "0.970685" in CHAPTER and "600 / 612 = 0.98039" in CHAPTER


def test_water_treatment_is_refused_then_solved_with_ignores():
    from raptor.core.analytical.errors import NotApplicable
    with pytest.raises(NotApplicable, match="Intake_Screen"):
        _solve("WaterTreatment")
    result = _solve("WaterTreatment", mode="availability",
                    ignore=["pm", "spares", "resources"])
    assert f"{result['availability']:.5f}" == "0.99785"
    assert "0.99785" in CHAPTER


def test_espresso_exercise_answer():
    result = _solve("Espresso1")
    assert f"{result['reliability']:.4f}" == "0.3606"
    assert "0.3606" in CHAPTER

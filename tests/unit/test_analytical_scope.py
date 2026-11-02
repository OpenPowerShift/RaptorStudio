"""Ignore sets, phase selection and standby treatment of the static solver."""
from __future__ import annotations

import os

import pytest

from raptor.core.analytical import build, evaluate
from raptor.core.analytical.errors import NotApplicable
from raptor.core.engine.load import load_model
from raptor.core.fileio.rbds import load_rbds

MODELS = os.path.join(os.path.dirname(__file__), "..", "..", "new_docs",
                      "models")
ALL = ["repairs", "pm", "spares", "resources"]


def _model(name):
    return load_model(load_rbds(os.path.join(MODELS, f"{name}.rbds")))


def test_standby_is_refused_until_chosen_and_names_the_choice():
    with pytest.raises(NotApplicable, match="Choose whether"):
        build(_model("Bakery1"), ignore=ALL)


def test_standby_active_is_never_worse_than_absent():
    model = _model("Bakery1")
    active = build(model, ignore=ALL, standby="active")
    absent = build(_model("Bakery1"), ignore=ALL, standby="absent")
    assert (evaluate.system_probability(active, 100.0)
            > evaluate.system_probability(absent, 100.0))
    assert any("absent" in info.state for info in absent.variables)
    assert any("absent" in note for note in absent.assumptions)


def test_ignore_notes_are_recorded():
    built = build(_model("Bakery1"), ignore=["pm", "spares"],
                  standby="active", ignore_repairs=True)
    text = " ".join(built.assumptions)
    assert "preventive maintenance was ignored" in text
    assert "spares were ignored" in text


def test_unknown_ignore_is_a_value_error():
    with pytest.raises(ValueError, match="cannot ignore"):
        build(_model("Lantern1"), ignore=["luck"])


def test_phased_model_needs_a_phase_and_phase_changes_the_answer():
    with pytest.raises(NotApplicable, match="Choose one phase"):
        build(_model("AirlinerMission"), ignore=ALL, standby="active")
    values = []
    for phase in (1, 2, 4):
        built = build(_model("AirlinerMission"), ignore=ALL,
                      standby="active", phase=phase)
        values.append(evaluate.system_probability(built, 10.0))
    assert len(set(values)) == 3
    assert any("phase transitions are ignored" in w for w in built.warnings)


def test_cut_block_forces_the_series_system_down():
    built = build(_model("CarWash1"), ignore=ALL, standby="active", phase=1)
    assert any(info.forced_down for info in built.variables)
    assert evaluate.system_probability(built, 10.0) == 0.0


def test_phase_out_of_range_and_unphased_model():
    with pytest.raises(ValueError, match="between 1 and"):
        build(_model("AirlinerMission"), ignore=ALL, standby="active",
              phase=99)
    with pytest.raises(NotApplicable, match="no phases"):
        build(_model("Lantern1"), phase=1)

"""The prompt before a long run is about time, not run-hours.

It used to fire at 50,000 run-hours, which the shipped examples exceed many
times over while finishing in seconds: opening the first example from the
start page and pressing Run met a warning about "tens of minutes"."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent.parent
MODELS = REPO / "new_docs" / "models"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def prompts(monkeypatch):
    """Record each prompt's text and answer it with its default button."""
    from PySide6.QtWidgets import QMessageBox
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: shown.append(self.text()) or 0)
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: self.defaultButton())
    return shown


def _window(qapp, name):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    win = MainWindow()
    path = str(MODELS / name)
    win._load_into_app(load_rbds(path), path)
    qapp.processEvents()
    return win


@pytest.mark.parametrize("name", ["HospitalPower.rbds", "WaterTreatment.rbds",
                                  "DC1.rbds"])
def test_a_shipped_example_runs_without_a_prompt(qapp, prompts, name):
    win = _window(qapp, name)
    trials = int(win._current_experiment()["trials"])
    assert win._confirm_long_batch(trials) is True
    assert prompts == []


def test_a_very_long_run_asks_without_quoting_a_guess(qapp, prompts):
    win = _window(qapp, "HospitalPower.rbds")
    win._set_exp_field("term_condition", 500_000.0)
    assert win._confirm_long_batch(50_000) is True      # the default is Run
    assert len(prompts) == 1
    text = prompts[0]
    assert "several minutes or longer" in text and "stop it at any time" in text
    assert "about" not in text          # no figure without a measurement


def test_a_guess_of_a_couple_of_minutes_does_not_ask(qapp, prompts):
    """The guess can be several times too high, and a prompt for a run that
    then takes seconds is a nag."""
    from raptor.core.storage_estimate import (cycles_per_time, element_count,
                                              rough_run_seconds)
    win = _window(qapp, "HospitalPower.rbds")
    win._set_exp_field("term_condition", 87_600.0)
    experiment = dict(win._current_experiment(), trials=400)
    guess = rough_run_seconds(experiment,
                              cycle_rate=cycles_per_time(win._rbd)[0],
                              elements=element_count(win._rbd), cores=1)
    assert 60 < guess < 600
    win._run_full_speed = False
    assert win._confirm_long_batch(400) is True
    assert prompts == []


def test_a_measured_run_decides_and_is_quoted(qapp, prompts):
    win = _window(qapp, "HospitalPower.rbds")
    horizon = float(win._current_experiment()["term_condition"])
    from raptor.gui import cores
    # This model's last run: 200 trials took 8 s.
    win._last_run_profile = (horizon, 200, 8.0, cores.chosen(), 1)
    win._last_run_profile_rbd = id(win._rbd)
    assert win._confirm_long_batch(400) is True         # 16 s: no prompt
    assert prompts == []
    assert win._confirm_long_batch(2_000) is True       # 80 s: asks
    assert len(prompts) == 1
    assert "about 80 seconds" in prompts[0] and "last run" in prompts[0]


def test_another_models_time_is_not_used(qapp, prompts):
    win = _window(qapp, "HospitalPower.rbds")
    horizon = float(win._current_experiment()["term_condition"])
    win._last_run_profile = (horizon, 1, 500.0, 1, 1)   # some slow model
    win._last_run_profile_rbd = id(object())
    assert win._confirm_long_batch(200) is True
    assert prompts == []


def test_cancelling_says_so(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    win = _window(qapp, "HospitalPower.rbds")
    win._set_exp_field("term_condition", 500_000.0)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    assert win._confirm_long_batch(50_000) is False
    assert "cancelled" in win._statusbar.currentMessage().lower()

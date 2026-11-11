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


def test_a_run_expected_to_take_minutes_asks_and_defaults_to_run(qapp, prompts):
    win = _window(qapp, "HospitalPower.rbds")
    win._set_exp_field("term_condition", 500_000.0)
    assert win._confirm_long_batch(5_000) is True      # the default is Run
    assert len(prompts) == 1
    assert "expected to take" in prompts[0] and "stop it at any time" in prompts[0]


def test_cancelling_says_so(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    win = _window(qapp, "HospitalPower.rbds")
    win._set_exp_field("term_condition", 500_000.0)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    assert win._confirm_long_batch(5_000) is False
    assert "cancelled" in win._statusbar.currentMessage().lower()

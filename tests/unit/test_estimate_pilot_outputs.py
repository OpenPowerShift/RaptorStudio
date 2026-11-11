"""The runtime estimate's pilot records what the run itself will: the
capacity file always, and the availability plot and interim cost report
when the model's Recording settings tick them. A pilot without an output
the run writes under-read it by about 3x (issue #4, item 10); a pilot with
one the run does not write over-reads it the same way."""
from __future__ import annotations

from types import SimpleNamespace

from raptor.api import estimate as estimate_mod
from raptor.core import run_experiment as rx


def _pilot_kwargs(monkeypatch, **recording):
    calls = []
    monkeypatch.setattr(estimate_mod, "run_simulation_with_reports",
                        lambda rbd, **kwargs: calls.append(kwargs))
    experiment = rx.defaults()
    experiment["recording"].update(recording)
    model = SimpleNamespace(experiment=experiment, sections={})
    estimate_mod._run_like_a_batch(model, num_runs=3, report_sink=[])
    (kwargs,) = calls
    return kwargs


def test_the_pilot_records_nothing_the_run_will_not(monkeypatch):
    kwargs = _pilot_kwargs(monkeypatch)
    assert kwargs["ao_sink"] is None and kwargs["cost_sink"] is None
    assert kwargs["capacity_sink"] == []
    assert kwargs["collect_logs"] is False


def test_the_pilot_records_the_plots_the_model_ticks(monkeypatch):
    kwargs = _pilot_kwargs(monkeypatch, ao_plot=True, cost_plot=True)
    assert kwargs["ao_sink"] == [] and kwargs["cost_sink"] == []
    assert kwargs["capacity_sink"] == []

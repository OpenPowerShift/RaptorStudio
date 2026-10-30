"""The runtime estimate's pilot asks for the outputs a real batch writes; without
them it under-read a run by about 3x (issue #4, item 10)."""
from __future__ import annotations

from raptor.api import estimate as estimate_mod


def test_the_pilot_asks_for_the_outputs_a_batch_writes(monkeypatch):
    calls = []

    def fake(rbd, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(estimate_mod, "run_simulation_with_reports", fake)
    estimate_mod._run_like_a_batch(object(), num_runs=3, report_sink=[])
    (kwargs,) = calls
    for sink in ("ao_sink", "capacity_sink", "cost_sink"):
        assert kwargs[sink] == [], sink
    assert kwargs["collect_logs"] is False

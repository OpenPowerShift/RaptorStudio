"""What a result needs switched on: one wording, and the switch on every surface."""
from __future__ import annotations

import pytest

from raptor.api import run_needs
from raptor.core import run_experiment as rx


def test_the_message_names_the_switch_on_every_surface():
    text = run_needs.message("attribution")
    assert text.startswith("this chart needs downtime attribution: turn it on")
    assert "simulate(attribution=true)" in text
    assert "raptor explain MODEL" in text
    assert "Detailed event log under Run panel ▸ Recording" in text


def test_the_capacity_plot_brings_the_capacity_analysis_it_needs():
    text = run_needs.message("capacity_plot")
    assert "capacity analysis and the capacity plot" in text
    assert "--analysis capacity -r capacity-plot" in text
    # the MCP switch is the same for both, so it is named once
    assert text.count("configure_system(capacity=true)") == 1
    assert run_needs.button_text("capacity_plot") == "Turn on Capacity and Capacity plot and run"


def test_turn_on_switches_each_option_and_leaves_the_rest():
    experiment = rx.normalise({})
    out = run_needs.turn_on(experiment, ["attribution", "capacity_plot", "cost", "importance"])
    assert out["recording"]["event_log"] and out["recording"]["capacity_plot"]
    assert set(out["analyses"]) >= {"capacity", "cost"}
    assert out["importance"] == "both"
    assert experiment["recording"]["capacity_plot"] is False     # a copy
    for key in run_needs.NEEDS:
        assert run_needs.is_on(run_needs.turn_on(experiment, [key]), key)
    assert rx.normalise(out)["analyses"] == [a for a in rx.ANALYSES if a in out["analyses"]]


def test_an_unknown_need_is_refused():
    with pytest.raises(KeyError):
        run_needs.message("telemetry")


def test_the_window_labels_are_the_run_panels_own(qapp_offscreen):
    from PySide6.QtWidgets import QCheckBox
    from raptor.gui.ui.run_panel import RunPanel
    panel = RunPanel()
    labels = {box.text() for box in panel.findChildren(QCheckBox)}
    for need in run_needs.NEEDS.values():
        assert need.label in labels, need.label


@pytest.fixture
def qapp_offscreen():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

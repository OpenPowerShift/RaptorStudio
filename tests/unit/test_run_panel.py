"""The Run panel: one home for every run setting.

The panel is the answer to a specific set of defects 
and the tests are written to pin THOSE down rather than to raise
a coverage number:

* the trial count had three editors with a hand-maintained two-way sync
  that Project Settings never participated in, so they drifted;
* a dependent plot (Capacity, Cost) could be ticked with its analysis
  off, and nothing enforced the dependency -- only two tooltips, in
  different groups, said so;
* a run's settings could be edited while the run was in progress, so
  the panel could show values the run was not using;
* the same `TermCondition` is a sim time under TimeSim and a plain
  count under FailSim/CycleSim, and a field labelled "Run length"
  holding "10" of failures is actively misleading.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core import run_experiment as rx
from raptor.gui.ui.run_panel import RunPanel

pytestmark = pytest.mark.gui


@pytest.fixture
def qapp():
    """A QApplication for the session. The project's other GUI test
    modules each define this locally; there is no conftest fixture, so
    this file declares its own rather than relying on a sibling."""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    p = RunPanel()
    p.set_experiment(rx.defaults())
    return p


def _rec(panel, key):
    return panel._recording_checks[key]


class TestSingleWriter:
    def test_setting_trials_emits_the_new_experiment(self, panel):
        got = []
        panel.experiment_changed.connect(got.append)
        panel._trials.setValue(250)
        panel._trials.editingFinished.emit()
        assert got, "editing trials must emit a change"
        assert got[-1]["trials"] == 250

    def test_set_experiment_does_not_echo_back(self, panel):
        got = []
        panel.experiment_changed.connect(got.append)
        panel.set_experiment(rx.normalise({"trials": 99}))
        assert not got, "loading a model must not look like a user edit"

    def test_changing_termination_emits_and_updates_the_model_value(self, panel):
        got = []
        panel.experiment_changed.connect(got.append)
        panel.set_experiment(rx.normalise({"termination_type": "TimeSim"}))
        panel._term_type.setCurrentIndex(
            panel._term_type.findData("FailSim"))
        assert got, "changing Stop at must write the experiment"
        assert got[-1]["termination_type"] == "FailSim"

    def test_enabling_ao_plot_seeds_an_interval_that_fits(self, panel):
        panel.set_experiment(rx.normalise(
            {"term_condition": 1.0,
             "recording": {"ao_plot": False, "ao_interval": 10.0}}))
        _rec(panel, "ao_plot").setChecked(True)
        exp = panel.experiment()
        assert exp["recording"]["ao_plot"]
        assert exp["recording"]["ao_interval"] < 1.0

    def test_unchanged_value_does_not_emit(self, panel):
        got = []
        panel.experiment_changed.connect(got.append)
        panel.set_experiment(rx.defaults())
        panel._trials.setValue(int(rx.defaults()["trials"]))
        panel._trials.editingFinished.emit()
        assert not got


class TestDependencyEnforcement:
    def test_dependent_plot_refused_without_its_analysis(self, panel):
        panel.set_experiment(rx.normalise({"analyses": []}))
        blocked = []
        panel.dependency_blocked.connect(
            lambda k, label: blocked.append((k, label)))
        _rec(panel, "capacity_plot").setChecked(True)
        assert not _rec(panel, "capacity_plot").isChecked(), \
            "capacity plot must not be tickable without the analysis"
        assert blocked == [("capacity_plot", "Capacity")]

    def test_refusal_says_what_is_missing(self, panel):
        panel.set_experiment(rx.normalise({"analyses": []}))
        assert "Capacity" in panel._recording_notes["capacity_plot"].text()

    def test_dependent_plot_allowed_once_the_analysis_is_on(self, panel):
        panel.set_experiment(rx.normalise({"analyses": ["capacity"]}))
        _rec(panel, "capacity_plot").setChecked(True)
        assert _rec(panel, "capacity_plot").isChecked()

    def test_turning_the_analysis_off_clears_the_plot(self, panel):
        """The other direction: an analysis that is switched off must not
        leave a plot ticked that nothing can fill."""
        panel.set_experiment(
            rx.normalise({"analyses": ["capacity", "cost"],
                          "recording": {"capacity_plot": True,
                                        "cost_plot": True}}))
        panel._analysis_checks["capacity"].setChecked(False)
        assert not _rec(panel, "capacity_plot").isChecked()
        assert _rec(panel, "cost_plot").isChecked()

    def test_disabled_plot_is_actually_disabled(self, panel):
        panel.set_experiment(rx.normalise({"analyses": []}))
        assert not _rec(panel, "capacity_plot").isEnabled()


class TestIntervalsFollowTheirPlot:
    def test_ao_interval_follows_the_ao_plot(self, panel):
        panel.set_experiment(rx.normalise(
            {"recording": {"ao_plot": False, "cost_plot": True}}))
        assert not panel._ao_interval.isEnabled()
        assert panel._cost_interval.isEnabled()

    def test_turning_the_plot_on_enables_the_interval(self, panel):
        panel.set_experiment(rx.normalise(
            {"recording": {"ao_plot": False}}))
        _rec(panel, "ao_plot").setChecked(True)
        assert panel._ao_interval.isEnabled()


class TestTerminationRelabelling:
    @pytest.mark.parametrize("term,label", [
        ("TimeSim", "Run length:"),
        ("FailSim", "System failures:"),
        ("CycleSim", "Phase cycles:"),
    ])
    def test_label_follows_the_termination_type(self, panel, term, label):
        """A field labelled "Run length" holding a count of failures is
        misleading, and the label is the only thing that can fix it."""
        panel.set_experiment(rx.normalise({"termination_type": term}))
        assert panel._term_label.text() == label

    def test_units_are_shown_for_a_time_terminated_run(self, panel):
        panel.set_units("hours", None)
        panel.set_experiment(rx.normalise({"termination_type": "TimeSim"}))
        assert "hours" in panel._unit_note.text()

    def test_start_stats_note_admits_it_is_meaningless_for_counts(
            self, panel):
        panel.set_units("hours", None)
        panel.set_experiment(rx.normalise({"termination_type": "FailSim"}))
        assert "only meaningful" in panel._unit_note.text()


class TestValidation:
    def test_start_stats_after_run_length_is_flagged(self, panel):
        panel.set_experiment(rx.normalise(
            {"termination_type": "TimeSim", "term_condition": 100.0,
             "start_stats": 500.0}))
        assert "earlier than the run length" in panel._start_stats.toolTip()

    def test_valid_pair_is_not_flagged(self, panel):
        panel.set_experiment(rx.normalise(
            {"termination_type": "TimeSim", "term_condition": 100.0,
             "start_stats": 0.0}))
        assert "earlier than the run length" not in \
            panel._start_stats.toolTip()


class TestRunTimeLock:
    def test_disable_locks_the_experiment_controls(self, panel):
        panel.set_enabled(False)
        assert not panel._trials.isEnabled()
        assert not panel._term_type.isEnabled()
        assert not any(c.isEnabled()
                       for c in panel._analysis_checks.values())

    def test_disabled_panel_emits_nothing(self, panel):
        """A locked panel must not be able to change what the panel says
        the run was using."""
        got = []
        panel.experiment_changed.connect(got.append)
        panel.set_enabled(False)
        panel._trials.setValue(500)
        panel._trials.editingFinished.emit()
        assert not got

    def test_debugger_parameters_stay_editable(self, panel):
        """Locking must not break stepping: a session in progress is
        stepped by hand, and the step parameters are for the NEXT press
        within that same session."""
        panel.set_enabled(False)
        assert panel._step_count.isEnabled()
        assert panel._run_to.isEnabled()
        assert panel._breakpoint.isEnabled()

    def test_reenable_restores(self, panel):
        panel.set_enabled(False)
        panel.set_enabled(True)
        assert panel._trials.isEnabled()


class TestReadiness:
    def test_ready_line_names_the_run(self, panel):
        panel.set_experiment(rx.normalise({"trials": 1000}))
        panel.set_readiness(True, "Ready. 1,000 trial(s).")
        assert "1,000" in panel._readiness.text()

    def test_not_ready_line_carries_a_reason(self, panel):
        """The whole point: a disabled Run button with a generic tooltip
        tells the user nothing."""
        panel.set_readiness(False, "Not ready — 2 problems in the model.")
        assert "2 problems" in panel._readiness.text()


class TestAccessibility:
    def test_every_named_control_has_an_accessible_name(self, panel):
        """`setAccessibleName` appeared once in the whole GUI before this
        panel, so every control here is a first -- and the spec makes
        screen-reader descriptions mandatory, not polish
        (the internal UI strategy notes)."""
        widgets = [panel._trials, panel._term_type, panel._term_value,
                   panel._start_stats, panel._profile, panel._step_count,
                   panel._run_to, panel._breakpoint, panel._ao_interval,
                   panel._cost_interval, panel._readiness,
                   *panel._analysis_checks.values(),
                   *panel._recording_checks.values()]
        for w in widgets:
            assert w.accessibleName(), f"{w} has no accessible name"

    def test_dependent_note_is_announced(self, panel):
        panel.set_experiment(rx.normalise({"analyses": []}))
        note = panel._recording_notes["capacity_plot"]
        assert "capacity" in note.accessibleName().lower()


class TestDebuggerParameters:
    def test_step_count_and_run_to_round_trip(self, panel):
        panel.set_debugger_state(step_count=42, run_to=1234.5)
        assert panel.step_count() == 42
        assert panel.run_to_time() == 1234.5

    def test_setting_debugger_state_does_not_emit(self, panel):
        got = []
        panel.experiment_changed.connect(got.append)
        panel.set_debugger_state(step_count=7, run_to=9.0)
        assert not got

    def test_breakpoint_options_keep_a_surviving_selection(self, panel):
        panel.set_breakpoint_options([("Power", "Power"), ("Pump", "Pump")])
        panel._breakpoint.setCurrentIndex(
            panel._breakpoint.findData("Pump"))
        # Re-populating (as a model load does) must not silently reset it.
        panel.set_breakpoint_options([("Power", "Power"), ("Pump", "Pump")])
        assert panel._breakpoint.currentData() == "Pump"

    def test_breakpoint_defaults_to_none(self, panel):
        panel.set_breakpoint_options([("Power", "Power")])
        assert panel._breakpoint.currentData() is None
        assert panel.breakpoint_block() is None


def test_panel_constructs_without_a_qapplication_error(qapp):
    """Cheap guard against the panel depending on construction order."""
    assert QApplication.instance() is qapp
    p = RunPanel()
    assert p.isEnabled()


class TestDisplayUnitRoundTrip:
    """Unit-converted fields must not drift by merely being visited."""

    def _panel(self, qapp, enabled=True):
        p = RunPanel()
        exp = rx.defaults()
        exp["termination_type"] = "TimeSim"
        exp["term_condition"] = 10000.0
        exp["start_stats"] = 1234.0
        p.set_experiment(exp, enabled=enabled)
        p.set_units("hours", "years")
        return p

    def test_focus_out_without_edit_writes_nothing(self, qapp):
        p = self._panel(qapp)
        seen = []
        p.experiment_changed.connect(seen.append)
        p._term_value.editingFinished.emit()
        p._start_stats.editingFinished.emit()
        assert seen == []
        assert p.experiment()["term_condition"] == 10000.0
        assert p.experiment()["start_stats"] == 1234.0

    def test_real_edit_is_converted_back(self, qapp):
        p = self._panel(qapp)
        seen = []
        p.experiment_changed.connect(seen.append)
        p._term_value.setValue(2.0)
        p._term_value.editingFinished.emit()
        assert len(seen) == 1
        assert seen[0]["term_condition"] == pytest.approx(2 * 8760.0, rel=0.01)

    def test_units_change_while_disabled_still_reconverts(self, qapp):
        p = self._panel(qapp, enabled=False)
        shown_years = p._term_value.value()
        p.set_units("hours", None)
        assert p._term_value.value() == pytest.approx(10000.0)
        assert shown_years < 100


class TestSectionColumns:
    """The panel sits in the wide, short bottom dock: sections go side by
    side when there is room, and stack when the dock is narrow."""

    def _cols(self, qapp, width):
        p = RunPanel()
        p.set_experiment(rx.defaults())
        p.resize(width, 300)
        p.show()
        qapp.processEvents()
        cols = p._columns
        p.close()
        return cols

    def test_wide_dock_uses_four_columns(self, qapp):
        assert self._cols(qapp, 1900) == 4

    def test_medium_dock_uses_two_not_three(self, qapp):
        # Three would strand the fourth section alone on a second row.
        assert self._cols(qapp, 1100) == 2

    def test_narrow_dock_stacks(self, qapp):
        assert self._cols(qapp, 400) == 1

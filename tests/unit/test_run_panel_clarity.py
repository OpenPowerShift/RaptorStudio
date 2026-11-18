"""New-user testing of the Run panel (2026-11-17): what a run option gives
is said where it is switched on, and the panel can start the run."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui


@pytest.fixture
def win():
    from raptor.gui.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow()
    w._new_file()
    yield w
    w._dirty = False
    w.close()


@pytest.mark.parametrize(("value", "decimals", "text"), [
    (1000.0, 6, "1000"), (13300.0, 6, "13300"), (0.5, 6, "0.5"),
    (720.0, 3, "720"), (0.0, 6, "0"), (2.125, 3, "2.125"), (-0.0, 3, "0")])
def test_numbers_are_not_padded_with_zeros(value, decimals, text):
    from raptor.gui.widgets.tidy_spin import tidy_number
    assert tidy_number(value, decimals) == text


def test_the_run_length_reads_1000_not_1000_000000(win):
    assert win._run_panel._term_value.text() == "1000"


def test_the_panel_has_the_ribbons_run_button(win):
    button = win._run_panel._run_button
    assert not button.isHidden()
    assert button.defaultAction() is win._run_action


def test_system_red_cost_is_shown_only_with_cost(win):
    """It prices failed time; with Cost off nothing is priced."""
    panel = win._run_panel
    assert panel._system_rows["red"].isHidden()
    panel._analysis_checks["cost"].setChecked(True)
    assert (not panel._system_rows["red"].isHidden())


def test_the_event_log_says_what_else_needs_it(win):
    note = win._run_panel._recording_checks["event_log"].toolTip()
    assert "Downtime drivers" in note and "Advisor" in note


def test_the_story_names_the_event_log_control_by_its_label():
    from raptor.gui.ui.story_page import StoryPage

    class _Story:
        drivers_available = False
    text = StoryPage._drivers_text(_Story())
    assert "Detailed event log" in text and "Run panel" in text


def test_results_know_they_were_made_without_the_event_log(win):
    results = win._results
    results._report_data = [object()]
    results._manifest = {"recording": {"event_log": False}}
    assert results.ran_without_event_log()
    results._manifest = {"recording": {"event_log": True}}
    assert not results.ran_without_event_log()
    results._manifest = None                    # nothing recorded: say nothing
    assert not results.ran_without_event_log()
    results._report_data = []


def test_the_advisor_says_when_it_could_not_check_downtime(win, monkeypatch):
    monkeypatch.setattr(type(win._results), "ran_without_event_log",
                        lambda self: True)
    win._refresh_advisor()
    text = win._advisor_panel.summary.text()
    assert "not checked" in text and "Detailed event log" in text

"""After a re-run the Overview cards say what each number was before, and
whether the difference is more than chance (new-user testing, 2026-11-17)."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.gui.ui import result_change as rc


def _pct(v: float) -> str:
    return f"{v:.3f}"


def test_a_clear_rise_in_availability_is_better():
    before = rc.figure([0.90, 0.91, 0.89, 0.90])
    now = rc.figure([0.95, 0.96, 0.95, 0.94])
    change = rc.compare("Availability", before, now, _pct)
    assert change.verdict == "better"
    assert change.text == "was 0.900 · up 0.050"


def test_a_rise_in_downtime_is_worse():
    change = rc.compare("MDT", rc.figure([2, 2.1, 1.9]), rc.figure([5, 5.1, 4.9]),
                        lambda v: f"{v:g} h")
    assert change.verdict == "worse" and "up" in change.text


def test_a_difference_inside_the_spread_is_not_called_a_change():
    """The random draws alone move the numbers; that is not the edit."""
    change = rc.compare("Availability", rc.figure([0.80, 0.95, 0.85, 0.90]),
                        rc.figure([0.82, 0.96, 0.86, 0.91]), _pct)
    assert change.verdict == "same"
    assert change.text.endswith("no clear change")
    assert "chance" in change.tooltip


def test_one_run_cannot_tell():
    change = rc.compare("Availability", rc.figure([0.9]), rc.figure([0.95]), _pct)
    assert change.verdict == "unknown" and change.text == "was 0.900"


def test_nothing_to_compare():
    assert rc.compare("Availability", None, rc.figure([0.9, 0.8]), _pct) is None
    assert rc.compare("Availability", rc.figure([0.9, 0.8]), rc.figure([]), _pct) is None
    assert rc.compare("MTBM", rc.figure([1, 2]), rc.figure([3, 4]), str) is None


# ---- on the cards ------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _setting_restored():
    from PySide6.QtCore import QSettings
    old = QSettings().value(rc.SETTING)
    yield
    if old is None:
        QSettings().remove(rc.SETTING)
    else:
        QSettings().setValue(rc.SETTING, old)


def _runs(availabilities):
    from raptor.core.engine.reporting import RunReportData
    from raptor.core.engine.stats import RunningStat
    return [RunReportData(
        rcond_assigned=False, stats_start_time=0.0,
        run_number=i + 1, ending_sim_time=100.0, availability=a,
        mtbde=None, mdt=None, reliability=0.0, conditional_reliability=0.0,
        num_system_failures=1, num_component_failures=2, mtbm=None,
        mtbmu=None, mtbms=None, mmt=None, mmtu=None, mmts=None,
        dependability=None, has_phases=False,
        green_percent=100 * a, yellow_percent=0.0, red_percent=100 * (1 - a),
        mission_green_percent=None, mission_yellow_percent=None,
        mission_red_percent=None, sys_fail_stat=RunningStat(),
        red_stat=RunningStat(), comp_fail_stat=RunningStat(),
        comp_repair_stat=RunningStat(), block_spares={},
    ) for i, a in enumerate(availabilities)]


def _show(dock, run_id, availabilities):
    dock.clear()                       # as a real run does first
    dock.set_results([[]], ["eor"], ["fin"], report_data=_runs(availabilities),
                     manifest={"run_id": run_id})


def test_the_card_says_what_it_was_in_the_run_before(qapp):
    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    _show(dock, "a", [0.90, 0.91, 0.89, 0.90])
    assert dock._cards["Availability"].change_text() == ""     # no run before
    _show(dock, "b", [0.95, 0.96, 0.95, 0.94])
    text = dock._cards["Availability"].change_text()
    assert text.startswith("was 0.9") and " up " in text


def test_the_same_run_populated_twice_is_not_compared_with_itself(qapp):
    """Importance variants populate the dock a second time for one run."""
    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    _show(dock, "a", [0.90, 0.91, 0.89, 0.90])
    _show(dock, "b", [0.95, 0.96, 0.95, 0.94])
    _show(dock, "b", [0.95, 0.96, 0.95, 0.94])
    assert dock._cards["Availability"].change_text().startswith("was 0.9000")


def test_another_model_starts_afresh(qapp):
    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    _show(dock, "a", [0.90, 0.91, 0.89, 0.90])
    dock.forget_previous_run()
    _show(dock, "b", [0.95, 0.96, 0.95, 0.94])
    assert dock._cards["Availability"].change_text() == ""


def test_the_preference_turns_it_off_and_on(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    _show(dock, "a", [0.90, 0.91, 0.89, 0.90])
    _show(dock, "b", [0.95, 0.96, 0.95, 0.94])
    settings.set_value(rc.SETTING, False)
    dock.refresh_changes()
    assert dock._cards["Availability"].change_text() == ""
    settings.set_value(rc.SETTING, True)
    dock.refresh_changes()
    assert dock._cards["Availability"].change_text().startswith("was")


def test_clear_comparison_forgets_the_run_before(qapp):
    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    button = dock._clear_change_button
    _show(dock, "a", [0.90, 0.91, 0.89, 0.90])
    assert button.isHidden()                        # nothing to compare
    _show(dock, "b", [0.95, 0.96, 0.95, 0.94])
    assert not button.isHidden()
    button.click()
    assert button.isHidden()
    assert all(card.change_text() == "" for card in dock._cards.values())
    dock.refresh_changes()                          # stays cleared
    assert dock._cards["Availability"].change_text() == ""
    # the run on screen is now what the next one is compared with
    _show(dock, "c", [0.80, 0.81, 0.79, 0.80])
    assert dock._cards["Availability"].change_text().startswith("was 0.95")
    assert not button.isHidden()


def test_a_probability_difference_is_short():
    """The card's nine places made "up 0.051233505"."""
    change = rc.compare("Availability", rc.figure([0.90, 0.91, 0.92]),
                        rc.figure([0.95, 0.96, 0.97]), lambda v: f"{v:.9f}",
                        rc.short_probability)
    assert change.text == "was 0.910000000 · up 0.05"


def test_the_preference_is_found_by_latest_run(qapp):
    """A user looking for "only the latest run" must be able to find it."""
    from raptor.gui.ui import dialogs
    import inspect
    source = inspect.getsource(dialogs.show_preferences)
    assert "Compare with the previous run" in source
    assert "only the latest run" in source

"""The Run panel layout agreed in #286: a primary Run action beside the
readiness line, options named by the results they feed, Model scope and
Cores in a More options section that never hides them, and the stepping
values on the Debug tab."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core import run_experiment as rx

pytestmark = pytest.mark.gui


@pytest.fixture
def more_closed():
    """Each test starts with More options closed, and leaves it so."""
    from raptor.gui.ui import settings
    from raptor.gui.ui.run_panel import RunPanel
    settings.set_value(RunPanel.MORE_OPTIONS_KEY, False)
    yield
    settings.set_value(RunPanel.MORE_OPTIONS_KEY, False)


@pytest.fixture
def panel(more_closed):
    from raptor.gui.ui.run_panel import RunPanel
    QApplication.instance() or QApplication([])
    p = RunPanel()
    p.set_experiment(rx.defaults())
    yield p
    p.close()


@pytest.fixture
def win(more_closed):
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    w._new_file()
    yield w
    w._dirty = False
    w.close()


# -- stepping moved to the Debug tab ----------------------------------------

def test_the_panel_has_no_stepping_section(panel):
    from PySide6.QtWidgets import QGroupBox
    titles = [b.title() for b in panel.findChildren(QGroupBox)]
    assert titles == ["Experiment", "Results to calculate", "Recording"]


@pytest.mark.parametrize("width", [1100, 1366, 1920])
def test_step_settings_are_on_the_debug_tab_in_sight(win, width):
    """On the Debug tab, and not folded into a popup at ordinary widths:
    the breakpoint is state to keep in sight while stepping."""
    win.show()
    win._ribbon.select_page("Debug")
    win.resize(width, 900)
    QApplication.processEvents()
    QApplication.processEvents()
    page = win._ribbon._pages[win._ribbon.tab_bar.currentIndex()]
    for w in (win._step_count_spin, win._run_to_time_spin,
              win._breakpoint_combo):
        assert not win._run_panel.isAncestorOf(w)
        assert page.isAncestorOf(w)
        assert w.isVisible()


def test_the_more_steps_items_follow_their_values(win):
    win._step_count_spin.setValue(25)
    assert win._step_multi_action.text() == "Multi (25 events)…"
    win._step_count_spin.setValue(1)
    assert win._step_multi_action.text() == "Multi (1 event)…"
    win._run_to_time_spin.setValue(250)
    assert "250" in win._run_to_action.text()


def test_multi_uses_the_debug_tab_value(win, monkeypatch):
    seen = {}
    monkeypatch.setattr(win, "_advance_until",
                        lambda **kw: seen.update(kw))
    win._step_count_spin.setValue(7)
    win._on_step_multi()
    assert seen == {"max_steps": 7}


# -- presets (were a Profile combo) --------------------------------------------

def test_presets_is_a_menu_not_a_field(panel):
    """Applied once, so no field naming a preset that goes stale."""
    assert not hasattr(panel, "_profile")
    menu = panel._presets_button.menu()
    texts = [a.text() for a in menu.actions()]
    for spec in rx.PROFILES.values():
        assert f"{spec['label']} — {spec['hint']}" in texts


def test_a_preset_sets_the_run(panel):
    panel.apply_preset("steady_state")
    exp = panel.experiment()
    over = rx.PROFILES["steady_state"]["overrides"]
    assert exp["term_condition"] == over["term_condition"]
    assert exp["start_stats"] == over["start_stats"]


def test_a_preset_never_unticks_an_analysis(panel):
    """Steady state names Weak Link only; it used to drop Cost."""
    panel.set_experiment(rx.normalise({"analyses": ["cost", "capacity"]}))
    panel._preset_actions["steady_state"].trigger()
    assert {"cost", "capacity", "weak"} <= set(panel.experiment()["analyses"])


def test_stop_at_is_one_row_with_its_unit(panel):
    panel.set_units("hours", None)
    panel.set_experiment(rx.normalise({"termination_type": "TimeSim"}))
    assert panel._term_label.text() == "hours"
    panel.set_units("hours", "years")
    assert panel._term_label.text() == "years"


# -- More options: Model scope and Cores --------------------------------------

def test_more_options_start_closed_but_say_scope_and_cores(panel):
    assert not panel.more_options_open()
    text = panel._more_button.text()
    assert "Whole model" in text and "core" in text


def test_opening_more_options_is_remembered(panel):
    from raptor.gui.ui import settings
    panel._more_button.setChecked(True)
    assert panel.more_options_open()
    assert settings.get_bool(panel.MORE_OPTIONS_KEY, False) is True


def test_a_hierarchy_scope_opens_more_options_without_saving_it(panel):
    from raptor.gui.ui import settings
    panel.set_hierarchies([(5, "Pumps")])
    panel._scope.setCurrentIndex(panel._scope.findData(5))
    assert panel.more_options_open()
    assert "Pumps" in panel._more_button.text()
    # The user did not open it: their remembered choice is unchanged.
    assert settings.get_bool(panel.MORE_OPTIONS_KEY, True) is False


def test_animate_is_named_in_the_summary(panel):
    panel.set_cores(8, False, "Animate is on: an animated run uses one core.",
                    "", animate=True)
    assert "1 core (Animate)" in panel._more_button.text()


# -- readiness and the Run button --------------------------------------------

def test_readiness_names_scope_cores_and_size(win):
    from raptor.gui import cores
    win._live_sim_action.setChecked(False)
    win._update_readiness()
    text = win._run_panel._readiness.text()
    assert text.startswith("✔ Ready · ")
    assert "Whole model" in text
    n = cores.chosen()
    assert f"{n} core" in text
    assert "trial(s)" not in text


def test_run_lines_up_with_the_last_section(panel):
    from PySide6.QtGui import QAction
    action = QAction("Run")
    panel.set_run_action(action)
    panel.set_readiness(True, "✔ Ready · 100 trials · 1000 hours.")
    panel.resize(1600, 400)
    panel.show()
    QApplication.processEvents()
    from PySide6.QtCore import QPoint
    last = panel._sections[-1]
    right = last.mapTo(panel, QPoint(last.width(), 0)).x()
    run = panel._run_button
    assert abs(run.mapTo(panel, QPoint(run.width(), 0)).x() - right) <= 2


def test_run_is_last_in_the_tab_order(panel):
    """Built first (it is on the top line), Run would be the first stop."""
    widget = panel._cost_interval.nextInFocusChain()
    while panel._cost_interval.isAncestorOf(widget):
        widget = widget.nextInFocusChain()
    assert widget is panel._run_button


# -- notes say what each option feeds ------------------------------------------

def test_only_the_event_log_warns_when_off(panel):
    """Notes are tooltips; a line in sight only where turning an option off
    loses something on by default (the event log feeds five results)."""
    panel.set_experiment(rx.normalise({"analyses": ["capacity", "cost"],
                                       "recording": {"event_log": False}}))
    note = panel._recording_notes["event_log"]
    assert not note.isHidden()
    assert note.text().startswith("Off:") and "Downtime drivers" in note.text()
    for key in ("ao_plot", "capacity_plot", "cost_plot"):
        assert panel._recording_notes[key].isHidden(), key
    panel.set_experiment(rx.normalise({"recording": {"event_log": True}}))
    assert panel._recording_notes["event_log"].isHidden()


def test_every_option_explains_itself_in_a_tooltip(panel):
    for cb in (*panel._analysis_checks.values(),
               *panel._recording_checks.values(), panel._importance_check):
        assert cb.toolTip()
        assert cb.accessibleDescription() == cb.toolTip()


def test_a_greyed_plot_says_what_it_needs(panel):
    panel.set_experiment(rx.normalise({"analyses": []}))
    box = panel._recording_checks["capacity_plot"]
    assert box.text() == "Capacity plot (needs Capacity)"
    assert not box.isEnabled() and "Needs the Capacity" in box.toolTip()
    panel.set_experiment(rx.normalise({"analyses": ["capacity"]}))
    assert box.text() == "Capacity plot" and box.isEnabled()


def test_every_option_has_a_feeds_entry():
    from raptor.api import run_needs
    keys = {"weak", "cost", "capacity", "importance",
            *(k for k, _l, _h in rx.RECORDING_FIELDS)}
    assert keys <= set(run_needs.FEEDS)


def test_importance_says_its_cost_in_sight_while_ticked(panel):
    panel.set_importance_blocks(12)
    assert panel._importance_details.isHidden()
    panel.set_experiment(rx.normalise({"importance": "both"}))
    assert not panel._importance_details.isHidden()
    assert "batches" in panel._importance_cost.text()


def test_the_settings_are_compact(panel):
    """User feedback: wide bars and a tall panel. Fields stop at a
    readable width, and the default panel is short."""
    panel.resize(1500, 600)
    panel.show()
    QApplication.processEvents()
    for field in (panel._trials, panel._term_value, panel._start_stats,
                  panel._term_type):
        assert field.width() <= panel.FIELD_MAX_WIDTH
    assert panel.content_height() < 260


def test_check_box_rows_have_room(panel):
    """User feedback: the check boxes were too close together."""
    from PySide6.QtWidgets import QGridLayout
    grids = [g for g in panel.findChildren(QGridLayout)
             if g is not panel._grid]
    assert grids and all(g.verticalSpacing() >= 8 for g in grids)


def test_readiness_counts_the_importance_runs(win):
    """Importance multiplies the work; the line says so before Run."""
    win._set_exp_field("importance", "both")
    win._update_readiness()
    text = win._run_panel._readiness.text()
    assert "+ importance (about" in text and "runs)" in text


def test_statistics_from_shows_its_unit(panel):
    panel.set_units("hours", None)
    assert panel._stats_unit.text() == "hours"


def test_presets_looks_like_a_button(panel):
    from PySide6.QtWidgets import QPushButton
    assert isinstance(panel._presets_button, QPushButton)


def _contrast(a: str, b: str) -> float:
    def lum(hex_colour: str) -> float:
        rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255
               for i in (0, 2, 4)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
               for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("dark", [False, True])
def test_run_button_text_is_readable_in_both_themes(dark):
    """WCAG AA for normal text: white on the dark theme's light blue was
    about 2.9:1."""
    from raptor.gui.resources.design_tokens import ThemeManager, active_colour
    from raptor.gui.ui.run_panel import RunPanel
    QApplication.instance() or QApplication([])
    tm = ThemeManager.instance()
    was = tm.is_dark
    tm.set_dark(dark)
    try:
        assert _contrast(active_colour()["accent"],
                         RunPanel.run_button_text_colour()) >= 4.5
    finally:
        tm.set_dark(was)


@pytest.mark.parametrize("dark", [False, True])
def test_ready_text_is_readable_in_both_themes(dark):
    from raptor.gui.resources.design_tokens import ThemeManager, active_colour
    QApplication.instance() or QApplication([])
    tm = ThemeManager.instance()
    was = tm.is_dark
    tm.set_dark(dark)
    try:
        c = active_colour()
        assert _contrast(c["go"], c["surface"]) >= 4.5
    finally:
        tm.set_dark(was)

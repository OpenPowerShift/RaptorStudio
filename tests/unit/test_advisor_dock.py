"""The Advisor panel and its main-window glue (fix as one undo step)."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.api import build
from raptor.api.advisor import Finding, Fix, Target
from raptor.gui.ui import advisor_dock
from raptor.gui.ui.advisor_dock import AdvisorPanel, summary_text


def finding(i=0, severity="warning", basis="exact", fixes=(), targets=None):
    return Finding(rule=f"M-T-{i:03d}", severity=severity, category="data",
                   title=f"Title {i}", message=f"Message {i}.", why="Because.",
                   targets=(Target("block", 1, "A"),) if targets is None else targets,
                   evidence={"stock": 3, "p_out": 0.123456}, basis=basis, fixes=fixes)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    p = AdvisorPanel()
    p.resize(420, 600)
    p.show()
    yield p
    p.close()
    p.deleteLater()


def test_summary_counts_problems_suggestions_notes():
    fs = [finding(severity="error"), finding(severity="warning"),
          finding(severity="advice"), finding(severity="info")]
    assert summary_text(fs) == "2 problems · 1 suggestion · 1 note"
    assert summary_text([]) == "No findings"


def test_empty_states_say_what_the_panel_is_for(panel):
    assert panel.summary.text() == advisor_dock.NO_MODEL_TEXT
    panel.set_findings([])
    assert panel.summary.text() == advisor_dock.EMPTY_TEXT and panel.cards == []
    assert not panel.more_button.isVisible()


def test_shows_top_five_then_all_on_request(panel):
    panel.set_findings([finding(i) for i in range(12)])
    assert len(panel.cards) == advisor_dock.TOP
    assert panel.more_button.isVisible() and panel.more_button.text() == "Show 7 more"
    QTest.mouseClick(panel.more_button, Qt.MouseButton.LeftButton)
    assert len(panel.cards) == 12
    QTest.mouseClick(panel.more_button, Qt.MouseButton.LeftButton)
    assert len(panel.cards) == advisor_dock.TOP


def test_severity_is_a_glyph_with_a_name_not_only_a_colour(panel):
    panel.set_findings([finding(0, "error"), finding(1, "advice"), finding(2, "info")])
    assert [c.glyph.text() for c in panel.cards] == ["▲", "●", "○"]
    assert [c.glyph.accessibleName() for c in panel.cards] == ["Problem", "Suggestion", "Note"]


def test_estimate_tag_only_on_heuristic_findings(panel):
    panel.set_findings([finding(0, basis="heuristic"), finding(1)])
    assert panel.cards[0].tag.isVisible() and panel.cards[0].tag.text() == "estimate"
    assert not panel.cards[1].tag.isVisible()


def test_why_reveals_reasoning_and_evidence(panel):
    panel.set_findings([finding()])
    card = panel.cards[0]
    assert not card.why.isVisible()
    QTest.mouseClick(card.why_button, Qt.MouseButton.LeftButton)
    assert card.why.isVisible()
    assert "Because." in card.why.text() and "p out: 0.1235" in card.why.text()


def test_fix_and_show_emit_the_finding(panel):
    fix = Fix("Set stock to 2", ("block:1.stock=2",))
    panel.set_findings([finding(fixes=(fix,))])
    got = []
    panel.fix_requested.connect(lambda f, fx: got.append(("fix", f.rule, fx.label)))
    panel.locate_requested.connect(lambda f: got.append(("show", f.rule)))
    card = panel.cards[0]
    QTest.mouseClick(card.fix_buttons[0], Qt.MouseButton.LeftButton)
    QTest.mouseClick(card.show_button, Qt.MouseButton.LeftButton)
    assert got == [("fix", "M-T-000", "Set stock to 2"), ("show", "M-T-000")]


def test_show_is_hidden_when_there_is_nothing_on_the_diagram_to_select(panel):
    panel.set_findings([finding(targets=(Target("system", None, "System", "runs"),))])
    assert not panel.cards[0].show_button.isVisible()


# ---- in the main window ---------------------------------------------------------

SPEC = {
    "blocks": [{"name": "Pump_A", "fail_dist": "Exponential", "fail_params": [876, 0, 0],
                "repair_params": [12, 2, 0], "sparing_type": "Custom", "stock": 1,
                "rso": 0, "eso": 0},
               {"name": "Pump_B", "fail_params": [900, 0, 0], "dep_type": "RBDBlock",
                "dep_num": 1, "dep_idle": 100}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "S", "type": 2, "k": 1},
              {"name": "J", "type": 2, "k": 2}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "S"}, {"from": "S", "to": "Pump_A"},
              {"from": "S", "to": "Pump_B"}, {"from": "Pump_A", "to": "J"},
              {"from": "Pump_B", "to": "J"}, {"from": "J", "to": "End"}],
    "experiment": {"trials": 5, "termination_type": "TimeSim", "term_condition": 8760},
}


@pytest.fixture
def window(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(build.build_from_spec(SPEC), None)
    qapp.processEvents()
    w._show_advisor()
    qapp.processEvents()
    yield w
    w._dirty = False
    w.close()
    w.deleteLater()


def rules(window):
    return [f.rule for f in window._advisor_panel.findings()]


def card(window, rule):
    panel = window._advisor_panel
    if panel.more_button.isVisible() and len(panel.cards) < len(panel.findings()):
        panel.more_button.click()
    return next(c for c in panel.cards if c.finding.rule == rule)


def test_dock_lists_the_models_findings_and_titles_the_problem_count(window):
    assert {"M-STR-003", "M-SPR-002", "M-EXP-001"} <= set(rules(window))
    problems = sum(f.severity in ("error", "warning")
                   for f in window._advisor_panel.findings())
    assert window._advisor_dock.windowTitle() == f"Advisor ({problems})"


@pytest.mark.parametrize("rule, attr, kind, oid, fixed", [
    ("M-SPR-002", "init_stock", "block", 1, 10),
    ("M-STR-003", "good_paths", "node", None, 1),
])
def test_a_fix_is_one_undo_step(window, qapp, rule, attr, kind, oid, fixed):
    view = window._diagram_dock.view
    target = card(window, rule).finding.targets[0]
    obj = (view.blocks if kind == "block" else view.nodes)[target.id]
    before = getattr(obj, attr)
    depth = view._stack.index()
    card(window, rule).fix_buttons[0].click()
    qapp.processEvents()
    assert getattr(obj, attr) == fixed and rule not in rules(window)
    assert view._stack.index() == depth + 1
    view._stack.undo()
    qapp.processEvents()
    window._refresh_advisor()
    assert getattr(obj, attr) == before and rule in rules(window)


def test_setting_runs_goes_through_the_run_settings(window, qapp):
    card(window, "M-EXP-001").fix_buttons[0].click()
    qapp.processEvents()
    assert window._rbd.experiment["trials"] == 200
    assert "M-EXP-001" not in rules(window)


def test_show_selects_the_element(window, qapp):
    assert window._advisor_locate(card(window, "M-SPR-002").finding) is True
    system_only = finding(targets=(Target("system", None, "System", "runs"),))
    assert window._advisor_locate(system_only) is False


def test_an_edit_refreshes_the_findings(window, qapp):
    view = window._diagram_dock.view
    from raptor.gui.ui.diagram_commands import ParamsCmd
    view._stack.push(ParamsCmd(view, "initial stock", 1, 1, 50))
    window._validate_model(quiet=True)
    assert "M-SPR-002" not in rules(window)


def test_results_add_what_the_run_says(window, qapp, monkeypatch):
    from raptor.core.engine.runner import run_simulation
    reports = []
    logs = run_simulation(window._rbd, 30, _report_sink=reports)
    results = window._results
    results._event_logs, results._report_data, results._units = logs, reports, "hours"
    results._schedule_drivers()
    monkeypatch.setattr(results, "has_results", lambda: True)
    results.contents_changed.emit()
    qapp.processEvents()
    found = set(rules(window))
    assert "R-DRV-001" in found and "R-DRV-002" in found
    assert "M-SPR-002" in found                       # the model findings are still there
    monkeypatch.setattr(results, "has_results", lambda: False)
    results.contents_changed.emit()
    assert not any(r.startswith("R-") for r in rules(window))


def test_cards_take_the_new_theme_on_refresh(panel, monkeypatch):
    """Cards bake their colours in when built, so a theme switch rebuilds
    them; otherwise the light theme's muted grey stays on a dark panel."""
    from raptor.gui.resources.design_tokens import DARK_COLOUR, LIGHT_COLOUR
    monkeypatch.setattr(advisor_dock, "active_colour", lambda: LIGHT_COLOUR)
    panel.set_findings([finding()])
    assert LIGHT_COLOUR["muted"] in panel.cards[0].why.styleSheet()
    monkeypatch.setattr(advisor_dock, "active_colour", lambda: DARK_COLOUR)
    panel.refresh_theme()
    assert DARK_COLOUR["muted"] in panel.cards[0].why.styleSheet()


def test_the_first_run_with_findings_opens_the_advisor_once(window, qapp, monkeypatch):
    """A new user never opened View > Advisor: the first run that has
    something to say shows it, and only that once."""
    from raptor.core.engine.runner import run_simulation
    from raptor.gui.ui import settings
    from raptor.gui.ui.advisor_mixin import ADVISOR_INTRODUCED
    settings.set_value(ADVISOR_INTRODUCED, False)  # a fresh install
    window._advisor_dock.hide()
    results = window._results
    reports = []
    logs = run_simulation(window._rbd, 30, _report_sink=reports)
    results._event_logs, results._report_data, results._units = logs, reports, "hours"
    results._schedule_drivers()
    monkeypatch.setattr(results, "has_results", lambda: True)
    results.contents_changed.emit()
    qapp.processEvents()
    assert window._advisor_dock.isVisible()
    assert settings.get_bool(ADVISOR_INTRODUCED)
    window._advisor_dock.hide()                 # the user closes it
    results.contents_changed.emit()
    qapp.processEvents()
    assert not window._advisor_dock.isVisible()


# ---- dismissing findings ----------------------------------------------------------

def test_ignoring_a_finding_hides_it_keeps_the_reason_and_undoes(window, qapp, monkeypatch):
    monkeypatch.setattr(type(window), "_ask_ignore_reason", lambda self, what: "on purpose")
    panel = window._advisor_panel
    target = card(window, "M-SPR-002").finding.targets[0]
    depth = window._diagram_dock.view._stack.index()
    card(window, "M-SPR-002").ignore_one.trigger()
    qapp.processEvents()
    assert "M-SPR-002" not in rules(window)
    (entry,) = panel.ignored()
    assert (entry["rule"], entry["target"], entry["reason"]) == (
        "M-SPR-002", f"block:{target.id}", "on purpose")
    assert panel.ignored_button.isVisible() and "1 ignored" in panel.ignored_button.text()
    # U28: listed by name and rule title, with a way to go to the block
    assert entry["subject"] == f"Block {target.name}"
    panel.ignored_button.click()
    qapp.processEvents()
    from PySide6.QtWidgets import QLabel, QToolButton
    row = panel.ignored_rows[0]
    assert target.name in row.findChild(QLabel).text()
    assert f"block:{target.id}" not in row.findChild(QLabel).text()
    located = []
    panel.locate_requested.connect(located.append)
    next(b for b in row.findChildren(QToolButton) if b.text() == "Show").click()
    assert located and located[0].targets[0].id == target.id
    panel.ignored_button.click()
    assert window._diagram_dock.view._stack.index() == depth + 1
    window._diagram_dock.view._stack.undo()
    qapp.processEvents()
    assert "M-SPR-002" in rules(window) and panel.ignored() == []


def test_cancelling_the_reason_ignores_nothing(window, qapp, monkeypatch):
    monkeypatch.setattr(type(window), "_ask_ignore_reason", lambda self, what: None)
    card(window, "M-SPR-002").ignore_one.trigger()
    qapp.processEvents()
    assert "M-SPR-002" in rules(window) and window._advisor_panel.ignored() == []


def test_restore_from_the_ignored_list(window, qapp, monkeypatch):
    monkeypatch.setattr(type(window), "_ask_ignore_reason", lambda self, what: "")
    panel = window._advisor_panel
    card(window, "M-SPR-002").ignore_rule.trigger()
    qapp.processEvents()
    assert panel.ignored()[0]["target"] == ""
    panel.ignored_button.click()
    qapp.processEvents()
    assert len(panel.ignored_rows) == 1
    from PySide6.QtWidgets import QToolButton
    next(b for b in panel.ignored_rows[0].findChildren(QToolButton)
         if b.text() == "Restore").click()
    qapp.processEvents()
    assert "M-SPR-002" in rules(window) and panel.ignored() == []


def test_a_fixed_run_finding_clears_until_the_next_run(window, qapp, monkeypatch):
    from raptor.api import advisor
    from raptor.api.advisor.finding import Finding, Fix, Target
    pump = next(b for b in window._diagram_dock.view.blocks.values() if b.name == "Pump_A")
    found = Finding(rule="R-SPR-101", severity="advice", category="spares",
                    title="Spares ran out", message="m", why="w",
                    targets=(Target("block", pump.id, "Pump_A", "stock"),),
                    fixes=(Fix("Hold 3 spares", (f"block:{pump.id}.stock=3",)),))
    report = [object()]
    results = type("Res", (), {"has_results": lambda self: True, "_report_data": report})()
    monkeypatch.setattr(window, "_results", results, raising=False)
    monkeypatch.setattr(advisor, "advise_run", lambda *a, **k: [found])
    monkeypatch.setattr("raptor.api.run._rd_to_dict", lambda rd: {})
    assert window._run_findings() == [found]
    assert window._advisor_apply_fix(found, found.fixes[0])
    assert window._run_findings() == []
    results._report_data = [object()]          # a new run
    assert window._run_findings() == [found]

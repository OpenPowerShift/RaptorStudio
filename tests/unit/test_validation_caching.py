"""Validation is one whole-model pass per revision, not one per question.

Reported from use: opening a large file appeared to freeze. Profiling a
3,471-block / 5,184-link model showed the same whole-model validation
(each pass rebuilding the engine graph) running four times during the
load and once more for the warnings, and again 600 ms after every run
setting edit. These tests pin the "how many passes" contract.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from PySide6.QtCore import QPointF

if TYPE_CHECKING:
    from raptor.gui.ui.diagram import DiagramView

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
MODEL = os.path.join(REPO, "tests/golden/HierarchyExample/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def _count_passes(win) -> list[int]:
    """Count calls that reach the whole-model validator."""
    calls: list[int] = []
    real = win._validation_issues

    def spy():
        calls.append(1)
        return real()
    win._validation_issues = spy
    return calls


def _window(qapp):
    from raptor.gui.ui.main_window import MainWindow
    return MainWindow()


def test_a_load_makes_one_whole_model_pass(qapp):
    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    calls = _count_passes(win)
    win._load_into_app(load_rbd(MODEL), MODEL)
    assert len(calls) == 1, (
        f"opening a model validated it {len(calls)} times; the badge, the "
        f"readiness line, the command state and the warnings must share "
        f"one pass")
    assert win._model_is_valid()


def test_a_selection_change_makes_no_pass(qapp):
    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    calls = _count_passes(win)
    view = win._diagram_dock.view
    block_id = next(iter(view._block_items))
    view._block_items[block_id].setSelected(True)
    win._refresh_commands()
    assert calls == []


def test_changing_a_run_setting_makes_no_pass(qapp):
    """Trials, termination and the analysis toggles live in SYSTEM_DATA:
    they cannot break a link, so re-walking every block is pure cost."""
    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    calls = _count_passes(win)

    win._set_exp_field("trials", 7)
    assert calls == [], "changing the trial count re-validated the model"
    assert win._model_is_valid()

    win._set_analysis_flag("weakAnalysis", True, "Weak Link")
    assert calls == [], "ticking Weak Link re-validated the model"
    assert win._model_is_valid()
    timer = getattr(win, "_validation_timer", None)
    assert timer is None or not timer.isActive(), (
        "a debounced validation was left queued after a run-setting edit")


def test_ticking_weak_link_in_the_run_panel_makes_no_pass(qapp):
    """The reported path: Simulate > Run panel > Analysis > Weak Link took
    ~2 s because the panel's own signal went through the rule gate and
    then scheduled a whole-model validation."""
    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    calls = _count_passes(win)
    panel = win._run_panel
    panel.set_experiment(win._current_experiment(), enabled=True)

    panel._analysis_checks["weak"].setChecked(True)
    assert calls == [], "ticking Weak Link re-validated the whole model"
    assert not win._validation_timer.isActive()
    assert win._model_is_valid()
    assert win._current_experiment()["analyses"] == ["weak"]


def test_the_checking_message_does_not_outlive_the_check(qapp):
    """"Checking the model…" has no timeout; leaving it up made the next
    (unrelated) slow click look like a validation."""
    import time

    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    win._bump_model_revision()
    win._schedule_validation()
    deadline = time.monotonic() + 5.0
    while win._validation_timer.isActive() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()
    assert win._statusbar.currentMessage() == "Model checked — no issues"


def test_a_load_cleans_up_its_busy_indicator(qapp):
    """The Open indicator is modal; leaving it up (or leaving the loading
    flag set) would block every later interaction."""
    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    assert getattr(win, "_busy_dialog", None) is None
    assert getattr(win, "_loading_model", False) is False


def test_an_invalid_run_setting_keeps_the_full_check_queued(qapp):
    """An invalid value must not be waved through: the cheap check fails,
    so the normal (whole-model) validation is still scheduled."""
    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    calls = _count_passes(win)
    # Cycle-terminated with no phases is an unconditional engine refusal.
    win._set_exp_field("termination_type", "CycleSim")
    assert win._model_is_valid() is False
    assert calls, "an invalid run setting must still re-validate"


# ---------------------------------------------------------------- layout
#
# Geometry is not an input to validation, the engine graph, the Inspector,
# the Explorer, the Data Tables or the results hash. Moving a block used to
# emit `model_changed` for every drag step: a 3,470-block model paid a
# whole-model validation (~2.8 s) on the first step and another on the click
# that followed, because the revision bump had made the cached validity
# stale. `layout_changed` is the light path for exactly these edits.


def _level_with_blocks(win) -> DiagramView:
    view = win._diagram_dock.view
    if not view._block_items:
        view.enter_level(23)
    return view


def test_a_drag_emits_layout_changed_not_model_changed(qapp):
    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    view = _level_with_blocks(win)
    emitted: list[str] = []
    view.model_changed.connect(lambda: emitted.append("model"))
    view.layout_changed.connect(lambda: emitted.append("layout"))
    item = next(iter(view._block_items.values()))

    item.setSelected(True)
    item.setPos(item.pos() + QPointF(5, 5))

    assert "layout" in emitted
    assert "model" not in emitted, (
        "a drag must not take the whole-model refresh path")
    assert win._dirty


def test_a_drag_and_the_click_after_it_make_no_validation_pass(qapp):
    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    view = _level_with_blocks(win)
    calls = _count_passes(win)
    item = next(iter(view._block_items.values()))
    item.setSelected(True)
    item.setPos(item.pos() + QPointF(5, 5))
    qapp.processEvents()
    win._refresh_commands()
    # The next click reads the memoised validity: the drag did not bump the
    # revision that keys it.
    win._command_state()
    assert calls == []


def test_a_move_command_skips_the_rule_gate(qapp):
    """Raptor 7's input rules do not read geometry, and the gate deep-copies
    the model and walks every row twice per push -- ~3.4 s on a large file
    for a drag release. A position-only command must not pay it."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import rule_gate
    from raptor.gui.ui.diagram_commands import MoveCmd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    view = _level_with_blocks(win)
    bid = next(iter(view._block_items))
    item = view._block_items[bid]
    old = item.pos()
    new = old + QPointF(5, 5)

    calls: list[int] = []
    real = rule_gate.check
    rule_gate.check = lambda model: (calls.append(1), real(model))[1]
    try:
        view._stack.push(MoveCmd(view, [("block", bid,
                                         (old.x(), old.y()),
                                         (new.x(), new.y()))]))
    finally:
        rule_gate.check = real

    assert calls == [], "a position-only move paid two whole-model rule passes"
    assert item.pos() == new
    assert win._dirty



def test_an_edit_does_not_validate_before_the_debounced_pass(qapp):
    """The pass after an edit is debounced so typing stays responsive. Two
    callers asked for validity synchronously one event-loop turn after every
    edit (the view refresh and the Run panel's readiness line), which ran
    the whole-model validation ahead of the timer: over a second per edit
    on a 1,000-block model."""
    from raptor.core.fileio.rbd import load_rbd

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    view = _level_with_blocks(win)
    qapp.processEvents()
    calls = _count_passes(win)
    bid = next(iter(view._block_items))
    view.rename_block(bid, "RenamedForTest")
    qapp.processEvents()            # the deferred view refresh and resync
    qapp.processEvents()
    assert calls == [], "an edit validated the model before the debounce"
    assert win._validation_timer.isActive()
    # The debounced pass still runs, and leaves validity known again.
    win._validation_timer.stop()
    win._validate_model(quiet=True)
    assert len(calls) >= 1
    assert win._model_is_valid() in (True, False)
    assert win._validity_cache[0] == win._model_revision


def test_the_rule_gate_checks_the_model_once_per_edit(qapp):
    """The gate compares the model's rule violations before and after a
    push. The model before a push is the model after the last one, so its
    violations are remembered instead of being worked out again."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import rule_gate

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    view = _level_with_blocks(win)
    ids = list(view._block_items)[:2]
    calls: list[int] = []
    real = rule_gate.check
    rule_gate.check = lambda model: (calls.append(1), real(model))[1]
    try:
        view.rename_block(ids[0], "GateFirst")
        first = len(calls)
        view.rename_block(ids[1], "GateSecond")
        second = len(calls) - first
        # An undo moves the stack, so what was remembered no longer
        # describes the model: the next edit checks both sides again.
        view._stack.undo()
        before_third = len(calls)
        view.rename_block(ids[1], "GateThird")
        third = len(calls) - before_third
    finally:
        rule_gate.check = real
    assert first == 2 and second == 1 and third == 2


def test_the_rule_gate_still_refuses_after_a_remembered_check(qapp, monkeypatch):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import msgbox

    win = _window(qapp)
    win._load_into_app(load_rbd(MODEL), MODEL)
    view = _level_with_blocks(win)
    ids = list(view._block_items)[:2]
    shown: list[str] = []
    monkeypatch.setattr(msgbox, "warning",
                        lambda _parent, _title, text, *a, **k: shown.append(text))
    view.rename_block(ids[0], "GateOk")           # remembered baseline
    old = view.blocks[ids[1]].name
    view.rename_block(ids[1], "has a space")      # Raptor 7 refuses blanks
    assert shown, "a rule-breaking edit after a remembered check was accepted"
    assert view.blocks[ids[1]].name == old

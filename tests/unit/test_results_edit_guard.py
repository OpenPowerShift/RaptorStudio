"""Editing while results are on screen.

Editing used to lock on the Simulate and Results tabs -- a mode hidden in
navigation, applied even to a freshly opened model with nothing to
protect. What is worth protecting is a DISPLAYED RESULT SET, and only from
edits that could change a simulated number. So:

* switching tabs never locks anything;
* with results showing, the first edit that changes what the engine would
  simulate asks once whether to discard them ("keep" undoes the edit);
* moving things, changing run settings and selecting never ask, because
  the comparison uses `model_results_hash`, which ignores layout;
* a burst of edits asks once, and the undo that "keep" performs cannot
  re-arm the prompt.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QGraphicsItem, QMessageBox

FIXTURE = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_settings():
    from PySide6.QtCore import QSettings
    QSettings().clear()
    yield
    QSettings().clear()


@pytest.fixture
def answers(monkeypatch):
    """Record every QMessageBox and answer with `answers.choice` (the text
    of the button to press). Never blocks."""
    class Answers:
        choice = "Keep results"
        shown: list[str] = []

    a = Answers()
    a.shown = []
    pressed = {}

    def fake_exec(self):
        a.shown.append(self.windowTitle())
        for b in self.buttons():
            if a.choice in b.text().replace("&&", "&"):
                pressed[id(self)] = b
                return 0
        pressed[id(self)] = self.buttons()[-1]
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec, raising=False)
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: pressed.get(id(self)), raising=False)
    return a


@pytest.fixture
def win(qapp, answers):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), FIXTURE)
    qapp.processEvents()
    yield w
    w._step_session = None
    w._run_in_progress = False
    w._dirty = False
    w.close()


def _pe(qapp, n=3):
    for _ in range(n):
        qapp.processEvents()


def _with_results(w, qapp):
    w._run_simulation()
    _pe(qapp, 10)
    assert w._results.has_results(), "the run must leave results on screen"
    return w._diagram_dock.view


def _first_block(v):
    return next(b for b in v.blocks)


def _edit_failure(v, bid, bump=1.0):
    vals = list(v.blocks[bid].fail_values)
    vals[0] += bump
    v.set_block_fail_params(bid, vals)


class TestTabsLockNothing:
    def test_no_tab_locks_the_canvas(self, win, qapp):
        v = win._diagram_dock.view
        bid = _first_block(v)
        item = v._block_items[bid]
        for i in range(win._ribbon.tab_bar.count()):
            win._ribbon.tab_bar.setCurrentIndex(i)
            _pe(qapp)
            assert item.flags() & QGraphicsItem.ItemIsMovable

    def test_inspector_is_not_read_only_on_simulate(self, win, qapp):
        win._ribbon.tab_bar.setCurrentIndex(win._simulate_page_index)
        _pe(qapp)
        assert not win._inspector._read_only


class TestFirstEditAsks:
    def test_no_prompt_without_results(self, win, qapp, answers):
        v = win._diagram_dock.view
        _edit_failure(v, _first_block(v))
        _pe(qapp)
        assert answers.shown == []

    def test_keep_results_undoes_the_edit(self, win, qapp, answers):
        v = _with_results(win, qapp)
        bid = _first_block(v)
        before = list(v.blocks[bid].fail_values)
        answers.choice = "Keep results"
        _edit_failure(v, bid)
        _pe(qapp)
        assert answers.shown == ["Results on screen"]
        assert win._results.has_results()
        assert list(v.blocks[bid].fail_values) == before, \
            "'keep results' must revert the edit"

    def test_discard_clears_results_and_keeps_the_edit(self, win, qapp,
                                                       answers):
        v = _with_results(win, qapp)
        bid = _first_block(v)
        after = list(v.blocks[bid].fail_values)
        after[0] += 1.0
        answers.choice = "Discard results"
        _edit_failure(v, bid)
        _pe(qapp)
        assert answers.shown == ["Results on screen"]
        assert not win._results.has_results()
        assert list(v.blocks[bid].fail_values) == after

    def test_after_discarding_the_next_edit_does_not_ask(self, win, qapp,
                                                         answers):
        v = _with_results(win, qapp)
        bid = _first_block(v)
        answers.choice = "Discard results"
        _edit_failure(v, bid)
        _pe(qapp)
        answers.shown.clear()
        _edit_failure(v, bid, bump=2.0)
        _pe(qapp)
        assert answers.shown == []

    def test_burst_of_edits_asks_once(self, win, qapp, answers):
        v = _with_results(win, qapp)
        bid = _first_block(v)
        answers.choice = "Discard results"
        for k in range(1, 6):
            _edit_failure(v, bid, bump=float(k))
        _pe(qapp, 6)
        assert len(answers.shown) == 1

    def test_undo_from_keep_does_not_reprompt(self, win, qapp, answers):
        v = _with_results(win, qapp)
        answers.choice = "Keep results"
        _edit_failure(v, _first_block(v))
        _pe(qapp, 8)
        assert len(answers.shown) == 1, \
            "the undo 'keep' performs must not arm the guard again"


class TestThingsThatDoNotAsk:
    def test_moving_a_block_does_not_ask(self, win, qapp, answers):
        v = _with_results(win, qapp)
        bid = _first_block(v)
        v.blocks[bid].x_position += 3.0
        win._mark_dirty()
        _pe(qapp)
        assert answers.shown == []
        assert win._results.has_results()

    def test_a_no_op_change_does_not_ask(self, win, qapp, answers):
        _with_results(win, qapp)
        win._mark_dirty()
        _pe(qapp)
        assert answers.shown == []

    def test_run_setting_change_does_not_ask(self, win, qapp, answers):
        _with_results(win, qapp)
        win._set_exp_field("trials", 3)
        _pe(qapp)
        assert answers.shown == []
        assert win._results.has_results()


class TestNotice:
    def test_inspector_warns_while_results_show(self, win, qapp):
        assert not win._inspector._lock_banner.isVisibleTo(win._inspector)
        _with_results(win, qapp)
        assert win._inspector._lock_banner.isVisibleTo(win._inspector)
        assert "discard" in win._inspector._lock_text.text().lower()
        assert not win._inspector._read_only, \
            "a warning, not a lock: fields stay editable"

    def test_notice_goes_away_when_results_are_cleared(self, win, qapp):
        _with_results(win, qapp)
        win._results.clear()
        _pe(qapp)
        assert not win._inspector._lock_banner.isVisibleTo(win._inspector)

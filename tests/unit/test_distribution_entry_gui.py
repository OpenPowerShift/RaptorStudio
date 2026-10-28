"""Distribution entry through the widgets: DistroEditor, the Phases dialog and
the Inspector's block distribution change.

Every route must leave a valid (code, parameters) pair in the model row AND
the typed object, in one undo step. The pure rules are in
test_distribution_entry_rules.py; the Data Tables / dialogs routes are in
test_distribution_entry_tables.py; the API / .rbds routes in
test_distribution_entry_api.py.
"""
from __future__ import annotations


import pytest
from PySide6.QtWidgets import (
    QApplication, QDialogButtonBox, QLineEdit)

from raptor.core.distro_rules import (
    OFFERED, DISTRO_PARAM_LABELS, validate_distro_params, params_for_mean)

pytestmark = pytest.mark.gui

AIRLINER = "new_docs/models/AirlinerMission.rbds"
DELAY = OFFERED["delay"]


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _type(ed, i, text):
    """Type into parameter field i and commit it, as the user does."""
    e = ed._param_edits[i]
    e.setText(text)
    e.editingFinished.emit()


def _switch(ed, code):
    ed._combo.setCurrentIndex(ed._combo.findData(code))
    ed._combo.activated.emit(ed._combo.currentIndex())


def _recorder(ed):
    got = []
    ed.changed.connect(lambda c, p: got.append((c, list(p))))
    return got


# --------------------------------------------------------------------------
# DistroEditor
# --------------------------------------------------------------------------

@pytest.mark.parametrize("old", DELAY)
def test_editor_switching_to_every_other_code_stays_valid(old):
    from raptor.gui.ui.distro_editor import DistroEditor
    for mean in (0.25, 100.0):
        for new in DELAY:
            if new == old:
                continue
            ed = DistroEditor(old, params_for_mean(old, mean), use="Phase length")
            got = _recorder(ed)
            _switch(ed, new)
            assert ed.value()[0] == new
            assert ed.is_valid(), (old, new, mean, ed.value())
            assert ed.error() is None
            assert got and got[-1][0] == new
            assert validate_distro_params(*got[-1], "x") is None, got[-1]
            assert not ed._error_label.isVisibleTo(ed)
            ed.deleteLater()


@pytest.mark.parametrize("code", DELAY)
def test_editor_accepts_a_valid_value_for_every_field(code):
    from raptor.gui.ui.distro_editor import DistroEditor
    labels = DISTRO_PARAM_LABELS[code]
    ed = DistroEditor(code, params_for_mean(code, 10.0), use="Phase length")
    shown = [i for i, lab in enumerate(labels) if lab]
    assert len(ed._param_edits) == len(shown)
    for pos, i in enumerate(shown):
        got = _recorder(ed)
        cur = ed.value()[1][i]
        # the same value retyped is harmless; a changed valid one lands
        new = cur
        if labels[i] in ("Probability", "P Success"):
            new = 0.3 if cur != 0.3 else 0.4
        elif labels[i] in ("Location",):
            new = cur + 1.0
        elif code == 14:
            new = cur + 0.0     # triangular covered by its own test
        elif labels[i] in ("Shape", "N Trials") and code in (2, 3, 5):
            new = cur + 1.0
        elif code == 9:
            new = cur
        else:
            new = cur + 1.0
        if new == cur:
            continue
        _type(ed, pos, repr(new))
        if code in (10, 14):    # cross-field: Min may pass Max for a moment
            continue
        assert ed.value()[1][i] == pytest.approx(new), (code, labels[i])
        assert got and got[-1][1][i] == pytest.approx(new)
        assert ed.is_valid()


_BAD = {
    "nan": "nan", "inf": "inf", "negative": "-5", "text": "abc",
    "zero": "0",
}


@pytest.mark.parametrize("code", [c for c in DELAY if c not in (19,)])
def test_editor_refuses_a_bad_value_and_keeps_the_old_one(code):
    from raptor.gui.ui.distro_editor import DistroEditor
    labels = [lab for lab in DISTRO_PARAM_LABELS[code] if lab]
    ed = DistroEditor(code, params_for_mean(code, 10.0), use="Phase length")
    got = _recorder(ed)
    fails = []
    ed.validation_failed.connect(fails.append)
    for pos, lab in enumerate(labels):
        for name, text in (("nan", "nan"), ("inf", "inf"), ("text", "abc"),
                           ("negative", "-5")):
            if name == "negative" and code in (9, 10, 14):
                continue        # Min/Max sign is a whole-set rule: see below
            before = ed.value()
            n_fail = len(fails)
            _type(ed, pos, text)
            assert ed.value() == before, (code, lab, name, ed.value())
            assert ed.is_valid()
            assert ed._param_edits[pos].text() != text      # put back
            assert len(fails) == n_fail + 1, (code, lab, name)
    assert not got


@pytest.mark.parametrize("code,vals", [(9, [1.0, 5.0, 0.0]), (10, [1.0, 5.0, 0.0]),
                                       (14, [5.0, 10.0, 15.0])])
def test_editor_negative_min_is_held_invalid_and_never_emitted(code, vals):
    from raptor.gui.ui.distro_editor import DistroEditor
    ed = DistroEditor(code, vals, use="Phase length")
    got = _recorder(ed)
    _type(ed, 0, "-5")
    assert not ed.is_valid() and not got
    assert ed._error_label.isVisibleTo(ed)
    _type(ed, 0, "2")
    assert ed.is_valid() and got and got[-1][1][0] == 2.0


@pytest.mark.parametrize("bad", ["0.25", "2.5", "0", "-1"])
def test_editor_binomial_n_trials_must_be_a_positive_integer(bad):
    from raptor.gui.ui.distro_editor import DistroEditor
    ed = DistroEditor(3, [4.0, 0.5, 0.0], use="Phase length")
    got = _recorder(ed)
    _type(ed, 0, bad)
    assert ed.value() == (3, [4.0, 0.5, 0.0]) and not got
    _type(ed, 0, "6")
    assert ed.value() == (3, [6.0, 0.5, 0.0]) and got[-1] == (3, [6.0, 0.5, 0.0])


@pytest.mark.parametrize("bad", ["1.5", "0", "-0.1", "2"])
def test_editor_probability_must_be_in_range(bad):
    from raptor.gui.ui.distro_editor import DistroEditor
    ed = DistroEditor(3, [4.0, 0.5, 0.0], use="Phase length")
    got = _recorder(ed)
    _type(ed, 1, bad)
    assert ed.value() == (3, [4.0, 0.5, 0.0]) and not got
    _type(ed, 1, "0.4")
    assert ed.value()[1][1] == pytest.approx(0.4)


def test_editor_fixed_to_binomial_then_probability_can_be_typed():
    """The reported bug: Fixed 0.25 -> Binomial left 0.25 in N Trials."""
    from raptor.gui.ui.distro_editor import DistroEditor
    ed = DistroEditor(19, [0.25, 0.0, 0.0], use="Phase length")
    got = _recorder(ed)
    _switch(ed, 3)
    n, _p, _ = ed.value()[1]
    assert n == round(n) and n >= 1 and ed.is_valid()
    _type(ed, 1, "0.4")
    assert ed.value()[1][1] == pytest.approx(0.4)
    assert got[-1][0] == 3 and got[-1][1][1] == pytest.approx(0.4)


def test_editor_triangular_moved_up_in_field_order_holds_then_emits():
    from raptor.gui.ui.distro_editor import DistroEditor
    ed = DistroEditor(14, [5.0, 10.0, 15.0], use="Phase length")
    got = _recorder(ed)
    states = []
    ed.validity_changed.connect(states.append)
    _type(ed, 0, "20")
    assert ed.value()[1][0] == 20.0                # held, not reverted
    assert not ed.is_valid() and not got
    assert ed._error_label.isVisibleTo(ed) and ed._error_label.text()
    assert ed._stats_labels[0].text() == "—"
    _type(ed, 1, "30")
    assert ed.value()[1][:2] == [20.0, 30.0] and not ed.is_valid() and not got
    _type(ed, 2, "40")
    assert ed.value() == (14, [20.0, 30.0, 40.0]) and ed.is_valid()
    assert got == [(14, [20.0, 30.0, 40.0])]
    assert states == [False, True]
    assert not ed._error_label.isVisibleTo(ed)


def test_editor_uniform_min_above_max_holds_then_emits():
    from raptor.gui.ui.distro_editor import DistroEditor
    ed = DistroEditor(10, [1.0, 2.0, 0.0], use="Phase length")
    got = _recorder(ed)
    _type(ed, 0, "5")
    assert not ed.is_valid() and not got and ed.value()[1][0] == 5.0
    _type(ed, 1, "9")
    assert ed.is_valid() and got == [(10, [5.0, 9.0, 0.0])]


def _drive_dialog(monkeypatch, steps, button):
    """Replace exec_dialog: run `steps(editor, ok_button)` then press `button`."""
    from raptor.gui.ui import distro_editor as de
    seen = {}

    def fake_exec(dlg):
        ed = dlg.findChild(de.DistroEditor)
        bb = dlg.findChild(QDialogButtonBox)
        ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        seen["ok_start"] = ok.isEnabled()
        steps(ed, ok, seen)
        (ok if button == "ok" else bb.button(
            QDialogButtonBox.StandardButton.Cancel)).click()
        return dlg.result()

    monkeypatch.setattr(de, "exec_dialog", fake_exec)
    return seen


def test_dialog_ok_is_disabled_while_invalid_and_returns_a_valid_pair(monkeypatch):
    from raptor.gui.ui.distro_editor import edit_distro_dialog

    def steps(ed, ok, seen):
        _type(ed, 0, "20")                 # Triangular Min above Mode
        seen["ok_invalid"] = ok.isEnabled()
        _type(ed, 1, "30")
        _type(ed, 2, "40")
        seen["ok_valid"] = ok.isEnabled()

    seen = _drive_dialog(monkeypatch, steps, "ok")
    out = edit_distro_dialog(None, 14, [5.0, 10.0, 15.0], use="Phase length")
    assert seen["ok_start"] and seen["ok_invalid"] is False and seen["ok_valid"]
    assert out == (14, [20.0, 30.0, 40.0])


def test_dialog_ok_is_not_reachable_when_invalid(monkeypatch):
    from raptor.gui.ui.distro_editor import edit_distro_dialog

    def steps(ed, ok, seen):
        _type(ed, 0, "20")
        seen["ok_invalid"] = ok.isEnabled()

    seen = _drive_dialog(monkeypatch, steps, "ok")   # click on a disabled button
    out = edit_distro_dialog(None, 14, [5.0, 10.0, 15.0], use="Phase length")
    assert seen["ok_invalid"] is False
    assert out is None                                # nothing invalid returned


def test_dialog_fixed_to_binomial_returns_integer_n(monkeypatch):
    from raptor.gui.ui.distro_editor import edit_distro_dialog

    def steps(ed, ok, seen):
        _switch(ed, 3)
        _type(ed, 1, "0.4")

    _drive_dialog(monkeypatch, steps, "ok")
    code, vals = edit_distro_dialog(None, 19, [0.25, 0, 0], use="Phase length")
    assert code == 3 and vals[0] == round(vals[0]) and vals[1] == pytest.approx(0.4)
    assert validate_distro_params(code, vals, "x") is None


def test_dialog_cancel_returns_none(monkeypatch):
    from raptor.gui.ui.distro_editor import edit_distro_dialog
    _drive_dialog(monkeypatch, lambda ed, ok, seen: _switch(ed, 3), "cancel")
    assert edit_distro_dialog(None, 19, [0.25, 0, 0]) is None


# --------------------------------------------------------------------------
# Main window routes
# --------------------------------------------------------------------------

@pytest.fixture
def win():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbds(AIRLINER), AIRLINER)
    yield w
    w._dirty = False
    w.close()


def _row(win, bid):
    for r in win._rbd.sections["BLOCK_DATA"].rows:
        if r.get("ID") == str(bid):
            return r
    raise AssertionError(f"no BLOCK_DATA row for block {bid}")


def _row_dist(row, which):
    p = "F" if which == "F" else "R"
    n = int(row[f"#{p}Param"])
    vals = [float(row[f"{p}Param{i}"]) for i in (1, 2, 3)]
    return int(row[f"{p}Dist"]), vals, n


def test_phases_dialog_fixed_to_binomial_end_to_end(win, monkeypatch):
    from raptor.gui.ui import distro_editor as de
    from raptor.gui.ui.phasing_dialog import PhasingDialog
    v = win._diagram_dock.view

    def fake_exec(dlg):
        ed = dlg.findChild(de.DistroEditor)
        bb = dlg.findChild(QDialogButtonBox)
        _switch(ed, 3)
        _type(ed, 1, "0.4")
        bb.button(QDialogButtonBox.StandardButton.Ok).click()
        return dlg.result()

    monkeypatch.setattr(de, "exec_dialog", fake_exec)
    dlg = PhasingDialog(win)
    names = [p["PhaseName"] for p in dlg._phases]
    row_i = names.index("Taxi_Out")

    def phase():
        return next(r for r in win._rbd.sections["PHASES"].rows
                    if r["PhaseName"] == "Taxi_Out")

    assert (phase()["Dist"], float(phase()["Param1"])) == ("19", 0.25)
    depth = v._stack.index()
    dlg._on_define_double_clicked(row_i, 2)
    ph = phase()
    assert ph["Dist"] == "3"
    n = float(ph["Param1"])
    assert n == round(n) and n >= 1
    assert float(ph["Param2"]) == pytest.approx(0.4)
    assert validate_distro_params(
        3, [float(ph["Param1"]), float(ph["Param2"]), float(ph["Param3"])],
        "x") is None
    v._stack.undo()
    ph = phase()
    assert ph["Dist"] == "19" and float(ph["Param1"]) == pytest.approx(0.25)
    assert v._stack.index() == depth, "the change was more than one undo step"


def test_phases_dialog_refuses_nothing_invalid_when_cancelled(win, monkeypatch):
    from raptor.gui.ui import distro_editor as de
    from raptor.gui.ui.phasing_dialog import PhasingDialog

    def fake_exec(dlg):
        ed = dlg.findChild(de.DistroEditor)
        _switch(ed, 3)
        dlg.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Cancel).click()
        return dlg.result()

    monkeypatch.setattr(de, "exec_dialog", fake_exec)
    dlg = PhasingDialog(win)
    dlg._on_define_double_clicked(0, 2)
    ph = win._rbd.sections["PHASES"].rows[0]
    assert ph["Dist"] == "19" and float(ph["Param1"]) == pytest.approx(0.25)


_FAIL = [c for c in OFFERED["failure"] if c not in (16, 18)]
_REPAIR = [c for c in OFFERED["repair"] if c not in (16, 18)]


def _block_state(win, bid, which):
    b = win._diagram_dock.view.blocks[bid]
    if which == "F":
        return b.fail_distro, list(b.fail_values)
    return b.repair_distro, list(b.repair_values)


@pytest.mark.parametrize("which,old", [("F", c) for c in _FAIL] +
                         [("R", c) for c in _REPAIR])
def test_inspector_distribution_change_is_valid_in_model_and_one_undo(win, which, old):
    v = win._diagram_dock.view
    bid = min(v.blocks)
    lists = OFFERED["failure" if which == "F" else "repair"]
    for mean in (0.25 if old % 2 else 8760.0,):    # one mean per case keeps it fast
        v.set_block_dist(bid, which, old)
        (v.set_block_fail_params if which == "F" else v.set_block_repair_params)(
            bid, params_for_mean(old, mean))
        before = _block_state(win, bid, which)
        for new in lists:
            if new == old:
                continue
            depth = v._stack.index()
            win._on_block_dist_type_changed(bid, which, new)
            code, vals = _block_state(win, bid, which)
            assert code == new
            row_code, row_vals, _n = _row_dist(_row(win, bid), which)
            assert row_code == new, (old, new)
            if new not in (16, 18):
                assert validate_distro_params(
                    new, (vals + [0.0] * 3)[:3], "x") is None, (old, new, vals)
                assert validate_distro_params(new, row_vals, "x") is None, (
                    old, new, row_vals)
                assert row_vals == pytest.approx((vals + [0.0] * 3)[:3], abs=1e-5)
            assert v._stack.index() == depth + 1, (old, new, "not one undo step")
            v._stack.undo()
            assert _block_state(win, bid, which) == before, (old, new)
            row_code, row_vals, _ = _row_dist(_row(win, bid), which)
            assert row_code == old
            assert row_vals == pytest.approx(
                (before[1] + [0.0] * 3)[:3], abs=1e-5)


def _inspector_edits(win, use):
    return [e for e in win._inspector.findChildren(QLineEdit)
            if e.accessibleName().startswith(use + " ")]


def _commit(edit, text):
    edit.setText(text)
    edit.editingFinished.emit()


def test_inspector_field_typing_after_a_change_to_binomial(win):
    v = win._diagram_dock.view
    bid = min(v.blocks)
    v.set_block_dist(bid, "F", 19)
    v.set_block_fail_params(bid, [0.25, 0.0, 0.0])
    win._on_block_dist_type_changed(bid, "F", 3)
    win._inspector.show_block(bid, v.blocks)
    edits = _inspector_edits(win, "Failure")
    assert len(edits) == 2
    fails = []
    win._inspector.validation_failed.connect(fails.append)
    _commit(edits[1], "0.4")
    assert not fails
    code, vals = _block_state(win, bid, "F")
    assert code == 3 and vals[1] == pytest.approx(0.4)
    assert validate_distro_params(3, (vals + [0, 0])[:3], "x") is None
    assert float(_row(win, bid)["FParam2"]) == pytest.approx(0.4)
    # a bad one is refused and changes nothing
    edits = _inspector_edits(win, "Failure")
    _commit(edits[1], "1.5")
    assert fails and _block_state(win, bid, "F")[1][1] == pytest.approx(0.4)
    assert float(_row(win, bid)["FParam2"]) == pytest.approx(0.4)
    _commit(edits[0], "nan")
    assert _block_state(win, bid, "F")[1][0] == vals[0]


def test_inspector_triangular_can_be_moved_up_in_field_order(win):
    v = win._diagram_dock.view
    bid = min(v.blocks)
    v.set_block_dist(bid, "F", 14)
    v.set_block_fail_params(bid, [5.0, 10.0, 15.0])
    win._inspector.show_block(bid, v.blocks)
    for pos, text in enumerate(("20", "30", "40")):
        _commit(_inspector_edits(win, "Failure")[pos], text)
    assert _block_state(win, bid, "F")[1][:3] == [20.0, 30.0, 40.0]

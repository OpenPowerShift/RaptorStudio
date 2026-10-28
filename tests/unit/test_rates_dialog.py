"""Rates library dialog: named, reusable value expressions that a block's
Failure/Repair Param field can link to via "@Name" instead of retyping the
same km/yr-style derivation on every block."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow
from raptor.gui.ui.rates_dialog import (
    RatesDialog, pick_rate, attach_rate_autocomplete,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOLD = os.path.join(REPO, "tests", "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def w(qapp):
    win = MainWindow()
    win._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "legacy.rbd")
    return win


def test_add_edit_delete_rate(w):
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    dlg._add_rate()
    QApplication.processEvents()
    # Apply-gated: the model is untouched until Apply.
    assert w._rbd.rates == []
    dlg.name_edit.setText("Highway")
    dlg.expr_edit.setText(
        "0.00015 * 20000 / 8760  # 0.00015 fail/km, 20000 km/yr")
    dlg._commit_current()
    # Apply-gated: the model is untouched until Apply.
    assert w._rbd.rates == []
    dlg._apply_edits()
    assert w._rbd.rates[0]["name"] == "Highway"
    assert abs(w._rbd.rates[0]["value"] - 0.00015 * 20000 / 8760) < 1e-9
    assert dlg.value_label.text() != "—"

    dlg._delete_rate()
    dlg._apply_edits()
    assert w._rbd.rates == []


def test_invalid_expression_shows_error_not_a_value(w):
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    dlg._add_rate()
    dlg.expr_edit.setText("__import__('os')")
    dlg._commit_current()
    assert dlg.value_label.text() == "—"
    assert dlg.error_label.text()
    dlg._apply_edits()
    # the bad expression must not have been saved as the rate's value
    assert w._rbd.rates[0]["expr"] != "__import__('os')"


def test_duplicate_name_rejected(w):
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    dlg._add_rate()
    dlg._add_rate()
    assert dlg.rates[0]["name"] != dlg.rates[1]["name"]
    dlg.listw.setCurrentRow(1)
    dlg.name_edit.setText(dlg.rates[0]["name"])
    dlg._commit_current()
    assert dlg.rates[1]["name"] != dlg.rates[0]["name"], \
        "renaming to an existing name must be rejected"


def test_picker_mode_returns_picked_name(w, monkeypatch):
    """`pick_rate` constructs its own dialog internally -- simulate a
    user selecting the first row and clicking "Use this rate" by
    patching `exec()` to drive that same selection on WHICHEVER
    instance it's called on, then checking the real return value."""
    w._rbd.rates.append({"name": "Highway", "expr": "1+1", "value": 2.0})

    def fake_exec(self):
        self.listw.setCurrentRow(0)
        self._use_selected()
        return QDialog.Accepted
    monkeypatch.setattr(RatesDialog, "exec", fake_exec)

    name = pick_rate(w, w._rbd, w._mark_dirty)
    assert name == "Highway"


def test_picker_mode_cancel_returns_none(w, monkeypatch):
    w._rbd.rates.append({"name": "Highway", "expr": "1+1", "value": 2.0})
    monkeypatch.setattr(RatesDialog, "exec", lambda self: QDialog.Rejected)
    assert pick_rate(w, w._rbd, w._mark_dirty) is None


def test_description_field_round_trips(w):
    """Saving a rate stores its description, and reselecting the row
    shows it back."""
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    dlg._add_rate()
    QApplication.processEvents()
    dlg.name_edit.setText("Highway")
    dlg.expr_edit.setText("1.71")
    dlg.desc_edit.setText("Highway failure rate")
    dlg._commit_current()
    dlg._apply_edits()
    assert w._rbd.rates[0]["description"] == "Highway failure rate"

    # reselecting the row must show the saved description back
    dlg._add_rate()
    QApplication.processEvents()
    dlg.listw.setCurrentRow(0)
    assert dlg.desc_edit.text() == "Highway failure rate"


def test_rate_expression_can_reference_another_rate(w):
    """A rate's own expression can embed a reference to another saved
    rate, not just plain arithmetic."""
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    dlg._add_rate()
    dlg.name_edit.setText("Base")
    dlg.expr_edit.setText("0.5")
    dlg._commit_current()

    dlg._add_rate()
    dlg.listw.setCurrentRow(1)
    dlg.name_edit.setText("Derived")
    dlg.expr_edit.setText("@Base * 1.4")
    dlg._commit_current()
    dlg._apply_edits()
    assert abs(w._rbd.rates[1]["value"] - 0.5 * 1.4) < 1e-9
    assert dlg.error_label.text() == ""


def test_rate_referencing_unknown_rate_shows_error(w):
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    dlg._add_rate()
    dlg.expr_edit.setText("@NoSuchRate * 2")
    dlg._commit_current()
    assert dlg.error_label.text()
    assert dlg.value_label.text() == "—"


def test_attach_rate_autocomplete_replaces_only_the_at_token():
    """The completer must replace just the "@word" being typed, not
    the whole field -- an "@Rate" reference can sit inside a larger
    expression."""
    from PySide6.QtWidgets import QLineEdit, QCompleter
    edit = QLineEdit()
    attach_rate_autocomplete(edit, lambda: ["BaseRate", "Base2", "Other"])

    edit.setText("2 * @Ba")
    edit.setCursorPosition(len("2 * @Ba"))
    edit.textEdited.emit(edit.text())
    QApplication.processEvents()
    completer = edit.findChild(QCompleter)
    assert completer is not None
    names = set()
    for i in range(completer.completionCount()):
        completer.setCurrentRow(i)
        names.add(completer.currentCompletion())
    assert names == {"@BaseRate", "@Base2"}

    completer.activated.emit("@BaseRate")
    QApplication.processEvents()
    assert edit.text() == "2 * @BaseRate"


def test_close_without_apply_closes_silently(w, monkeypatch):
    """Close/Cancel closes without an Apply discard confirm: the
    Apply-gated buffer is throwaway, so closing does not touch the model.
    Stubbing `question_box` to raise proves no confirm path runs."""
    import raptor.gui.ui.dialogs as dialogs_mod
    from PySide6.QtWidgets import QPushButton

    def _boom(*a, **kw):
        raise AssertionError("discard confirm must not appear on Close")
    monkeypatch.setattr(dialogs_mod, "question_box", _boom)

    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False,
                      view=w._diagram_dock.view)
    dlg._add_rate()
    assert dlg._dirty
    close = next(b for b in dlg.findChildren(QPushButton)
                 if b.text() == "Close")
    close.click()
    QApplication.processEvents()
    assert not dlg.isVisible()
    # the model was never touched (buffer dropped)
    assert w._rbd.rates == []


# ---------------------------------------------------------------------------
# Rate identity + provenance: ONE owner with raptor.api.rates (review D-20)
#
# The dialog used to carry its own copy of two rules the API already owned,
# and its copy was wrong both times. The pure-function behaviour is pinned
# in tests/unit/test_rates_single_owner.py; these are the end-to-end
# statements through the real dialog.
# ---------------------------------------------------------------------------


def _mk(name, expr="1", value=1.0):
    return {"name": name, "expr": expr, "value": value, "description": ""}


def _add(dlg, name, expr="1"):
    """Add a rate through the dialog's own Add -> rename -> Apply cycle.

    `_commit_current` is the step that pushes the name/expression fields
    into the buffer row; without it Apply writes the auto-generated
    `NewRate`.
    """
    dlg._add_rate()
    dlg.name_edit.setText(name)
    dlg.expr_edit.setText(expr)
    QApplication.processEvents()
    dlg._commit_current()
    QApplication.processEvents()
    dlg._apply_edits()
    QApplication.processEvents()


def test_rename_to_a_case_variant_is_refused(w):
    """THE D-20 BUG. A case-sensitive check let this through, and the API
    then refused the second name and deleted whichever came first."""
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    _add(dlg, "Highway")
    _add(dlg, "Rail")
    assert [r["name"] for r in w._rbd.rates] == ["Highway", "Rail"]

    dlg.listw.setCurrentRow(1)          # Rail
    QApplication.processEvents()
    dlg.name_edit.setText("highway")     # collides with Highway, case-insensitively
    dlg._commit_current()
    QApplication.processEvents()
    assert dlg.error_label.text(), (
        "a case-variant rename must be refused, exactly as an exact-match "
        "one is -- otherwise the API refuses this name afterwards")
    assert [r["name"] for r in dlg.rates] == ["Highway", "Rail"], (
        "the rename must not have been applied to the buffer")


def test_a_genuinely_new_name_is_still_accepted(w):
    """The boundary: the case-insensitive check must not refuse a name
    that merely looks similar."""
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    _add(dlg, "Highway")
    dlg.listw.setCurrentRow(0)
    QApplication.processEvents()
    dlg.name_edit.setText("Highway2")
    dlg._commit_current()
    QApplication.processEvents()
    assert not dlg.error_label.text(), dlg.error_label.text()
    assert [r["name"] for r in dlg.rates] == ["Highway2"]


def test_the_auto_namer_does_not_generate_a_collision(w):
    """`_add_rate` picked `NewRate`, then `NewRate2`, ... using a
    case-SENSITIVE set. Given an existing `newrate`, `name in existing` is
    False for `NewRate`, so the dialog invented a second rate whose name
    `find_rate` then refuses to address."""
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    _add(dlg, "newrate")
    QApplication.processEvents()
    dlg._add_rate()
    QApplication.processEvents()
    names = [r["name"] for r in dlg.rates]
    assert len(names) == 2
    assert names[1].lower() != names[0].lower(), (
        f"the auto-namer generated {names[1]!r}, which collides with the "
        f"existing {names[0]!r} under the case-insensitive rule")
    from raptor.api.rates import find_rate
    assert find_rate(dlg.rates, names[1]) == 1, (
        f"the generated name is not addressable: {names}")


def test_deleting_a_rate_purges_its_formula_provenance(w):
    """The API's delete always purged the `{"rate": name}` entries; the
    dialog's just did `del self.rates[i]`, leaving the stale `@Name`
    reference that main_window's own legacy-save warning complains about."""
    w._rbd.rates = [_mk("Highway"), _mk("Rail")]
    w._rbd.formulas = {
        "Block:1:FParam1": {"rate": "Highway", "value": 3.0},
        "Block:1:FParam2": {"rate": "Rail", "value": 4.0},
        "Block:1:FParam3": {"expr": "2 * 3", "value": 6.0},
    }
    view = w._diagram_dock.view
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False, view=view)
    QApplication.processEvents()

    dlg.listw.setCurrentRow(0)          # Highway
    QApplication.processEvents()
    dlg._delete_rate()
    QApplication.processEvents()
    assert [r["name"] for r in dlg.rates] == ["Rail"]
    assert "Block:1:FParam1" in w._rbd.formulas, (
        "Apply-gated: nothing reaches the model yet")

    dlg._apply_edits()
    QApplication.processEvents()
    assert "Block:1:FParam1" not in w._rbd.formulas, (
        f"deleted rate's provenance survived: {w._rbd.formulas}")
    assert "Block:1:FParam2" in w._rbd.formulas, "live rate lost its entry"
    assert "Block:1:FParam3" in w._rbd.formulas, "an expr entry was dropped"


def test_undoing_a_rate_delete_restores_the_provenance(w):
    """If the purge happened outside the undo command, Undo would restore
    the rate while leaving its provenance purged -- reintroducing the very
    dangling `@Name` the prune removed."""
    w._rbd.rates = [_mk("Highway"), _mk("Rail")]
    w._rbd.formulas = {"Block:1:FParam1": {"rate": "Highway", "value": 3.0}}
    view = w._diagram_dock.view
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False, view=view)
    QApplication.processEvents()

    dlg.listw.setCurrentRow(0)
    QApplication.processEvents()
    dlg._delete_rate()
    QApplication.processEvents()
    dlg._apply_edits()
    QApplication.processEvents()
    assert "Block:1:FParam1" not in w._rbd.formulas

    view._stack.undo()
    QApplication.processEvents()
    assert [r["name"] for r in w._rbd.rates] == ["Highway", "Rail"]
    assert w._rbd.formulas.get("Block:1:FParam1") == {
        "rate": "Highway", "value": 3.0}, (
        "undo restored the rate but not its provenance -- that is the "
        f"dangling-reference corruption: {w._rbd.formulas}")


def test_the_two_doors_agree_on_which_rate_a_name_means(w):
    """The end-to-end statement of D-20: build `Highway` in the GUI, then
    have the API address it as `highway`."""
    dlg = RatesDialog(w, w._rbd, w._mark_dirty, picker=False)
    _add(dlg, "Highway")
    QApplication.processEvents()
    from raptor.api.rates import find_rate
    assert find_rate(w._rbd.rates, "highway") == 0
    assert find_rate(w._rbd.rates, "HIGHWAY") == 0

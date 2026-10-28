"""Rates library: named, reusable value expressions with a
human-readable basis -- e.g. a Highway failure rate derived from
"0.00015 failures/km x 20000 km/yr" -- defined once here and then
linked into any block's Failure/Repair Param field (or any other value
field that supports formulas, see `expr_eval.py`) instead of retyping
the same derivation on every block that shares it.

Same list + detail-form + Add/Delete/Apply shape as `pool_dialogs.py`'s
`_EntityDialog` (Spare Pools/Resources/Triggers/Phases), Apply-gated the
same way: edits stay in the dialog buffer until Apply. Rates aren't a
`.rbd` section -- there's no RowCellCmd/TableRowCmd; Apply pushes one
whole-library `RatesCmd` onto the view's undo stack when a view is
reachable (ribbon entry point), so Edit-menu Undo/Redo covers rate edits
too. View-less entry points (the Inspector's rate picker) fall back to a
direct write of `RbdFile.rates` (a plain .rbds-only list) with no undo.
"""
from __future__ import annotations
from raptor.gui.ui.modality import exec_dialog

from raptor.gui.ui.qtcompat import req

from collections.abc import Callable

from PySide6.QtCore import Qt, QStringListModel
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QFormLayout, QLineEdit, QPushButton, QLabel, QGroupBox,
    QCompleter,
)

from raptor.api.rates import find_rate, prune_formulas, rate_name_conflict
from raptor.gui.resources.design_tokens import SPACING, active_colour
from raptor.gui.ui.expr_eval import resolve, ExpressionError


class RatesCmd(QUndoCommand):
    """Swap the whole rates library (old list <-> new list) as ONE
    undoable step, so Edit-menu Undo/Redo covers rate edits like every
    other model edit.

    The formula records ride along with the swap. They are not
    decoration: a ``{"rate": name}`` entry is the provenance of a stored
    float, so restoring a deleted rate WITHOUT its provenance would
    resurrect the dangling ``@Name`` reference that
    ``api.rates.prune_formulas`` exists to prevent -- the undo would
    reintroduce the very corruption the delete removed.
    """

    def __init__(self, view, rbd, old, new, old_formulas, new_formulas):
        super().__init__("Edit rates")
        self._v = view
        self._rbd = rbd
        self._old = [dict(r) for r in old]
        self._new = [dict(r) for r in new]
        self._old_f = dict(old_formulas or {})
        self._new_f = dict(new_formulas or {})

    def _apply(self, rows, formulas):
        self._rbd.rates = [dict(r) for r in rows]
        self._rbd.formulas = {k: dict(v) for k, v in formulas.items()}
        # Every field linked to a rate takes the rate's value. It used to
        # keep the old number -- the Inspector showed "@GenMTBF" beside a
        # figure from the previous value, and a run used that figure. A
        # function of rates + formulas, so undo is "old library, propagate".
        from raptor.api.rates import propagate_rates
        changed = propagate_rates(self._rbd)
        if self._v is not None:
            if changed:
                self._v._rebuild_scene()     # block faces show the values
            if not self._v._loading:
                self._v.model_changed.emit()

    def redo(self):
        self._apply(self._new, self._new_f)

    def undo(self):
        self._apply(self._old, self._old_f)


def attach_rate_autocomplete(edit: QLineEdit,
                             get_rate_names: Callable[[], list[str]]) -> None:
    """Autocomplete after "@". Popping a completer for the whole field
    (Qt's default) would be wrong here since "@Rate" can sit inside a
    larger expression ("@Rate * 1.4"); this completes only the "@word"
    token at the cursor, leaving the rest of the text alone.
    `get_rate_names` is a callable (not a fixed list) so a caller whose
    rate list can change after the field is built always sees the
    current names."""
    completer = QCompleter(edit)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setWidget(edit)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)

    def _word_bounds() -> tuple[int, int] | None:
        """(start, end) of the "@word" token the cursor is currently
        inside/at the end of, or None if the cursor isn't in one."""
        pos = edit.cursorPosition()
        text = edit.text()
        start = pos
        while start > 0 and (text[start - 1].isalnum()
                             or text[start - 1] == "_"):
            start -= 1
        if start > 0 and text[start - 1] == "@":
            return start - 1, pos
        return None

    def on_edited(_text: str):
        bounds = _word_bounds()
        if bounds is None:
            req(completer.popup()).hide()
            return
        start, end = bounds
        word = edit.text()[start:end]   # includes the leading "@"
        model = QStringListModel(
            [f"@{n}" for n in get_rate_names()], completer)
        completer.setModel(model)
        completer.setCompletionPrefix(word)
        if completer.completionCount() == 0:
            req(completer.popup()).hide()
            return
        rect = edit.cursorRect()
        rect.setWidth(req(completer.popup()).sizeHintForColumn(0)
                      + req(completer.popup()).verticalScrollBar()
                      .sizeHint().width())
        completer.complete(rect)

    def on_activated(text: str):
        bounds = _word_bounds()
        if bounds is None:
            return
        start, end = bounds
        cur = edit.text()
        edit.setText(cur[:start] + text + cur[end:])
        edit.setCursorPosition(start + len(text))

    edit.textEdited.connect(on_edited)
    completer.activated.connect(on_activated)


class RatesDialog(QDialog):
    """`rbd`/`mark_dirty` are passed explicitly rather than a MainWindow
    reference -- this dialog can be opened from either the ribbon (which
    has a MainWindow) or the Inspector dock (which only holds the
    `RbdFile` itself, never a MainWindow reference)."""

    def __init__(self, parent, rbd, mark_dirty, picker: bool = False,
                 view=None):
        super().__init__(parent)
        self._rbd = rbd
        self._mark_dirty = mark_dirty
        #: The DiagramView owning the undo stack, when one is reachable
        #: (ribbon entry point). Apply pushes one RatesCmd so Edit-menu
        #: Undo/Redo covers rate edits; view-less entry points (the
        #: Inspector's rate picker) fall back to a direct write.
        self._view = view
        self.picker = picker
        self.picked_name: str | None = None
        self.rates: list[dict] = [dict(r) for r in
                                  getattr(rbd, "rates", []) or []]
        self.setWindowTitle("Rates" + (" — Use a Saved Rate" if picker
                                       else ""))
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setSpacing(SPACING["md"])
        top = QHBoxLayout()
        top.setSpacing(SPACING["md"])
        outer.addLayout(top, 1)

        list_col = QVBoxLayout()
        list_col.setSpacing(SPACING["xs"])
        list_col.addWidget(QLabel("Rates"))
        self.listw = QListWidget()
        self.listw.setMinimumWidth(170)
        list_col.addWidget(self.listw, 1)
        top.addLayout(list_col, 1)

        card = QGroupBox("Rate — Details")
        self.form = QFormLayout(card)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.form.setHorizontalSpacing(SPACING["md"])
        self.form.setVerticalSpacing(SPACING["xs"])
        top.addWidget(card, 2)

        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._commit_current)
        self.form.addRow("Name:", self.name_edit)
        self.expr_edit = QLineEdit()
        self.expr_edit.setPlaceholderText(
            "0.00015 * 20000 / 8760  # 0.00015 fail/km, 20000 km/yr")
        self.expr_edit.editingFinished.connect(self._commit_current)
        self.form.addRow("Expression:", self.expr_edit)
        attach_rate_autocomplete(
            self.expr_edit,
            lambda: [x.get("name", "") for x in
                    self._other_rates(self._current())])
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText(
            "What this rate represents, e.g. \"Highway failure rate\"")
        self.desc_edit.editingFinished.connect(self._commit_current)
        self.form.addRow("Description:", self.desc_edit)
        self.value_label = QLabel("—")
        self.form.addRow("Value:", self.value_label)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(
            f"color: {active_colour()['failed']};")
        self.form.addRow("", self.error_label)

        bottom = QHBoxLayout()
        bottom.setSpacing(SPACING["sm"])
        add_b = QPushButton("Add")
        del_b = QPushButton("Delete")
        add_b.clicked.connect(self._add_rate)
        del_b.clicked.connect(self._delete_rate)
        bottom.addWidget(add_b)
        bottom.addWidget(del_b)
        bottom.addStretch(1)
        if picker:
            use_b = QPushButton("Use this rate")
            use_b.clicked.connect(self._use_selected)
            bottom.addWidget(use_b)
        else:
            apply_b = QPushButton("Apply")
            apply_b.clicked.connect(self._apply_edits)
            bottom.addWidget(apply_b)
        close_b = QPushButton("Close" if not picker else "Cancel")
        close_b.clicked.connect(self.reject)
        bottom.addWidget(close_b)
        outer.addLayout(bottom)

        self._building = False
        self._dirty = False
        self.listw.currentRowChanged.connect(self._on_row_changed)
        self._reload_list(0)

    # ------------------------------------------------------------------
    def _reload_list(self, select_row: int = 0):
        self.listw.blockSignals(True)
        self.listw.clear()
        for r in self.rates:
            self.listw.addItem(QListWidgetItem(r.get("name", "?")))
        self.listw.blockSignals(False)
        if self.rates:
            self.listw.setCurrentRow(
                min(max(select_row, 0), len(self.rates) - 1))
        else:
            self._on_row_changed(-1)

    def _current(self) -> dict | None:
        i = self.listw.currentRow()
        return self.rates[i] if 0 <= i < len(self.rates) else None

    def _on_row_changed(self, _row: int):
        self._building = True
        try:
            r = self._current()
            enabled = r is not None
            self.name_edit.setEnabled(enabled)
            self.expr_edit.setEnabled(enabled)
            self.desc_edit.setEnabled(enabled)
            self.name_edit.setText(r.get("name", "") if r else "")
            self.expr_edit.setText(r.get("expr", "") if r else "")
            self.desc_edit.setText(r.get("description", "") if r else "")
            self._refresh_value_display(r)
        finally:
            self._building = False

    def _other_rates(self, r: dict | None) -> list[dict]:
        """Every other rate (excludes `r` itself) -- passed to
        `resolve()` so this rate's own expression can reference a saved
        rate too, e.g. a derived rate built from a base one. A rate
        referencing itself would find itself excluded here and correctly
        error as unknown, not infinite-loop."""
        return [x for x in self.rates if x is not r]

    def _refresh_value_display(self, r: dict | None):
        if r is None:
            self.value_label.setText("—")
            self.error_label.setText("")
            return
        try:
            v = resolve(r.get("expr", ""), self._other_rates(r))
            self.value_label.setText(f"{v:g}")
            self.error_label.setText("")
        except ExpressionError as e:
            self.value_label.setText("—")
            self.error_label.setText(str(e))

    def _commit_current(self):
        """Buffer the visible editors into the current rate (Apply-gated:
        nothing reaches the model until Apply / Use this rate)."""
        if self._building:
            return
        r = self._current()
        if r is None:
            return
        new_name = self.name_edit.text().strip()
        new_expr = self.expr_edit.text()
        if new_name and new_name != r.get("name"):
            # DELEGATED. This used to be a case-SENSITIVE set membership
            # test (`new_name in {x.get("name") ...}`), while
            # `api.rates.find_rate` matches case-insensitively -- so the
            # dialog would accept `Highway` beside `highway` and the API
            # would then refuse the second name and delete whichever came
            # first. One owner for rate identity.
            clash = rate_name_conflict(self.rates, new_name, exclude=r)
            if clash is not None:
                self.error_label.setText(
                    f"A rate named {clash!r} already exists.")
                self.name_edit.setText(r.get("name", ""))
                return
            r["name"] = new_name
            self.listw.item(self.listw.currentRow()).setText(new_name)
        r["description"] = self.desc_edit.text().strip()
        try:
            v = resolve(new_expr, self._other_rates(r))
            r["expr"] = new_expr
            r["value"] = v
            self.error_label.setText("")
            self.value_label.setText(f"{v:g}")
        except ExpressionError as e:
            self.error_label.setText(str(e))
            self.value_label.setText("—")
            return
        self._dirty = True

    def _apply_edits(self):
        """Push the buffered library into the model -- one undoable
        RatesCmd when a view (undo stack) is reachable, else a direct
        write (view-less picker entry point).

        Both paths also run `prune_formulas`, the same rule
        `api.rates.delete_rate` applies. It used to be applied only on the
        API door, so deleting a rate in this dialog left its
        ``{"rate": name}`` provenance entries behind -- the stale ``@Name``
        corruption that then surfaced in the GUI's own legacy-save warning
        at `main_window.py:3043`.
        """
        if self._building:
            return
        old = [dict(r) for r in getattr(self._rbd, "rates", []) or []]
        old_formulas = dict(getattr(self._rbd, "formulas", None) or {})
        new_formulas = prune_formulas(self.rates, old_formulas)
        if self._view is not None:
            self._view._stack.push(RatesCmd(self._view, self._rbd,
                                            old, self.rates,
                                            old_formulas, new_formulas))
        else:
            self._save_to_model(new_formulas)
        self._dirty = False

    def _save_to_model(self, formulas=None):
        self._rbd.rates = [dict(r) for r in self.rates]
        if formulas is not None:
            self._rbd.formulas = formulas
        self._mark_dirty()

    def reject(self) -> None:
        """Close/Cancel just closes: the Apply-gated buffer is
        throwaway (the model was never touched), so there is no discard
        confirmation, matching the Raptor 7's own Cancel buttons."""
        self._dirty = False
        super().reject()

    # ------------------------------------------------------------------
    def _add_rate(self):
        base = "NewRate"
        n = 1
        name = base
        # Case-insensitively, via the shared owner. With a plain
        # case-sensitive set this could *generate* a collision: given an
        # existing `newrate`, `name in existing` is False for `NewRate`,
        # so the dialog invented a second rate that `find_rate` then
        # refuses to address.
        while find_rate(self.rates, name) is not None:
            n += 1
            name = f"{base}{n}"
        self.rates.append({"name": name, "expr": "0", "value": 0.0,
                          "description": ""})
        self._dirty = True
        self._reload_list(len(self.rates) - 1)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _delete_rate(self):
        i = self.listw.currentRow()
        if not (0 <= i < len(self.rates)):
            return
        del self.rates[i]
        self._dirty = True
        self._reload_list(max(0, i - 1))

    def _use_selected(self):
        r = self._current()
        if r is None:
            return
        if self._dirty:
            self._apply_edits()
        self.picked_name = r.get("name")
        self.accept()


def open_rates_dialog(win) -> None:
    """Ribbon entry point -- full manage (add/edit/delete)."""
    if getattr(win, "_rbd", None) is None:
        return
    dlg = RatesDialog(win, win._rbd, win._mark_dirty, picker=False,
                      view=win._diagram_dock.view)
    dlg.resize(640, 400)
    exec_dialog(dlg)


def pick_rate(parent, rbd, mark_dirty) -> str | None:
    """Field-level entry point ("Use saved rate…", e.g. the Inspector's
    Failure/Repair Param rows) -- returns the picked rate's name, or
    None if cancelled/no rates exist. Takes `rbd`/`mark_dirty`
    directly rather than a MainWindow (see class docstring): the
    Inspector dock never holds a MainWindow reference, only the
    `RbdFile` itself."""
    if rbd is None:
        return None
    dlg = RatesDialog(parent, rbd, mark_dirty, picker=True)
    dlg.resize(640, 400)
    if exec_dialog(dlg) == QDialog.DialogCode.Accepted:
        return dlg.picked_name
    return None

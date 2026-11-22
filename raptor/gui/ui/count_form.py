"""One dialog per "how many?" question, instead of a chain of popups.

Quick Add's parallel pattern, Duplicate in Parallel and Array each asked two
or three questions in separate `QInputDialog`s, and the redundancy ones
defaulted to "all must work" -- a series arrangement drawn in parallel.
Here every question sits on one form, k defaults to 1 and a line under it
says what the choice means.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QSpinBox,
    QWidget,
)

from raptor.gui import i18n


def redundancy_hint(k: int, n: int) -> str:
    """What k-of-n means, in words."""
    if n <= 1:
        return "One block: no redundancy."
    if k >= n:
        return (f"All {n} must work: no redundancy, it behaves like {n} blocks "
                "in series.")
    spare = n - k
    return (f"{k} of {n} must work: the group survives {spare} "
            f"failure{'s' if spare != 1 else ''} at once.")


class CountForm(QDialog):
    """A small form of whole-number and choice fields."""

    def __init__(self, parent: QWidget | None, title: str, intro: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._form = QFormLayout(self)
        if intro:
            note = QLabel(intro)
            note.setWordWrap(True)
            self._form.addRow(note)
        self._fields: dict[str, QSpinBox | QComboBox] = {}
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

    def int_field(self, key: str, label: str, default: int, lo: int, hi: int,
                  tip: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        spin.setToolTip(tip)
        self._form.addRow(label, spin)
        self._fields[key] = spin
        return spin

    def choice_field(self, key: str, label: str, choices: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(choices)
        self._form.addRow(label, combo)
        self._fields[key] = combo
        return combo

    def note(self) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        self._form.addRow(label)
        return label

    def finish(self) -> None:
        self._form.addRow(self._buttons)

    def values(self) -> dict[str, int | str]:
        return {k: (f.value() if isinstance(f, QSpinBox) else f.currentText())
                for k, f in self._fields.items()}


def redundancy_form(parent: QWidget | None, title: str, *, count_label: str,
                    count_default: int, count_max: int, already: int = 0,
                    intro: str = "") -> CountForm:
    """A form asking how many blocks (`count`) and how many must work (`k`).

    `already` blocks exist besides the new ones (Duplicate in Parallel keeps
    the original), so the group is `count + already` wide."""
    form = CountForm(parent, title, intro)
    count = form.int_field("count", count_label, count_default, 1, count_max)
    k_label = QLabel()
    k = QSpinBox()
    k.setRange(1, count_default + already)
    k.setValue(1)
    form._form.addRow(k_label, k)
    form._fields["k"] = k
    hint = form.note()

    def update() -> None:
        n = count.value() + already
        k.setMaximum(n)
        k_label.setText(f"How many of the {n} must work (k):")
        hint.setText(redundancy_hint(k.value(), n))

    count.valueChanged.connect(update)
    k.valueChanged.connect(update)
    update()
    form.finish()
    return form


def ask_redundancy(parent: QWidget | None, title: str, **kw) -> tuple[int, int] | None:
    """(count, k), or None when cancelled."""
    form = redundancy_form(parent, title, **kw)
    if i18n.exec_translated(form) != QDialog.DialogCode.Accepted:
        return None
    v = form.values()
    return int(v["count"]), int(v["k"])


def array_form(parent: QWidget | None) -> CountForm:
    form = CountForm(parent, "Array",
                     "Copies of the selection, in a row or a column.")
    form.int_field("copies", "Copies:", 3, 1, 200)
    form.choice_field("direction", "Direction:", ["right", "down"])
    form.int_field("spacing", "Distance between copies (block widths):", 2, 1, 20,
                   "1 puts the copies edge to edge; 2 leaves a block's width "
                   "between them")
    form.finish()
    return form


def ask_array(parent: QWidget | None) -> tuple[int, str, int] | None:
    """(copies, direction, spacing), or None when cancelled."""
    form = array_form(parent)
    if i18n.exec_translated(form) != QDialog.DialogCode.Accepted:
        return None
    v = form.values()
    return int(v["copies"]), str(v["direction"]), int(v["spacing"])

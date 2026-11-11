"""Module-level helpers, constants and small widgets shared by the Inspector and its mixins."""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req

from typing import Any, cast
from collections.abc import Callable
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import (
    QWidget, QLayout, QVBoxLayout, QLabel, QHBoxLayout, QCheckBox, QPlainTextEdit,
)

# The distribution rules are `raptor.core.distro_rules`'s, not this
# module's (review D-04) -- they are pure model rules and belong in a
# Qt-free home. Imported here because this widget still uses both.
from raptor.core.fileio.typed import row_int


#: Raptor 7's `UnitsCombo` items, in its own order
#: The box was
#: editable, so these are presets rather than a closed list: Raptor 7
#: reads `unitsBox.Text()` and every label is built from it.
RAPTOR7_UNITS = (
    "seconds", "minutes", "hours", "kilo-hrs", "mega-hrs", "giga-hrs",
    "tera-hrs", "days", "months", "years", "cycles", "units", "miles",
    "kilometers", "PPM", "PPB",
)


#: Inspector Comment editor: type, pause this long, and the draft is
#: committed as one undo command. Focus-out commits immediately, so
#: clicking away never loses the tail of a sentence. This is what keeps
#: a typing burst (and its undo history) from becoming one command and
#: one full Inspector rebuild per character.
COMMENT_COMMIT_DELAY_MS = 400


class _CommentEdit(QPlainTextEdit):
    """A Comment box that holds a local draft instead of writing per key.

    The old box emitted on every `textChanged`; MainWindow pushed a
    `CommentCmd`, `model_changed` rebuilt the Inspector, and the focused
    editor (with its caret and scroll position) was deleted and recreated
    -- visibly jumping the pane to the bottom. The draft fixes that: the
    model is only touched on debounce/focus-out, and the commit is one
    command. `MainWindow` additionally suppresses the Inspector half of
    its deferred refresh for those self-originated commits, so even a
    debounced commit leaves this widget, its caret and the pane's scroll
    position in place.
    """

    def __init__(self, text: str, on_commit: Callable[[str], None],
                 parent=None):
        super().__init__(parent)
        self._on_commit = on_commit
        self._committed = str(text or "")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COMMENT_COMMIT_DELAY_MS)
        self._timer.timeout.connect(self.commit)
        self.setPlainText(self._committed)
        self.textChanged.connect(lambda: self._timer.start())
        # Tab leaves the Comment box (and its focus-out commits the
        # draft) instead of inserting a tab character into free text.
        self.setTabChangesFocus(True)

    def commit(self) -> None:
        """Flush the draft if it differs from the last committed text."""
        self._timer.stop()
        text = self.toPlainText()
        if text == self._committed:
            return
        self._committed = text
        self._on_commit(text)

    def set_source(self, text: str) -> None:
        """Adopt externally-changed text without disturbing a matching draft.

        Undo/redo of a comment command rebuilds this editor; if the text
        already matches the draft, leave the caret where it is. A real
        external change (undo) replaces the contents and puts the caret at
        the end, which is the least surprising place for a programmatic
        update.
        """
        text = str(text or "")
        if self.toPlainText() == text:
            self._committed = text
            self._timer.stop()
            return
        self._timer.stop()
        cursor = self.textCursor()
        pos = min(cursor.position(), len(text))
        self.setPlainText(text)
        cursor = self.textCursor()
        cursor.setPosition(pos)
        self.setTextCursor(cursor)
        self._committed = text

    def has_pending(self) -> bool:
        return self.toPlainText() != self._committed

    def focusOutEvent(self, event):
        self.commit()
        super().focusOutEvent(event)


_SIM_START = {
    # Port of Raptor 7's Input-Tables display code plus
    # the engine semantics in Raptor 7's `CASE simStartType`
    # 1 = completely random state; 2/3 start Up (random life
    # vs a fixed amount exhausted); 4/5/6 start Down (random point in the
    # repair cycle vs % complete vs repair just starting).
    1: "Random",
    2: "Start Up — Random life exhausted",
    3: "Start Up — Some life exhausted",
    4: "Start Failed — Random point in repair",
    5: "Start Failed — % repair complete",
    6: "Start Failed — Repair at start",
}


def _i_raw(row: dict, key: str, default: int = 0) -> int:
    """int-coerce a raw `.rbd` row cell, for columns RBDNode/RBDBlock
    don't carry.

    DELEGATED to `core.fileio.typed.row_int` (review E-12). This was a
    sixth copy of the same coercion -- not in the review's list of five --
    and its own docstring said *"same shape as typed.py's `_i`"* without
    noting that `typed._i` was importable. That is the setup for one copy
    gaining a fix the other misses.

    Note the guard differs slightly from the old code: this used to skip
    the float conversion only for `None`/`""` and fall back to `default`
    for anything else unparseable, which is what `row_int` does.
    """
    return row_int(row.get(key), default)


def _row_float(v) -> float:
    """float-coerce a raw `.rbd` row cell, never raising (blank -> 0)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _stream_label(v: int) -> str:
    """Random-stream display: streams 201..210 are the named system
    streams A..J everywhere in the legacy UI;
    anything else shows as its literal number."""
    if 201 <= v <= 210:
        return chr(ord("A") + v - 201)
    return str(v)


def parse_flow_cell(text: str, label: str,
                    n_in: int) -> tuple[int | None, str | None]:
    """Parse one Capacity-tab flow cell into the stored value.

    `label` is the sentinel keyword belonging to THIS column -- "MaxFlow"
    on Nominal Flow, "FullFlow" on Max Flow -- and the two map to the same
    stored sentinels (-1 for "all generated flow", -2 for "an even share
    across the node's n incoming paths"). The other column's keyword used
    to be accepted here as well, so typing it re-rendered as this column's
    keyword and looked as though the editor had picked the other one.

    Returns ``(value, error)``: a value of -2, -1 or >= 0, or None with a
    message to show when the text is not a flow at all (or is the wrong
    column's keyword).
    """
    raw = (text or "").strip()
    key = raw.upper().replace(" ", "")
    own, other = (("MAXFLOW", "FULLFLOW") if label == "MaxFlow"
                  else ("FULLFLOW", "MAXFLOW"))
    if key == own:
        return -1, None
    if key == f"{own}/{n_in}":
        return -2, None
    if key.startswith(own + "/"):
        return None, (f"write {label}/{n_in} for an even share, or a number")
    if key.startswith(other):
        return None, (f"{label} takes {label} or {label}/{n_in}; "
                      f"{other.title()} belongs to the other column")
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None, (f"enter a flow (0 or more), {label}, or "
                      f"{label}/{n_in}")
    if value < 0 and value not in (-1, -2):
        return None, (f"enter a flow (0 or more), {label}, or "
                      f"{label}/{n_in}")
    return value, None


class _WordWrapCheckBox(QWidget):
    """A checkbox whose label word-wraps (QCheckBox has no native
    wrap, and its unwrapped minimum width forces a narrow dock into a
    horizontal scrollbar). Indicator + wrapping QLabel side by side;
    clicking the label toggles like clicking the checkbox text would,
    so the interaction is unchanged."""

    toggled = Signal(bool)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._cb = QCheckBox()
        # The box has no text of its own (the wrapping label carries it),
        # so without this a screen reader announces an unlabelled checkbox.
        self._cb.setAccessibleName(text)
        self._lbl = QLabel(text)
        self._lbl.setWordWrap(True)
        self._lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._cb, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self._lbl, 1)
        self._cb.toggled.connect(self.toggled)
        self._cb.toggled.connect(
            lambda _on: self._lbl.setStyleSheet(""))
        # clicking the label toggles (QLabel eats clicks otherwise)
        cast(Any, self._lbl).mousePressEvent = self._label_clicked

    def _label_clicked(self, _event) -> None:
        self._cb.toggle()

    def isChecked(self) -> bool:
        return self._cb.isChecked()

    def setChecked(self, on: bool) -> None:
        self._cb.setChecked(on)

    def setToolTip(self, tip: str) -> None:
        super().setToolTip(tip)
        self._cb.setToolTip(tip)
        self._lbl.setToolTip(tip)


class _FormulaCmd(QUndoCommand):
    """Undoable change to one field's stored formula/rate RECORD
    (`RbdFile.formulas[addr]`). The numeric VALUE is restored by the
    editor's own value command; without this, Ctrl+Z restored the number
    but left the (now-lying) basis record behind."""

    def __init__(self, rbd, addr: str, old, new):
        super().__init__("Edit formula basis")
        self._rbd, self.addr = rbd, addr
        self.old, self.new = old, new

    def _apply(self, record):
        if record is None:
            self._rbd.formulas.pop(self.addr, None)
        else:
            self._rbd.formulas[self.addr] = dict(record)

    def redo(self):
        self._apply(self.new)

    def undo(self):
        self._apply(self.old)


#: Annotation kinds as the user sees them, matching the Annotate
#: ribbon's tool buttons rather than the .rbds `kind` strings.
_ANNO_KIND_LABELS = {
    "rect": "Rectangle", "ellipse": "Ellipse",
    "text": "Text", "arrow": "Arrow",
}

#: How the Inspector spells each stored anchor location. The stored values
#: live in `raptor.core.annotations.ANNO_LOCATIONS`; this is only display.
_ANCHOR_LOCATION_LABELS = {
    "right": "Right", "left": "Left", "above": "Above",
    "below": "Below", "center": "Centre",
}


def _vbox(layout: QLayout | None) -> QVBoxLayout:
    """The page layouts the inspector tabs build are all QVBoxLayouts; the stubs return `QLayout | None`."""
    return cast(QVBoxLayout, req(layout))

"""Refuse a canvas or dialog edit that breaks one of Raptor 7's input rules.

Raptor 7 refuses bad input where it is typed or drawn (its dialogs' OK
checks, `LinkObj.CheckValidLink`); the rules live in
`raptor.core.input_rules`, shared with the API and `validate()`. Every model
edit in the GUI is an undo command pushed on the view's stack, so the gate
sits there: `RuleGatedStack` wraps the view's `QUndoStack`, lets the command
run, and if the model now breaks a rule it did not break before, takes the
command back off and shows Raptor 7's message. The undone command is then
discarded (an obsolete command pushed on top deletes the redo tail), so Redo
cannot bring the refused edit back.

A macro (several commands as one undo step: a mass edit, a collapse) is
checked once, when it ends, and refused as a whole. Content copied in rather
than typed (Paste, Import as hierarchy) runs `ungated()`: Raptor 7's
`PasteObject` does not apply these rules either,
and a file's existing values are not edits.
"""
from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from PySide6.QtGui import QUndoCommand, QUndoStack

from raptor.core.input_rules import Violation, check, refusal_text


class _Discard(QUndoCommand):
    """Pushed after a refused edit is undone: it deletes the redo tail (any
    push does) and then itself (it is obsolete)."""

    def redo(self) -> None:
        self.setObsolete(True)


class RuleGatedStack:
    """The view's undo stack, with `push`/`endMacro` checked against Raptor 7's
    input rules. Every other attribute is the wrapped stack's."""

    def __init__(self, view: Any, stack: QUndoStack) -> None:
        self._view = view
        self._stack = stack
        self._depth = 0
        self._before: set | None = None
        self._off = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stack, name)

    @property
    def stack(self) -> QUndoStack:
        return self._stack

    @contextmanager
    def ungated(self) -> Iterator[None]:
        """Pushes inside are not checked (copied content, not typed input)."""
        self._off += 1
        try:
            yield
        finally:
            self._off -= 1

    # ---------------------------------------------------------------- gate
    def _current(self) -> list[Violation] | None:
        """The model's edit-blocking violations, read from a COPY of the rows
        with the typed model written into it: some edits reach the rows only on
        write-back, and writing back into the live rows would move things
        (positions are synced from the canvas) that only Save should touch."""
        view = self._view
        rbd = getattr(view, "_rbd_ref", None)
        editor = getattr(view, "_editor", None)
        if rbd is None or editor is None or self._off or getattr(view, "_loading", False):
            return None
        snapshot = copy.deepcopy(rbd)
        editor.write_back(snapshot, hier_ids=set(getattr(view, "_hier_items", {})))
        return [v for v in check(snapshot) if v.kind != "run"]

    def _keys(self) -> set | None:
        found = self._current()
        return None if found is None else {v.key for v in found}

    def _added(self, before: set | None) -> list[Violation]:
        if before is None:
            return []
        found = self._current() or []
        return [v for v in found if v.key not in before]

    def _refuse(self, added: list[Violation]) -> None:
        self._stack.undo()
        self._stack.push(_Discard("Refused edit"))
        from raptor.gui.ui import msgbox
        msgbox.warning(self._view, "Raptor 7 refuses this", refusal_text(added))

    # ---------------------------------------------------------------- stack API
    def push(self, cmd: QUndoCommand) -> None:
        # Layout-only commands skip Raptor 7's input-rule gate: no rule
        # reads geometry, and the check is two whole-model deep copies plus
        # two full rule walks. See `_ViewCmd.needs_rule_check`.
        if not getattr(cmd, "needs_rule_check", True):
            self._stack.push(cmd)
            return
        if self._depth:
            self._stack.push(cmd)
            return
        before = self._keys()
        self._stack.push(cmd)
        added = self._added(before)
        if added:
            self._refuse(added)

    def beginMacro(self, text: str) -> None:
        if self._depth == 0:
            self._before = self._keys()
        self._depth += 1
        self._stack.beginMacro(text)

    def endMacro(self) -> None:
        self._stack.endMacro()
        self._depth = max(0, self._depth - 1)
        if self._depth == 0:
            added = self._added(self._before)
            self._before = None
            if added:
                self._refuse(added)

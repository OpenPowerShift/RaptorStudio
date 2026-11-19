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

from raptor.core.input_rules import (
    Violation, check, check_link_ends, check_new_rows,
    hierarchy_depth_violations,
    refusal_text)


def _snapshot(rbd: Any) -> Any:
    """A copy of the model whose rows can be written without touching the
    original's.

    The rows are flat dicts of strings, which is nearly all of a model, so
    they are copied as such; `copy.deepcopy` walked every cell through its
    memo and took a second on a 5,000-block model, twice per edit. Anything
    that is not a row (the experiment, annotations, rates...) is small and
    is deep-copied as before."""
    from raptor.core.fileio.rbd import RbdSection
    snap = copy.copy(rbd)
    for name, value in vars(rbd).items():
        if name == "sections":
            continue
        if isinstance(value, (list, dict, set)):
            setattr(snap, name, copy.deepcopy(value))
    snap.sections = {
        key: RbdSection(
            sec.name, list(sec.columns),
            [{k: (v if isinstance(v, str) else copy.deepcopy(v))
              for k, v in row.items()} for row in sec.rows])
        for key, sec in rbd.sections.items()}
    return snap


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
        # The violations found after the last checked push, valid until the
        # stack moves again (a push, an undo, a redo). The next push's
        # "before" is that same model, so it need not be walked a second
        # time: the check is a whole-model copy and rule pass, and running
        # it twice per edit was most of an edit's cost on a large model.
        self._moves = 0
        self._known: tuple[int, set] | None = None
        self._found_keys: set | None = None
        stack.indexChanged.connect(self._on_stack_moved)
        #: Signals the steps of a macro left for the macro's end (see
        #: `_ViewCmd._done`); emitted once by `flush_signals`.
        view._pending_signals = []

    def _on_stack_moved(self, _index: int) -> None:
        self._moves += 1
        if not self._depth:
            self.flush_signals()

    def flush_signals(self) -> None:
        """Emit, once each, the view signals a macro's steps put off."""
        pending = getattr(self._view, "_pending_signals", None)
        if not pending:
            return
        names = list(pending)
        pending.clear()
        if getattr(self._view, "_loading", False):
            return
        for name in names:
            getattr(self._view, name).emit()

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
        if rbd is None or editor is None or getattr(view, "_loading", False):
            return None
        if self._off:
            # Copied-in content is not typed input, but it still may not
            # nest hierarchies deeper than can be opened. The rows hold the
            # hierarchy structure already, so no copy is needed.
            return hierarchy_depth_violations(rbd)
        snapshot = _snapshot(rbd)
        editor.write_back(snapshot, hier_ids=set(getattr(view, "_hier_items", {})))
        return [v for v in check(snapshot) if v.kind != "run"]

    def _keys(self) -> set | None:
        found = self._current()
        return None if found is None else {v.key for v in found}

    def _added(self, before: set | None) -> list[Violation]:
        """The violations the model has now that it did not have `before`.
        Leaves what it found in `_found_keys` for the caller to remember
        (None when the model could not be read)."""
        found = self._current()
        self._found_keys = (None if found is None or self._off
                            else {v.key for v in found})
        if before is None or found is None:
            return []
        return [v for v in found if v.key not in before]

    #: how many edits the gate has refused, for a caller to tell a refused
    #: step from one that simply made no undo step
    refusals = 0

    def _refuse(self, added: list[Violation]) -> None:
        self.refusals += 1
        self._stack.undo()
        self._stack.push(_Discard("Refused edit"))
        from raptor.gui.ui import msgbox
        msgbox.warning(self._view, "Not allowed", refusal_text(added, limit=5))

    # ---------------------------------------------------------------- stack API
    def push(self, cmd: QUndoCommand) -> None:
        if self._depth:
            # a step of a macro: its signal waits for the macro's end, on
            # the first run and on every later undo and redo of the macro
            cmd._in_macro = True  # type: ignore[attr-defined]
        # Layout-only commands skip Raptor 7's input-rule gate: no rule
        # reads geometry, and the check is two whole-model deep copies plus
        # two full rule walks. See `_ViewCmd.needs_rule_check`.
        if not getattr(cmd, "needs_rule_check", True):
            self._stack.push(cmd)
            return
        if self._depth:
            self._stack.push(cmd)
            return
        if self._push_new_rows(cmd):
            return
        known = self._known
        if known is not None and known[0] == self._moves and not self._off:
            before: set | None = known[1]
        else:
            before = self._keys()
        self._known = None
        self._stack.push(cmd)
        self._found_keys = None
        added = self._added(before)
        if added:
            self._refuse(added)
        elif self._found_keys is not None:
            self._known = (self._moves, self._found_keys)

    def _push_new_rows(self, cmd: QUndoCommand) -> bool:
        """New elements with no links (add, duplicate one block): check
        just their rows. A whole-model check per add made building a model
        one block at a time quadratic (800 adds: 30 s). Their rows hold
        what the canvas wrote back on insert. False when this does not
        apply and the whole-model gate must run."""
        view = self._view
        rbd = getattr(view, "_rbd_ref", None)
        if self._off or rbd is None or getattr(view, "_loading", False):
            return False
        known = self._known
        still_known = known is not None and known[0] == self._moves
        links_of = getattr(cmd, "new_links", None)
        links = links_of() if callable(links_of) else None
        if links is not None:
            return self._push_new_links(cmd, rbd, links, known if still_known else None)
        scope = getattr(cmd, "new_rows", None)
        found = scope() if callable(scope) else None
        if found is None:
            return False
        self._stack.push(cmd)
        section, rows = found
        added = [v for v in check_new_rows(rbd, section, rows)
                 if v.kind != "run"]
        if added:
            self._known = None
            self._refuse(added)
        elif still_known and known is not None:
            # Elements with no links add no finding elsewhere: what was
            # known before the add is still everything.
            self._known = (self._moves, known[1])
        return True

    def _push_new_links(self, cmd: QUndoCommand, rbd: Any, links: list[dict],
                        known: tuple[int, set] | None) -> bool:
        """A new link: compare the findings at its two ends before and
        after (`check_link_ends`), not the whole model's."""
        def keys(found: list[Violation]) -> set:
            return {v.key for v in found if v.kind != "run"}

        before = keys(check_link_ends(rbd, links, with_links=False))
        self._stack.push(cmd)
        after_found = [v for v in check_link_ends(rbd, links) if v.kind != "run"]
        added = [v for v in after_found if v.key not in before]
        if added:
            self._known = None
            self._refuse(added)
        elif known is not None:
            self._known = (self._moves,
                           (known[1] - before) | {v.key for v in after_found})
        else:
            self._known = None
        return True

    def beginMacro(self, text: str) -> None:
        if self._depth == 0:
            known = self._known
            if known is not None and known[0] == self._moves and not self._off:
                self._before = known[1]
            else:
                self._before = self._keys()
            self._known = None
        self._depth += 1
        self._stack.beginMacro(text)

    def endMacro(self) -> None:
        self._stack.endMacro()
        self._depth = max(0, self._depth - 1)
        if self._depth == 0:
            self.flush_signals()
            before, self._before = self._before, None
            self._found_keys = None
            added = self._added(before)
            if added:
                self._refuse(added)
            elif self._found_keys is not None:
                self._known = (self._moves, self._found_keys)

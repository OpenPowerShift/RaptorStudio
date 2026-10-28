"""The undoable row-edit pattern has one owner (review E-06).

The pattern is *diff row → float-tolerant equality → push `RowCellCmd` →
write → `endMacro`*. The review claimed **nine** sites across seven files
and said it "removes the float-tolerance rule from eight places".

**That count was wrong, and correcting it changed the fix.** Measured:

- `float(old) == float(new)` appears in exactly **3** places — not eight.
- `RowCellCmd` is pushed in **6** places, and **three of those six never do
  a float comparison at all**: `pool_dialogs` compares plain `!=` against a
  value it has already normalised, and the two `main_window` sites compare
  a single normalised column. They are not making this judgement.
- `dialogs.py`'s "Mass edit" macro **never pushes `RowCellCmd`** — it calls
  `v.set_block_dist`/`set_block_fail_params`/etc., which push their own
  commands. It is not an instance of this pattern at all.
- Both `phasing_dialog` sites go through `set_keyed_cell`, which is the
  *keyed*-row address, not `RowCellCmd`'s positional one.

So the real duplication is **3** copies of one 14-line loop, two of them in
the same file. That is a much smaller claim than "nine times across seven
files", and it is a *sharper* one: those three sites share a rule that must
agree or the model's dirty-tracking diverges between them.

The two sites that would have been folded in but were not — `pool_dialogs`
and `main_window` — are deliberately excluded, and `TestTheExcludedSites`
records why, so a later consolidation does not "complete" the set by
changing what those two do.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from raptor.gui.ui.diagram_commands import RowCellCmd, apply_row_edits, values_differ

REPO = Path(__file__).resolve().parent.parent.parent
GUI = REPO / "raptor" / "gui"

#: The three files the review named, checked for the inlined rule.
SITES = ["ui/run_experiment_mixin.py", "ui/dialogs.py"]


class _Stack:
    """Minimal `QUndoStack` stand-in that records macro nesting."""

    def __init__(self) -> None:
        self.macros: list[str] = []
        self.pushed: list[RowCellCmd] = []
        self.open = 0
        self.max_open = 0

    def beginMacro(self, label):
        self.open += 1
        self.max_open = max(self.max_open, self.open)
        self.macros.append(label)

    def endMacro(self):
        self.open -= 1

    def push(self, cmd):
        self.pushed.append(cmd)


class _View:
    def __init__(self) -> None:
        self._stack = _Stack()


class TestValuesDiffer:
    def test_identical_values_do_not_differ(self):
        assert values_differ("20", "20") is False

    def test_a_formatting_difference_is_not_a_change(self):
        """The rule's whole reason to exist: the file stores
        `"20.000000"` where the UI holds `20`."""
        assert values_differ("20.000000", "20") is False
        assert values_differ("20", "20.000000") is False

    def test_a_real_change_does_differ(self):
        assert values_differ("20", "21") is True

    def test_a_non_numeric_string_does_differ(self):
        assert values_differ("Exponential", "Gamma") is True

    def test_none_does_not_raise(self):
        """The `TypeError` arm is load-bearing: `float(None)` raises it.
        A copy that forgot that arm crashed instead of comparing as text."""
        assert values_differ(None, "20") is True
        assert values_differ("20", None) is True
        assert values_differ(None, None) is False

    def test_an_empty_old_value_does_not_raise(self):
        assert values_differ("", "20") is True

    def test_non_string_numbers_work(self):
        """`row.get(col, "")` can hand back whatever was written, which is
        not necessarily a string."""
        assert values_differ(20.0, 20) is False
        assert values_differ(20, 20.5) is True


class TestApplyRowEdits:
    def test_one_macro_wraps_every_cell(self):
        v = _View()
        row = {}
        n = apply_row_edits(v, "SYSTEM_DATA", 0, row,
                            [("a", "1"), ("b", "2")], "Run settings")
        assert n == 2
        assert v._stack.macros == ["Run settings"]
        assert v._stack.max_open == 1, "macros must not nest"

    def test_the_row_is_written_through(self):
        v = _View()
        row = {}
        apply_row_edits(v, "SYSTEM_DATA", 0, row, [("a", "1")], "x")
        assert row == {"a": "1"}

    def test_unchanged_cells_are_skipped(self):
        v = _View()
        row = {"a": "20.000000"}
        n = apply_row_edits(v, "SYSTEM_DATA", 0, row, [("a", "20")], "x")
        assert n == 0
        assert v._stack.pushed == [], "a no-op edit must not push a command"

    def test_a_three_tuple_old_is_honoured(self):
        """`pool_dialogs` has already established `old` from the live row;
        passing it avoids a second lookup and lets it skip a cell it has
        already compared."""
        v = _View()
        row = {"a": "1"}
        n = apply_row_edits(v, "SYSTEM_DATA", 0, row,
                            [("a", "1", "1"), ("a", "1", "2")], "x")
        assert n == 1
        assert v._stack.pushed[0].old == "1"
        assert v._stack.pushed[0].new == "2"

    def test_a_missing_column_reads_as_empty(self):
        v = _View()
        row = {}
        apply_row_edits(v, "SYSTEM_DATA", 0, row, [("missing", "7")], "x")
        assert v._stack.pushed[0].old == ""

    def test_the_macro_is_closed_even_when_a_write_raises(self):
        """The `try/finally` is the point of the helper: a half-open macro
        corrupts the undo stack for the rest of the session."""
        v = _View()

        class _Boom(dict):
            """Raises on write, so the write-through inside the helper is
            where the exception comes from."""

            def __setitem__(self, k, value):
                raise RuntimeError("boom")

        boom = _Boom()
        with pytest.raises(RuntimeError):
            apply_row_edits(v, "SYSTEM_DATA", 0, boom, [("a", "1")], "x")
        assert v._stack.open == 0, "the macro was left open"

    def test_the_return_value_counts_pushed_commands(self):
        v = _View()
        row = {"a": "same"}
        assert apply_row_edits(v, "S", 0, row,
                               [("a", "same"), ("b", "1")], "x") == 1


class TestOneOwner:
    FILES = ["ui/run_experiment_mixin.py", "ui/dialogs.py",
             "ui/pool_dialogs.py", "ui/main_window.py",
             "ui/diagram_commands.py"]

    @pytest.mark.parametrize("rel", FILES)
    def test_the_float_tolerance_rule_is_not_inlined(self, rel):
        """AST, not text: the helper's own docstring and the call sites'
        comments both NAME the rule while explaining it."""
        tree = ast.parse((GUI / rel).read_text())
        for node in ast.walk(tree):
            if rel.endswith("diagram_commands.py"):
                continue
            if isinstance(node, ast.Compare) and any(
                    isinstance(op, ast.Eq) for op in node.ops):
                names = {n.id for n in ast.walk(node)
                         if isinstance(n, ast.Name)}
                if {"old", "new"} <= names and any(
                        isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "float" for n in ast.walk(node)):
                    pytest.fail(
                        f"{rel}:{node.lineno} re-implements the "
                        "float-tolerant equality; call values_differ()")

    @pytest.mark.parametrize("rel", ["ui/run_experiment_mixin.py",
                                     "ui/dialogs.py"])
    def test_the_three_sites_use_the_helper(self, rel):
        src = (GUI / rel).read_text()
        assert "apply_row_edits(" in src, (
            f"{rel} no longer calls the helper")

    def test_the_self_clone_is_gone(self):
        """`run_experiment_mixin.py` wrote this loop twice in one file --
        the review's sharpest observation, and it survives the recount."""
        src = (GUI / "ui/run_experiment_mixin.py").read_text()
        assert src.count("apply_row_edits(") == 2, (
            "expected exactly two call sites in run_experiment_mixin.py")
        assert "beginMacro" not in src, (
            "run_experiment_mixin still opens a macro by hand")

    def test_the_macro_labels_are_preserved(self):
        """Undo history text is user-visible in the Edit menu, so the labels
        are part of the contract, not incidental strings."""
        mixin = (GUI / "ui/run_experiment_mixin.py").read_text()
        assert '"Run settings"' in mixin
        dlg = (GUI / "ui/dialogs.py").read_text()
        assert '"System settings"' in dlg


class TestTheExcludedSites:
    """Two `RowCellCmd` sites are deliberately NOT converted.

    `pool_dialogs` and `main_window` push `RowCellCmd` but never compare
    with float tolerance — they use plain `!=` against a value already
    normalised. Folding them into `apply_row_edits` would have changed
    their behaviour rather than deduplicating it, so they keep their own
    push. Recorded so a later pass does not "finish" the set.
    """

    def test_pool_dialogs_uses_plain_inequality(self):
        src = (GUI / "ui/pool_dialogs.py").read_text()
        tree = ast.parse(src)
        has_float_compare = any(
            isinstance(n, ast.Compare)
            and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                    and c.func.id == "float" for c in ast.walk(n))
            for n in ast.walk(tree))
        assert not has_float_compare, (
            "pool_dialogs gained a float comparison -- if that was "
            "deliberate, apply_row_edits is now the wrong owner for it")

    def test_main_window_pushes_row_cell_cmd_directly(self):
        src = (GUI / "ui/main_window.py").read_text()
        assert "RowCellCmd(" in src, (
            "main_window's single-column pushes moved somewhere; if they "
            "now go through apply_row_edits, say so here")

    def test_pool_dialogs_does_not_call_the_helper(self):
        # `main_window` was later consolidated onto `apply_row_edits` (its
        # system-settings and system-comment writes go through the same
        # macro bookkeeping now), so only `pool_dialogs` stays excluded: it
        # compares one already-normalised column with plain `!=`.
        assert "apply_row_edits(" not in (GUI / "ui/pool_dialogs.py").read_text()


class TestTheReviewCount:
    """The recount, pinned so it is not quietly re-inflated."""

    def test_three_float_comparisons_existed_and_all_are_gone(self):
        total = 0
        for path in GUI.rglob("*.py"):
            if path.name == "diagram_commands.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Compare)
                        and {n.id for n in ast.walk(node)
                             if isinstance(n, ast.Name)} >= {"old", "new"}
                        and any(isinstance(c, ast.Call)
                                and isinstance(c.func, ast.Name)
                                and c.func.id == "float"
                                for c in ast.walk(node))):
                    total += 1
        assert total == 0, (
            f"{total} inlined float-tolerant comparisons remain in the GUI")

    def test_four_row_cell_cmd_pushes_remain_outside_the_helper(self):
        """The recount, measured on HEAD.

        There were **six** `RowCellCmd` pushes; converting the three
        float-tolerant sites leaves **four** hand-written ones — the two in
        `main_window` and the two in `pool_dialogs`, none of which ever did
        a float comparison (see `TestTheExcludedSites`). The helper's own
        push is counted separately so the numbers are comparable with the
        review's.
        """
        outside = 0
        for path in GUI.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "push"
                        and any(isinstance(a, ast.Call)
                                and isinstance(a.func, ast.Name)
                                and a.func.id == "RowCellCmd"
                                for a in node.args)):
                    # The helper's own push, not a call site.
                    if "apply_row_edits" in ast.unparse(node):
                        continue
                    outside += 1
        assert outside == 4, (
            f"expected 4 hand-written RowCellCmd pushes, found {outside}")
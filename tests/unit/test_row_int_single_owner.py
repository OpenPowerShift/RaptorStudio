"""Row int coercion has one implementation (review E-12).

Six copies of "coerce this raw cell to an int" existed. Five were in the
review's list; the sixth — `inspector._i_raw` — was found while fixing them
and had its own docstring admitting *"same shape as typed.py's `_i`"*
without noting that `typed._i` was importable. That is the setup for one
copy gaining a fix the other misses.

The copies disagreed on the **sentinel**, and that is not cosmetic:

| Sentinel | Meaning | Where |
|---|---|---|
| `-1` | "no element has this id" | `diagram_common`, `hierarchy_plan`, `clipboard_plan` |
| `0` | "unset" — and ID 0 is *meaningful* | `phasing_dialog` |
| `None` | "absent", distinguishable from a real 0 | `model_edit` |

`phasing_dialog` is the one that would break quietly: it does
`oid = s(r.get("ID")); if oid == 0: continue`, because ID 0 is the file's
injected template row. So an unreadable ID reading as 0 makes it skip
cleanly. Switching it to the other copies' `-1` would put a bogus phase
entry on the template row instead.

So the consolidation keeps `row_int(value, default)` with the sentinel
**stated at each call site**, and adds `int_or_none` for the case a
sentinel cannot express.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from raptor.core.fileio.typed import int_or_none, row_int

REPO = Path(__file__).resolve().parent.parent.parent

#: The modules that used to carry their own copy, and the sentinel each one
#: meant. If a module drops off this list WITHOUT the change being reverted,
#: its sentinel decision was lost rather than consolidated.
CONSOLIDATED = {
    "raptor/gui/ui/diagram_common.py": "-1",
    "raptor/gui/hierarchy_plan.py": "-1",
    "raptor/gui/clipboard_plan.py": "-1",
    "raptor/gui/model_edit.py": "None",
    "raptor/gui/ui/phasing_dialog.py": "0",
    "raptor/gui/ui/inspector.py": "0",   # _i_raw's own default
}


class TestRowInt:
    def test_a_float_cell_reads_as_an_int(self):
        """The whole reason this is not `int()`: the legacy writer emits
        six-decimal floats for integer columns, so `int("3.0")` would
        raise on half the corpus."""
        assert row_int("3.0") == 3
        assert row_int("3.000000") == 3
        assert row_int("-2.000000") == -2

    def test_an_int_passes_through(self):
        assert row_int(7) == 7
        assert row_int("7") == 7

    @pytest.mark.parametrize("bad", ["", "junk", None, "1.2.3", [], {}])
    def test_unreadable_takes_the_default(self, bad):
        assert row_int(bad, 0) == 0
        assert row_int(bad, -1) == -1

    def test_the_default_is_minus_one(self):
        """Documented, not incidental: -1 is the majority reading ("no
        element has this id") and the safer of the two numeric options for
        key building."""
        assert row_int("junk") == -1

    def test_zero_is_a_real_value_not_a_failure(self):
        assert row_int("0", -1) == 0
        assert row_int("0.000000", -1) == 0


class TestIntOrNone:
    def test_it_distinguishes_absent_from_zero(self):
        """Why it exists: a numeric sentinel cannot express this, which is
        why `model_edit` had its own copy in the first place."""
        assert int_or_none("junk") is None
        assert int_or_none("") is None
        assert int_or_none(None) is None
        assert int_or_none("0") == 0
        assert int_or_none("3.0") == 3


class TestTheSixthCopyIsGone:
    def test_inspector_i_raw_delegates(self):
        from raptor.gui.ui import inspector
        src = __import__("inspect").getsource(inspector._i_raw)
        assert "row_int" in src, (
            "inspector._i_raw is still its own coercion -- it was NOT in "
            "the review's list of five, and its docstring used to admit it "
            "duplicated typed._i")

    def test_nobody_defines_a_second_coercion(self):
        """Structural: AST-based, since these functions may legitimately
        mention the coercion in prose."""
        for rel in ("raptor/gui/model_edit.py",
                    "raptor/gui/hierarchy_plan.py",
                    "raptor/gui/clipboard_plan.py",
                    "raptor/gui/ui/diagram_common.py",
                    "raptor/gui/ui/inspector.py"):
            tree = ast.parse((REPO / rel).read_text())
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if fn.name not in ("_safe_int", "_int", "_i_raw", "s"):
                    continue
                body = ast.Module(body=fn.body, type_ignores=[])
                calls_int_float = any(
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id == "float"
                    for n in ast.walk(body))
                if calls_int_float:
                    pytest.fail(
                        f"{rel}:{fn.lineno} {fn.name}() re-implements the "
                        "coercion; call core.fileio.typed.row_int")


class TestEachSentinelSurvived:
    """The consolidation must not have quietly unified the sentinels --
    that is the one change here that could break behaviour."""

    @pytest.mark.parametrize("rel,sentinel", sorted(CONSOLIDATED.items()))
    def test_the_module_still_states_its_choice(self, rel, sentinel):
        src = (REPO / rel).read_text()
        if sentinel == "None":
            assert "int_or_none" in src, (
                f"{rel} must keep the None sentinel, via int_or_none")
        elif sentinel == "0":
            assert "row_int(" in src and ", 0)" in src, (
                f"{rel} must keep the 0 sentinel -- it is load-bearing "
                "there (ID 0 is the injected template row)")
        else:
            assert "row_int(" in src and "-1)" in src, (
                f"{rel} must keep the -1 sentinel")

    def test_phasing_dialog_still_skips_the_template_row(self):
        """The behavioural statement of the `0` sentinel.

        If this copy were switched to -1, the `if oid == 0: continue` below
        would stop matching and a phase entry would be offered for the
        file's injected template row.
        """
        src = (REPO / "raptor/gui/ui/phasing_dialog.py").read_text()
        assert "if oid == 0:" in src, (
            "the template-row skip is gone; verify what 0 means here now")
        assert "row_int(x, 0)" in src

    def test_the_lookup_modules_still_reject_garbage_ids(self):
        """The `-1` sentinel's job: a garbage id must not collide with a
        real one."""
        for rel in ("raptor/gui/hierarchy_plan.py",
                    "raptor/gui/clipboard_plan.py",
                    "raptor/gui/ui/diagram_common.py"):
            src = (REPO / rel).read_text()
            assert "row_int(" in src
            assert "-1" in src.split("row_int(")[1][:60], (
                f"{rel} passes something other than -1 as the sentinel")


class TestBehavesLikeTheOriginals:
    """End-to-end: the shared function agrees with what each copy did."""

    def test_typed_i_still_defaults_to_zero(self):
        """`_i`'s signature default is 0, not row_int's -1. Not a change
        of behaviour -- the default is passed explicitly."""
        from raptor.core.fileio.typed import _i
        assert _i({}, "missing") == 0
        assert _i({"ID": "3.0"}, "ID") == 3

    def test_a_real_row_still_reads(self):
        from raptor.core.fileio.rbd import load_rbd
        from raptor.core.fileio.typed import block_from_row
        rbd = load_rbd(str(REPO / "tests" / "golden" / "TwoBlockRedundancy"
                           / "input.rbd"))
        row = next(r for r in rbd.sections["BLOCK_DATA"].rows
                   if r.get("ID") not in (None, "", "0"))
        block = block_from_row(row)
        assert block.id == int(float(row["ID"]))

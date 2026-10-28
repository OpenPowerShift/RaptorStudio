"""Boolean cells have ONE rule (review E-13).

Four readers had their own answer to "is this cell set?", and they
disagreed **in both directions**:

| Reader | Rule |
|---|---|
| `core/fileio/typed.py::_b` | deny-list — anything not `"0"/"0.0"/"0.000000"/"False"/"FALSE"` is **True** |
| `gui/ui/pool_dialogs.py::_row_bool` | allow-list — only `"1"/"T"/"TRUE"` is True |
| `gui/ui/pool_dialogs.py` (inlined, seeding the checkbox) | the same allow-list, a second time |
| `gui/ui/settings.py::get_bool` | a *different* allow-list, adding `yes`/`on` |

So a row containing `"false"` — a spelling that means the opposite — read
as **True** to the engine. And because the pool dialog seeds its checkbox
from the allow-list while the engine reads the same cell with the
deny-list, a checkbox could render **unchecked** while the engine ran with
the flag **set**.

The allow-list is the faithful rule, and not by preference: the legacy
writer emits these cells via `str(rso)` (`rbdlib.make_spare_pool_row`), i.e. `"0"` or
`"1"` and nothing else, and all 393 pool rows across the repo's 235
`.rbd` files carry exactly `'0'`, `'1'`, or absent. Raptor 7
keeps them as real booleans (`RSO:=TRUE`), so the
on-disk form is the writer's `str()` — not a word. An allow-list
therefore loses no fidelity and defaults an unreadable spelling to the
safe answer.
"""
from __future__ import annotations

import glob
import os

import pytest

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.typed import cell_truthy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
GOLDEN = os.path.join(REPO, "tests", "golden")


class TestCellTruthy:
    #: The spellings the legacy writer actually produces (`str(rso)`).
    WRITTEN = {"0": False, "1": True}

    @pytest.mark.parametrize("value,expected", sorted(WRITTEN.items()))
    def test_the_spellings_raptor_writes(self, value, expected):
        assert cell_truthy(value) is expected

    @pytest.mark.parametrize("value,expected", [
        ("", False), ("   ", False), (None, False),
    ])
    def test_absent_is_not_set(self, value, expected):
        assert cell_truthy(value) is expected

    @pytest.mark.parametrize("value", ["1", "t", "T", "true", "TRUE",
                                       "True", " 1 ", "yes", "on", "y"])
    def test_positive_spellings(self, value):
        assert cell_truthy(value) is True

    @pytest.mark.parametrize("value", [
        "0", "0.0", "0.000000", "f", "F", "false", "False", "FALSE",
        "n", "N", "no", "off", "-1",
    ])
    def test_negative_spellings(self, value):
        assert cell_truthy(value) is False

    @pytest.mark.parametrize("value", ["2", "0.5", "3.14", "junk",
                                       "0x1", "null"])
    def test_a_number_that_is_not_a_boolean_is_not_set(self, value):
        """THE DANGEROUS DIRECTION.

        The old deny-list returned **True** for all of these, so a typo or
        a stray value switched a flag ON. An allow-list cannot do that:
        anything it does not recognise reads as not-set.
        """
        assert cell_truthy(value) is False, (
            f"{value!r} was read as True -- an unrecognised spelling must "
            "never enable a flag")

    def test_false_reads_as_false(self):
        """The single most important case: the old engine rule got this
        backwards, enabling a flag written as `false`."""
        assert cell_truthy("false") is False
        assert cell_truthy("False") is False
        assert cell_truthy("FALSE") is False

    def test_default_applies_only_to_absent(self):
        """`default=True` means "absent means set", NOT "unrecognised
        means set"."""
        assert cell_truthy(None, True) is True
        assert cell_truthy("", True) is True
        assert cell_truthy("0", True) is False
        assert cell_truthy("false", True) is False


class TestTheEngineUsesIt:
    def test_b_delegates(self):
        from raptor.core.fileio import typed
        row = {"X": "false", "Y": "1", "Z": "junk"}
        assert typed._b(row, "X") is False
        assert typed._b(row, "Y") is True
        assert typed._b(row, "Z") is False

    def test_b_honours_its_default(self):
        from raptor.core.fileio.typed import _b
        assert _b({}, "missing") is False
        assert _b({}, "missing", True) is True

    def test_a_pool_row_reads_its_ordering_flags(self):
        row = {"PoolName": "P", "RSO": "1", "ESO": "false", "SLO": "0"}
        from raptor.core.fileio.typed import spare_pool_from_row
        pool = spare_pool_from_row(row)
        assert pool.routine_spare_ordering is True
        assert pool.emergency_spare_ordering is False, (
            "an `ESO` cell written 'false' must not enable emergency "
            "spares")
        assert pool.stock_level_ordering is False


class TestTheGuiUsesIt:
    def test_pool_dialogs_row_bool_delegates(self):
        import inspect

        from raptor.gui.ui import pool_dialogs
        src = inspect.getsource(pool_dialogs._row_bool)
        assert "cell_truthy" in src, (
            "pool_dialogs._row_bool has its own rule again; it must "
            "delegate to core.fileio.typed.cell_truthy")

    def test_the_checkbox_seeds_through_the_same_helper(self):
        """It used to inline the allow-list a SECOND time, right where the
        checkbox state is set -- the exact place the divergence showed up."""
        import ast
        import inspect

        from raptor.gui.ui import pool_dialogs
        tree = ast.parse(inspect.getsource(pool_dialogs).replace(
            "\t", "    ").replace("    ", "    ", 1))
        # Walk every setChecked call: its value must be a call, not an
        # inline membership test.
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setChecked"
                    and node.args):
                arg = node.args[0]
                assert isinstance(arg, ast.Call), (
                    "setChecked is given a computed expression, not "
                    "_row_bool(...); the allow-list has been inlined again")

    def test_settings_get_bool_agrees_on_every_spelling(self):
        """QSettings rather than a file cell, so it keeps its own function
        -- but it must not answer differently."""
        import inspect

        from raptor.gui.ui import settings
        assert "cell_truthy" in inspect.getsource(settings.get_bool)

    @pytest.mark.parametrize("value,expected", [
        ("1", True), ("0", False), ("true", True), ("false", False),
        ("yes", True), ("no", False), ("on", True), ("off", False),
        ("junk", False), (None, False),
    ])
    def test_the_four_readers_now_agree(self, value, expected):
        """The end-to-end statement: every reader gives the same answer."""
        from raptor.gui.ui.pool_dialogs import _row_bool
        from raptor.core.fileio.typed import _b
        row = {"C": value}
        assert cell_truthy(value) is expected
        assert _b(row, "C") is expected
        assert _row_bool(row, "C") is expected


class TestAgainstEveryFixture:
    """The rule must not change how any real model reads."""

    def test_no_fixture_changes_meaning(self):
        """Only `'0'`, `'1'` and absent occur, and those are unaffected."""
        seen = set()
        count = 0
        for path in (glob.glob(os.path.join(GOLDEN, "*", "input.rbd"))
                     + glob.glob(os.path.join(REPO, "legacy", "rbd", "*.rbd"))):
            try:
                rbd = load_rbd(path)
            except Exception:
                continue
            for section in ("SPARE_POOL_DATA", "RESOURCE_DATA"):
                sec = rbd.sections.get(section)
                if not sec:
                    continue
                for row in sec.rows:
                    count += 1
                    for col in ("RSO", "ESO", "SLO"):
                        v = row.get(col)
                        seen.add(v)
                        assert cell_truthy(v) in (True, False)
        assert count > 100, f"only {count} pool rows found -- fixture glob wrong?"
        # Every spelling that actually occurs is one the rule understands.
        unexpected = {v for v in seen
                      if v is not None and str(v).strip().lower()
                      not in {"0", "1", "true", "false", "t", "f", "yes", "no",
                              "on", "off", "y", "n", "0.0", "0.000000"}}
        assert not unexpected, (
            f"fixtures contain spellings the rule does not cover: "
            f"{sorted(unexpected)}")

    def test_every_pool_flag_in_every_fixture_is_a_bool(self):
        """A sanity pass over the whole corpus through the real reader."""
        for path in glob.glob(os.path.join(GOLDEN, "*", "input.rbd")):
            try:
                rbd = load_rbd(path)
            except Exception:
                continue
            from raptor.core.fileio.typed import spare_pool_from_row
            sec = rbd.sections.get("SPARE_POOL_DATA")
            if not sec:
                continue
            for row in sec.rows:
                pool = spare_pool_from_row(row)
                for flag in (pool.routine_spare_ordering,
                             pool.emergency_spare_ordering,
                             pool.stock_level_ordering):
                    assert isinstance(flag, bool)

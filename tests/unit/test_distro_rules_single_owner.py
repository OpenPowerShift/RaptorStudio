"""Distribution parameter rules have ONE owner (review D-04).

`DISTRO_PARAM_LABELS` and `validate_distro_params` were ported twice:
once into `gui/ui/inspector.py` and once into `api/validate.py`. On
inspection the two were **byte-identical in code** and differed only in
comments — and the API's copy had already lost the NaN guard's rationale,
the Chi-square/Erlang integer-shape note, and the Triangular
legacy-comparison note.

Byte-identical is worse than divergent: a clone detector sees nothing, and
a reviewer comparing behaviour sees nothing either. What drifted was the
*documentation of why the rules are what they are*, which is the part a
maintainer needs most when changing one.

Worse, the GUI copy was the de facto shared table: `inspector.py`,
`data_tables.py`, `distro_editor.py`, `dialogs.py` and `phasing_dialog.py`
all reached into the widget module by private name, so a validation rule
lived in a widget and five other modules depended on it there.

Now: `raptor/core/distro_rules.py` owns both, `api/validate.py` and the
GUI import it, and no module imports the rules from a widget.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from raptor.core.distro_rules import DISTRO_PARAM_LABELS, validate_distro_params

REPO = Path(__file__).resolve().parent.parent.parent


def _defines(path: Path, name: str) -> bool:
    """Does *path* DEFINE *name* (as opposed to importing it)?"""
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return True
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) \
                    and node.target.id == name:
                return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return True
    return False


def _imports_from_widget(path: Path, name: str) -> bool:
    """Does *path* import *name* from any `gui.ui.*` module?"""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module \
                and node.module.startswith("raptor.gui"):
            if any(a.name == name for a in node.names):
                return True
    return False


class TestNoSecondCopy:
    """The structural guard. This is the half of D-04 that no behavioural
    test can catch, because identical copies behave identically."""

    @pytest.mark.parametrize("path", [
        "raptor/api/validate.py",
        "raptor/gui/ui/inspector.py",
        "raptor/gui/ui/data_tables.py",
        "raptor/gui/ui/distro_editor.py",
        "raptor/gui/ui/dialogs.py",
        "raptor/gui/ui/phasing_dialog.py",
        "raptor/gui/ui/preflight.py",
    ])
    def test_nobody_defines_its_own_labels_table(self, path):
        f = REPO / path
        assert not _defines(f, "DISTRO_PARAM_LABELS"), (
            f"{path} defines its own DISTRO_PARAM_LABELS; the rules have "
            "one owner in raptor/core/distro_rules.py")

    @pytest.mark.parametrize("path", [
        "raptor/api/validate.py",
        "raptor/gui/ui/inspector.py",
        "raptor/gui/ui/data_tables.py",
        "raptor/gui/ui/distro_editor.py",
        "raptor/gui/ui/dialogs.py",
        "raptor/gui/ui/phasing_dialog.py",
        "raptor/gui/ui/preflight.py",
    ])
    def test_nobody_defines_its_own_validator(self, path):
        f = REPO / path
        assert not _defines(f, "validate_distro_params"), (
            f"{path} defines its own validate_distro_params; the rules "
            "have one owner in raptor/core/distro_rules.py")

    @pytest.mark.parametrize("name", [
        "DISTRO_PARAM_LABELS", "validate_distro_params",
    ])
    def test_nobody_imports_the_rules_from_a_widget(self, name):
        """The GUI used to reach into `inspector` for a model rule, so a
        validation rule lived in a widget with five dependents."""
        offenders = []
        for path in (REPO / "raptor").rglob("*.py"):
            if path.name == "inspector.py":
                continue
            if _imports_from_widget(path, name):
                offenders.append(str(path.relative_to(REPO)))
        assert not offenders, (
            f"{name} is imported from a GUI widget module by {offenders}; "
            "import it from raptor.core.distro_rules instead")

    def test_the_owner_is_free_of_qt_and_of_the_gui(self):
        """A 'Qt-free home' that imports Qt is not one. This is the
        failure that produced §2.6 in the first place."""
        src = (REPO / "raptor" / "core" / "distro_rules.py").read_text()
        for banned in ("PySide6", "raptor.gui", "QtCore", "QtWidgets"):
            assert banned not in src, (
                f"core/distro_rules.py references {banned}; the rules are "
                "pure model rules and must stay importable from headless "
                "contexts (the API, MCP and the engine all need them)")


class TestTheRulesStillBehave:
    """Promoting a function must not change what it does. These pin the
    behaviour the two doors depend on."""

    @pytest.mark.parametrize("distro", sorted(DISTRO_PARAM_LABELS))
    def test_every_distribution_has_a_three_slot_label_tuple(self, distro):
        labels = DISTRO_PARAM_LABELS[distro]
        assert len(labels) == 3, distro

    def test_the_label_table_covers_exactly_the_22_distributions(self):
        assert set(DISTRO_PARAM_LABELS) == set(range(1, 23))

    def test_three_parameter_families_are_labelled(self):
        """Distributions whose slots the legacy dialog actually shows."""
        for distro in (6, 11, 12, 14, 15):
            assert all(lbl for lbl in DISTRO_PARAM_LABELS[distro]), distro

    def test_hidden_slots_are_the_empty_string(self):
        """The empty string marks a slot the dialog hides -- not None and
        not a placeholder, because `validate_distro_params` keys off it."""
        assert DISTRO_PARAM_LABELS[16] == ("", "", "")
        assert DISTRO_PARAM_LABELS[18] == ("", "", "")

    def test_a_valid_shape_returns_none(self):
        assert validate_distro_params(1, [2.0, 5.0, 0.0], "F") is None

    def test_an_out_of_range_shape_is_reported(self):
        assert validate_distro_params(1, [0.0, 5.0, 0.0], "F") is not None

    def test_nan_is_rejected(self):
        """The guard the API's copy had, without the comment explaining
        why: every comparison against NaN is False, so NaN would otherwise
        pass every rule and reach the engine."""
        assert validate_distro_params(1, [float("nan"), 5.0, 0.0], "F") \
            is not None
        assert validate_distro_params(1, [float("inf"), 5.0, 0.0], "F") \
            is not None

    def test_triangular_ordering_is_enforced(self):
        assert validate_distro_params(14, [5.0, 1.0, 9.0], "F") is not None
        assert validate_distro_params(14, [1.0, 5.0, 9.0], "F") is None

    def test_uniform_min_must_not_exceed_max(self):
        assert validate_distro_params(10, [9.0, 1.0, 0.0], "F") is not None

    def test_chi_square_demands_an_integer_shape(self):
        """Its CASE branch runs on top of the generic shape rule."""
        assert validate_distro_params(2, [2.5, 0.0, 0.0], "F") is not None
        assert validate_distro_params(2, [2.0, 0.0, 0.0], "F") is None

    def test_the_error_message_names_the_use(self):
        msg = validate_distro_params(1, [0.0, 5.0, 0.0], "Repair")
        assert msg and "Repair" in msg


class TestBothDoorsGetTheSameRule:
    """The whole point: one function, so the two doors cannot disagree."""

    def test_the_api_imports_the_canonical_function(self):
        from raptor.api import validate
        from raptor.core import distro_rules
        assert validate.validate_distro_params \
            is distro_rules.validate_distro_params

    def test_the_inspector_imports_the_canonical_function(self):
        from raptor.core import distro_rules
        from raptor.gui.ui import inspector
        assert inspector.validate_distro_params \
            is distro_rules.validate_distro_params

    def test_the_two_doors_agree_on_every_distribution(self):
        """A cross-check that would have caught the original drift, and
        that the old "three golden fixtures" claim could not support."""
        from raptor.api.validate import validate_distro_params as api_v
        from raptor.gui.ui.inspector import (
            validate_distro_params as gui_v,
        )
        import itertools
        for distro in sorted(DISTRO_PARAM_LABELS):
            for values in itertools.product(
                    (0.0, 1.0, -1.0, 2.5, 1e9), repeat=2):
                for full in (list(values) + [0.0],):
                    assert api_v(distro, full, "F") == gui_v(distro, full, "F"), \
                        (distro, full)

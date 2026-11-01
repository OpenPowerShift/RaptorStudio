"""Annotation defaults and z bands have one owner (review E-07).

`ANNO_DEFAULTS` (9 keys) and `ANNO_Z_MIDDLE` were each written twice — in
`api/annotations.py` and in `gui/ui/diagram_annotations.py` — with the same
values. The API's copy carried the comment *"kept here rather than imported
so the API layer never needs Qt"*: a **sound reason and the wrong
mechanism**. `core/` needs no Qt, so moving the table there satisfies both.

The API's cross-reference was also **stale**: it pointed at
`raptor/gui/ui/diagram.py`, but the GUI copy had moved to
`diagram_annotations.py`. That is the specific hazard of duplicating a
constant *together with* a cross-reference — the reference rots
independently of the value, and nothing notices for as long as the two
still agree.

`ANNO_Z_MIDDLE` was also the int `20` on the API side and the float `20.0`
on the GUI side: the same value, two types. Harmless for the float
comparisons the canvas does, and exactly the kind of difference that makes
`is` fail while `==` succeeds.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from raptor.core.annotations import (
    ANNO_DEFAULT_LOCATION,
    ANNO_DEFAULTS,
    ANNO_Z_BANDS,
    ANNO_Z_BEHIND,
    ANNO_Z_DEFAULT,
    ANNO_Z_FRONT,
    ANNO_Z_MIDDLE,
)

REPO = Path(__file__).resolve().parent.parent.parent

#: The nine keys the format defines. Pinned rather than derived from the
#: dict, because a key silently dropped from the table would otherwise be
#: invisible: `_ANNO_STYLE_KEYS` is built FROM this dict, so it would shrink
#: in agreement with itself.
EXPECTED_KEYS = {
    "border_colour", "border_width", "fill_colour", "text_colour",
    "font_family", "font_size", "font_bold", "font_italic", "line_style",
}


class TestTheTable:
    def test_it_has_exactly_the_nine_documented_keys(self):
        assert set(ANNO_DEFAULTS) == EXPECTED_KEYS

    def test_the_values_are_the_ones_the_format_documents(self):
        """Recorded explicitly. `docs/RBDS_FORMAT.md` is the third copy of
        this table, in prose, and it cannot be checked by an import."""
        assert ANNO_DEFAULTS == {
            "border_colour": "#5D7080",
            "border_width": 1,
            "fill_colour": None,        # None = transparent
            "text_colour": "#132238",
            "font_family": "sans-serif",
            "font_size": 10,
            "font_bold": False,
            "font_italic": False,
            "line_style": "solid",
        }

    def test_a_new_annotation_has_a_visible_border(self):
        """`border_width` 1, not 0.

        0 is the user's own explicit "off" value, so defaulting to it makes
        a freshly placed note invisible until its fill or text makes it
        noticeable. This is a *behavioural* reason for the number, not a
        preference."""
        assert ANNO_DEFAULTS["border_width"] == 1

    def test_the_default_fill_is_transparent(self):
        assert ANNO_DEFAULTS["fill_colour"] is None


class TestTheZbands:
    def test_they_are_ordered_back_to_front(self):
        assert ANNO_Z_BANDS == (ANNO_Z_BEHIND, ANNO_Z_MIDDLE, ANNO_Z_FRONT)
        assert ANNO_Z_BEHIND < ANNO_Z_MIDDLE < ANNO_Z_FRONT

    def test_behind_clears_the_grid(self):
        """The grid is at -100. If `ANNO_Z_BEHIND` did not clear it, the
        background would paint over a note."""
        assert ANNO_Z_BEHIND > -100

    def test_front_clears_the_placement_ghosts(self):
        """Rubber-band ghosts sit at 15."""
        assert ANNO_Z_FRONT > 15

    def test_behind_clears_the_reliability_elements(self):
        """Link items and hierarchy ovals sit at -1, blocks/nodes/events at
        0 (10 when selected), so at-or-below -1 is behind all of them."""
        assert ANNO_Z_BEHIND <= -1

    def test_middle_is_the_callout_band(self):
        assert ANNO_Z_MIDDLE == 20.0

    def test_a_new_annotation_is_behind_by_default(self):
        assert ANNO_Z_DEFAULT == ANNO_Z_BEHIND

    def test_a_new_annotation_is_above_its_target_by_default(self):
        assert ANNO_DEFAULT_LOCATION == "above"

    def test_they_are_floats_consistently(self):
        """The API's copy was the int `20` where the GUI's was `20.0`.

        Same value, two types — harmless for the canvas's float comparisons
        and exactly the difference that makes `is` fail while `==` succeeds.
        """
        for z in ANNO_Z_BANDS:
            assert isinstance(z, float), (
                f"z band {z!r} is a {type(z).__name__}; a mix of int and "
                "float here is how the two copies drifted apart")


class TestOneOwner:
    """Structural. Two identical dicts are indistinguishable from one by
    behaviour, which is how this survived — and the values DID agree."""

    FILES = ["raptor/api/annotations.py",
             "raptor/gui/ui/diagram_annotations.py"]

    def test_the_api_imports_rather_than_defines(self):
        from raptor.api import annotations
        assert annotations.ANNO_DEFAULTS is ANNO_DEFAULTS, (
            "the API still carries its own ANNO_DEFAULTS")
        assert annotations.ANNO_Z_MIDDLE is ANNO_Z_MIDDLE
        assert annotations.ANNO_Z_DEFAULT is ANNO_Z_DEFAULT
        assert annotations.ANNO_DEFAULT_LOCATION is ANNO_DEFAULT_LOCATION

    def test_the_gui_imports_rather_than_defines(self):
        pytest.importorskip("PySide6")
        from raptor.gui.ui import diagram_annotations
        assert diagram_annotations.ANNO_DEFAULTS is ANNO_DEFAULTS
        assert diagram_annotations.ANNO_Z_MIDDLE is ANNO_Z_MIDDLE
        assert diagram_annotations.ANNO_Z_DEFAULT is ANNO_Z_DEFAULT

    @pytest.mark.parametrize("rel", FILES)
    def test_nobody_builds_the_dict_again(self, rel):
        """AST, not text: both modules legitimately *name* the constants
        when explaining where they came from."""
        tree = ast.parse((REPO / rel).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id in ("ANNO_DEFAULTS", "ANNO_Z_MIDDLE",
                                 "ANNO_Z_BEHIND", "ANNO_Z_FRONT",
                                 "ANNO_Z_DEFAULT",
                                 "ANNO_DEFAULT_LOCATION"):
                    pytest.fail(
                        f"{rel}:{node.lineno} assigns {target.id}; it lives "
                        "in raptor.core.annotations")

    def test_the_owner_needs_no_qt(self):
        """The reason the API could not import before was Qt. If the new
        home has grown a Qt import, it has reproduced the problem it was
        created to solve."""
        src = (REPO / "raptor" / "core" / "annotations.py").read_text()
        for banned in ("PySide6", "raptor.gui"):
            assert banned not in src, (
                f"core/annotations.py references {banned}; annotation "
                "defaults are a format concern, and the API must be able to "
                "read them headless")

    def test_no_stale_cross_reference_remains(self):
        """The specific rot the finding recorded: the API pointed at
        `gui/ui/diagram.py` after the copy had moved."""
        src = (REPO / "raptor" / "api" / "annotations.py").read_text()
        assert "ANNO_DEFAULTS`` (``raptor/gui/ui/diagram.py``)" not in src
        # And the file it actually names must be the one that had the copy.
        assert (REPO / "raptor" / "gui" / "ui" / "diagram_annotations.py").exists()


class TestTheDerivedKeySet:
    def test_style_keys_add_z(self):
        """`_ANNO_STYLE_KEYS` is built FROM `ANNO_DEFAULTS`, so a key dropped
        from the table shrinks it in agreement with itself. Pinning the
        expected size catches that."""
        from raptor.api.annotations import _ANNO_STYLE_KEYS
        assert EXPECTED_KEYS | {"z"} == _ANNO_STYLE_KEYS

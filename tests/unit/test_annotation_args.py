"""The annotation API's argument validation.

These tests exist for one specific class of defect: `place_annotation`
takes a dozen optional coordinates, and an incomplete set used to reach
`float()` on a `None` and surface as a bare `TypeError` from several
frames inside the placement code. A caller — or an MCP client on the
other end of a JSON request — cannot act on that message; it names
neither the argument nor the fix.

The checks are all `BuildError`, which is the exception the module
already raises for every other bad argument, so a client that handles
`BuildError` handles all of them.
"""
from __future__ import annotations

import pytest

from raptor.api.build import BuildError
from raptor.api.annotations import place_annotation
from raptor.core.fileio.rbd import parse_rbd
from raptor.gui.new_model import NEW_MODEL_TEXT


@pytest.fixture
def rbd():
    return parse_rbd(NEW_MODEL_TEXT)


def _block_ids(rbd, n=1) -> list[int]:
    """The first `n` non-template block ids, in file order."""
    return [int(float(r["ID"])) for r in rbd.sections["BLOCK_DATA"].rows
            if int(float(r["ID"])) != 0][:n]


def _block_id(rbd, n=1) -> int:
    return _block_ids(rbd, n)[0]


class TestTextCoordinates:
    def test_both_coordinates_place(self, rbd):
        e = place_annotation(rbd, kind="text", text="t", x=100.0, y=50.0)
        assert (e["x"], e["y"]) == (100.0, 50.0)

    def test_x_without_y_is_rejected_with_a_message(self, rbd):
        """REGRESSION: this passed validation (`has_coords` only asked
        whether EITHER was given) and then raised
        `TypeError: float() argument must be... not 'NoneType'` from
        inside the placement code."""
        with pytest.raises(BuildError) as exc:
            place_annotation(rbd, kind="text", text="t", x=100.0)
        msg = str(exc.value)
        assert "x" in msg and "y" in msg, \
            f"the message must name the missing argument: {msg!r}"

    def test_y_without_x_is_rejected_too(self, rbd):
        with pytest.raises(BuildError):
            place_annotation(rbd, kind="text", text="t", y=50.0)

    def test_neither_coordinate_nor_anchor_is_rejected(self, rbd):
        with pytest.raises(BuildError) as exc:
            place_annotation(rbd, kind="text", text="t")
        assert "adjacent_to" in str(exc.value)

    def test_coordinates_are_ignored_when_anchored(self, rbd):
        """An anchor wins over stray coordinates -- the pre-existing
        rule, asserted so the new coordinate check cannot have changed
        it."""
        e = place_annotation(rbd, kind="text", text="a",
                             adjacent_to=_block_id(rbd), x=1.0, y=2.0)
        assert e["anchor"]["target"]
        assert (e["x"], e["y"]) != (1.0, 2.0)


class TestArrowCoordinates:
    def test_all_four_place(self, rbd):
        e = place_annotation(rbd, kind="arrow",
                             x1=0.0, y1=0.0, x2=90.0, y2=45.0)
        assert (e["x1"], e["y1"], e["x2"], e["y2"]) == (0.0, 0.0, 90.0, 45.0)

    @pytest.mark.parametrize("kwargs", [
        {"x1": 1.0}, {"y1": 1.0}, {"x2": 1.0}, {"y2": 1.0},
        {"x1": 1.0, "y1": 1.0, "x2": 1.0},        # missing y2
        {"y1": 1.0, "x2": 1.0, "y2": 1.0},        # missing x1
    ])
    def test_partial_arrow_coordinates_are_rejected(self, rbd, kwargs):
        with pytest.raises(BuildError) as exc:
            place_annotation(rbd, kind="arrow", **kwargs)
        assert "x1/y1/x2/y2" in str(exc.value)

    def test_no_endpoints_and_no_coordinates_is_rejected(self, rbd):
        with pytest.raises(BuildError):
            place_annotation(rbd, kind="arrow")

    def test_one_endpoint_reference_is_rejected(self, rbd):
        with pytest.raises(BuildError) as exc:
            place_annotation(rbd, kind="arrow", from_=_block_id(rbd))
        assert "both from_ and to" in str(exc.value)

    def test_endpoints_and_adjacent_to_are_mutually_exclusive(self, rbd):
        with pytest.raises(BuildError):
            place_annotation(rbd, kind="arrow",
                             from_=_block_id(rbd), to=_block_id(rbd),
                             adjacent_to=_block_id(rbd))


class TestIntegerSelectors:
    """`resolve_element` is annotated `str | int` and its body has an
    explicit `isinstance(selector, int)` branch. These pin that an int
    selector resolves to the same element as its string form, so
    widening the annotation cannot have changed routing."""

    def test_integer_adjacent_to_resolves(self, rbd):
        e = place_annotation(rbd, kind="text", text="a",
                             adjacent_to=_block_id(rbd))
        assert e["anchor"]["target"]

    def test_integer_and_string_selectors_agree(self, rbd):
        a = place_annotation(rbd, kind="text", text="a",
                             adjacent_to=_block_id(rbd), side="left")
        b = place_annotation(rbd, kind="text", text="b",
                             adjacent_to=str(_block_id(rbd)), side="left")
        assert a["anchor"]["target"] == b["anchor"]["target"]
        assert a["x"] == b["x"]

    def test_arrow_by_integer_endpoints(self, rbd):
        """Two DISTINCT endpoints, so this also proves the int branch
        reads the row rather than always landing on the first block.
        The new-model template has one real block, so the two-block golden
        model stands in."""
        import os
        from raptor.core.fileio.rbd import load_rbd
        golden = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "tests", "golden", "TwoBlockRedundancy", "input.rbd")
        if not os.path.exists(golden):
            pytest.skip("fixture not present")
        two = load_rbd(golden)
        a, b = _block_ids(two, 2)
        e = place_annotation(two, kind="arrow", from_=a, to=b)
        assert e["anchor"]["from"] and e["anchor"]["to"]
        # Asserted on GEOMETRY, not on the anchor names: both blocks in
        # this fixture are literally called "unnamed", so the names are
        # equal even when two different rows are correctly read. The
        # resolved scene positions differing is the thing that proves the
        # int branch looked up two different elements.
        assert (e["x1"], e["y1"]) != (e["x2"], e["y2"])


class TestUnchangedBehaviour:
    def test_auto_size_applies_when_absent(self, rbd):
        e = place_annotation(rbd, kind="text", text="t", x=0.0, y=0.0)
        assert e["w"] > 0 and e["h"] > 0

    def test_explicit_size_wins_over_auto(self, rbd):
        e = place_annotation(rbd, kind="text", text="t",
                             x=0.0, y=0.0, w=33.0, h=44.0)
        assert (e["w"], e["h"]) == (33.0, 44.0)

    def test_unknown_kind_and_side_still_rejected(self, rbd):
        with pytest.raises(BuildError):
            place_annotation(rbd, kind="not-a-kind", text="t", x=1.0, y=1.0)
        with pytest.raises(BuildError):
            place_annotation(rbd, kind="text", text="t", x=1.0, y=1.0,
                             side="diagonal")

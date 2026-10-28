"""The model hash must cover every section that can change a simulated number.

Two failure modes motivated this module:

1. `run_manifest._ENGINEERING_SECTIONS` once listed `"RESOURCE_POOL_DATA"`,
   a section name that exists nowhere else in the codebase -- the real one
   is `"RESOURCE_DATA"`. `sections.get()` returns `None` for an unknown
   name, so the section was dropped from the hash payload *silently*:
   editing a resource pool's NumIn / PerTimeUnit / FixedPerUse /
   SpareCost left `model_results_hash` unchanged and the GUI's
   "are these results still valid?" check never fired. All 168 golden
   models carry a `RESOURCE_DATA` section, so this was not theoretical.

2. The section list is duplicated across `core/fileio` (`_END_MARKERS`,
   `_LEGACY_SECTION_ORDER`), `core/fileio/rbds.py`, `api/build.py` and
   here. The tests below pin *this* list against the canonical one so a
   typo cannot recur, and pin the hash's actual sensitivity so a future
   drop-out is caught even if the names stay valid.
"""
from __future__ import annotations

import pytest

from raptor.core.fileio.rbd import (
    _END_MARKERS,
    _LEGACY_SECTION_ORDER,
)
from raptor.core.run_manifest import (
    _ENGINEERING_SECTIONS,
    model_content_hash,
    model_results_hash,
)

GOLDEN = __import__("pathlib").Path(__file__).resolve().parent.parent / "golden"


class TestSectionNames:
    def test_every_name_is_a_real_section(self):
        """A name the file format does not use is a silent hash blind spot.

        This is the regression guard for the `RESOURCE_POOL_DATA` typo:
        nothing raised, the section was just skipped.
        """
        canonical = set(_LEGACY_SECTION_ORDER) | set(_END_MARKERS)
        unknown = [n for n in _ENGINEERING_SECTIONS if n not in canonical]
        assert not unknown, (
            "_ENGINEERING_SECTIONS names sections the file format does not "
            f"have, so they are dropped from the hash without error: {unknown}"
        )

    def test_pools_are_both_covered(self):
        """Spare pools AND resource pools must be hashed.

        Named explicitly because they are the pair that drifted: the list
        had one of the two misspelled, which read as complete coverage.
        """
        assert "SPARE_POOL_DATA" in _ENGINEERING_SECTIONS
        assert "RESOURCE_DATA" in _ENGINEERING_SECTIONS

    def test_run_settings_are_excluded(self):
        """The hash is engineering content only.

        NumRuns / TermCondition / analysis flags live in SYSTEM_DATA and
        must not move it -- otherwise every run-settings edit would
        invalidate the provenance record.
        """
        assert "SYSTEM_DATA" not in _ENGINEERING_SECTIONS


def _model_with_resource_pools():
    """A real golden model carrying three populated RESOURCE_DATA rows.

    `LadL7Cost` is a cost-analysis ladder model, so its pools actually
    drive `res_fixed_per_use` / `res_cost_per_time` -- the values that
    make a pool edit change simulated numbers. Using a checked-in fixture
    rather than a hand-rolled string keeps this honest about the real
    file format (section order, `EndOfResourceData` sentinel, column
    alignment).
    """
    from raptor.core.fileio.builder import load

    rbd = load(str(GOLDEN / "LadL7Cost" / "input.rbd"))
    rows = rbd.sections["RESOURCE_DATA"].rows
    # Asserted loosely on purpose: the point is "this fixture has pools",
    # not a row count that a fixture edit would break for no reason.
    assert len(rows) >= 2, f"fixture has no usable pool rows: {len(rows)}"
    return rbd


class TestHashSensitivity:
    def test_resource_pool_edit_changes_the_results_hash(self):
        """The regression test for the typo.

        Changing a pool's `NumIn` changes simulated numbers, so
        `model_results_hash` MUST move. Before the fix it did not: the
        section was named `"RESOURCE_POOL_DATA"`, `sections.get()`
        returned None, and the whole section was dropped from the hash.
        """
        rbd = _model_with_resource_pools()
        before = model_results_hash(rbd)

        rbd.sections["RESOURCE_DATA"].rows[0]["NumIn"] = "7"

        assert model_results_hash(rbd) != before, (
            "editing a resource pool left model_results_hash unchanged, so "
            "the GUI would not notice its displayed results went stale"
        )

    def test_resource_pool_edit_changes_the_content_hash(self):
        rbd = _model_with_resource_pools()
        before = model_content_hash(rbd)
        rbd.sections["RESOURCE_DATA"].rows[0]["PerTimeUnit"] = "0.9"
        assert model_content_hash(rbd) != before

    def test_every_pool_section_is_hashed(self):
        """Not just the first row -- a drop-out drops the whole section."""
        rbd = _model_with_resource_pools()
        before = model_results_hash(rbd)
        for i, _row in enumerate(rbd.sections["RESOURCE_DATA"].rows):
            rbd.sections["RESOURCE_DATA"].rows[i]["SpareCost"] = f"{i + 1}.0"
        assert model_results_hash(rbd) != before

    def test_layout_only_edit_leaves_the_results_hash_alone(self):
        """Moving a block cannot change a simulated number.

        `LAYOUT_COLUMNS` is the documented exemption; this pins it so the
        exemption is not widened by accident.
        """
        rbd = _model_with_resource_pools()
        before = model_results_hash(rbd)
        rbd.sections["BLOCK_DATA"].rows[0]["xPos"] = "999.0"
        assert model_results_hash(rbd) == before

    def test_distribution_edit_moves_the_hash(self):
        rbd = _model_with_resource_pools()
        before = model_results_hash(rbd)
        rbd.sections["BLOCK_DATA"].rows[0]["FParam1"] = "250.0"
        assert model_results_hash(rbd) != before

    def test_spare_pool_edit_moves_the_hash(self):
        """The sibling section, named correctly, must behave the same."""
        rbd = _model_with_resource_pools()
        if not rbd.sections.get("SPARE_POOL_DATA") or not rbd.sections["SPARE_POOL_DATA"].rows:
            pytest.skip("fixture has no populated spare pools")
        before = model_results_hash(rbd)
        rbd.sections["SPARE_POOL_DATA"].rows[0]["NumIn"] = "9"
        assert model_results_hash(rbd) != before

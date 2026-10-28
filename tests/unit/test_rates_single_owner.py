"""Rate identity and formula provenance have ONE owner (review D-20).

The GUI's Rates dialog and `raptor.api.rates` disagreed about two rules,
and the GUI's copy was the wrong one in both:

- **Case sensitivity.** `api.rates` matched a rate name
  case-INsensitively; the dialog's duplicate check was a
  case-SENSITIVE set membership test. So the dialog accepted `Highway`
  beside `highway`, `add_rate` then refused `highway`, and
  `delete_rate("highway")` removed whichever came first in the list --
  possibly the other rate. The dialog's auto-namer had the same flaw and
  would *generate* a collision from an existing `newrate`.

- **Provenance purging.** `delete_rate` purged the `{"rate": name}`
  formula entries belonging to the rate it removed; the dialog's delete
  path just did `del self.rates[i]`, leaving the stale `@Name` reference
  that `main_window.py`'s own legacy-save warning then complains about.
  The API's purge was also an exact-match test against the one removed
  name, while formula entries record the name as *typed* -- so an entry
  written as `@highway` survived deleting `Highway`.

These tests pin the shared rules and both divergences.
"""
from __future__ import annotations

import pytest

from raptor.api.rates import find_rate, prune_formulas, rate_name_conflict


def _rate(name: str) -> dict:
    return {"name": name, "expr": "1", "value": 1.0, "description": ""}


class TestFindRate:
    def test_exact_match(self):
        assert find_rate([_rate("Highway"), _rate("Rail")], "Rail") == 1

    def test_case_insensitive(self):
        assert find_rate([_rate("Highway")], "highway") == 0

    def test_absent(self):
        assert find_rate([_rate("Highway")], "Motorway") is None

    def test_strips_surrounding_whitespace(self):
        assert find_rate([_rate("Highway")], "  highway  ") == 0

    def test_an_empty_name_matches_an_unnamed_rate(self):
        """Documented consequence of stripping both sides. A rate whose
        name is empty or whitespace is addressed as ""."""
        assert find_rate([{"expr": "1", "value": 1.0}], "") == 0


class TestRateNameConflict:
    """The dialog's duplicate check. Same rule, shaped for a list that
    contains the row being renamed."""

    def test_no_conflict(self):
        assert rate_name_conflict([_rate("Highway")], "Rail") is None

    def test_exact_clash_returns_the_stored_spelling(self):
        got = rate_name_conflict([_rate("Highway")], "Highway")
        assert got == "Highway"

    def test_a_case_variant_is_a_clash(self):
        """THE D-20 BUG. A case-sensitive check let this through."""
        assert rate_name_conflict([_rate("Highway")], "highway") == "Highway"

    def test_a_row_is_not_in_conflict_with_itself(self):
        r = _rate("Highway")
        assert rate_name_conflict([r, _rate("Rail")], "Highway",
                                  exclude=r) is None

    def test_exclude_only_skips_that_identity(self):
        r = _rate("Highway")
        other = _rate("highway")
        assert rate_name_conflict([r, other], "Highway",
                                  exclude=r) == "highway"


class TestPruneFormulas:
    def test_a_removed_rate_takes_its_provenance(self):
        formulas = {
            "Block:1:FParam1": {"rate": "Highway", "value": 3.0},
            "Block:1:FParam2": {"rate": "Rail", "value": 4.0},
        }
        got = prune_formulas([_rate("Rail")], formulas)
        assert got == {"Block:1:FParam2": {"rate": "Rail", "value": 4.0}}

    def test_an_expression_entry_is_untouched(self):
        """`{"expr": ...}` carries no rate reference to dangle."""
        formulas = {"Block:1:FParam1": {"expr": "2 * @gone", "value": 1.0}}
        assert prune_formulas([], formulas) == formulas

    def test_a_case_mismatched_reference_is_pruned(self):
        """An entry records the name as TYPED. Deleting `Highway` must
        also drop an entry written as `@highway` -- the old exact-match
        purge missed this half."""
        formulas = {"Block:1:FParam1": {"rate": "highway", "value": 3.0}}
        assert prune_formulas([], formulas) == {}

    def test_a_live_rate_keeps_its_entry_regardless_of_case(self):
        formulas = {"Block:1:FParam1": {"rate": "highway", "value": 3.0}}
        assert prune_formulas([_rate("Highway")], formulas) == formulas

    def test_it_is_idempotent(self):
        """Stated as a function of the surviving rates, so running it
        twice is the same as running it once -- which is what makes it
        safe at a delete site that cannot know what else was removed."""
        formulas = {"b": {"rate": "A", "value": 1.0},
                    "c": {"rate": "B", "value": 2.0}}
        once = prune_formulas([_rate("A")], formulas)
        assert prune_formulas([_rate("A")], once) == once

    def test_an_entry_pointing_at_no_rate_is_kept(self):
        formulas = {"b": {"value": 1.0}}
        assert prune_formulas([], formulas) == formulas

    def test_empty_inputs(self):
        assert prune_formulas([], None) == {}
        assert prune_formulas(None, None) == {}

    def test_a_non_dict_entry_does_not_crash(self):
        """Defensive: `formulas` is loaded from a legacy file, so a
        malformed value must not take the delete path down."""
        formulas = {"b": "not-a-dict", "c": {"rate": "Gone", "value": 1.0}}
        assert prune_formulas([], formulas) == {"b": "not-a-dict"}

    def test_it_does_not_mutate_its_input(self):
        formulas = {"b": {"rate": "Gone", "value": 1.0}}
        prune_formulas([], formulas)
        assert formulas == {"b": {"rate": "Gone", "value": 1.0}}


class TestTheDialogUsesTheSharedRules:
    """Structural: the dialog must not grow its own identity rule back."""

    @pytest.fixture()
    def dialog_src(self) -> str:
        import inspect

        import raptor.gui.ui.rates_dialog as mod
        return inspect.getsource(mod)

    def test_the_dialog_imports_the_shared_owners(self, dialog_src):
        for name in ("find_rate", "rate_name_conflict", "prune_formulas"):
            assert name in dialog_src, f"{name} is not used by the dialog"

    def test_no_case_sensitive_name_set_is_left_behind(self, dialog_src):
        """The old check was `{x.get("name") for x in ...}`."""
        assert 'x.get("name") for x in' not in dialog_src, (
            "a case-sensitive name set has been reintroduced in the dialog; "
            "use rate_name_conflict instead")


class TestThePurgeIsUndoable:
    """Structural, and it is the part that is easy to get wrong.

    `RatesCmd` used to swap `rbd.rates` only. If the purge happens
    outside the undo command, Undo restores the deleted rate while its
    `{"rate": name}` provenance stays purged -- so the undo reintroduces
    the exact dangling `@Name` the purge removed.
    """

    def test_the_undo_command_carries_the_formulas(self):
        import inspect

        from raptor.gui.ui.rates_dialog import RatesCmd
        params = inspect.signature(RatesCmd.__init__).parameters
        assert "old_formulas" in params and "new_formulas" in params, (
            "RatesCmd no longer snapshots the formula records, so undoing a "
            "rate delete cannot restore the provenance it removed")

    def test_both_apply_directions_pass_them(self):
        """redo/undo delegate to `_apply`, so both must hand it the
        formulas as well as the rows."""
        import inspect

        from raptor.gui.ui.rates_dialog import RatesCmd
        for meth in (RatesCmd.redo, RatesCmd.undo):
            assert "self._apply(" in inspect.getsource(meth), (
                f"{meth.__name__} no longer goes through _apply")
        assert "_new_f" in inspect.getsource(RatesCmd.redo)
        assert "_old_f" in inspect.getsource(RatesCmd.undo)

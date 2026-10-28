"""One predicate, one severity for the StartStats warning (review E-15).

The condition *"TimeSim and `start_stats >= term_condition`"* was written
twice, and the two copies disagreed about how serious it was:

| Site | Effect |
|---|---|
| `run_experiment_mixin.py`'s readiness line | `panel.set_readiness(not warn, …)` — the panel reported **not ready** |
| `run_panel.py`'s field styling | red border + tooltip; `set_readiness` never called — **non-blocking** |

Same condition, same screen, two answers to "can this model be run?": a
blocking red banner in one place and a red field border in the other.

The severity is now a **warning**, decided once in
`core/run_experiment.py::stats_start_is_blocking()`. That is the right
answer on the merits rather than by compromise: the run still executes and
still produces numbers — it just excludes no start-up transient, which is a
modelling choice, not an error. `normalise` clamps both fields rather than
rejecting them, and the engine has no gate on the pair.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from raptor.core.run_experiment import (
    STATS_START_WARNING,
    stats_start_after_run_length,
    stats_start_is_blocking,
    stats_start_tooltip,
    stats_start_warning,
)

REPO = Path(__file__).resolve().parent.parent.parent


def _exp(**over) -> dict:
    base = {"termination_type": "TimeSim", "start_stats": 0.0,
            "term_condition": 1000.0}
    base.update(over)
    return base


class TestThePredicate:
    def test_a_start_after_the_end_is_flagged(self):
        assert stats_start_after_run_length(
            _exp(start_stats=1000.0, term_condition=1000.0)) is True

    def test_a_start_before_the_end_is_fine(self):
        assert stats_start_after_run_length(
            _exp(start_stats=999.0, term_condition=1000.0)) is False

    def test_a_zero_start_is_fine(self):
        assert stats_start_after_run_length(
            _exp(start_stats=0.0, term_condition=1000.0)) is False

    @pytest.mark.parametrize("term", ["FailSim", "CycleSim"])
    def test_it_does_not_apply_outside_timesim(self, term):
        """A FailSim/CycleSim run ends on a condition, not a time, so there
        is no run length for a start time to be 'not before'."""
        assert stats_start_after_run_length(
            _exp(termination_type=term, start_stats=99999.0)) is False

    def test_it_tolerates_missing_fields(self):
        assert stats_start_after_run_length({}) is False

    def test_it_accepts_strings(self):
        """`normalise` coerces, but a caller may hand a raw dict."""
        assert stats_start_after_run_length(
            {"termination_type": "TimeSim", "start_stats": "1000",
             "term_condition": "500"}) is True


class TestTheSeverity:
    def test_it_is_not_blocking(self):
        """THE BUG. The readiness line treated this as "not ready", while
        the field styling treated it as a warning -- same screen."""
        assert stats_start_is_blocking() is False

    def test_the_run_still_executes_with_it(self):
        """Stated as a fact about the engine, so the severity has a
        justification beyond "the banner looked nicer"."""
        assert stats_start_after_run_length(
            _exp(start_stats=5000.0)) is True, (
            "premise: this is the flagged case, and the engine runs it")

    def test_normalise_clamps_rather_than_rejects(self):
        """`normalise` does not refuse the pair either -- so nothing
        downstream treats it as an error."""
        from raptor.core.run_experiment import normalise
        out = normalise({"termination_type": "TimeSim",
                         "start_stats": 5000.0, "term_condition": 100.0})
        assert out["start_stats"] == 5000.0
        assert out["term_condition"] == 100.0


class TestTheWording:
    def test_the_warning_names_the_problem(self):
        assert stats_start_warning(
            _exp(start_stats=5000.0)) == STATS_START_WARNING
        assert "statistics start" in STATS_START_WARNING
        assert "run length" in STATS_START_WARNING

    def test_no_warning_when_there_is_no_problem(self):
        assert stats_start_warning(_exp(start_stats=10.0)) == ""

    def test_the_tooltip_says_the_fix_when_flagged(self):
        tip = stats_start_tooltip(_exp(start_stats=5000.0))
        assert "earlier" in tip.lower(), (
            "the flagged tooltip must say what to do about it")

    def test_the_tooltip_explains_the_field_when_not(self):
        tip = stats_start_tooltip(_exp(start_stats=10.0))
        assert "earlier" not in tip.lower()
        assert "Statistics" in tip


class TestBothSitesDelegate:
    """Structural: the predicate and the severity are each written once."""

    def _source(self, rel: str) -> str:
        return (REPO / rel).read_text()

    @pytest.mark.parametrize("rel", [
        "raptor/gui/ui/run_experiment_mixin.py",
        "raptor/gui/ui/run_panel.py",
    ])
    def test_nobody_reimplements_the_predicate(self, rel):
        tree = ast.parse(self._source(rel))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            compared = {n.attr for n in ast.walk(node)
                        if isinstance(n, ast.Attribute)}
            if "start_stats" in compared or "term_condition" in compared:
                pytest.fail(
                    f"{rel}:{node.lineno} compares start_stats/term_condition "
                    "directly; call core.run_experiment."
                    "stats_start_after_run_length instead")

    def test_the_readiness_line_asks_the_severity_question(self):
        src = self._source("raptor/gui/ui/run_experiment_mixin.py")
        assert "stats_start_is_blocking" in src, (
            "the readiness line must consult stats_start_is_blocking(), not "
            "infer severity from whether a warning string is non-empty")

    def test_the_field_styling_uses_the_shared_tooltip(self):
        src = self._source("raptor/gui/ui/run_panel.py")
        assert "stats_start_tooltip" in src
        assert "stats_start_after_run_length" in src

    def test_the_two_sites_cannot_disagree_about_the_warning_text(self):
        """One string in the core, quoted by both. The old copies each
        carried their own wording."""
        for rel in ("raptor/gui/ui/run_experiment_mixin.py",
                    "raptor/gui/ui/run_panel.py"):
            src = self._source(rel)
            assert "statistics start is not before" not in src, (
                f"{rel} retypes the warning text; it must come from "
                "core.run_experiment.STATS_START_WARNING")

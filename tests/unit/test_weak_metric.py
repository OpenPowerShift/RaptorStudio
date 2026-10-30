"""One definition of the Ch.11 Availability/Dependability ratio.

The ratio was computed twice, and the two copies disagreed on the single
question the manual answers: does dependency-blocked time (`idle`) count
against **Dependability**?

- `results.py`'s Weak Link table dropped `idle` from the denominator, with
  a comment quoting Ch.11 ("excludes the outside influences of standby
  (blues), preventive maintenance (oranges) and dependency (brown)").
- `main_window.py`'s canvas overlay kept it -- `PRINCIPAL_DOWN = ("idle",
  "repair", "rep_hold", "done")`.

Both fed `apply_weak_colors` with the same GY/YR thresholds, so for any
model with dependencies the same block could be painted green in the table
and red on the canvas. Two views of one question, answering differently,
with no test connecting them.
"""
from __future__ import annotations

import pytest

from raptor.core.engine.weak_metric import (
    AVAILABILITY,
    AVAILABILITY_DOWN,
    AVAILABILITY_UP,
    DEPENDABILITY,
    DEPENDABILITY_DOWN,
    DEPENDABILITY_UP,
    buckets,
    cross_run_weak_metric,
    weak_metric,
)


class TestBucketSets:
    def test_idle_is_excluded_from_dependability(self):
        """THE BUG. `idle` is a block's `BlockStatus.IDLE`, entered only via
        dependency.py's dependence transitions -- the block-level
        equivalent of a node's "brown". Manual Ch.11 says dependability
        excludes brown. It is dependency time, not downtime.

        `main_window`'s overlay had it in the denominator, so a block held
        out of service by an upstream dependency dragged its dependability
        down: 100 hours running and 1 hour idle scored 0.990 instead of
        1.000, and against a 0.98 threshold that is the difference between
        green and red.
        """
        assert "idle" not in DEPENDABILITY_DOWN
        stats = {"run": 100.0, "idle": 1.0}
        assert weak_metric(stats, DEPENDABILITY) == 1.0

    def test_idle_is_included_in_availability(self):
        """The contrast that makes the exclusion meaningful: for
        AVAILABILITY the block genuinely was not available, so `idle`
        counts against it."""
        assert "idle" in AVAILABILITY_DOWN
        stats = {"run": 100.0, "idle": 1.0}
        assert weak_metric(stats, AVAILABILITY) == pytest.approx(100 / 101)

    def test_standby_is_up_for_availability_and_out_of_both_denominators(self):
        """Standby is uptime for AVAILABILITY and simply unaccounted-for
        for DEPENDABILITY (Ch.11 excludes blues) -- so it is in the
        availability numerator, and in neither denominator."""
        assert "standby" in AVAILABILITY_UP
        for keys in (AVAILABILITY_DOWN, DEPENDABILITY_UP, DEPENDABILITY_DOWN):
            assert "standby" not in keys
        stats = {"run": 10.0, "standby": 5.0}
        assert weak_metric(stats, AVAILABILITY) == 1.0
        assert weak_metric(stats, DEPENDABILITY) == 1.0   # all "run"

    def test_pm_and_pm_hold_count_against_availability_but_not_dependability(self):
        """Ch.11 excludes PM (orange) from DEPENDABILITY. For AVAILABILITY
        it counts against you: the block was not available while under
        maintenance.

        Both original copies already agreed on this (results.py's `bdown`
        and main_window's `DOWN_KEYS` both carried them), so it is
        behaviour, not an inference -- and it is the contrast that makes
        the `idle` exclusion the odd one out.
        """
        assert "pm" in AVAILABILITY_DOWN
        assert "pm_hold" in AVAILABILITY_DOWN
        for keys in (DEPENDABILITY_UP, DEPENDABILITY_DOWN,
                     AVAILABILITY_UP):
            assert "pm" not in keys
            assert "pm_hold" not in keys
        stats = {"run": 100.0, "pm": 10.0}
        assert weak_metric(stats, AVAILABILITY) == pytest.approx(100 / 110)
        assert weak_metric(stats, DEPENDABILITY) == 1.0

    def test_real_failure_states_are_in_both_denominators(self):
        for keys in (AVAILABILITY_DOWN, DEPENDABILITY_DOWN):
            for state in ("repair", "rep_hold", "done"):
                assert state in keys

    def test_availability_accounts_for_every_weak_state(self):
        """Availability must name every weak state the engine tracks, or
        time silently vanishes from the ratio."""
        assert set(AVAILABILITY_UP) | set(AVAILABILITY_DOWN) == {
            "run", "standby", "idle", "pm", "pm_hold", "repair",
            "rep_hold", "done"}

    def test_dependability_accounts_for_run_and_the_failure_states(self):
        """Dependability names fewer buckets BY DESIGN (Ch.11) -- the test
        pins the exact set so a future edit to it is deliberate."""
        assert set(DEPENDABILITY_UP) | set(DEPENDABILITY_DOWN) == {
            "run", "repair", "rep_hold", "done"}


def _module_source(rel: str) -> str:
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    return (root / rel).read_text(encoding="utf-8")


class TestConsumersDoNotReimplementIt:
    """The drift happened because two consumers spelled the buckets out
    locally. Testing `weak_metric` alone cannot catch that -- a consumer
    can go back to its own constants while every test here still passes.

    So these assert the *call sites*: neither the results tab nor the
    canvas overlay may name a weak-state bucket of its own. Both must
    import the shared owner. This is the check that fails when the
    `main_window` bug is reintroduced.
    """

    #: Weak-state bucket names. Appearing inside a *metric* function means
    #: it is re-deriving the ratio; appearing in a legend or a log-line
    #: colour classifier is unrelated and fine, which is why the check is
    #: scoped to the metric functions rather than the whole file.
    _BUCKET_LITERALS = ("pm_hold", "rep_hold", "standby", "idle")

    @staticmethod
    def _function_source(rel: str, qualname: str) -> str:
        """The source text of one function, by dotted name."""
        import ast
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2]
        src = (root / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        parts = qualname.split(".")
        node: ast.AST = tree
        for part in parts:
            found = None
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef)) and child.name == part:
                    found = child
                    break
            assert found is not None, f"{part!r} not found in {rel}"
            node = found
        lines = src.splitlines()
        return "\n".join(lines[node.lineno - 1: node.end_lineno])

    @pytest.mark.parametrize("rel, qualname", [
        ("raptor/gui/ui/main_window.py", "MainWindow._toggle_weak_colors"),
        ("raptor/gui/ui/results.py", "ResultsDock._weak_metric_rows"),
    ])
    def test_the_metric_function_names_no_buckets(self, rel, qualname):
        body = self._function_source(rel, qualname)
        offenders = [lit for lit in self._BUCKET_LITERALS
                     if f'"{lit}"' in body or f"'{lit}'" in body]
        assert not offenders, (
            f"{qualname} names weak-state buckets inline ({offenders}); it "
            "must call core.engine.weak_metric instead. Two copies of the "
            "bucket sets is how D-07 happened."
        )

    def test_main_window_uses_the_shared_cross_run_helper(self):
        """The canvas overlay's colouring path specifically. It averaged
        per-run fractions across runs; the helper must still be the thing
        doing that."""
        src = _module_source("raptor/gui/ui/main_window.py")
        assert "cross_run_weak_metric(" in src, (
            "main_window no longer calls the shared metric -- check "
            "_toggle_weak_colors still colours from weak_metric"
        )

    def test_results_tab_takes_its_buckets_from_the_module(self):
        src = _module_source("raptor/gui/ui/results.py")
        assert "weak_metric.buckets(" in src, (
            "the Weak Link tab no longer takes its bucket sets from the "
            "shared module"
        )

    def test_buckets_dispatch(self):
        assert buckets(AVAILABILITY) == (AVAILABILITY_UP, AVAILABILITY_DOWN)
        assert buckets(DEPENDABILITY) == (DEPENDABILITY_UP, DEPENDABILITY_DOWN)

    def test_an_unknown_analysis_falls_back_to_availability(self):
        """Callers select on a user-facing menu string. A 0.0 would be
        worse than the wrong-but-total denominator."""
        assert buckets("dependabilty") == buckets(AVAILABILITY)


class TestRatio:
    def test_pure_up_is_one(self):
        assert weak_metric({"run": 42.0}, AVAILABILITY) == 1.0

    def test_pure_down_is_zero(self):
        assert weak_metric({"repair": 42.0}, AVAILABILITY) == 0.0

    def test_an_empty_dict_is_one_not_a_crash(self):
        # Raptor 7's `DoDenom`/`AoDenom` fallback is 1.0; a phased block
        # that spent the whole run on standby must average as 1.0.
        assert weak_metric({}, AVAILABILITY) == 1.0
        assert weak_metric({}, DEPENDABILITY) == 1.0

    def test_standby_only_run_counts_as_dependable(self):
        # Phasing3's HighGain spends all of a run on standby. Raptor's
        # Do denominator is then empty and its fallback is 1.0.
        assert weak_metric({"standby": 100.0}, DEPENDABILITY) == 1.0

    def test_standby_only_runs_average_like_raptor_phasing3(self):
        # Phasing3's HighGain: most runs have no Do denominator at all,
        # one has 70/100 run time. Raptor reports .971, not .071/.171.
        runs = [{1: {"standby": 100.0}} for _ in range(9)]
        runs.append({1: {"run": 70.0, "repair": 30.0}})
        got = cross_run_weak_metric(runs, [1], DEPENDABILITY)[1]
        assert got == pytest.approx((9.0 + 0.7) / 10.0)

    def test_phasing3_highgain_dependability_matches_raptor(self):
        from pathlib import Path
        from raptor.core.fileio.rbd import load_rbd
        from raptor.core.engine.runner import run_simulation_with_reports

        rbd = load_rbd(str(Path(__file__).resolve().parents[1]
                           / "golden" / "Phasing3" / "input.rbd"))
        rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
        rows = []
        run_simulation_with_reports(rbd, num_runs=10, date="x",
                                    report_sink=rows)
        got = cross_run_weak_metric(
            [d.weak_stats for d in rows], [7], DEPENDABILITY)[7]
        assert got == pytest.approx(0.971, abs=5e-4)

    def test_missing_keys_are_treated_as_zero(self):
        assert weak_metric({"run": 10.0}, AVAILABILITY) == 1.0

    def test_lowercase_analysis_names_use_the_same_metric(self):
        """File/user strings need not match the display capitalisation;
        lower-case 'dependability' used to fall through to Availability."""
        from raptor.core.engine.weak_metric import event_metric, node_metric

        block = {"run": 5.0, "idle": 95.0}
        assert weak_metric(block, "dependability") == weak_metric(
            block, DEPENDABILITY)
        node = {"green": 20.0, "red": 80.0}
        assert node_metric(node, "dependability") == node_metric(
            node, DEPENDABILITY)
        event = {"run": 30.0, "standby": 70.0}
        assert event_metric(event, "dependability") == event_metric(
            event, DEPENDABILITY)

    def test_the_two_analyses_differ_exactly_when_idle_is_nonzero(self):
        """A useful invariant: with no `idle` time the two metrics agree,
        so the divergence only ever shows up on a model that actually has
        dependencies."""
        for stats in ({"run": 100.0}, {"run": 5.0, "repair": 5.0},
                      {"run": 5.0, "standby": 5.0}):
            assert weak_metric(stats, AVAILABILITY) == weak_metric(
                stats, DEPENDABILITY), stats
        with_idle = {"run": 5.0, "idle": 5.0}
        assert weak_metric(with_idle, AVAILABILITY) != weak_metric(
            with_idle, DEPENDABILITY)


class TestCrossRun:
    def test_averages_the_ratios_not_the_totals(self):
        """Mean-of-ratios, matching what both views did. Two runs, very
        different magnitudes: pooling first would let the long run
        dominate, and the table would stop agreeing with its own rows."""
        runs = [{1: {"run": 1.0, "repair": 1.0}},          # 0.5
                {1: {"run": 99.0, "repair": 1.0}}]         # 0.99
        got = cross_run_weak_metric(runs, [1], AVAILABILITY)[1]
        assert got == pytest.approx((0.5 + 0.99) / 2)
        pooled = 100.0 / 102.0
        assert got != pytest.approx(pooled)

    def test_a_block_missing_from_a_run_is_skipped_not_zeroed(self):
        runs = [{1: {"run": 10.0, "repair": 0.0}},
                {2: {"run": 10.0, "repair": 10.0}}]
        # Block 1 appears in one run only; averaging in a 0.0 for the
        # other would halve a perfect availability.
        assert cross_run_weak_metric(runs, [1, 2], AVAILABILITY) == {
            1: 1.0, 2: 0.5}

    def test_a_block_absent_everywhere_is_omitted(self):
        runs = [{1: {"run": 10.0}}]
        assert 99 not in cross_run_weak_metric(runs, [1, 99], AVAILABILITY)

    def test_no_runs_gives_an_empty_result(self):
        assert cross_run_weak_metric([], [1], AVAILABILITY) == {}

    def test_dependability_is_averaged_over_the_excluded_buckets(self):
        """The end-to-end statement of D-07: same inputs, both views, one
        number."""
        runs = [{7: {"run": 50.0, "idle": 50.0}}]
        assert cross_run_weak_metric(runs, [7], AVAILABILITY)[7] == pytest.approx(
            0.5)
        assert cross_run_weak_metric(runs, [7], DEPENDABILITY)[7] == 1.0


# ---------------------------------------------------------------------------
# Reliability (the third Ch.11 analysis type)
# ---------------------------------------------------------------------------

from raptor.core.engine.weak_metric import (  # noqa: E402
    ANALYSES, RELIABILITY, canonical, reliability, weak_metric)


class TestCanonical:
    @pytest.mark.parametrize("raw,want", [
        ("Availability", AVAILABILITY), ("availability", AVAILABILITY),
        ("Dependability", DEPENDABILITY), ("dep", DEPENDABILITY),
        ("Reliability", RELIABILITY), ("reliability  ", RELIABILITY),
        ("rel", RELIABILITY),
        ("", AVAILABILITY), (None, AVAILABILITY), ("nonsense", AVAILABILITY),
    ])
    def test_names(self, raw, want):
        assert canonical(raw) == want

    def test_every_listed_analysis_round_trips(self):
        for a in ANALYSES:
            assert canonical(a) == a


class TestReliability:
    def test_is_mission_based_not_time_based(self):
        stats = {"run": 1.0, "repair": 99.0, "m_completed": 3}
        assert reliability(stats, 4) == pytest.approx(0.75)
        assert weak_metric(stats, "Reliability", 4) == pytest.approx(0.75)
        # the time-based answers are unaffected by missions
        assert weak_metric(stats, "Availability", 4) == pytest.approx(0.01)

    @pytest.mark.parametrize("missions", [0, None, -1])
    def test_no_missions_attempted_is_one(self, missions):
        assert reliability({"m_completed": 0}, missions) == 1.0

    def test_missing_m_completed_is_zero_completed(self):
        assert reliability({}, 5) == 0.0

    def test_cross_run_mean_of_ratios_uses_each_runs_own_missions(self):
        runs = [{7: {"m_completed": 1}}, {7: {"m_completed": 3}}]
        got = cross_run_weak_metric(runs, [7], "Reliability",
                                    missions=[2, 4])
        assert got[7] == pytest.approx((0.5 + 0.75) / 2)


# --- node, event and hierarchy ratios (no Qt) ------------------------------------------------
# These were reached only by GUI-marked tests, so the engine tier left them at
# 44% branch coverage. They are engine functions; the engine tier pins them.
from raptor.core.engine.weak_metric import (  # noqa: E402
    cross_run_event_metric,
    cross_run_node_metric,
    event_metric,
    hier_time_shares,
    node_metric,
)

_NODE = {"green": 6.0, "yellow": 2.0, "red": 1.0, "orange": 0.5, "blue": 0.25, "brown": 0.25}


class TestNodeMetric:
    def test_availability_counts_blue_as_up_and_the_rest_in_the_denominator(self):
        assert node_metric(_NODE, "Availability") == pytest.approx((6 + 2 + 0.25) / 10.0)

    def test_dependability_leaves_standby_pm_and_dependency_out(self):
        assert node_metric(_NODE, "Dependability") == pytest.approx(8.0 / 9.0)

    def test_reliability_delegates_to_the_mission_ratio(self):
        assert node_metric({"m_completed": 3}, "Reliability", 4) == pytest.approx(0.75)

    def test_an_empty_node_is_one_not_a_crash(self):
        assert node_metric({}, "Availability") == 1.0
        assert node_metric({}, "Dependability") == 1.0


class TestEventMetric:
    _EVT = {"run": 6.0, "standby": 2.0, "repair": 2.0}

    def test_availability_counts_standby_as_up(self):
        assert event_metric(self._EVT, "Availability") == pytest.approx(0.8)

    def test_dependability_leaves_standby_out(self):
        assert event_metric(self._EVT, "Dependability") == pytest.approx(0.75)

    def test_reliability_delegates_to_the_mission_ratio(self):
        assert event_metric({"m_completed": 1}, "Reliability", 2) == pytest.approx(0.5)

    def test_an_empty_event_is_one(self):
        assert event_metric({}, "Availability") == 1.0


class TestCrossRunNodeAndEvent:
    def test_node_metric_is_the_mean_of_ratios_and_skips_absent_nodes(self):
        runs = [{1: {"green": 1.0, "red": 1.0}}, {1: {"green": 3.0, "red": 1.0}}, {2: {"green": 1.0}}]
        out = cross_run_node_metric(runs, [1, 2, 3], "Availability")
        assert out == {1: pytest.approx((0.5 + 0.75) / 2), 2: 1.0}      # node 3 is in no run

    def test_node_metric_reads_each_runs_own_mission_count(self):
        runs = [{1: {"m_completed": 1}}, {1: {"m_completed": 3}}]
        out = cross_run_node_metric(runs, [1], "Reliability", missions=[2, 4])
        assert out == {1: pytest.approx((0.5 + 0.75) / 2)}

    def test_event_metric_is_the_mean_of_ratios_and_skips_absent_events(self):
        runs = [{7: {"run": 1.0, "repair": 1.0}}, {7: {"run": 3.0, "repair": 1.0}}, None]
        out = cross_run_event_metric(runs, [7, 8], "Dependability")
        assert out == {7: pytest.approx((0.5 + 0.75) / 2)}

    def test_event_metric_reads_each_runs_own_mission_count(self):
        runs = [{7: {"m_completed": 2}}, {7: {"m_completed": 0}}]
        out = cross_run_event_metric(runs, [7], "Reliability", missions=[2, 2])
        assert out == {7: pytest.approx(0.5)}


class TestHierTimeShares:
    def test_shares_add_to_one_and_availability_counts_standby_as_up(self):
        out = hier_time_shares([{1: {"green": 5.0, "yellow": 1.0, "blue": 1.0, "red": 1.0,
                                    "orange": 1.0, "brown": 1.0}}])[1]
        assert out["working"] == pytest.approx(5 / 10)
        assert out["degraded"] == pytest.approx(1 / 10)
        assert out["standby"] == pytest.approx(1 / 10)
        assert out["down"] == pytest.approx(3 / 10)
        assert out["availability"] == pytest.approx(7 / 10)
        assert out["working"] + out["degraded"] + out["standby"] + out["down"] == pytest.approx(1.0)
        assert out["runs"] == 1

    def test_it_averages_ratios_and_skips_runs_with_no_time(self):
        runs = [{1: {"green": 1.0, "red": 1.0}}, {1: {"green": 3.0, "red": 1.0}},
                {1: {}}, None]
        out = hier_time_shares(runs)
        assert out[1]["runs"] == 2
        assert out[1]["availability"] == pytest.approx((0.5 + 0.75) / 2)

    def test_a_hierarchy_that_never_had_time_is_absent(self):
        assert hier_time_shares([{1: {}}]) == {}

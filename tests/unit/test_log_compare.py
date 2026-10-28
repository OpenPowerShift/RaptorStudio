"""The shared event-log comparator: one tolerance, one boundary rule.

This module replaced eleven hand-written copies. Eight of them used a flat
`abs(a - b) > 1.5e-6`; three used the magnitude-scaled
`max(1.5e-6, abs(b) * 2e-6)`, which is the form `test_orchestrator.py`
justified in prose ("a flat absolute tolerance is too tight for
SpaceStation's larger-magnitude Gamma/Pearson5 draws, where residuals of
a few 1e-6 are fp32-level noise that scales with the value's own
magnitude").

The two variants could disagree about the same line: at magnitude 1e5 a
3e-6 residual is `fp32-noise` to one copy and `structural` to another.
The flat group survived only because its fixtures happened to be
low-magnitude. These tests pin the chosen behaviour and the boundary, so
a future tolerance change is a deliberate edit rather than a drift.

`test_orchestrator.py`, `test_reports.py` and `test_golden_fidelity.py`
also carry a rationale for having copied rather than imported ("pytest's
default import mode doesn't expose sibling test modules as importable
packages"). That was false -- `tests/__init__.py` and
`tests/unit/__init__.py` both exist and sibling imports were already in
use -- so the copies are gone.
"""
from __future__ import annotations

from tests.unit.log_compare import (
    ABS_TOL,
    LANDMARK_BEGIN_RUN,
    LANDMARK_END_SIM,
    REL_TOL,
    Comparison,
    classify,
    compare_runs,
    read_lines,
    split_runs,
)


class TestClassification:
    """What `classify` actually decides, and what it deliberately does not.

    The rule is NUMERIC-FIELD-based, not text-based: two lines match when
    they hold the same number of numerics and each is within tolerance,
    whatever the surrounding words are. That is faithful to what the
    per-file copies did -- it is why `PumpA` vs `PumpB` above comes back
    `fp32-noise`. Asserting otherwise would be asserting a stronger
    comparison than the golden captures ever had, and would fail on real
    captures whose prose varies between versions.
    """

    def test_identical_lines_match(self):
        assert classify("12.500000 Block Pump", "12.500000 Block Pump") == "match"

    def test_the_comparison_is_numeric_not_textual(self):
        """Pinned so the tolerance's scope stays understood.

        A name change with the same numerics is NOT caught by `classify`.
        Callers that care about names compare them separately; the eleven
        copies this replaced had exactly this behaviour.
        """
        assert classify("12.5 Block PumpA", "12.5 Block PumpB") == "fp32-noise"

    def test_a_differing_field_count_is_structural(self):
        assert classify("1.0 2.0", "1.0") == "structural"

    def test_a_large_relative_residual_is_structural(self):
        """At magnitude 1e9 the scaled band is 2e-6 * 1e9 = 2000, so a
        difference must exceed that to be flagged -- and a genuine
        regression in a large-magnitude value is far larger than that."""
        assert classify("999999999.0", "1000000000.0") == "fp32-noise"
        assert classify("999000000.0", "1000000000.0") == "structural"

    def test_fp32_noise_at_magnitude_is_not_a_regression(self):
        """A residual of 3e-6 on a value of 1e5 is 3e-11 relative -- well
        inside float32's ~1e-7, so it is noise, not a regression. The flat
        1.5e-6 tolerance would have called this `structural`."""
        big = "100000.0"
        nudged = f"{100000.0 + 3e-6:.9f}"
        assert abs(float(nudged) - float(big)) > ABS_TOL, "premise: exceeds the flat bound"
        assert classify(big, nudged) == "fp32-noise"

    def test_the_flat_tolerance_would_have_been_wrong(self):
        """States the D-14 bug as an executable claim about the old rule.

        If someone reinstates a flat-only comparison, this is what breaks:
        a large-magnitude fixture passes now and would fail then.
        """
        big, nudged = "100000.0", f"{100000.0 + 3e-6:.9f}"
        would_be_structural = abs(float(big) - float(nudged)) > ABS_TOL
        assert would_be_structural is True
        assert classify(big, nudged) == "fp32-noise"

    def test_a_small_magnitude_residual_is_still_caught(self):
        """Scaled must not mean lenient: near zero the absolute floor binds,
        and a genuine 1.0 difference is structural."""
        assert classify("10.0", "11.0") == "structural"

    def test_the_tolerances_are_the_documented_pair(self):
        assert ABS_TOL == 1.5e-6
        assert REL_TOL == 2e-6

    def test_a_non_numeric_field_that_differs_is_still_noise(self):
        """The companion to the numeric-scope test above.

        Both sides carry one numeric and the same count, so this is
        `fp32-noise` -- see `test_the_comparison_is_numeric_not_textual`
        for why that is the intended behaviour rather than a gap.
        """
        assert classify("1.0 Failed", "1.0 Repaired") == "fp32-noise"

    def test_a_non_numeric_that_parses_as_a_number_is_compared(self):
        """A token the number regex picks up IS compared, so a change there
        is caught."""
        assert classify("1.0 12", "1.0 13") == "structural"


class TestCompareRuns:
    def test_counts_are_reported_per_bucket(self):
        mine = ["1.0 A", "2.0 B", "3.0 C"]
        theirs = ["1.0 A", "2.000000001 B", "9.0 C"]
        counts, first = compare_runs(mine, theirs)
        assert isinstance(counts, dict) and isinstance(first, int | None)
        assert counts["match"] == 1
        assert counts["structural"] == 1
        assert first == 2, "the first structural line is 1-based line 3"

    def test_a_perfect_match_reports_no_structural(self):
        counts, first = compare_runs(["1.0 A"], ["1.0 A"])
        assert counts.get("structural", 0) == 0
        assert first is None

    def test_a_length_mismatch_leaves_the_common_prefix_intact(self):
        """`compare_runs` zips, so extra lines on one side are ignored
        rather than counted as differences.

        Pinned deliberately: a zip-shaped diff means a capture that
        GREW does not fail. That is the behaviour the eleven copies had,
        and changing it would turn many golden comparisons red for
        reasons unrelated to engine correctness.
        """
        counts, first = compare_runs(["1.0 A"], ["1.0 A", "2.0 B"])
        assert counts["match"] == 1
        assert first is None

    def test_it_compares_the_overlapping_prefix_zip_style(self):
        counts, _ = compare_runs(["1.0 A", "9.0 Z"], ["1.0 A"])
        assert counts.get("structural", 0) == 0

    def test_the_namedtuple_shape_is_the_one_callers_expect(self):
        """The per-file `_compare` helpers returned `(counts, first)`; the
        callers were rewritten to this shape, so it is pinned."""
        result = compare_runs(["1.0 A"], ["1.0 A"])
        assert isinstance(result, Comparison)
        counts, first = result
        assert counts is result.counts
        assert first is result.first_structural


class TestRunBoundaries:
    """Two capture shapes, one `landmark` parameter.

    `LANDMARK_BEGIN_RUN` splits a WHOLE-SIMULATION log: the landmark line
    is the run header and is dropped, runs run from one landmark to the
    next (last to EOF), and `num_runs=` asserts the count.

    `LANDMARK_END_SIM` splits a PER-BLOCK log: there is no header, the
    landmark is the run's own terminator and IS kept, a trailing
    unterminated run is dropped, and `max_runs=` stops early.

    These were two implicit conventions living in one file across eleven
    copies. They are now named, and each call site declares which it
    means -- so the fixture below is built to be valid under BOTH.
    """
    TEXT = [
        "0.000000 System Begin_Run_1",
        "0.100000 Block A Failed",
        "1.000000 Block A End_sim",
        "0.000000 System Begin_Run_2",
        "0.200000 Block A Failed",
        "1.000000 Block A End_sim",
    ]

    def test_end_sim_splits_two_runs_and_keeps_the_terminator(self):
        runs = split_runs(self.TEXT, LANDMARK_END_SIM)
        assert len(runs) == 2
        assert any("End_sim" in l for l in runs[0]), (
            "the terminator line is kept under the End_sim shape")

    def test_begin_run_splits_two_runs_and_drops_the_header(self):
        runs = split_runs(self.TEXT, LANDMARK_BEGIN_RUN)
        assert len(runs) == 2
        assert not any("Begin_Run" in l for l in runs[0]), (
            "the header line is dropped under the Begin_Run shape")

    def test_the_two_landmarks_are_distinct_constants(self):
        assert LANDMARK_END_SIM != LANDMARK_BEGIN_RUN
        assert "End_sim" in LANDMARK_END_SIM
        assert "Begin_Run" in LANDMARK_BEGIN_RUN

    def test_max_runs_stops_the_terminator_shape_early(self):
        assert len(split_runs(self.TEXT, LANDMARK_END_SIM, max_runs=1)) == 1

    def test_num_runs_asserts_the_header_shape(self):
        assert len(split_runs(self.TEXT, LANDMARK_BEGIN_RUN, num_runs=2)) == 2
        import pytest
        with pytest.raises(AssertionError):
            split_runs(self.TEXT, LANDMARK_BEGIN_RUN, num_runs=5)

    def test_a_trailing_unterminated_run_is_dropped(self):
        """End_sim shape only: a run that never reaches its terminator is
        not a complete run, so it is discarded rather than reported short."""
        truncated = self.TEXT[:-1]
        assert len(split_runs(truncated, LANDMARK_END_SIM)) == 1

    def test_blank_lines_are_dropped_unless_asked(self):
        padded = ["", "0.0 Block A", "", "1.0 Block A End_sim", ""]
        assert all(l.strip() for r in split_runs(padded, LANDMARK_END_SIM)
                   for l in r)
        kept = split_runs(padded, LANDMARK_END_SIM, keep_blank=True)
        assert any(not l.strip() for r in kept for l in r)

    def test_an_empty_log_yields_no_runs(self):
        assert split_runs([], LANDMARK_END_SIM) == []


class TestReadLines:
    """Legacy captures are latin-1 and CRLF, and that is load-bearing.

    latin-1 is a bijection on bytes, so a byte-level `b"Block" in line`
    and a str-level `"Block" in line` select the same lines -- the eleven
    copies each did one or the other. Splitting on `b"\r\n"` rather than
    `splitlines()` keeps a lone `\r` inside a field as data.
    """

    #: A CRLF-terminated fixture ends in `\r\n`, so a literal split on
    #: `b"\r\n"` yields a trailing EMPTY element. `read_lines` returns it
    #: as-is; `split_runs` is what drops blanks. Pinned because eleven
    #: copies each handled the trailing element differently, and a caller
    #: indexing the last line would otherwise be one fixture short.
    def test_a_crlf_file_keeps_its_trailing_empty_element(self, tmp_path):
        p = tmp_path / "log.txt"
        p.write_bytes(b"0.0 Block Pump\r\n")
        assert read_lines(p) == ["0.0 Block Pump", ""]

    def test_gzip_is_transparent(self, tmp_path):
        import gzip
        p = tmp_path / "log.txt.gz"
        with gzip.open(p, "wb") as f:
            f.write(b"0.0 Block Pump\r\n0.1 Block Pump Failed\r\n")
        assert read_lines(p) == ["0.0 Block Pump", "0.1 Block Pump Failed", ""]

    def test_the_keep_predicate_filters(self, tmp_path):
        p = tmp_path / "log.txt"
        p.write_bytes(b"0.0 Block A\r\n0.0 System X\r\n0.1 Block B\r\n")
        # `keep` also drops the trailing empty element, since "" contains
        # no "Block" -- which is why `read_lines` needs no blank handling of
        # its own.
        assert read_lines(p, keep=lambda l: "Block" in l) == ["0.0 Block A", "0.1 Block B"]

    def test_keep_filters_identically_with_and_without_gzip(self, tmp_path):
        """latin-1 is a bijection, so a byte test and a str test select the
        same lines -- one of the reasons the eleven copies agreed."""
        import gzip
        plain = tmp_path / "a.txt"
        plain.write_bytes(b"0.0 Block A\r\n0.0 System X\r\n")
        packed = tmp_path / "a.txt.gz"
        with gzip.open(packed, "wb") as f:
            f.write(b"0.0 Block A\r\n0.0 System X\r\n")
        keep = lambda l: "Block" in l  # noqa: E731
        assert read_lines(plain, keep=keep) == read_lines(packed, keep=keep)

    def test_high_bytes_survive(self, tmp_path):
        """latin-1 round-trips every byte, which is why it is used."""
        p = tmp_path / "log.txt"
        p.write_bytes(b"0.0 Block \xff\xfe\r\n")
        assert read_lines(p) == ["0.0 Block \xff\xfe", ""]

    def test_a_missing_capture_skips_by_default(self, tmp_path):
        import pytest
        with pytest.raises(Exception) as exc:
            read_lines(tmp_path / "absent.txt")
        # A missing optional fixture is a skip, not a failure.
        assert "Skipped" in type(exc.value).__name__ or isinstance(
            exc.value, pytest.skip.Exception)

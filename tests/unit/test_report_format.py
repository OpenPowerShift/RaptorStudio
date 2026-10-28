"""Unit tests for `report_format.py`'s formatters, checked against
hand-computed examples and real captured report values."""
from __future__ import annotations

import pytest

from raptor.core.engine.report_format import (
    format_stats, get_display_str, int_to_str, pop_to_sample,
    real_to_cost, real_to_sig_fig_str, real_to_sn, real_to_str,
)


def test_format_stats_exact_one():
    assert format_stats(1.0) == "1.000000000"


def test_format_stats_real_capture_values():
    # tests/golden/TwoBlockRedundancy/legacy/rpt_input_two_results.txt
    assert format_stats(0.731144122) == "0.731144122"
    assert format_stats(0.757501689) == "0.757501689"
    assert format_stats(0.791644152) == "0.791644152"


def test_format_stats_carry_quirk():
    # A value that rounds up to exactly 1.0 at the 9th decimal must
    # still print "1.000000000", not "2.000000000" -- the `SUBSTR(1,1,...)
    # = "2"` carry check in Raptor 7.
    assert format_stats(0.9999999996) == "1.000000000"


def test_real_to_sig_fig_str_basic():
    assert real_to_sig_fig_str(0.531123, 6) == "0.531123"
    assert real_to_sig_fig_str(100.0, 6) == "100.000000"
    assert real_to_sig_fig_str(-0.212608, 6) == "(0.212608)"


def test_real_to_sig_fig_str_round_half_up():
    # 0.125 rounded to 2 places should round the trailing 5 up (Modsim's
    # manual round-half-up loop), not banker's-round (which would give
    # "0.12"). Exactly representable in binary, unlike 0.1234565.
    assert real_to_sig_fig_str(0.125, 2) == "0.13"


def test_real_to_sn_no_padding_needed():
    # tests/golden/TwoBlockRedundancy: StDev/SEM columns are width 11,
    # 9 sig figs -- too narrow for scientific notation (11 < 9+6).
    assert real_to_sn(0.018130457, 11, 9, 1) == "0.018130457"
    assert real_to_sn(0.004054093, 11, 9, 1) == "0.004054093"


def test_real_to_sn_padding():
    assert real_to_sn(1.5, 10, 2, 1) == "      1.50"
    assert real_to_sn(1.5, 10, 2, 0) == "1.50      "


def test_pop_to_sample():
    # sample stdev is always >= population stdev for n>1.
    sigma_pop = 0.018130457 / (20 / 19) ** 0.5
    assert abs(pop_to_sample(sigma_pop, 20) - 0.018130457) < 1e-9


def test_get_display_str_na_below_two_samples():
    assert get_display_str(0.5, 0, 11, 9, 1, "stdev") == "N/A"
    assert get_display_str(0.5, 1, 11, 9, 1, "stdev") == "N/A"


def test_real_to_str_and_int_to_str():
    assert real_to_str(100.0) == "100.000000"
    assert real_to_str(0.531123) == "0.531123"
    assert int_to_str(97) == "97"


# --- REALTOCOST ----------------------------------------------------------
#
# No real capture exercises this formatter (it drives GUI cost-analysis
# displays, not the text reports), so every expectation below is hand-
# traced through Raptor 7 statement by statement. Each test names
# the source rung it pins.


@pytest.mark.parametrize(("val", "str_size", "expected"), [
    # The three sub-$1000 rungs share one branch and never consult
    # `fullLength`, so the plain `$d.cc` form always wins.
    (0.5, 7, "$0.50"),
    (9.99, 7, "$9.99"),
    (12.5, 7, "$12.50"),
    (999.99, 7, "$999.99"),
])
def test_real_to_cost_below_thousand_never_abbreviates(val, str_size, expected):
    assert real_to_cost(val, str_size, 3) == expected


@pytest.mark.parametrize(("val", "str_size", "expected"), [
    # $x,xxx.xx, fullLength 9
    (1234.5678, 9, "$1,234.57"),
    (1234.5678, 12, "$1,234.57"),
    # $xx,xxx.xx, fullLength 10
    (12345.67, 10, "$12,345.67"),
    # $xxx,xxx.xx, fullLength 11
    (123456.78, 11, "$123,456.78"),
    # $x,xxx,xxx.xx, fullLength 13
    (1234567.89, 13, "$1,234,567.89"),
    # $x,xxx,xxx,xxx.xx, fullLength 17
    (1234567890.12, 17, "$1,234,567,890.12"),
])
def test_real_to_cost_full_comma_grouped_form(val, str_size, expected):
    assert real_to_cost(val, str_size, 3) == expected


@pytest.mark.parametrize(("val", "str_size", "expected"), [
    # Rule 3 of the source's own header: K/M/B truncate, never round.
    # Each case is one character too narrow for its comma-grouped form.
    # `$1234.57` -> digits "123457", lastDigitPos = 1+8-4 = 5.
    (1234.5678, 8, "$1.2345K"),
    # `$12345.67` -> digits "1234567", lastDigitPos = 2+9-5 = 6.
    (12345.67, 9, "$12.3456K"),
    # `$123456.78` -> digits "12345678", lastDigitPos = 3+10-6 = 7.
    (123456.78, 10, "$123.4567K"),
    # M rung: `$12345678.90` -> digits "1234567890", lastDigitPos = 7.
    (12345678.9, 10, "$12.34567M"),
    # B rung: 1e11 -> digits "10000000000000", lastDigitPos = 3+12-6 = 9.
    (100000000000.0, 12, "$100.000000B"),
])
def test_real_to_cost_magnitude_suffix_truncates(val, str_size, expected):
    assert real_to_cost(val, str_size, 3) == expected


def test_real_to_cost_truncation_is_not_rounding():
    """`$1234.57` in an 8-wide field keeps `2345`, not the rounded
    `2346` -- the source's truncation rule."""
    assert real_to_cost(1234.5678, 8, 3) == "$1.2345K"
    assert "2346" not in real_to_cost(1234.5678, 8, 3)


def test_real_to_cost_cents_carry():
    """Cents rounding to 100 carries a whole dollar and re-derives
    `dollarString`, so 999.995 crosses into the $1,000 rung."""
    assert real_to_cost(999.995, 10, 3) == "$1,000.00"


def test_real_to_cost_negative_wraps_in_parens_and_costs_two_chars():
    # A negative subtracts 2 from `maxSize`, so the same value that fits
    # its comma-grouped form at 9 wide when positive needs 11 when
    # negative.
    assert real_to_cost(-1234.56, 11, 3) == "($1,234.56)"
    # At 9 wide the negative's own maxSize is 7, below the rung's
    # fullLength of 9, so it falls back to K: lastDigitPos = 1+7-4 = 4.
    assert real_to_cost(-1234.56, 9, 3) == "($1.234K)"


def test_real_to_cost_narrow_field_hashes_at_full_str_size():
    """The filler is `SUBSTR(1, strSize, ...)`, using `strSize` rather
    than the negative-adjusted `maxSize`. So a negative in an 8-wide field
    (maxSize 6, below 7) hashes to eight characters."""
    assert real_to_cost(1.0, 6, 3) == "######"
    assert real_to_cost(-1.0, 8, 3) == "########"
    # The widest field that can ever reach this branch is 8 (a negative,
    # whose maxSize of 6 is the largest still below 7), so source's
    # ten-character `"##########"` literal is never actually exhausted
    # and the filler always spans the full field.
    assert real_to_cost(-1.0, 14, 3) == "($1.00)"


def test_real_to_cost_above_one_trillion():
    # Guards on `inVal`, before any rung is chosen.
    assert real_to_cost(1e12, 20, 3) == ">1T"
    assert real_to_cost(999999999999.996, 20, 3) == ">1T"


def test_real_to_cost_justification():
    # Identical padding machinery to REALTOSN: an odd remainder puts its
    # extra space on the right for left/right justification and on the
    # left for centred.
    assert real_to_cost(0.5, 10, 0) == "$0.50     "
    assert real_to_cost(0.5, 10, 1) == "     $0.50"
    assert real_to_cost(0.5, 10, 2) == "   $0.50  "
    assert real_to_cost(0.5, 10, 3) == "$0.50"


def test_real_to_cost_overlong_is_returned_untouched():
    """Unlike `real_to_sn`, REALTOCOST has no `#`-fill for an overlong
    result -- the `ELSE outVal:=outString` branch returns it as-is.
    `$999.99` is 7 characters in a 7-wide field, and `$12,345.67` is 10
    in a field of 7 once the rung's own fallback is ruled out."""
    assert real_to_cost(999.99, 7, 1) == "$999.99"
    assert len(real_to_cost(1234567.89, 13, 1)) == 13


def test_auto_stopped_censors_mdt_and_mmt() -> None:
    """An `autoStopped` FailSim run scales MDT and the MMT means by
    n/(n-1), because the last down period is unfinished. Hand-computed."""
    from raptor.core.engine.reporting import build_run_report_data
    from raptor.core.engine.system_status import SystemState

    st = SystemState()
    for v in (10.0, 20.0, 30.0):
        st.red_stat.add(v)
    st.green_stat.add(100.0)
    for v in (2.0, 4.0):
        st.comp_repair_stat.add(v)
    plain = build_run_report_data(1, 100.0, 100.0, st, [], termination_type="FailSim")
    cens = build_run_report_data(1, 100.0, 100.0, st, [], termination_type="FailSim", auto_stopped=True)
    assert plain.mdt == 20.0 and cens.mdt == 20.0 * 3 / 2
    assert plain.mmtu == 3.0 and cens.mmtu == 3.0 * 2 / 1

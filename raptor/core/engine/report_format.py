"""Number-formatting helpers for the `End_Of_Run`/`Final_Results_Report`
text output, ported line-for-line from Raptor 7. Pure string
formatting, no engine state -- independently testable against hand-computed
examples.

Scope: the formatters used by the SUMMARY/LOGISTICS/SPARING sections, plus
`REALTOCOST`, the currency formatter the GUI's cost-analysis displays use.
`COSTTOREAL` (its inverse) is out of scope -- it
parses a formatted string back out of a GUI text field, which has no
counterpart here.
"""
from __future__ import annotations

import math
from raptor.core.engine.errors import EngineInvariantError
import itertools
import decimal


def real_to_sig_fig_str(val: float, sig_fig: int) -> str:
    """Port of `REALTOSigFigSTR`: a manual
    round-half-up fixed-decimal formatter (NOT Python's round-half-even
    `round()`), negative values get `(...)` parenthesization instead of
    a leading `-`."""
    if sig_fig < 1:
        return str(round(val))

    negative = val < 0.0
    if negative:
        val = -val

    whole_as_int = math.trunc(val)
    frac_as_real = val - float(math.trunc(val))
    digits = [0] * (sig_fig + 1)
    for i in range(sig_fig + 1):
        frac_as_real *= 10.0
        digits[i] = math.trunc(frac_as_real)
        frac_as_real -= float(math.trunc(frac_as_real))

    if digits[sig_fig] >= 5:
        i = sig_fig - 1
        rounding = True
        while rounding:
            if digits[i] < 9:
                digits[i] += 1
                rounding = False
            else:
                digits[i] = 0
                if i == 0:
                    whole_as_int += 1
                    rounding = False
                else:
                    i -= 1

    sig_fig_str = str(whole_as_int) + "." + "".join(str(d) for d in digits[:sig_fig])
    if negative:
        sig_fig_str = f"({sig_fig_str})"
    return sig_fig_str


def real_to_sn(val: float, str_size: int, sig_fig: int, justification: int) -> str:
    """Port of `REALTOSN`: fixed-decimal
    formatting via `real_to_sig_fig_str`, falling back to scientific
    notation only when the field is wide enough (`str_size>=sig_fig+6`)
    AND the value is outside the field's own representable fixed-point
    range. `justification`: 0=left, 1=right, 2=center, 3=none."""
    if val != val or val in (float("inf"), float("-inf")):
        # A GUI Stop can abort mid-run, leaving a monitor mean as
        # 0/0 -> nan or x/0 -> inf. Raptor never formatted such values, and
        # math.trunc(log10(inf)) raises OverflowError; render the field's
        # zero-value convention instead (the aborted run's report is discarded
        # by the caller anyway).
        val = 0.0
    negative = val < 0.0
    num_length = str_size
    if negative:
        val = -val
        num_length -= 2
    if sig_fig > 10:
        sig_fig = 10
    if (num_length - sig_fig) > 10:
        num_length = 10 + sig_fig
    can_use_sn = num_length >= (sig_fig + 6)

    pre_dot = num_length - sig_fig - 1
    too_big_level = sum(9.0 * 10.0 ** i for i in range(pre_dot))
    too_big_level += sum(9.0 * 10.0 ** (-i) for i in range(1, sig_fig + 1))
    too_small_level = 10.0 ** (-sig_fig)

    if val > 0.0 and (val > too_big_level or val < too_small_level) and can_use_sn:
        post_dot = num_length - 6 if num_length <= 15 else 9
        log_val = math.log10(val)
        exponent = math.trunc(log_val) if val > 1.0 else math.floor(log_val)
        prefix_val = val / (10.0 ** exponent)
        int_part = str(math.trunc(prefix_val))
        dec_part = real_to_sig_fig_str(prefix_val - float(math.trunc(prefix_val)), post_dot)
        if dec_part[0:1] == "1":
            if int_part == "9":
                int_part = "1"
                exponent += 1
            else:
                int_part = str(int(int_part) + 1)
        dec_part = dec_part[1:]
        if exponent < -99:
            suffix = "E-99"
        elif exponent < -9:
            suffix = f"E{exponent}"
        elif exponent < 0:
            suffix = f"E-0{abs(exponent)}"
        elif exponent < 10:
            suffix = f"E+0{exponent}"
        elif exponent < 100:
            suffix = f"E+{exponent}"
        else:
            suffix = "E+99"
        if exponent < -99:
            out_val = "<1E-99"
        elif exponent < 100:
            out_val = int_part + dec_part + suffix
        else:
            out_val = ">1E+99"
    else:
        out_val = real_to_sig_fig_str(val, sig_fig)

    if negative:
        out_val = f"({out_val})"

    out_size = len(out_val)
    if out_size < str_size:
        pad = str_size - out_size
        odd_space = " " if pad % 2 else ""
        if odd_space:
            pad -= 1
        spaces = " " * (pad // 2)
        if justification == 0:
            prefix, suffix = "", spaces + spaces + odd_space
        elif justification == 1:
            prefix, suffix = spaces + spaces + odd_space, ""
        elif justification == 2:
            prefix, suffix = spaces + odd_space, spaces
        else:
            prefix, suffix = "", ""
        out_val = prefix + out_val + suffix
    elif out_size > str_size:
        out_val = "#" * str_size

    return out_val


def pop_to_sample(sigma_pop: float, n: int) -> float:
    """Port of `PopToSample`: converts a
    population standard deviation (`RStatObj.StdDev`'s own N-divisor
    convention) to a sample standard deviation."""
    return sigma_pop * math.sqrt(float(n) / float(n - 1))


def get_display_str(new_real: float, count: int, a: int, b: int, c: int, key: str) -> str:
    """Port of `GetDisplayStr`: formats a
    StDev (`key="stdev"`) or SEM (`key="sem"`) column, `"N/A"` when
    `count<2` (not just `count==0`)."""
    if count < 2:
        return "N/A"
    sigma = pop_to_sample(new_real, count)
    if key == "stdev":
        return real_to_sn(sigma, a, b, c)
    if key == "sem":
        return real_to_sn(sigma / math.sqrt(float(count)), a, b, c)
    raise EngineInvariantError(key)  # pragma: no cover


def format_stats(stat: float) -> str:
    """Port of `FormatStats`: always-9-
    fractional-digit formatter used only for Availability/Reliability/
    ConditionalReliability/Dependability. The `SUBSTR(1,1,...)="2"`
    check is a real carry-quirk in the source (catches ONLY the specific
    case where rounding `(1.0+stat)*1e9` carries into a leading "2",
    i.e. `stat` rounds up to exactly `1.0`) -- kept as-is to stay
    byte-exact."""
    if stat == 1.0:
        return "1.000000000"
    rounded = str(round((1.0 + stat) * 1_000_000_000.0))
    if rounded[0:1] == "2":
        return "1.000000000"
    return "0." + rounded[1:10]


def write_real(val: float, dec: int) -> str:
    """MODSIM `WriteReal(val, w, dec)`'s digits: the exact binary value
    rounded half AWAY from zero to `dec` places (Python's format rounds an
    exact half to even: 0.1640625 -> "0.164062", Raptor 7 prints "0.164063" --
    ReleaseProbe25's availability plot). Width padding is the caller's."""
    q = decimal.Decimal(val).quantize(decimal.Decimal(1).scaleb(-dec), rounding=decimal.ROUND_HALF_UP)
    return f"{q:.{dec}f}"


def real_to_str(val: float) -> str:
    """Port of `REALTOSTR` (Modsim runtime primitive, no source in this
    tree) -- a fixed 6-decimal format, matching what every captured report
    field and `eventlog.format_event_log_line` render for the same underlying
    Modsim call."""
    return f"{val:.6f}"


def int_to_str(val: int) -> str:
    """Port of `INTTOSTR` (Modsim runtime primitive) -- plain decimal."""
    return str(val)


#: `SUBSTR(1, strSize, "##########")` -- the overflow filler REALTOCOST
#: returns when the field is too narrow to hold even `$x.xx`
#: Ten characters is source's own literal and
#: that is always enough: the branch needs `maxSize < 7`, and `maxSize`
#: is `strSize` (positive) or `strSize - 2` (negative), so the widest
#: field that can reach it is 8.
_COST_HASHES = "##########"


def _substr(start: int, end: int, s: str) -> str:
    """MODSIM `SUBSTR(IN start, IN end, IN s)` -- 1-based, INCLUSIVE of
    both endpoints, and empty when `start > end`. Python's own slice
    semantics already clamp an `end` past the string's length, matching
    the source uses here (all of which index within `s`)."""
    if start > end:
        return ""
    return s[start - 1:end]


def _round_half_away(val: float) -> int:
    """MODSIM `ROUND` -- Modula-2 lineage, so half rounds AWAY from zero,
    not to even the way Python's own `round()` does. Only ever called
    here on a non-negative value (REALTOCOST flips the sign first)."""
    return math.floor(val + 0.5) if val >= 0 else -math.floor(-val + 0.5)


def real_to_cost(val: float, str_size: int, justification: int) -> str:
    """Port of `REALTOCOST` -- currency
    formatting for the GUI's cost-analysis displays.

    Source's own documented rules, quoted from its header comment:

    1. `str_size` must be >= 7 for positive numbers, >= 9 for negative
       ones (a negative costs two extra characters for its parentheses).
       Narrower fields get a row of `#`.
    2. `justification`: 0=left, 1=right, 2=center, 3=no padding.
    3. "If K, B or M are used values are substringed, not rounded" --
       i.e. the magnitude suffixes TRUNCATE. `$1234.57` rendered into a
       field too narrow for `$1,234.57` becomes `$1.2345K`, not
       `$1.235K`.

    The structure is a ladder over `dollarReal`'s magnitude. Each rung
    knows its own `fullLength` (the width of the fully comma-grouped
    `$x,xxx.xx` form) and falls back to a truncated K/M/B form only when
    that does not fit the field. Below $1000 there is no fallback at all
    -- source sets `fullLength` on those rungs but never tests it.

    Faithfully reproduced quirks, all deliberate:

    - The too-narrow `#` filler uses `strSize`, NOT the negative-adjusted
      `maxSize`, so a negative value in a narrow field is hashed to the
      FULL field width.
    - `dollarReal >= 1e12` leaves `outString` never assigned, so source
      returns padding only. The `>1T` guard above it
       tests `inVal`, not `dollarReal`, which is what makes
      the branch all but unreachable rather than strictly dead.
    - When the rendered string is already at least `str_size` wide it is
      returned UNPADDED and UNTRUNCATED -- unlike `real_to_sn`, which
      hashes an overlong field.
    """
    negative = False
    max_size = str_size
    if val < 0.0:
        negative = True
        val = -val
        max_size -= 2

    temp_string = real_to_str(val)
    dot_position = temp_string.find(".") + 1  # MODSIM POSITION: 1-based, 0 if absent
    dollar_string = _substr(1, dot_position - 1, temp_string)
    dollar_real = float(dollar_string)
    cents = _round_half_away((val - dollar_real) * 100.0)
    if cents == 100:
        # `.995`-and-up rounded the cents into a whole dollar: carry it,
        # then re-derive `dollarString` from the incremented value
        # .
        dollar_real += 1.0
        cents = 0
        temp_string = real_to_str(dollar_real)
        dot_position = temp_string.find(".") + 1
        dollar_string = _substr(1, dot_position - 1, temp_string)
    cent_string = f"0{cents}" if cents < 10 else str(cents)

    if max_size < 7:
        return _substr(1, str_size, _COST_HASHES)
    if val > 999999999999.995:
        return ">1T"

    out_string = ""
    if dollar_real < 10.0 or dollar_real < 100.0 or dollar_real < 1000.0:
        # -- three separate rungs in source, each setting a
        # different (unused) `fullLength` and the SAME `outString`.
        out_string = f"${dollar_string}.{cent_string}"
    else:
        # Each remaining rung is (limit, full_length, group_widths,
        # suffix): `group_widths` are the comma-separated digit-group
        # end positions of the full form, and `suffix` is the magnitude
        # letter its truncated fallback uses.
        ladder = (
            (10000.0, 9, (1, 4), "K"),
            (100000.0, 10, (2, 5), "K"),
            (1000000.0, 11, (3, 6), "K"),
            (10000000.0, 13, (1, 4, 7), "M"),
            (100000000.0, 14, (2, 5, 8), "M"),
            (1000000000.0, 15, (3, 6, 9), "M"),
            (10000000000.0, 17, (1, 4, 7, 10), "B"),
            (100000000000.0, 18, (2, 5, 8, 11), "B"),
            (1000000000000.0, 19, (3, 6, 9, 12), "B"),
        )
        for limit, full_length, groups, suffix in ladder:
            if dollar_real >= limit:
                continue
            lead = groups[0]
            if full_length <= max_size:
                parts = [_substr(1, lead, dollar_string)]
                for start, end in itertools.pairwise(groups):
                    parts.append(_substr(start + 1, end, dollar_string))
                out_string = "$" + ",".join(parts) + "." + cent_string
            else:
                # The K/M/B fallback: dollars and cents concatenated into
                # ONE digit run, then cut -- which is why it truncates
                # rather than rounds. `lastDigitPos := lead + maxSize -
                # (lead + 3)`, i.e. the field width less the `$`, the
                # `.`, the suffix letter and the leading digit group.
                digits = dollar_string + cent_string
                last_digit_pos = lead + max_size - (lead + 3)
                if last_digit_pos >= len(digits):
                    last_digit_pos = len(digits)
                out_string = (
                    "$" + _substr(1, lead, digits) + "."
                    + _substr(lead + 1, last_digit_pos, digits) + suffix
                )
            break

    if negative:
        out_string = f"({out_string})"

    num_chars = len(out_string)
    if num_chars >= str_size:
        return out_string

    num_left = str_size - num_chars
    odd_space = " " if num_left % 2 else ""
    if odd_space:
        num_left -= 1
    spaces = " " * (num_left // 2)
    if justification == 0:
        prefix, suffix_pad = "", spaces + spaces + odd_space
    elif justification == 1:
        prefix, suffix_pad = spaces + spaces + odd_space, ""
    elif justification == 2:
        prefix, suffix_pad = spaces + odd_space, spaces
    else:
        prefix, suffix_pad = "", ""
    return prefix + out_string + suffix_pad


def ao_value(v: float) -> str:
    """`WriteReal(v,8,6)` digits (half away from zero), see `write_real`."""
    return write_real(v, 6)


def ao_time_column(t: float) -> str:
    """Ao-plot time column: `real_to_str` right-trimmed into a fixed
    20-char field plus its own two-space separator ('s
    `SUBSTR(1,20,...)+"  "`). Emitted by both the interval process (kickoff)
    and the run-end row (finalize)."""
    return (real_to_str(t) + " " * 20)[:20] + "  "

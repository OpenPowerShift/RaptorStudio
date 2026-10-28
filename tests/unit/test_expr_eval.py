"""Safe arithmetic expression evaluator for formula/rate value fields.

Keeps a failure rate linked to its km/yr basis instead of flattening it to
a number. Pure Python, no PySide6."""
from __future__ import annotations

import pytest

from raptor.gui.ui.expr_eval import (
    ExpressionError, eval_expression, is_bare_rate_ref, is_plain_number,
    resolve,
)


def test_plain_number_and_arithmetic():
    assert eval_expression("3") == 3.0
    assert eval_expression("-5") == -5.0
    assert eval_expression("(1+2)*3") == 9.0
    assert eval_expression("2**3") == 8.0


def test_basis_comment_stripped_but_not_evaluated():
    expected = 0.00015 * 20000 / 8760
    text = "0.00015 * 20000 / 8760  # 0.00015 fail/km, 20000 km/yr"
    assert abs(eval_expression(text) - expected) < 1e-12


def test_multiple_summed_segments():
    a, b = 0.00015 * 15000 / 8760, 0.0003 * 5000 / 8760
    text = "(0.00015 * 15000/8760) + (0.0003 * 5000/8760)  # two segments"
    assert abs(eval_expression(text) - (a + b)) < 1e-12


@pytest.mark.parametrize("bad", [
    "import os",
    "__import__('os')",
    "os.system('ls')",
    "open('x')",
    "abc",
    "",
    "   ",
    "2+",
    "2**",
    "1/0",
])
def test_rejects_anything_not_pure_arithmetic(bad):
    """The whole point: a value field is untrusted, user-authored text.
    Nothing here may execute code, import, call, or read attributes --
    only numeric literals and +-*/() ever reach `_eval_node`."""
    with pytest.raises(ExpressionError):
        eval_expression(bad)


def test_resolve_rate_reference():
    rates = [{"name": "Highway", "value": 1.71},
            {"name": "City", "value": 3.2}]
    assert resolve("@Highway", rates) == 1.71
    assert resolve("@City", rates) == 3.2
    assert resolve("2 + 2", rates) == 4.0


def test_resolve_unknown_rate_raises():
    with pytest.raises(ExpressionError):
        resolve("@Nope", [{"name": "Highway", "value": 1.0}])


def test_resolve_with_no_rates_still_does_arithmetic():
    assert resolve("3 * 4", None) == 12.0
    with pytest.raises(ExpressionError):
        resolve("@Anything", None)


def test_resolve_embedded_rate_reference_in_arithmetic():
    """`@RateName` is not only valid as the whole field; it can sit
    inside a larger expression too."""
    rates = [{"name": "Base", "value": 0.5}, {"name": "Other", "value": 2.0}]
    assert resolve("@Base*1.4", rates) == 0.5 * 1.4
    assert resolve("@Base * 1.4", rates) == 0.5 * 1.4
    assert resolve("2 * @Base + @Other", rates) == 2 * 0.5 + 2.0
    assert resolve("(@Base + 1) * 2", rates) == (0.5 + 1) * 2


def test_resolve_embedded_unknown_rate_raises():
    with pytest.raises(ExpressionError):
        resolve("@Nope * 2", [{"name": "Base", "value": 1.0}])


def test_resolve_comment_at_sign_not_mistaken_for_rate_token():
    """A "#" comment containing "@" (e.g. referencing an external doc)
    must not be treated as a rate reference -- the comment is stripped
    BEFORE substitution, not after."""
    assert resolve("1.5  # see @SomeExternalDoc for basis", None) == 1.5


def test_is_bare_rate_ref():
    assert is_bare_rate_ref("@Highway") == "Highway"
    assert is_bare_rate_ref("  @Highway  ") == "Highway"
    assert is_bare_rate_ref("@Highway * 1.4") is None
    assert is_bare_rate_ref("2 * @Highway") is None
    assert is_bare_rate_ref("Highway") is None
    assert is_bare_rate_ref("") is None


def test_is_plain_number():
    assert is_plain_number("3.5")
    assert is_plain_number("-2")
    assert is_plain_number("  4  ")
    assert not is_plain_number("3.5 * 2")
    assert not is_plain_number("@Highway")
    assert not is_plain_number("")


@pytest.mark.parametrize("bad", [
    "9**9**9**9",                 # OverflowError
    "1" + "+1" * 3000,            # RecursionError from ast.parse
    "1e308*10",                   # overflow to inf
    "(1e308*10)-(1e308*10)",      # inf - inf = nan
    "2**2000",                    # exponent cap
])
def test_rejects_overflow_and_non_finite(bad):
    """Overflow and DoS vectors: an OverflowError or RecursionError must
    not escape the caller, and inf/nan must not pass through (NaN would
    slip past `validate_distro_params`, whose comparisons are all False
    against NaN). All must surface as ExpressionError."""
    with pytest.raises(ExpressionError):
        eval_expression(bad)


def test_resolve_rejects_non_finite_and_bad_rate_values():
    with pytest.raises(ExpressionError):
        resolve("1e308*10")
    with pytest.raises(ExpressionError):
        resolve("@Inf", [{"name": "Inf", "value": float("inf")}])
    with pytest.raises(ExpressionError):
        resolve("2**2000")

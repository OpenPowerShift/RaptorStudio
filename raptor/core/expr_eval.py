"""Safe arithmetic expressions and `@RateName` references for numeric
value fields.

`resolve` tries two things in order:

1. `@RateName` -- a reference to an entry in `RbdFile.rates` (the Rates
   library, `rates_dialog.py`), resolving to that rate's `value`. A
   reference may be embedded in a larger expression (`@Rate * 1.4`,
   `@A + @B`, `2 * @Rate - 1`): `@RateName` tokens are substituted with
   the referenced rate's `value` before arithmetic evaluation. Each
   reference reads the other rate's already-computed `value`, not a
   live recursive re-evaluation of its own expression, so a cycle
   cannot loop forever -- worst case one side is a step stale until
   re-committed.
2. A restricted arithmetic expression: numeric literals, `+ - * / ( )`
   and unary +/-, nothing else. `#` starts a trailing comment (the
   human-readable basis, e.g. "0.00015 * 20000 / 8760  # 0.00015
   fail/km, 20000 km/yr"), kept verbatim in the stored text so
   reopening the field shows the original derivation, but never
   evaluated.

The field text is user-authored and untrusted, so this is deliberately
not `eval()`: the AST walker accepts only the numeric-expression node
types above, with no Name/Call/Attribute/Subscript/Compare and no path
to executing code, importing modules, or reading other data.
`@RateName` substitution is a text rewrite before parsing (each
reference becomes a plain numeric literal), so the walker's own
guarantee -- only numbers and +-*/() -- still holds for whatever
reaches it.
"""
from __future__ import annotations

import ast
import math
import re

#: A bare `@RateName` reference and nothing else -- the whole field is
#: just this, no surrounding arithmetic. Distinguished from an embedded
#: reference because it is stored and redisplayed differently
#: (`{"rate": name}` vs `{"expr": text}` in `RbdFile.formulas`).
_BARE_RATE_RE = re.compile(r"^@(\w+)$")
#: Any `@RateName` token, for substitution inside a larger expression.
_RATE_TOKEN_RE = re.compile(r"@(\w+)")

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)

#: Reject absurdly long input before `ast.parse` recurses on it. A
#: chain like "1+1+1+..." x3000 makes CPython's parser raise
#: RecursionError, which callers do not catch.
_MAX_EXPR_LEN = 10_000
#: Cap `**` exponents so `9**9**9**9` cannot build an astronomically
#: large exponent (OverflowError), keeping the accepted surface close to
#: the documented "+ - * / ( )".
_MAX_POW_EXP = 1024.0


class ExpressionError(ValueError):
    """Raised for text that isn't a plain number, a `@RateName`
    reference, or a valid restricted arithmetic expression."""


def _strip_comment(text: str) -> str:
    """Text before the first '#' -- a bare '#' with nothing before it
    (or an empty string) is not a comment, just invalid input, so the
    caller's own parse failure reports that clearly rather than this
    silently returning an empty expression."""
    i = text.find("#")
    return text if i < 0 else text[:i]


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)):
            raise ExpressionError(f"not a number: {node.value!r}")
        return float(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ExpressionError("division by zero")
            return left / right
        if isinstance(node.op, ast.Pow):
            if abs(right) > _MAX_POW_EXP:
                raise ExpressionError("exponent is too large")
            if left == 0 and right < 0:
                raise ExpressionError("division by zero")
            try:
                result = left ** right
            except OverflowError:
                raise ExpressionError("result is too large") from None
            if isinstance(result, complex):
                # a negative base with a fractional exponent
                raise ExpressionError("result is not a real number")
            return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op,
                                                     _ALLOWED_UNARYOPS):
        v = _eval_node(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    raise ExpressionError(
        f"expressions may only use numbers and + - * / ( ) "
        f"(got {type(node).__name__})")


def eval_expression(text: str) -> float:
    """Evaluate a restricted arithmetic expression (comment stripped).
    Raises `ExpressionError` for anything else -- a name, a call, an
    empty expression, invalid syntax, division by zero, a non-finite or
    overflowed result."""
    expr = _strip_comment(text).strip()
    if not expr:
        raise ExpressionError("empty expression")
    if len(expr) > _MAX_EXPR_LEN:
        raise ExpressionError("expression is too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"invalid expression: {e.msg}") from e
    except (RecursionError, MemoryError, ValueError) as e:
        # `ast.parse` itself recurses on a long "1+1+..." chain
        # (RecursionError) rather than raising SyntaxError.
        raise ExpressionError("expression is too complex") from e
    try:
        result = _eval_node(tree)
    except (OverflowError, RecursionError, MemoryError) as e:
        raise ExpressionError("expression result is too large") from e
    if not math.isfinite(result):
        raise ExpressionError("expression result is not finite")
    return result


def is_bare_rate_ref(text: str) -> str | None:
    """The rate name if `text` (stripped) is exactly `@RateName` and
    nothing else, else None. Callers use this to decide whether to
    store a formula as `{"rate": name}` (bare) or `{"expr": text}`
    (embedded in arithmetic, or plain)."""
    m = _BARE_RATE_RE.match(text.strip())
    return m.group(1) if m else None


def _rate_value(name: str, rates: list[dict] | None) -> float:
    for r in (rates or []):
        if r.get("name") == name:
            try:
                value = float(r["value"])
            except (KeyError, TypeError, ValueError):
                raise ExpressionError(
                    f"rate {name!r} has no valid value") from None
            if not math.isfinite(value):
                raise ExpressionError(
                    f"rate {name!r} has a non-finite value")
            return value
    raise ExpressionError(f"no saved rate named {name!r}")


def _substitute_rates(expr: str, rates: list[dict] | None) -> str:
    """Replace every `@RateName` token with its resolved value
    (parenthesized, so e.g. `@Rate * -1` and unary-minus precedence
    aren't affected by the substitution)."""
    def _sub(m: re.Match) -> str:
        return f"({_rate_value(m.group(1), rates)!r})"
    return _RATE_TOKEN_RE.sub(_sub, expr)


def resolve(text: str, rates: list[dict] | None = None) -> float:
    """`text` -> a float, per the two forms in this module's docstring.
    `rates` is `RbdFile.rates` (or None/empty if the model has none)."""
    stripped = text.strip()
    name = is_bare_rate_ref(stripped)
    if name is not None:
        return _rate_value(name, rates)
    # Strip the comment before substituting -- an "@" inside the human-
    # readable basis text ("# see @SomeDoc") must not be mistaken for a
    # rate reference.
    code = _strip_comment(text)
    if "@" in code:
        code = _substitute_rates(code, rates)
    return eval_expression(code)


def is_plain_number(text: str) -> bool:
    """True if `text` is already just the bare canonical form of a float
    (`"3"`, `"3.5"`, `"-2"`). Callers use this to decide whether a
    field's committed text is worth remembering as a formula or rate
    link, or is just an ordinary number with no basis to lose."""
    try:
        float(text.strip())
        return True
    except ValueError:
        return False

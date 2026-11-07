"""Per-column filter expressions for the GUI's big tables (pure Python, no Qt).

A filter box holds one small expression. ``compile_filter`` turns it into a
predicate over a cell, given as its display text and, when the table has
one, its raw number. The syntax, kept deliberately small:

    pump           text contains "pump" (case-insensitive)
    "main pump"    a phrase, spaces included
    Pump*  *_B  P?mp   wildcards (* any run, ? one character); with a
                   wildcard the whole cell must match, so ``Pump*`` means
                   "starts with Pump" and ``*pump*`` means "contains"
    =Pump_A        the whole cell equals the text (also ``=5`` numerically)
    !pump          NOT: cells that do not match the rest of the term
    >0.9  <=100  >=5  <2  =3  !=0     number comparisons
    5..10          a number from 5 to 10 inclusive
    blank  nonblank   empty / non-empty cell

Terms separated by spaces must all match (AND); ``|`` or ``,`` between
terms means OR, and binds loosest: ``>5 <10 | blank`` is "(>5 and <10) or
blank". A number term compares the cell's raw value when there is one,
otherwise the number read from its text (commas, ``%``, a ``>`` censor mark
and a trailing unit are ignored); a cell with no number never matches one.

A bad expression never raises: the result has ``valid=False`` and a message,
and matches everything, so the table is left unfiltered while the box shows
the problem.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

#: Predicate over (display text, raw number or None).
CellTest = Callable[[str, "float | None"], bool]

SYNTAX_HELP = (
    "Type to filter this column (case-insensitive).\n"
    "  pump         contains 'pump'          Pump*   starts with 'Pump'\n"
    "  >0.9  <=100  number comparisons       5..10   a number from 5 to 10\n"
    "  =Pump_A      the whole cell equals    !pump   does not contain 'pump'\n"
    "  blank / nonblank                      \"two words\"  a phrase\n"
    "Space = AND, | or , = OR.   Example:  >5 <10 | blank")

_NUM = re.compile(r"[-+]?(?:\d[\d,]*\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_UNIT_TAIL = re.compile(r"^\s*(?:%|[^\W\d_][^\d]{0,11})?\s*$")
_OPS = (">=", "<=", "!=", ">", "<", "=")


class FilterSyntaxError(ValueError):
    """A bad expression; the message is shown to the user."""


@dataclass(frozen=True)
class CompiledFilter:
    test: CellTest
    valid: bool = True
    empty: bool = False
    message: str = ""

    def __call__(self, text: str, raw: float | None = None) -> bool:
        return self.test(text, raw)


def parse_number(text: str) -> float | None:
    """The number a cell shows: ``1,234.5``, ``96.2%``, ``>1000``, ``3 hours``
    read as numbers; anything else None."""
    s = text.strip().lstrip("<>≥≤~≈").strip()
    m = _NUM.match(s)
    if m is None:
        return None
    if not _UNIT_TAIL.match(s[m.end():]):
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def _cell_number(text: str, raw: float | None) -> float | None:
    return raw if raw is not None else parse_number(text)


def _split_or(expr: str) -> list[str]:
    """Split on | and , outside double quotes."""
    parts: list[str] = []
    cur: list[str] = []
    quoted = False
    for ch in expr:
        if ch == '"':
            quoted = not quoted
        if ch in "|," and not quoted:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if quoted:
        raise FilterSyntaxError('Unclosed quote ("')
    parts.append("".join(cur))
    return parts


def _split_and(group: str) -> list[str]:
    """Split on whitespace outside double quotes; quotes stay on the term."""
    terms, cur, quoted = [], [], False
    for ch in group:
        if ch == '"':
            quoted = not quoted
            cur.append(ch)
        elif ch.isspace() and not quoted:
            if cur:
                terms.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        terms.append("".join(cur))
    return terms


def _unquote(s: str) -> str:
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s.replace('"', "")


def _glob(pattern: str) -> re.Pattern[str]:
    out = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c)
                  for c in pattern)
    return re.compile(f"^{out}$", re.IGNORECASE | re.DOTALL)


def _operand(text: str, op: str) -> float:
    n = parse_number(text)
    if n is None:
        raise FilterSyntaxError(f"'{op}' needs a number, not '{text}'")
    return n


def _term(term: str) -> CellTest:
    low = term.lower()
    if low == "blank" or low == "empty":
        return lambda t, r: not t.strip()
    if low == "nonblank":
        return lambda t, r: bool(t.strip())
    if term.startswith("!") and not term.startswith("!="):
        rest = term[1:]
        if not rest:
            raise FilterSyntaxError("'!' needs something after it")
        inner = _term(rest)
        return lambda t, r: not inner(t, r)
    for op in _OPS:
        if term.startswith(op):
            return _comparison(op, term[len(op):])
    if ".." in term and not term.startswith('"'):
        return _range_term(term)
    return _text_term(term)


def _range_term(term: str) -> CellTest:
    lo_s, _, hi_s = term.partition("..")
    lo, hi = _operand(lo_s, ".."), _operand(hi_s, "..")
    if lo > hi:
        lo, hi = hi, lo

    def in_range(t: str, r: float | None) -> bool:
        n = _cell_number(t, r)
        return n is not None and lo <= n <= hi
    return in_range


def _text_term(term: str) -> CellTest:
    body = _unquote(term)
    if not body:
        raise FilterSyntaxError("Empty text")
    if "*" in body or "?" in body:
        rx = _glob(body)
        return lambda t, r: rx.match(t) is not None
    needle = body.lower()
    return lambda t, r: needle in t.lower()


def _comparison(op: str, operand: str) -> CellTest:
    operand = _unquote(operand.strip())
    if not operand and op not in ("=", "!="):
        raise FilterSyntaxError(f"'{op}' needs a number after it")
    if op in ("=", "!="):
        number = parse_number(operand)
        want = operand.lower()
        rx = _glob(operand) if ("*" in operand or "?" in operand) else None

        def equal(t: str, r: float | None) -> bool:
            if number is not None:
                n = _cell_number(t, r)
                if n is not None:
                    return n == number
            if rx is not None:
                return rx.match(t) is not None
            return t.strip().lower() == want
        if op == "=":
            return equal
        return lambda t, r: not equal(t, r)
    ref = _operand(operand, op)
    compare = {">": lambda a: a > ref, "<": lambda a: a < ref,
               ">=": lambda a: a >= ref, "<=": lambda a: a <= ref}[op]

    def numeric(t: str, r: float | None) -> bool:
        n = _cell_number(t, r)
        return n is not None and compare(n)
    return numeric


def compile_filter(expr: str) -> CompiledFilter:
    """The predicate for a filter box's text (see the module docstring)."""
    if not expr.strip():
        return CompiledFilter(lambda t, r: True, empty=True)
    try:
        groups = []
        for part in _split_or(expr):
            terms = [_term(t) for t in _split_and(part)]
            if terms:
                groups.append(terms)
        if not groups:
            return CompiledFilter(lambda t, r: True, empty=True)
    except FilterSyntaxError as exc:
        return CompiledFilter(lambda t, r: True, valid=False, message=str(exc))

    if len(groups) == 1 and len(groups[0]) == 1:
        return CompiledFilter(groups[0][0])

    def test(t: str, r: float | None) -> bool:
        return any(all(f(t, r) for f in g) for g in groups)
    return CompiledFilter(test)

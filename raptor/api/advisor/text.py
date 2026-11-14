"""Number and name formatting shared by every advisor rule."""
from __future__ import annotations

import math
from collections.abc import Iterable


def fmt_num(x: float, sig: int = 3) -> str:
    """A number to `sig` significant figures with thousands separators."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    if x == 0:
        return "0"
    mag = math.floor(math.log10(abs(x)))
    decimals = max(0, sig - 1 - mag)
    rounded = round(x, decimals) if decimals > 0 else round(x, sig - 1 - mag)
    if decimals == 0:
        return f"{int(rounded):,}"
    text = f"{rounded:,.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt_time(x: float, units: str = "") -> str:
    u = _unit_word(units)
    return f"{fmt_num(x)} {u}".rstrip()


def fmt_pct(fraction: float, sig: int = 2) -> str:
    return f"{fmt_num(100.0 * fraction, sig)} %"


def _unit_word(units: str) -> str:
    u = (units or "").strip()
    return {"hours": "h", "hour": "h", "hrs": "h"}.get(u.lower(), u)


def join_names(names: Iterable[str], limit: int = 3) -> str:
    """`A, B and C` -- or `A, B, C and 4 more` past `limit`."""
    items = list(names)
    if not items:
        return ""
    if len(items) <= limit:
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    return ", ".join(items[:limit]) + f" and {len(items) - limit} more"


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"

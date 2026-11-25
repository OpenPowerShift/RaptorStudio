"""Money in the model's currency, the same way on every screen.

A model names its currency once (`RbdFile.currency`, edited in Preferences ▸
Model; "$" when it names none).  It only labels numbers: no amount is ever
converted.  Charts and the Story already print it (`axes.currency`); this
module is what the rest of the application uses, so an input box, a table
heading, a results line and a canvas tooltip all say "€" for a euro model:

- `amount(1234.5, "€")` -> "€1,234.50" (a code gets a space: "AUD 1,234.50")
- `prefix("€")` -> "€", the text in front of a cost input box
- `heading("Total cost", "€")` -> "Total cost (€)", a table column
"""
from __future__ import annotations

from typing import Any

from raptor.api.insight.axes import with_currency
from raptor.core.fileio.rbds import DEFAULT_CURRENCY, clean_currency


def model_currency(rbd: Any) -> str:
    """The currency `rbd` shows money in ("$" when there is no model or it
    names none)."""
    if rbd is None:
        return DEFAULT_CURRENCY
    return clean_currency(getattr(rbd, "currency", None))


def amount(value: float, currency: str = DEFAULT_CURRENCY, *,
           decimals: int = 2) -> str:
    """`value` with thousands separators and the currency in front:
    "$1,234.50", "-€12.00", "AUD 3.00"."""
    return with_currency(f"{float(value):,.{decimals}f}", currency)


def prefix(currency: str = DEFAULT_CURRENCY) -> str:
    """What goes in front of a cost input: "$", "€" or "AUD " (a code
    ending in a letter keeps a space before the number)."""
    return with_currency("", currency)


def heading(label: str, currency: str = DEFAULT_CURRENCY) -> str:
    """A column or field label that holds money: "Total cost (€)"."""
    return f"{label} ({currency})"


def glyph(currency: str = DEFAULT_CURRENCY) -> str:
    """One character for a small badge: the symbol itself ("$", "€"), the
    symbol inside a longer text ("A$" -> "$", "R$" -> "$"), else the
    generic currency sign "¤" (a code such as "AUD" does not fit)."""
    if len(currency) == 1:
        return currency
    return next((ch for ch in currency if not ch.isalnum() and not ch.isspace()),
                "¤")

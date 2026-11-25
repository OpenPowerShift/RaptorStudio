"""`raptor.api.money`: money printed the same way on every screen."""
from __future__ import annotations

from types import SimpleNamespace

from raptor.api import money


def test_amount_puts_the_currency_in_front():
    assert money.amount(1234.5) == "$1,234.50"
    assert money.amount(1234.5, "€") == "€1,234.50"
    assert money.amount(-12, "€") == "-€12.00"
    assert money.amount(3, "AUD") == "AUD 3.00"       # a code keeps a space
    assert money.amount(7, "€", decimals=0) == "€7"


def test_prefix_heading_and_glyph():
    assert money.prefix("€") == "€"
    assert money.prefix("CHF") == "CHF "
    assert money.heading("Initial cost", "£") == "Initial cost (£)"
    assert money.glyph("€") == "€"
    assert money.glyph("A$") == "$"
    assert money.glyph("AUD") == "¤"


def test_model_currency_defaults_to_dollars():
    assert money.model_currency(None) == "$"
    assert money.model_currency(SimpleNamespace()) == "$"
    assert money.model_currency(SimpleNamespace(currency="  € ")) == "€"
    assert money.model_currency(SimpleNamespace(currency="")) == "$"

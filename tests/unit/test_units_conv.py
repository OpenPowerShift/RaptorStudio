"""Time-unit display conversion: view and enter values in a unit other
than the model's native one. Pure conversion-math tests -- no PySide6
needed."""
from __future__ import annotations

from raptor.gui.ui.units_conv import (
    HOURS_PER_UNIT, convertible, native_to_display, display_to_native,
)


def test_hours_to_years_and_back():
    assert abs(native_to_display(8760, "hours", "years") - 1.0) < 1e-9
    assert abs(display_to_native(1.0, "hours", "years") - 8760) < 1e-6


def test_days_to_hours():
    assert abs(native_to_display(1, "days", "hours") - 24) < 1e-9


def test_same_unit_is_noop():
    assert native_to_display(5, "hours", "hours") == 5
    assert display_to_native(5, "hours", "hours") == 5


def test_no_display_unit_is_noop():
    assert native_to_display(5, "hours", None) == 5
    assert display_to_native(5, "hours", None) == 5


def test_unknown_unit_is_noop_and_not_convertible():
    assert native_to_display(5, "cycles", "years") == 5
    assert not convertible("cycles", "years")
    assert not convertible("hours", "bogus")
    assert convertible("hours", "years")


def test_round_trip_every_pair():
    for native in HOURS_PER_UNIT:
        for display in HOURS_PER_UNIT:
            v = 3.7
            shown = native_to_display(v, native, display)
            back = display_to_native(shown, native, display)
            assert abs(back - v) < 1e-6, (native, display)

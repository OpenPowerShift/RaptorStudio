"""Time-unit display conversion.

Lets a value be viewed and entered in a unit other than the model's own
native one -- e.g. typing "20" years into a field on an hours-native
model instead of hand-converting to 175200 first.

This is purely a display- and entry-time transform: the model's stored
values and native `units` column never change, only what a value field
shows and how it parses what is typed. The choice persists as
`RbdFile.display_unit`, in `.rbds` files only (see that field's own
docstring for why).
"""
from __future__ import annotations

#: Canonical hours-per-unit factors. Keys are lowercase to match the
#: model's own `units` column convention (always "hours"/"days", never
#: Title Case).
HOURS_PER_UNIT = {
    "minutes": 1 / 60,
    "hours": 1.0,
    "days": 24.0,
    "weeks": 24.0 * 7,
    "months": 730.0,     # 365-day year / 12 -- the reliability-
    "years": 8760.0,     # engineering convention (24 * 365), not a
                         # leap-aware calendar year.
}

#: Combo order for the Preferences picker. "(native)" (None) always
#: first -- the default, no-conversion state.
DISPLAY_UNIT_CHOICES = [None, "minutes", "hours", "days", "weeks",
                        "months", "years"]


def parse_display_unit(text: str | None) -> str | None:
    """The Display unit box's text -> the value stored on the model.

    "" and "(native units)" mean no override. A listed unit (any case) is stored lowercase and converts. Any other
    text is the user's own unit name, stored as typed: it relabels the unit and never converts (`convertible` is
    False for it), e.g. "flight hours" on an hours model."""
    t = (text or "").strip()
    if not t or t.lower() in ("(native units)", "native", "(native)"):
        return None
    return t.lower() if t.lower() in HOURS_PER_UNIT else t


def _factor(unit: str | None) -> float | None:
    return HOURS_PER_UNIT.get((unit or "").strip().lower())


def convertible(native_unit: str | None, display_unit: str | None) -> bool:
    """False when either unit isn't in the known vocabulary (e.g. the
    model's own `units` is something exotic like "cycles", which isn't
    a physical time unit at all) OR no display override is set --
    callers use this to decide whether to show a conversion at all."""
    if not display_unit:
        return False
    return _factor(native_unit) is not None and _factor(display_unit) is not None


def native_to_display(value: float, native_unit: str | None,
                      display_unit: str | None) -> float:
    """`value` in the model's native unit -> the display unit. A no-op
    (returns `value` unchanged) whenever `convertible` would be False,
    or the two units are actually the same."""
    if not convertible(native_unit, display_unit) or \
            (display_unit or "").lower() == (native_unit or "").lower():
        return value
    return value * (_factor(native_unit) or 1.0) / (_factor(display_unit) or 1.0)


def display_to_native(value: float, native_unit: str | None,
                      display_unit: str | None) -> float:
    """Inverse of `native_to_display` -- what a field's commit path
    calls on the number the user just typed."""
    if not convertible(native_unit, display_unit) or \
            (display_unit or "").lower() == (native_unit or "").lower():
        return value
    return value * (_factor(display_unit) or 1.0) / (_factor(native_unit) or 1.0)

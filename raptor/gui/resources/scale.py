"""Interface-scale bounds, shared by the GUI, Preferences and the CLI.

Kept free of Qt imports so `raptor config` stays a lightweight CLI command:
importing the design tokens would pull in QtWidgets just to validate a
number.
"""
from __future__ import annotations

#: Interface scale, as a percentage of the design size. `SCALE_STEPS`
#: are the Preferences choices; the CLI also accepts any whole percent in
#: `SCALE_MIN`-`SCALE_MAX`, which Preferences then shows as a custom step.
SCALE_MIN = 100
SCALE_MAX = 200
SCALE_STEPS = (100, 125, 150, 175, 200)


def normalise_scale(value: object) -> int:
    """A usable interface scale: an int in range, or the design size.

    Hand-edited settings files are read through this, so junk falls back to
    100% instead of stopping the application at startup.
    """
    try:
        percent = round(float(value))          # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return SCALE_MIN
    return max(SCALE_MIN, min(SCALE_MAX, percent))

"""A number field that shows ``1000`` rather than ``1000.000000``."""
from __future__ import annotations

from PySide6.QtWidgets import QDoubleSpinBox


def tidy_number(value: float, decimals: int) -> str:
    """`value` to `decimals` places with the trailing zeros dropped."""
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class TidySpinBox(QDoubleSpinBox):
    """Keeps every decimal the field allows for typing and storing, but
    does not pad the number out to them: six decimals made a run length
    read ``1000.000000`` and a cost ``13300.000000``."""

    def textFromValue(self, value: float) -> str:  # noqa: N802 (Qt API)
        return tidy_number(value, self.decimals())

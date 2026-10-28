"""WCAG luminance and contrast, shared by the canvas and annotations.

Element labels and annotation colours both need the same question
answered -- "will this read against that fill?" -- and each module used
to carry its own copy of the arithmetic, with a comment explaining the
duplication. This is the one copy. No widget state, only `QColor`.
"""
from __future__ import annotations

from PySide6.QtGui import QColor

from raptor.gui.resources.design_tokens import COLOUR


def relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance (0 = black, 1 = white) of a hex colour."""
    c = QColor(hex_colour)

    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return (0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF())
            + 0.0722 * lin(c.blueF()))


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG contrast ratio (1..21) between two colours."""
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def readable_text_colour(background: str,
                         preferred: str | None = None) -> QColor:
    """Highest-contrast text colour for a filled element.

    Result fills are theme-independent green/yellow/red, so the theme's
    usual text colour can disappear on them (dark text on dark red, light
    text on light yellow). Choose the better of the theme text,
    near-black and white by WCAG relative-luminance contrast.
    """
    candidates = [c for c in (preferred, COLOUR["text"], "#FFFFFF",
                              "#000000") if c]
    return QColor(max(candidates, key=lambda c: contrast_ratio(background, c)))

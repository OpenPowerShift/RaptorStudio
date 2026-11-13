"""Colours and fill patterns for downtime causes, in both themes.

A cause is never told apart by colour alone: each also has its own fill
pattern, so the charts read in greyscale and for colour-blind readers.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor

from raptor.gui.resources.design_tokens import ThemeManager

#: cause -> (light theme, dark theme)
_COLOURS = {
    "repair": ("#C4472F", "#F0806A"),
    "wait_spare": ("#C98A0B", "#F2B84B"),
    "wait_resource": ("#2F6FB7", "#6FA8E8"),
    "logistics": ("#7A5AA6", "#B095DA"),
    "pm": ("#2A8C82", "#5CC7BB"),
    "pm_wait": ("#5E9C3F", "#93CC72"),
    "dependency": ("#6B7785", "#A4B0BF"),
    "done": ("#3C4653", "#D0D7E0"),
    "other": ("#9AA5B1", "#75808D"),
}
_PATTERNS = {
    "repair": Qt.BrushStyle.SolidPattern,
    "wait_spare": Qt.BrushStyle.BDiagPattern,
    "wait_resource": Qt.BrushStyle.FDiagPattern,
    "logistics": Qt.BrushStyle.Dense4Pattern,
    "pm": Qt.BrushStyle.HorPattern,
    "pm_wait": Qt.BrushStyle.VerPattern,
    "dependency": Qt.BrushStyle.CrossPattern,
    "done": Qt.BrushStyle.DiagCrossPattern,
    "other": Qt.BrushStyle.Dense6Pattern,
}


def is_dark() -> bool:
    return QColor(ThemeManager.instance().colour["surface"]).lightness() < 128


def cause_colour(cause: str) -> QColor:
    light, dark = _COLOURS.get(cause, _COLOURS["other"])
    return QColor(dark if is_dark() else light)


def cause_brush(cause: str) -> QBrush:
    """The pattern overlay for a cause (painted over its plain colour)."""
    return QBrush(QColor(ThemeManager.instance().colour["surface"]),
                  _PATTERNS.get(cause, Qt.BrushStyle.Dense6Pattern))


def has_pattern(cause: str) -> bool:
    return _PATTERNS.get(cause) != Qt.BrushStyle.SolidPattern

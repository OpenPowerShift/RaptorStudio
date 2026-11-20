"""Colours and fill patterns for downtime causes, in both themes.

A cause is never told apart by colour alone: each also has its own fill
pattern, so the charts read in greyscale and for colour-blind readers.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor

from raptor.gui.resources.design_tokens import ThemeManager

#: cause -> (light theme, dark theme), from the active chart palette.
from raptor.api.insight import palette as _palette

_COLOURS = {
    cause: (_palette.PALETTES[_palette.DEFAULT]["light"][cause],
            _palette.PALETTES[_palette.DEFAULT]["dark"][cause])
    for cause in _palette.PALETTES[_palette.DEFAULT]["light"]
}
_PATTERNS = {
    "repair": Qt.BrushStyle.SolidPattern,
    "wait_spare": Qt.BrushStyle.BDiagPattern,
    "wait_resource": Qt.BrushStyle.FDiagPattern,
    "logistics": Qt.BrushStyle.Dense5Pattern,
    "pm": Qt.BrushStyle.HorPattern,
    "pm_wait": Qt.BrushStyle.VerPattern,
    "dependency": Qt.BrushStyle.CrossPattern,
    "done": Qt.BrushStyle.DiagCrossPattern,
    "other": Qt.BrushStyle.Dense6Pattern,
    "purchase": Qt.BrushStyle.SolidPattern,
    "operating": Qt.BrushStyle.HorPattern,
    "sparing": Qt.BrushStyle.BDiagPattern,
    "disposal": Qt.BrushStyle.CrossPattern,
    "spare_pool": Qt.BrushStyle.Dense5Pattern,
}

#: How strongly the pattern is drawn over a cause's colour (0-255).
PATTERN_ALPHA = 120


def is_dark() -> bool:
    return QColor(ThemeManager.instance().colour["surface"]).lightness() < 128


def cause_colour(cause: str) -> QColor:
    return QColor(_palette.colour(cause, dark=is_dark()))


def cause_brush(cause: str) -> QBrush:
    """The pattern overlay for a cause, painted over its plain colour.

    The lines are a darker shade of the cause's own colour (lighter in the
    dark theme), so the fill keeps its colour and a label on it stays
    readable; a solid cause gets no overlay at all.  (The overlay used to be
    the window colour, which washed every fill out to a pale hatch.)"""
    style = _PATTERNS.get(cause, Qt.BrushStyle.Dense6Pattern)
    if style == Qt.BrushStyle.SolidPattern:
        return QBrush(Qt.BrushStyle.NoBrush)
    base = cause_colour(cause)
    ink = base.lighter(160) if is_dark() else base.darker(170)
    return QBrush(ink, style)


#: The two inks a label on a cause fill can use.
INKS = ("#111111", "#FFFFFF")


def cause_ink(cause: str) -> QColor:
    """Text colour that reads on the cause's fill (pattern included)."""
    return ink_on(cause_colour(cause))


def ink_on(colour: QColor) -> QColor:
    """The near-black or white ink, whichever has the higher WCAG contrast
    with `colour`.  (It used to compare against pure black but then draw
    #111111, which put under 4.5:1 text on mid-tone fills.)"""
    from raptor.gui.ui.contrast import contrast_ratio
    fill = colour.name()
    return QColor(max(INKS, key=lambda ink: contrast_ratio(fill, ink)))


def has_pattern(cause: str) -> bool:
    return _PATTERNS.get(cause) != Qt.BrushStyle.SolidPattern

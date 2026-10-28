"""Ribbon icons: every command has its own painted glyph, visible in both
themes.

The ribbon used to mix stock QStyle icons (which follow the desktop's icon
theme, so Domains vanished in the light theme) with painted ones, and one
painted glyph stood for up to four unrelated commands. These pin the fix:
the table names a painted icon for every command, no two commands share
one, and each icon's colour clears 3:1 against the ribbon in both themes
(WCAG 1.4.11, non-text contrast).
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from raptor.gui.command_table import COMMANDS
from raptor.gui.resources import icons
from raptor.gui.resources.design_tokens import DARK_COLOUR, LIGHT_COLOUR

pytestmark = pytest.mark.gui


def _luminance(hex_colour: str) -> float:
    def ch(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _split(spec):
    name, _, token = spec.icon.partition(":")
    return name, token or "accent"


def test_every_command_names_a_painted_icon():
    missing = [s.id for s in COMMANDS if not s.icon]
    assert not missing, f"commands with no icon: {missing}"
    unknown = [s.id for s in COMMANDS if not hasattr(icons, _split(s)[0])]
    assert not unknown, f"icon functions that do not exist: {unknown}"


def test_no_two_commands_share_a_glyph():
    seen: dict[str, str] = {}
    clashes = []
    for s in COMMANDS:
        name = _split(s)[0]
        if name in seen:
            clashes.append(f"{seen[name]} and {s.id} both use {name}")
        seen.setdefault(name, s.id)
    assert not clashes, "\n".join(clashes)


@pytest.mark.parametrize("palette", [LIGHT_COLOUR, DARK_COLOUR],
                         ids=["light", "dark"])
def test_icon_colours_clear_3_to_1_on_the_ribbon(palette):
    bg = palette["surface"]
    low = []
    for s in COMMANDS:
        token = _split(s)[1]
        ratio = _contrast(palette[token], bg)
        if ratio < 3.0:
            low.append(f"{s.id}: {token} {palette[token]} is {ratio:.2f}:1")
    assert not low, "\n".join(low)


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_icons_paint_and_have_their_own_disabled_image(qapp, dark):
    """Each icon actually paints something, and its disabled image is
    painted (not Qt's greyed copy, which stayed bright on the navy)."""
    from raptor.gui.resources.design_tokens import ThemeManager
    tm = ThemeManager.instance()
    was = tm.is_dark
    tm._dark = dark
    icons.clear_cache()
    try:
        palette = DARK_COLOUR if dark else LIGHT_COLOUR
        blank = []
        for s in COMMANDS:
            name, token = _split(s)
            icon = getattr(icons, name)(palette[token])
            img = icon.pixmap(32, 32).toImage()
            if not any(img.pixelColor(x, y).alpha() > 0
                       for x in range(0, 32, 2) for y in range(0, 32, 2)):
                blank.append(s.id)
            assert icon.availableSizes(QIcon.Mode.Disabled), (
                f"{s.id}: no painted disabled image")
        assert not blank, f"icons that paint nothing: {blank}"
    finally:
        tm._dark = was
        icons.clear_cache()

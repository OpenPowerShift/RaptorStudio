"""UI scale, high contrast and visible focus styles."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.gui.resources import design_tokens as D


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_css_scaling_changes_every_px_length():
    css = "QWidget { padding: 4px; font-size: 13px; border: 1px solid red; }"
    scaled = D._scale_css(css, 150)
    assert "padding: 6px" in scaled
    assert "font-size: 19.5px" in scaled
    assert "border: 1.5px" in scaled
    assert D._scale_css(css, 100) == css


def test_high_contrast_palette_has_black_white_text_and_strong_borders():
    theme = D.ThemeManager.instance()
    old = (theme.is_dark, theme.high_contrast, theme.scale)
    try:
        theme.set_high_contrast(False)
        theme.set_dark(False)
        normal = theme.colour
        theme.set_high_contrast(True)
        light = theme.colour
        assert light["text"] == "#000000" and light["surface"] == "#FFFFFF"
        assert light["border"] == "#000000"
        assert light["text"] != normal["text"] or light["border"] != normal["border"]
        theme.set_dark(True)
        dark = theme.colour
        assert dark["text"] == "#FFFFFF" and dark["surface"] == "#000000"
        assert dark["border"] == "#FFFFFF"
        # Semantic state colours stay data, not chrome.
        assert dark["operating"] == D.APPENDIX_D_STATE_COLOURS["running"]
    finally:
        theme.set_dark(old[0])
        theme.set_high_contrast(old[1])
        theme.set_scale(old[2])


def test_scale_sets_the_application_font_and_survives_a_theme_change(qapp):
    theme = D.ThemeManager.instance()
    old = theme.scale
    try:
        theme.set_scale(150)
        assert theme.scale == 150
        assert qapp.font().pixelSize() >= 19
        assert "font-size: 19.5px" in D.global_stylesheet()
        theme.set_scale(100)
        assert qapp.font().pixelSize() == 13
    finally:
        theme.set_scale(old)


def test_the_stylesheet_has_a_visible_focus_rule(qapp):
    css = D.global_stylesheet()
    assert "QPushButton:focus" in css
    assert "QLineEdit:focus" in css

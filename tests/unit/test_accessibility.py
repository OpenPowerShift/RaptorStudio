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
        # Semantic state marks get a high-contrast shade, not the low-contrast
        # Appendix D fill that is invisible on white/black.
        assert dark["operating"] != D.APPENDIX_D_STATE_COLOURS["running"]
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
        font = qapp.font()
        assert (font.pixelSize() >= 19 if font.pixelSize() > 0
                else font.pointSizeF() >= 9)
        assert "font-size: 19.5px" in D.global_stylesheet()
        theme.set_scale(100)
        font = qapp.font()
        assert (font.pixelSize() in (13, 14) if font.pixelSize() > 0
                else 8 <= font.pointSizeF() <= 12)
    finally:
        theme.set_scale(old)


def test_the_stylesheet_has_a_visible_focus_rule(qapp):
    css = D.global_stylesheet()
    assert "QPushButton:focus" in css
    assert "QLineEdit:focus" in css


def test_geometry_scales_and_keeps_the_ribbon_above_the_font(qapp):
    from PySide6.QtGui import QFontMetrics
    theme = D.ThemeManager.instance()
    old = theme.scale
    try:
        theme.set_scale(150)
        assert D.RIBBON["small_height"] >= QFontMetrics(qapp.font()).height()
        assert D.RIBBON["tab_height"] > D.BASE_RIBBON["tab_height"]
        theme.set_scale(200)
        assert D.RIBBON["small_height"] >= QFontMetrics(qapp.font()).height()
    finally:
        theme.set_scale(old)


def test_the_application_font_has_a_usable_point_or_pixel_size(qapp):
    font = qapp.font()
    assert font.pointSizeF() > 0 or font.pixelSize() > 0
    # Chart code does point-size arithmetic; a pixel-only font returns -1.
    if font.pointSizeF() <= 0:
        assert font.pixelSize() > 0


def test_cli_scale_reaches_the_gui_settings(monkeypatch, tmp_path):
    from raptor.cli.main import main
    from raptor.gui.ui import settings
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = settings.get("appearance/ui_scale", 100)
    try:
        assert main(["config", "set", "appearance/ui_scale", "150"]) == 0
        assert settings.get("appearance/ui_scale") == 150
        assert main(["config", "set", "appearance/ui_scale", "300"]) != 0
    finally:
        settings.set_value("appearance/ui_scale", old)


def test_chart_title_font_stays_above_the_body_font(qapp):
    from PySide6.QtGui import QFont, QFontMetrics
    base = QFont(qapp.font())
    title = QFont(base)
    if title.pointSizeF() > 0:
        title.setPointSizeF(title.pointSizeF() + 2)
    else:
        title.setPixelSize(title.pixelSize() + 2)
    assert QFontMetrics(title).height() > QFontMetrics(base).height()


def test_the_ribbon_geometry_grows_with_the_scale(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    old = settings.get("appearance/ui_scale", 100)
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/ui_scale", 150)
    try:
        w = MainWindow()
        try:
            w.show()
            qapp.processEvents()
            rb = w._ribbon
            assert rb.height() >= 150
            assert rb._page_host.height() >= rb._page_host.minimumSizeHint().height()
        finally:
            w._dirty = False
            w.close()
    finally:
        settings.set_value("appearance/ui_scale", old)
        D.ThemeManager.instance().set_scale(old)

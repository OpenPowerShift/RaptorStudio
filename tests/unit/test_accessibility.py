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
    for selector in ("QPushButton:focus", "QLineEdit:focus",
                     "QCheckBox:focus", "QRadioButton:focus",
                     "QTextBrowser:focus", "QSlider:focus",
                     "QScrollBar:focus", "QGroupBox:focus"):
        assert selector in css, selector
    # An accent ring on an accent-filled default button is invisible.
    assert "QPushButton:default:focus" in css
    default_focus = css[css.index("QPushButton:default:focus"):]
    default_focus = default_focus[:default_focus.index("}")]
    assert D.active_colour()["text"] in default_focus


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
        assert main(["config", "set", "appearance/ui_scale", "99"]) != 0
        assert main(["config", "set", "appearance/ui_scale", "150.5"]) != 0
        # Any whole percent in range is honoured, not only the menu steps.
        assert main(["config", "set", "appearance/ui_scale", "137"]) == 0
        assert settings.get("appearance/ui_scale") == 137
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
    old_start = settings.get("startup/show_start_page", None)
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
        if old_start is None:
            settings.remove("startup/show_start_page")
        else:
            settings.set_value("startup/show_start_page", old_start)
        settings.set_value("appearance/ui_scale", old)
        D.ThemeManager.instance().set_scale(old)


def _lum(hex_str: str) -> float:
    h = hex_str.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def f(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_high_contrast_keeps_every_text_and_mark_legible():
    """The high-contrast palette is a complete palette, not a partial
    override: tokens it forgot (relationship, result traffic lights) used to
    keep their normal shade on pure black -- relationship was 3.3:1."""
    panels = ("surface", "canvas", "group_header", "input_bg")
    text_tokens = ("text", "muted", "accent", "warn", "result_green",
                   "result_red", "relationship")
    mark_tokens = ("go", "stop", "result_yellow", "operating",
                   "understressed", "overstressed", "degraded", "standby",
                   "maintenance", "idle", "failed")
    for name, palette in (("light", D.HIGH_CONTRAST_LIGHT),
                          ("dark", D.HIGH_CONTRAST_DARK)):
        assert set(palette) == set(D.LIGHT_COLOUR), name
        for token in text_tokens:
            for bg in panels:
                assert _ratio(palette[token], palette[bg]) >= 4.5, (
                    name, token, bg)
        for token in mark_tokens:
            for bg in panels:
                assert _ratio(palette[token], palette[bg]) >= 3.0, (
                    name, token, bg)
        assert _ratio(palette["text"], palette["surface"]) >= 7.0
        assert _ratio(palette["selection_text"],
                      palette["selection_bg"]) >= 4.5


@pytest.mark.parametrize("raw, want", [
    (100, 100), (150, 150), (200, 200), (99, 100), (201, 200),
    (137.6, 138), ("150", 150), ("junk", 100), (None, 100), (True, 100),
    (float("inf"), 100), (float("nan"), 100),
])
def test_scale_normalisation_clamps_and_survives_junk(raw, want):
    assert D.normalise_scale(raw) == want


def test_the_theme_manager_uses_the_same_scale_rules(qapp):
    theme = D.ThemeManager.instance()
    old = theme.scale
    try:
        theme.set_scale(250)
        assert theme.scale == D.SCALE_MAX
        theme.set_scale(37)
        assert theme.scale == D.SCALE_MIN
        theme.set_scale("not a number")
        assert theme.scale == D.SCALE_MIN
    finally:
        theme.set_scale(old)


def test_a_scale_set_before_the_app_still_applies(monkeypatch):
    """set_scale used to return at the unchanged default, so a scale chosen
    before QApplication existed never reached the font or stylesheet."""
    theme = D.ThemeManager()
    calls = []

    def fake_apply():
        calls.append(theme.scale)
        theme._applied = True

    monkeypatch.setattr(theme, "_apply", fake_apply)
    theme.set_scale(100)
    theme._applied = False
    theme.set_high_contrast(False)
    assert calls == [100, 100]


def test_no_gui_stylesheet_hard_codes_a_font_size():
    """Every local stylesheet scales its own type. `px(...)` is the marked
    exception; a file whose whole sheet goes through `scale_stylesheet`
    (Backstage) is exempt, as are the canvas labels, which are
    drawing-scale by design."""
    import pathlib
    import re
    repo = pathlib.Path(__file__).resolve().parents[2]
    exempt = {"design_tokens.py", "block_labels.py"}
    bad = []
    for path in (repo / "raptor" / "gui").rglob("*.py"):
        if path.name in exempt:
            continue
        src = path.read_text(encoding="utf-8")
        if "scale_stylesheet(" in src:
            continue
        for match in re.finditer(r"font-size:\s*([^;\n]+)", src):
            value = match.group(1).strip()
            if "px(" in value or "_px(" in value:
                continue
            line = src[:match.start()].count("\n") + 1
            bad.append(f"{path.relative_to(repo)}:{line}: {value}")
    assert not bad, bad


def test_the_mono_table_font_follows_the_application_font(qapp):
    """The results tables used a fixed 10 pt monospace font, so an OS font
    or interface scale never reached them."""
    from raptor.gui.ui.results_common import _mono_font
    theme = D.ThemeManager.instance()
    old = theme.scale
    try:
        theme.set_scale(200)
        mono = _mono_font()
        app_font = qapp.font()
        if app_font.pointSizeF() > 0:
            assert mono.pointSizeF() == app_font.pointSizeF()
        else:
            assert mono.pixelSize() == app_font.pixelSize()
    finally:
        theme.set_scale(old)


def test_a_dark_launch_states_the_theme_action_and_repairs_the_scale(qapp):
    """Appearance is applied before the ribbon exists, so the View action
    cannot rely on a theme-change callback at launch. A saved 300% is
    clamped to 200% and written back, so Preferences and the next launch
    agree with what is on screen."""
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    theme = D.ThemeManager.instance()
    old = {
        "appearance/dark_mode": settings.get_bool("appearance/dark_mode", False),
        "appearance/ui_scale": settings.get_int("appearance/ui_scale", 100),
        "startup/show_start_page": settings.get("startup/show_start_page", None),
    }
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/dark_mode", True)
    settings.set_value("appearance/ui_scale", 300)
    try:
        w = MainWindow()
        try:
            assert theme.is_dark
            assert theme.scale == D.SCALE_MAX
            assert settings.get_int("appearance/ui_scale") == D.SCALE_MAX
            assert w._theme_action.isChecked()
            assert w._theme_action.text() == "Light Theme"
        finally:
            w._dirty = False
            w.close()
    finally:
        settings.set_value("appearance/dark_mode", old["appearance/dark_mode"])
        settings.set_value("appearance/ui_scale", old["appearance/ui_scale"])
        if old["startup/show_start_page"] is None:
            settings.remove("startup/show_start_page")
        else:
            settings.set_value("startup/show_start_page",
                               old["startup/show_start_page"])
        theme.set_dark(old["appearance/dark_mode"])
        theme.set_scale(old["appearance/ui_scale"])


def test_saved_appearance_reaches_the_first_window(qapp):
    from PySide6.QtGui import QPalette
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    old = {k: settings.get(k) for k in (
        "appearance/dark_mode", "appearance/high_contrast",
        "appearance/ui_scale")}
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/dark_mode", True)
    settings.set_value("appearance/high_contrast", True)
    settings.set_value("appearance/ui_scale", 150)
    try:
        w = MainWindow()
        try:
            w.show()
            qapp.processEvents()
            assert w.palette().color(QPalette.ColorRole.Window).name() == "#000000"
            assert "#000000" in w.styleSheet()
            assert D.ThemeManager.instance().scale == 150
        finally:
            w._dirty = False
            w.close()
    finally:
        for key, value in old.items():
            if value is None:
                settings.remove(key)
            else:
                settings.set_value(key, value)
        D.ThemeManager.instance().set_scale(100)
        D.ThemeManager.instance().set_high_contrast(False)
        D.ThemeManager.instance().set_dark(False)


def test_literal_widget_sizes_follow_the_interface_scale(qapp):
    """The few hard-coded widths (combos, cards, chips) grow with the font."""
    theme = D.ThemeManager.instance()
    old = theme.scale
    try:
        theme.set_scale(100)
        assert D.scaled(90) == 90 and D.scaled(280) == 280
        theme.set_scale(150)
        assert D.scaled(90) == 135 and D.scaled(280) == 420
        theme.set_scale(200)
        assert D.scaled(84) == 168
    finally:
        theme.set_scale(old)


def test_a_dialog_gets_the_accessible_name_screen_readers_announce(qapp):
    from PySide6.QtWidgets import QDialog
    from raptor.gui.ui import modality
    dlg = QDialog()
    dlg.setWindowTitle("Component Groups and Tags")
    original = QDialog.exec
    QDialog.exec = lambda self: 0          # do not open a window
    try:
        modality.exec_dialog(dlg)
    finally:
        QDialog.exec = original
    assert dlg.accessibleName() == "Component Groups and Tags"


def test_the_diagram_canvas_has_a_text_alternative(qapp):
    from raptor.gui.ui.diagram import DiagramView
    view = DiagramView()
    try:
        assert view.accessibleName()
        assert "Model Explorer" in view.accessibleDescription()
    finally:
        view.deleteLater()
        qapp.processEvents()

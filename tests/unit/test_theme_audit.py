"""Exhaustive theming audit — token parity, WCAG contrast, render diffs,
and re-theme participation of snapshot-styled canvas items."""

from __future__ import annotations

import os
import pathlib

import pytest

from PySide6.QtWidgets import QApplication

from raptor.gui.resources.design_tokens import (
    DARK_COLOUR, LIGHT_COLOUR,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")


def _lum(hex_str: str) -> float:
    h = hex_str.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_light_dark_token_parity():
    assert set(LIGHT_COLOUR) == set(DARK_COLOUR), (
        set(LIGHT_COLOUR) ^ set(DARK_COLOUR))


def test_statusbar_uses_the_normal_ui_font():
    """The status bar uses the ordinary UI font family, like the rest of
    the app, rather than a fixed-width font."""
    from raptor.gui.resources.design_tokens import global_stylesheet, TYPE
    css = global_stylesheet()
    m = css.find("QStatusBar")
    assert m != -1
    block = css[m:css.find("}", m)]
    assert TYPE["family"] in block


def test_stylesheet_keys_all_exist():
    import re
    src = (REPO / "raptor/gui/resources/design_tokens.py").read_text()
    keys = set(re.findall(r"\{c\['(\w+)'\]\}", src))
    assert keys <= set(LIGHT_COLOUR), keys - set(LIGHT_COLOUR)
    # Build each theme's stylesheet directly rather than calling the
    # app-wide set_dark(), which re-polishes every widget the suite has
    # leaked so far and can hang. Theme application is covered by the
    # isolated subprocess in test_theme_toggle_changes_render.
    from raptor.gui.resources.design_tokens import _build_stylesheet
    assert "{" in _build_stylesheet(LIGHT_COLOUR)
    assert "{" in _build_stylesheet(DARK_COLOUR)


def test_contrast_aa_muted_and_legend_text():
    """Contrast floors: muted text >= 4.5 on every light surface it
    appears on; legend words use the theme's TEXT colour."""
    for bg in ("surface", "canvas", "group_header"):
        assert _ratio(LIGHT_COLOUR["muted"], LIGHT_COLOUR[bg]) >= 4.5, bg
        assert _ratio(DARK_COLOUR["muted"], DARK_COLOUR[bg]) >= 4.5, bg
    assert _ratio(LIGHT_COLOUR["text"], LIGHT_COLOUR["surface"]) >= 7
    assert _ratio(DARK_COLOUR["text"], DARK_COLOUR["surface"]) >= 7


def test_theme_toggle_changes_render(qapp):
    """Render-diff in a subprocess: grabbing view pixmaps around theme
    toggles leaves native resources that segfault at interpreter exit
    (all tests pass; only teardown crashes). Isolating it keeps the main
    suite's exit clean."""
    import subprocess
    import sys
    code = f"""
import os, sys
sys.path.insert(0, {str(REPO)!r})
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow
w = MainWindow(); w.resize(1200, 800); w.show()
w._load_into_app(load_rbd({str(GOLD)!r} + "/TwoBlockRedundancy/input.rbd"), "x")
from raptor.gui.resources.design_tokens import ThemeManager
tm = ThemeManager.instance()
start = tm.is_dark
pm = {{}}
for dark in (not start, start):
    tm.set_dark(dark)
    app.processEvents()
    pm[dark] = w._diagram_dock.view.viewport().grab().toImage()
assert not pm[True].isNull() and not pm[False].isNull()
assert pm[True] != pm[False], "canvas identical in both themes"
print("RENDER_DIFF_OK", flush=True)
os._exit(0)   # skip poisoned Qt teardown entirely
"""
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert "RENDER_DIFF_OK" in proc.stdout, (
        f"{proc.stdout}\n{proc.stderr[-800:]}")


def test_hier_item_dsi_dot_rethemes(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        GOLD, "HierarchyExample", "input.rbd")))
    v.apply_hier_dsi({23: {"state": "!unknown", "nodes": 1, "down": 0,
                           "degraded": 0}})
    item = v._hier_items[23]
    # Appendix D state colours are deliberately theme-invariant; the
    # FALLBACK tint comes from c["muted"], which differs per theme --
    # so use it to prove retheme() actually re-derives the dot.
    before = item._dsi_dot.brush().color().name()
    # Hand the proxy a DARK palette directly: a global set_dark() this
    # late in the suite re-polishes every leaked widget and can hang,
    # and retheme(c) is the unit under test.
    item.retheme(DARK_COLOUR)          # DiagramView.retheme() reads
    after = item._dsi_dot.brush().color().name()   # the live proxy
    assert before != after, "DSI dot kept its old theme tint"


def test_file_dialog_nav_buttons_use_theme_tinted_icons(qapp):
    """The Save dialog's Back/Forward/Up buttons use the platform's
    SP_Arrow* icons -- a fixed mid-grey that is nearly invisible against
    the dark-theme navy and only faint on light. `_make_file_dialog`
    repaints them from the active theme's text colour, as `icons.py` does
    for the ribbon."""
    from PySide6.QtWidgets import QFileDialog, QToolButton
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.resources import icons
    from raptor.gui.resources.design_tokens import active_colour

    w = MainWindow()
    dlg = w._make_file_dialog("Save RBD File As", QFileDialog.AcceptSave)
    by_name = {tb.objectName(): tb for tb in dlg.findChildren(QToolButton)}

    text_colour = active_colour()["text"]
    expected = {
        "backButton": icons.back_arrow_icon(text_colour),
        "forwardButton": icons.forward_arrow_icon(text_colour),
        "toParentButton": icons.up_arrow_icon(text_colour),
    }
    for name, want_icon in expected.items():
        got = by_name[name].icon()
        assert not got.isNull()
        assert got.pixmap(18, 18).toImage() == want_icon.pixmap(18, 18).toImage(), \
            f"{name} must use the theme-tinted icon, not the platform default"


# ---- WCAG contrast of the coloured tokens, chart fills and inks ----------

_PANELS = ("surface", "canvas", "group_header", "input_bg")
#: Tokens the GUI also draws TEXT in (links, the red ">" censoring marker,
#: diff rows, relationship labels): 4.5:1 on every panel background.
_TEXT_TOKENS = ("text", "muted", "accent", "warn", "result_green",
                "result_red", "relationship")
#: Tokens drawn as marks only (bars, dots, lines, icons): 3:1 (WCAG 1.4.11).
#: `border` is a decorative gridline / frame and exempt.
_MARK_TOKENS = ("stop", "go", "result_yellow")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_coloured_text_tokens_meet_aa(theme):
    P = LIGHT_COLOUR if theme == "light" else DARK_COLOUR
    low = [(k, bg, round(_ratio(P[k], P[bg]), 2))
           for k in _TEXT_TOKENS for bg in _PANELS
           if _ratio(P[k], P[bg]) < 4.5]
    assert not low, low


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_mark_tokens_meet_non_text_contrast(theme):
    P = LIGHT_COLOUR if theme == "light" else DARK_COLOUR
    low = [(k, bg, round(_ratio(P[k], P[bg]), 2))
           for k in _MARK_TOKENS + _TEXT_TOKENS for bg in _PANELS
           if _ratio(P[k], P[bg]) < 3.0]
    assert not low, low


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_accent_fill_ink(theme):
    """Accent-filled buttons draw their label in the surface colour."""
    P = LIGHT_COLOUR if theme == "light" else DARK_COLOUR
    assert _ratio(P["surface"], P["accent"]) >= 4.5
    from raptor.gui.resources.design_tokens import _build_stylesheet
    css = _build_stylesheet(P)
    for selector in ("QPushButton:default {", "#RibbonFileButton:hover {"):
        block = css[css.index(selector):]
        block = block[:block.index("}")]
        assert f"color: {P['surface']}" in block, selector


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chart_cause_fills_and_inks(qapp, theme):
    """Every cause / cost-group fill is a 3:1 mark on the chart surface and
    the label ink chosen for it is 4.5:1 on the fill."""
    from PySide6.QtGui import QColor
    from raptor.gui.ui import chart_theme
    P = LIGHT_COLOUR if theme == "light" else DARK_COLOUR
    low = []
    for cause, (light, dark) in chart_theme._COLOURS.items():
        fill = light if theme == "light" else dark
        if _ratio(fill, P["surface"]) < 3.0:
            low.append((cause, "fill", round(_ratio(fill, P["surface"]), 2)))
        ink = chart_theme.ink_on(QColor(fill)).name()
        if _ratio(ink, fill) < 4.5:
            low.append((cause, "ink", ink, round(_ratio(ink, fill), 2)))
    assert not low, low


def test_svg_chart_colours_and_inks():
    from raptor.render import charts_svg as sv
    low = []
    for cause, fill in sv.CAUSE_COLOURS.items():
        if _ratio(fill, "#ffffff") < 3.0:
            low.append((cause, "fill"))
        if _ratio(sv._ink_on(fill), fill) < 4.5:
            low.append((cause, "ink", sv._ink_on(fill)))
    for name in ("INK", "MUTED", "ACCENT", "UP", "DOWN"):
        if _ratio(getattr(sv, name), "#ffffff") < 4.5:
            low.append(name)
    assert not low, low


def test_ink_on_picks_the_higher_contrast_ink(qapp):
    """It used to test against pure black and then draw #111111, putting
    4.1:1 text on mid greys such as #6B7785."""
    from PySide6.QtGui import QColor
    from raptor.gui.ui.chart_theme import ink_on
    from raptor.render.charts_svg import _ink_on
    assert ink_on(QColor("#6B7785")).name() == "#ffffff"
    assert _ink_on("#6B7785") == "#ffffff"
    assert ink_on(QColor("#F2B84B")).name() == "#111111"


def test_first_light_set_dark_still_applies_the_palette(monkeypatch):
    """Light is the starting state, so a light launch's `set_dark(False)`
    used to return early and leave the app on the platform palette."""
    from raptor.gui.resources.design_tokens import ThemeManager
    tm = ThemeManager()
    calls = []

    def fake_apply():
        calls.append(tm.is_dark)
        tm._applied = True
    monkeypatch.setattr(tm, "_apply", fake_apply)
    tm.set_dark(False)
    tm.set_dark(False)
    assert calls == [False]


def test_links_take_the_new_palette_after_a_theme_switch():
    """A rich-text label under the app style sheet keeps the link colour
    of the moment its text was set; `_relink_labels` (run by every theme
    apply) re-sets it, or a switch to dark left dark-blue links on the dark
    surface.  In a subprocess: it switches the app-wide theme."""
    import subprocess
    import sys
    code = f"""
import os, sys
sys.path.insert(0, {str(REPO)!r})
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication, QLabel
app = QApplication([])
from raptor.gui.resources.design_tokens import (
    DARK_COLOUR, LIGHT_COLOUR, ThemeManager)
tm = ThemeManager.instance()
tm.set_dark(False)
label = QLabel('<a href="x">LINKLINKLINK</a>')
label.resize(140, 24); label.show(); app.processEvents()
def colours():
    img = label.grab().toImage()
    return {{img.pixelColor(x, y).name() for x in range(140) for y in range(24)}}
assert LIGHT_COLOUR["accent"].lower() in colours(), colours()
tm.set_dark(True); app.processEvents()
assert DARK_COLOUR["accent"].lower() in colours(), colours()
print("LINKS_OK", flush=True)
os._exit(0)
"""
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert "LINKS_OK" in proc.stdout, f"{proc.stdout}\n{proc.stderr[-800:]}"


def test_no_pseudo_state_changes_a_font(qapp):
    """Qt sizes a widget from its plain font, so a bolder or larger font
    under :default, :selected, :checked or :hover is drawn in a box sized
    for the smaller one and is cut off ("Jse for Intake_Screer")."""
    import re
    from raptor.gui.resources.design_tokens import ThemeManager
    for dark in (False, True):
        ThemeManager.instance().set_dark(dark)
        sheet = qapp.styleSheet()
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet):
            states = re.findall(r":(default|selected|checked|hover|pressed|focus|on)\b",
                                selector)
            if states and "::" not in selector:
                assert "font" not in body, selector.strip()
    ThemeManager.instance().set_dark(False)


def test_a_default_buttons_label_fits(qapp):
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton
    from raptor.gui.resources.design_tokens import ThemeManager
    ThemeManager.instance().set_dark(True)
    dlg = QDialog()
    lay = QHBoxLayout(dlg)
    btn = QPushButton("Use for Intake_Screen")
    btn.setDefault(True)
    lay.addWidget(btn)
    lay.addStretch(1)
    dlg.show()
    qapp.processEvents()
    room = btn.contentsRect().width()
    assert btn.fontMetrics().horizontalAdvance(btn.text()) < room
    dlg.close()
    ThemeManager.instance().set_dark(False)

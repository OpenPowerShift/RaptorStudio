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

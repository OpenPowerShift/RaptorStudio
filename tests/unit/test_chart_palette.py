"""Chart palettes: user choice, contrast and agreement across surfaces."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from raptor.api.insight import charts, palette
from raptor.render import charts_svg


def _luminance(colour: str) -> float:
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


@pytest.fixture(autouse=True)
def restore_palette():
    before = palette.active()
    yield
    palette.set_active(before)


def test_the_default_is_muted_and_the_choices_are_named():
    assert palette.DEFAULT == "muted"
    assert "classic" in palette.names()
    assert "colourblind" in palette.names()
    assert "high_contrast" in palette.names()
    assert palette.label("muted").lower().startswith("muted")
    assert palette.why("colourblind")


def test_every_palette_is_legible_in_both_themes():
    """A fill is a 3:1 mark on the chart surface and an ink for its label
    is 4.5:1 on the fill.  Colour is never the only cue, but it must read."""
    surfaces = {"light": "#FFFFFF", "dark": "#1E2329"}
    for name in palette.names():
        for theme, surface in surfaces.items():
            for cause, fill in palette.colours(name, dark=theme == "dark").items():
                assert _contrast(fill, surface) >= 3.0, (name, theme, cause)
                best = max(_contrast(fill, "#111111"), _contrast(fill, "#ffffff"))
                assert best >= 4.5, (name, theme, cause)


def test_set_active_ignores_an_unknown_name():
    palette.set_active("classic")
    assert palette.colour("repair") == "#C4472F"
    assert palette.set_active("no such palette") == palette.DEFAULT
    assert palette.active() == palette.DEFAULT


def test_a_palette_changes_the_svg_but_keeps_the_patterns():
    attribution = {
        "runs": 2, "causes": {"repair": 0.6, "wait_spare": 0.4},
        "elements": [
            {"name": "Pump", "dtci": {"mean": 0.4}, "causes": {"repair": 1.0},
             "main_cause": "repair"},
            {"name": "Valve", "dtci": {"mean": 0.2},
             "causes": {"wait_spare": 1.0}, "main_cause": "wait_spare"},
        ],
    }
    spec = charts.downtime_pareto(attribution)
    palette.set_active("muted")
    muted = charts_svg.render(spec)
    palette.set_active("classic")
    classic = charts_svg.render(spec)
    assert muted != classic
    assert "pat-wait_spare" in muted and "pat-wait_spare" in classic
    assert palette.colour("repair") in classic


def test_qt_cause_colours_follow_the_chosen_palette(qapp):
    from PySide6.QtGui import QColor
    from raptor.gui.ui import chart_theme
    palette.set_active("classic")
    assert chart_theme.cause_colour("repair") == QColor("#C4472F")
    palette.set_active("colourblind")
    assert chart_theme.cause_colour("repair") == QColor("#D55E00")
    assert chart_theme.ink_on(chart_theme.cause_colour("repair")).name() \
        in {"#111111", "#ffffff"}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

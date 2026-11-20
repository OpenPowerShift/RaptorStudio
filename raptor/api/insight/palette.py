"""Chart colour palettes, chosen by the reader.

The cause names and patterns are fixed (a cause always has the same pattern
so a chart reads in greyscale); only the fills move.  Keeping the palettes
here, Qt-free, lets the desktop chart, the SVG in a report and the saved
image agree on the same choice.

Palettes are tested for contrast by `tests/unit/test_theme_audit.py`; every
fill is also drawn with its cause pattern, so colour is never the only cue.
"""
from __future__ import annotations

from typing import Any

#: A palette maps a cause to (light, dark) fills.  The names are stable:
#: they are stored in settings and travel in a report's provenance.
PALETTES: dict[str, dict[str, Any]] = {
    "muted": {
        "label": "Muted (recommended)",
        "why": "Softer fills that sit quietly behind labels; repair is still red.",
        "light": {
            "repair": "#B5533F", "wait_spare": "#A97A2E",
            "wait_resource": "#3A6E9E", "logistics": "#7D6A9E",
            "pm": "#2F746C", "pm_wait": "#5E8C45",
            "dependency": "#7A8794", "done": "#4A5563", "other": "#8A949E",
            "purchase": "#3A6E9E", "operating": "#8A4F22",
            "sparing": "#A97A2E", "disposal": "#7A8794",
            "spare_pool": "#87679A",
        },
        "dark": {
            "repair": "#E08A76", "wait_spare": "#D8B86A",
            "wait_resource": "#79AEDC", "logistics": "#B69AD0",
            "pm": "#72C2B6", "pm_wait": "#A5CC85",
            "dependency": "#AAB5C2", "done": "#C2CAD4", "other": "#9AA4AF",
            "purchase": "#79AEDC", "operating": "#D89A6A",
            "sparing": "#D8B86A", "disposal": "#AAB5C2",
            "spare_pool": "#C6A3D8",
        },
    },
    "classic": {
        "label": "Raptor classic",
        "why": "The original, more saturated red and teal.",
        "light": {
            "repair": "#C4472F", "wait_spare": "#B07806",
            "wait_resource": "#2F6FB7", "logistics": "#7A5AA6",
            "pm": "#2A8C82", "pm_wait": "#5E9C3F",
            "dependency": "#6B7785", "done": "#3C4653", "other": "#848F9B",
            "purchase": "#2F6FB7", "operating": "#A9591A",
            "sparing": "#B07806", "disposal": "#6B7785",
            "spare_pool": "#8E5B9E",
        },
        "dark": {
            "repair": "#F0806A", "wait_spare": "#F2B84B",
            "wait_resource": "#6FA8E8", "logistics": "#B095DA",
            "pm": "#5CC7BB", "pm_wait": "#93CC72",
            "dependency": "#A4B0BF", "done": "#D0D7E0", "other": "#75808D",
            "purchase": "#6FA8E8", "operating": "#E79A5A",
            "sparing": "#F2B84B", "disposal": "#A4B0BF",
            "spare_pool": "#C394D3",
        },
    },
    "colourblind": {
        "label": "Colour-blind (Okabe–Ito)",
        "why": "The Okabe–Ito set; repair is vermillion rather than pure red.",
        "light": {
            "repair": "#D55E00", "wait_spare": "#A26600",
            "wait_resource": "#0072B2", "logistics": "#CC79A7",
            "pm": "#009E73", "pm_wait": "#2A7396",
            "dependency": "#666666", "done": "#222222", "other": "#767676",
            "purchase": "#0072B2", "operating": "#8C510A",
            "sparing": "#A26600", "disposal": "#666666",
            "spare_pool": "#CC79A7",
        },
        "dark": {
            "repair": "#F2915A", "wait_spare": "#F2C15A",
            "wait_resource": "#5AB4E8", "logistics": "#E3A6C6",
            "pm": "#4AC8A6", "pm_wait": "#8CD0F2",
            "dependency": "#B0B0B0", "done": "#E0E0E0", "other": "#BDBDBD",
            "purchase": "#5AB4E8", "operating": "#D9A066",
            "sparing": "#F2C15A", "disposal": "#B0B0B0",
            "spare_pool": "#E3A6C6",
        },
    },
    "high_contrast": {
        "label": "High contrast",
        "why": "Darker fills and brighter dark-theme fills for low-vision readers.",
        "light": {
            "repair": "#B00000", "wait_spare": "#7A4E00",
            "wait_resource": "#003F8A", "logistics": "#4A1D78",
            "pm": "#005F52", "pm_wait": "#2F6B00",
            "dependency": "#2E2E2E", "done": "#000000", "other": "#5F5F5F",
            "purchase": "#003F8A", "operating": "#7A3D00",
            "sparing": "#7A4E00", "disposal": "#2E2E2E",
            "spare_pool": "#6B3D82",
        },
        "dark": {
            "repair": "#FF8A73", "wait_spare": "#FFD166",
            "wait_resource": "#70C0FF", "logistics": "#D9B0FF",
            "pm": "#68E0CE", "pm_wait": "#B6E36B",
            "dependency": "#D0D0D0", "done": "#FFFFFF", "other": "#C8C8C8",
            "purchase": "#70C0FF", "operating": "#FFB077",
            "sparing": "#FFD166", "disposal": "#D0D0D0",
            "spare_pool": "#E0B6F5",
        },
    },
}

DEFAULT = "muted"
_active = DEFAULT


def names() -> list[str]:
    return list(PALETTES)


def label(name: str) -> str:
    return str(PALETTES.get(name, PALETTES[DEFAULT])["label"])


def why(name: str) -> str:
    return str(PALETTES.get(name, PALETTES[DEFAULT])["why"])


def set_active(name: str) -> str:
    """Choose the palette for every chart in this process; returns its key."""
    global _active
    _active = name if name in PALETTES else DEFAULT
    return _active


def active() -> str:
    return _active


def colour(cause: str, *, dark: bool = False) -> str:
    entry = PALETTES.get(_active, PALETTES[DEFAULT])
    side = entry["dark" if dark else "light"]
    return str(side.get(cause, side["other"]))


def colours(name: str | None = None, *, dark: bool = False) -> dict[str, str]:
    entry = PALETTES.get(name or _active, PALETTES[DEFAULT])
    return dict(entry["dark" if dark else "light"])

"""Qt-free estimate of an element's drawn size, in scene pixels.

The canvas grows a block from ``BLOCK_W`` to ``BLOCK_MAX_W`` to fit a long
name, and a node/hierarchy label can be wider than its symbol. Auto layout,
the model-to-scene scale and the API/MCP paths all need the same answer, so
this is the one owner. It is an estimate, not a font engine: the GUI may
override it with exact ``QFontMetricsF`` extents, but the estimate keeps the
headless paths consistent and is deliberately slightly conservative.
"""
from __future__ import annotations

#: Average glyph width as a fraction of the font's pixel size. Sans-serif
#: bold at 10 pt is roughly 0.58 em; 0.62 leaves room for wide glyphs.
_GLYPH = 0.62
#: Extra horizontal room around a label (the item's own padding).
_PAD = 10.0

#: The model-units-per-pixel scale the headless layout paths size their
#: columns at. The GUI measures exact Qt extents and divides by this same
#: reference, NOT by the view's current scale: a layout pass must produce
#: the same model-unit pitches every time, or re-running Auto Layout would
#: feed a shrinking scale back into the next pass and ratchet the diagram.
#: The transform itself is then derived from the drawn extents.
REFERENCE_SCALE = 64.0

#: Name column per section.
NAME_COLUMNS = {
    "BLOCK_DATA": "blockName",
    "NODE_DATA": "Name",
    "EVENT_DATA": "eventName",
    "HIERARCHY_DATA": "hierarchyName",
}


def _font_px(pt: float) -> float:
    # Qt's default logical DPI is 96, so 1 pt = 96/72 = 1.333 px.
    return float(pt) * 96.0 / 72.0


def text_width(name: str, pt: float) -> float:
    """Rough rendered width of ``name`` at ``pt`` logical pixels."""
    return len(str(name or "")) * _font_px(pt) * _GLYPH


def half_width_px(section: str, name: str) -> float:
    """Half the drawn width of an element, in scene pixels."""
    from raptor.core.fileio.view_transform import BLOCK_W, NODE_R

    if section == "BLOCK_DATA":
        # The canvas widens a block to twice BLOCK_W at most
        # (`BLOCK_MAX_W` in gui/ui/diagram_items.py).
        width = max(BLOCK_W, text_width(name, 10) + 2 * _PAD)
        return min(width, 2 * BLOCK_W) / 2.0
    if section == "NODE_DATA":
        # Node names sit above the circle; the wider of the two wins.
        return max(float(NODE_R), text_width(name, 9) / 2.0 + _PAD / 2.0)
    if section == "EVENT_DATA":
        return max(float(NODE_R) + 2.0, text_width(name, 8) / 2.0 + _PAD / 2.0)
    if section == "HIERARCHY_DATA":
        # Hierarchy oval is 150 px wide; a longer name widens its box for
        # collision purposes even though the oval itself stays fixed.
        return max(75.0, text_width(name, 10) / 2.0 + _PAD / 2.0)
    return 54.0


def half_height_px(section: str, name: str = "") -> float:
    """Half the drawn height, including the label where it matters."""
    if section == "BLOCK_DATA":
        return 23.0
    if section == "NODE_DATA":
        return max(18.0, 9.0 + 2.0 + 18.0)
    if section == "EVENT_DATA":
        return 22.0
    if section == "HIERARCHY_DATA":
        return 28.0
    return 23.0


def model_half_width(section: str, name: str, scale: float) -> float:
    """Half width in model units for a given scene scale."""
    return half_width_px(section, name) / max(float(scale), 1e-9)


def model_half_height(section: str, name: str, scale: float) -> float:
    return half_height_px(section, name) / max(float(scale), 1e-9)

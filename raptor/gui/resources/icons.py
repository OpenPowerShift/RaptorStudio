"""Theme-aware painted icons for the ribbon.

Stock QStyle standard icons (SP_MediaPlay and similar) are platform-drawn
bitmaps whose colours do not follow the active theme, so they can be
near-invisible against our palettes. Painting the glyphs from the active
palette's tokens guarantees contrast in both themes; callers re-fetch
them on every theme switch.
"""
from __future__ import annotations

from math import atan2, cos, pi, sin

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QIcon, QPainter,
                           QPixmap, QPen, QPolygonF)

_SIZE = 48
_cache: dict[tuple[str, str], QIcon] = {}


def _painter(pm: QPixmap) -> QPainter:
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return p


def _disabled_colour() -> str:
    from raptor.gui.resources.design_tokens import active_colour
    return active_colour()["muted"]


def _icon(name: str, colour_hex: str, draw) -> QIcon:
    """Paint `draw` into an icon, with its own disabled image.

    Qt derives a disabled image by greying the normal one, which on the
    dark theme's navy left a disabled glyph almost as bright as an enabled
    one. The disabled image is painted instead: the same shape in the
    theme's muted colour at half strength, so it reads as unavailable in
    both themes."""
    disabled = _disabled_colour()
    key = (name, colour_hex, disabled)
    if key in _cache:
        return _cache[key]
    icon = QIcon()
    for mode, colour, opacity in ((QIcon.Mode.Normal, colour_hex, 1.0),
                                  (QIcon.Mode.Disabled, disabled, 0.55)):
        pm = QPixmap(_SIZE, _SIZE)
        pm.fill(Qt.GlobalColor.transparent)
        p = _painter(pm)
        p.setOpacity(opacity)
        draw(p, QColor(colour))
        p.end()
        icon.addPixmap(pm, mode)
    _cache[key] = icon
    return icon


def play_icon(colour: str) -> QIcon:
    """Filled Run triangle."""
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([
            QPointF(14, 8), QPointF(40, 24), QPointF(14, 40),
        ]))
    return _icon("play", colour, draw)


def more_icon(colour: str) -> QIcon:
    """Three dots: a folded ribbon group none of whose commands has an
    icon of its own."""
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for x in (11, 24, 37):
            p.drawEllipse(QPointF(x, 24), 4.5, 4.5)
    return _icon("more", colour, draw)


def pointer_icon(colour: str) -> QIcon:
    """Select-tool mouse-pointer arrow."""
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([
            QPointF(15, 6), QPointF(15, 38), QPointF(23, 30),
            QPointF(29, 42), QPointF(35, 39), QPointF(29, 27),
            QPointF(39, 26),
        ]))
    return _icon("pointer", colour, draw)


def chart_icon(colour: str) -> QIcon:
    """Show-Results bar chart."""
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(8, 24, 8, 16)
        p.drawRect(20, 12, 8, 28)
        p.drawRect(32, 18, 8, 22)
    return _icon("chart", colour, draw)


def _magnifier(p: QPainter, c: QColor):
    from PySide6.QtGui import QPen
    p.setPen(QPen(c, 4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(9, 9, 22, 22)   # lens
    p.drawLine(28, 28, 40, 40)    # handle


def zoom_in_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        _magnifier(p, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(18, 13, 5, 15)   # vertical bar of "+"
        p.drawRect(13, 18, 15, 5)   # horizontal bar
    return _icon("zoom_in", colour, draw)


def zoom_out_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        _magnifier(p, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(13, 18, 15, 5)   # just the horizontal bar of "-"
    return _icon("zoom_out", colour, draw)


def table_grid_icon(colour: str) -> QIcon:
    """A small table: shaded header row over a grid (Export Tables)."""
    def draw(p: QPainter, c: QColor):
        head = QColor(c)
        head.setAlpha(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(head)
        p.drawRect(7, 9, 34, 9)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 3))
        p.drawRect(7, 9, 34, 30)
        p.setPen(QPen(c, 2))
        p.drawLine(7, 18, 41, 18)
        p.drawLine(7, 28, 41, 28)
        p.drawLine(19, 9, 19, 39)
        p.drawLine(30, 9, 30, 39)
    return _icon("table_grid", colour, draw)


def zoom_selection_icon(colour: str) -> QIcon:
    """Magnifier over a selection box (Zoom to Selection)."""
    def draw(p: QPainter, c: QColor):
        _magnifier(p, c)
        p.setPen(QPen(c, 2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(15, 15, 10, 10)
    return _icon("zoom_selection", colour, draw)


def zoom_hierarchy_icon(colour: str) -> QIcon:
    """Magnifier over an oval (Zoom to Hierarchy: enter the oval)."""
    def draw(p: QPainter, c: QColor):
        _magnifier(p, c)
        p.setPen(QPen(c, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(13, 16, 14, 8)
    return _icon("zoom_hierarchy", colour, draw)


def flip_h_icon(colour: str) -> QIcon:
    """Two triangles mirrored across a vertical axis (Flip Horizontal)."""
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 2, Qt.PenStyle.DashLine))
        p.drawLine(24, 4, 24, 44)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(19, 10), QPointF(19, 38),
                                 QPointF(5, 38)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 3))
        p.drawPolygon(QPolygonF([QPointF(29, 10), QPointF(29, 38),
                                 QPointF(43, 38)]))
    return _icon("flip_h", colour, draw)


def flip_v_icon(colour: str) -> QIcon:
    """Two triangles mirrored across a horizontal axis (Flip Vertical)."""
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 2, Qt.PenStyle.DashLine))
        p.drawLine(4, 24, 44, 24)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(10, 19), QPointF(38, 19),
                                 QPointF(38, 5)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 3))
        p.drawPolygon(QPolygonF([QPointF(10, 29), QPointF(38, 29),
                                 QPointF(38, 43)]))
    return _icon("flip_v", colour, draw)


def save_icon(colour: str) -> QIcon:
    """Floppy-disk Save, in place of the stock SP_DialogSaveButton
    bitmap."""
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        # Disk body with clipped top-right corner.
        p.drawPolygon(QPolygonF([
            QPointF(8, 8), QPointF(34, 8), QPointF(40, 14), QPointF(40, 40),
            QPointF(8, 40),
        ]))
        # Punch the label window and shutter slot out of the disk body.
        from PySide6.QtGui import QPainter
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.drawRect(15, 26, 18, 14)          # label window
        p.drawRect(24, 9, 10, 10)           # shutter slot
    return _icon("save", colour, draw)


# --- Build-tool glyphs -------------------------------------------------
#: Outline icons, 2px stroke, accent-coloured, per the design system's
#: iconography spec.


def _outline_pen(c: QColor) -> QPen:
    from PySide6.QtGui import QPen as _QP
    return _QP(c, 3)


# ---------------------------------------------------------------------------
# Arrowhead geometry, shared by the canvas and the ribbon glyph
# ---------------------------------------------------------------------------
#: Head length at a 1px shaft, and how much it grows per extra pixel of
#: shaft width. A head sized for a hairline all but vanishes the moment
#: the user picks a thick "Line width" in the Inspector -- an 8px head
#: on an 8px shaft reads as a blunt smudge, not an arrow -- so the head
#: scales with the shaft.
ARROW_HEAD_LEN = 8.0
ARROW_HEAD_PER_WIDTH = 2.5
#: A 90-degree half-spread puts each barb perpendicular to the shaft, so
#: neither can overshoot the tip. Wider spreads drive `cos()` negative
#: and put the barbs IN FRONT of the point, which draws the triangle on
#: the wrong side of the shaft -- the bug that made the ribbon arrow
#: glyph look like it was pointing backwards.
ARROW_HEAD_SPREAD = pi / 2


def arrow_head_len(width: float) -> float:
    """Arrowhead length for a shaft `width` pixels wide."""
    return max(ARROW_HEAD_LEN, ARROW_HEAD_PER_WIDTH * max(1.0, width))


def arrow_head_points(x1: float, y1: float, x2: float, y2: float,
                      head_len: float) -> QPolygonF:
    """The arrowhead triangle: tip at (x2, y2), both barbs placed from
    the point `head_len` BEHIND the tip and offset to either side.

    Placed relative to a back-point rather than as `tip - L*cos(angle ±
    spread)` so the barbs are behind the tip by construction. Used by
    `arrow_annotation_icon` and by the canvas's own arrow, so the ribbon
    glyph and the on-canvas arrow are the same shape by definition
    rather than by two implementations agreeing."""
    angle = atan2(y2 - y1, x2 - x1)
    back_x = x2 - head_len * cos(angle)
    back_y = y2 - head_len * sin(angle)
    half = head_len * 0.55
    return QPolygonF([
        QPointF(x2, y2),
        QPointF(back_x + half * cos(angle + ARROW_HEAD_SPREAD),
                back_y + half * sin(angle + ARROW_HEAD_SPREAD)),
        QPointF(back_x + half * cos(angle - ARROW_HEAD_SPREAD),
                back_y + half * sin(angle - ARROW_HEAD_SPREAD)),
    ])


def block_icon(colour: str) -> QIcon:
    """Block element: nested double square (the icon gallery's 'B')."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(7, 7, 34, 34)
        p.drawRect(15, 15, 18, 18)
    return _icon("block", colour, draw)


def node_icon(colour: str) -> QIcon:
    """Node element: filled circle inside an outline ring ('N')."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawEllipse(7, 7, 34, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(16, 16, 16, 16)
    return _icon("node", colour, draw)


def event_icon(colour: str) -> QIcon:
    """Event element: rounded outline square with the letter E."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRoundedRect(7, 7, 34, 34, 6, 6)
        p.setPen(_outline_pen(c))
        f = QFont("sans-serif", 20)
        f.setBold(True)
        p.setFont(f)
        p.drawText(7, 4, 34, 34, Qt.AlignmentFlag.AlignCenter, "E")
    return _icon("event", colour, draw)


def connect_icon(colour: str) -> QIcon:
    """Connect tool: diagonal flow arrow between two endpoints ('L')."""
    def draw(p: QPainter, c: QColor):
        from PySide6.QtGui import QPen
        p.setPen(QPen(c, 3))
        p.drawLine(9, 39, 36, 12)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([
            QPointF(40, 8), QPointF(41, 22), QPointF(28, 9),
        ]))
        p.drawEllipse(QPointF(9, 39), 4, 4)
    return _icon("connect", colour, draw)


def delete_icon(colour: str) -> QIcon:
    """Delete: trash can outline."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(11, 14, 26, 28)
        p.drawLine(7, 14, 41, 14)
        p.drawLine(17, 14, 17, 8)
        p.drawLine(17, 8, 31, 8)
        p.drawLine(31, 8, 31, 14)
        p.drawLine(19, 20, 19, 36)
        p.drawLine(24, 20, 24, 36)
        p.drawLine(29, 20, 29, 36)
    return _icon("delete", colour, draw)


def _curved_arrow(p: QPainter, c: QColor, flip: bool):
    from PySide6.QtGui import QPen
    p.setPen(QPen(c, 3.5))
    rect = QRectF(10, 12, 26, 26) if not flip else QRectF(12, 12, 26, 26)
    p.drawArc(rect, 30 * 16 if not flip else -30 * 16, 210 * 16 if not flip else -210 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    if flip:
        p.drawPolygon(QPolygonF([QPointF(38, 6), QPointF(44, 18), QPointF(30, 16)]))
    else:
        p.drawPolygon(QPolygonF([QPointF(10, 6), QPointF(4, 18), QPointF(18, 16)]))


def undo_icon(colour: str) -> QIcon:
    """Undo: left hook-arrow (the standard modern glyph)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        # Hook: horizontal shaft curving up to the right.
        p.drawPolyline([QPointF(36, 34), QPointF(36, 22), QPointF(14, 22)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([
            QPointF(16, 10), QPointF(16, 34), QPointF(2, 22),
        ]))
    return _icon("undo", colour, draw)


def redo_icon(colour: str) -> QIcon:
    """Redo: right hook-arrow (mirror)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline([QPointF(12, 34), QPointF(12, 22), QPointF(34, 22)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([
            QPointF(32, 10), QPointF(32, 34), QPointF(46, 22),
        ]))
    return _icon("redo", colour, draw)


def float_window_icon(colour: str) -> QIcon:
    """Float-Window: a window popping out of another."""
    def draw(p: QPainter, c: QColor):
        from PySide6.QtGui import QPen
        # Back window outline
        p.setPen(QPen(c, 3.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(8, 8, 22, 18)
        # Front window, filled
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(20, 22, 21, 17)
    return _icon("floatwin", colour, draw)


def hierarchy_icon(colour: str) -> QIcon:
    """Hierarchy element: elongated oval containing two small squares
    (Raptor 7 hierarchy symbol with children inside)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRoundedRect(4, 14, 40, 20, 10, 10)
        p.setPen(_outline_pen(c))
        p.drawRect(13, 19, 9, 9)
        p.drawRect(26, 19, 9, 9)
    return _icon("hierarchy", colour, draw)


def collapse_icon(colour: str) -> QIcon:
    """Collapse into hierarchy: two squares with inward arrows merging
    into one oval below."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(8, 6, 11, 11)
        p.drawRect(29, 6, 11, 11)
        # inward arrows
        p.drawLine(16, 12, 22, 18)
        p.drawLine(32, 12, 26, 18)
        arrow = QPolygonF([QPointF(22, 18), QPointF(17, 17),
                           QPointF(21, 13)])
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(arrow)
        arrow2 = QPolygonF([QPointF(26, 18), QPointF(31, 17),
                            QPointF(27, 13)])
        p.drawPolygon(arrow2)
        # target oval
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawEllipse(14, 28, 20, 12)
    return _icon("collapse", colour, draw)


# --- Annotation glyphs --------------------------------------------------
#: Distinct from block_icon/node_icon/event_icon -- reusing those icons
#: for annotations would make an annotation button and a model-element
#: button on the same ribbon page look identical. Dashed strokes read
#: as "markup" rather than "structure".


def text_annotation_icon(colour: str) -> QIcon:
    return _text_glyph("T", "anno_text", colour)


def rect_annotation_icon(colour: str) -> QIcon:
    """Rectangle annotation: dashed outline (vs block_icon's solid
    nested squares)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = _outline_pen(c)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRect(8, 10, 32, 28)
    return _icon("rect_annotation", colour, draw)


def ellipse_annotation_icon(colour: str) -> QIcon:
    """Ellipse annotation: dashed outline (vs node_icon's filled dot
    inside a solid ring)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = _outline_pen(c)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawEllipse(6, 10, 36, 28)
    return _icon("ellipse_annotation", colour, draw)


def arrow_annotation_icon(colour: str) -> QIcon:
    """Arrow annotation: a diagonal line with an arrowhead (vs the
    dashed-outline shapes above -- this one is solid, since a dashed
    arrow shaft would be hard to tell from the rect/ellipse glyphs at
    icon size).

    The head comes from the canvas's own `arrow_head_points`, so the
    ribbon glyph and the on-canvas arrow are literally the same shape.
    The previous local version placed its two barbs at
    `tip - head_len*cos(angle ± 144°)`; with a spread that wide
    `cos()` goes negative, so both barbs landed IN FRONT of the tip and
    the triangle came out drawn on the wrong side -- the glyph read as
    pointing back down the shaft."""
    def draw(p: QPainter, c: QColor):
        x1, y1, x2, y2 = 8.0, 36.0, 33.0, 11.0
        pen = _outline_pen(c)
        pen.setWidth(3)
        p.setPen(pen)
        p.setBrush(QColor(c))
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.drawPolygon(arrow_head_points(x1, y1, x2, y2, 12.0))
    return _icon("arrow_annotation", colour, draw)


# --- Z-order glyphs -----------------------------------------------------


def _order_icon(front: bool, double: bool, colour: str) -> QIcon:
    """Shared painter for the four z-order commands (Bring to Front /
    Bring Forward / Send Backward / Send to Back).

    An outlined square (the layer being left behind) and a FILLED square
    (the layer being moved to) either side of a clear lane, with one or
    two chevrons in the lane pointing at the filled one. A single chevron
    is one step along the stack, a doubled chevron is the absolute move
    -- the standard chevron-pair idiom, and the only cue that separates
    the four glyphs at the ribbon's 15px icon size.
    """
    name = ("bring_to_front" if front else "send_to_back") \
        if double else ("bring_forward" if front else "send_backward")

    def draw(p: QPainter, c: QColor):
        near, far = ((2, 35) if front else (32, 2))
        p.setPen(_outline_pen(c))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(far, 15, 14, 14))     # the layer left behind
        p.setBrush(c)
        p.drawRect(QRectF(near, 15, 14, 14))    # the layer moved to
        pen = _outline_pen(c)
        pen.setWidth(3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        # Chevrons point at the filled square: right for the "front"
        # pair, left for the "back" pair. Two of them is the absolute
        # move, one is a single step.
        for tip_x in ((31, 24) if double else (31,)) if front \
                else ((17, 24) if double else (17,)):
            tip = QPointF(tip_x, 23)
            arm_x = tip_x - 6 if front else tip_x + 6
            p.drawLine(QPointF(arm_x, 15), tip)
            p.drawLine(tip, QPointF(arm_x, 31))
    return _icon(name, colour, draw)


def bring_to_front_icon(colour: str) -> QIcon:
    return _order_icon(front=True, double=True, colour=colour)


def bring_forward_icon(colour: str) -> QIcon:
    return _order_icon(front=True, double=False, colour=colour)


def send_backward_icon(colour: str) -> QIcon:
    return _order_icon(front=False, double=False, colour=colour)


def send_to_back_icon(colour: str) -> QIcon:
    return _order_icon(front=False, double=True, colour=colour)


# --- Align / distribute glyphs ------------------------------------------


def align_left_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 3))
        p.drawLine(10, 4, 10, 44)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(10, 8, 14, 10)
        p.drawRect(10, 26, 24, 10)
    return _icon("align_left", colour, draw)


def align_right_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 3))
        p.drawLine(38, 4, 38, 44)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(24, 8, 14, 10)
        p.drawRect(14, 26, 24, 10)
    return _icon("align_right", colour, draw)


def align_top_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 3))
        p.drawLine(4, 10, 44, 10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(8, 10, 10, 14)
        p.drawRect(26, 10, 10, 24)
    return _icon("align_top", colour, draw)


def align_bottom_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 3))
        p.drawLine(4, 38, 44, 38)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(8, 24, 10, 14)
        p.drawRect(26, 14, 10, 24)
    return _icon("align_bottom", colour, draw)


def align_hcenter_icon(colour: str) -> QIcon:
    """Centre Horizontally: align_selected('hcenter') -- items share a
    vertical centreline."""
    def draw(p: QPainter, c: QColor):
        pen = QPen(c, 2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(24, 4, 24, 44)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(17, 8, 14, 10)
        p.drawRect(12, 26, 24, 10)
    return _icon("align_hcenter", colour, draw)


def align_vcenter_icon(colour: str) -> QIcon:
    """Centre Vertically: align_selected('vcenter') -- items share a
    horizontal centreline."""
    def draw(p: QPainter, c: QColor):
        pen = QPen(c, 2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(4, 24, 44, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(8, 17, 10, 14)
        p.drawRect(26, 12, 10, 24)
    return _icon("align_vcenter", colour, draw)


def distribute_h_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for x in (6, 20, 34):
            p.drawRect(x, 18, 8, 12)
        p.setPen(QPen(c, 2))
        p.drawLine(14, 24, 20, 24)
        p.drawLine(28, 24, 34, 24)
    return _icon("distribute_h", colour, draw)


def distribute_v_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for y in (6, 20, 34):
            p.drawRect(18, y, 12, 8)
        p.setPen(QPen(c, 2))
        p.drawLine(24, 14, 24, 20)
        p.drawLine(24, 28, 24, 34)
    return _icon("distribute_v", colour, draw)


def clear_cache():
    """Theme changed — drop cached bitmaps so the next fetch repaints."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Compact glyph set for the remaining ribbon actions. Each painter sticks
# to strokes/chevrons/bars so it reads at 24px in both themes.
# ---------------------------------------------------------------------------

def _bars(colour: str) -> QIcon:
    """Pause: two vertical bars."""
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(12, 10, 7, 28)
        p.drawRect(29, 10, 7, 28)
    return _icon("pause", colour, draw)


def _stop_sq(colour: str) -> QIcon:
    """Stop / Early Terminate: solid square."""
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(12, 12, 24, 24)
    return _icon("stop", colour, draw)


def _skip_end(colour: str) -> QIcon:
    """Jump: chevron pointing at a stop bar."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        polygon = QPolygonF([QPointF(10, 8), QPointF(26, 24), QPointF(10, 40)])
        p.drawPolygon(polygon)
        p.drawRect(30, 8, 6, 32)
    return _icon("jump", colour, draw)


def next_time_icon(colour: str) -> QIcon:
    """Step-to-next-time (Shift+F6): chevron pointing at a time tick --
    the same chevron-plus-bar family as `_skip_end`, but the bar is a
    shorter tick with a second faded tick beyond it, advancing to the
    next distinct simulation time. Painted rather than using
    SP_MediaSeekForward, because stock bitmaps don't follow the theme."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        polygon = QPolygonF([QPointF(10, 8), QPointF(26, 24), QPointF(10, 40)])
        p.drawPolygon(polygon)
        p.drawRect(31, 12, 5, 24)
        faded = QColor(c)
        faded.setAlpha(90)
        p.setBrush(faded)
        p.drawRect(40, 12, 5, 24)
    return _icon("next_time", colour, draw)


def _chevrons(colour: str, flip: bool) -> QIcon:
    """Slower/Faster: double chevron left/right."""
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        d = -1 if flip else 1
        for x0 in ((20, 30) if not flip else (28, 18)):
            p.drawLine(x0, 12, x0 - 8 * d * -1 if False else x0 + (-8 if flip else 8), 24)
            p.drawLine(x0 + (-8 if flip else 8), 24, x0, 36)
    return _icon(f"chev{'l' if flip else 'r'}", colour, draw)


def _single_arrow(p: QPainter, c: QColor, direction: str):
    p.setPen(QPen(c, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if direction == "left":
        p.drawLine(30, 12, 18, 24)
        p.drawLine(18, 24, 30, 36)
    elif direction == "right":
        p.drawLine(18, 12, 30, 24)
        p.drawLine(30, 24, 18, 36)
    else:   # "up"
        p.drawLine(12, 30, 24, 18)
        p.drawLine(24, 18, 36, 30)


def back_arrow_icon(colour: str) -> QIcon:
    """File dialog Back button. The platform's own SP_ArrowBack icon is a
    fixed mid-grey that is nearly invisible on the app's dark-theme
    navy; this paints it from the active theme's own text colour
    instead."""
    return _icon("back_arrow", colour, lambda p, c: _single_arrow(p, c, "left"))


def forward_arrow_icon(colour: str) -> QIcon:
    return _icon("forward_arrow", colour, lambda p, c: _single_arrow(p, c, "right"))


def up_arrow_icon(colour: str) -> QIcon:
    return _icon("up_arrow", colour, lambda p, c: _single_arrow(p, c, "up"))


def slower_icon(colour: str) -> QIcon:
    return _chevrons(colour, True)


def faster_icon(colour: str) -> QIcon:
    return _chevrons(colour, False)


def _text_glyph(txt: str, key: str, colour: str) -> QIcon:
    """Text as an icon, sized to FILL the box like every shape icon.

    It used a fixed 13 pt font on the 48 px canvas, so the Text
    annotation's "T" was 10 x 13 px (a quarter of the box) beside shapes
    that span 30-40 px, and read as an empty button at ribbon size."""
    def draw(p: QPainter, c: QColor):
        f = QFont("sans-serif")
        f.setBold(True)
        # Largest pixel size whose INK fits a 36 px square (not the line
        # box, which includes empty ascent and descent): short glyphs like
        # "T" hit the height cap, wide ones like "MAX" the width cap.
        size = 60
        while size > 8:
            f.setPixelSize(size)
            ink = QFontMetrics(f).tightBoundingRect(txt)
            if ink.width() <= 36 and ink.height() <= 36:
                break
            size -= 1
        p.setFont(f)
        p.setPen(_outline_pen(c))
        # Centre the ink, not the line box.
        p.drawText(QPointF(24 - ink.x() - ink.width() / 2,
                           24 - ink.y() - ink.height() / 2), txt)
    return _icon(key, colour, draw)


def one_evs_icon(colour: str) -> QIcon:
    return _text_glyph("1×", "oneevs", colour)


def max_icon(colour: str) -> QIcon:
    return _text_glyph("MAX", "maxspd", colour)


def gear_icon(colour: str) -> QIcon:
    """Preferences/System settings."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawEllipse(15, 15, 18, 18)
        for a in range(0, 360, 45):
            import math
            r1, r2 = 11, 17
            x = 24 + r1 * math.cos(math.radians(a))
            y = 24 + r1 * math.sin(math.radians(a))
            x2 = 24 + r2 * math.cos(math.radians(a))
            y2 = 24 + r2 * math.sin(math.radians(a))
            p.drawLine(int(x), int(y), int(x2), int(y2))
    return _icon("gear", colour, draw)


def sliders_icon(colour: str) -> QIcon:
    """Simulation options: three slider tracks with knobs."""
    def draw(p: QPainter, c: QColor):
        pen = QPen(c, 3)
        p.setPen(pen)
        for y, kx in ((14, 30), (24, 16), (34, 34)):
            p.drawLine(9, y, 39, y)
            p.setBrush(c)
            p.drawEllipse(kx - 4, y - 4, 8, 8)
    return _icon("sliders", colour, draw)


def copy_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(14, 8, 22, 28)
        p.drawRect(9, 14, 22, 28)
    return _icon("copy", colour, draw)


def cut_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        pen = QPen(c, 3)
        p.setPen(pen)
        p.drawLine(13, 8, 33, 34)
        p.drawLine(35, 8, 15, 34)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(9, 33, 9, 9)
        p.drawEllipse(30, 33, 9, 9)
    return _icon("cut", colour, draw)


def paste_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(10, 9, 28, 32)
        p.setBrush(c)
        p.drawRect(19, 5, 10, 8)
    return _icon("paste", colour, draw)


def select_all_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        pen = QPen(c, 3, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(9, 9, 30, 30)
        p.setPen(QPen(c, 3))
        p.drawLine(16, 25, 22, 31)
        p.drawLine(22, 31, 33, 17)
    return _icon("selectall", colour, draw)


def crosshair_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawEllipse(14, 14, 20, 20)
        p.setPen(QPen(c, 3))
        p.drawLine(24, 5, 24, 43)
        p.drawLine(5, 24, 43, 24)
    return _icon("crosshair", colour, draw)


def form_defaults_icon(colour: str) -> QIcon:
    """Block defaults: little form with check rows."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(9, 7, 30, 34)
        p.setPen(QPen(c, 3))
        for y in (15, 24, 33):
            p.drawLine(14, y, 22, y)
            p.drawLine(26, y, 34, y)
    return _icon("formdefs", colour, draw)


def pencil_multi_icon(colour: str) -> QIcon:
    """Mass edit: two pencils."""
    def draw(p: QPainter, c: QColor):
        pen = QPen(c, 3)
        p.setPen(pen)
        p.drawLine(12, 36, 32, 12)
        p.drawLine(20, 38, 38, 16)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(12, 36), QPointF(9, 39),
                                 QPointF(13, 39)]))
    return _icon("massedit", colour, draw)


def droplet_icon(colour: str) -> QIcon:
    """Weak-link colouring: droplet quartered G/Y/R."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawEllipse(12, 10, 24, 28)
        p.setPen(QPen(c, 2))
        p.drawLine(24, 10, 24, 38)
        p.drawLine(12, 24, 36, 24)
    return _icon("droplet", colour, draw)


def history_clock_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawEllipse(10, 10, 28, 28)
        p.setPen(QPen(c, 3))
        p.drawLine(24, 16, 24, 25)
        p.drawLine(24, 25, 31, 29)
    return _icon("history", colour, draw)


def folder_open_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(7, 14, 20, 22)
        p.drawPolygon(QPolygonF([QPointF(27, 14), QPointF(41, 14),
                                 QPointF(37, 36), QPointF(27, 36)]))
    return _icon("folderopen", colour, draw)


def new_folder_icon(colour: str) -> QIcon:
    """New Folder: a folder outline with a plus."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(7, 14, 27, 22)
        p.drawLine(7, 14, 17, 14)
        p.drawLine(7, 14, 7, 10)
        p.drawLine(7, 10, 15, 10)
        p.drawLine(15, 10, 18, 14)
        p.setPen(QPen(c, 3, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(33, 25, 43, 25)
        p.drawLine(38, 20, 38, 30)
    return _icon("newfolder", colour, draw)


def list_view_icon(colour: str) -> QIcon:
    """List View: three rows with bullet markers."""
    def draw(p: QPainter, c: QColor):
        p.setPen(_outline_pen(c))
        p.setBrush(c)
        for y in (14, 24, 34):
            p.drawEllipse(QPointF(11, y), 2.2, 2.2)
        p.setPen(QPen(c, 2))
        for y in (14, 24, 34):
            p.drawLine(18, y, 40, y)
    return _icon("listview", colour, draw)


def detail_view_icon(colour: str) -> QIcon:
    """Detail View: a small grid of tiles."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        for x in (8, 26):
            for y in (8, 26):
                p.drawRect(x, y, 14, 14)
    return _icon("detailview", colour, draw)


def check_badge_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(12, 25, 21, 34)
        p.drawLine(21, 34, 37, 15)
    return _icon("checkbadge", colour, draw)


def fit_frame_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(8, 8, 32, 32)
        p.drawRect(17, 17, 14, 14)
    return _icon("fitframe", colour, draw)


def half_theme_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPie(8, 8, 32, 32, 90 * 16, 180 * 16)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawEllipse(8, 8, 32, 32)
    return _icon("halftheme", colour, draw)


def image_icon(colour: str) -> QIcon:
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_outline_pen(c))
        p.drawRect(7, 10, 34, 28)
        p.setBrush(c)
        p.drawEllipse(14, 15, 6, 6)
        p.setPen(QPen(c, 2))
        p.drawLine(9, 34, 20, 24)
        p.drawLine(18, 34, 30, 22)
        p.drawLine(28, 34, 39, 26)
    return _icon("image", colour, draw)


def home_icon(colour: str) -> QIcon:
    """Little house for the hierarchy Home action."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        # roof
        p.drawLine(6, 24, 25, 8)
        p.drawLine(25, 8, 44, 24)
        # body
        p.drawRect(12, 24, 26, 18)
        # door
        p.drawLine(22, 42, 22, 32)
        p.drawLine(22, 32, 28, 32)
        p.drawLine(28, 32, 28, 42)
    return _icon("home", colour, draw)


def up_filled_icon(colour: str) -> QIcon:
    """Filled bold up arrow (hierarchy Up)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon([QPointF(25, 6), QPointF(44, 27), QPointF(33, 27),
                       QPointF(33, 43), QPointF(17, 43), QPointF(17, 27),
                       QPointF(6, 27)])
    return _icon("upfilled", colour, draw)


def mouse_arrow_icon(colour: str) -> QIcon:
    """Conventional pointer arrow (Select tool / cursors)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(c)
        p.setPen(QPen(QColor("#FFFFFF"), 1.5))
        p.drawPolygon([QPointF(14, 6), QPointF(14, 38), QPointF(23, 29),
                       QPointF(29, 42), QPointF(35, 39), QPointF(29, 26),
                       QPointF(40, 26)])
    return _icon("mousearrow", colour, draw)


def percent_zoom_icon(colour: str) -> QIcon:
    """Serifed % glyph (Zoom Percent)."""
    def draw(p: QPainter, c: QColor):
        f = QFont("Georgia", 21)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(c, 1))
        p.drawText(QRectF(0, 0, 50, 50), Qt.AlignmentFlag.AlignCenter, "%")
    return _icon("percentzoom", colour, draw)


def dollar_icon(colour: str) -> QIcon:
    """Cost analysis toggle."""
    def draw(p: QPainter, c: QColor):
        f = QFont("Georgia", 24)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(c, 1))
        p.drawText(QRectF(0, 0, 50, 50), Qt.AlignmentFlag.AlignCenter, "$")
    return _icon("dollar", colour, draw)


def gauge_icon(colour: str) -> QIcon:
    """Capacity analysis toggle: semicircular gauge with needle."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(8, 12, 34, 34, 0, 180 * 16)
        p.setPen(QPen(c, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(25, 29, 36, 16)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(22, 26, 6, 6)
    return _icon("gauge", colour, draw)




def rank_icon(colour: str) -> QIcon:
    """Importance measures: three ranked bars, tallest first.

    Distinct from `chart_icon` (which the Results toggle already uses):
    this one reads as a RANKING -- descending bars with a crown-less
    leader -- which is what an importance overlay shows.
    """
    def draw(p: QPainter, c: QColor):
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        # Tallest (rank 1) on the left, descending to the right.
        p.drawRect(8, 10, 9, 30)
        p.drawRect(21, 20, 9, 20)
        p.drawRect(34, 30, 9, 10)
    return _icon("rank", colour, draw)


def spare_pool_icon(colour: str) -> QIcon:
    """Spare Pools: a small stack of crates (inventory on hand)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 2.5))
        p.drawRect(8, 22, 14, 14)
        p.drawRect(26, 22, 14, 14)
        p.drawRect(17, 6, 14, 14)
    return _icon("spare_pool", colour, draw)


def resource_icon(colour: str) -> QIcon:
    """Resources: a simple person glyph (crewed/shared resource)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(17, 6, 14, 14)
        p.drawEllipse(30, 10, 10, 10)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawArc(6, 24, 28, 22, 0, 180 * 16)
        p.drawArc(26, 26, 20, 16, 0, 180 * 16)
    return _icon("resource", colour, draw)


def trigger_icon(colour: str) -> QIcon:
    """Triggers: a lightning bolt."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon([
            QPointF(28, 4), QPointF(12, 27), QPointF(22, 27),
            QPointF(18, 44), QPointF(38, 19), QPointF(26, 19),
        ])
    return _icon("trigger", colour, draw)


def phase_icon(colour: str) -> QIcon:
    """Phases: stacked horizontal bands (mission phases in sequence)."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for y in (10, 24, 38):
            p.drawLine(6, y, 42, y)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(3, 7, 6, 6)
        p.drawEllipse(3, 21, 6, 6)
        p.drawEllipse(3, 35, 6, 6)
    return _icon("phase", colour, draw)


def phase_active_icon(colour: str) -> QIcon:
    """Phase mode ACTIVE: a filled play triangle inside a circle, meaning
    the phase runs normally, with the optional stress multiplier beside
    it in the cell's spinbox."""
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(6, 6, 36, 36)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon([QPointF(19, 15), QPointF(19, 33), QPointF(34, 24)])
    return _icon("phase_active", colour, draw)


def phase_link_icon(colour: str) -> QIcon:
    """Phase mode LINK: two interlocked chain links."""
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(6, 18, 18, 12, 6, 6)
        p.drawRoundedRect(24, 18, 18, 12, 6, 6)
    return _icon("phase_link", colour, draw)


def phase_cut_icon(colour: str) -> QIcon:
    """Phase mode CUT: scissors."""
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(12, 8, 34, 34)
        p.drawLine(34, 8, 12, 34)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(7, 33, 9, 9)
        p.drawEllipse(32, 33, 9, 9)
    return _icon("phase_cut", colour, draw)


def autolayout_icon(colour: str) -> QIcon:
    """Auto Layout: three blocks snapping into a column flow."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 2))
        # scattered small squares
        p.drawRect(4, 4, 10, 8)
        p.drawRect(22, 2, 10, 8)
        p.drawRect(14, 20, 12, 9)
        # arrow to organised column
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon([QPointF(40, 18), QPointF(40, 30),
                       QPointF(48, 24)])
    return _icon("autolayout", colour, draw)


def import_rbd_icon(colour: str) -> QIcon:
    """Import RBD: arrow into an open box."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 3))
        p.drawPolyline([QPointF(6, 20), QPointF(6, 40), QPointF(28, 40),
                        QPointF(34, 34)])
        p.drawLine(25, 6, 10, 21)
        p.drawLine(10, 10, 10, 21)
        p.drawLine(10, 21, 21, 21)
        p.setBrush(c)
        p.drawPolygon([QPointF(20, 16), QPointF(30, 6), QPointF(23, 0)])
    return _icon("importrbd", colour, draw)


def export_lib_icon(colour: str) -> QIcon:
    """Export Library: book with outward arrow."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 3))
        p.drawRect(5, 10, 22, 30)
        p.drawLine(11, 10, 11, 40)
        p.setPen(QPen(c, 3))
        p.drawLine(26, 25, 44, 25)
        p.setBrush(c)
        p.drawPolygon([QPointF(38, 19), QPointF(46, 25),
                       QPointF(38, 31)])
    return _icon("exportlib", colour, draw)


def clear_results_icon(colour: str) -> QIcon:
    """Clear Results: table swept by a diagonal wipe."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 2))
        p.drawRect(6, 8, 32, 24)
        for y in (14, 20, 26):
            p.drawLine(6, y, 38, y)
        p.setPen(QPen(QColor("#CC4545"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(10, 40, 42, 6)
    return _icon("clearresults", colour, draw)


def finish_flag_icon(colour: str) -> QIcon:
    """Checkered Finish flag (stepped run completion)."""
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 3))
        p.drawLine(12, 4, 12, 44)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        for row in range(3):
            for col in range(4):
                if (row + col) % 2 == 0:
                    p.drawRect(14 + col * 7, 6 + row * 6, 7, 6)
    return _icon("finishflag", colour, draw)


def printer_icon(colour: str) -> QIcon:
    """Printer with a sheet feeding."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 3))
        p.drawRect(8, 18, 34, 18)
        p.drawRect(15, 6, 20, 10)
        p.drawRect(15, 30, 20, 12)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(36, 24, 4, 4)
    return _icon("printer", colour, draw)


def lock_icon(colour: str) -> QIcon:
    """Padlock for the Inspector read-only banner."""
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # shackle
        p.drawArc(QRectF(16, 8, 16, 16), 0, 180 * 16)
        p.drawLine(16, 16, 16, 22)
        p.drawLine(32, 16, 32, 22)
        # body
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(10, 22, 28, 20), 3, 3)
    return _icon("lock", colour, draw)


# ---------------------------------------------------------------------------
# Simulate-tab glyphs that had none.
#
# Deliberately small set, added for CONTROLS THAT ARE ICON-ONLY or need
# a recognition cue at ribbon size -- not "every control gets a picture".
# A vertical list of checkboxes reads faster as text than as four 24px
# line drawings, so the Files & Reports toggles stay text-only on
# purpose. The governing rule is LOCAL uniqueness: no glyph may mean two
# things on the same screen. See the shared-glyph note in
# `main_window._apply_action_icons`.
# ---------------------------------------------------------------------------


def restart_icon(colour: str) -> QIcon:
    """Circular arrow -- restart the stepped run.

    Distinct from `undo_icon` (a left-pointing hook) because Restart
    discards a run and begins a fresh one, it is not an edit."""
    def draw(p: QPainter, c: QColor):
        pen = QPen(c, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(12, 12, 24, 24), 40 * 16, 280 * 16)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(30, 4), QPointF(40, 12),
                                 QPointF(27, 16)]))
    return _icon("restart", colour, draw)


def animate_icon(colour: str) -> QIcon:
    """Play triangle inside a motion arc -- the animate-run toggle.

    Reads as "watch it happen", which is the whole distinction from
    plain `play_icon` (Run). Deliberately NOT a second play triangle:
    two bare triangles on one screen would be indistinguishable."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(15, 14), QPointF(15, 34),
                                 QPointF(30, 24)]))
        pen = QPen(c, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(28, 12, 16, 24), -70 * 16, 140 * 16)
    return _icon("animate", colour, draw)


def log_icon(colour: str) -> QIcon:
    """Lined page -- the Interactive Log."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 3))
        p.drawRoundedRect(QRectF(13, 7, 22, 34), 3, 3)
        p.setPen(QPen(c, 2))
        for i, y in enumerate((17, 23, 29, 35)):
            p.drawLine(18, y, 30 - (2 if i == 3 else 0), y)
    return _icon("log", colour, draw)


def status_summary_icon(colour: str) -> QIcon:
    """Panel with a green bar and an amber bar -- the System Status
    Summary window. Not a bare 'info' circle: the summary is a list of
    counts, not a message."""
    def draw(p: QPainter, c: QColor):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 3))
        p.drawRoundedRect(QRectF(9, 9, 30, 30), 3, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(15, 16, 18, 5)
        p.setBrush(c.lighter(140))
        p.drawRect(15, 27, 12, 5)
    return _icon("statussummary", colour, draw)


# ---------------------------------------------------------------------------
# One glyph per command.
#
# These replaced the last stock QStyle bitmaps on the ribbon (which follow
# the desktop's icon theme, not ours, and could vanish in the light theme)
# and the glyphs that were shared by unrelated commands (one chevron pair
# meant Run in Background, Step, Multi-step and Faster). The rule is now
# GLOBAL uniqueness: no two ribbon commands share a glyph.
#
# Style: outline at a 3.5 stroke on the 48 grid with round caps, so a
# 16 px rendering keeps a ~1.2 px line; a filled shape only where the
# convention is a solid glyph (play, stop).
# ---------------------------------------------------------------------------


def _pen(c: QColor, w: float = 3.5) -> QPen:
    pen = QPen(c, w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _stroke(p: QPainter, c: QColor, w: float = 3.5) -> None:
    p.setPen(_pen(c, w))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _page(p: QPainter, c: QColor, x: float = 10, y: float = 5,
          w: float = 26, h: float = 36) -> None:
    """A sheet with a folded top-right corner."""
    f = 9
    p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x + w - f, y),
                             QPointF(x + w, y + f), QPointF(x + w, y + h),
                             QPointF(x, y + h)]))
    p.drawPolyline(QPolygonF([QPointF(x + w - f, y), QPointF(x + w - f, y + f),
                              QPointF(x + w, y + f)]))


def _arrowhead(p: QPainter, c: QColor, tip: QPointF, angle_deg: float,
               size: float = 9) -> None:
    """A filled arrowhead with its point at `tip`, pointing `angle_deg`
    (0 = right, 90 = down)."""
    a = angle_deg * pi / 180
    back = QPointF(tip.x() - size * cos(a), tip.y() - size * sin(a))
    half = size * 0.6
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPolygon(QPolygonF([
        tip,
        QPointF(back.x() + half * cos(a + pi / 2), back.y() + half * sin(a + pi / 2)),
        QPointF(back.x() + half * cos(a - pi / 2), back.y() + half * sin(a - pi / 2)),
    ]))
    p.restore()


def _clear_disc(p: QPainter, cx: float, cy: float, r: float) -> None:
    """Punch a transparent disc so a badge does not collide with the
    outline beneath it."""
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.restore()


def _badge_plus(p: QPainter, c: QColor, cx: float = 36, cy: float = 36) -> None:
    _clear_disc(p, cx, cy, 10)
    p.setPen(_pen(c, 3.5))
    p.drawLine(QPointF(cx - 6, cy), QPointF(cx + 6, cy))
    p.drawLine(QPointF(cx, cy - 6), QPointF(cx, cy + 6))


# ---- File ----------------------------------------------------------------

def new_file_icon(colour: str) -> QIcon:
    """New model: a blank sheet with a plus."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        _page(p, c, 8, 5, 26, 36)
        _badge_plus(p, c, 36, 36)
    return _icon("newfile", colour, draw)


def close_file_icon(colour: str) -> QIcon:
    """Close model: a sheet with a cross."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        _page(p, c, 8, 5, 26, 36)
        _clear_disc(p, 36, 36, 10)
        p.setPen(_pen(c, 3.5))
        p.drawLine(31, 31, 41, 41)
        p.drawLine(41, 31, 31, 41)
    return _icon("closefile", colour, draw)


def save_as_icon(colour: str) -> QIcon:
    """Save As: the floppy outline with a pencil across its corner."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawPolygon(QPolygonF([QPointF(6, 6), QPointF(28, 6), QPointF(34, 12),
                                 QPointF(34, 22), QPointF(22, 34), QPointF(6, 34)]))
        p.drawRect(13, 6, 12, 9)
        # pencil
        p.setPen(_pen(c, 3.5))
        p.drawLine(24, 40, 40, 24)
        p.drawLine(29, 44, 44, 29)
        p.drawLine(40, 24, 44, 29)
        p.drawLine(24, 40, 22, 46)
        p.drawLine(22, 46, 29, 44)
    return _icon("saveas", colour, draw)


def pdf_icon(colour: str) -> QIcon:
    """Export PDF: a sheet carrying a solid "PDF" band."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        _page(p, c, 10, 4, 28, 40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(4, 22, 32, 14), 2, 2)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        f = QFont("sans-serif")
        f.setBold(True)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QPen(c))
        p.drawText(QRectF(4, 22, 32, 14), Qt.AlignmentFlag.AlignCenter, "PDF")
    return _icon("pdf", colour, draw)


def svg_icon(colour: str) -> QIcon:
    """Export SVG: a Bézier curve with its two handles -- vector art."""
    def draw(p: QPainter, c: QColor):
        from PySide6.QtGui import QPainterPath
        path = QPainterPath(QPointF(7, 38))
        path.cubicTo(QPointF(12, 6), QPointF(36, 42), QPointF(41, 10))
        _stroke(p, c)
        p.drawPath(path)
        p.setPen(_pen(c, 2))
        p.drawLine(7, 38, 12, 14)
        p.drawLine(41, 10, 36, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for x, y in ((12, 14), (36, 34)):
            p.drawRect(QRectF(x - 4, y - 4, 8, 8))
    return _icon("svg", colour, draw)


def export_tables_icon(colour: str) -> QIcon:
    """Export Tables: a small table with an arrow leaving it."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawRect(5, 8, 26, 26)
        p.drawLine(5, 16, 31, 16)
        p.drawLine(5, 25, 31, 25)
        p.drawLine(16, 8, 16, 34)
        p.setPen(_pen(c, 3.5))
        p.drawLine(26, 40, 40, 40)
        p.drawLine(40, 40, 40, 30)
        _arrowhead(p, c, QPointF(40, 22), -90, 9)
    return _icon("exporttables", colour, draw)


def copy_view_icon(colour: str) -> QIcon:
    """Copy View: a camera -- a snapshot of the canvas to the clipboard."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawRoundedRect(QRectF(5, 14, 38, 26), 4, 4)
        p.drawPolyline(QPolygonF([QPointF(15, 14), QPointF(19, 8),
                                  QPointF(29, 8), QPointF(33, 14)]))
        p.drawEllipse(QPointF(24, 27), 7, 7)
    return _icon("copyview", colour, draw)


# ---- Model / Data -------------------------------------------------------

def extract_hierarchy_icon(colour: str) -> QIcon:
    """Extract Hierarchy: the hierarchy pill with an arrow lifting out."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawRoundedRect(QRectF(5, 22, 30, 18), 9, 9)
        p.drawRect(QRectF(11, 27, 8, 8))
        p.drawRect(QRectF(21, 27, 8, 8))
        p.setPen(_pen(c, 3.5))
        p.drawLine(32, 18, 41, 9)
        _arrowhead(p, c, QPointF(44, 6), -45, 10)
    return _icon("extracthier", colour, draw)


def import_lib_icon(colour: str) -> QIcon:
    """Import Library: book with an arrow coming in (mirror of
    `export_lib_icon`)."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawRect(21, 10, 22, 30)
        p.drawLine(27, 10, 27, 40)
        p.setPen(_pen(c, 3))
        p.drawLine(3, 25, 16, 25)
        _arrowhead(p, c, QPointF(22, 25), 0, 9)
    return _icon("importlib", colour, draw)


def rates_icon(colour: str) -> QIcon:
    """Rates library: lambda, the failure-rate symbol."""
    return _text_glyph("λ", "rates", colour)


# ---- Simulate / Debug ---------------------------------------------------

def bg_run_icon(colour: str) -> QIcon:
    """Run in Background: a play triangle in front of a stacked window."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawRoundedRect(QRectF(16, 5, 27, 22), 3, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(7, 18), QPointF(31, 32), QPointF(7, 46)]))
    return _icon("bgrun", colour, draw)


def run_to_time_icon(colour: str) -> QIcon:
    """Run to time: a clock face with an arrow running into it."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawEllipse(QPointF(30, 24), 14, 14)
        p.drawLine(30, 16, 30, 24)
        p.drawLine(30, 24, 36, 28)
        p.drawLine(3, 24, 10, 24)
        _arrowhead(p, c, QPointF(16, 24), 0, 8)
    return _icon("runtotime", colour, draw)


def step_icon(colour: str) -> QIcon:
    """Step: an arrow arcing over onto a dot -- one event forward."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawArc(QRectF(8, 10, 28, 26), 0 * 16, 180 * 16)
        _arrowhead(p, c, QPointF(36, 30), 90, 10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(36, 40), 4.5, 4.5)
        p.drawEllipse(QPointF(8, 40), 3, 3)
    return _icon("step", colour, draw)


def step_multi_icon(colour: str) -> QIcon:
    """Multi-step: two arcs hopping across three dots."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawArc(QRectF(6, 16, 18, 20), 0, 180 * 16)
        p.drawArc(QRectF(24, 16, 18, 20), 0, 180 * 16)
        _arrowhead(p, c, QPointF(42, 30), 90, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for x in (6, 24, 42):
            p.drawEllipse(QPointF(x, 39), 3.5, 3.5)
    return _icon("stepmulti", colour, draw)


def step_change_icon(colour: str) -> QIcon:
    """Step to next change: delta, the change symbol."""
    return _text_glyph("Δ", "stepchange", colour)


def terminate_icon(colour: str) -> QIcon:
    """Terminate the stepped session: the power symbol."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 4)
        p.drawArc(QRectF(9, 10, 30, 30), 120 * 16, 300 * 16)
        p.drawLine(24, 6, 24, 24)
    return _icon("terminate", colour, draw)


def fev_icon(colour: str) -> QIcon:
    """Failure Effects view: a block sending out an effect ripple."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawRect(QRectF(5, 15, 18, 18))
        p.drawArc(QRectF(14, 9, 20, 30), -50 * 16, 100 * 16)
        p.drawArc(QRectF(16, 3, 28, 42), -50 * 16, 100 * 16)
    return _icon("fev", colour, draw)


def fail_icon(colour: str) -> QIcon:
    """Fail a block: a lightning bolt striking a block."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawRect(QRectF(5, 22, 22, 20))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(34, 3), QPointF(22, 26), QPointF(31, 26),
                                 QPointF(26, 45), QPointF(44, 19), QPointF(34, 19),
                                 QPointF(41, 3)]))
    return _icon("fail", colour, draw)


def wrench_icon(colour: str) -> QIcon:
    """Repair: an open-ended wrench."""
    def draw(p: QPainter, c: QColor):
        p.setPen(_pen(c, 7))
        p.drawLine(12, 38, 28, 22)
        _stroke(p, c, 4)
        # jaw: an open C around the head
        p.drawArc(QRectF(23, 7, 20, 20), 120 * 16, 270 * 16)
    return _icon("wrench", colour, draw)


def _phase_strip(p: QPainter, c: QColor) -> None:
    """Three phase bars along the bottom, the middle one current."""
    p.setPen(Qt.PenStyle.NoPen)
    faded = QColor(c)
    faded.setAlpha(110)
    for i, x in enumerate((6, 19, 32)):
        p.setBrush(c if i == 1 else faded)
        p.drawRect(QRectF(x, 30, 10, 12))


def phase_prev_icon(colour: str) -> QIcon:
    """Previous phase: phase strip with a left arrow above."""
    def draw(p: QPainter, c: QColor):
        _phase_strip(p, c)
        p.setPen(_pen(c, 3.5))
        p.drawLine(16, 15, 40, 15)
        _arrowhead(p, c, QPointF(8, 15), 180, 10)
    return _icon("phaseprev", colour, draw)


def phase_next_icon(colour: str) -> QIcon:
    """Next phase: phase strip with a right arrow above."""
    def draw(p: QPainter, c: QColor):
        _phase_strip(p, c)
        p.setPen(_pen(c, 3.5))
        p.drawLine(8, 15, 32, 15)
        _arrowhead(p, c, QPointF(40, 15), 0, 10)
    return _icon("phasenext", colour, draw)


def phase_goto_icon(colour: str) -> QIcon:
    """Go to phase: phase strip with an arrow dropping onto one bar."""
    def draw(p: QPainter, c: QColor):
        _phase_strip(p, c)
        p.setPen(_pen(c, 3.5))
        p.drawLine(37, 5, 37, 17)
        _arrowhead(p, c, QPointF(37, 26), 90, 9)
    return _icon("phasegoto", colour, draw)


def eraser_icon(colour: str) -> QIcon:
    """Clear the failures set in this phase: an eraser on a line."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawPolygon(QPolygonF([QPointF(22, 8), QPointF(40, 26), QPointF(26, 40),
                                 QPointF(8, 22)]))
        p.drawLine(14, 28, 28, 14)
        p.drawLine(6, 44, 42, 44)
    return _icon("eraser", colour, draw)


def reset_all_icon(colour: str) -> QIcon:
    """Reset every phase's failures: a double sweep, all phases cleared."""
    def draw(p: QPainter, c: QColor):
        _phase_strip(p, c)
        _stroke(p, c, 3.5)
        p.drawArc(QRectF(8, 4, 30, 20), 20 * 16, 250 * 16)
        _arrowhead(p, c, QPointF(37, 16), 70, 9)
    return _icon("resetall", colour, draw)


# ---- Results --------------------------------------------------------------

def spof_icon(colour: str) -> QIcon:
    """Single-point failures: three blocks in series, the middle one
    solid with a warning mark over it -- the block whose loss alone
    takes the system down."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawLine(2, 34, 46, 34)
        for x in (3, 35):
            _clear_disc(p, x + 5, 34, 6)
            p.drawRect(QRectF(x, 29, 10, 10))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(QRectF(17, 27, 14, 14))
        p.setPen(_pen(c, 4.5))
        p.drawLine(24, 4, 24, 15)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(24, 21), 2.6, 2.6)
    return _icon("spof", colour, draw)


def domains_icon(colour: str) -> QIcon:
    """Weak Link domains: a dashed boundary around a node and the blocks
    that feed it."""
    def draw(p: QPainter, c: QColor):
        p.setPen(QPen(c, 2.5, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(3, 5, 42, 38), 8, 8)
        _stroke(p, c, 3)
        p.drawRect(QRectF(9, 12, 10, 9))
        p.drawRect(QRectF(9, 27, 10, 9))
        p.drawLine(19, 16, 28, 24)
        p.drawLine(19, 31, 28, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(33, 24), 6, 6)
    return _icon("domains", colour, draw)


def chain_link_icon(colour: str) -> QIcon:
    """Weak Link analysis: two chain links."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 4)
        p.save()
        p.translate(24, 24)
        p.rotate(-45)
        p.drawRoundedRect(QRectF(-21, -7, 24, 14), 7, 7)
        p.drawRoundedRect(QRectF(-3, -7, 24, 14), 7, 7)
        p.restore()
    return _icon("chainlink", colour, draw)


def traffic_light_icon(colour: str) -> QIcon:
    """Colour Weak Links: green / yellow / red bands. The lamps take the
    theme's result colours; `colour` draws the housing."""
    def draw(p: QPainter, c: QColor):
        from raptor.gui.resources.design_tokens import active_colour
        r = active_colour()
        _stroke(p, c, 3)
        p.drawRoundedRect(QRectF(14, 3, 20, 42), 7, 7)
        p.setPen(Qt.PenStyle.NoPen)
        for y, tok in ((12, "result_red"), (24, "result_yellow"), (36, "result_green")):
            p.setBrush(QColor(r[tok]))
            p.drawEllipse(QPointF(24, y), 5, 5)
    return _icon("trafficlight", colour, draw)


# ---- View ----------------------------------------------------------------

def snap_grid_icon(colour: str) -> QIcon:
    """Snap to Grid: a dot grid with one square snapped onto it."""
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for x in (8, 20, 32, 44):
            for y in (8, 20, 32, 44):
                p.drawEllipse(QPointF(x, y), 2.4, 2.4)
        _stroke(p, c, 3.5)
        p.drawRect(QRectF(20, 20, 12, 12))
    return _icon("snapgrid", colour, draw)


def _panel(p: QPainter, c: QColor) -> None:
    p.drawRoundedRect(QRectF(5, 7, 38, 34), 3, 3)


def explorer_dock_icon(colour: str) -> QIcon:
    """Model Explorer: a tree."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawLine(12, 10, 12, 38)
        p.drawLine(12, 24, 20, 24)
        p.drawLine(12, 38, 20, 38)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(QRectF(6, 5, 12, 8))
        p.drawRect(QRectF(22, 20, 20, 8))
        p.drawRect(QRectF(22, 34, 20, 8))
    return _icon("explorerdock", colour, draw)


def inspector_dock_icon(colour: str) -> QIcon:
    """Inspector: a property sheet -- label and value rows."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        _panel(p, c)
        for y in (16, 24, 32):
            p.drawLine(11, y, 18, y)
        p.setPen(_pen(c, 4))
        for y in (16, 24, 32):
            p.drawLine(23, y, 37, y)
    return _icon("inspectordock", colour, draw)


def results_dock_icon(colour: str) -> QIcon:
    """Results dock: a panel holding a bar chart."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        _panel(p, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(QRectF(12, 25, 6, 10))
        p.drawRect(QRectF(21, 15, 6, 20))
        p.drawRect(QRectF(30, 20, 6, 15))
    return _icon("resultsdock", colour, draw)


def run_panel_icon(colour: str) -> QIcon:
    """Run panel: a panel with a play triangle and setting lines."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        _panel(p, c)
        p.drawLine(26, 18, 37, 18)
        p.drawLine(26, 30, 37, 30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(11, 15), QPointF(21, 24), QPointF(11, 33)]))
    return _icon("runpanel", colour, draw)


def reset_layout_icon(colour: str) -> QIcon:
    """Reset Layout: a window split into panes, with a return arrow."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawRoundedRect(QRectF(4, 5, 36, 30), 3, 3)
        p.drawLine(16, 5, 16, 35)
        p.drawLine(16, 20, 40, 20)
        _clear_disc(p, 35, 35, 13)
        _stroke(p, c, 3.5)
        p.drawArc(QRectF(27, 27, 16, 16), 100 * 16, 260 * 16)
        _arrowhead(p, c, QPointF(29, 28), 200, 8)
    return _icon("resetlayout", colour, draw)


def preferences_icon(colour: str) -> QIcon:
    """Preferences: two toggle switches, one on and one off."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawRoundedRect(QRectF(6, 7, 36, 14), 7, 7)
        p.drawRoundedRect(QRectF(6, 27, 36, 14), 7, 7)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(35, 14), 5, 5)
        p.drawEllipse(QPointF(13, 34), 5, 5)
    return _icon("preferences", colour, draw)


def stopwatch_icon(colour: str) -> QIcon:
    """Animation speed: a stopwatch."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawEllipse(QPointF(24, 27), 16, 16)
        p.drawLine(20, 5, 28, 5)
        p.drawLine(24, 5, 24, 11)
        p.drawLine(24, 27, 31, 20)
    return _icon("stopwatch", colour, draw)


# ---- Help ----------------------------------------------------------------

def guide_icon(colour: str) -> QIcon:
    """User Guide: an open book."""
    def draw(p: QPainter, c: QColor):
        from PySide6.QtGui import QPainterPath
        _stroke(p, c)
        for flip in (1, -1):
            path = QPainterPath(QPointF(24, 14))
            path.cubicTo(QPointF(24 - flip * 8, 9), QPointF(24 - flip * 15, 9),
                         QPointF(24 - flip * 20, 11))
            path.lineTo(QPointF(24 - flip * 20, 38))
            path.cubicTo(QPointF(24 - flip * 15, 36), QPointF(24 - flip * 8, 36),
                         QPointF(24, 41))
            p.drawPath(path)
        p.drawLine(24, 14, 24, 41)
    return _icon("guide", colour, draw)


def manual_icon(colour: str) -> QIcon:
    """Reference Manual: a closed book with a bookmark ribbon."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawRoundedRect(QRectF(9, 5, 30, 38), 3, 3)
        p.drawLine(15, 5, 15, 43)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(24, 5), QPointF(32, 5), QPointF(32, 22),
                                 QPointF(28, 18), QPointF(24, 22)]))
    return _icon("manual", colour, draw)


def formulas_icon(colour: str) -> QIcon:
    """Formulas: f(x)."""
    return _text_glyph("f(x)", "formulas", colour)


def keyboard_icon(colour: str) -> QIcon:
    """Keyboard Shortcuts: a keyboard."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawRoundedRect(QRectF(3, 11, 42, 26), 4, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for row, y in enumerate((16, 23)):
            for i in range(6):
                p.drawRect(QRectF(8 + i * 6 + row * 2, y, 4, 4))
        p.drawRect(QRectF(13, 30, 22, 3.5))
    return _icon("keyboard", colour, draw)


def about_icon(colour: str) -> QIcon:
    """About: an information circle."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawEllipse(QPointF(24, 24), 18, 18)
        p.setPen(_pen(c, 4.5))
        p.drawLine(24, 21, 24, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(24, 14), 3, 3)
    return _icon("about", colour, draw)


def report_issue_icon(colour: str) -> QIcon:
    """Report Issue: a speech bubble with an exclamation mark."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c)
        p.drawPolygon(QPolygonF([QPointF(6, 6), QPointF(42, 6), QPointF(42, 32),
                                 QPointF(22, 32), QPointF(12, 42), QPointF(13, 32),
                                 QPointF(6, 32)]))
        p.setPen(_pen(c, 4.5))
        p.drawLine(24, 12, 24, 21)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(24, 27), 2.6, 2.6)
    return _icon("reportissue", colour, draw)


def model_info_icon(colour: str) -> QIcon:
    """Project Settings: a sheet with a gear corner -- settings OF the
    model, distinct from the app's Preferences."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        _page(p, c, 6, 4, 26, 36)
        import math
        p.setPen(_pen(c, 3))
        p.setBrush(QColor(0, 0, 0, 0))
        p.drawEllipse(QPointF(35, 34), 6, 6)
        for a in range(0, 360, 60):
            x1 = 35 + 7.5 * math.cos(math.radians(a))
            y1 = 34 + 7.5 * math.sin(math.radians(a))
            x2 = 35 + 11 * math.cos(math.radians(a))
            y2 = 34 + 11 * math.sin(math.radians(a))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    return _icon("modelinfo", colour, draw)


def recent_files_icon(colour: str) -> QIcon:
    """Recent files: a sheet behind a clock."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        _page(p, c, 5, 4, 24, 32)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 0))
        _stroke(p, c, 3.5)
        p.drawEllipse(QPointF(33, 32), 11, 11)
        p.drawLine(33, 26, 33, 32)
        p.drawLine(33, 32, 37, 35)
    return _icon("recentfiles", colour, draw)


def close_x_icon(colour: str) -> QIcon:
    """Close a panel: a plain cross (dock title bars)."""
    def draw(p: QPainter, c: QColor):
        p.setPen(_pen(c, 4))
        p.drawLine(13, 13, 35, 35)
        p.drawLine(35, 13, 13, 35)
    return _icon("closex", colour, draw)


def document_icon(colour: str) -> QIcon:
    """A plain model file (the File view's recent list)."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        _page(p, c, 10, 4, 28, 40)
        p.setPen(_pen(c, 2.5))
        for y in (22, 29, 36):
            p.drawLine(16, y, 32, y)
    return _icon("document", colour, draw)


def issues_icon(colour: str) -> QIcon:
    """Issues, when the model cannot run: a cross in a circle (the ribbon's
    Issues button; `check_badge_icon` when it can)."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3.5)
        p.drawEllipse(QPointF(24, 24), 17, 17)
        p.setPen(_pen(c, 4.5))
        p.drawLine(17, 17, 31, 31)
        p.drawLine(31, 17, 17, 31)
    return _icon("issues", colour, draw)


def sweep_icon(colour: str) -> QIcon:
    """Sweep: one parameter stepped across values -- points rising along a
    curve over an axis."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawLine(6, 42, 44, 42)
        p.drawLine(6, 42, 6, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for x, y in ((13, 34), (21, 25), (29, 19), (37, 15)):
            p.drawEllipse(QPointF(x, y), 3.6, 3.6)
        _stroke(p, c, 2)
        p.drawPolyline(QPolygonF([QPointF(13, 34), QPointF(21, 25),
                                  QPointF(29, 19), QPointF(37, 15)]))
    return _icon("sweep", colour, draw)


def diff_icon(colour: str) -> QIcon:
    """Differences: two sheets side by side, a changed line marked on the
    right one."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawRect(QRectF(5, 8, 16, 32))
        p.drawRect(QRectF(27, 8, 16, 32))
        _stroke(p, c, 2)
        for y in (15, 21, 33):
            p.drawLine(QPointF(8, y), QPointF(18, y))
            p.drawLine(QPointF(30, y), QPointF(40, y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(QRectF(29, 25, 13, 4))
    return _icon("diff", colour, draw)


def spread_icon(colour: str) -> QIcon:
    """Spread colours: three bands of a range, the middle one twice as
    wide, with tick marks where the quartiles cut it."""
    def draw(p: QPainter, c: QColor):
        p.setPen(Qt.PenStyle.NoPen)
        for x, w, alpha in ((5, 9, 255), (16, 16, 120), (34, 9, 255)):
            col = QColor(c)
            col.setAlpha(alpha)
            p.setBrush(col)
            p.drawRect(QRectF(x, 20, w, 10))
        _stroke(p, c, 3)
        for x in (15, 33):
            p.drawLine(x, 12, x, 38)
    return _icon("spread", colour, draw)


def compare_icon(colour: str) -> QIcon:
    """Compare: two models' results as bars side by side."""
    def draw(p: QPainter, c: QColor):
        _stroke(p, c, 3)
        p.drawLine(5, 42, 43, 42)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(QRectF(9, 16, 9, 26))
        faded = QColor(c)
        faded.setAlpha(120)
        p.setBrush(faded)
        p.drawRect(QRectF(20, 24, 9, 18))
        p.setBrush(c)
        p.drawRect(QRectF(31, 10, 9, 32))
    return _icon("compare", colour, draw)

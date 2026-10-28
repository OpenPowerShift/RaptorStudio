"""The canvas items: blocks, nodes, links, events and hierarchy ovals, the
grid background, LOD and snap helpers."""
from __future__ import annotations
from collections.abc import Callable, Mapping
from typing import Any
from math import (ceil, floor)
from shiboken6 import isValid
from PySide6.QtCore import (
    Qt, QRectF, QPointF, QLineF,
)
from PySide6.QtGui import (
    QPen, QBrush, QColor, QFont, QFontMetricsF, QPainter, QPolygonF,
    QLinearGradient, QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsTextItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem, QStyle, QStyleOptionGraphicsItem,
)
from PySide6.QtGui import QPainterPath
from raptor.gui.resources.icons import arrow_head_len, arrow_head_points
from raptor.gui.resources.design_tokens import (
    APPENDIX_D_STATE_COLOURS,
    COLOUR,
)
from raptor.gui.ui.diagram_common import (  # noqa: I001
    BLOCK_H,
    BLOCK_W,
    NODE_R,
    _SNAP_STATE,
    log,
)
from raptor.gui.ui.block_labels import sublabel_html
from raptor.gui.ui.contrast import readable_text_colour
#: The phased-element hatch: how faint, and how widely spaced, the
#: diagonal lines are drawn over a block/node/event/hierarchy.
#:
#: A block is only BLOCK_W x BLOCK_H (108x46) with 8-11px name text on
#: it. The hatch was a default-density `Qt.BDiagPattern` in the full
#: accent colour across the whole shape -- roughly every 6-8px, ~50%
#: duty -- so on a phased model the name sat on a mess of hard accent
#: diagonals and was effectively unreadable. Sparser and fainter: the
#: hatch still reads unambiguously as "phased", but the text gets a
#: near-uniform background to sit on.
PHASED_HATCH_OPACITY = 0.30
PHASED_HATCH_TILE = 3.0


def _phased_hatch(painter: QPainter, shape: str, geometry) -> None:
    """The phased marker: a faint, widely-spaced diagonal hatch over
    `geom` ("rect" / "ellipse" / "polygon").

    One implementation for all four element types -- they carried
    byte-identical inline copies of the dense fill, so the four easily
    drifted apart on any tuning."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setOpacity(PHASED_HATCH_OPACITY)
    brush = QBrush(QColor(COLOUR["accent"]), Qt.BrushStyle.BDiagPattern)
    # Stretch the pattern's 1-unit tile so the lines are
    # PHASED_HATCH_TILE times further apart than Qt's default.
    brush.setTransform(QTransform.fromScale(PHASED_HATCH_TILE,
                                            PHASED_HATCH_TILE))
    painter.setBrush(brush)
    if shape == "rect":
        painter.drawRect(geometry)
    elif shape == "ellipse":
        painter.drawEllipse(geometry)
    else:
        painter.drawPolygon(geometry)
    painter.restore()


#: Viewport pixels the pointer must travel past press before a selected
#: element is allowed to move. Below this, Qt's default drag handling
#: would nudge the item by whatever tiny sub-pixel delta a real
#: mouse/trackpad reports even for an intended plain click -- and since
#: every position change snaps live to the grid (see
#: `_snapped_x`/`_snapped_y`), an element that wasn't already
#: grid-aligned could visibly jump from that one barely-perceptible
#: nudge. In viewport pixels (not scene units) so the deadzone feels the
#: same regardless of zoom level.
_CLICK_DRAG_DEADZONE_PX = 4.0

def _snap_of(item) -> dict:
    """The grid-snap step of the canvas `item` is on (each view has its
    own); the module default for an item on a bare scene (tests)."""
    scene = item.scene() if item is not None else None
    return getattr(scene, "_snap_state", _SNAP_STATE)

def _snapped_x(value: float, state: dict | None = None) -> float:
    step = (state or _SNAP_STATE)["x"]
    if step <= 0:
        return value
    return round(value / step) * step

def _snapped_y(value: float, state: dict | None = None) -> float:
    step = (state or _SNAP_STATE)["y"]
    if step <= 0:
        return value
    return round(value / step) * step

def _next_grid_line(current: float, step: float, direction: int) -> float:
    """The next grid line strictly in `direction` (+1/-1) from `current`
    -- not `current + direction*step`, which (when `current` isn't
    already grid-aligned) can overshoot past more than one line at once.
    Used by arrow-key nudge; the directional floor/ceil means the first
    nudge from an unaligned position is a small catch-up to the nearest
    line, and every nudge after that (now aligned) is a clean single
    step."""
    if step <= 0:
        return current + direction
    eps = step * 1e-6
    n = current / step
    if direction > 0:
        return (floor(n + eps) + 1) * step
    return (ceil(n - eps) - 1) * step

#: Arrow-key nudge directions (dx sign, dy sign) -- keyPressEvent.
_NUDGE_DIRS = {
    Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
    Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1),
}

#: Wheel-zoom bounds -- deliberately very wide (arbitrarily deep zoom in
#: both directions), just enough to prevent floating-point/Qt breakdown
#: at the extremes.
ZOOM_MIN = 0.02

ZOOM_MAX = 4000.0

#: Semantic zoom (level-of-detail): below this VIEW scale (screen pixels
#: per scene pixel, i.e. `transform().m11()`) element text would render at
#: a sub-legible size, so it is hidden -- shapes, links and badges stay, so
#: a zoomed-out model reads as a clean miniature and redraws faster. View-
#: layer only; never written to the model or the saved file.
LOD_LABELS_MIN_SCALE = 0.25

#: Band around the threshold where the current state is kept, so a resize
#: that wiggles across it does not flicker every label.
LOD_LABELS_HYSTERESIS = 0.05

def _arrow(painter: QPainter, x1: float, y1: float, x2: float, y2: float,
           width: float = 1.0):
    """Canvas arrowhead. `width` is the shaft's pen width: the head is
    sized from it (see `icons.arrow_head_len`) so a thick arrow still
    reads as an arrow rather than a blunt smudge. The triangle itself
    comes from `icons.arrow_head_points`, so the ribbon glyph and the
    on-canvas arrow are the same shape by construction."""
    painter.drawPolygon(arrow_head_points(x1, y1, x2, y2,
                                     arrow_head_len(width)))

class _GridBackground(QGraphicsPathItem):
    def __init__(self, rect: QRectF, x_step: float = 120,
                 y_step: float | None = None):
        """x_step/y_step independent (not always square): when the model
        has its own authored spacing, _apply_scene_layout passes that
        spacing directly so every element's row/column lands on a line
        -- a single isotropic step could match at most one axis when the
        legacy file's horizontal and vertical spacing differ."""
        super().__init__()
        self.setZValue(-100)
        if y_step is None:
            y_step = x_step
        # Guard against a degenerate/non-finite step: the while loops
        # below would otherwise spin forever (OOM, not a clean crash)
        # appending path elements. Callers are expected to keep steps
        # >= 20, this is a last-resort floor.
        if not (x_step > 0) or x_step != x_step:   # NaN != NaN
            log.debug("_GridBackground: bad x_step=%r, falling back to 120",
                      x_step)
            x_step = 120.0
        if not (y_step > 0) or y_step != y_step:
            log.debug("_GridBackground: bad y_step=%r, falling back to 120",
                      y_step)
            y_step = 120.0
        path = QPainterPath()
        x0 = floor(rect.left() / x_step) * x_step
        y0 = floor(rect.top() / y_step) * y_step
        x = x0
        while x < rect.right():
            path.moveTo(x, rect.top())
            path.lineTo(x, rect.bottom())
            x += x_step
        y = y0
        while y < rect.bottom():
            path.moveTo(rect.left(), y)
            path.lineTo(rect.right(), y)
            y += y_step
        self.setPath(path)
        self.setPen(QPen(QColor(COLOUR["border"]), 0.5))

def _blend(hex_a: str, hex_b: str, t: float) -> QColor:
    a, b = QColor(hex_a), QColor(hex_b)
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )

#: Bottom-left corner badge size for the dependency-marker dot -- kept
#: as a module constant so `_BlockItem.paint()`/`_dep_badge_rect()`
#: (hit-testing) and the click/hover handlers agree on exactly the same
#: rect without recomputing it in more than one place.
_DEP_BADGE_D = 14.0

#: A block grows wider to fit its name, up to this width; a longer name
#: is cut short with an ellipsis (the whole name is on hover). Purely how
#: it is drawn: the file keeps only the centre.
BLOCK_MAX_W = 2 * BLOCK_W
_BLOCK_NAME_PAD = 8.0


class _BlockItem(QGraphicsRectItem):
    def __init__(self, block_id: int, name: str, cx: float, cy: float,
                 on_moved: Callable | None = None, on_dep_hover: Callable | None = None):
        w, h = BLOCK_W, BLOCK_H
        super().__init__(0, 0, w, h)
        self._block_id = block_id
        self._on_moved = on_moved
        self._on_dep_hover = on_dep_hover
        self._name_text = name
        self._selected = False
        self._phased = False
        self._link_cut: str | None = None  # None | "linked" | "cut" -- Raptor 7 overlay
        self._fev_hatch = False  # Failure Effects View: induced by a dependency
        self._result_badge: tuple[str, str | None] | None = None  # None | (glyph_str, colour_hex)
        self._dep_badge: dict | None = None  # None | dict(colour, glyph, tooltip, ...)
        # Live Simulation View state (Appendix D fill) -- set by
        # `set_live_fill`, cleared by `retheme`. While it is on, the
        # selection handler must not retheme.
        self._live_active = False
        # Forget the state colour too: `set_live_fill` skips a colour it
        # thinks is already painted, so a stale one left the item plain
        # when the same colour came back (Weak Link off and on again).
        self._live_colour = None

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        # Qt only calls itemChange() for position changes when this flag
        # is on (it is off by default). Must be set before the
        # constructor's own initial setPos() below, and `_on_moved` is
        # already assigned above, so that first placement is also safe to
        # notify -- DiagramView.load_rbd's own `_loading` guard is what
        # distinguishes initial placement from a user drag, not this
        # ordering.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        # Cursor over elements: hand = "grabbable" in select mode; with
        # the Connect tool armed the cross says "click to link from/to
        # here".
        _apply_hover_cursor(self)
        self.setPos(cx - w / 2, cy - h / 2)

        self._label = QGraphicsTextItem(name, self)
        font = QFont("sans-serif", 10)
        font.setBold(True)
        self._label.setFont(font)
        # At-a-glance labels under the name (means, spread or rate -- see
        # `block_labels`), drawn small so the name stays dominant.
        self._sub = QGraphicsTextItem("", self)
        sub_font = QFont("sans-serif", 7)
        self._sub.setFont(sub_font)
        # QTextDocument's default ~4px margin is what created the
        # "gap the height of the name" between title and F/R.
        self._label.document().setDocumentMargin(0)
        self._sub.document().setDocumentMargin(0)
        # Weak Link analysis value, drawn under the box (Raptor 7's
        # BlockAoText). Hidden unless the Weak Link overlay is on.
        self._anal_label = QGraphicsTextItem("", self)
        self._anal_label.setFont(QFont("sans-serif", 8))
        self._anal_label.document().setDocumentMargin(0)
        self._anal_label.setDefaultTextColor(QColor(COLOUR["text"]))
        self._anal_label.hide()
        self.retheme(COLOUR)
        self._adjust_label()

    def retheme(self, c: Mapping[str, Any]):
        """Re-apply every theme-derived colour."""
        pen = QPen(QColor(c["accent"]), 3 if self._selected else 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)

        gradient = QLinearGradient(0, 0, 0, BLOCK_H)
        gradient.setColorAt(0, QColor(c["surface"]))
        gradient.setColorAt(0.12, QColor(c["group_header"]))
        gradient.setColorAt(1, _blend(c["surface"], c["accent"], 0.18))
        self.setBrush(QBrush(gradient))
        self._live_active = False
        # Forget the state colour too: `set_live_fill` skips a colour it
        # thinks is already painted, so a stale one left the item plain
        # when the same colour came back (Weak Link off and on again).
        self._live_colour = None
        self._label.setDefaultTextColor(QColor(c["text"]))
        if hasattr(self, "_sub"):
            self._sub.setDefaultTextColor(QColor(c["text_secondary"])
                                          if "text_secondary" in c
                                          else QColor(c["muted"]))

    def set_sublabel(self, lines: list[str], tooltip: str = ""):
        """The small at-a-glance lines under the name (see
        `block_labels.sublabel`), and the full story as their hover text.

        `lines` is already formatted by `block_labels`, including the
        model's units, so this is drawing only -- an empty list is the
        'off' mode and draws nothing.
        """
        self._sub.setHtml(sublabel_html(lines))
        self._sub.setToolTip(tooltip)
        self._adjust_label()

    def set_anal_label(self, text: str | None,
                       tooltip: str | None = None) -> None:
        """Weak Link value under the box (Raptor 7's BlockAoText)."""
        if not text:
            self._anal_label.hide()
            self._anal_label.setToolTip("")
            return
        self._anal_label.setPlainText(text)
        self._anal_label.setToolTip(tooltip or "")
        tm = self._anal_label.boundingRect()
        self._anal_label.setPos((self.rect().width() - tm.width()) / 2,
                                self.rect().bottom() + 2)
        self._anal_label.setDefaultTextColor(QColor(COLOUR["text"]))
        self._anal_label.show()

    def set_name(self, name: str):
        self._name_text = name
        self._adjust_label()

    def set_live_fill(self, colour_hex: str, tooltip: str | None = None):
        """Solid state colour during the live simulation view (Appendix D).
        A normal retheme restores the themed gradient. `tooltip` explains
        the state (hover help while debugging a run). Marks the item as
        live-painted so a selection change (itemChange) does not
        retheme over the state colour."""
        colour = QColor(colour_hex)
        if getattr(self, "_live_colour", None) == colour.name():
            # Unchanged state: skip setBrush() (which would schedule a
            # repaint 30x/s during a run even when nothing changed).
            if tooltip:
                self.setToolTip(tooltip)
            return
        self._live_colour = colour.name()
        self.setBrush(QBrush(colour))
        self._live_active = True
        # The name/F-R text sits on the fill: keep it AA-readable on
        # light and dark themes.
        text = readable_text_colour(colour.name())
        self._label.setDefaultTextColor(text)
        self._sub.setDefaultTextColor(text)
        if tooltip:
            self.setToolTip(tooltip)

    def set_phased(self, phased: bool):
        """Raptor 7: "the ten elements that are displayed as
        HATCHED will change their characteristics ... from one phase to
        another" -- Raptor's visual marker for a phased element. Drawn as
        an overlay in `paint()` rather than baked into the brush, so it
        survives live-sim colour changes (`set_live_fill`) and theme
        switches."""
        if self._phased != bool(phased):
            self._phased = bool(phased)
            self.update()

    def set_link_cut(self, state: str | None):
        """Raptor 7: a LINKED component during a
        live sim gets a white square overlay, a cut one a black square
        -- "although a linked or cut component will possess an
        overlaying square, the component's state can still be
        determined", so this is a small corner badge, not a fill
        (the state colour underneath from `set_live_fill` stays
        visible). `state` is None (plain Active), "linked" or "cut"."""
        if self._link_cut != state:
            self._link_cut = state
            if state == "linked":
                self.setToolTip(
                    "Linked — this block follows its master's state in "
                    "Failure Effects View (white square).")
            elif state == "cut":
                self.setToolTip(
                    "Cut — this block's paths are cut in Failure Effects "
                    "View (black square).")
            elif self.toolTip().startswith(("Linked", "Cut")):
                self.setToolTip("")
            self.update()

    def set_fev_hatch(self, hatched: bool):
        """Failure Effects View: a block whose state a dependency induced is
        drawn with a narrow cross-hatch (`FevDepMode`, `NarrowCrosshatchFill`
        in Raptor 7); clicking it cycles its alternative outcomes."""
        if self._fev_hatch != bool(hatched):
            self._fev_hatch = bool(hatched)
            self.update()

    def set_result_badge(self, glyph: str | None, colour_hex: str | None):
        """Results Overlay: a small colour-coded glyph badge in the
        top-right corner (opposite `_link_cut`'s top-left, so a live-sim
        Linked/Cut badge and a Results Overlay badge never overlap).
        `glyph` is a short string ("$" for Cost, "!" for a flagged Spares
        block); `colour_hex` is the same green/yellow/red band colour the
        fill uses, so badge and fill always agree. `glyph=None` clears
        it."""
        new = (glyph, colour_hex) if glyph else None
        if self._result_badge != new:
            self._result_badge = new
            self.update()

    def set_dependency_badge(self, desc: dict | None):
        """Dependency marker -- a small dot in the bottom-left corner
        (the one corner `_link_cut`/`_result_badge` don't use), coloured
        and lettered by dependency kind (see
        `DiagramView._dependency_visual`). `desc` is that method's
        return value, or None to clear. Also sets the item's base hover
        tooltip; a live-sim `set_live_fill` call temporarily overwrites
        it."""
        if self._dep_badge != desc:
            self._dep_badge = desc
            self.setToolTip(desc["tooltip"] if desc else "")
            self.update()

    def _dep_badge_rect(self) -> QRectF:
        # On the bottom-left corner, half outside: inside the block it sat
        # on the F/R line. `boundingRect` grows to cover it.
        d = _DEP_BADGE_D
        return QRectF(self.rect().left() - d / 2, BLOCK_H - d / 2, d, d)

    def boundingRect(self) -> QRectF:
        # Room for the corner badges, which overhang the block by half
        # their size (painting outside the bounding rect leaves trails).
        m = 9.0
        return super().boundingRect().adjusted(-m, -m, m, m)

    def hoverEnterEvent(self, event):
        if self._dep_badge is not None and self._on_dep_hover:
            self._on_dep_hover(self, True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if self._dep_badge is not None and self._on_dep_hover:
            self._on_dep_hover(self, False)
        super().hoverLeaveEvent(event)

    def paint(self, painter: QPainter, option, widget=None):
        # Suppress Qt's default black dashed selection border (it sat on
        # top of the live state fill during stepping/live runs). A themed
        # accent dash is drawn after the base shape instead -- selection
        # reads as a border, never as a state colour change.
        option = QStyleOptionGraphicsItem(option)
        option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, widget)
        if self._phased:
            _phased_hatch(painter, "rect", self.rect())
        if self._fev_hatch:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setOpacity(0.55)
            painter.setBrush(QBrush(QColor("#000000"), Qt.BrushStyle.DiagCrossPattern))
            painter.drawRect(self.rect())
            painter.restore()
        if self._link_cut:
            painter.save()
            fill = Qt.GlobalColor.white if self._link_cut == "linked" else Qt.GlobalColor.black
            painter.setPen(QPen(Qt.GlobalColor.gray, 1))
            painter.setBrush(QBrush(fill))
            painter.drawRect(QRectF(self.rect().left() + 4, 4, 12, 12))
            painter.restore()
        if self._result_badge:
            glyph, colour_hex = self._result_badge
            painter.save()
            d = 16.0
            # On the top-right corner, half outside: inside, it covered the
            # end of the block's name.
            badge_rect = QRectF(self.rect().right() - d / 2, -d / 2, d, d)
            painter.setPen(QPen(Qt.GlobalColor.white, 1))
            painter.setBrush(QBrush(QColor(colour_hex)))
            painter.drawEllipse(badge_rect)
            painter.setPen(QPen(Qt.GlobalColor.white, 1))
            f = QFont("sans-serif", 9)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, glyph)
            painter.restore()
        if self._dep_badge:
            painter.save()
            badge_rect = self._dep_badge_rect()
            painter.setPen(QPen(Qt.GlobalColor.white, 1))
            painter.setBrush(QBrush(QColor(self._dep_badge["colour"])))
            painter.drawEllipse(badge_rect)
            painter.setPen(QPen(Qt.GlobalColor.white, 1))
            f = QFont("sans-serif", 8)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter,
                             self._dep_badge["glyph"])
            painter.restore()
        if self.isSelected():
            # Themed selection ring (replaces Qt's black dashed
            # border, suppressed above).
            painter.save()
            painter.setPen(QPen(QColor(COLOUR["accent"]), 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(2, 2, -2, -2))
            painter.restore()

    def _fit_name(self):
        """Width to fit the name AND the at-a-glance label lines, BLOCK_W
        at least, widening equally either side of the centre so the
        position (the centre), snapping and every link port stay where
        they were. A long name is still elided at BLOCK_MAX_W; a label
        line may widen the box past that, since unlike the name it cannot
        be cut short without losing a number."""
        fm = QFontMetricsF(self._label.font())
        room = BLOCK_MAX_W - 2 * _BLOCK_NAME_PAD
        name = self._name_text
        shown = name
        if fm.horizontalAdvance(name) > room:
            shown = fm.elidedText(name, Qt.TextElideMode.ElideRight, room)
        if self._label.toPlainText() != shown:
            self._label.setPlainText(shown)
        self._label.setToolTip(name if shown != name else "")
        sub_w = 0.0
        if hasattr(self, "_sub") and self._sub.toPlainText():
            sub_w = self._sub.boundingRect().width()
        needed = max(fm.horizontalAdvance(shown), sub_w) + 2 * _BLOCK_NAME_PAD
        # Round to a whole even pixel: the label is centred, and a
        # fractional width puts its two ends off the pixel grid.
        w = max(BLOCK_W, ceil(max(needed, BLOCK_W) / 2.0) * 2.0)
        if self.rect().width() != w:
            self.setRect(-(w - BLOCK_W) / 2, 0, w, BLOCK_H)

    def _adjust_label(self):
        self._fit_name()
        tm = self._label.boundingRect()
        if hasattr(self, "_sub") and self._sub.toPlainText():
            # Name at top; F/R tucked directly beneath.
            self._label.setPos((BLOCK_W - tm.width()) / 2, 2)
            sm = self._sub.boundingRect()
            self._sub.setPos((BLOCK_W - sm.width()) / 2,
                             2 + tm.height() - 1)
        else:
            self._label.setPos((BLOCK_W - tm.width()) / 2,
                               (BLOCK_H - tm.height()) / 2 - 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._selected = bool(value)
            self.setZValue(10 if value else 0)
            # Live Simulation View paints the Appendix D state colour;
            # retheme would snap it back to the default gradient. The
            # selection ring is drawn in paint() independent of this, so
            # selection still reads.
            if not getattr(self, "_live_active", False):
                self.retheme(COLOUR)
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            pos = self.pos()
            # _BlockItem's local rect is top-left anchored (0,0,w,h),
            # unlike _NodeItem/_EventItem's centre-anchored shapes. Snap
            # the centre, then convert back to the top-left pos, so a
            # block re-aligns with the centre-anchored items.
            new = QPointF(
                _snapped_x(pos.x() + BLOCK_W / 2, _snap_of(self)) - BLOCK_W / 2,
                _snapped_y(pos.y() + BLOCK_H / 2, _snap_of(self)) - BLOCK_H / 2)
            if new != pos:
                self.setPos(new)  # re-enters once with the snapped value
                return super().itemChange(change, value)
            if self._on_moved:
                self._on_moved(self._block_id, self)
        return super().itemChange(change, value)

class _NodeItem(QGraphicsEllipseItem):
    def __init__(self, node_id: int, name: str, node_type: int, cx: float, cy: float,
                 on_moved: Callable | None = None, label_text: str | None = None,
                 on_dep_hover: Callable | None = None):
        r = NODE_R
        super().__init__(-r, -r, r * 2, r * 2)
        self._node_id = node_id
        self._on_moved = on_moved
        self._on_dep_hover = on_dep_hover
        self._node_type = node_type
        self._dep_badge: dict | None = None  # None | dict(colour, glyph, tooltip, ...)

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        # See _BlockItem's identical flag for why this is required.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        # See _BlockItem's identical flag for why this is required.
        _apply_hover_cursor(self)
        self.setPos(cx, cy)

        type_names = {1: "S", 2: label_text or "", 3: "E", 4: "HI", 5: "HO"}
        t = type_names.get(node_type, "")
        inner = QGraphicsTextItem(t, self)
        ifont = QFont("sans-serif", 8 if node_type in (1, 3) else 7)
        ifont.setBold(True)
        inner.setFont(ifont)
        inner.setToolTip({"1": "Start node", "3": "End node"}.get(
            node_type, "k-of-n node"))  # same default as _inner_tip()
        tm = inner.boundingRect()
        inner.setPos(-tm.width() / 2, -tm.height() / 2)

        self._name_label = QGraphicsTextItem(name, self)
        font = QFont("sans-serif", 9)
        self._name_label.setFont(font)
        self._inner_label = inner
        self._anal_label = QGraphicsTextItem("", self)
        self._anal_label.setFont(QFont("sans-serif", 8))
        self._anal_label.document().setDocumentMargin(0)
        self._anal_label.setDefaultTextColor(QColor(COLOUR["text"]))
        self._anal_label.hide()
        self._r = r
        self._phased = False
        self._capacity_badge: tuple[float, str | None] | None = None  # None | (fraction, colour_hex)
        # Live capacity readout (Raptor 7 manual Fig. 1.4's Simulation View shows
        # e.g. "500" on the Power node): the node's live FLOW drawn under
        # it while the simulation runs (Raptor 7 `ShowFlow`,
        # Raptor 7 -- flow only, not flow/capacity),
        # cleared when the live view ends.
        self._live_cap_text: str | None = None
        self.retheme(COLOUR)
        self._adjust_label()

    def retheme(self, c: Mapping[str, Any]):
        """Re-apply every theme-derived colour."""
        # A live/result fill is not a theme colour. Forget the cache so
        # `_reapply_result_visuals` can paint the same colour again after
        # a retheme; otherwise `set_live_fill` sees an unchanged colour,
        # skips setBrush(), and the node keeps the themed brush while the
        # capacity gauge says it is still overlaid.
        self._live_colour = None
        type_colours = {
            1: c["operating"], 2: c["accent"],
            3: c["failed"], 4: c["relationship"],
            5: c["relationship"],
        }
        colour = type_colours.get(self._node_type, c["muted"])

        d = 6 if self._node_type in (1, 3) else 9
        self.setPen(QPen(QColor(colour), d / 3))
        fill = QColor(colour) if self._node_type in (1, 3) else QColor(c["surface"])
        self.setBrush(QBrush(fill))

        tc = QColor(c["surface"]) if self._node_type in (1, 3) else QColor(colour)
        self._inner_label.setDefaultTextColor(tc)
        self._name_label.setDefaultTextColor(QColor(c["text"]))

    def _inner_tip(self) -> str:
        return {"1": "Start node", "3": "End node"}.get(
            self._node_type, "k-of-n node")

    def set_anal_label(self, text: str | None,
                       tooltip: str | None = None) -> None:
        if not text:
            self._anal_label.hide()
            self._anal_label.setToolTip("")
            self._inner_label.setToolTip(self._inner_tip())
            return
        self._anal_label.setPlainText(text)
        self._anal_label.setToolTip(tooltip or "")
        tm = self._anal_label.boundingRect()
        self._anal_label.setPos(-tm.width() / 2, self._r + 2)
        self._anal_label.setDefaultTextColor(QColor(COLOUR["text"]))
        self._anal_label.show()

    def set_name(self, name: str):
        self._name_label.setPlainText(name)
        self._adjust_label()

    def set_label_text(self, text: str):
        """Replace the in-circle badge (e.g. the k/n K-of-N label)."""
        if hasattr(self, "_inner_label"):
            self._inner_label.setPlainText(text)
            tm = self._inner_label.boundingRect()
            self._inner_label.setPos(-tm.width() / 2, -tm.height() / 2)

    def set_live_fill(self, colour_hex: str, tooltip: str | None = None):
        """Solid state colour during the live simulation view (Appendix
        D "Node Colors") -- see `_BlockItem.set_live_fill`'s own
        docstring. A normal retheme (`DiagramView.retheme` already
        iterates every item type, this class included) restores the
        themed fill/pen."""
        colour = QColor(colour_hex)
        if getattr(self, "_live_colour", None) == colour.name():
            if tooltip:
                self.setToolTip(tooltip)
            return
        self._live_colour = colour.name()
        self.setBrush(QBrush(colour))
        self.setPen(QPen(colour, self.pen().widthF() or 2))
        # The k / k-n label is inside the ellipse.
        self._inner_label.setDefaultTextColor(
            readable_text_colour(colour.name()))
        if tooltip:
            self.setToolTip(tooltip)

    def set_phased(self, phased: bool):
        """See `_BlockItem.set_phased` -- Raptor 7's hatched marker,
        same overlay-in-paint() approach."""
        if self._phased != bool(phased):
            self._phased = bool(phased)
            self.update()

    def set_capacity_badge(self, fraction: float | None,
                           colour_hex: str | None):
        """Results Overlay: a small fuel-gauge-style bar drawn below the
        node, filled proportional to `fraction` (0-1, the same `usage`
        value `apply_capacity_colors` bands into green/yellow/red).
        `fraction=None` clears it."""
        new = (fraction, colour_hex) if fraction is not None else None
        if self._capacity_badge != new:
            self._capacity_badge = new
            self.update()

    def set_dependency_badge(self, desc: dict | None):
        """Same marker as `_BlockItem.set_dependency_badge` -- see its
        docstring. Drawn to the left of the ellipse (the name label is
        above, the capacity gauge below, so left is the one side
        that's clear)."""
        if self._dep_badge != desc:
            self._dep_badge = desc
            self.setToolTip(desc["tooltip"] if desc else "")
            self.update()

    def set_live_capacity_text(self, text: str | None):
        """Live FLOW readout under the node during a simulation
        (Raptor 7 `ShowFlow`, formats 1-9999,
        then 10K/1.2M/3G; manual Fig. 1.4 shows "500" on the Power node).
        `None` clears it (live view over / capacity analysis off)."""
        if self._live_cap_text != text:
            # The live text is painted below the ellipse, outside the
            # ellipse's own rect. Tell the scene before the bounding rect
            # changes, then repaint; without this Qt clips/trails the
            # right-hand digits.
            self.prepareGeometryChange()
            self._live_cap_text = text
            if text:
                label = text[5:] if text.startswith("Flow=") else text
                self.setToolTip(f"Flow (live): {label}")
            elif self.toolTip().startswith("Capacity"):
                self.setToolTip("")
            self.update()

    def _dep_badge_rect(self) -> QRectF:
        d = _DEP_BADGE_D
        return QRectF(-self._r - d - 2, -d / 2, d, d)

    def hoverEnterEvent(self, event):
        if self._dep_badge is not None and self._on_dep_hover:
            self._on_dep_hover(self, True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if self._dep_badge is not None and self._on_dep_hover:
            self._on_dep_hover(self, False)
        super().hoverLeaveEvent(event)


    def boundingRect(self):
        # Extend below the ellipse to cover the capacity gauge bar --
        # QGraphicsEllipseItem's default boundingRect only knows about
        # `rect()` itself, so without this the gauge would be clipped and
        # never properly repainted by Qt's update() mechanism. Reserved
        # unconditionally (not gated on `_capacity_badge` being set) so
        # toggling the badge never needs `prepareGeometryChange()`.
        # Extended left too, for the dependency badge.
        br = super().boundingRect()
        br = br.united(QRectF(-self._r * 0.9, self._r + 4,
                              self._r * 1.8, 5.0))
        # Live "flow/capacity" text: centred, 2r+28 wide, 12 high.
        # It sits clear below the capacity gauge and the Weak Link value
        # label (which uses r+2), so Start/End nodes never stack two
        # numbers. Include it only while shown; an unconditional union
        # changed every node's layout bounds and broke the collapse
        # characterization.
        if self._live_cap_text:
            br = br.united(QRectF(-self._r - 14, self._r + 16,
                                  self._r * 2 + 28, 12))
        d = _DEP_BADGE_D
        return br.united(QRectF(-self._r - d - 2, -d / 2, d, d))

    def paint(self, painter: QPainter, option, widget=None):
        # Suppress Qt's default black dashed selection border (the dashed
        # overlay sat on top of the live state fill). A themed accent
        # dash is drawn after the base shape instead.
        option = QStyleOptionGraphicsItem(option)
        option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, widget)
        if self._phased:
            _phased_hatch(painter, "ellipse", self.rect())
        if self._capacity_badge and not self._live_cap_text:
            fraction, colour_hex = self._capacity_badge
            painter.save()
            w, h = self._r * 1.8, 5.0
            x, y = -w / 2, self._r + 4
            outline = QRectF(x, y, w, h)
            painter.setPen(QPen(QColor(COLOUR["border"]), 1))
            painter.setBrush(QBrush(QColor(COLOUR["surface"])))
            painter.drawRect(outline)
            fill_w = w * max(0.0, min(fraction, 1.0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(colour_hex)))
            painter.drawRect(QRectF(x, y, fill_w, h))
            painter.restore()
        if self._dep_badge:
            painter.save()
            badge_rect = self._dep_badge_rect()
            painter.setPen(QPen(Qt.GlobalColor.white, 1))
            painter.setBrush(QBrush(QColor(self._dep_badge["colour"])))
            painter.drawEllipse(badge_rect)
            painter.setPen(QPen(Qt.GlobalColor.white, 1))
            f = QFont("sans-serif", 8)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter,
                             self._dep_badge["glyph"])
            painter.restore()
        if self._live_cap_text:
            painter.save()
            f = QFont("sans-serif", 8)
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(QPen(QColor(COLOUR["text"]), 1))
            # Below the gauge and the Weak Link value (r+2), so the two
            # readouts cannot overlap on a Start/End node.
            painter.drawText(
                QRectF(-self._r - 14, self._r + 16,
                       self._r * 2 + 28, 12),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self._live_cap_text)
            painter.restore()
        if self.isSelected():
            # Themed selection ring (replaces Qt's black dashed border,
            # suppressed above) — reads as a border, never as a state
            # colour change.
            painter.save()
            painter.setPen(QPen(QColor(COLOUR["accent"]), 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.rect().adjusted(2, 2, -2, -2))
            painter.restore()

    def _adjust_label(self):
        ltm = self._name_label.boundingRect()
        self._name_label.setPos(-ltm.width() / 2, -self._r - ltm.height() - 3)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(10 if value else 0)
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            pos = self.pos()
            snap = _snap_of(self)
            new = QPointF(_snapped_x(pos.x(), snap), _snapped_y(pos.y(), snap))
            if new != pos:
                self.setPos(new)  # re-enters once with the snapped value
                return super().itemChange(change, value)
            if self._on_moved:
                self._on_moved(self._node_id, self)
        return super().itemChange(change, value)

#: Bumped whenever a link item is created or enters/leaves a scene, so the
#: per-scene link index below knows when it is stale.
_LINK_GEN = [0]


def _links_at(item) -> tuple[list, list]:
    """(incoming, outgoing) link items of `item`.

    Routing needs an element's links constantly (its entry and exit sides
    depend on where its neighbours are). It used to find them by walking
    EVERY item in the scene and testing `isinstance` -- per element, per
    link, on every drag step -- which made moving a block on a 150-block
    model take a second. The index is built once per scene and reused until
    a link is added or removed."""
    scene = item.scene()
    if scene is None:
        return [], []
    idx = getattr(scene, "_link_index", None)
    if idx is None or idx[0] != _LINK_GEN[0]:
        table: dict[int, tuple[list, list]] = {}
        for it in scene.items():
            if isinstance(it, _LinkItem):
                table.setdefault(id(it._to), ([], []))[0].append(it)
                table.setdefault(id(it._from), ([], []))[1].append(it)
        idx = (_LINK_GEN[0], table)
        scene._link_index = idx  # type: ignore[attr-defined]
    pair = idx[1].get(id(item))
    if pair is None or not isValid(item):
        return [], []
    # A level change deletes items without necessarily telling the index;
    # never hand back a link (or an end of one) that Qt has already deleted.
    return ([link for link in pair[0] if isValid(link) and isValid(link._from)],
            [link for link in pair[1] if isValid(link) and isValid(link._to)])


def links_affected_by_moving(item) -> list:
    """Every link whose drawn route can change when `item` moves: the links
    touching it, and the links touching its direct neighbours (a
    neighbour's entry/exit side depends on where its neighbours are, and
    `item` is one of them). Nothing else reads `item`'s position, so this
    is exact, not an approximation."""
    incoming, outgoing = _links_at(item)
    affected: dict[int, object] = {}
    for link in (*incoming, *outgoing):
        affected[id(link)] = link
        for end in (link._from, link._to):
            if end is item:
                continue
            i2, o2 = _links_at(end)
            for other in (*i2, *o2):
                affected[id(other)] = other
    return list(affected.values())


class _LinkItem(QGraphicsPathItem):
    """A link: a straight line between two ports. A port is the
    **midpoint of the side** the link approaches from, computed on the
    item's real outline -- a rectangle edge, a circle's cardinal point, a
    hexagon vertex/edge midpoint or an ellipse's extreme point -- so the
    line always touches the shape (the previous centre-to-centre line
    exited the axis-aligned bounding box and floated off hexagons/ovals).

    Ports follow the page's flow: an element's incoming links enter on
    the side facing their source, and its outgoing links leave on the
    **opposite** side (in from the left -> out to the right; in from the
    top -> out the bottom), so a node never enters and leaves on the same
    side. `line()` is the drawn segment between the two ports."""

    def __init__(self, from_item: QGraphicsItem, to_item: QGraphicsItem,
                 link_num: int | None = None):
        super().__init__()
        self._from = from_item
        self._to = to_item
        self._link_num = link_num
        self._selected = False
        self._p0 = QPointF()
        self._p3 = QPointF()
        self._head = QPolygonF()
        _LINK_GEN[0] += 1
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        _apply_hover_cursor(self)
        self.setZValue(-1)
        self.retheme(COLOUR)
        self._update_route()

    def line(self) -> QLineF:
        """The segment between the two ports -- the drawn geometry."""
        return QLineF(self._p0, self._p3)

    def shape(self):
        """Wide (10px) hit area along the line -- the bare 2.5px stroke
        was practically impossible to click for selection."""
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(self.path())

    def boundingRect(self):
        # Pad past the path's own stroke so the arrowhead wings (painted
        # beyond the head endpoint) aren't left un-repainted.
        base = super().boundingRect()
        return base.adjusted(-12, -12, 12, 12)

    def _update_route(self):
        from_item, to_item = self._from, self._to
        # Every incoming link to an element shares its one entry port, and
        # every outgoing link shares its one exit port (opposite the
        # entry), so the flow passes straight through and both ports are
        # visible on the outline.
        fc = self._true_centre(from_item)
        tc = self._true_centre(to_item)
        exit_side = self._exit_side(from_item)
        p0 = self._port_point(from_item, exit_side)
        # Per-link direction, NOT the element's single shared entry side.
        #
        # `_entry_side` collapsed every incoming link onto one side and
        # forced "left" for anything in a left-to-right run, so a block fed
        # from directly above AND from the left put both connections on
        # its left edge -- the incoming-from-above link then had to run
        # round the body, which is both ugly and what the crossing
        # avoidance below exists to paper over. `_side` is already
        # direction-aware and already biased towards the page-flow
        # direction (a vertical port needs the vertical component to
        # dominate by more than `_HORIZONTAL_BIAS`), so asking it per
        # link gives top-above / left-from-the-left with no special case.
        entry = self._entry_side_for(to_item, fc)
        p3 = self._port_point(to_item, entry)
        # A link must never cut through the body of an element it connects
        # to. If the shared port would send this particular line across
        # the shape (fan-in from more than one direction), fall back to
        # the facing side. Iterate: flipping one end can change whether
        # the other end is crossed.
        for _ in range(3):
            changed = False
            if self._segment_crosses(to_item, p0, p3):
                p3 = self._port_point(
                    to_item, self._entry_side_for(to_item, fc))
                changed = True
            if self._segment_crosses(from_item, p0, p3):
                p0 = self._port_point(
                    from_item, self._exit_fallback_side(from_item, tc))
                changed = True
            if not changed:
                break
        self.prepareGeometryChange()
        self._p0, self._p3 = p0, p3
        self._head = arrow_head_points(p0.x(), p0.y(), p3.x(), p3.y(),
                                       arrow_head_len(1.0))
        path = QPainterPath(p0)
        path.lineTo(p3)
        self.setPath(path)

    @staticmethod
    def _true_centre(item) -> QPointF:
        """The item's real centre. A block is top-left anchored (its
        `pos()` is the corner), everything else is centre anchored. Uses
        the shape rect rather than `sceneBoundingRect()` for a node,
        whose capacity-gauge/dependency-badge padding skews the box
        centre off the circle."""
        if isinstance(item, _BlockItem):
            return item.mapToScene(item.rect().center())
        return item.scenePos()

    @staticmethod
    def _half_extents(item):
        """Half-width/half-height of the item's true shape -- the port
        sits at the middle of a side, so this places it on the outline."""
        if isinstance(item, _BlockItem):
            return item.rect().width() / 2.0, BLOCK_H / 2.0
        if isinstance(item, _NodeItem):
            return item._r, item._r
        if isinstance(item, _EventItem):
            return item._half_w, item._r
        if isinstance(item, _HierItem):
            return _HierItem.W / 2.0, _HierItem.H / 2.0
        r = item.sceneBoundingRect()
        return r.width() / 2.0, r.height() / 2.0

    #: Horizontal component (relative to the vertical) above which a
    #: connection is treated as entering/leaving the left/right side.
    #: The page flows left-to-right, so up to ~63 degrees off horizontal
    #: still reads as a "left/right" port -- keeping entry on the left
    #: and exit on the right for the balanced look, and reserving
    #: top/bottom for genuinely vertical connections.
    _HORIZONTAL_BIAS = 0.5
    #: The same, for round junction nodes (see `_side`).
    _NODE_HORIZONTAL_BIAS = 0.2
    #: An element whose sources are all on one side of it and targets all
    #: on the other reads as a horizontal flow even when the links are
    #: steep, as long as each is at least this far off vertical.
    _MONOTONE_MIN_SLOPE = 0.15

    @staticmethod
    def _side(item, toward: QPointF) -> str:
        """Which side of `item` faces `toward`. Prefers the horizontal
        (page-flow) sides: the vertical component has to dominate by more
        than `_HORIZONTAL_BIAS` before a top/bottom port is chosen. Not
        normalised by the item's half-extents -- doing that made a wide,
        short block "face" straight up/down and take links on its
        top/bottom edge instead of the side the flow arrives from."""
        c = _LinkItem._true_centre(item)
        dx, dy = toward.x() - c.x(), toward.y() - c.y()
        # A round junction node is where a fan of links meets; taking its
        # ports on the top or bottom for anything steeper than ~27 degrees
        # off horizontal sent one link of a fan into the top of the node
        # while the rest arrived on the left. It only uses a vertical port
        # for a link that is almost straight up or down.
        bias = (_LinkItem._NODE_HORIZONTAL_BIAS
                if isinstance(item, _NodeItem)
                else _LinkItem._HORIZONTAL_BIAS)
        if abs(dx) >= bias * abs(dy):
            return "right" if dx >= 0 else "left"
        return "bottom" if dy >= 0 else "top"

    _OPPOSITE = {"left": "right", "right": "left",
                 "top": "bottom", "bottom": "top"}

    @staticmethod
    def _port_point(item, side: str) -> QPointF:
        """Scene point at the middle of `side` on the item's outline."""
        c = _LinkItem._true_centre(item)
        hw, hh = _LinkItem._half_extents(item)
        if side == "right":
            return QPointF(c.x() + hw, c.y())
        if side == "left":
            return QPointF(c.x() - hw, c.y())
        if side == "bottom":
            return QPointF(c.x(), c.y() + hh)
        return QPointF(c.x(), c.y() - hh)

    @staticmethod
    def _point_inside(item, p: QPointF) -> bool:
        """True if scene point `p` is strictly inside `item`'s drawn
        shape (a small inset, so a point on the outline doesn't count)."""
        if isinstance(item, _BlockItem):
            return item.rect().adjusted(3, 3, -3, -3) \
                .contains(item.mapFromScene(p))
        if isinstance(item, _NodeItem):
            d = p - item.scenePos()
            return (d.x() ** 2 + d.y() ** 2) ** 0.5 < item._r - 3
        if isinstance(item, _EventItem):
            polygon = item.polygon()
            scene_poly = QPolygonF([item.mapToScene(polygon.at(i))
                                    for i in range(polygon.count())])
            return scene_poly.containsPoint(p, Qt.FillRule.OddEvenFill)
        if isinstance(item, _HierItem):
            c = item.scenePos()
            a = _HierItem.W / 2.0 - 3
            b = _HierItem.H / 2.0 - 3
            return ((p.x() - c.x()) / a) ** 2 + \
                ((p.y() - c.y()) / b) ** 2 < 1.0
        return False

    @staticmethod
    def _segment_crosses(item, a: QPointF, b: QPointF) -> bool:
        """True if the segment a-b runs through `item`'s body. Sampled
        from just past each endpoint (a port sits on the boundary, so the
        crossing that matters happens immediately after it).

        Blocks and nodes -- nearly every link end -- take a fast path: the
        item's shape is read once and the 19 samples are tested with plain
        arithmetic. The general path maps every sample through Qt, which
        was most of the cost of routing a link. Same sample points, same
        result."""
        ax, ay = a.x(), a.y()
        dx, dy = b.x() - ax, b.y() - ay
        if isinstance(item, _BlockItem):
            r = item.mapRectToScene(item.rect().adjusted(3, 3, -3, -3))
            left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
            for i in range(1, 20):
                t = i / 20.0
                x, y = ax + dx * t, ay + dy * t
                if left <= x <= right and top <= y <= bottom:
                    return True
            return False
        if isinstance(item, _NodeItem):
            lim = item._r - 3
            if lim <= 0:
                return False
            c = item.scenePos()
            cx, cy, lim2 = c.x(), c.y(), lim * lim
            for i in range(1, 20):
                t = i / 20.0
                ox, oy = ax + dx * t - cx, ay + dy * t - cy
                if ox * ox + oy * oy < lim2:
                    return True
            return False
        for i in range(1, 20):
            t = i / 20.0
            p = QPointF(ax + dx * t, ay + dy * t)
            if _LinkItem._point_inside(item, p):
                return True
        return False

    @staticmethod
    def _majority_side(counts: dict) -> str | None:
        """The most common side in `counts`; ties prefer the horizontal
        (page-flow) sides, left before right."""
        if not counts:
            return None
        order = {"left": 0, "right": 1, "top": 2, "bottom": 3}
        return min(counts, key=lambda s: (-counts[s], order[s]))

    @staticmethod
    def _flow_horizontal(item) -> bool:
        """Whether `item` sits in the page's left-to-right run: true if any
        of its neighbours is reached more horizontally than vertically.
        Such an element takes its ports on the left/right (enter left,
        exit right) even when some inputs arrive from below, which is what
        keeps a join/split visually balanced."""
        if item.scene() is None:
            return True
        c = _LinkItem._true_centre(item)
        incoming, outgoing = _links_at(item)
        for it in (*incoming, *outgoing):
            other = it._to if it._from is item else it._from
            oc = _LinkItem._true_centre(other)
            dx, dy = oc.x() - c.x(), oc.y() - c.y()
            if abs(dx) >= _LinkItem._HORIZONTAL_BIAS * abs(dy):
                return True
        return _LinkItem._runs_across(item, c, incoming, outgoing)

    @staticmethod
    def _runs_across(item, c: QPointF, incoming, outgoing) -> bool:
        """Every source on one side of `item` and every target on the
        other (by x), each clearly off vertical: the flow runs across it
        even though the links are steep. A block fed from a node low on its
        left and feeding one low on its right is such an element -- both
        its links used to land on its bottom edge."""
        if not incoming or not outgoing:
            return False
        slope = _LinkItem._MONOTONE_MIN_SLOPE
        sides = set()
        for links, sign in ((incoming, -1), (outgoing, 1)):
            for it in links:
                other = it._from if links is incoming else it._to
                oc = _LinkItem._true_centre(other)
                dx, dy = oc.x() - c.x(), oc.y() - c.y()
                if abs(dx) < slope * abs(dy) or dx == 0:
                    return False
                sides.add(sign * (1 if dx > 0 else -1))
        return len(sides) == 1          # all +1 (L->R) or all -1 (R->L)

    @staticmethod
    def _exit_fallback_side(item, toward: QPointF) -> str:
        """The side to leave `item` from when the flow-direction exit would
        run the line through its own body: the side facing the target --
        unless that is the side the flow ENTERS on, in which case an element
        would take its input and its output on the same edge. Then use the
        perpendicular side facing the target (bottom or top)."""
        side = _LinkItem._side(item, toward)
        if _LinkItem._flow_horizontal(item):
            entry = _LinkItem._entry_side(item)
            if side == entry and side in ("left", "right"):
                c = _LinkItem._true_centre(item)
                return "bottom" if toward.y() >= c.y() else "top"
        return side

    @staticmethod
    def _flow_sign(item) -> int:
        """Which way the flow runs through `item` along the page: +1 for
        left-to-right, -1 for right-to-left.

        Decided by where its neighbours are: sources to the left and
        targets to the right (or the reverse) vote for their direction, in
        proportion to how far away they are. Everything used to assume
        left-to-right, so an element in a chain that runs the other way
        (after a Flip Horizontal, or a snaking layout) got its input on the
        wrong side and its line cut through its own body."""
        c = _LinkItem._true_centre(item)
        incoming, outgoing = _links_at(item)
        total = 0.0
        for it in incoming:
            total += c.x() - _LinkItem._true_centre(it._from).x()
        for it in outgoing:
            total += _LinkItem._true_centre(it._to).x() - c.x()
        return -1 if total < 0 else 1

    @classmethod
    def _entry_side_for(cls, item, toward: QPointF) -> str:
        """The side an INCOMING link from `toward` should use.

        `_side` is the pure geometric answer; this adds the one rule that
        keeps the result readable: on an element that flows
        left-to-right, an incoming link may use `left` or `top` only.

        * `right` is a backwards arrow, and
        * `bottom` runs the line UP into the block, which fights the
          reading direction and can clip the body,

        so both fall back to `left`. `top` is deliberately NOT remapped: a
        link dropping in from directly above is an arrow pointing INTO the
        block, which reads cleanly and does not contradict a left-to-right
        flow. That is the case this exists for -- previously every input
        collapsed onto a single shared side, so a block fed from above and
        from the left drew both connections on its left edge.

        Single-sourced on purpose: the crossing-avoidance loop in
        `_update_route` recomputes the entry side when a line would cut
        through the body, and if that path used the raw `_side` it would
        silently undo this rule in exactly the case the rule exists for.
        """
        side = cls._side(item, toward)
        if cls._flow_horizontal(item):
            if cls._flow_sign(item) > 0:
                if side in ("right", "bottom"):
                    return "left"
            elif side in ("left", "bottom"):     # right-to-left: mirror
                return "right"
        return side

    @staticmethod
    def _entry_side(item):
        """The single side *all* of `item`'s incoming links enter on, or
        None if it has none. On a left-to-right element that's the left
        side; a vertical-chain element takes the majority side of its
        sources. One port for every input."""
        if item.scene() is None:
            return None
        counts: dict[str, int] = {}
        for it in _links_at(item)[0]:
            s = _LinkItem._side(item, _LinkItem._true_centre(it._from))
            counts[s] = counts.get(s, 0) + 1
        if not counts:
            return None
        if _LinkItem._flow_horizontal(item):
            return "left" if _LinkItem._flow_sign(item) > 0 else "right"
        return _LinkItem._majority_side(counts)

    @staticmethod
    def _exit_side(item) -> str:
        """The single side *all* of `item`'s outgoing links leave from.
        On a left-to-right element that's the right side; otherwise the
        side opposite its entry so the flow passes through rather than
        doubling back. A source with no incoming links faces its targets
        as a group."""
        entry = _LinkItem._entry_side(item)
        if _LinkItem._flow_horizontal(item):
            return "right" if _LinkItem._flow_sign(item) > 0 else "left"
        if entry is not None:
            return _LinkItem._OPPOSITE[entry]
        counts: dict[str, int] = {}
        for it in _links_at(item)[1]:
            s = _LinkItem._side(item, _LinkItem._true_centre(it._to))
            counts[s] = counts.get(s, 0) + 1
        return _LinkItem._majority_side(counts) or "right"

    @staticmethod
    def _anchor_geom(item):
        """(rect, centre, radius) used by the Connect-click nearest-edge
        fallback and the dependency hover line. For a round node marker,
        `centre`/`radius` come directly from the item's own
        `scenePos()`/`_r`, not `sceneBoundingRect()` -- the capacity-gauge
        and dependency-badge boundingRect() overrides reserve asymmetric
        padding, which shifts sceneBoundingRect().center() off the true
        centre. Everything else (blocks/events/hiers) uses its own box
        rect/centre."""
        if isinstance(item, _NodeItem):
            centre = item.scenePos()
            r = item._r
            return (QRectF(centre.x() - r, centre.y() - r, r * 2, r * 2),
                   centre, r)
        rect = item.sceneBoundingRect()
        return rect, rect.center(), None

    def retheme(self, c: Mapping[str, Any]):
        self._live_active = False
        # Forget the state colour too: `set_live_fill` skips a colour it
        # thinks is already painted, so a stale one left the item plain
        # when the same colour came back (Weak Link off and on again).
        self._live_colour = None
        self.setPen(QPen(QColor(c["accent"] if self.isSelected()
                                else c["muted"]),
                         3 if self.isSelected() else 2.5))

    def set_live_fill(self, colour_hex: str, tooltip: str | None = None):
        """Solid state colour during the live simulation view -- a link
        has no fill (it's a stroke), only a pen, so "fill" here just
        means the line's own colour. See `_BlockItem.set_live_fill`'s
        own docstring; `DiagramView.retheme` restores the themed pen
        afterwards, this class included."""
        if getattr(self, "_live_colour", None) == QColor(colour_hex).name():
            if tooltip:
                self.setToolTip(tooltip)
            return
        self._live_colour = QColor(colour_hex).name()
        self.setPen(QPen(QColor(colour_hex), 3 if self.isSelected() else 2.5))
        self._live_active = True
        if tooltip:
            self.setToolTip(tooltip)

    def itemChange(self, change, value):
        if change in (QGraphicsItem.GraphicsItemChange.ItemSceneChange,
                      QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged):
            _LINK_GEN[0] += 1          # the scene's link index is stale
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            # Repaint with the accent pen while selected. NB: geometry
            # must not change here -- only the pen. During the live
            # Simulation View the pen is the running state colour, so
            # don't snap it back to the themed one (same guard as
            # `_BlockItem.itemChange`).
            self.setZValue(5 if value else -1)
            if not getattr(self, "_live_active", False):
                self.retheme(COLOUR)
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None):
        pen = self.pen()
        painter.setPen(pen)
        painter.setBrush(QBrush())
        painter.drawPath(self.path())
        # The head is worked out when the route is (`_update_route`), not on
        # every repaint: a pan or a zoom repaints every link.
        # Filled, in the link's own colour: the shaft's no-fill brush was
        # still in force for the polygon, so the head came out as a bare
        # triangle outline -- hard to read at 2.5px against the canvas.
        # `NoPen` for the head so the stroke does not fatten it and eat
        # the point.
        painter.setBrush(QBrush(pen.color()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(self._head)

class _EventItem(QGraphicsPolygonItem):
    """An RBDEvent, drawn as a hexagon (the manual's own event symbol)
    in the relationship colour."""

    def __init__(self, event_id: int, name: str, cx: float, cy: float,
                 on_moved: Callable | None = None,
                 probability: float | None = None):
        r = NODE_R + 2
        super().__init__(QPolygonF())
        self._event_id = event_id
        self._on_moved = on_moved
        self._r = r       # vertical half-height, for link ports/normals
        self._half_w = r  # horizontal half-width; `_refit` widens it
        self._phased = False
        self._link_cut: str | None = None  # None | "linked" | "cut" -- Raptor 7 overlay

        self.setPen(QPen(QColor(COLOUR["relationship"]), 2))
        self.setBrush(QBrush(QColor(COLOUR["surface"])))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        _apply_hover_cursor(self)
        self.setPos(cx, cy)

        self._label = QGraphicsTextItem(name, self)
        font = QFont("sans-serif", 8)
        font.setBold(True)
        self._label.setFont(font)
        tm = self._label.boundingRect()
        self._label.setDefaultTextColor(QColor(COLOUR["relationship"]))
        self._label.setPos(-tm.width() / 2, -tm.height() / 2)
        # The success probability rides on the second line inside the
        # hexagon, so the name and the number can both be read at a
        # glance (the old drawing had only the name).
        self._prob_label = QGraphicsTextItem("", self)
        pfont = QFont("sans-serif", 7)
        self._prob_label.setFont(pfont)
        self._prob_label.document().setDocumentMargin(0)
        self._prob_label.setDefaultTextColor(QColor(COLOUR["text"]))
        self._prob_label.hide()
        self._anal_label = QGraphicsTextItem("", self)
        self._anal_label.setFont(QFont("sans-serif", 8))
        self._anal_label.document().setDocumentMargin(0)
        self._anal_label.setDefaultTextColor(QColor(COLOUR["text"]))
        self._anal_label.hide()
        if probability is not None:
            self._prob_label.setPlainText(f"P={float(probability):g}")
            self._prob_label.show()
        self._refit()

    def set_anal_label(self, text: str | None,
                       tooltip: str | None = None) -> None:
        if not text:
            self._anal_label.hide()
            self._anal_label.setToolTip("")
            return
        self._anal_label.setPlainText(text)
        self._anal_label.setToolTip(tooltip or "")
        tm = self._anal_label.boundingRect()
        self._anal_label.setPos(-tm.width() / 2, self._r + 2)
        self._anal_label.setDefaultTextColor(QColor(COLOUR["text"]))
        self._anal_label.show()

    def set_probability(self, probability: float | None) -> None:
        """Second line under the name: the event's success probability."""
        text = "" if probability is None else f"P={probability:g}"
        if self._prob_label.toPlainText() != text:
            self._prob_label.setPlainText(text)
        self._prob_label.setVisible(bool(text))
        self._refit()

    def _refit(self) -> None:
        """Fit the hexagon's flat top/bottom to the two label lines.

        The points stay on the left/right, so links keep entering and
        leaving on the flow sides; only the flat edges grow to the text
        width."""
        name_w = self._label.boundingRect().width()
        prob_w = (self._prob_label.boundingRect().width()
                  if self._prob_label.toPlainText() else 0.0)
        text_w = max(name_w, prob_w)
        edge = max(self._r * 0.5, text_w / 2.0 + 4.0)
        half_w = edge + self._r * 0.5
        self._half_w = half_w
        self.setPolygon(QPolygonF([
            QPointF(-half_w, 0), QPointF(-edge, -self._r),
            QPointF(edge, -self._r), QPointF(half_w, 0),
            QPointF(edge, self._r), QPointF(-edge, self._r),
        ]))
        name_h = self._label.boundingRect().height()
        prob_h = (self._prob_label.boundingRect().height()
                  if self._prob_label.toPlainText() else 0.0)
        total = name_h + prob_h
        self._label.setPos(-name_w / 2.0, -total / 2.0)
        if prob_h:
            self._prob_label.setPos(-prob_w / 2.0,
                                    -total / 2.0 + name_h)

    def set_name(self, name: str):
        self._label.setPlainText(name)
        self._refit()

    def retheme(self, c: Mapping[str, Any]):
        self.setPen(QPen(QColor(c["relationship"]), 2))
        self.setBrush(QBrush(QColor(c["surface"])))
        self._label.setDefaultTextColor(QColor(c["relationship"]))
        self._prob_label.setDefaultTextColor(QColor(c["text"]))
        # Like the other items: a retheme clears the cached overlay
        # colour so Weak Link/Cost/Spares can repaint the same colour.
        self._live_colour = None

    def set_live_fill(self, colour_hex: str, tooltip: str | None = None):
        """Solid state colour during the live simulation view (Appendix
        D "Event Colors": Success/Armed/Failed). See `_BlockItem.set_
        live_fill`'s own docstring; `DiagramView.retheme` restores the
        themed fill/pen afterwards, this class included."""
        colour = QColor(colour_hex)
        if getattr(self, "_live_colour", None) == colour.name():
            if tooltip:
                self.setToolTip(tooltip)
            return
        self._live_colour = colour.name()
        self.setBrush(QBrush(colour))
        self.setPen(QPen(colour, 2))
        self._label.setDefaultTextColor(
            readable_text_colour(colour.name()))
        self._prob_label.setDefaultTextColor(
            readable_text_colour(colour.name()))
        if tooltip:
            self.setToolTip(tooltip)

    def set_phased(self, phased: bool):
        """See `_BlockItem.set_phased` -- Raptor 7's hatched marker,
        same overlay-in-paint() approach."""
        if self._phased != bool(phased):
            self._phased = bool(phased)
            self.update()

    def set_link_cut(self, state: str | None):
        """See `_BlockItem.set_link_cut` -- Raptor 7: "a linked
        event will be displayed with a white diamond overlaying the
        event...a cut event will be displayed with a black diamond"."""
        if self._link_cut != state:
            self._link_cut = state
            if state == "linked":
                self.setToolTip(
                    "Linked — this event follows its master's state in "
                    "Failure Effects View (white diamond).")
            elif state == "cut":
                self.setToolTip(
                    "Cut — this event's paths are cut in Failure Effects "
                    "View (black diamond).")
            elif self.toolTip().startswith(("Linked", "Cut")):
                self.setToolTip("")
            self.update()

    def paint(self, painter: QPainter, option, widget=None):
        super().paint(painter, option, widget)
        if self._phased:
            _phased_hatch(painter, "polygon", self.polygon())
        if self._link_cut:
            painter.save()
            fill = Qt.GlobalColor.white if self._link_cut == "linked" else Qt.GlobalColor.black
            painter.setPen(QPen(Qt.GlobalColor.gray, 1))
            painter.setBrush(QBrush(fill))
            painter.drawPolygon(QPolygonF([
                QPointF(0, -6), QPointF(6, 0),
                QPointF(0, 6), QPointF(-6, 0)]))
            painter.restore()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(10 if value else 0)
            pen = QPen(QColor(COLOUR["accent"] if value else COLOUR["relationship"]),
                       3 if value else 2)
            self.setPen(pen)
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            pos = self.pos()
            snap = _snap_of(self)
            new = QPointF(_snapped_x(pos.x(), snap), _snapped_y(pos.y(), snap))
            if new != pos:
                self.setPos(new)  # re-enters once with the snapped value
                return super().itemChange(change, value)
            if self._on_moved:
                self._on_moved(self._event_id, self)
        return super().itemChange(change, value)

class _HierItem(QGraphicsEllipseItem):
    """A hierarchy element: the manual's elongated oval. Double-
    click descends into its level."""

    W, H = 150, 56

    def __init__(self, hier_id: int, name: str, cx: float, cy: float,
                 on_moved: Callable | None = None):
        super().__init__(-self.W / 2, -self.H / 2, self.W, self.H)
        self._hier_id = hier_id
        self._on_moved = on_moved
        self._phased = False
        self.setPen(QPen(QColor(COLOUR["relationship"]), 2))
        self.setBrush(QBrush(QColor(COLOUR["group_header"])))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        _apply_hover_cursor(self)
        self.setZValue(-1)
        self.setPos(cx, cy)

        self._label = QGraphicsTextItem(name, self)
        font = QFont("sans-serif", 10)
        font.setBold(True)
        self._label.setFont(font)
        # No QTextDocument margin. The name sits INSIDE the oval at rest
        # and moves BELOW it while a simulation view (Animate, Step,
        # Failure Effects, the after-run DSI) owns the body's centre and
        # colour -- see `_name_below`.
        self._label.document().setDocumentMargin(0)
        self._label.setDefaultTextColor(QColor(COLOUR["relationship"]))
        self._layout_label()
        self._anal_label = QGraphicsTextItem("", self)
        self._anal_label.setFont(QFont("sans-serif", 8))
        self._anal_label.document().setDocumentMargin(0)
        self._anal_label.setDefaultTextColor(QColor(COLOUR["text"]))
        self._anal_label.hide()

        #: End-of-run Descendant Status Indicator: None until a
        #: simulation pushes per-hierarchy rollups; then Raptor 7's
        #: symbol -- two stacked downward triangles in the CENTRE of the
        #: oval (`HierMid`, manual Ch.16 p.16-15).
        #: The attribute keeps its historical name `_dsi_dot`; it is a
        #: path item, not a dot.
        self._dsi_info: dict | None = None
        self._dsi_dot = QGraphicsPathItem(self._dsi_triangles(), self)
        self._dsi_dot.setPos(0, -1)
        # Never swallow the oval's clicks/double-click: the marker is a
        # label, not a target (the old ellipse sat over the top-right
        # edge and could take the click instead of the hierarchy).
        self._dsi_dot.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._dsi_dot.hide()
        # Live Simulation View state colour guard -- same contract as
        # `_BlockItem._live_active` (selection must not retheme the
        # running state colour back to the default).
        self._live_active = False
        # Forget the state colour too: `set_live_fill` skips a colour it
        # thinks is already painted, so a stale one left the item plain
        # when the same colour came back (Weak Link off and on again).
        self._live_colour = None

    @staticmethod
    def _dsi_triangles() -> QPainterPath:
        """Raptor 7's `HierMid` symbol: TWO stacked downward triangles,
        centred on the hierarchy image (--
        a 5-type polygon with exactly these two triangle outlines; manual
        Figure 16.31/16.32). Sized for the 150x56 oval."""
        path = QPainterPath()
        for top in (-9.0, -0.5):
            path.moveTo(-6.0, top)
            path.lineTo(6.0, top)
            path.lineTo(0.0, top + 7.0)
            path.closeSubpath()
        return path

    def set_dsi(self, info: dict | None, c: Mapping[str, Any] | None = None):
        """Raptor 7's Descendant Status Indicator (DSI): the two stacked
        triangles in the centre of the oval. It shows how the hierarchies
        INSIDE this one are doing -- NOT this hierarchy's own state (that
        is the body colour).

        Live (Animate, Step): `{"state": "AllUp"|"Degraded"|"Down"|...,
        "nodes": n, "down": n, "degraded": n}`. After a run:
        `{"kind": "average", "state": band, "availability", "working",
        "degraded", "down", "runs"}` -- the share of time over every run
        (`weak_metric.hier_time_shares`). None clears it. `c` overrides
        the palette so a theme switch can't leave a stale tint."""
        self._dsi_info = info
        if not info:
            self._dsi_dot.setVisible(False)
            self._dsi_dot.setToolTip("")
            self._label.setToolTip("")
            self.setToolTip("")
            self._relayout_labels()
            return
        c = c or COLOUR
        # All six node statuses Raptor 7's DSI can show (`ChangeDSIStatus`,
        # Raptor 7), plus White/Black for a Linked/Cut out
        # node. Degraded is Yellow, not the theme's `degraded` token:
        # that token is the over-stress green (Appendix D), which made a
        # yellow DSI render nearly invisible on the green body.
        state = info.get("state", "")
        colour = {"AllUp": c["operating"],
                  "Degraded": APPENDIX_D_STATE_COLOURS["degraded"],
                  "NodeStandby": APPENDIX_D_STATE_COLOURS["warm_standby"],
                  "NodeIdle": APPENDIX_D_STATE_COLOURS["idle"],
                  "NodePM": APPENDIX_D_STATE_COLOURS["maintenance"],
                  "Down": c["failed"],
                  "Linked": "#FFFFFF",
                  "Cut": "#000000"}.get(state, c["muted"])
        self._dsi_dot.setBrush(QBrush(QColor(colour)))
        self._dsi_dot.setPen(QPen(Qt.PenStyle.NoPen))
        self._dsi_dot.setVisible(True)
        self._relayout_labels()
        tip = self._dsi_text(info)
        self._dsi_dot.setToolTip(tip)
        # The name is outside the body but still part of the element.
        self._label.setToolTip(tip)
        self.setToolTip(tip)

    @staticmethod
    def _dsi_text(info: dict) -> str:
        """The DSI in words."""
        if info.get("kind") == "average":
            runs = int(info.get("runs", 0))

            def pct(k: str) -> str:
                return f"{100.0 * float(info.get(k, 0.0)):.1f}%"
            standby = (f", on standby {pct('standby')}"
                       if float(info.get("standby", 0.0)) >= 0.0005 else "")
            return (f"Descendant Status Indicator (DSI): over {runs:,} "
                    f"run{'s' if runs != 1 else ''}, everything inside this "
                    f"hierarchy was working {pct('working')} of the time, "
                    f"partly failed {pct('degraded')}{standby} and down "
                    f"{pct('down')} (failed, in maintenance or waiting on a "
                    f"dependency).\n"
                    f"Availability {float(info.get('availability', 0.0)):.6g}, "
                    f"coloured by the Weak Link bands. The oval's own body "
                    f"colour is this hierarchy's status; the triangles are "
                    f"the worst status of the hierarchies inside it.")
        state = info.get("state", "")
        words = {
            "AllUp": "this hierarchy and the hierarchies inside it are up, "
                     "and none is degraded",
            "Degraded": "this hierarchy and the hierarchies inside it are "
                        "up; at least one is degraded",
            "NodeStandby": "this hierarchy and the hierarchies inside it are "
                           "up; at least one is on standby",
            "Down": "this hierarchy or at least one hierarchy inside it is "
                    "down",
            "NodeIdle": "this hierarchy or at least one hierarchy inside it "
                        "is idle",
            "NodePM": "this hierarchy or at least one hierarchy inside it is "
                      "down for preventive maintenance",
            "Linked": "this hierarchy's output is Linked (Failure Effects)",
            "Cut": "this hierarchy's output is Cut (Failure Effects)",
        }.get(state, state or "unknown")
        down, nodes = info.get("down", 0), info.get("nodes", 0)
        detail = (f" ({down}/{nodes} nodes down, "
                  f"{info.get('degraded', 0)} degraded)" if nodes else "")
        return f"Descendant Status Indicator (DSI): {words}{detail}."

    def _name_below(self) -> bool:
        """True while a simulation view uses the body: a live state fill
        (Animate, Step, Failure Effects) or the DSI triangles in the
        centre. Then the name moves under the oval, Raptor 7's Hierarchy
        label position, so the animation never clashes with the text."""
        dsi = getattr(self, "_dsi_dot", None)
        return bool(getattr(self, "_live_active", False)
                    or (dsi is not None and dsi.isVisible()))

    def _layout_label(self) -> None:
        """Name centred in the oval at rest, under it while simulating.
        A long name is shrunk to fit inside rather than spilling out."""
        if self._name_below():
            self._label.setScale(1.0)
            tm = self._label.boundingRect()
            self._label.setPos(-tm.width() / 2, self.H / 2 + 2)
            return
        tm = self._label.boundingRect()
        room = self.W - 24
        scale = min(1.0, room / tm.width()) if tm.width() > 0 else 1.0
        self._label.setScale(scale)
        self._label.setPos(-tm.width() * scale / 2,
                           -tm.height() * scale / 2)

    def _layout_anal_label(self) -> None:
        """Weak-link value: under the name when the name is below the
        oval, otherwise straight under the oval."""
        tm = self._anal_label.boundingRect()
        drop = (self._label.boundingRect().height() + 1
                if self._name_below() else 0)
        self._anal_label.setPos(-tm.width() / 2, self.H / 2 + 2 + drop)

    def _relayout_labels(self) -> None:
        self._layout_label()
        self._layout_anal_label()

    def set_name(self, name: str):
        self._label.setPlainText(name)
        self._layout_label()
        self._layout_anal_label()

    def set_anal_label(self, text: str | None,
                       tooltip: str | None = None) -> None:
        if not text:
            self._anal_label.hide()
            self._anal_label.setToolTip("")
            return
        self._anal_label.setPlainText(text)
        self._anal_label.setToolTip(tooltip or "")
        self._layout_anal_label()
        self._anal_label.setDefaultTextColor(QColor(COLOUR["text"]))
        self._anal_label.show()

    def retheme(self, c: Mapping[str, Any]):
        selected = self.isSelected()
        self._live_active = False
        # Forget the state colour too: `set_live_fill` skips a colour it
        # thinks is already painted, so a stale one left the item plain
        # when the same colour came back (Weak Link off and on again).
        self._live_colour = None
        self.setPen(QPen(QColor(c["accent"] if selected else c["relationship"]),
                         3 if selected else 2))
        self.setBrush(QBrush(QColor(c["group_header"])))
        self._label.setDefaultTextColor(QColor(
            c["accent"] if selected else c["relationship"]))
        self._relayout_labels()
        # the DSI's colour encodes state via theme tokens -- re-derive
        # from the palette being applied so a theme switch can't leave a
        # stale tint
        if getattr(self, "_dsi_info", None):
            self.set_dsi(self._dsi_info, c)

    def set_live_fill(self, colour_hex: str, tooltip: str | None = None):
        """Solid state colour during the live simulation view (Appendix
        D "Hierarchy Colors": Good/Degraded/Standby/Idle/PM/Bad) --
        colours the whole oval; the name moves below it. The DSI triangles are a separate child item (see
        `set_dsi`). See `_BlockItem.set_live_fill`'s own docstring;
        `DiagramView.retheme` restores the themed fill/pen afterwards,
        this class included."""
        colour = QColor(colour_hex)
        if getattr(self, "_live_colour", None) == colour.name():
            if tooltip:
                self.setToolTip(tooltip)
            return
        self._live_colour = colour.name()
        self.setBrush(QBrush(colour))
        self.setPen(QPen(colour, 2))
        self._live_active = True
        # Deliberately NOT `readable_text_colour(body)`: while the state
        # colour is showing the name is on the canvas below the oval, so
        # it keeps the themed text colour.
        self._relayout_labels()
        if tooltip:
            self.setToolTip(tooltip)

    def set_phased(self, phased: bool):
        """See `_BlockItem.set_phased` -- Raptor 7's hatched marker
        (the manual's own worked example, Phasing1.rbd, marks its
        "500path" hierarchy phased alongside its block/node/event
        peers), same overlay-in-paint() approach."""
        if self._phased != bool(phased):
            self._phased = bool(phased)
            self.update()

    def paint(self, painter: QPainter, option, widget=None):
        super().paint(painter, option, widget)
        if self._phased:
            _phased_hatch(painter, "ellipse", self.rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(10 if value else -1)
            # Same live-view guard as `_BlockItem.itemChange`: selection
            # must not snap the running state colour back to the
            # themed default.
            if not getattr(self, "_live_active", False):
                self.retheme(COLOUR)
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            pos = self.pos()
            snap = _snap_of(self)
            new = QPointF(_snapped_x(pos.x(), snap), _snapped_y(pos.y(), snap))
            if new != pos:
                self.setPos(new)
                return super().itemChange(change, value)
            if self._on_moved:
                self._on_moved(self._hier_id, self)
        return super().itemChange(change, value)

def _apply_hover_cursor(item):
    """Set an item's hover cursor from the armed tool: cross for
    Connect (click to link), hand otherwise."""
    try:
        views = item.scene().views() if item.scene() else []
        view = views[0] if views else None
    except Exception:
        view = None
    item.setCursor(_hover_cursor_for(view))

def _hover_cursor_for(view):
    """Cursor items should show on hover, based on the armed tool:
    cross for Connect (click to link), open-hand for others."""
    from PySide6.QtCore import Qt as _Qt
    if view is not None and getattr(view, "tool_mode", "select") \
            == "connect":
        return _Qt.CursorShape.CrossCursor
    return _Qt.CursorShape.PointingHandCursor

def _view_status(view, msg: str):
    """Best-effort status hint: MainWindow attaches a sink."""
    sink = getattr(view, "status_message_sink", None)
    if callable(sink):
        sink(msg)

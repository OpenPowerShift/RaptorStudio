"""Shared helpers, the grid background and the block and node items of the diagram canvas."""
from __future__ import annotations
from collections.abc import Callable, Mapping
from typing import Any
from math import (ceil, floor)
from PySide6.QtCore import (
    Qt, QRectF, QPointF,
)
from PySide6.QtGui import (
    QPen, QBrush, QColor, QFont, QFontMetricsF, QPainter, QLinearGradient, QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsTextItem,
    QGraphicsPathItem,
    QStyle, QStyleOptionGraphicsItem,
)
from PySide6.QtGui import QPainterPath
from raptor.gui.resources.icons import arrow_head_len, arrow_head_points
from raptor.gui.resources.design_tokens import (
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
        self._live_colour: str | None = None

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
        # Initial placement must not run the drag-snap or notify the
        # view: 9,000 blocks at load paid itemChange cost for positions
        # that were already exactly where the file says.
        self._suspend_changes = True
        self.setPos(cx - w / 2, cy - h / 2)
        self._suspend_changes = False

        self._label = QGraphicsTextItem(name, self)
        font = QFont("sans-serif", 10)
        font.setBold(True)
        self._label.setFont(font)
        # At-a-glance labels under the name (means, spread or rate -- see
        # `block_labels`), drawn small so the name stays dominant. Built
        # lazily: a fit-to-window large level hides labels, and creating a
        # QGraphicsTextItem + QTextDocument for every block was several
        # seconds of pure construction.
        self._sub: QGraphicsTextItem | None = None
        # QTextDocument's default ~4px margin is what created the
        # "gap the height of the name" between title and F/R.
        self._label.document().setDocumentMargin(0)
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
        if self._sub is not None:
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
        if self._sub is None:
            if not lines:
                self.setToolTip(tooltip)
                return
            self._sub = QGraphicsTextItem("", self)
            self._sub.setFont(QFont("sans-serif", 7))
            self._sub.document().setDocumentMargin(0)
            self._sub.setDefaultTextColor(
                QColor(COLOUR.get("text_secondary", COLOUR["muted"])))
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
        if self._sub is not None:
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
        _apply_hover_cursor(self)
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
        if self._sub is not None and self._sub.toPlainText():
            sub_w = self._sub.boundingRect().width()
        needed = max(fm.horizontalAdvance(shown), sub_w) + 2 * _BLOCK_NAME_PAD
        # Round to a whole even pixel: the label is centred, and a
        # fractional width puts its two ends off the pixel grid.
        w = max(BLOCK_W, ceil(max(needed, BLOCK_W) / 2.0) * 2.0)
        if self.rect().width() != w:
            self.setRect(-(w - BLOCK_W) / 2, 0, w, BLOCK_H)
            # Routing caches the block's centre; the rect just changed.
            self._route_centre = None

    def _adjust_label(self):
        self._fit_name()
        tm = self._label.boundingRect()
        if self._sub is not None and self._sub.toPlainText():
            # Name at top; F/R tucked directly beneath.
            self._label.setPos((BLOCK_W - tm.width()) / 2, 2)
            sm = self._sub.boundingRect()
            self._sub.setPos((BLOCK_W - sm.width()) / 2,
                             2 + tm.height() - 1)
        else:
            self._label.setPos((BLOCK_W - tm.width()) / 2,
                               (BLOCK_H - tm.height()) / 2 - 2)

    def itemChange(self, change, value):
        if getattr(self, "_suspend_changes", False):
            return super().itemChange(change, value)
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
        self.setPos(cx, cy)

        type_names = {1: "S", 2: label_text or "", 3: "E", 4: "HI", 5: "HO"}
        t = type_names.get(node_type, "")
        inner = QGraphicsTextItem(t, self)
        ifont = QFont("sans-serif", 8 if node_type in (1, 3) else 7)
        ifont.setBold(True)
        inner.setFont(ifont)
        inner.setToolTip({"1": "Start node", "3": "End node"}.get(
            str(node_type), "k-of-n node"))  # same default as _inner_tip()
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
        self._live_colour: str | None = None
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
            str(self._node_type), "k-of-n node")

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
        _apply_hover_cursor(self)
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
    """Cursor an element shows on hover: the view's element cursor, which
    is the armed tool's cursor (Add Block, Connect...) or the pointing
    hand with no tool armed. A fixed hand here replaced the tool cursor
    whenever a block or node came under the pointer, e.g. while zooming."""
    from PySide6.QtCore import Qt as _Qt
    cursor_for = getattr(view, "element_cursor", None)
    if callable(cursor_for):
        return cursor_for()
    return _Qt.CursorShape.PointingHandCursor


def _view_status(view, msg: str):
    """Best-effort status hint: MainWindow attaches a sink."""
    sink = getattr(view, "status_message_sink", None)
    if callable(sink):
        sink(msg)

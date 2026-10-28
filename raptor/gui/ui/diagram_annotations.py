"""The annotation layer (.rbds only): note/shape/arrow items, their
handles, and the commands that add, edit and comment."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from PySide6.QtCore import (
    Qt, QRectF, QPointF, QLineF,
)
from PySide6.QtGui import (
    QPen, QBrush, QColor, QFont,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsTextItem,
    QGraphicsLineItem, QStyle, QStyleOptionGraphicsItem,
)
from PySide6.QtGui import QPainterPath
from raptor.gui.ui.contrast import contrast_ratio
from raptor.gui.resources.design_tokens import (
    COLOUR,
)
from raptor.gui.ui.diagram_commands import (  # noqa: I001
    _ViewCmd,
)
from raptor.gui.ui.diagram_items import (  # noqa: I001
    _arrow,
)


# =====================================================================
# ANNOTATION LAYER (.rbds only; dropped with a warning on legacy save)
# =====================================================================

# DELEGATED to `raptor.core.annotations` (review E-07). Both the defaults
# and the z bands were written out here AND in `api/annotations.py`, with the
# API copy commenting that it was "kept here rather than imported so the API
# layer never needs Qt" -- a sound reason and the wrong mechanism. The
# cross-reference rotted independently of the values: it still pointed at
# `gui/ui/diagram.py` after the copy had moved to THIS file, and nothing
# noticed because the numbers still agreed.
from raptor.core.annotations import (  # noqa: F401  (re-exported)
    ANNO_DEFAULTS,
    ANNO_Z_BANDS,
    ANNO_Z_BEHIND,
    ANNO_Z_FRONT,
    ANNO_Z_MIDDLE,
)

#: The four z-order commands: key -> label. The key is what the ribbon
#: action, the menu item and the keyboard shortcut all dispatch on; the
#: label doubles as the undo command's text, so the history reads "Bring
#: to Front" rather than the generic "Edit annotation" AnnoEditCmd would
#: otherwise use. The TARGET z is not here -- it depends on the current
#: selection, so `annotation_order_target` computes it.
ANNO_ORDER_COMMANDS = {
    "front": "Bring to Front",
    "forward": "Bring Forward",
    "backward": "Send Backward",
    "back": "Send to Back",
}

#: Line-style dropdown choices (label -> Qt.PenStyle name): Qt's
#: standard dashed-line set.
ANNO_LINE_STYLES = {
    "solid": Qt.PenStyle.SolidLine,
    "dash": Qt.PenStyle.DashLine,
    "dot": Qt.PenStyle.DotLine,
    "dash-dot": Qt.PenStyle.DashDotLine,
    "dash-dot-dot": Qt.PenStyle.DashDotDotLine,
}

def _anno_base_props() -> dict:
    """Factory props for a newly placed annotation, re-tinted for the
    active theme. `ANNO_DEFAULTS` itself stays a static, light-theme-
    tuned fallback (used when loading a saved entry that's missing a
    colour key entirely) -- but a brand-new annotation should start
    visible in whichever theme is active right now: ANNO_DEFAULTS'
    ["text_colour"]/["border_colour"] are literally light theme's own
    "text"/"muted" tokens, and the default text colour in particular is
    near-black, close to invisible against the dark theme's near-black
    canvas. A colour the user has explicitly set is never touched by a
    theme switch -- this only affects the starting colour of a shape
    that hasn't been customised yet."""
    props = dict(ANNO_DEFAULTS)
    props["border_colour"] = COLOUR["muted"]
    props["text_colour"] = COLOUR["text"]
    return props

#: Annotation kinds as the user names them in menus, singular and plural.
ANNO_KIND_NOUNS = {
    "rect": ("Rectangle", "Rectangles"),
    "ellipse": ("Ellipse", "Ellipses"),
    "text": ("Text Box", "Text Boxes"),
    "arrow": ("Arrow", "Arrows"),
}

#: Props each kind actually HAS, matching what
#: `InspectorDock.show_annotation` offers for it. A saved style default
#: is filtered through this, so persisting a rectangle's style does not
#: drag its (invisible, unused) font and text colour along, and a text
#: box never picks up a line style it has no control for.
ANNO_APPLICABLE_PROPS = {
    "rect": ("border_colour", "border_width", "line_style", "fill_colour"),
    "ellipse": ("border_colour", "border_width", "line_style",
                "fill_colour"),
    "text": ("border_colour", "border_width", "fill_colour", "text_colour",
             "font_family", "font_size", "font_bold", "font_italic"),
    "arrow": ("border_colour", "border_width", "line_style"),
}

def _anno_pen_brush(props):
    border = props.get("border_colour", ANNO_DEFAULTS["border_colour"])
    width = float(props.get("border_width", ANNO_DEFAULTS["border_width"]))
    # width==0 means "no border" (a Qt cosmetic pen at width 0 still
    # draws a visible 1px line, so this needs its own check) -- the
    # user's control for turning a border off, alongside the border
    # colour picker's own "(none)" option.
    pen: QPen | Qt.PenStyle
    if border and width > 0:
        pen = QPen(QColor(border), width)
        pen.setStyle(ANNO_LINE_STYLES.get(
            props.get("line_style", "solid"), Qt.PenStyle.SolidLine))
    else:
        pen = Qt.PenStyle.NoPen   # no border
    fill = props.get("fill_colour")
    brush = QBrush(QColor(fill)) if fill else Qt.BrushStyle.NoBrush
    return pen, brush

#: Below this ratio a colour pairing reads as "hard to see" rather than
#: literally invisible -- looser than WCAG AA's 3.0 minimum (large
#: text/graphics) since these are user-picked decorative colours, not
#: body text; better to under- than over-flag and nag on every switch.
_LOW_CONTRAST_RATIO = 2.0

def annotation_is_low_contrast(a: dict) -> bool:
    """True if annotation dict `a` (id/kind/parent/props -- the same
    shape `DiagramView.annotations` holds, one entry per saved
    annotation) would be hard to see against the currently active
    theme's canvas. Checks whichever of text/border/fill applies to this
    annotation's `kind`, each against whatever it's actually drawn over
    (its own fill if one is set, else the bare canvas)."""
    kind = a.get("kind")
    canvas = COLOUR["canvas"]
    fill = a.get("fill_colour")
    over = fill or canvas
    if fill and contrast_ratio(fill, canvas) < _LOW_CONTRAST_RATIO:
        return True
    # A None colour is a legitimate stored value -- it is how "(none)"
    # round-trips through the .rbds, and the API can set it directly --
    # and it simply means "not drawn", so there is nothing to contrast.
    # `QColor(None)` is invalid and would silently compare as black
    # instead, flagging a deliberately borderless note as unreadable.
    if kind == "text" and a.get("text_colour") is not None:
        text_colour = a.get("text_colour", ANNO_DEFAULTS["text_colour"])
        if contrast_ratio(text_colour, over) < _LOW_CONTRAST_RATIO:
            return True
    border_width = float(a.get("border_width",
                               ANNO_DEFAULTS["border_width"]))
    if kind in ("rect", "ellipse", "arrow") and border_width > 0 \
            and a.get("border_colour") is not None:
        border = a.get("border_colour", ANNO_DEFAULTS["border_colour"])
        if contrast_ratio(border, over) < _LOW_CONTRAST_RATIO:
            return True
    return False

def _shift_annotation(item, dx: float, dy: float):
    """Nudge one canvas annotation by (dx, dy). `_AnnoLine` keeps its
    geometry in absolute line() endpoints, so it gets a dedicated shift
    that moves both ends; everything else just moves pos(). `item` is
    deliberately untyped: the annotation classes share `_AnnotationBase`,
    which has no static Qt base for mypy to resolve the Qt methods on."""
    if isinstance(item, _AnnoLine):
        item.translate_by(dx, dy)
    else:
        item.setPos(item.pos() + QPointF(dx, dy))

if TYPE_CHECKING:
    # The mixins below are combined with a concrete QGraphicsItem subclass at runtime; for the type
    # checker they stand on QGraphicsItem so the calls they make through `self` resolve.
    _ItemBase = QGraphicsItem
else:
    _ItemBase = object


class _HandleHitAreaMixin(_ItemBase):
    """Widens a small handle's clickable area past its painted square
    (painted stays `rect()`, e.g. 10x10 -- the hit area is `rect()`
    expanded by `_HIT_MARGIN` on every side). The owning shape's
    `shape()` accepts clicks only on its thin perimeter band, so a click
    that was a near-miss on a tiny handle would land on empty canvas and
    deselect everything instead of grabbing the handle.

    It also drives the drag itself. Qt's default handling would move the
    handle's *selected parent* annotation (a child drag of a selected item
    drags the whole selection), so a handle press appeared to move the
    shape instead of resizing it. Intercepting the press/move and just
    repositioning the handle reuses each handle's own `itemChange` ->
    `owner.resize_to`/`resize_end` wiring."""
    _HIT_MARGIN = 8.0

    def boundingRect(self):
        m = self._HIT_MARGIN
        return super().boundingRect().adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Accept without running the movable-item default, which would
            # begin dragging the selected parent annotation.
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._drag_to(event.scenePos())
            event.accept()
        else:
            event.ignore()

    def mouseReleaseEvent(self, event):
        event.accept()

    def _drag_to(self, scene_pos):
        """Resize the owner for a handle drag; each handle maps the scene
        point into its owner's coordinates."""
        raise NotImplementedError

class _AnnoHandle(_HandleHitAreaMixin, QGraphicsRectItem):
    """Bottom-right resize handle for shape annotations. Hidden until
    the owning annotation is selected -- `_AnnotationBase.itemChange`'s
    ItemSelectedHasChanged branch is what shows/hides it."""

    def __init__(self, owner):
        super().__init__(-5, -5, 10, 10)
        self._owner = owner
        self.setBrush(QBrush(QColor(COLOUR["accent"])))
        self.setPen(Qt.PenStyle.NoPen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setZValue(30)
        self.setVisible(False)

    def _drag_to(self, scene_pos):
        self._owner.resize_to(self._owner.mapFromScene(scene_pos))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._owner.resize_to(self.pos())
        return super().itemChange(change, value)

class _AnnoEndHandle(_HandleHitAreaMixin, QGraphicsRectItem):
    """Drag handle for one end (tail or head) of an arrow annotation --
    _AnnoHandle's own single-corner design doesn't fit a line, which
    has two independent endpoints instead of one anchored corner."""

    def __init__(self, owner, is_head: bool):
        super().__init__(-5, -5, 10, 10)
        self._owner = owner
        self._is_head = is_head
        self.setBrush(QBrush(QColor(COLOUR["accent"])))
        self.setPen(Qt.PenStyle.NoPen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(30)
        self.setVisible(False)   # see _AnnoHandle's own comment

    def _drag_to(self, scene_pos):
        self._owner.resize_end(self._is_head,
                               self._owner.mapFromScene(scene_pos))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._owner.resize_end(self._is_head, self.pos())
        return super().itemChange(change, value)

class _CursorHook:
    """Hover cursor follows the armed tool (cross for Connect)."""

def _anno_selection_ring(painter, item, rect: QRectF):
    """Themed accent dashed selection ring, matching what
    `_BlockItem.paint`/`_NodeItem.paint` draw for elements.

    Without it the rect/ellipse/text annotation classes fall back to Qt's
    default black dotted selection rectangle, which is invisible on the
    dark theme and doesn't match the rest of the canvas -- the same
    gesture read three different ways depending on what was selected.
    `_AnnoLine` already drew its own; it now shares this one."""
    if not item.isSelected():
        return
    painter.save()
    painter.setPen(QPen(QColor(COLOUR["accent"]), 2, Qt.PenStyle.DashLine))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)
    painter.restore()

def _anno_unselected_option(option):
    """`option` with `State_Selected` cleared, so `super().paint()`
    doesn't draw Qt's own black dotted selection rectangle underneath
    ours. `_BlockItem.paint` does the same for the same reason."""
    option = QStyleOptionGraphicsItem(option)
    option.state &= ~QStyle.StateFlag.State_Selected
    return option

class _AnnotationBase(_ItemBase):
    """Shared behaviour: identity, props, sync hook."""

    def _init_anno(self, anno_id: int, parent_level: int, props: dict,
                   view):
        self._anno_id = anno_id
        self._parent_level = parent_level
        self.props = dict(ANNO_DEFAULTS)
        self.props.update(props or {})
        self._view = view
        self._handle: Any = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(20)

    @property
    def kind(self):
        raise NotImplementedError

    def apply_props(self):
        pen, brush = _anno_pen_brush(self.props)
        shape: Any = self     # the concrete subclass is a QAbstractGraphicsShapeItem
        shape.setPen(pen)
        if hasattr(self, "setBrush"):
            shape.setBrush(brush)
        tc = QColor(self.props.get("text_colour",
                                   ANNO_DEFAULTS["text_colour"]))
        f = QFont(self.props.get("font_family",
                                 ANNO_DEFAULTS["font_family"]),
                  int(self.props.get("font_size", 10)))
        f.setBold(bool(self.props.get("font_bold")))
        f.setItalic(bool(self.props.get("font_italic")))
        if hasattr(self, "_label"):
            self._label.setDefaultTextColor(tc)
            self._label.setFont(f)
            try:
                doc = self._label.document()
                doc.setDefaultFont(f)
            except Exception:
                pass
        self.setToolTip("Annotation — double-click to edit text, "
                        "right-click for colours/font")

    def ensure_handle(self):
        if self._handle is None and hasattr(self, "resize_to"):
            self._handle = _AnnoHandle(self)
            h = self._handle
            h.setParentItem(self)
            r = cast(Any, self).rect() if hasattr(self, "rect") else \
                self.sceneBoundingRect()
            h.setPos(r.right(), r.bottom())

    def resize_to(self, handle_pos):
        rect_item: Any = self
        r = rect_item.rect()
        w = max(12.0, handle_pos.x() - r.left())
        h = max(12.0, handle_pos.y() - r.top())
        rect_item.setRect(r.left(), r.top(), w, h)
        self._handle.setPos(r.left() + w, r.top() + h)
        # Mirror the new size into the model entry, so it survives a
        # level rebuild and the release captures an undoable change.
        view = getattr(self, "_view", None)
        if view is not None and not getattr(view, "_loading", False):
            view.sync_annotation_geometry(self)

    def geometry_snapshot(self):
        sp = self.scenePos()
        return {"x": round(sp.x(), 2), "y": round(sp.y(), 2)}

    def _perimeter_shape(self, path: QPainterPath) -> QPainterPath:
        """A ~10px hit band around `path` (the shape's own outline),
        same width `_LinkItem.shape()` already uses for a link's click
        zone -- shared by `_AnnoRect`/`_AnnoEllipse`'s own `shape()`
        overrides. A filled shape's default Qt `shape()` covers its
        whole interior, silently eating clicks meant for a block/node
        sitting underneath it."""
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(path)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            view = getattr(self, "_view", None)
            if view is not None and not getattr(view, "_loading", False):
                view.sync_annotation_geometry(self)
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            # Grab handles only make sense while selected.
            for name in ("_handle", "_tail_handle", "_head_handle"):
                h = getattr(self, name, None)
                if h is not None:
                    h.setVisible(bool(value))
        return super().itemChange(change, value)

class _AnnoRect(_AnnotationBase, QGraphicsRectItem):
    # `_AnnotationBase` must come first in the base list: PySide6
    # resolves a virtual override (`itemChange`) by walking the MRO for
    # the first class that defines it -- with the Qt class first,
    # `QGraphicsRectItem`'s own inherited `itemChange` (a real, if
    # no-op, attribute) is found before `_AnnotationBase.itemChange`, so
    # it never fires on move/resize. `_AnnotationBase` has no `__init__`
    # of its own, so `super().__init__(...)` below still reaches
    # `QGraphicsRectItem.__init__` -- reordering is safe.
    kind = "rect"

    def __init__(self, anno_id, parent_level, props, view,
                 x=0.0, y=0.0, w=120.0, h=70.0):
        super().__init__(0, 0, w, h)
        self._init_anno(anno_id, parent_level, props, view)
        self.apply_props()
        self.ensure_handle()
        # Place at the annotation's x/y rather than the scene origin.
        self.setPos(x, y)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.rect())
        # Once selected, grab from anywhere inside -- a filled shape is
        # naturally dragged from its middle. While unselected only the
        # perimeter band hits, so clicks still fall through to any
        # element underneath (see _perimeter_shape's own comment).
        if self.isSelected():
            return path
        return self._perimeter_shape(path)

    def paint(self, painter, option, widget=None):
        super().paint(painter, _anno_unselected_option(option), widget)
        _anno_selection_ring(painter, self, self.rect().adjusted(2, 2, -2, -2))

class _AnnoEllipse(_AnnotationBase, QGraphicsEllipseItem):
    # Base order matters -- see _AnnoRect's own comment for why.
    kind = "ellipse"

    def __init__(self, anno_id, parent_level, props, view,
                 x=0.0, y=0.0, w=120.0, h=80.0):
        super().__init__(0, 0, w, h)
        self._init_anno(anno_id, parent_level, props, view)
        self.apply_props()
        # See _AnnoRect's own comment.
        self.setPos(x, y)
        self.ensure_handle()

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(self.rect())
        # Selected -> full interior; unselected -> perimeter only (see
        # _AnnoRect.shape's own comment).
        if self.isSelected():
            return path
        return self._perimeter_shape(path)

    def paint(self, painter, option, widget=None):
        super().paint(painter, _anno_unselected_option(option), widget)
        _anno_selection_ring(painter, self, self.rect().adjusted(2, 2, -2, -2))

class _AnnoText(_AnnotationBase, QGraphicsTextItem):
    # Base order matters -- see _AnnoRect's own comment for why.
    kind = "text"

    #: Inner padding between the text and its frame, and the document
    #: margin used to inset the glyphs from the box border.
    _PAD = 4.0

    def __init__(self, anno_id, parent_level, props, view,
                 x=0.0, y=0.0, text="Text", w=120.0, h=40.0):
        super().__init__(text)
        self._box_w = max(20.0, float(w))
        self._box_h = max(12.0, float(h))
        self._init_anno(anno_id, parent_level, props, view)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.apply_props()
        self.setPos(x, y)
        self.ensure_handle()

    def rect(self) -> QRectF:
        """The text box's fixed frame in local coords. Lets the shared
        handle/sync code treat a text box like the rect/ellipse shapes,
        even though QGraphicsTextItem has no `rect()` of its own."""
        return QRectF(0.0, 0.0, self._box_w, self._box_h)

    def set_box(self, w: float, h: float):
        """Resize the box; the text reflows (wraps) to the new width."""
        self.prepareGeometryChange()
        self._box_w = max(20.0, float(w))
        self._box_h = max(12.0, float(h))
        self.setTextWidth(self._box_w)
        if self._handle is not None:
            self._handle.setPos(self._box_w, self._box_h)
        self.update()

    def apply_props(self):
        tc = QColor(self.props.get("text_colour",
                                   ANNO_DEFAULTS["text_colour"]))
        f = QFont(self.props.get("font_family",
                                 ANNO_DEFAULTS["font_family"]),
                  int(self.props.get("font_size", 10)))
        f.setBold(bool(self.props.get("font_bold")))
        f.setItalic(bool(self.props.get("font_italic")))
        self.setDefaultTextColor(tc)
        self.setFont(f)
        # Wrap the text to the box width (setTextWidth) rather than
        # letting it grow sideways forever; the document margin insets
        # the glyphs from the frame border.
        self.prepareGeometryChange()
        self.document().setDocumentMargin(self._PAD)
        self.setTextWidth(self._box_w)
        # Background frame: fill + border make the same Colours & Font
        # panel meaningful for text as for shapes; empty fill/None =
        # no frame drawn (still selectable/movable via its bounds).
        #
        # The pen comes from `_anno_pen_brush`, the SAME builder the
        # rect/ellipse use, so "border colour = none" means the same
        # thing for every kind. It used to be rebuilt here with
        # `props.get("border_colour") or ANNO_DEFAULTS[...]`, which
        # silently replaced a deliberate "(none)" with the default
        # grey and kept drawing a border the user had asked to remove.
        self._frame_fill = QColor(
            self.props["fill_colour"]) if self.props.get(
                "fill_colour") else None
        self._frame_pen, _brush = _anno_pen_brush(self.props)
        self.update()
        self.setToolTip("Text annotation — double-click to edit, "
                        "drag the corner to resize")

    def _border_width(self) -> float:
        return float(self.props.get("border_width",
                                    ANNO_DEFAULTS["border_width"]))

    def boundingRect(self):
        text = super().boundingRect()
        box = self.rect().united(text)
        pad = max(self._PAD, self._border_width() / 2 + 1)
        return box.adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        # The visible frame is drawn around the whole box; make the whole
        # box clickable so the frame and its border are grabbable.
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def paint(self, painter, option, widget=None):
        box = self.rect()
        pen = getattr(self, "_frame_pen", None)
        has_pen = pen is not None and pen != Qt.PenStyle.NoPen
        if getattr(self, "_frame_fill", None) is not None or has_pen:
            # Only a fill, only a border, or both -- each drawn from the
            # same `_anno_pen_brush` result, so a "none" border is a
            # genuinely absent border.
            painter.setPen(pen if has_pen else QPen(Qt.PenStyle.NoPen))
            painter.setBrush(QBrush(self._frame_fill)
                             if self._frame_fill is not None
                             else Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 3, 3)
        # Ring BEFORE the text, unlike the plain shapes: a text
        # annotation's glyphs are drawn by the item itself rather than
        # being child items, so a ring painted afterwards would draw
        # straight across the first line of the label.
        _anno_selection_ring(painter, self, box.adjusted(2, 2, -2, -2))
        super().paint(painter, _anno_unselected_option(option), widget)

    def mouseDoubleClickEvent(self, event):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus()
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.prepareGeometryChange()   # re-measure after edits
        # don't leave a lingering selection inside the box
        c = self.textCursor()
        c.clearSelection()
        self.setTextCursor(c)
        view = getattr(self, "_view", None)
        if view is not None:
            view.set_annotation_text(self, self.toPlainText())
        super().focusOutEvent(event)

    def resize_to(self, handle_pos):
        self.set_box(handle_pos.x(), handle_pos.y())
        view = getattr(self, "_view", None)
        if view is not None and not getattr(view, "_loading", False):
            view.sync_annotation_geometry(self)

    def geometry_snapshot(self):
        sp = self.scenePos()
        return {"x": round(sp.x(), 2), "y": round(sp.y(), 2),
                "w": round(self._box_w, 2), "h": round(self._box_h, 2)}

class _AnnoLine(_AnnotationBase, QGraphicsLineItem):
    # Base order matters -- see _AnnoRect's own comment for why.
    kind = "arrow"

    def __init__(self, anno_id, parent_level, props, view,
                 x1=0.0, y1=0.0, x2=100.0, y2=0.0):
        super().__init__(x1, y1, x2, y2)
        self._init_anno(anno_id, parent_level, props, view)
        # pos() stays fixed at the scene origin -- both endpoints live
        # entirely in the item's own local line() coordinates, which
        # for an untranslated item are scene coordinates. That keeps
        # the two end handles fully independent (each only ever
        # touches its own endpoint) with no pos()/line() coupling to
        # fight over, unlike the rect/ellipse handle which moves pos()
        # while resize_to() grows rect() out from a fixed top-left.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._tail_handle = None
        self._head_handle = None
        #: Gesture-start line captured on press; the whole-arrow drag
        #: translates this fixed baseline so Qt's cumulative drag
        #: positions can't compound (see mouseMoveEvent).
        self._press_line = None
        self.apply_props()
        self.ensure_handle()

    def apply_props(self):
        pen, _brush = _anno_pen_brush(self.props)
        if pen == Qt.PenStyle.NoPen:
            # the line is the whole annotation -- "no border" would
            # make it vanish entirely, unlike a rect/ellipse's fill.
            pen = QPen(QColor(self.props.get("border_colour")
                              or ANNO_DEFAULTS["border_colour"]), 1)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)
        self.setToolTip("Arrow — drag either end to adjust, "
                        "right-click for colour/line style")

    def ensure_handle(self):
        if self._tail_handle is None:
            self._tail_handle = _AnnoEndHandle(self, is_head=False)
            self._tail_handle.setParentItem(self)
            self._tail_handle.setPos(self.line().p1())
        if self._head_handle is None:
            self._head_handle = _AnnoEndHandle(self, is_head=True)
            self._head_handle.setParentItem(self)
            self._head_handle.setPos(self.line().p2())

    def resize_end(self, is_head: bool, new_pos):
        ln = self.line()
        self.prepareGeometryChange()
        if is_head:
            self.setLine(ln.x1(), ln.y1(), new_pos.x(), new_pos.y())
        else:
            self.setLine(new_pos.x(), new_pos.y(), ln.x2(), ln.y2())
        self._sync_handles()
        view = getattr(self, "_view", None)
        if view is not None and not getattr(view, "_loading", False):
            view.sync_annotation_geometry(self)

    def shape(self) -> QPainterPath:
        """Wide (10px) hit band along the shaft -- QGraphicsLineItem's
        default shape is just the pen width (~1px), which made the arrow
        practically impossible to click (the same band _LinkItem uses)."""
        path = QPainterPath()
        ln = self.line()
        path.moveTo(ln.p1())
        path.lineTo(ln.p2())
        return self._perimeter_shape(path)

    def _sync_handles(self):
        """Keep the two end handles on the endpoints after the whole
        arrow has been translated."""
        if self._tail_handle is not None:
            self._tail_handle.setPos(self.line().p1())
        if self._head_handle is not None:
            self._head_handle.setPos(self.line().p2())

    def translate_by(self, dx: float, dy: float):
        """Shift BOTH endpoints together -- the whole-arrow move. Its
        geometry lives in absolute line() coords (pos() stays at the
        origin), so a plain setPos() would move the item without moving
        the drawn line."""
        ln = self.line()
        self.prepareGeometryChange()
        self.setLine(ln.x1() + dx, ln.y1() + dy,
                     ln.x2() + dx, ln.y2() + dy)
        self._sync_handles()
        view = getattr(self, "_view", None)
        if view is not None and not getattr(view, "_loading", False):
            view.sync_annotation_geometry(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Fixed gesture baseline: Qt reports drag positions cumulatively
            # from button-down, so translating this each move is correct
            # without compounding earlier offsets.
            self._press_line = QLineF(self.line())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and \
                self._press_line is not None:
            d = event.scenePos() - event.buttonDownScenePos(
                Qt.MouseButton.LeftButton)
            self.prepareGeometryChange()
            self.setLine(self._press_line.translated(d))
            self._sync_handles()
            view = getattr(self, "_view", None)
            if view is not None and not getattr(view, "_loading", False):
                view.sync_annotation_geometry(self)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_line = None
        super().mouseReleaseEvent(event)

    def geometry_snapshot(self):
        ln = self.line()
        return {"x1": round(ln.x1(), 2), "y1": round(ln.y1(), 2),
                "x2": round(ln.x2(), 2), "y2": round(ln.y2(), 2)}

    def boundingRect(self):
        # Pad past QGraphicsLineItem's own default so the arrowhead's
        # wings (drawn beyond the shaft in paint(), below) aren't left
        # un-repainted/un-hit-testable at the head end.
        base = super().boundingRect()
        pad = 12
        return base.adjusted(-pad, -pad, pad, pad)

    def paint(self, painter, option, widget=None):
        pen = self.pen()
        width = pen.widthF()
        painter.setPen(pen)
        painter.setBrush(QBrush(pen.color()))
        ln = self.line()
        painter.drawLine(ln)
        # The head is sized from the SHAFT's width, not a fixed 8px, so
        # a thick arrow still reads as an arrow.
        _arrow(painter, ln.x1(), ln.y1(), ln.x2(), ln.y2(), width)
        _anno_selection_ring(painter, self, self.boundingRect())

class CommentCmd(_ViewCmd):
    """Undoable COMMENTS-section write for one element (old <-> new
    stored text, '~'-joined)."""

    def __init__(self, view, etype: str, oid: int, old: str, new: str):
        super().__init__(view, "Edit comment")
        self.etype, self.oid = etype, oid
        self.old, self.new = old, new

    def redo(self):
        self._v._apply_element_comment_stored(self.etype, self.oid,
                                              self.new)
        self._done()

    def undo(self):
        self._v._apply_element_comment_stored(self.etype, self.oid,
                                              self.old)
        self._done()

class AnnoCmd(_ViewCmd):
    """Undoable annotation lifecycle: add or remove one annotation,
    carrying its full state (entry dict + canvas props) so redo/undo
    rebuild it exactly."""

    def __init__(self, view, entry: dict, add: bool):
        super().__init__(view,
                         "Add annotation" if add else "Delete annotation")
        self.entry = dict(entry)
        self.add = add
        self._live_item = None

    def _canvas(self):
        a = self.entry
        cls = _ANNO_CLASSES.get(a.get("kind", ""))
        if cls is None:
            return None
        item = cls(int(a.get("id", 0)), int(a.get("parent", 0)), a,
                   self._v, *_anno_ctor_args(a))
        item.setZValue(float(a.get("z", ANNO_Z_MIDDLE)))
        return item

    def redo(self):
        v = self._v
        if self.add:
            v.annotations.append(dict(self.entry))
            item = self._canvas()
            if item is not None:
                v._scene.addItem(item)
                v._anno_items[int(self.entry["id"])] = item
        else:
            aid = int(self.entry["id"])
            item = v._anno_items.pop(aid, None)
            if item is not None:
                v._scene.removeItem(item)
            v.annotations = [a for a in getattr(v, "annotations", [])
                             if int(a.get("id", 0)) != aid]
        self._done()

    def undo(self):
        v = self._v
        if self.add:
            aid = int(self.entry["id"])
            item = v._anno_items.pop(aid, None)
            if item is not None:
                v._scene.removeItem(item)
            v.annotations = [a for a in getattr(v, "annotations", [])
                             if int(a.get("id", 0)) != aid]
        else:
            v.annotations.append(dict(self.entry))
            item = self._canvas()
            if item is not None:
                v._scene.addItem(item)
                v._anno_items[int(self.entry["id"])] = item
        self._done()

class AnnoEditCmd(_ViewCmd):
    """Props/geometry/text/z edit of one annotation (old <-> new state).
    `label` overrides the history text so a command with a name of its
    own (the z-order moves) doesn't show up as a generic edit."""

    def __init__(self, view, anno_id: int, old: dict, new: dict,
                 label: str = "Edit annotation", layout_only: bool = False):
        super().__init__(view, label)
        self.aid = anno_id
        self.old, self.new = dict(old), dict(new)
        # A drag/resize changes only drawn geometry, which no simulation
        # reads -- send it down the light path (see `_ViewCmd`).
        if layout_only:
            self.changed_signal = "layout_changed"
            self.needs_rule_check = False

    def _apply(self, state: dict):
        v = self._v
        for _i, a in enumerate(v.annotations):
            if int(a.get("id", 0)) == self.aid:
                a.clear(); a.update(state)
                break
        item = getattr(v, "_anno_items", {}).get(self.aid)
        if item is not None:
            item.props = {k: v for k, v in state.items()
                          if k in ANNO_DEFAULTS}
            item.apply_props()
            if hasattr(item, "setPlainText"):
                item.setPlainText(state.get("text",
                                            item.toPlainText()))
            if hasattr(item, "setRect"):
                r0 = item.rect()
                item.setRect(r0.left(), r0.top(),
                             float(state.get("w", r0.width())),
                             float(state.get("h", r0.height())))
                if getattr(item, "_handle", None) is not None:
                    item._handle.setPos(r0.left() +
                                        float(state.get("w", 120)),
                                        r0.top() +
                                        float(state.get("h", 70)))
            elif hasattr(item, "set_box"):
                # Text box: resize (and reflow) rather than setRect.
                item.set_box(float(state.get("w", item._box_w)),
                             float(state.get("h", item._box_h)))
            if hasattr(item, "setLine"):
                ln = item.line()
                item.prepareGeometryChange()
                item.setLine(float(state.get("x1", ln.x1())),
                            float(state.get("y1", ln.y1())),
                            float(state.get("x2", ln.x2())),
                            float(state.get("y2", ln.y2())))
                if getattr(item, "_tail_handle", None) is not None:
                    item._tail_handle.setPos(item.line().p1())
                if getattr(item, "_head_handle", None) is not None:
                    item._head_handle.setPos(item.line().p2())
            sp = item.scenePos()
            item.setPos(float(state.get("x", sp.x())),
                        float(state.get("y", sp.y())))
            item.setZValue(float(state.get("z", ANNO_Z_MIDDLE)))

    def redo(self):
        self._apply(self.new); self._done()

    def undo(self):
        self._apply(self.old); self._done()

#: annotation item types
_ANNO_TYPES = (_AnnoRect, _AnnoEllipse, _AnnoText, _AnnoLine)

#: kind -> canvas class, shared by every load/undo/paste construction
#: site.
_ANNO_CLASSES = {"rect": _AnnoRect, "ellipse": _AnnoEllipse,
                 "text": _AnnoText, "arrow": _AnnoLine}

def _anno_ctor_args(a: dict) -> list:
    """Positional geometry args for `_ANNO_CLASSES[a['kind']](id,
    parent, props, view, *_anno_ctor_args(a))`."""
    kind = a.get("kind")
    if kind in ("rect", "ellipse"):
        return [float(a.get("x", 0)), float(a.get("y", 0)),
                float(a.get("w", 120)), float(a.get("h", 70))]
    if kind == "arrow":
        return [float(a.get("x1", 0)), float(a.get("y1", 0)),
                float(a.get("x2", 100)), float(a.get("y2", 0))]
    return [float(a.get("x", 0)), float(a.get("y", 0)),
            a.get("text", ""),
            float(a.get("w", 120)), float(a.get("h", 40))]

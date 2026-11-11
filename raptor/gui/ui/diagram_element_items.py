"""Event and hierarchy items of the diagram canvas."""
from __future__ import annotations

from raptor.gui.ui.diagram_item_core import _phased_hatch, _snap_of, _snapped_x, _snapped_y
from collections.abc import Callable, Mapping
from typing import Any
from PySide6.QtCore import (
    Qt, QPointF,
)
from PySide6.QtGui import (
    QPen, QBrush, QColor, QFont, QPainter, QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsTextItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
)
from PySide6.QtGui import QPainterPath
from raptor.gui.resources.design_tokens import (
    APPENDIX_D_STATE_COLOURS,
    COLOUR,
)
from raptor.gui.ui.diagram_common import (  # noqa: I001
    NODE_R,
)
from raptor.gui.ui.contrast import readable_text_colour


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
        self._half_w: float = r  # horizontal half-width; `_refit` widens it
        self._phased = False
        self._link_cut: str | None = None  # None | "linked" | "cut" -- Raptor 7 overlay

        self.setPen(QPen(QColor(COLOUR["relationship"]), 2))
        self.setBrush(QBrush(QColor(COLOUR["surface"])))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
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
        self._live_colour: str | None = None

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
        self._live_colour: str | None = None

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

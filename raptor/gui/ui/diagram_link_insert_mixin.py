"""Hover feedback and target resolution for inserting an element on a link.

Expects on `self`: the scene, `_rbd` and the current tool mode."""
from __future__ import annotations


import math
from typing import TYPE_CHECKING, Any

from shiboken6 import isValid
from PySide6.QtCore import (
    Qt, QRectF, QPointF,
)
from PySide6.QtGui import (
    QPen, QBrush, QColor, QPainterPath,
)

from raptor.gui.resources.design_tokens import (
    COLOUR,
)
from raptor.gui.ui import settings as app_settings
from raptor.gui.ui.diagram_common import (
    MIN_ELEMENT_GAP,
)
from raptor.gui import link_insert
from raptor.gui.ui.diagram_items import flow_direction
from raptor.gui.ui.diagram_items import (
    _BlockItem,
    _EventItem,
    _HierItem,
    _LinkItem,
    _NodeItem,
    _view_status,
)


#: Everything arrow-key nudge knows how to move. Arrow annotations are
#: included even though they are not `ItemIsMovable` (their endpoints are
#: independent); the Alt+Up enablement/handler must use this same predicate
#: or an arrow-only selection silently ascends instead of nudging.

if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsView as _Base
else:
    _Base = object


class LinkInsertMixin(_Base):
    """Hover feedback and target resolution for inserting an element on a link."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _insert_halo: Any
        _insert_halos: Any
        _insert_hover_link: Any
        _insert_hover_state: Any
        _insert_hover_status: Any
        _page_flow_cache: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    # ------------------------------------------------------ insert on link
    #
    # The add tools can place an element *in series* on a connector. The
    # interaction is deliberately the same hover/click gesture as ordinary
    # canvas placement; only the target changes. The geometry rules live in
    # `raptor.gui.link_insert` (Qt-free) so they are unit-testable.
    def _insert_tool_info(self) -> tuple[str, str, str] | None:
        return self._INSERT_TOOL_KINDS.get(self.tool_mode)

    @staticmethod
    def _element_shape_rect(item) -> QRectF:
        """The item's true drawn shape box, without label/badge padding."""
        centre = _LinkItem._true_centre(item)
        hw, hh = _LinkItem._half_extents(item)
        return QRectF(centre.x() - hw, centre.y() - hh, hw * 2.0, hh * 2.0)

    @staticmethod
    def _span_of(rect: QRectF, axis: str) -> tuple[float, float]:
        if axis == "x":
            return (rect.left(), rect.right())
        return (rect.top(), rect.bottom())

    def _standard_insert_gap(self) -> float:
        """Scene-pixel gap used between the new element and its neighbours.

        `MIN_ELEMENT_GAP` is the canvas's standard minimum; the user's
        Auto Layout spacing scales it so insertion follows the same density
        preference as Auto Layout.
        """
        try:
            spacing = float(app_settings.get("layout/spacing", 1.0) or 1.0)
        except (TypeError, ValueError, OverflowError):
            spacing = 1.0
        spacing = max(0.25, min(spacing, 3.0))
        return MIN_ELEMENT_GAP * spacing * self._INSERT_GAP_SCALE

    def _element_body_at(self, scene_pos: QPointF):
        """The topmost block/node/event/hierarchy whose body contains the
        point, without `_element_at`'s generous 15px near-miss fallback."""
        for it in self._scene.items(scene_pos):
            node: Any = it
            while node is not None and node.parentItem() is not None:
                node = node.parentItem()
            if isinstance(node, (_BlockItem, _NodeItem, _EventItem,
                                 _HierItem)):
                return node
        return None

    def _page_flow(self) -> str:
        """The page flow used to choose an insertion axis.

        Explicit LR/RL/TB/BT (Auto Layout) wins. In auto mode the visible
        links vote, weighted by length. The result is cached against the
        layout generation so hovering does not walk every link on every
        mouse move.
        """
        configured = flow_direction()
        if configured in link_insert.FLOW_DIRECTIONS:
            return configured
        key = (self._layout_generation, len(self._links),
               self._current_parent)
        cached = self._page_flow_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        vectors: list[tuple[float, float]] = []
        for link in self._links:
            try:
                if not (isValid(link) and isValid(link._from)
                        and isValid(link._to)):
                    continue
                source = _LinkItem._true_centre(link._from)
                target = _LinkItem._true_centre(link._to)
            except RuntimeError:
                continue
            vectors.append((target.x() - source.x(),
                            target.y() - source.y()))
        inferred = link_insert.dominant_flow(vectors, configured)
        self._page_flow_cache = (key, inferred)
        return inferred

    def _link_flow_axis(self, link) -> link_insert.FlowAxis | None:
        try:
            if not (isValid(link) and isValid(link._from)
                    and isValid(link._to)):
                return None
            source = _LinkItem._true_centre(link._from)
            target = _LinkItem._true_centre(link._to)
        except RuntimeError:
            return None
        return link_insert.choose_flow_axis(
            target.x() - source.x(), target.y() - source.y(),
            self._page_flow())

    def _link_insert_refusal(self, link, info: tuple[str, str, str]
                             ) -> str | None:
        if self._rbd_ref is None:
            return "Open a model first"
        if getattr(link, "_link_num", None) is None:
            return "This connector has no model row"
        if link._from is link._to:
            return "A connector that loops to itself cannot be split"
        axis = self._link_flow_axis(link)
        if axis is None:
            return "This connector has no clear flow direction"
        if info[0] == "hier":
            from raptor.core.input_rules import MAX_HIERARCHY_DEPTH
            if self._current_depth() >= MAX_HIERARCHY_DEPTH:
                return (f"Hierarchies can nest at most "
                        f"{MAX_HIERARCHY_DEPTH} levels deep")
        return None

    def _link_candidate_distance(self, link, scene_pos: QPointF) -> float:
        point = link.nearest_point(scene_pos)
        return math.hypot(point.x() - scene_pos.x(),
                          point.y() - scene_pos.y())

    def _resolve_link_insert_target(self, scene_pos: QPointF
                                    ) -> tuple[Any | None, str, str]:
        """(target, "none"|"allowed"|"blocked", reason).

        The target is a link (insert in series) or, when the pointer is on
        a block, event or hierarchy, that element (add in parallel).
        Nodes are not targets: a click on one places as before.
        """
        info = self._insert_tool_info()
        if info is None or self._rbd_ref is None:
            return (None, "none", "")
        element = self._element_body_at(scene_pos)
        if element is not None:
            if isinstance(element, _NodeItem):
                return (None, "none", "")
            reason = self._parallel_refusal(element, info)
            if reason:
                return (element, "blocked", reason)
            return (element, "allowed", "")
        scale = abs(self.transform().m11()) or 1.0
        pick_radius = self._LINK_PICK_RADIUS_PX / scale
        margin = self._LINK_AMBIGUITY_PX / scale
        # The spatial query uses the link's own 10px stroke shape; widen
        # the rectangle so a link just outside the visible stroke but
        # inside the pick radius is still measured exactly.
        query = pick_radius + 12.0 / scale
        rect = QRectF(scene_pos.x() - query, scene_pos.y() - query,
                      query * 2.0, query * 2.0)
        candidates = [
            item for item in self._scene.items(
                rect, Qt.ItemSelectionMode.IntersectsItemShape)
            if isinstance(item, _LinkItem) and isValid(item)
            and getattr(item, "_link_num", None) is not None
        ]
        if not candidates:
            return (None, "none", "")
        distances = [self._link_candidate_distance(item, scene_pos)
                     for item in candidates]
        pick = link_insert.pick_hover(distances, pick_radius, margin)
        if pick.index is None:
            return (None, "none", "")
        link = candidates[pick.index]
        if pick.state == "blocked":
            return (link, "blocked", pick.reason)
        reason = self._link_insert_refusal(link, info)
        if reason:
            return (link, "blocked", reason)
        return (link, "allowed", "")

    def _endpoint_text(self, item) -> str:
        if isinstance(item, _BlockItem):
            return self._endpoint_name("Block", item._block_id)
        if isinstance(item, _NodeItem):
            return self._endpoint_name("Node", item._node_id)
        if isinstance(item, _EventItem):
            return self._endpoint_name("Event", item._event_id)
        return self._endpoint_name("Hier", item._hier_id)

    def _link_endpoint_label(self, link) -> str:
        return f"{self._endpoint_text(link._from)} → {self._endpoint_text(link._to)}"

    def _insert_message(self, target, state: str, reason: str) -> str:
        """The status-bar text for an Add-tool hover or refused click."""
        info = self._insert_tool_info()
        noun = info[2] if info is not None else "Element"
        if isinstance(target, _LinkItem):
            if state == "blocked":
                return f"Cannot insert {noun}: {reason}"
            return (f"Insert {noun} on {self._link_endpoint_label(target)}"
                    f" — click to place")
        name = self._endpoint_text(target)
        if state == "blocked":
            return f"Cannot add {noun} in parallel with {name}: {reason}"
        split, join, _siblings = self._parallel_preview(target)
        if split is not None and join is not None:
            k = self.nodes[join._node_id].good_paths
            n = 1 + sum(1 for link in self._links
                        if isValid(link) and link._to is join)
            what = (f"as branch {n} between {self._endpoint_text(split)} "
                    f"and {self._endpoint_text(join)} "
                    f"({self._endpoint_text(join)} becomes {k}/{n})")
        elif split is None and join is None:
            what = "with a new split node and a new join node"
        elif split is None:
            what = (f"with a new split node, joining at "
                    f"{self._endpoint_text(join)}")
        else:
            what = (f"from {self._endpoint_text(split)}, with a new join "
                    f"node")
        return f"Add {noun} in parallel with {name} {what} — click to place"

    def _parallel_preview(self, target) -> tuple[Any, Any, list]:
        """What adding in parallel with `target` will reuse: (split node
        item or None, join node item or None, the other elements between
        them). None means that node will be created; the siblings are only
        listed when both nodes are reused, since only then does the new
        element join an existing group."""
        into, out_of = self._item_links(target)
        if len(into) != 1 or len(out_of) != 1:
            return (None, None, [])
        split = into[0]._from if self._is_regular_node(into[0]._from) \
            else None
        join = out_of[0]._to if self._is_regular_node(out_of[0]._to) \
            else None
        siblings: list = []
        if split is not None and join is not None:
            members, edges = self._layout_graph()
            split_key = ("node", split._node_id)
            join_key = ("node", join._node_id)
            target_key = self._item_key(target)
            siblings = [members[key] for key in
                        self._between(split_key, join_key, edges)
                        if key not in (split_key, join_key, target_key)
                        and key in members]
        return (split, join, siblings)

    def _set_insert_hover(self, target, state: str, reason: str = "") -> None:
        if target is self._insert_hover_link \
                and state == self._insert_hover_state:
            return          # nothing changed: no repaint, no re-walk
        self._clear_link_insert_hover()
        self._insert_hover_link = target
        self._insert_hover_state = state
        if target is None:
            return
        if isinstance(target, _LinkItem):
            target.set_insert_highlight(state)
        else:
            self._show_element_insert_halo(target, state)
            if state == "allowed":
                split, join, siblings = self._parallel_preview(target)
                for other in (split, join, *siblings):
                    if other is not None:
                        self._show_element_insert_halo(
                            other, state, strong=False)
        message = self._insert_message(target, state, reason)
        if message != self._insert_hover_status:
            self._insert_hover_status = message
            _view_status(self, message)

    def _show_element_insert_halo(self, item, state: str, *,
                                  strong: bool = True) -> None:
        """A translucent outline round an element, the element counterpart
        of a link's insert halo (accent = allowed, failure colour =
        refused). The target gets a strong one; the nodes and branches the
        new element will join get a lighter one. Overlay items owned by
        the view, so the element classes keep their own painting."""
        from PySide6.QtWidgets import QGraphicsPathItem
        halo = QGraphicsPathItem()
        halo.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        halo.setAcceptHoverEvents(False)
        halo.setOpacity(0.32 if strong else 0.18)
        pad = 5.0 if strong else 4.0
        rect = self._element_shape_rect(item).adjusted(-pad, -pad, pad, pad)
        path = QPainterPath()
        if isinstance(item, (_NodeItem, _HierItem)):
            path.addEllipse(rect)
        else:
            path.addRoundedRect(rect, 8.0, 8.0)
        colour = COLOUR["accent"] if state == "allowed" else COLOUR["failed"]
        pen = QPen(QColor(colour), 8.0 if strong else 6.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        halo.setPen(pen)
        halo.setBrush(QBrush())
        halo.setPath(path)
        halo.setZValue(item.zValue() - 0.5)
        self._scene.addItem(halo)
        self._insert_halos.append(halo)
        if strong:
            self._insert_halo = halo

    def _clear_link_insert_hover(self) -> None:
        target = self._insert_hover_link
        self._insert_hover_link = None
        self._insert_hover_state = None
        self._insert_hover_status = ""
        if isinstance(target, _LinkItem):
            try:
                if isValid(target):
                    target.set_insert_highlight(None)
            except RuntimeError:
                pass
        self._insert_halo = None
        halos, self._insert_halos = self._insert_halos, []
        for halo in halos:
            try:
                if isValid(halo) and halo.scene() is not None:
                    halo.scene().removeItem(halo)
            except RuntimeError:
                pass

    def _update_link_insert_hover(self, event) -> None:
        if self._insert_tool_info() is None or self._rbd_ref is None:
            self._clear_link_insert_hover()
            return
        if event.buttons():
            self._clear_link_insert_hover()
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        link, state, reason = self._resolve_link_insert_target(scene_pos)
        if link is None:
            self._clear_link_insert_hover()
            return
        self._set_insert_hover(link, state, reason)

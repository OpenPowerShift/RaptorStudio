"""Colour by tag: a thin split stripe above each tagged element.

A tag is model metadata, not a result, so it is not one of the Results
ribbon's single-slot fills: the stripe sits above the element and shows
beside any result overlay. An element with several tags gets one stripe
segment per tag, up to three; a fourth and later tag widen the tooltip,
not the stripe ("+2 more"). A hierarchy oval shows its own tags only;
what is tagged inside it shows when you open it.

View state only: no model change, no undo command.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem

from raptor.core import tags as tag_model

#: The most stripe segments one element draws.
MAX_STRIPES = 3
STRIPE_H = 7.0
_GAP = 3.0
_KEY = "_tag_stripe"


def _clear(item: QGraphicsItem) -> None:
    old = getattr(item, _KEY, None)
    if old is None:
        return
    old.setParentItem(None)
    scene = old.scene()
    if scene is not None:
        scene.removeItem(old)
    setattr(item, _KEY, None)


def _frame(item: QGraphicsItem) -> QRectF:
    rect = getattr(item, "rect", None)
    if callable(rect):
        return QRectF(rect())
    polygon = getattr(item, "polygon", None)
    if callable(polygon):
        return polygon().boundingRect()
    return item.boundingRect()


def _stripe(item: QGraphicsItem, tags: list[str],
            colours: dict[str, str]) -> QGraphicsRectItem:
    frame = _frame(item)
    width = max(18.0, min(frame.width(), 80.0))
    left = frame.center().x() - width / 2
    bar = QGraphicsRectItem(0, 0, width, STRIPE_H, item)
    bar.setPos(left, frame.top() - STRIPE_H - _GAP)
    bar.setPen(QPen(Qt.PenStyle.NoPen))
    bar.setBrush(QBrush(Qt.GlobalColor.transparent))
    shown = tags[:MAX_STRIPES]
    step = width / len(shown)
    for index, tag in enumerate(shown):
        seg = QGraphicsRectItem(index * step, 0, step, STRIPE_H, bar)
        seg.setPen(QPen(QColor("#00000040"), 0.5))
        seg.setBrush(QBrush(QColor(colours.get(tag) or "#6B7280")))
    more = len(tags) - len(shown)
    tip = "Tags: " + ", ".join(tags[:12])
    if len(tags) > 12:
        tip += f" (+{len(tags) - 12} more)"
    elif more:
        tip += f" ({more} more than the stripe shows)"
    bar.setToolTip(tip)
    for child in bar.childItems():
        child.setToolTip(tip)
    bar.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    bar.setData(0, "tag-stripe")
    return bar


def apply_stripes(view: Any) -> int:
    """Draw (or clear) the stripes on the current level; the count drawn."""
    on = bool(getattr(view, "_tag_colours_on", False))
    rbd = getattr(view, "_rbd_ref", None)
    colours: dict[str, str] = {}
    drawn = 0
    for kind, items in (("block", getattr(view, "_block_items", {})),
                        ("node", getattr(view, "_node_items", {})),
                        ("event", getattr(view, "_event_items", {})),
                        ("hier", getattr(view, "_hier_items", {}))):
        for oid, item in items.items():
            try:
                _clear(item)
                if not on or rbd is None:
                    continue
                tags = tag_model.tags_for(rbd, f"{kind}:{oid}")
                if not tags:
                    continue
                for tag in tags:
                    if tag not in colours:
                        colours[tag] = tag_model.tag_colour(rbd, tag)
                setattr(item, _KEY, _stripe(item, tags, colours))
                drawn += 1
            except RuntimeError:          # an item deleted under us
                continue
    return drawn


def legend(rbd: Any, view: Any) -> list[tuple[str, str, int]]:
    """(tag, colour, elements on this level) for the tags shown here,
    most-used first."""
    if rbd is None:
        return []
    counts: dict[str, int] = {}
    for kind, items in (("block", getattr(view, "_block_items", {})),
                        ("node", getattr(view, "_node_items", {})),
                        ("event", getattr(view, "_event_items", {})),
                        ("hier", getattr(view, "_hier_items", {}))):
        for oid in items:
            for tag in tag_model.tags_for(rbd, f"{kind}:{oid}"):
                counts[tag] = counts.get(tag, 0) + 1
    return [(tag, tag_model.tag_colour(rbd, tag), count)
            for tag, count in sorted(counts.items(),
                                     key=lambda kv: (-kv[1], kv[0].casefold()))]

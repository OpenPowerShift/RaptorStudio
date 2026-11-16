"""How an element taken out of service looks on the canvas (U27).

A cut element (down for the whole run) is dimmed inside a red dashed
outline tagged CUT, and its links are dashed: its path is broken. A linked
element (never fails) gets a teal dashed outline tagged LINKED. Both carry
Raptor 7's Failure Effects View diamond (black for cut, white for linked)
in the tag, the same language that view uses. Anything inside a cut or
linked hierarchy is marked too, when the hierarchy is opened.

The mark is a child item, so the element's own paint and size are
untouched and a live run's colours still show through.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPathItem,
                               QGraphicsPolygonItem, QGraphicsSimpleTextItem)

from raptor.core.fileio import out_of_service as oos

COLOURS = {"cut": "#c62828", "linked": "#00897b"}
TIPS = {"cut": "Out of service: CUT — down for the whole run, its path is broken. "
               "Right-click ▸ Out of Service to put it back.",
        "linked": "Out of service: LINKED — never fails, as good as a wire. "
                  "Right-click ▸ Out of Service to put it back."}
_DIM = 0.45


def _mark(item: QGraphicsItem, state: str | None) -> None:
    old = getattr(item, "_oos_mark", None)
    if old is not None:
        scene = old.scene()
        if scene is not None:
            scene.removeItem(old)
        old.setParentItem(None)
        item._oos_mark = None          # type: ignore[attr-defined]
    item.setOpacity(_DIM if state == "cut" else 1.0)
    tip = item.toolTip()
    for text in TIPS.values():
        tip = tip.replace("\n\n" + text, "").replace(text, "")
    if state is None:
        item.setToolTip(tip)
        return
    colour = QColor(COLOURS[state])
    rect = item.boundingRect().adjusted(-5, -5, 5, 5)
    path = QPainterPath()
    path.addRoundedRect(rect, 6, 6)
    outline = QGraphicsPathItem(path, item)
    pen = QPen(colour, 2.0, Qt.PenStyle.DashLine)
    pen.setCosmetic(True)
    outline.setPen(pen)
    outline.setBrush(Qt.BrushStyle.NoBrush)
    outline.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresParentOpacity)
    outline.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    outline.setZValue(5)
    label = QGraphicsSimpleTextItem(state.upper(), outline)
    font = label.font()
    font.setPointSizeF(7.5)
    font.setBold(True)
    label.setFont(font)
    label.setBrush(QBrush(colour))
    size = label.boundingRect()
    label.setPos(rect.right() - size.width(), rect.top() - size.height() - 1)
    diamond = QGraphicsPolygonItem(QPolygonF([
        QPointF(0, -4), QPointF(4, 0), QPointF(0, 4), QPointF(-4, 0)]), outline)
    diamond.setPen(QPen(colour, 1))
    diamond.setBrush(QBrush(Qt.GlobalColor.black if state == "cut" else Qt.GlobalColor.white))
    diamond.setPos(rect.right() - size.width() - 7, rect.top() - size.height() / 2 - 1)
    item._oos_mark = outline           # type: ignore[attr-defined]
    item.setToolTip((tip + "\n\n" if tip else "") + TIPS[state])


def apply_marks(view: Any) -> None:
    """Mark every out-of-service element on the level `view` shows, and
    dash the links of the cut ones."""
    rbd = getattr(view, "_rbd_ref", None)
    if rbd is None:
        return
    blocks, events = oos.effective(rbd)
    hiers = oos.hierarchies(rbd)
    cut_items = set()
    for items, states in ((getattr(view, "_block_items", {}), blocks),
                          (getattr(view, "_event_items", {}), events),
                          (getattr(view, "_hier_items", {}), hiers)):
        for oid, item in items.items():
            state = states.get(oid)
            if state != getattr(item, "_oos_state", None):
                _mark(item, state)
                item._oos_state = state    # set even when None, so a re-mark is cheap
            if state == "cut":
                cut_items.add(id(item))
    for link in getattr(view, "_links", []):
        dashed = id(getattr(link, "_from", None)) in cut_items or \
            id(getattr(link, "_to", None)) in cut_items
        if getattr(link, "_oos_cut", False) != dashed:
            link._oos_cut = dashed
            link.update()


def bounding(item: QGraphicsItem) -> QRectF:
    """The element plus its mark, for tests and fitting."""
    mark = getattr(item, "_oos_mark", None)
    return item.sceneBoundingRect() if mark is None else \
        item.sceneBoundingRect().united(mark.sceneBoundingRect())


STATE_LABELS = (("In Service", None), ("Cut — down for the whole run", "cut"),
                ("Linked — never fails", "linked"))


def set_states(view: Any, targets: list[tuple[str, int]], state: str | None) -> bool:
    """Put `targets` [(kind, id)] in `state` as ONE undo step. Returns
    whether anything changed."""
    from raptor.gui.ui.diagram_commands import OutOfServiceCmd
    rbd = getattr(view, "_rbd_ref", None)
    targets = [(k, o) for k, o in targets if k in oos.KINDS]
    if rbd is None or not targets:
        return False
    before = dict(oos.states(rbd))
    probe = type("_P", (), {"out_of_service": dict(before)})()
    for kind, oid in targets:
        oos.set_state(probe, kind, oid, state)
    after = probe.out_of_service
    if after == before:
        return False
    word = {"cut": "Cut", "linked": "Link", None: "Restore"}[state]
    view._stack.push(OutOfServiceCmd(view, before, after,
                                     f"{word} {len(targets)} element(s) "
                                     + ("into service" if state is None else "out of service")))
    return True


def add_submenu(view: Any, menu: Any, targets: list[tuple[str, int]]) -> None:
    """"Out of Service ▸" with the three states, the current one ticked
    (none ticked when the targets differ)."""
    targets = [(k, o) for k, o in targets if k in oos.KINDS]
    if not targets:
        return
    rbd = getattr(view, "_rbd_ref", None)
    current = {oos.states(rbd).get(oos.key(k, o)) for k, o in targets} if rbd else {None}
    sub = menu.addMenu("Out of Service")
    sub.setToolTipsVisible(True)
    for label, state in STATE_LABELS:
        act = sub.addAction(label)
        act.setCheckable(True)
        act.setChecked(current == {state})
        act.setToolTip("Keeps the element's data; only runs and studies see the "
                       "change, and every result says so.")
        act.triggered.connect(lambda _c=False, s=state: set_states(view, targets, s))


def selected_targets(view: Any) -> list[tuple[str, int]]:
    out = []
    for kind, items in (("block", getattr(view, "_block_items", {})),
                        ("event", getattr(view, "_event_items", {})),
                        ("hier", getattr(view, "_hier_items", {}))):
        out += [(kind, oid) for oid, item in items.items() if item.isSelected()]
    return out

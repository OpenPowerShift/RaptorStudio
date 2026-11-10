"""Annotations on the canvas: creation, anchors, text, ordering and style defaults.

Expects on `self`: the view's scene, `_rbd`, `_undo`."""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req

from typing import Any

from shiboken6 import isValid
from PySide6.QtCore import (
    Qt, QPointF,
)
from PySide6.QtGui import (
    QPen, QColor,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
)

from raptor.core.annotations import (
    ANNO_DEFAULT_LOCATION,
    ANNO_Z_DEFAULT,
    annotation_geometry,
    annotation_label,
)
from raptor.gui.resources.design_tokens import (
    COLOUR,
)
from raptor.gui.ui.diagram_items import (
    _BlockItem,
    _EventItem,
    _HierItem,
    _LinkItem,
    _NodeItem,
    _view_status,
)
from raptor.gui.ui.diagram_annotations import (
    ANNO_APPLICABLE_PROPS,
    ANNO_DEFAULTS,
    ANNO_KIND_NOUNS,
    ANNO_ORDER_COMMANDS,
    ANNO_Z_BANDS,
    ANNO_Z_BEHIND,
    ANNO_Z_FRONT,
    AnnoCmd,
    AnnoEditCmd,
    apply_anno_state,
    _ANNO_TYPES,
    _anno_base_props,
)


#: Everything arrow-key nudge knows how to move. Arrow annotations are
#: included even though they are not `ItemIsMovable` (their endpoints are
#: independent); the Alt+Up enablement/handler must use this same predicate
#: or an arrow-only selection silently ascends instead of nudging.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsView as _Base
else:
    _Base = object


class AnnotationMixin(_Base):
    """Annotations on the canvas: creation, anchors, text, ordering and style defaults."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _anchor_leader: Any
        _anchor_pick_end: Any
        _anchor_pick_for: Any
        _arrow_ghost: Any
        _hidden_following: Any
        _rbd_ref: Any
        _shape_anno_ghost: Any
        annotations: Any
        default_annotation_props: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    # -------------------------------------------------- annotations --
    def _next_anno_id(self) -> int:
        used = {int(a.get("id", 0)) for a in
                getattr(self, "annotations", [])}
        used |= set(self._anno_items)
        # Routing points share the id space. Without this every point added
        # in a row got the same id, so only the first was saved to the file
        # and removing one removed them all.
        used |= {int(g.get("id", 0))
                 for g in getattr(self._rbd_ref, "link_grips", None) or []}
        i = getattr(self, "_anno_seq", 1)
        while i in used:
            i += 1
        return i

    def _begin_annotation(self, kind: str, scene_pos: QPointF):
        """Create a new text annotation (single click: it grows with its
        own content, unlike rect/ellipse's two-click drag placement --
        see _begin_annotation_shape). The AnnoCmd's redo builds both the
        canvas item and the model entry (so undo/redo are exact)."""
        if getattr(self, "_rbd_ref", None) is None:
            return None
        aid = self._next_anno_id()
        w0, h0 = 120, 40
        x0, y0 = round(scene_pos.x(), 2), round(scene_pos.y(), 2)
        entry = {
            "kind": kind, "id": aid,
            "parent": self._current_parent,
            "x": x0, "y": y0,
            "w": w0, "h": h0, "text": "",
            "z": self.new_annotation_z(),
            **self._anno_default_props(kind)}
        self._stack.push(AnnoCmd(self, entry, add=True))
        item = self._anno_items.get(aid)
        if kind == "text" and item is not None:
            item.enter_edit()
        return item

    def _begin_annotation_arrow(self, tail: QPointF, head: QPointF):
        """Second click of the two-click arrow placement: commit an
        "arrow" annotation running tail->head."""
        if getattr(self, "_rbd_ref", None) is None:
            return None
        aid = self._next_anno_id()
        entry = {
            "kind": "arrow", "id": aid,
            "parent": self._current_parent,
            "x": 0, "y": 0,
            "x1": round(tail.x(), 2), "y1": round(tail.y(), 2),
            "x2": round(head.x(), 2), "y2": round(head.y(), 2),
            "text": "",
            "z": self.new_annotation_z(),
            **self._anno_default_props("arrow")}
        self._stack.push(AnnoCmd(self, entry, add=True))
        return self._anno_items.get(aid)

    def _drop_arrow_ghost(self):
        if self._arrow_ghost is not None:
            self._scene.removeItem(self._arrow_ghost)
            self._arrow_ghost = None

    def _begin_annotation_shape(self, kind: str, p1: QPointF, p2: QPointF):
        """Second click of the two-click rect/ellipse placement (mirrors
        _begin_annotation_arrow): commit a shape spanning corners
        `p1`/`p2`. A same-spot double-click (zero drag) still produces a
        usable box, clamped to the same 12x12 minimum the resize handle
        itself enforces."""
        if getattr(self, "_rbd_ref", None) is None:
            return None
        aid = self._next_anno_id()
        x0 = min(p1.x(), p2.x())
        y0 = min(p1.y(), p2.y())
        w0 = max(12.0, abs(p2.x() - p1.x()))
        h0 = max(12.0, abs(p2.y() - p1.y()))
        entry = {
            "kind": kind, "id": aid,
            "parent": self._current_parent,
            "x": round(x0, 2), "y": round(y0, 2),
            "w": round(w0, 2), "h": round(h0, 2), "text": "",
            "z": self.new_annotation_z(),
            **self._anno_default_props(kind)}
        self._stack.push(AnnoCmd(self, entry, add=True))
        return self._anno_items.get(aid)

    def _drop_shape_ghost(self):
        if self._shape_anno_ghost is not None:
            self._scene.removeItem(self._shape_anno_ghost)
            self._shape_anno_ghost = None

    def sync_annotation_geometry(self, item):
        """Item moved/resized: mirror into the model annotations."""
        aid = getattr(item, "_anno_id", None)
        if aid is None:
            return
        for a in getattr(self, "annotations", []):
            if int(a.get("id", 0)) == aid:
                sp = item.scenePos()
                a["x"] = round(sp.x(), 2); a["y"] = round(sp.y(), 2)
                if hasattr(item, "rect"):
                    r = item.rect()
                    a["w"] = round(r.width(), 2)
                    a["h"] = round(r.height(), 2)
                if hasattr(item, "line"):
                    ln = item.line()
                    a["x1"] = round(ln.x1(), 2); a["y1"] = round(ln.y1(), 2)
                    a["x2"] = round(ln.x2(), 2); a["y2"] = round(ln.y2(), 2)
                break
        self.layout_changed.emit()

    def _anchor_tables(self) -> dict[str, dict[int, Any]]:
        return {"block": self._block_items, "node": self._node_items,
                "event": self._event_items, "hier": getattr(self, "_hier_items", {})}

    def _anchor_element_name(self, kind: str, oid: int) -> str:
        if kind == "annotation":
            entry = self.annotation_entry(int(oid))
            return annotation_label(entry) if entry is not None else ""
        tables: dict[str, dict[int, Any]] = {
            "block": self.blocks, "node": self.nodes, "event": self.events,
            "hier": self.hiers}
        obj = tables[kind].get(oid)
        if obj is None:
            return ""
        return (getattr(obj, "node_name", "") if kind == "node" else getattr(obj, "name", "")) or ""

    def _anchor_item(self, ref):
        """The canvas item an anchor names on this level (`Block:3`,
        `Annotation:7`, or a name/label that is unique here), else None."""
        if ref in (None, ""):
            return None
        text = str(ref)
        prefix, _, ident = text.partition(":")
        if prefix == "Annotation" and ident.strip().isdigit():
            return getattr(self, "_anno_items", {}).get(int(ident))
        tables = self._anchor_tables()
        if prefix in self._REF_KIND and ident.strip().isdigit():
            return tables[self._REF_KIND[prefix]].get(int(ident))
        found = [item for kind, table in tables.items() for oid, item in table.items()
                 if self._anchor_element_name(kind, oid) == text]
        if found:
            return found[0] if len(found) == 1 else None
        notes = [item for aid, item in getattr(self, "_anno_items", {}).items()
                 if annotation_label(self.annotation_entry(aid) or {}) == text]
        return notes[0] if len(notes) == 1 else None

    def _anchor_geometry(self, ref) -> dict | None:
        item = self._anchor_item(ref)
        if item is None:
            return None
        if isinstance(item, tuple(_ANNO_TYPES)):
            entry = self.annotation_entry(int(getattr(item, "_anno_id", 0)))
            return annotation_geometry(entry) if entry is not None else None
        centre = _LinkItem._true_centre(item)
        if hasattr(item, "_half_w"):           # an event's hexagon
            hw, hh = float(item._half_w), float(item._r)
        elif hasattr(item, "rect"):
            r = item.rect()
            hw, hh = r.width() / 2.0, r.height() / 2.0
        else:
            return None
        return {"cx": centre.x(), "cy": centre.y(), "half_w": hw, "half_h": hh,
                "shape": ("ellipse" if isinstance(item, (_NodeItem, _EventItem))
                          else "rect")}

    def _anchored_geometry(self, entry: dict) -> dict | None:
        """The geometry that puts `entry` next to its anchor again, or None
        when it has no anchor or the target is not on this level."""
        anchor = entry.get("anchor")
        if not isinstance(anchor, dict):
            return None
        from raptor.api.annotations import _place_at_location, anchor_offset
        if entry.get("kind") == "arrow":
            # Each end may be attached or free; an attached end stops at
            # the target's outline, not its centre.
            from raptor.core.annotations import arrow_points
            tail = head = None
            has_tail = anchor.get("from_ref") or anchor.get("from")
            has_head = anchor.get("to_ref") or anchor.get("to")
            if has_tail:
                tail = self._anchor_geometry(has_tail)
            if has_head:
                head = self._anchor_geometry(has_head)
            if (has_tail and tail is None) or (has_head and head is None):
                return None             # a target is not on this level
            if tail is None and head is None:
                return None
            return arrow_points(entry, tail, head)
        target = self._anchor_geometry(anchor.get("ref") or anchor.get("target"))
        if target is None:
            return None
        px, py = _place_at_location(target,
                                    str(anchor.get("location", ANNO_DEFAULT_LOCATION)),
                                    float(anchor.get("gap", 12.0)),
                                    float(entry.get("w") or 0),
                                    float(entry.get("h") or 0))
        ox, oy = anchor_offset(anchor)
        return {"x": round(px + ox, 2), "y": round(py + oy, 2)}

    def follow_anchors(self, moved: set | None = None) -> int:
        """Put the annotations anchored to the `moved` targets ((kind, id)
        pairs; every anchored one when None) back next to them. Called from
        inside the move commands, so it makes no command of its own.

        An annotation can be anchored to another annotation, so this runs
        in passes: a note behind a note behind a block reaches its final
        place however the annotations happen to be ordered. The pass count
        is bounded, which also stops a cycle from spinning.
        """
        refs: set[str] | None = None
        if moved is not None:
            refs = set()
            for kind, oid in moved:
                refs.add(f"{self._KIND_REF.get(kind, kind)}:{oid}")
                name = self._anchor_element_name(kind, oid)
                if name:
                    refs.add(name)
        entries = [e for e in list(getattr(self, "annotations", []))
                   if isinstance(e.get("anchor"), dict)
                   and int(e.get("parent", 0) or 0) == self._current_parent]
        changed_ids: set[int] = set()
        for _pass in range(len(entries) + 1):
            changed = 0
            for entry in entries:
                anchor = entry["anchor"]
                if refs is not None and _pass == 0 and not refs & {
                        anchor.get(k) for k in
                        ("ref", "target", "from_ref", "to_ref", "from", "to")}:
                    continue
                geometry = self._anchored_geometry(entry)
                if geometry is None or all(entry.get(k) == v for k, v in geometry.items()):
                    continue
                apply_anno_state(self, int(entry.get("id", 0)), {**entry, **geometry})
                changed += 1
                changed_ids.add(int(entry.get("id", 0)))
            if not changed:
                break
        self._refresh_anchor_leader()
        return len(changed_ids)

    def _hide_following_annotations(self, moved: set[tuple[str, int]]) -> None:
        refs: set[str] = set()
        for kind, oid in moved:
            refs.add(f"{self._KIND_REF.get(kind, kind)}:{oid}")
            name = self._anchor_element_name(kind, oid)
            if name:
                refs.add(name)
        entries = [e for e in list(getattr(self, "annotations", []))
                   if int(e.get("parent", 0) or 0) == self._current_parent]
        hidden: list[int] = []
        changed = True
        while changed:
            changed = False
            for entry in entries:
                aid = int(entry.get("id", 0) or 0)
                if aid in hidden:
                    continue
                anchor = entry.get("anchor")
                if not isinstance(anchor, dict):
                    continue
                if not refs & {anchor.get(k) for k in
                               ("ref", "target", "from_ref", "to_ref",
                                "from", "to")}:
                    continue
                item = self._anno_items.get(aid)
                if item is not None:
                    item.setVisible(False)
                hidden.append(aid)
                # A note anchored to this one follows it too, so hide it
                # in the next pass even though its target did not move
                # directly.
                refs.add(f"Annotation:{aid}")
                refs.add(annotation_label(entry))
                changed = True
        self._hidden_following = hidden
        self._refresh_anchor_leader()

    def _show_following_annotations(self) -> None:
        for aid in getattr(self, "_hidden_following", []) or []:
            item = self._anno_items.get(aid)
            if item is not None:
                item.setVisible(True)
        self._hidden_following = []
        self._refresh_anchor_leader()

    def anchor_targets(self, exclude: int | None = None) -> list[tuple[str, str]]:
        """(label, `Kind:id`) for everything on this level an annotation can
        be anchored to, for the Inspector. `exclude` is the annotation being
        anchored: it cannot anchor to itself."""
        out = []
        for kind, table in self._anchor_tables().items():
            for oid in table:
                name = self._anchor_element_name(kind, oid) or f"#{oid}"
                out.append((f"{self._KIND_REF[kind]}: {name}", f"{self._KIND_REF[kind]}:{oid}"))
        for aid in getattr(self, "_anno_items", {}):
            if exclude is not None and int(aid) == int(exclude):
                continue
            entry = self.annotation_entry(int(aid))
            if entry is None:
                continue
            out.append((f"Annotation: {annotation_label(entry)}",
                        f"Annotation:{aid}"))
        return sorted(out, key=lambda t: t[0].lower())

    def annotation_entry(self, aid: int) -> dict | None:
        return next((dict(a) for a in getattr(self, "annotations", [])
                     if int(a.get("id", 0)) == aid), None)

    def start_anchor_pick(self, aid: int, end: str | None = None) -> None:
        """Arm pick-anchor mode for annotation `aid`: the next canvas click
        chooses the target instead of selecting or moving anything. For an
        arrow `end` says which end ("from" or "to") the click attaches."""
        if int(aid) not in self._anno_items:
            return
        self._anchor_pick_end = end if end in ("from", "to") else None
        self._anchor_pick_for = int(aid)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        _view_status(self, "Click an element or another annotation to "
                     "anchor to it (Esc to cancel)")

    def cancel_anchor_pick(self, message: str | None = None) -> None:
        if self._anchor_pick_for is None:
            return
        self._anchor_pick_for = None
        self._anchor_pick_end = None
        self._apply_tool_cursor()
        if message:
            _view_status(self, message)

    def _anchor_pick_target(self, scene_pos: QPointF):
        """Topmost annotation at `scene_pos`, else the topmost element (with
        the same near-miss tolerance Connect uses), else the topmost
        annotation whose box contains the point.

        The last fallback matters because an annotation's own `shape()` is
        only its border band (so clicks pass through a transparent note).
        Anchoring to a note means its whole box, otherwise clicking the
        middle of an ellipse could never pick it."""
        for it in self._scene.items(scene_pos):
            if isinstance(it, tuple(_ANNO_TYPES)):
                return it
        element = self._element_at(scene_pos)
        if element is not None:
            return element
        best = None
        for it in getattr(self, "_anno_items", {}).values():
            if it.sceneBoundingRect().contains(scene_pos) and (
                    best is None or it.zValue() >= best.zValue()):
                best = it
        return best

    def _anchor_for_item(self, item) -> dict | None:
        """Anchor object naming `item`, sans location/gap (the caller keeps
        the annotation's current ones)."""
        if isinstance(item, tuple(_ANNO_TYPES)):
            aid = int(getattr(item, "_anno_id", 0))
            entry = self.annotation_entry(aid)
            if entry is None:
                return None
            return {"target": annotation_label(entry),
                    "ref": f"Annotation:{aid}"}
        kind = ("block" if isinstance(item, _BlockItem)
                else "node" if isinstance(item, _NodeItem)
                else "event" if isinstance(item, _EventItem)
                else "hier" if isinstance(item, _HierItem) else None)
        if kind is None:
            return None
        oid = int(item._block_id if kind == "block"
                  else item._node_id if kind == "node"
                  else item._event_id if kind == "event"
                  else item._hier_id)
        return {"target": self._anchor_element_name(kind, oid),
                "ref": f"{self._KIND_REF[kind]}:{oid}"}

    def _handle_anchor_pick(self, scene_pos: QPointF) -> None:
        aid = self._anchor_pick_for
        if aid is None:
            return
        item = self._anchor_pick_target(scene_pos)
        anchor = self._anchor_for_item(item) if item is not None else None
        if anchor is None:
            _view_status(self, "Nothing to anchor to there — click an "
                         "element or another annotation (Esc to cancel)")
            return
        if isinstance(item, tuple(_ANNO_TYPES)) \
                and int(getattr(item, "_anno_id", 0)) == aid:
            _view_status(self, "An annotation cannot be anchored to itself "
                         "— click another element (Esc to cancel)")
            return
        end = self._anchor_pick_end
        self.cancel_anchor_pick()
        entry = self.annotation_entry(aid) or {}
        stored = entry.get("anchor")
        old: dict = stored if isinstance(stored, dict) else {}
        if end is not None:
            # An arrow end: keep the other end as it is.
            merged = {k: v for k, v in old.items()
                      if k in ("from", "from_ref", "to", "to_ref")}
            merged[end] = anchor["target"]
            merged[f"{end}_ref"] = anchor["ref"]
            self.set_annotation_props(aid, {"anchor": merged})
            anchor = {"target": anchor["target"]}
        else:
            anchor["location"] = str(old.get("location", ANNO_DEFAULT_LOCATION))
            anchor["gap"] = float(old.get("gap", 12.0))
            self.set_annotation_props(aid, {"anchor": anchor})
        note = self._anno_items.get(aid)
        if note is not None:
            self._scene.clearSelection()
            note.setSelected(True)
        _view_status(self, f"Anchored to {anchor['target']}")

    def _refresh_anchor_leader(self) -> None:
        """While one anchored box annotation is selected, a dashed line from
        it to its element shows what it is attached to."""
        old = getattr(self, "_anchor_leader", None)
        self._anchor_leader = None
        if old is not None:
            try:
                if old.scene() is not None:
                    self._scene.removeItem(old)
            except RuntimeError:
                pass            # deleted with the scene
        try:
            selected = self._scene.selectedItems()
        except RuntimeError:
            return
        if len(selected) != 1 or not isinstance(selected[0], tuple(_ANNO_TYPES)):
            return
        item: Any = selected[0]
        if not item.isVisible():
            return          # hidden for a drag; the leader would lag it
        entry = self.annotation_entry(int(getattr(item, "_anno_id", 0)))
        anchor = (entry or {}).get("anchor")
        if not isinstance(anchor, dict) or (entry or {}).get("kind") == "arrow":
            return
        target = self._anchor_item(anchor.get("ref") or anchor.get("target"))
        if target is None:
            return
        geometry = self._anchor_geometry(anchor.get("ref") or anchor.get("target"))
        if geometry is None:
            return
        a = item.sceneBoundingRect().center()
        b = QPointF(geometry["cx"], geometry["cy"])
        leader = QGraphicsLineItem(a.x(), a.y(), b.x(), b.y())
        pen = QPen(QColor(COLOUR["accent"]), 1.5, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        leader.setPen(pen)
        leader.setZValue(item.zValue() - 0.5)
        leader.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        leader.setToolTip(f"Anchored to {anchor.get('target')}")
        self._scene.addItem(leader)
        self._anchor_leader = leader

    def _detach_if_moved_alone(self, old_state: dict, new_state: dict,
                               moved: set) -> None:
        """An anchored annotation dragged by hand, without its target.

        A box note keeps its anchor and records how far it was dragged from
        the anchored place (``dx``/``dy``), so it follows its target from
        its new spot; the Inspector's *Reset position* clears the offset and
        *Break link* removes the anchor. An arrow has no single place to
        offset from, so dragging it by hand still frees it. Either is one
        undoable edit (undo puts back the old state)."""
        anchor = old_state.get("anchor")
        if not isinstance(anchor, dict):
            return
        refs = {f"{self._KIND_REF.get(k, k)}:{i}" for k, i in moved}
        refs |= {self._anchor_element_name(k, i) for k, i in moved}
        if refs & {anchor.get(k) for k in ("ref", "target", "from_ref", "to_ref", "from", "to")}:
            return
        sink: Any = getattr(self, "status_message_sink", None)
        base = None
        if new_state.get("kind") != "arrow":
            plain = {**new_state, "anchor": {k: v for k, v in anchor.items()
                                             if k not in ("dx", "dy")}}
            base = self._anchored_geometry(plain)
        if base is None:
            new_state.pop("anchor", None)
            if sink is not None:
                sink(f"Annotation moved by hand: no longer anchored to "
                     f"{anchor.get('target') or anchor.get('from')} (re-anchor it in the Inspector)")
            return
        dx = round(float(new_state.get("x", 0)) - base["x"], 2)
        dy = round(float(new_state.get("y", 0)) - base["y"], 2)
        kept = {k: v for k, v in anchor.items() if k not in ("dx", "dy")}
        if dx or dy:
            kept["dx"], kept["dy"] = dx, dy
        new_state["anchor"] = kept
        if sink is not None and (dx or dy):
            sink(f"Moved; still anchored to {anchor.get('target')} "
                 f"(Inspector: Reset position or Break link)")

    def _sync_anno_z(self, item):
        aid = getattr(item, "_anno_id", None)
        for a in getattr(self, "annotations", []):
            if int(a.get("id", 0)) == aid:
                a["z"] = item.zValue()
                break
        self.layout_changed.emit()

    def set_annotation_text(self, item, text: str) -> None:
        """Commit an edited text annotation as one undoable command. The
        canvas item already holds the new text (the user typed it) while
        the model entry still holds the pre-edit text, so that entry is
        the "before" snapshot. Without this a typed edit was written
        straight into the model with no command, so undo/redo skipped it
        (and redo of the original "add" restored the empty initial text).
        """
        aid = getattr(item, "_anno_id", None)
        if aid is None:
            return
        entry = next((dict(a) for a in getattr(self, "annotations", [])
                      if int(a.get("id", 0)) == aid), None)
        if entry is None or entry.get("text", "") == text:
            return
        old = dict(entry)
        old.update(item.geometry_snapshot())
        old["z"] = item.zValue()
        new = dict(old)
        new["text"] = text
        link = entry.get("comment_of")
        comment_target = None
        comment_old = comment_new = None
        if isinstance(link, dict) and link.get("id") is not None:
            try:
                etype = str(link.get("type") or "")
                oid = int(link["id"])
            except (TypeError, ValueError):
                etype, oid = "", -1
            if etype in ("block", "node", "event", "hier") and oid >= 0:
                comment_target = (etype, oid)
                comment_old = self.current_comment_stored(etype, oid)
                comment_new = text.replace("\n", "~")
        self._stack.push(AnnoEditCmd(
            self, aid, old, new, comment_target=comment_target,
            comment_old=comment_old, comment_new=comment_new))

    def write_back_annotations(self):
        """Sync every live annotation item's geometry/text/props into
        rbd.annotations (called before save)."""
        for aid, item in getattr(self, "_anno_items", {}).items():
            for a in getattr(self, "annotations", []):
                if int(a.get("id", 0)) == aid:
                    sp = item.scenePos()
                    a["x"] = round(sp.x(), 2)
                    a["y"] = round(sp.y(), 2)
                    if hasattr(item, "rect"):
                        r = item.rect()
                        a["w"] = round(r.width(), 2)
                        a["h"] = round(r.height(), 2)
                    if hasattr(item, "line"):
                        ln = item.line()
                        a["x1"] = round(ln.x1(), 2)
                        a["y1"] = round(ln.y1(), 2)
                        a["x2"] = round(ln.x2(), 2)
                        a["y2"] = round(ln.y2(), 2)
                    if hasattr(item, "text_source"):
                        # Raw Markdown, not the rendered visible text.
                        a["text"] = item.text_source()
                    for k in ANNO_DEFAULTS:
                        if k in item.props:
                            a[k] = item.props[k]
                    break
        req(self._rbd_ref).annotations = list(getattr(self, "annotations", []))

    # ---------------------------------------------- annotation z-order
    def _anno_z_values(self) -> list[float]:
        """Every z value in use by an annotation on the CURRENT level, in
        scene order, de-duplicated. This is the ladder Bring Forward /
        Send Backward step along: a single step always lands on a real
        slot someone is actually using rather than an arbitrary +1, so
        two notes that share a band are swapped in one press instead of
        needing a dozen."""
        zs = {round(float(a.get("z", ANNO_Z_DEFAULT)), 4)
              for a in getattr(self, "annotations", [])
              if int(a.get("parent", 0)) == self._current_parent}
        return sorted(zs)

    def _selected_annotation_ids(self) -> list[int]:
        """Ids of the selected annotations, in scene insertion order."""
        return [aid for aid, it in getattr(self, "_anno_items", {}).items()
                if isValid(it) and it.isSelected()]

    def _n_deletable_selected(self) -> int:
        """How many things a Delete will actually remove.

        Not `len(selectedItems())`: an arrow annotation is deliberately
        NOT `ItemIsMovable` (it has no pos() to drag -- its two endpoints
        are its geometry), so a movable-flag count misses arrows, while
        adding the annotation count on top of a full `selectedItems()`
        count double-counts every other note. This is the one number the
        context menus label "Delete N selected" with.
        """
        return len([it for it in self._scene.selectedItems()
                    if (it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
                    or isinstance(it, tuple(_ANNO_TYPES))])

    def _add_order_actions(self, menu) -> dict:
        """Append the four z-order items to `menu` and return them keyed
        by command. Each is enabled only when it would move something --
        `annotation_order_target` is the single rule, shared with the
        ribbon buttons, so a menu can never offer a move the buttons
        would refuse."""
        out = {}
        for key, label in ANNO_ORDER_COMMANDS.items():
            act = menu.addAction(label)
            act.setEnabled(self.annotation_order_target(key) is not None)
            out[key] = act
        return out

    def _edit_annotation_text(self, item):
        """Put a text annotation into in-place editing, as a double-click
        does, so the menu is a real alternative to hunting for the
        item's middle."""
        if getattr(item, "kind", None) != "text":
            return
        item.enter_edit()

    def _duplicate_selected_annotations(self) -> int:
        """Copy-paste the selected annotations, offset down-right so the
        copy is visible next to Raptor 7. One undo step, same
        `AnnoCmd` path a normal paste uses."""
        if not self._selected_annotation_ids():
            return 0
        # `copy_selection`'s return is the ELEMENT count, which is 0 for
        # an annotation-only selection even though it filled the buffer
        # -- so ask the buffer what is actually in it.
        self.copy_selection()
        annos = [r for k, r in getattr(self, "_paste_buffer", [])
                 if k == "annotation"]
        if not annos:
            return 0
        self.paste(offset=(16.0, 12.0))
        return len(annos)

    def annotation_order_target(self, command: str) -> float | None:
        """The z value `command` would move the current annotation
        selection to, or None when the command has nothing to do.

        This is the single source of truth behind the ribbon buttons, the
        context-menu items and their disabled states -- the menu asks
        here rather than re-deriving the rule, so a button can never be
        enabled for a move that would not change anything.
        """
        sel = self._selected_annotation_ids()
        if not sel:
            return None
        items: list[Any] = [self._anno_items[aid] for aid in sel]
        current = [it.zValue() for it in items]
        ladder = sorted(set(self._anno_z_values())
                        | set(ANNO_Z_BANDS))
        target: float | None
        if command == "front":
            target = ANNO_Z_FRONT
        elif command == "back":
            target = ANNO_Z_BEHIND
        elif command == "forward":
            above = [z for z in ladder if z > max(current)]
            target = above[0] if above else None
        elif command == "backward":
            below = [z for z in ladder if z < min(current)]
            target = below[-1] if below else None
        else:
            return None
        if target is None or all(abs(c - target) < 1e-6 for c in current):
            return None      # already there: nothing to do
        return target

    def set_annotation_order(self, command: str) -> bool:
        """Apply one of the four z-order commands to the whole annotation
        selection as ONE undo step. Returns True if anything moved."""
        # Checked BEFORE anything moves: this is an undoable edit, and a
        # canvas with no stack yet must not be half-changed.
        stack = getattr(self, "_stack", None)
        if stack is None:
            return False
        target = self.annotation_order_target(command)
        if target is None:
            return False
        label = ANNO_ORDER_COMMANDS[command]
        edits = []
        for aid in self._selected_annotation_ids():
            before = self._anno_state(aid)
            if before is None:
                continue
            item: Any = self._anno_items[aid]
            item.setZValue(target)
            self._sync_anno_z(item)
            after = self._anno_state(aid)
            if after is not None and after != before:
                edits.append((aid, before, after))
        if not edits:
            return False
        if len(edits) == 1:
            aid, before, after = edits[0]
            stack.push(AnnoEditCmd(self, aid, before, after, label,
                                   layout_only=True))
        else:
            # Z-order cannot break an input rule either; keep the whole
            # macro off the gate's two whole-model passes (the `ungated`
            # scope must cover beginMacro too -- that is where the "before"
            # snapshot is taken).
            with stack.ungated():
                stack.beginMacro(label)
                try:
                    for aid, before, after in edits:
                        stack.push(AnnoEditCmd(self, aid, before, after,
                                               label, layout_only=True))
                finally:
                    stack.endMacro()
        return True

    def new_annotation_z(self) -> float:
        """z for a freshly placed annotation: behind the reliability
        elements.

        A note annotates the diagram; starting it in front let it cover
        the blocks it describes (and, while a target was being dragged,
        float over the space that target had left). Bring Forward / Bring
        to Front are still there for a callout the user wants on top.
        """
        return ANNO_Z_DEFAULT

    def _anno_default_props(self, kind: str) -> dict:
        """Fresh props for a newly placed `kind` annotation: the
        theme-aware factory base, overlaid with the user's saved style
        default for that kind.

        The default is set from the context menu's "Set as Default for
        New <Kind>s" on an existing annotation, so the workflow is
        "style one, then place the rest" instead of re-picking the same
        colours on every shape. Keys absent from the saved default (and
        keys that don't apply to this kind, like a fill on an arrow) fall
        through to the factory value, so a default saved for one kind
        can never give another kind an invalid prop.
        """
        props = _anno_base_props()
        applicable = ANNO_APPLICABLE_PROPS.get(kind, tuple(ANNO_DEFAULTS))
        saved = getattr(self, "default_annotation_props", {}).get(kind)
        if saved:
            for key, value in saved.items():
                if key in applicable:
                    props[key] = value
        if kind == "arrow" and "border_width" not in (saved or {}):
            # The shared shape default (1px, or 0 for "no border") would
            # make a brand-new arrow all but invisible the instant it is
            # placed -- unlike a rect/ellipse/text box, the line IS the
            # whole annotation, so it needs its own visible width. A
            # saved arrow default that names a width wins over this.
            props["border_width"] = 2
        return props

    def set_annotation_style_default(self, kind: str, props: dict) -> None:
        """Adopt `props` as the style for every future `kind`
        annotation. Persisting it is MainWindow's job (it owns QSettings);
        this applies it and says so."""
        stored = {k: v for k, v in dict(props or {}).items()
                  if k in ANNO_APPLICABLE_PROPS.get(kind, ())}
        self.default_annotation_props[kind] = stored
        self.annotation_style_default_changed.emit(kind, dict(stored))
        noun = ANNO_KIND_NOUNS.get(kind, (kind, kind))[1]
        _view_status(self, f"New {noun} will use this style")

    def clear_annotation_style_default(self, kind: str) -> None:
        """Forget the saved style for `kind`, back to the factory look
        (theme-aware colours, no fill)."""
        self.default_annotation_props.pop(kind, None)
        self.annotation_style_default_changed.emit(kind, None)
        noun = ANNO_KIND_NOUNS.get(kind, (kind, kind))[1]
        _view_status(self, f"New {noun} reverted to the default style")

    def has_annotation_style_default(self, kind: str) -> bool:
        return kind in getattr(self, "default_annotation_props", {})

    def delete_selected_annotations(self) -> int:
        stack = getattr(self, "_stack", None)
        n = 0
        for aid, item in list(getattr(self, "_anno_items", {}).items()):
            if not item.isSelected():
                continue
            entry = next((a for a in getattr(self, "annotations", [])
                          if int(a.get("id", 0)) == aid), None)
            if entry is None:
                continue
            # Undoable via the existing AnnoCmd (add=False) -- before
            # this it removed the item directly, so a Ctrl+Z could not
            # bring a deleted annotation back even though the command
            # class already existed for exactly this.
            if stack is not None:
                stack.push(AnnoCmd(self, entry, add=False))
            else:
                self._scene.removeItem(item)
                del self._anno_items[aid]
                self.annotations = [
                    a for a in getattr(self, "annotations", [])
                    if int(a.get("id", 0)) != aid]
            n += 1
        if n:
            _view_status(self, f"{n} annotation(s) deleted")
            if stack is None:
                self.model_changed.emit()
        return n

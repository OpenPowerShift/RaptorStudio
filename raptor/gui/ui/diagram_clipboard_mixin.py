"""Cut, copy, paste and delete on the canvas.

Expects on `self`: `_rbd`, the undo stack and the item tables."""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req

from typing import TYPE_CHECKING, Any

from shiboken6 import isValid
from PySide6.QtCore import (
    QByteArray, QMimeData,
)
from PySide6.QtWidgets import (
    QApplication,
)

from raptor.gui import clipboard_payload
from raptor.gui.clipboard_plan import (
    count_elements, plan_copy, plan_paste, remap_dependencies)
from raptor.gui.ui.diagram_items import routing_batch
from raptor.gui.ui.diagram_common import (
    BLOCK_H,
    BLOCK_W,
    _safe_int,
)
from raptor.gui.ui.diagram_commands import (
    AddCmd,
    DeleteCmd,
    DepResetCmd,
    RouteGripCmd,
)
from raptor.gui.ui.diagram_items import (
    _LinkItem,
    _view_status,
)
from raptor.gui.ui.diagram_annotations import (
    AnnoCmd,
)


if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsView as _Base
    import PySide6.QtCore
    import PySide6.QtWidgets
    import raptor.core.model
    import raptor.gui.ui.diagram_element_items
    import raptor.gui.ui.diagram_item_core
    import raptor.gui.ui.diagram_link_items
else:
    _Base = object


class ClipboardMixin(_Base):
    """Cut, copy, paste and delete on the canvas."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _block_items: dict[int, raptor.gui.ui.diagram_item_core._BlockItem]
        def _clear_route_caches(self) -> None: ...
        _current_parent: int
        def _dissolve_hierarchies(self, hids: Any) -> Any: ...
        _event_items: dict[int, raptor.gui.ui.diagram_element_items._EventItem]
        _links: list[raptor.gui.ui.diagram_link_items._LinkItem]
        _loading: bool
        def _model_to_scene(self, model_xy: tuple[float, float]) -> tuple[float, float]: ...
        _model_token: str
        def _n_deletable_selected(self) -> int: ...
        def _name_index(self) -> dict[tuple[str, int], str]: ...
        _node_items: dict[int, raptor.gui.ui.diagram_item_core._NodeItem]
        _paste_buffer: list
        _rbd_ref: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _recount_link_ends(self) -> None: ...
        def _resolve_link_endpoint(self, ref: str, oid: int) -> Any: ...
        _scale: float
        _scene: PySide6.QtWidgets.QGraphicsScene
        _stack: Any
        def _tx(self, x: float) -> float: ...
        def _ty(self, y: float) -> float: ...
        annotations: list[Any]
        blocks: dict[int, raptor.core.model.RBDBlock]
        events: dict[int, Any]
        hiers: dict[int, raptor.core.model.RBDHier]
        links: list[raptor.core.model.Link]
        model_changed: PySide6.QtCore.SignalInstance
        nodes: dict[int, raptor.core.model.RBDNode]
        paste_state_changed: PySide6.QtCore.SignalInstance
        def selected_element_count(self) -> int: ...
        structural_about_to_change: PySide6.QtCore.SignalInstance

    def copyable_count(self) -> int:
        """Selected things Copy/Cut take: elements and annotations."""
        try:
            n = self.selected_element_count()
            return n + sum(1 for it in getattr(self, "_anno_items", {}).values()
                           if it.isSelected())
        except RuntimeError:        # scene torn down during shutdown
            return 0

    def _clipboard_payload(self) -> clipboard_payload.Payload | None:
        """The Raptor Studio clip on the system clipboard, if there is one
        (copied in another model, another window or another running app)."""
        mime = QApplication.clipboard().mimeData()
        if mime is None or not mime.hasFormat(clipboard_payload.MIME):
            return None
        return clipboard_payload.decode(
            bytes(mime.data(clipboard_payload.MIME).data()))

    def can_paste(self) -> bool:
        """Paste has something to paste: a Raptor clip on the system
        clipboard, or (when the clipboard is empty or unavailable) this
        view's own earlier copy. Something else on the clipboard (text from
        another program) means there is nothing of ours to paste."""
        if self._rbd_ref is None:
            return False
        mime = QApplication.clipboard().mimeData()
        if mime is not None and mime.formats():
            return self._clipboard_payload() is not None
        return bool(self._paste_buffer)

    def _publish_clip(self, buffer) -> None:
        """Put `buffer` on the system clipboard beside the in-app copy."""
        mime = QMimeData()
        name = getattr(self._rbd_ref, "file_name", "") or ""
        mime.setData(clipboard_payload.MIME, QByteArray(
            clipboard_payload.encode(buffer, source_name=name,
                                     model_token=self._model_token)))
        mime.setText(clipboard_payload.summary_text(buffer, name))
        QApplication.clipboard().setMimeData(mime)

    def copy_selection(self) -> int:
        """Snapshot the selected elements (+ their fully-internal links)
        into the paste buffer. Returns the element count. With nothing
        selected it does nothing, so it cannot empty a buffer that holds
        an earlier copy."""
        if self._rbd_ref is None or not self.copyable_count():
            return 0
        sel_b = [bid for bid, it in self._block_items.items()
                 if it.isSelected() and bid != 0]
        sel_n = [nid for nid, it in self._node_items.items()
                 if it.isSelected() and self.nodes[nid].type_node == 2]
        sel_e = [eid for eid, it in self._event_items.items()
                 if it.isSelected()]

        # Selected text/rect/ellipse overlays travel with the copy as
        # full entry dicts.
        sel_annos = [dict(a) for aid, it in
                     getattr(self, "_anno_items", {}).items()
                     if it.isSelected()
                     for a in getattr(self, "annotations", [])
                     if int(a.get("id", 0)) == aid]
        sel_h = [hid for hid, it in
                 getattr(self, "_hier_items", {}).items()
                 if it.isSelected()]
        buffer = plan_copy(self._rbd_ref, self.hiers, blocks=sel_b,
                           nodes=sel_n, events=sel_e, sel_hiers=sel_h,
                           parent=self._current_parent)
        for a in sel_annos:
            buffer.append(("annotation", dict(a)))
        self._paste_buffer = buffer
        if buffer:
            self._publish_clip(buffer)
        self.paste_state_changed.emit()
        return count_elements(buffer)

    def cut_selection(self) -> int:
        """Copy then delete, in one gesture.

        Whether to delete is decided from the SELECTION, not from
        `copy_selection`'s return value: that number is the count of
        copied *elements*, which is 0 for an annotation-only selection
        even though the buffer filled fine. Gating the delete on it made
        Cut a silent no-op on any annotation -- the user pressed Cut,
        the note filled the clipboard, and it stayed on the canvas.
        """
        n = self._n_deletable_selected()
        if not n:
            return 0
        self.copy_selection()
        self.delete_selected()
        return n

    def paste(self, at_model=None, offset=(8.0, 6.0), buffer=None):
        """Re-insert buffered elements with fresh ids; internal links are
        remapped to the new ids. `at_model` anchors the cluster's top-left
        to a model point (context-menu paste). One macro undo step.
        Returns the number of elements pasted."""
        if self._rbd_ref is None:
            return 0
        notes: list[str] = []
        same_model_paste = True
        if buffer is None:
            # The system clipboard first: it carries copies made in other
            # models and other windows. Anything else on it, or nothing,
            # falls back to this view's own last copy.
            payload = self._clipboard_payload()
            if payload is not None:
                same_model_paste = clipboard_payload.same_model(
                    payload, self._model_token)
                adapted = clipboard_payload.adapt_for_target(
                    self._rbd_ref, payload, same_model=same_model_paste)
                buffer, notes = adapted.buffer, adapted.notes
            else:
                mime = QApplication.clipboard().mimeData()
                if mime is not None and mime.formats():
                    return 0       # something foreign is on the clipboard
                buffer = self._paste_buffer
        if not buffer:
            return 0
        parent = self._current_parent

        anno_entries = [r for k, r in buffer if k == "annotation"]
        elements = [(k, r) for k, r in buffer
                    if k not in ("link", "annotation")]

        # Anchor and target, both in SCENE coordinates, then converted
        # per-kind. This used to compute the anchor from the elements'
        # raw `xPos` (model units) and add that same model-space delta to
        # the annotations' `x`/`y` (SCENE units) -- so a pasted
        # annotation moved by 1/scale of the intended distance, and an
        # annotation-only buffer (no elements to take an anchor from)
        # anchored at 0.0 and moved by the raw model coordinate of the
        # click. Everything below works in scene space and converts once.
        scene_anchors: list[tuple[float, float]] = [
            (self._tx(float(r.get("xPos", 0))),
             self._ty(float(r.get("yPos", 0)))) for _, r in elements]
        for a in anno_entries:
            if a.get("kind") == "arrow":
                # An arrow's own x/y are always 0 -- its geometry lives
                # entirely in the two endpoints (see _AnnoLine), so
                # anchoring it on x/y would put the reference point at
                # the scene origin and shift the copy by the wrong
                # amount.
                scene_anchors.append((float(a.get("x1", 0)),
                                      float(a.get("y1", 0))))
            else:
                scene_anchors.append((float(a.get("x", 0)),
                                      float(a.get("y", 0))))
        anchor_sx = min((p[0] for p in scene_anchors), default=0.0)
        anchor_sy = min((p[1] for p in scene_anchors), default=0.0)
        if at_model is not None:
            target_sx, target_sy = self._model_to_scene(at_model)
        else:
            # Plain paste: anchor at the mouse cursor when it is over
            # the canvas, else the viewport centre -- so Ctrl+V puts
            # the copy right where the user is looking. `offset` nudges
            # it clear of the original so repeated Ctrl+V cascades
            # visibly instead of stacking into an exact overlap.
            from PySide6.QtGui import QCursor
            vp = self.viewport()
            local = vp.mapFromGlobal(QCursor.pos())
            if vp.rect().contains(local):
                sp = self.mapToScene(local)
            else:
                sp = self.mapToScene(vp.rect().center())
            target_sx = sp.x() + offset[0]
            target_sy = sp.y() + offset[1]
        # A plain paste must never land exactly on its own source. The
        # `offset` nudge is 8 scene px, so whenever the cursor (or the
        # viewport centre, headless) sat within that of the source the
        # copy was drawn on top of the original: the user sees nothing
        # happen, and repeated Ctrl+V looks broken. Guarantee a real
        # cascade step instead -- down and to the right, by one block.
        if (abs(target_sx - anchor_sx) < 1e-6
                and abs(target_sy - anchor_sy) < 1e-6):
            target_sx += BLOCK_W / 2.0
            target_sy += BLOCK_H
        sdx = target_sx - anchor_sx
        sdy = target_sy - anchor_sy
        # Element rows are in model units, and y is flipped: a scene
        # delta of +sdy is a model delta of -sdy / scale.
        dx = sdx / self._scale
        dy = -sdy / self._scale
        plan = plan_paste(
            self._rbd_ref, buffer,
            {"block": self.blocks, "node": self.nodes,
             "event": self.events, "hier": self.hiers},
            dx=dx, dy=dy, parent=parent,
            parent_level=(self.hiers[parent].level
                          if parent in self.hiers else None))
        new_rows = plan.new_rows
        # Dependencies among the pasted elements follow their copies; ones on
        # elements left behind survive only in the model they came from.
        lost = remap_dependencies(plan, keep_external=same_model_paste)
        if lost:
            notes.append(f"{lost} dependency(ies) on elements that were not "
                         "copied were dropped")

        # Pasted content, not typed input: Raptor 7's PasteObject checks only
        # the edition's block limit and hierarchy depth, not the dialog or
        # link rules, so neither does `rule_gate`.
        with self._stack.ungated():
            self._stack.beginMacro("Paste elements")
            try:
                if new_rows["block"]:
                    self._stack.push(AddCmd(self, "block", new_rows["block"]))
                if new_rows["node"]:
                    self._stack.push(AddCmd(self, "node", new_rows["node"]))
                if new_rows["event"]:
                    self._stack.push(AddCmd(self, "event", new_rows["event"]))
                if new_rows["hier"]:
                    self._stack.push(AddCmd(self, "hier", new_rows["hier"]))
                # Internal links last: both ends must exist when they are added.
                if plan.link_rows:
                    self._stack.push(AddCmd(self, "link", plan.link_rows))
                # Pasted annotations: fresh ids, offset by the SCENE delta
                # (`sdx`/`sdy`, not the model-space `dx`/`dy` the element
                # rows use -- annotation x/y are scene coordinates).
                #
                # Pushed as `AnnoCmd(add=True)` rather than appended to
                # `self.annotations` directly, so it lands in this macro like
                # the element rows. Appending it raw left the copy outside
                # the undo stack: Ctrl+Z after pasting a note removed the
                # elements beside it (or nothing at all) and left the note
                # sitting on the canvas, and a later redo added a second
                # one.
                for a in anno_entries:
                    a2 = dict(a)
                    used_a = {int(x.get("id", 0)) for x in self.annotations}
                    nid = max(used_a | {0}) + 1
                    a2["id"] = nid
                    a2["x"] = round(float(a.get("x", 0)) + sdx, 2)
                    a2["y"] = round(float(a.get("y", 0)) + sdy, 2)
                    if a2.get("kind") == "arrow":
                        a2["x1"] = round(float(a.get("x1", 0)) + sdx, 2)
                        a2["y1"] = round(float(a.get("y1", 0)) + sdy, 2)
                        a2["x2"] = round(float(a.get("x2", 100)) + sdx, 2)
                        a2["y2"] = round(float(a.get("y2", 0)) + sdy, 2)
                    a2["parent"] = parent
                    self._stack.push(AnnoCmd(self, a2, add=True))
            finally:
                self._stack.endMacro()
        if notes:
            sink = getattr(self, "status_message_sink", None)
            if sink is not None:
                sink("Pasted. " + "; ".join(notes) + ".")
        # Select the pasted elements.
        self._scene.clearSelection()
        for kind, rows in new_rows.items():
            items = {"block": self._block_items, "node": self._node_items,
                     "event": self._event_items,
                     "hier": getattr(self, "_hier_items", {})}[kind]
            for r in rows:
                it = items.get(_safe_int(r["ID"]))
                if it is not None:
                    it.setSelected(True)
        return len(elements)

    def _delete_annos(self, anno_entries: list, macro: str | None):
        """Push one `AnnoCmd(add=False)` per annotation entry, optionally
        wrapped in a named macro. `macro=None` pushes them individually
        (a caller that is already inside its own macro -- the hierarchy
        dissolve -- must not nest)."""
        if not anno_entries:
            return
        stack = self._stack

        def _push():
            for entry in anno_entries:
                stack.push(AnnoCmd(self, entry, add=False))
        if macro is None:
            _push()
        else:
            stack.beginMacro(macro)
            try:
                _push()
            finally:
                stack.endMacro()

    def delete_selected(self):
        """Delete every selected block/node/event/link/annotation (plus
        any links orphaned by the deletion)."""
        # Commit a Comment draft before the deletion command is pushed:
        # the comment must be part of the model the delete captures (and
        # must not become the top undo step above DeleteCmd).
        self.structural_about_to_change.emit()
        # Selected ANNOTATIONS ride along with everything else. They used
        # to be deleted only when NO block/node/event was also selected,
        # so a mixed selection silently left the notes behind while the
        # context menu said "Delete N selected".
        # GUI-only routing grips first: they are layout state, not model
        # rows, and the annotation deletion path below does not know them.
        for grips in list(getattr(self, "_route_grips", {}).values()):
            for grip in list(grips):
                if not isValid(grip) or not grip.isSelected():
                    continue
                entry = next((a for a in getattr(self._rbd_ref, "link_grips", [])
                              if int(a.get("id", 0)) == grip._entry_id), None)
                if entry is not None:
                    self._stack.push(RouteGripCmd(self, entry, add=False))
        sel_annos = {int(aid) for aid, it in
                     getattr(self, "_anno_items", {}).items()
                     if it.isSelected()}
        anno_entries = [a for a in getattr(self, "annotations", [])
                        if int(a.get("id", 0)) in sel_annos]
        # pushes one DeleteCmd that removes the underlying file rows,
        # so Save really deletes and Undo restores them.
        if self._rbd_ref is None:
            return
        dead_blocks = {iid for iid, it in self._block_items.items()
                       if it.isSelected() and iid != 0}
        dead_nodes = {iid for iid, it in self._node_items.items()
                      if it.isSelected()
                      # Start/End/In/Out markers are structural (the
                      # manual: "cannot be deleted").
                      and self.nodes[iid].type_node in (2,)}
        dead_events = {iid for iid, it in self._event_items.items()
                       if it.isSelected()}
        dead_link_nums = {it._link_num for it in self._links
                          if it.isSelected() and it._link_num is not None}

        # Selected hierarchy ovals: dissolve (children promote to the
        # enclosing level; oval + markers die; oval-referencing links
        # are cut). Handled as its own undoable command, one per hier.
        sel_hiers = [hid for hid, it in
                     getattr(self, "_hier_items", {}).items()
                     if it.isSelected()]
        if sel_hiers and not (dead_blocks or dead_nodes or dead_events):
            # A hierarchy dissolve on its own. Delete any annotations in
            # the same selection first (not nested -- this is its own
            # top-level undo step, as it always was).
            self._delete_annos(anno_entries,
                               f"Delete {len(anno_entries)} annotation(s)"
                               if anno_entries else None)
            self._dissolve_hierarchies(sel_hiers)
            return
        dead_any = dead_blocks | dead_nodes | dead_events

        block_section = self._rbd_ref.sections.get("BLOCK_DATA")
        node_section = self._rbd_ref.sections.get("NODE_DATA")
        event_section = self._rbd_ref.sections.get("EVENT_DATA")
        link_section = self._rbd_ref.sections.get("LINK_DATA")

        def _rows(sec, id_col, ids):
            return [dict(r) for r in (sec.rows if sec else [])
                    if _safe_int(r.get(id_col)) in ids]

        # Links to remove: explicitly selected or orphaned by the deletion.
        # Match the endpoint TYPE as well as its id: ids are numbered
        # independently per kind, so deleting Node 3 must not also drop a
        # link that touches Block 3 (reported as "earlier links break"
        # when deleting nodes whose ids collide with blocks).
        dead_by_kind = {"Block": dead_blocks, "Node": dead_nodes,
                        "Event": dead_events}

        def _end_dead(row: dict, side: str) -> bool:
            kind = row.get(f"{side}Type", "")
            oid = _safe_int(row.get(f"{side}ID"))
            return oid in dead_by_kind.get(kind, ())

        link_rows = [dict(r) for r in (link_section.rows if link_section else [])
                     if (_safe_int(r.get("ID")) in dead_link_nums
                         or _end_dead(r, "From")
                         or _end_dead(r, "To"))]
        if not (dead_blocks or dead_nodes or dead_events or link_rows
                or anno_entries):
            return
        # Dependency warning: replicate Raptor's behaviour when deleting
        # elements other elements depend on -- its own reset routine is
        # ResetDepNums (Raptor 7, called from ClearObject).
        # Collect the surviving elements whose DependencyNum
        # points at anything in the selection, warn with their names, and
        # -- on confirm -- reset them to independent in the same undoable
        # macro as the deletion, so dangling DependencyNums can never
        # survive a Save.
        reset_entries: list[tuple[str, int, int, str]] = []
        dependent_lines: list[str] = []
        for section, obj_attr in (("BLOCK_DATA", "blocks"),
                                  ("NODE_DATA", "nodes"),
                                  ("EVENT_DATA", "events")):
            sec = self._rbd_ref.sections.get(section)
            if sec is None:
                continue
            for r in sec.rows:
                oid = _safe_int(r.get("ID"))
                dep = _safe_int(r.get("DepNum" if section == "BLOCK_DATA"
                                       else "depNum"))
                if oid in dead_any or dep <= 0 or dep not in dead_any:
                    continue
                obj = getattr(self, obj_attr, {}).get(oid)
                old = (getattr(obj, "dependency_num", dep) if obj else dep,
                       getattr(obj, "dep_type", "") if obj else "")
                reset_entries.append((section, oid, old[0], old[1]))
                name = getattr(obj, "name", None) or r.get(
                    "blockName", r.get("Name", r.get("eventName", oid)))
                target_name = (self._name_index().get(
                    ("block" if dep in dead_blocks
                     else "node" if dep in dead_nodes else "event", dep))
                    or f"#{dep}")
                dependent_lines.append(f"• {name} — depends on {target_name}")
        if dependent_lines:
            from PySide6.QtWidgets import QMessageBox as _MB
            detail = "\n".join(dependent_lines)
            n = len(reset_entries)
            answer = _MB.warning(
                self,
                "Dependencies on deleted element"
                + ("s" if len(dead_any) != 1 else ""),
                "At least one other element depends on what you are "
                "deleting:\n\n" + detail + "\n\n"
                f"{'They will' if n != 1 else 'It will'} be RESET TO "
                "INDEPENDENT when the deletion is made.\n\n"
                "Delete anyway?",
                _MB.StandardButton.Yes | _MB.StandardButton.No, _MB.StandardButton.No)
            if answer != _MB.StandardButton.Yes:
                return
        # One macro for the whole deletion, so a single Ctrl+Z restores
        # the elements, the links AND the annotations together -- the
        # mixed selection is one gesture and undoes as one.
        n_annos = len(anno_entries)
        label = ("Delete selection + reset dependents" if reset_entries
                 else (f"Delete {n_annos} annotation(s)"
                       if not (dead_blocks or dead_nodes or dead_events
                               or link_rows)
                       else "Delete selection"))
        self._stack.beginMacro(label)
        try:
            if reset_entries:
                self._stack.push(DepResetCmd(self, reset_entries))
            has_elements = bool(dead_blocks or dead_nodes or dead_events
                                or link_rows)
            if has_elements:
                self._stack.push(DeleteCmd(
                    self,
                    _rows(block_section, "ID", dead_blocks),
                    _rows(node_section, "ID", dead_nodes),
                    _rows(event_section, "ID", dead_events),
                    link_rows))
            self._delete_annos(anno_entries, macro=None)
        finally:
            self._stack.endMacro()
        if n_annos:
            _view_status(self, f"Deleted {n_annos} annotation(s)"
                         + (" and the selected elements" if has_elements
                            else ""))

    def remove_links_selected(self):
        """Delete every link touching a selected block/node/event/
        hierarchy without deleting the elements themselves -- unlike
        delete_selected, which takes the elements (and their links) with
        it."""
        if self._rbd_ref is None:
            return
        endpoints = set()
        for kind, items in (("Block", self._block_items),
                            ("Node", self._node_items),
                            ("Event", self._event_items),
                            ("Hier", getattr(self, "_hier_items", {}))):
            for oid, it in items.items():
                if it.isSelected():
                    endpoints.add((kind, oid))
        if not endpoints:
            return

        def touches(link) -> bool:
            f = (link.connect_f_ref.replace("RBD", "", 1),
                 link.connect_from_id)
            t = (link.connect_t_ref.replace("RBD", "", 1),
                 link.connect_to_id)
            return f in endpoints or t in endpoints

        dead_link_nums = {link.object_num for link in self.links
                          if touches(link)}
        if not dead_link_nums:
            _view_status(self, "No links to remove")
            return
        link_section = self._rbd_ref.sections.get("LINK_DATA")
        link_rows = [dict(r) for r in (link_section.rows if link_section else [])
                     if _safe_int(r.get("ID")) in dead_link_nums]
        self._stack.push(DeleteCmd(self, [], [], [], link_rows))
        _view_status(self, f"Removed {len(dead_link_nums)} link(s)")

    def _remove_ids(self, dead_blocks, dead_nodes, dead_events, dead_link_nums):
        """Shared removal: drops file rows, canvas items, and model
        entries (used by DeleteCmd.redo and AddCmd.undo)."""
        block_section = req(self._rbd_ref).sections.get("BLOCK_DATA")
        node_section = req(self._rbd_ref).sections.get("NODE_DATA")
        event_section = req(self._rbd_ref).sections.get("EVENT_DATA")
        link_section = req(self._rbd_ref).sections.get("LINK_DATA")

        _dead_any = dead_blocks | dead_nodes | dead_events

        def _drop_rows(sec, id_col, ids):
            if sec is not None and ids:
                sec.rows[:] = [r for r in sec.rows
                               if _safe_int(r.get(id_col)) not in ids]

        _drop_rows(block_section, "ID", dead_blocks)
        _drop_rows(node_section, "ID", dead_nodes)
        _drop_rows(event_section, "ID", dead_events)
        if link_section is not None and dead_link_nums:
            link_section.rows[:] = [r for r in link_section.rows
                            if _safe_int(r.get("ID")) not in dead_link_nums]
        # A link explicitly selected and deleted (not just orphaned by a
        # block/node/event deletion) has both endpoints still alive, so
        # the endpoint-aliveness filter alone would keep redrawing it --
        # self.links must drop it by object_num here too.
        if dead_link_nums:
            self.links[:] = [link for link in self.links
                             if link.object_num not in dead_link_nums]
        for iid in dead_blocks:
            item: Any
            item = self._block_items.pop(iid, None)
            if item is not None:
                self._scene.removeItem(item)
            self.blocks.pop(iid, None)
        for iid in dead_nodes:
            item = self._node_items.pop(iid, None)
            if item is not None:
                self._scene.removeItem(item)
            self.nodes.pop(iid, None)
        for iid in dead_events:
            item = self._event_items.pop(iid, None)
            if item is not None:
                self._scene.removeItem(item)
            self.events.pop(iid, None)
        self._rebuild_link_items()
        self._recount_link_ends()
        if not self._loading:
            self.model_changed.emit()

    def _rebuild_link_items(self):
        """Recreate every _LinkItem from the live links list, dropping
        links whose endpoints are gone. Endpoint liveness is checked per
        type -- ids collide across collections (a node and a deleted
        block can both be id 1)."""
        def alive(ref: str, oid: int) -> bool:
            # LINK_DATA stores "Block"/"Node"/..., but link_from_row maps
            # refs to the model class names ("RBDBlock", ...) -- match both.
            if ref in ("Block", "RBDBlock"):
                return oid in self.blocks
            if ref in ("Event", "RBDEvent"):
                return oid in self.events
            if ref in ("Hier", "RBDHier"):
                return oid in self.hiers  # boundary links reference the oval
            return oid in self.nodes  # Node / RBDNode

        self.links[:] = [
            existing_link for existing_link in self.links
            if alive(existing_link.connect_f_ref, existing_link.connect_from_id)
            and alive(existing_link.connect_t_ref, existing_link.connect_to_id)]
        # Keep every link item whose two ends are still the same canvas
        # items, and only add or remove the ones that changed. Deleting one
        # block used to throw away and rebuild every link on the level, and
        # each new link routed itself in its constructor, which rescanned the
        # whole scene for the link index: about 24 s on a 1,000-block level
        # for a change that touches a handful of links.
        existing: dict[int, _LinkItem] = {}
        for li in self._links:
            num = li._link_num
            if num is None or num in existing or not isValid(li):
                continue
            existing[num] = li
        owned = {id(li) for li in existing.values()}
        # Anything else link-shaped on the scene (an item this list lost
        # track of) goes, as the full rebuild always did.
        for item in list(self._scene.items()):
            if isinstance(item, _LinkItem) and id(item) not in owned:
                self._scene.removeItem(item)
        kept: list[_LinkItem] = []
        added: list[_LinkItem] = []
        touched: dict[int, Any] = {}

        def touch(li: _LinkItem) -> None:
            for end in (li._from, li._to):
                if end is not None and isValid(end):
                    touched[id(end)] = end

        for link in self.links:
            src = self._resolve_link_endpoint(
                link.connect_f_ref, link.connect_from_id)
            dst = self._resolve_link_endpoint(
                link.connect_t_ref, link.connect_to_id)
            if src is None or dst is None:
                continue
            link_item: _LinkItem | None = existing.pop(link.object_num, None)
            if link_item is not None and (link_item._from is not src or link_item._to is not dst):
                touch(link_item)
                self._scene.removeItem(link_item)
                link_item = None
            if link_item is None:
                link_item = _LinkItem(src, dst, link_num=link.object_num,
                               defer_route=True)
                self._scene.addItem(link_item)
                added.append(link_item)
                touch(link_item)
            kept.append(link_item)
        for li in existing.values():        # their link is gone
            touch(li)
            self._scene.removeItem(li)
        changed = len(added) + len(existing)
        self._links[:] = kept
        # Ports depend on an element's other links (its incoming side
        # fixes its outgoing side), so re-route once every link exists
        # rather than leaving the earlier ones with a partial picture of
        # the topology. A small change re-routes only the links that meet
        # an element whose links changed; a large one re-routes them all.
        self._clear_route_caches()
        if changed * 4 >= max(len(kept), 1):
            reroute = list(kept)
        else:
            from raptor.gui.ui.diagram_link_items import _links_at
            seen: dict[int, _LinkItem] = {id(li): li for li in added}
            for end in touched.values():
                incoming, outgoing = _links_at(end)
                for li in (*incoming, *outgoing):
                    seen[id(li)] = li
            reroute = list(seen.values())
        with routing_batch(self._scene):
            for li in reroute:
                li._update_route()

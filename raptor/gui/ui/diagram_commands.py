"""Undo commands for the diagram canvas: each captures the old value and
re-applies via the view's `_apply_*` helpers or its file rows."""
from __future__ import annotations
from PySide6.QtGui import (
    QUndoCommand,
)
from raptor.core.fileio.typed import (
    block_from_row,
)
from raptor.gui.ui.diagram_common import (  # noqa: I001
    BLOCK_H,
    BLOCK_W,
    _PARENT_COLS,
    _safe_int,
)


# --- Undo/redo commands -------------------------------------------------
#
# The DiagramView owns the QUndoStack: every mutation entry point
# (Inspector signals, Data Tables grid, build tools, canvas drags)
# funnels through a public view method that pushes one of these commands.
# Commands call back into small _apply_* helpers on the view; the helpers
# update both the live model objects and the underlying RbdFile rows, so
# Save persists whatever state undo/redo leaves behind.


class _ViewCmd(QUndoCommand):
    """Base for every canvas undo command: holds the view and emits
    model_changed after each successful apply (suppressed during
    programmatic rebuilds via _loading). Subclasses implement
    redo()/undo() and call self._done() last.

    Position-only commands override `changed_signal` with
    "layout_changed": the engine does not read geometry, so replaying a
    drag must not invalidate the whole-model validation or repopulate the
    Data Tables/Inspector (`MainWindow._mark_layout_dirty` is the light
    path for exactly that)."""

    #: Name of the DiagramView signal `_done` emits.
    changed_signal = "model_changed"
    #: Whether `RuleGatedStack` must re-run Raptor 7's input rules around
    #: this command. A layout-only command cannot break a data rule (no
    #: rule reads x/y/w/h/z), and the check deep-copies the whole model and
    #: walks every row twice -- seconds on a 3,470-block file, on every
    #: drag release. Position-only commands turn it off.
    needs_rule_check = True

    def __init__(self, view, text):
        super().__init__(text)
        self._v = view

    def _done(self):
        if not self._v._loading:
            getattr(self._v, self.changed_signal).emit()

class RenameCmd(_ViewCmd):
    def __init__(self, view, kind, oid, old, new):
        super().__init__(view, f"Rename {kind}")
        self.kind, self.oid, self.old, self.new = kind, oid, old, new

    def redo(self):
        self._v._apply_rename(self.kind, self.oid, self.new)
        self._done()

    def undo(self):
        self._v._apply_rename(self.kind, self.oid, self.old)
        self._done()

class ParamsCmd(_ViewCmd):
    """Fail/repair param triple, PM frequency, or node k."""

    def __init__(self, view, what, oid, old, new):
        super().__init__(view, f"Edit {what}")
        self.what, self.oid, self.old, self.new = what, oid, old, new

    def redo(self):
        self._v._apply_value(self.what, self.oid, self.new)
        self._done()

    def undo(self):
        self._v._apply_value(self.what, self.oid, self.old)
        self._done()

class DistCmd(_ViewCmd):
    """Distribution change (Data Tables): swaps FDist/RDist + the
    matching #FParam/#RParam counts in model and file row."""

    def __init__(self, view, bid, which, old, new):
        super().__init__(view, f"Change {which} distribution")
        self.bid, self.which = bid, which
        self.old, self.new = old, new  # (distro, nparams)

    def redo(self):
        self._v._apply_dist(self.bid, self.which, self.new)
        self._done()

    def undo(self):
        self._v._apply_dist(self.bid, self.which, self.old)
        self._done()

class LibUpdateCmd(_ViewCmd):
    """Raptor 7 library "update matching": rewrites the F/R distribution,
    param and stream COLUMNS of matched BLOCK_DATA rows AND the typed
    objects' fields, in one undoable step."""

    _TYPED = (("FDist", "fail_distro"), ("RDist", "repair_distro"),
              ("FStream", "fail_stream"), ("RStream", "repair_stream"))

    def __init__(self, view, section, entries):
        """entries: [(row_index, block_id, old_cols, new_cols)]"""
        super().__init__(view, "Update from library")
        self._section = section
        self.entries = entries

    def redo(self):
        self._apply(new=True)
        self._done()

    def undo(self):
        self._apply(new=False)
        self._done()

    def _apply(self, new: bool):
        for row_index, bid, old_cols, new_cols in self.entries:
            rows = self._section.rows
            if not (0 <= row_index < len(rows)):
                continue
            row = rows[row_index]
            cols = new_cols if new else old_cols
            row.update(cols)
            block = self._v.blocks.get(bid)
            if block is None:
                continue
            fresh = block_from_row(row)
            block.fail_distro = fresh.fail_distro
            block.fail_values = list(fresh.fail_values)
            block.repair_distro = fresh.repair_distro
            block.repair_values = list(fresh.repair_values)
            block.fail_stream = fresh.fail_stream
            block.repair_stream = fresh.repair_stream
            self._v._refresh_block_sublabels(bid)

class StreamCmd(_ViewCmd):
    """Random-stream change (Inspector). `which` is 'F', 'R', or 'FR'
    -- the Simulation Theory dialog writes both failure and repair
    streams from one System-letter combo, so a
    system pick lands as 'FR'."""

    def __init__(self, view, bid, which, old_pair, new):
        super().__init__(view, "Change random stream")
        self.bid, self.which = bid, which
        self.old_pair = old_pair          # (fail_stream, repair_stream)
        self.new = new                    # applied per `which`

    def redo(self):
        self._v._apply_streams(self.bid, *self._split(self.new))
        self._done()

    def undo(self):
        self._v._apply_streams(self.bid, *self.old_pair)
        self._done()

    @staticmethod
    def _split(which_and_val):
        """('F'|'R'|'FR', v) -> (f, r) pairs the applier understands."""
        which, v = which_and_val
        return {"F": (v, None), "R": (None, v),
                "FR": (v, v)}[which]

class MoveCmd(_ViewCmd):
    """One drag gesture: every selected element's displacement."""

    changed_signal = "layout_changed"
    needs_rule_check = False

    def __init__(self, view, moves):
        super().__init__(view, "Move elements")
        self.moves = moves  # [(kind, id, (old_sx, old_sy), (new_sx, new_sy))]

    def _place(self, poses):
        v = self._v
        v._user_arranged = True   # drags pin the level scale too
        # setPos() below fires itemChange() same as any other move, which
        # would re-snap these exact recorded positions through whatever
        # v._snap_state is active right now. Undo/redo must restore the
        # exact recorded coordinates, so snap is disabled for the
        # duration.
        saved_snap = (v._snap_state["x"], v._snap_state["y"])
        v._snap_state["x"] = v._snap_state["y"] = 0.0
        try:
            for kind, oid, (sx, sy) in poses:
                # Record the override before the item lookup: an
                # undo/redo replaying a move whose element now lives on
                # a hidden level has no item here, but the override
                # must still move (or the next rebuild resurrects the
                # pre-command position). Same math, no item needed.
                if kind == "block":
                    mx = (sx + BLOCK_W / 2) / v._scale + v._origin_x
                    my = v._flip_base - (sy + BLOCK_H / 2) / v._scale
                else:
                    mx = sx / v._scale + v._origin_x
                    my = v._flip_base - sy / v._scale
                v._pos_overrides[(kind, oid)] = (mx, my)
                item = (v._block_items.get(oid) if kind == "block"
                        else v._node_items.get(oid) if kind == "node"
                        else v._event_items.get(oid) if kind == "event"
                        else v._hier_items.get(oid))
                if item is None:
                    continue
                item.setPos(sx, sy)
                # Sync the live model coords (same math as sync_model_positions).
                if kind == "block":
                    b = v.blocks.get(oid)
                    if b:
                        b.x_position = (sx + BLOCK_W / 2) / v._scale + v._origin_x
                        b.y_position = v._flip_base - (sy + BLOCK_H / 2) / v._scale
                elif kind == "hier":
                    # Oval pos is its centre (write_back's hier pass uses
                    # the same scenePos -> model math).
                    h = v.hiers.get(oid)
                    if h is not None:
                        h.x_position = sx / v._scale + v._origin_x
                        h.y_position = v._flip_base - sy / v._scale
                else:
                    obj = v.nodes.get(oid) if kind == "node" else v.events.get(oid)
                    if obj is not None:
                        obj.x_position = sx / v._scale + v._origin_x
                        obj.y_position = v._flip_base - sy / v._scale
        finally:
            v._snap_state["x"], v._snap_state["y"] = saved_snap
        v._write_positions_to_rows()       # the rows move with the canvas
        for link in v._links:
            link._update_route()
        # Annotations anchored to these elements go with them; on undo too,
        # since their place is a function of the element's.
        follow = getattr(v, "follow_anchors", None)
        if follow is not None:
            follow({(kind, oid) for kind, oid, _ in poses})

    def redo(self):
        self._place(self.moves_new)
        self._done()

    def undo(self):
        self._place([(k, i, o) for k, i, o, _ in self.moves])
        self._done()

    @property
    def moves_new(self):
        return [(k, i, n) for k, i, _, n in self.moves]

class BulkGeometryCmd(_ViewCmd):
    """Undoable bulk placement (Align / Distribute / Auto Layout): the
    old and new scene positions of every affected item. Captured at the
    item level because these operations place items directly rather than
    through MoveCmd; without a command Ctrl+Z could not undo them."""

    changed_signal = "layout_changed"
    needs_rule_check = False

    def __init__(self, view, text, old, new):
        super().__init__(view, text)
        # [(item, QPointF), ...]
        self.old = list(old)
        self.new = list(new)

    def _place(self, states):
        v = self._v
        v._user_arranged = True
        saved_snap = (v._snap_state["x"], v._snap_state["y"])
        v._snap_state["x"] = v._snap_state["y"] = 0.0
        try:
            for item, pos in states:
                try:
                    if item.scene() is not None:
                        item.setPos(pos)
                except RuntimeError:
                    continue   # deleted C++ object
        finally:
            v._snap_state["x"], v._snap_state["y"] = saved_snap
        v.sync_model_positions()
        follow = getattr(v, "follow_anchors", None)
        if follow is not None:
            follow()

    def redo(self):
        self._place(self.new)
        self._done()

    def undo(self):
        self._place(self.old)
        self._done()

class LayoutCmd(_ViewCmd):
    """Auto Layout as one undo step: the level's model-unit positions and
    the canvas transform, before and after (`DiagramView._layout_state`).

    Not `BulkGeometryCmd`: Auto Layout re-derives the scale and rebuilds
    every item, so an item-level record pointed at deleted items and put
    old-scale pixel positions into the new scale -- undo scrambled the
    diagram. Model units survive the rebuild. Pushed after the layout is
    done, so the first redo (QUndoStack.push calls it) does nothing."""

    changed_signal = "layout_changed"
    needs_rule_check = False

    def __init__(self, view, text, before, after):
        super().__init__(view, text)
        self.before, self.after = before, after
        self._applied = True

    def redo(self):
        if self._applied:
            self._applied = False
            return
        self._v._restore_layout(self.after)
        self._done()

    def undo(self):
        self._v._restore_layout(self.before)
        self._done()


class AddCmd(_ViewCmd):
    """Element creation: re-inserts / removes its cloned file rows."""

    def __init__(self, view, kind, rows):
        super().__init__(view, f"Add {kind}")
        self.kind = kind
        self.rows = [dict(r) for r in rows]
        self.ids = None

    def redo(self):
        id_col = {"block": ("BLOCK_DATA", "ID"), "node": ("NODE_DATA", "ID"),
                  "event": ("EVENT_DATA", "ID"),
                  "link": ("LINK_DATA", "ID"),
                  "hier": ("HIERARCHY_DATA", "ID")}[self.kind]
        section = self._v._rbd_ref.sections[id_col[0]]
        for row in self.rows:
            if row not in section.rows:
                section.rows.append(row)
        inserter = getattr(self._v, f"_insert_{self.kind}")
        self.ids = [inserter(row) for row in self.rows]
        if self.kind in ("block", "node", "event"):
            # The canvas snaps a new element to the grid; the row keeps the
            # snapped position too (it held the unsnapped one until the
            # next write-back, so the rows and the canvas disagreed).
            self._v.sync_model_positions()
            for oid in self.ids:
                self._v._editor.write_element(self._v._rbd_ref, self.kind,
                                              oid)
        self._done()

    def undo(self):
        id_col = {"block": ("BLOCK_DATA", "ID"), "node": ("NODE_DATA", "ID"),
                  "event": ("EVENT_DATA", "ID"),
                  "link": ("LINK_DATA", "ID"),
                  "hier": ("HIERARCHY_DATA", "ID")}[self.kind]
        section = self._v._rbd_ref.sections[id_col[0]]
        ids = set(self.ids or [])
        section.rows[:] = [r for r in section.rows
                           if _safe_int(r.get(id_col[1])) not in ids]
        remover = getattr(self._v, f"_remove_{self.kind}")
        for oid in ids:
            remover(oid)
        self._done()

class DeleteCmd(_ViewCmd):
    """Deletion of any mix of blocks/nodes/events/links; undo restores
    the exact file rows and rebuilds their canvas items."""

    def __init__(self, view, block_rows, node_rows, event_rows, link_rows):
        super().__init__(view, "Delete selection")
        self.block_rows = [dict(r) for r in block_rows]
        self.node_rows = [dict(r) for r in node_rows]
        self.event_rows = [dict(r) for r in event_rows]
        self.link_rows = [dict(r) for r in link_rows]

    def redo(self):
        v = self._v
        dead_b = {_safe_int(r.get("ID")) for r in self.block_rows}
        dead_n = {_safe_int(r.get("ID")) for r in self.node_rows}
        dead_e = {_safe_int(r.get("ID")) for r in self.event_rows}
        dead_l = {_safe_int(r.get("ID")) for r in self.link_rows}
        v._remove_ids(dead_b, dead_n, dead_e, dead_l)
        self._done()

    def undo(self):
        v = self._v
        for r in self.block_rows:
            v._rbd_ref.sections["BLOCK_DATA"].rows.append(dict(r))
        for r in self.node_rows:
            v._rbd_ref.sections["NODE_DATA"].rows.append(dict(r))
        for r in self.event_rows:
            v._rbd_ref.sections["EVENT_DATA"].rows.append(dict(r))
        for r in self.link_rows:
            v._rbd_ref.sections["LINK_DATA"].rows.append(dict(r))
        # Insert elements first, then parse the restored LINK rows into
        # real Link objects + canvas items (_insert_link does both).
        for r in self.block_rows:
            v._insert_block(r)
        for r in self.node_rows:
            v._insert_node(r)
        for r in self.event_rows:
            v._insert_event(r)
        for r in self.link_rows:
            v._insert_link(r)
        self._done()

class DepResetCmd(_ViewCmd):
    """Reset each dependent element to independent -- port of Raptor's
    `ResetDepNums`, which runs when a
    delete removes an element other elements' DependencyNum still points
    at: every block/event/node whose DependencyNum matched the deleted
    item's Id goes back to independent (0, no type). One undoable step
    alongside DeleteCmd inside a macro, so Undo brings both the deleted
    element and the dependents' old dependency settings back."""

    #: section name -> (typed-objects dict attr, dep column, type column)
    _SECTIONS = (
        ("BLOCK_DATA", "blocks", "DepNum", "DepType"),
        ("NODE_DATA", "nodes", "depNum", "depType"),
        ("EVENT_DATA", "events", "depNum", "depType"),
    )

    def __init__(self, view, entries):
        """entries: [(section, id, old_dep_num, old_dep_type)]"""
        super().__init__(view, "Reset dependent elements to independent")
        self.entries = list(entries)

    def _lookup(self, section, oid):
        attr, _, dep_col, type_col = next(
            s for s in self._SECTIONS if s[0] == section)
        sec = self._v._rbd_ref.sections.get(section)
        row = None
        if sec is not None:
            for r in sec.rows:
                if _safe_int(r.get("ID")) == oid:
                    row = r
                    break
        return attr, row, dep_col, type_col

    def _apply(self, entries):
        for section, oid, dep_num, dep_type in entries:
            attr, row, dep_col, type_col = self._lookup(section, oid)
            obj = getattr(self._v, attr, {}).get(oid)
            if obj is not None:
                obj.dependency_num = dep_num
                obj.dep_type = dep_type
            if row is not None:
                row[dep_col] = str(dep_num)
                row[type_col] = dep_type
            item = getattr(self._v, f"_{attr[:-1]}_items", {}).get(oid)
            if item is not None and hasattr(item, "set_dependency_badge"):
                item.set_dependency_badge(
                    self._v._dependency_visual(dep_num, dep_type))
        self._v._apply_dependency_badges()

    def redo(self):
        # forward: everything back to independent
        self._apply([(s, o, 0, "") for s, o, _, _ in self.entries])
        self._done()

    def undo(self):
        self._apply(self.entries)
        self._done()

class DissolveCmd(_ViewCmd):
    """Delete a hierarchy oval: its child elements promote to the
    enclosing level, the oval + In/Out markers are removed, and links
    that referenced the oval/markers are cut (same convention as
    move-out -- re-wire manually). One undo step restores everything:
    row positions, parent rewrites and link re-parenting."""

    def __init__(self, view, hier_row, marker_rows, removed_links,
                 parent_rewrites, link_pid_rewrites,
                 rewired_links=()):
        super().__init__(view, "Delete hierarchy")
        self.hier_row = dict(hier_row)
        self.marker_rows = [dict(r) for r in marker_rows]
        self.removed_links = [(dict(r), i) for r, i in removed_links]
        # [(section, id, old_parent, new_parent)]
        self.parent_rewrites = list(parent_rewrites)
        # [(link_id, old_pid, new_pid)]
        self.link_pid_rewrites = list(link_pid_rewrites)
        # [(link_id, {col: old}, {col: new})] -- boundary rewiring: an
        # oval-referencing link `X -> Hier` reconnects to the element
        # the In-marker fed (`X -> S_in`), and `Hier -> X` reconnects
        # from the element that fed the Out-marker (`S_out -> X`), so a
        # wired hierarchy dissolves into a still-connected flow instead
        # of two dead ends.
        self.rewired_links = [(lid, dict(old), dict(new))
                              for lid, old, new in rewired_links]
        self.hier_id = _safe_int(hier_row.get("ID"))

    @staticmethod
    def _row_by_id(section, oid):
        for r in section.rows:
            if _safe_int(r.get("ID")) == oid:
                return r
        return None

    def _apply(self, forward: bool):
        v = self._v
        rbd = v._rbd_ref
        hier_section = rbd.sections["HIERARCHY_DATA"]
        if forward:
            rows = hier_section.rows
            rows[:] = [r for r in rows
                       if _safe_int(r.get("ID")) != self.hier_id]
            dead_n = {_safe_int(r["ID"]) for r in self.marker_rows}
            node_section = rbd.sections["NODE_DATA"]
            node_section.rows[:] = [r for r in node_section.rows
                            if _safe_int(r.get("ID")) not in dead_n]
            dead_l = {_safe_int(r["ID"]) for r, _i in self.removed_links}
            link_section = rbd.sections["LINK_DATA"]
            link_section.rows[:] = [r for r in link_section.rows
                            if _safe_int(r.get("ID")) not in dead_l]
            for section, oid, _old, new in self.parent_rewrites:
                row = self._row_by_id(rbd.sections[section], oid)
                if row is not None:
                    col = ("parent" if section == "NODE_DATA"
                           else "ParentId")
                    row[col] = str(new)
            for lid, _old, new in self.link_pid_rewrites:
                row = self._row_by_id(link_section, lid)
                if row is not None:
                    row["pID"] = str(new)
            for lid, _old, new in self.rewired_links:
                row = self._row_by_id(link_section, lid)
                if row is not None:
                    row.update(new)
        else:
            hier_section.rows.append(dict(self.hier_row))
            node_section = rbd.sections["NODE_DATA"]
            for r in self.marker_rows:
                node_section.rows.append(dict(r))
            link_section = rbd.sections["LINK_DATA"]
            for lid, old, _new in self.rewired_links:
                row = self._row_by_id(link_section, lid)
                if row is not None:
                    row.update(old)
            for section, oid, old, _new in self.parent_rewrites:
                row = self._row_by_id(rbd.sections[section], oid)
                if row is not None:
                    col = ("parent" if section == "NODE_DATA"
                           else "ParentId")
                    row[col] = str(old)
            for lid, old, _new in self.link_pid_rewrites:
                row = self._row_by_id(link_section, lid)
                if row is not None:
                    row["pID"] = str(old)
            for r, idx in sorted(self.removed_links, key=lambda p: p[1]):
                link_section.rows.insert(min(idx, len(link_section.rows)), dict(r))
        v._rebuild_scene()

    def redo(self):
        self._apply(True)
        self._done()

    def undo(self):
        self._apply(False)
        self._done()

class ReParentCmd(_ViewCmd):
    """Drop selection onto a hierarchy oval: the elements move under that hier's
    ParentId, links fully inside the selection travel with it, and links
    that would cross the new boundary are removed -- the tutorial then
    has the user wire the In/Out markers manually inside the level. One
    undo step."""

    def __init__(self, view, hid, parent_moves, removed_links,
                 internal_links=()):
        super().__init__(view, "Move into hierarchy")
        self.hid = hid
        # [(section_name, element_id, old_parent_value)]
        self.parent_moves = list(parent_moves)
        # [(row_copy, original_index)]
        self.removed_links = [(dict(r), i) for r, i in removed_links]
        # Fully-internal links: [(row_copy, index)] -- their pID flips
        # to the new hier on redo, back on undo.
        self.internal_links = [(dict(r), i) for r, i in internal_links]

    @staticmethod
    def _row_by_id(section, oid: int):
        for r in section.rows:
            if _safe_int(r.get("ID")) == oid:
                return r
        return None

    def _apply(self, forward: bool):
        v = self._v
        rbd = v._rbd_ref
        new_parent = str(self.hid) if forward else None
        for section, oid, old in self.parent_moves:
            row = self._row_by_id(rbd.sections[section], oid)
            if row is not None:
                row[_PARENT_COLS[section]] = (
                    new_parent if forward else old)
        if forward:
            dead = {_safe_int(r["ID"]) for r, _ in self.removed_links}
            rows = rbd.sections["LINK_DATA"].rows
            rows[:] = [r for r in rows
                       if _safe_int(r.get("ID")) not in dead]
            for row, _idx in self.internal_links:
                live = self._row_by_id(rbd.sections["LINK_DATA"],
                                       _safe_int(row["ID"]))
                if live is not None:
                    live["pID"] = str(self.hid)
        else:
            for row, _idx in self.internal_links:
                live = self._row_by_id(rbd.sections["LINK_DATA"],
                                       _safe_int(row["ID"]))
                if live is not None:
                    live["pID"] = row["pID"]
            rows = rbd.sections["LINK_DATA"].rows
            for row, idx in sorted(self.removed_links, key=lambda p: p[1]):
                rows.insert(min(idx, len(rows)), dict(row))
        v._rebuild_scene()

    def redo(self):
        self._apply(True)
        self._done()

    def undo(self):
        self._apply(False)
        self._done()

class TableCellCmd(_ViewCmd):
    """Single-cell edit in a name-keyed row (spare/resource pools,
    triggers -- sections with no ID column)."""

    def __init__(self, view, section, key_col, key_val, col, old, new):
        super().__init__(view, f"Edit {key_val}.{col}")
        self.section, self.key_col = section, key_col
        self.key_val, self.col, self.old, self.new = \
            key_val, col, old, new

    def _set(self, value):
        v = self._v
        sec = v._rbd_ref.sections.get(self.section)
        if sec is None:
            return
        for r in sec.rows:
            if r.get(self.key_col) == self.key_val:
                r[self.col] = value
                break
        self._done()

    def redo(self):
        self._set(self.new)

    def undo(self):
        self._set(self.old)

class IdCellCmd(_ViewCmd):
    """Single-cell edit in an ID-keyed row (LINK_DATA's flow fields:
    a node's per-link Nominal/Max Flow and Capacity Priority, manual
    Raptor 7 -- undoable)."""

    def __init__(self, view, section, id_col, oid, col, old, new,
                 undo_text="Edit flow field"):
        super().__init__(view, undo_text)
        self.section, self.id_col, self.oid = section, id_col, oid
        self.col, self.old, self.new = col, old, new

    def _set(self, value):
        v = self._v
        sec = v._rbd_ref.sections.get(self.section)
        if sec is None:
            return
        for r in sec.rows:
            if _safe_int(r.get(self.id_col)) == self.oid:
                r[self.col] = value
                break
        self._done()

    def redo(self):
        self._set(self.new)

    def undo(self):
        self._set(self.old)

class RowCellCmd(_ViewCmd):
    """Single-cell edit in a POSITION-addressed row (SYSTEM_DATA's single
    settings row has no key column)."""

    def __init__(self, view, section, row_index, col, old, new):
        super().__init__(view, f"Edit system {col}")
        self.section, self.row_index = section, row_index
        self.col, self.old, self.new = col, old, new

    def _set(self, value):
        v = self._v
        sec = v._rbd_ref.sections.get(self.section)
        if sec is None or not (0 <= self.row_index < len(sec.rows)):
            return
        sec.rows[self.row_index][self.col] = value
        self._done()

    def redo(self):
        self._set(self.new)

    def undo(self):
        self._set(self.old)

class TableRowCmd(_ViewCmd):
    """Add or remove a whole keyed row (pool/trigger creation and
    deletion). Matches the live row by object identity -- names are not
    unique-safe and copies would go stale."""

    def __init__(self, view, section, row, index, add: bool):
        super().__init__(view, "Add row" if add else "Delete row")
        self.section = section
        #: For deletes this is the live row object from the section;
        #: for adds it's the template that becomes the live row.
        self.row = row if not add else dict(row)
        self.index = index
        self.add = add
        self._live = None if add else row

    def _insert(self):
        sec = self._v._rbd_ref.sections.get(self.section)
        if sec is None:
            return
        self._live = dict(self.row)
        sec.rows.insert(min(self.index, len(sec.rows)), self._live)

    def _remove(self):
        sec = self._v._rbd_ref.sections.get(self.section)
        if sec is None or self._live is None:
            return
        try:
            sec.rows.remove(self._live)
        except ValueError:
            pass

    def redo(self):
        self._insert() if self.add else self._remove()
        self._done()

    def undo(self):
        self._remove() if self.add else self._insert()
        self._done()

def phase_fields_from_tokens(tokens: dict) -> tuple[list, list, list]:
    """Parse a row's `P 001`/`P001` cells (key -> token) into the live object's `(phase_type, phase_value, phase)`."""
    types, values, node_ks = [], [], []

    def key_num(k: str) -> int:
        digits = k[2:] if k[1] == " " else k[1:]
        return int(digits)
    for key in sorted(tokens, key=key_num):
        tok = tokens[key]
        code, _, val = tok.partition(":")
        if code.strip() and val:
            types.append(code.strip())
            try:
                values.append(float(val))
            except ValueError:
                values.append(0.0)
        stripped = tok.strip()
        if stripped == "C":
            node_ks.append(-1)
        elif stripped == "L":
            node_ks.append(0)
        else:
            try:
                node_ks.append(int(stripped))
            except ValueError:
                pass
    return types, values, node_ks


class PhaseCmd(_ViewCmd):
    """Element-level phase assignment: sets/clears the
    row's `Phasing` flag and its ragged `P 001…` token cells, then
    updates the live model object's parsed phase fields."""

    def __init__(self, view, kind, oid, section, parent_col,
                 old_state, new_state):
        super().__init__(view, f"Set phasing ({kind})")
        self.kind, self.oid = kind, oid
        self.section, self.parent_col = section, parent_col
        # old/new state: {"flag": "0"/"1", "tokens": {"P 001": "A: 1.000"}}
        self.states = [old_state, new_state]

    @staticmethod
    def _token_keys(row):
        # Blocks/events: "P 001" (with space); nodes: "P001" (bare int).
        return [k for k in row
                if (k.startswith("P ") and k[2:].isdigit())
                or (k.startswith("P") and k[1:].isdigit())]

    def _apply(self, forward: bool):
        v = self._v
        sec = v._rbd_ref.sections.get(self.section)
        if sec is None:
            return
        state = self.states[1 if forward else 0]
        row = next((r for r in sec.rows
                    if _safe_int(r.get("ID")) == self.oid), None)
        if row is None:
            return
        flag_col = state.get("flag_col", "Phasing")
        row[flag_col] = state["flag"]
        for key in self._token_keys(row):
            del row[key]
        for key, tok in state["tokens"].items():
            row[key] = tok
        obj = {"block": v.blocks.get(self.oid),
               "event": v.events.get(self.oid),
               "node": v.nodes.get(self.oid),
               "hier": v.hiers.get(self.oid)}[self.kind]
        if obj is not None and hasattr(obj, "uses_phasing"):
            obj.uses_phasing = state["flag"] == "1"
        # Live hatch overlay -- update the on-screen item
        # directly rather than waiting for a scene rebuild, if this
        # element is on the currently-rendered level.
        canvas_item = {"block": v._block_items, "event": v._event_items,
                       "node": v._node_items,
                       "hier": v._hier_items}[self.kind].get(self.oid)
        if canvas_item is not None:
            canvas_item.set_phased(state["flag"] == "1")
        if obj is None:
            return
        types, values, node_ks = phase_fields_from_tokens(state["tokens"])
        if hasattr(obj, "phase_type"):
            obj.phase_type = types
            obj.phase_value = values
        if hasattr(obj, "phase"):
            obj.phase = node_ks

    def redo(self):
        self._apply(True)
        self._done()

    def undo(self):
        self._apply(False)
        self._done()

class MoveOutCmd(_ViewCmd):
    """Move selected elements out of the current hierarchy back to the
    parent level (the inverse of dropping them onto an oval): parents
    reset to the grandparent id, links fully inside the moving set stay
    with it, links that would cross the new boundary are cut (the
    tutorial wires In/Out markers manually). One undo step."""

    def __init__(self, view, target_parent, parent_moves, removed_links,
                 internal_links=()):
        super().__init__(view, "Move out of hierarchy")
        self.target_parent = str(target_parent)
        self.parent_moves = list(parent_moves)
        self.removed_links = [(dict(r), i) for r, i in removed_links]
        self.internal_links = [(dict(r), i) for r, i in internal_links]

    @staticmethod
    def _row_by_id(section, oid):
        for r in section.rows:
            if _safe_int(r.get("ID")) == oid:
                return r
        return None

    def _apply(self, forward: bool):
        v = self._v
        rbd = v._rbd_ref
        new_parent = self.target_parent if forward else None
        for section, oid, old in self.parent_moves:
            row = self._row_by_id(rbd.sections[section], oid)
            if row is not None:
                row[_PARENT_COLS[section]] = (
                    new_parent if forward else old)
        if forward:
            dead = {_safe_int(r["ID"]) for r, _ in self.removed_links}
            rows = rbd.sections["LINK_DATA"].rows
            rows[:] = [r for r in rows
                       if _safe_int(r.get("ID")) not in dead]
            for row, _idx in self.internal_links:
                live = self._row_by_id(rbd.sections["LINK_DATA"],
                                       _safe_int(row["ID"]))
                if live is not None:
                    live["pID"] = self.target_parent
        else:
            for row, _idx in self.internal_links:
                live = self._row_by_id(rbd.sections["LINK_DATA"],
                                       _safe_int(row["ID"]))
                if live is not None:
                    live["pID"] = row["pID"]
            rows = rbd.sections["LINK_DATA"].rows
            for row, idx in sorted(self.removed_links, key=lambda p: p[1]):
                rows.insert(min(idx, len(rows)), dict(row))
        v._rebuild_scene()

    def redo(self):
        self._apply(True)
        self._done()

    def undo(self):
        self._apply(False)
        self._done()

class CollapseCmd(_ViewCmd):
    """Collapse selection into a new hierarchy: the selected elements move under the new
    ParentId, fully-internal links are re-parented with them, and each
    boundary link splits in two -- an inside half ending at the Out
    marker (or starting at the In marker) plus an outside half touching
    the Hier oval (`Node->Hier` / `Hier->Node` rows on the parent
    level). One undo step restores the exact prior state -- these row
    edits have no finer-grained command of their own, so the command
    snapshots everything it touches."""

    def __init__(self, view, hrows, parent_moves, link_ops,
                 moved_annotations=None):
        super().__init__(view, "Collapse into hierarchy")
        # [(anno_id, old_parent)] — selected annotations follow in
        self.moved_annotations = list(moved_annotations or [])
        self._old_level = getattr(view, "_current_parent", 0)
        # [hier_row, in_row, out_row] -- fresh rows to add.
        self.hrows = [dict(r) for r in hrows]
        # [(section_name, element_id, old_parent_value)]
        self.parent_moves = list(parent_moves)
        # [("del", old_link_row, original_index) | ("add", new_link_row, -1)]
        self.link_ops = [(op, dict(r), i) for op, r, i in link_ops]

    @staticmethod
    def _row_by_id(section, oid: int):
        for r in section.rows:
            if _safe_int(r.get("ID")) == oid:
                return r
        return None

    def _apply(self, forward: bool):
        v = self._v
        rbd = v._rbd_ref
        hier_section = rbd.sections["HIERARCHY_DATA"]
        node_section = rbd.sections["NODE_DATA"]
        link_section = rbd.sections["LINK_DATA"]
        hid = _safe_int(self.hrows[0].get("ID"))
        if forward:
            hier_section.rows.append(dict(self.hrows[0]))
            node_section.rows.extend(dict(r) for r in self.hrows[1:])
            for section, oid, _old in self.parent_moves:
                row = self._row_by_id(rbd.sections[section], oid)
                if row is not None:
                    row[_PARENT_COLS[section]] = str(hid)
            # Order matters: deletions before additions (a split reuses
            # the original link's ID for its outside half).
            for op, row, _idx in sorted(self.link_ops,
                                        key=lambda o: (o[0] != "del",
                                                       o[2])):
                if op == "del":
                    link_section.rows[:] = [
                        r for r in link_section.rows
                        if _safe_int(r.get("ID")) != _safe_int(row["ID"])]
                else:
                    link_section.rows.append(dict(row))
            for aid, _np in self.moved_annotations:
                for a in getattr(v, "annotations", []):
                    if int(a.get("id", 0)) == aid:
                        a["parent"] = hid
                        break
        else:
            dead_h = {_safe_int(r.get("ID")) for r in self.hrows[:1]}
            dead_n = {_safe_int(r.get("ID")) for r in self.hrows[1:]}
            hier_section.rows[:] = [r for r in hier_section.rows
                            if _safe_int(r.get("ID")) not in dead_h]
            node_section.rows[:] = [r for r in node_section.rows
                            if _safe_int(r.get("ID")) not in dead_n]
            for section, oid, old_parent in self.parent_moves:
                row = self._row_by_id(rbd.sections[section], oid)
                if row is not None:
                    row[_PARENT_COLS[section]] = str(old_parent)
            # undo: restore annotation parent levels
            for aid, _np in self.moved_annotations:
                for a in getattr(v, "annotations", []):
                    if int(a.get("id", 0)) == aid:
                        a["parent"] = self._old_level
                        break
            added_ids = {_safe_int(r["ID"]) for op, r, _ in self.link_ops
                         if op == "add"}
            keep = [r for r in link_section.rows
                    if _safe_int(r.get("ID")) not in added_ids]
            # Splice replaced rows back at their original positions so
            # the undone file matches the pre-collapse byte order.
            for _op, row, idx in sorted(
                    (o for o in self.link_ops if o[0] == "del"),
                    key=lambda o: o[2]):
                keep.insert(min(idx, len(keep)), dict(row))
            link_section.rows[:] = keep
        v._rebuild_scene()

    def redo(self):
        self._apply(True)
        self._done()

    def undo(self):
        self._apply(False)
        self._done()


def values_differ(old, new) -> bool:
    """Does this cell edit need an undo entry? (review E-06)

    An `.rbd` row stores whatever text was written, so the same number can
    read back as `"20.000000"` where the UI holds `20`. Comparing with `==`
    alone would push a `RowCellCmd` for a no-op edit and dirty the model,
    so the comparison falls back to numeric equality and treats a
    formatting difference as no change.

    This rule was written out three times — `run_experiment_mixin.py` twice
    (its own self-clone) and `dialogs.py` once — each with its own copy of
    the `try/except (ValueError, TypeError)` guard, which is why it drifted:
    a caller that forgot the `TypeError` arm raised on a `None` old value
    rather than falling back to a string compare.

    Deliberately NOT applied at the `pool_dialogs` or `main_window` sites.
    Those compare with plain `!=` against a value they have already
    normalised, so they are not making this judgement at all; folding them
    in would have changed what they do rather than deduplicating it.
    """
    if old == new:
        return False
    try:
        return float(old) != float(new)
    except (ValueError, TypeError):
        return True


def apply_row_edits(view, section, row_index, row, edits,
                    macro_label: str) -> int:
    """Push one `RowCellCmd` per changed cell, as ONE undo step.

    `edits` is an iterable of `(col, new)` pairs — or `(col, old, new)`,
    in which case `old` is checked against the row first and the edit is
    skipped when the caller has already established it is a change. Returns
    the number of commands pushed, so a caller can report "no changes" or
    keep counting for its status bar.

    This is the whole *pattern*, not just its equality test: macro
    bookkeeping that must not be left dangling if a cell write raises, the
    skip-if-unchanged rule, and the write-through to the live row after the
    command is pushed. Every site that hand-rolled it had to get all three
    right, and each had its own copy to keep right.

    `RowCellCmd._set` writes the row itself on redo, so writing `row[col]`
    here as well is deliberate: the live model must reflect the edit before
    the surrounding code reads it back.
    """
    stack = view._stack
    # Collect first: beginMacro/endMacro with nothing inside still pushes
    # an empty macro, which made a no-op "edit" add an undo step and mark
    # the model dirty (System streams did exactly that on editingFinished).
    changes = []
    for edit in edits:
        col, new = edit[0], edit[-1]
        old = edit[1] if len(edit) == 3 else row.get(col, "")
        if values_differ(old, new):
            changes.append((col, old, new))
    if not changes:
        return 0
    stack.beginMacro(macro_label)
    try:
        for col, old, new in changes:
            stack.push(RowCellCmd(view, section, row_index, col, old, new))
            row[col] = new
    finally:
        stack.endMacro()
    return len(changes)


class PhaseStructureCmd(_ViewCmd):
    """Insert, delete or clear phases (`core.fileio.phase_edit`): one undo step that restores every phase row, `P`
    column and token exactly. The edit runs the first time `redo` is called; the live objects' parsed phase
    fields are rebuilt from the rows afterwards."""

    _SECTIONS = ("PHASES", "BLOCK_DATA", "EVENT_DATA", "NODE_DATA")

    def __init__(self, view, text, edit):
        super().__init__(view, text)
        self._edit = edit
        self._before = None
        self._after = None
        self.result = None

    def _snapshot(self):
        import copy
        rbd = self._v._rbd_ref
        return ({n: (list(rbd.sections[n].columns), copy.deepcopy(rbd.sections[n].rows))
                 for n in self._SECTIONS if n in rbd.sections}, rbd.counts.get("Phases"))

    def _restore(self, snap):
        import copy
        rbd = self._v._rbd_ref
        sections, count = snap
        for n, (cols, rows) in sections.items():
            rbd.sections[n].columns[:] = cols
            rbd.sections[n].rows[:] = copy.deepcopy(rows)
        rbd.counts["Phases"] = count
        self._refresh_live()

    def _refresh_live(self):
        v = self._v
        rbd = v._rbd_ref
        for sec_name, live, _flag_col in (("BLOCK_DATA", v.blocks, "Phasing"), ("EVENT_DATA", v.events, "Phasing"),
                                         ("NODE_DATA", v.nodes, "Phases")):
            sec = rbd.sections.get(sec_name)
            if sec is None:
                continue
            for row in sec.rows:
                obj = live.get(_safe_int(row.get("ID")))
                if obj is None:
                    continue
                toks = {k: row[k] for k in PhaseCmd._token_keys(row)}
                types, values, ks = phase_fields_from_tokens(toks)
                if hasattr(obj, "phase_type"):
                    obj.phase_type, obj.phase_value = types, values
                if hasattr(obj, "phase"):
                    obj.phase = ks

    def redo(self):
        if self._before is None:
            self._before = self._snapshot()
            self.result = self._edit(self._v._rbd_ref)
            self._after = self._snapshot()
            self._refresh_live()
        else:
            self._restore(self._after)
        self._done()

    def undo(self):
        self._restore(self._before)
        self._done()

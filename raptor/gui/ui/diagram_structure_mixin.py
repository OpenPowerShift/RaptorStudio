"""Structural edits: adding, connecting, inserting on links, hierarchies and events.

Expects on `self`: `_rbd`, the undo stack and the item tables."""
from __future__ import annotations


import copy
import itertools
import math
import os
import re
from typing import Any, cast

from shiboken6 import isValid
from PySide6.QtCore import (
    QPointF,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
)

from raptor.core.fileio.rbd import RbdFile
from raptor.core.fileio.rbdlib import dist_param_count as _dist_param_count
from raptor.core.fileio import view_transform
from raptor.core.edit import row_ops
from raptor.core.fileio.band_handover import handover_points
from raptor.gui.ui.diagram_items import routing_batch
from raptor.core.edit.hierarchy_plan import (apply_collapse_plan,
                                       collapse_new_violations,
                                       plan_collapse, plan_import)
from raptor.core.fileio.typed import (
    block_from_row, node_from_row, link_from_row, hier_from_row,
    cell_truthy,
)
from raptor.gui.ui import settings as app_settings
from raptor.gui.ui.diagram_common import (
    BLOCK_H,
    BLOCK_W,
    _safe_int,
)
from raptor.gui import link_insert
from raptor.gui.ui.diagram_commands import (
    AddCmd,
    LayoutCmd,
    CollapseCmd,
    MoveCmd,
    RouteGripCmd,
    SplitLinkCmd,
)
from raptor.gui.ui.diagram_items import (
    _BlockItem,
    _EventItem,
    _HierItem,
    _LinkItem,
    _RouteGripItem,
    _NodeItem,
    _view_status,
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


class StructureMixin(_Base):
    """Structural edits: adding, connecting, inserting on links, hierarchies and events."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _block_items: Any
        _event_items: Any
        _hier_items: Any
        _loading: Any
        _node_items: Any
        _snap_state: Any
        _user_arranged: Any
        blocks: Any
        events: Any
        hiers: Any
        links: Any
        nodes: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def _template_row(self, section_name: str, id_col: str):
        return row_ops.template_row(self._rbd_ref, section_name, id_col)

    @staticmethod
    def _sanitize_name(name: str) -> str:
        return row_ops.sanitize_name(name)

    def _next_id(self, section_name: str, id_col: str, existing: dict) -> int:
        return row_ops.next_id(self._rbd_ref, section_name, id_col, existing)

    def _insert_block(self, row: dict) -> int:
        """Build model object + canvas item from a full BLOCK_DATA row
        (used by AddCmd.redo and DeleteCmd.undo).

        Level-aware: `self.blocks`/`_block_items` are level-scoped, so a
        row parented to a different hierarchy is registered in the file
        only -- no canvas item, no dict entry. DeleteCmd.undo /
        AddCmd.undo tolerate the missing entries (`pop(iid, None)`), and
        entering that level rebuilds them from the rows as usual."""
        bid = _safe_int(row.get("ID"))
        block = block_from_row(row)
        if _safe_int(row.get("ParentId", "0")) != self._current_parent:
            return bid
        self.blocks[bid] = block
        item = _BlockItem(bid, block.name,
                          self._tx(block.x_position), self._ty(block.y_position),
                          on_moved=self._item_moved,
                          on_dep_hover=self._on_dep_hover)
        self._apply_block_labels(item, block)
        item.set_phased(block.uses_phasing)
        # A fresh add_block() always has dependency_num==0 (no badge),
        # but DeleteCmd.undo restores an existing block that may well
        # have had one.
        item.set_dependency_badge(
            self._dependency_visual(block.dependency_num, block.dep_type))
        self._scene.addItem(item)
        self._block_items[bid] = item
        if not self._defer_block_refresh:
            self.refresh_after_block_insert()
        return bid

    def refresh_after_block_insert(self) -> None:
        """What a block insert needs once per batch, not once per block."""
        self._refresh_lod()
        self._recount_link_ends()

    def add_block(self, name: str, model_x: float, model_y: float,
                  fdist: int | None = None,
                  fparam: float | None = None) -> int:
        """Create a block at model coordinates; defaults come from the
        Block Defaults unless given."""
        row = self._new_block_row(name, model_x, model_y, fdist, fparam)
        self._stack.push(AddCmd(self, "block", [row]))
        return _safe_int(row["ID"])

    def _new_block_row(self, name: str, model_x: float, model_y: float,
                       fdist: int | None = None,
                       fparam: float | None = None,
                       bid: int | None = None) -> dict:
        """A full BLOCK_DATA row for a fresh block (Block Defaults, every
        column a new block has, PM included), not yet added."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        if fdist is None:
            fdist = self.default_block_fdist
        fparams = list(self.default_block_fparams)
        if fparam is not None:
            fparams[0] = fparam
        repair_dist = self.default_block_rdist
        repair_params = list(self.default_block_rparams)
        sparing = self.default_block_sparing
        row = self._template_row("BLOCK_DATA", "ID") or {}
        if bid is None:
            bid = self._next_id("BLOCK_DATA", "ID", self.blocks)
        row["blockName"] = self._sanitize_name(name)
        row["ID"] = str(bid)
        # On the level being shown. The template row says 0, so a block
        # added inside a hierarchy used to land on the home level.
        row["ParentId"] = str(self._current_parent)
        row["xPos"] = f"{model_x:.6f}"
        row["yPos"] = f"{model_y:.6f}"
        row["FDist"] = str(fdist)
        # DELEGATED to the canonical arity table (`rbdlib`, sourced from
        # the legacy writer). This used to hard-code `"2" if fdist == 4`,
        # i.e. two parameters for every non-Exponential distribution --
        # but `DIST_NPARAMS` says Gamma (6), Pearson5 (11), Pearson6 (12),
        # Triangular (14) and Weibull (15) take THREE. So a Weibull placed
        # from the canvas wrote `#FParam = 2` into a file the Raptor 7 engine
        # reads out of bounds: exactly the failure the verification ladder
        # already records. One owner for "how many params does this
        # distribution take" (`api/build.py` uses the same table).
        row["#FParam"] = str(_dist_param_count(fdist))
        for i in range(3):
            row[f"FParam{i + 1}"] = f"{fparams[i] if i < _dist_param_count(fdist) else 0.0:.6f}"
        row["RDist"] = str(repair_dist)
        row["#RParam"] = str(_dist_param_count(repair_dist))
        for i in range(3):
            row[f"RParam{i + 1}"] = f"{repair_params[i] if i < _dist_param_count(repair_dist) else 0.0:.6f}"
        row["SpType"] = sparing
        return row

    def add_node(self, name: str, model_x: float, model_y: float,
                 node_type: int = 2) -> int:
        """Create a node (Regular by default) cloned from an existing node
        row of the same type."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        section = self._rbd_ref.sections["NODE_DATA"]
        # Clone a same-type row so type-specific columns are right.
        base = None
        for row in section.rows:
            try:
                if int(float(row.get("Type", "0"))) == node_type:
                    base = dict(row)
                    break
            except (ValueError, KeyError):
                continue
        if base is None and section.rows:
            base = dict(section.rows[0])
        if base is None:
            base = {"Name": "", "ID": "0", "xPos": "0", "yPos": "0",
                    "parent": "0", "Type": str(node_type), "k": "0",
                    "n": "0", "Out": "0", "Cold": "0", "PR": "0",
                    "Autos": "0", "k*": "0", "Phases": "0", "depNum": "0",
                    "depType": "None", "divFlow": "1", "fullflow": "1",
                    "repCap": "0", "repNA": "0"}
        nid = self._next_id("NODE_DATA", "ID", self.nodes)
        base["Name"] = self._sanitize_name(name)
        base["ID"] = str(nid)
        base["Type"] = str(node_type)
        base["parent"] = str(self._current_parent)   # the level shown
        base["xPos"] = f"{model_x:.6f}"
        base["yPos"] = f"{model_y:.6f}"
        if node_type == 2:
            # A new node is a fresh node: the clone above supplies the
            # columns, never another node's settings. Copying them gave a
            # new node Start's k=0 (or a neighbour's k=2, its dependency,
            # standby and phases), so its first incoming link was refused.
            # k starts at 1, one good path; the status bar says where to
            # set more.
            from raptor.core.fileio.rbdlib import make_node_row
            fresh = make_node_row(nid, base["Name"], 0, 0, k=1, n=0, out=0)
            for col in list(base):
                if re.fullmatch(r"P\s?\d{3}", col):
                    del base[col]
            for col in ("k", "n", "Out", "Cold", "PR", "Autos", "k*",
                        "Phases", "depNum", "depType", "divFlow",
                        "fullflow", "repCap", "repNA"):
                base[col] = fresh[col]
        self._stack.push(AddCmd(self, "node", [base]))
        return nid

    def _ensure_hier_section(self):
        """Models without hierarchies have no HIERARCHY_DATA section."""
        return row_ops.ensure_section(self._rbd_ref, "HIERARCHY_DATA")

    def _build_hierarchy_rows(self, name: str, model_x: float,
                              model_y: float, rbd=None):
        """Fresh (hier_row, in_row, out_row) for the current level.

        `rbd` defaults to the live model. Collapse passes a throwaway copy
        so id allocation and the section it may create stay off the real
        model until the whole edit has been validated."""
        parent = self._current_parent
        h = self.hiers.get(parent)
        return row_ops.build_hierarchy_rows(
            rbd if rbd is not None else self._rbd_ref,
            name=name, x=model_x, y=model_y, parent=parent,
            parent_level=h.level if h is not None else None,
            used_ids=(set(self.blocks) | set(self.nodes) | set(self.events)
                      | set(self.hiers)),
            node_ids=self.nodes)

    def add_hierarchy(self, name: str, model_x: float,
                      model_y: float) -> int:
        """Create a hierarchy at model coordinates: the HIERARCHY_DATA
        row plus its In/Out marker nodes (type 4/5, ParentId = the new
        hier's own id). One undo step via a stack macro (hier row + two
        node rows move together)."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        hier_row, in_row, out_row = self._build_hierarchy_rows(
            name, model_x, model_y)
        self._stack.beginMacro("Add hierarchy")
        try:
            self._stack.push(AddCmd(self, "hier", [hier_row]))
            self._stack.push(AddCmd(self, "node", [in_row, out_row]))
        finally:
            self._stack.endMacro()
        return _safe_int(hier_row["ID"])

    def import_rbd_as_hierarchy(self, path: str,
                                name: str | None = None) -> int:
        """Merge an existing .rbds or .rbd file in as one hierarchy: default name = the file's own
        name; the imported model's Start/End nodes become the wrapper's
        In/Out markers; every id is remapped into a fresh range; the
        imported home level hangs directly under the new hier. One macro
        undo step."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        # .rbds or .rbd, by extension: the legacy reader alone refused every
        # .rbds file at its first line.
        from raptor.core.fileio.model_file import load_model_file
        imported = load_model_file(path)
        # Its positions are in its own drawing units: re-express them in
        # this model's, or every spacing inside the hierarchy changes.
        view_transform.rebase(imported, view_transform.ensure(imported),
                              self._vt)
        if not name:
            stem = os.path.basename(path)
            name = os.path.splitext(stem)[0]
        # The wrapper sits one level below this one, and the imported
        # model's own hierarchies below that: all within the nesting limit.
        from raptor.core.input_rules import (
            MAX_HIERARCHY_DEPTH, hierarchy_depths)
        deepest = (self._current_depth() + 1
                   + max(hierarchy_depths(imported).values(), default=0))
        if deepest > MAX_HIERARCHY_DEPTH:
            raise ValueError(
                f"importing {name} here would nest hierarchies {deepest} "
                f"deep; at most {MAX_HIERARCHY_DEPTH} are allowed. Import it "
                "at a shallower level.")

        # Wrapper first so the import remap avoids its ids too.
        hier_row, in_row, out_row = self._build_hierarchy_rows(name, 50.0, 50.0)
        hid = _safe_int(hier_row["ID"])
        plan = plan_import(
            self._rbd_ref, imported, path, hid=hid,
            wrapper_ids=(hid, _safe_int(in_row["ID"]),
                         _safe_int(out_row["ID"])))

        # The oval lands at the host level's current view centre (the
        # imported content keeps its own coordinates on its own level;
        # only the wrapper oval shows here).
        centre = self.mapToScene(self.viewport().rect().center())
        hmodel_x, hmodel_y = self.scene_to_model(centre)
        hier_row, in_row, out_row = self._build_hierarchy_rows(
            name, hmodel_x, hmodel_y)

        # The imported Start/End become this hierarchy's In/Out markers.
        hier_row["in"], hier_row["out"] = str(plan.in_id), str(plan.out_id)
        parent = self._current_parent
        level = (self.hiers[parent].level + 1) if parent in self.hiers else 1
        hier_row["Parent"] = str(parent)
        hier_row["level"] = str(level)
        hier_rows, node_rows = plan.hier_rows, plan.node_rows
        block_rows, event_rows = plan.block_rows, plan.event_rows
        link_rows = plan.link_rows

        # A file's own content, not typed input: not checked against
        # Raptor 7's input rules (`rule_gate`).
        with self._stack.ungated():
            self._stack.beginMacro(f"Import {name} as hierarchy")
            try:
                self._stack.push(AddCmd(self, "hier",
                                        [hier_row] + hier_rows))
                # No wrapper in/out marker rows here: the imported model's
                # own Start/End were converted into the markers (node_rows
                # carries them, re-typed 4/5) and `hrow["in"]/["out"]`
                # reference them. Pushing `_build_hierarchy_rows`' own
                # in_row/out_row as well would leave an unreferenced
                # duplicate in/out pair.
                self._stack.push(AddCmd(self, "node", node_rows))
                if block_rows:
                    self._stack.push(AddCmd(self, "block", block_rows))
                if event_rows:
                    self._stack.push(AddCmd(self, "event", event_rows))
                if link_rows:
                    self._stack.push(AddCmd(self, "link", link_rows))
            finally:
                self._stack.endMacro()
        return hid

    def collapse_selection(self, name: str | None = None) -> int | None:
        """Collapse every selected block/node/event into a new hierarchy
        on this level. Internal links move into it;
        boundary links are replaced by Hier-referencing ones (the real
        file convention: `Node -> Hier` / `Hier -> Node` rows on the
        parent level, `pID` = parent). Returns the new hier id.

        The edit is built and validated on a copy first, then pushed as ONE
        undo command. A collapse whose boundary cannot be rebuilt, or whose
        result would add a rule violation (including the run-time
        connectivity rules), is refused atomically with a message: the live
        model, the undo stack and the annotations are left untouched."""
        self.structural_about_to_change.emit()   # draft before the command
        if self._rbd_ref is None:
            return None
        sel_b = [bid for bid, it in self._block_items.items()
                 if it.isSelected() and bid != 0]
        sel_n = [nid for nid, it in self._node_items.items()
                 if it.isSelected() and self.nodes[nid].type_node == 2]
        sel_e = [eid for eid, it in self._event_items.items()
                 if it.isSelected()]
        # A selected hierarchy oval moves in whole (with everything inside
        # it). It used to be ignored: the collapse wrapped only the other
        # elements, or miscounted the boundary and refused.
        sel_h = [hid for hid, it in self._hier_items.items()
                 if it.isSelected()]
        if not (sel_b or sel_n or sel_e or sel_h):
            return None
        keys = ({("Block", str(i)) for i in sel_b} | {("Node", str(i)) for i in sel_n}
                | {("Event", str(i)) for i in sel_e}
                | {("Hier", str(i)) for i in sel_h})
        crossing = [((r.get("FromType", "").replace("RBD", "", 1), str(r.get("FromID"))) in keys,
                     (r.get("ToType", "").replace("RBD", "", 1), str(r.get("ToID"))) in keys)
                    for r in self._rbd_ref.sections["LINK_DATA"].rows
                    if _safe_int(r.get("pID", "0")) == self._current_parent] \
            if "LINK_DATA" in self._rbd_ref.sections else []
        from raptor.core.input_rules import collapse_boundary_problem
        problem = collapse_boundary_problem(sum(1 for f, t in crossing if t and not f),
                                            sum(1 for f, t in crossing if f and not t))
        if problem:
            from raptor.gui.ui import msgbox
            msgbox.warning(self, "Collapse Selection", problem)
            return None
        if not name:
            name = f"Hierarchy_{len(self.hiers) + 1}"

        # Oval lands at the selection's centroid.
        points = []
        for items in ((self._block_items, sel_b), (self._node_items, sel_n),
                      (self._event_items, sel_e), (self._hier_items, sel_h)):
            table, ids = items
            for oid in ids:
                c = table[oid].sceneBoundingRect().center()
                points.append((c.x(), c.y()))
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        mx, my = self.scene_to_model(QPointF(cx, cy))

        # Build the whole edit against a throwaway copy of the model --
        # including the hierarchy rows (whose builder may create
        # HIERARCHY_DATA) -- and refuse before touching the live model
        # when the result would not validate. This is what keeps a failed
        # rewire from leaving the block after the new hierarchy with no
        # input (issue #72) and keeps the collapse one atomic undo step.
        preview = copy.deepcopy(self._rbd_ref)
        hier_row, in_row, out_row = self._build_hierarchy_rows(
            name, mx, my, rbd=preview)
        hid = _safe_int(hier_row["ID"])
        plan = plan_collapse(
            preview, hid=hid, in_id=_safe_int(hier_row["in"]),
            out_id=_safe_int(hier_row["out"]), parent=self._current_parent,
            blocks=sel_b, nodes=sel_n, events=sel_e, hiers=sel_h)
        parent_moves, link_ops = plan.parent_moves, plan.link_ops
        if plan.unrebuildable:
            from raptor.gui.ui import msgbox
            link_ids = ", ".join(
                str(_safe_int(r.get("ID"))) for r in plan.unrebuildable)
            msgbox.warning(
                self, "Collapse Selection",
                "The collapse was not applied: the selection is connected "
                f"by link(s) filed on another hierarchy level (ID {link_ids}), "
                "so the boundary cannot be rebuilt. Nothing was changed.")
            return None
        # Selected annotations at this level follow into the hierarchy
        # -- their parent level becomes the new hier id. The old parent
        # is recorded for undo; nothing is written here.
        moved_annos = []
        for a in getattr(self, "annotations", []):
            if int(a.get("parent", 0)) == self._current_parent:
                it = self._anno_items.get(int(a.get("id", 0)))
                if it is not None and it.isSelected():
                    old_parent = int(a.get("parent", 0))
                    moved_annos.append((int(a.get("id", 0)), old_parent, hid))
        selected: dict[str, list[int]] = {
            "BLOCK_DATA": sel_b, "NODE_DATA": sel_n, "EVENT_DATA": sel_e,
            "HIERARCHY_DATA": sel_h}
        apply_collapse_plan(
            preview, hrows=[hier_row, in_row, out_row],
            parent_moves=parent_moves, link_ops=link_ops,
            moved_annotations=moved_annos, level_moves=plan.level_moves)
        problems = collapse_new_violations(
            self._rbd_ref, preview, hid=hid, selected=selected,
            in_id=_safe_int(hier_row["in"]),
            out_id=_safe_int(hier_row["out"]))
        if problems:
            from raptor.core.input_rules import refusal_text
            from raptor.gui.ui import msgbox
            msgbox.warning(
                self, "Collapse Selection",
                "The collapse was not applied: it would leave the model "
                "invalid, so nothing was changed.\n\n"
                + refusal_text(problems)
                + "\n\nInclude the node where the paths split and the node "
                  "where they join so the boundary can be rebuilt.")
            return None
        self._stack.push(CollapseCmd(self, [hier_row, in_row, out_row],
                                     parent_moves, link_ops,
                                     moved_annotations=moved_annos,
                                     level_moves=plan.level_moves))
        return hid

    def _insert_node(self, row: dict) -> int:
        nid = _safe_int(row.get("ID"))
        node = node_from_row(row)
        # Drawn only on its own level, the rule a level rebuild follows: a
        # hierarchy's In/Out markers belong inside it, not beside its oval
        # on the parent level (an added or imported hierarchy showed them
        # there until the next rebuild).
        if _safe_int(row.get("parent", "0")) != self._current_parent:
            return nid   # another level's row -- file only (see _insert_block)
        self.nodes[nid] = node
        item = _NodeItem(nid, node.node_name, node.type_node,
                         self._tx(node.x_position), self._ty(node.y_position),
                         on_moved=self._item_moved,
                         on_dep_hover=self._on_dep_hover)
        item.set_phased(node.uses_phasing)
        item.set_dependency_badge(
            self._dependency_visual(node.dependency_num, node.dep_type))
        self._scene.addItem(item)
        self._node_items[nid] = item
        self._refresh_lod()
        self._recount_link_ends()
        return nid

    def _insert_hier(self, row: dict) -> int:
        hid = _safe_int(row.get("ID"))
        hier = hier_from_row(row)
        self.hiers[hid] = hier
        # Only the current level renders its own hierarchy ovals --
        # a newly created oval must appear immediately, but ovals
        # restored by Undo deep inside other levels stay hidden until
        # navigated to (same rule as load_rbd's rebuild).
        if hier.parent_id == self._current_parent:
            item = _HierItem(hid, hier.name,
                             self._tx(hier.x_position),
                             self._ty(hier.y_position),
                             on_moved=self._item_moved)
            item.set_phased(hier.uses_phasing)
            self._scene.addItem(item)
            self._hier_items[hid] = item
            self._refresh_lod()
        return hid

    def _remove_hier(self, oid: int):
        item = self._hier_items.pop(oid, None)
        if item is not None:
            self._scene.removeItem(item)
        self.hiers.pop(oid, None)

    def _link_section(self):
        if self._rbd_ref is None:
            return None
        return self._rbd_ref.sections.get("LINK_DATA")

    def _next_link_id(self) -> int:
        """The next free LINK_DATA id, across every hierarchy level."""
        section = self._link_section()
        if section is None:
            return 1
        nums = [_safe_int(row.get("ID")) for row in section.rows]
        return max(nums) + 1 if nums else 1

    def _make_link_row(self, from_type: str, from_id: int,
                       to_type: str, to_id: int, link_id: int) -> dict:
        """A new LINK_DATA row exactly as Raptor's Connect tool makes one.

        Real Raptor (IDisplay.mod, the connect handler) gives every drawn
        link fixed defaults -- ColdPri 50, CapPri 50, nomFlow -2, MaxFlow
        -1, AProb 1, AutoTime 0, ManTime 0 -- and parents it to the level
        that is open (`SetParentID(activeWindow)`). It does NOT copy
        another link's settings; only copy/paste carries a link's values.
        `make_link_row` holds those defaults (the API builder uses it too).
        """
        from raptor.core.fileio.rbdlib import make_link_row
        # File tokens: LINK_DATA stores unprefixed kinds ("Block",
        # "Node", "Hier") -- link_from_row adds the RBD prefix on parse.
        # Storing caller-style "RBDBlock" here would produce RBDRBDBlock
        # refs that the engine's forward walk rejects ("no outgoing link
        # found").
        return make_link_row(
            link_id, from_type.replace("RBD", "", 1), from_id,
            to_type.replace("RBD", "", 1), to_id,
            pid=_safe_int(self._current_parent))

    def connect_elements(self, from_type: str, from_id: int,
                         to_type: str, to_id: int) -> int | None:
        """Create a LINK_DATA row between two elements (e.g. Node->Block).
        Exact duplicates are ignored; a second outgoing link from the same
        element is allowed by the file format but the engine only ports
        one outgoing link per block/event -- callers surface that through
        `model_changed` -> Run's NotImplementedError dialog."""
        if self._rbd_ref is None:
            return None

        def same_ref(a: str, b: str) -> bool:
            # Parsed refs carry the model-class prefix ("RBDBlock") while
            # callers pass the file token ("Block") -- compare with the
            # prefix stripped, or the duplicate check is dead code and
            # repeated connect clicks stack identical links.
            return a.replace("RBD", "", 1) == b.replace("RBD", "", 1)

        # Exact duplicate?
        for existing_link in self.links:
            if (same_ref(existing_link.connect_f_ref, from_type)
                    and existing_link.connect_from_id == from_id
                    and same_ref(existing_link.connect_t_ref, to_type)
                    and existing_link.connect_to_id == to_id):
                return None
        section = self._link_section()
        if section is None:
            return None
        # `_next_link_id` allocates against EVERY link row, not
        # `self.links`: that list is level-scoped now (only links with both
        # ends on the visible level), so a new link at home could otherwise
        # reuse the id of a hidden-level link and write a duplicate
        # LINK_DATA id.
        num = self._next_link_id()
        row = self._make_link_row(from_type, from_id, to_type, to_id, num)
        before = len(section.rows)
        self._stack.push(AddCmd(self, "link", [row]))
        if len(section.rows) == before:
            return None             # the input-rule gate refused the link
        return num

    @staticmethod
    def _capacity_analysis_enabled(rbd: RbdFile | None) -> bool:
        section = rbd.sections.get("SYSTEM_DATA") if rbd is not None else None
        row = section.rows[0] if section is not None and section.rows else None
        return row is not None and cell_truthy(row.get("capacityAnalysis"))

    def insert_routing_node(self, link_num: int, model_x: float,
                            model_y: float, name: str | None = None):
        """Add a draggable routing point (dog-leg) to a link.

        This is drawing only: the link still connects the same two
        elements, and the point has no `NODE_DATA` row, no properties and
        no effect on EFPA, capacity flow or dependencies. One undo step.
        Returns the grip item, or None when the link is not on screen.
        """
        del name                      # kept for the context-menu call site
        if self._rbd_ref is None or isinstance(link_num, bool):
            return None
        try:
            link_id = int(link_num)
            model_x, model_y = float(model_x), float(model_y)
        except (TypeError, ValueError, OverflowError):
            return None
        if link_id <= 0 or not (math.isfinite(model_x)
                                and math.isfinite(model_y)):
            return None
        link = next((item for item in self._links
                     if item._link_num == link_id), None)
        if link is None:
            return None
        # The context menu passes model units; the grip lives in scene
        # coordinates. Without this conversion the point was drawn far off
        # the canvas -- selecting the menu entry appeared to do nothing.
        scene_x, scene_y = self._vt.to_scene(model_x, model_y)
        point = link.nearest_point(QPointF(scene_x, scene_y))
        entry = {
            "kind": "route_grip",
            "id": self._next_anno_id(),
            "parent": self._current_parent,
            "link": link_id,
            "x": round(point.x(), 2),
            "y": round(point.y(), 2),
            # Added, not yet dragged: the existing link path is unchanged.
            "active": False,
        }
        self._stack.push(RouteGripCmd(self, entry, add=True))
        grips = self._route_grips.get(link_id) or []
        return grips[-1] if grips else None

    def route_grip_by_entry(self, entry_id: int):
        """The live routing-point item with `entry_id`, or None."""
        for grips in self._route_grips.values():
            for grip in grips:
                if isValid(grip) and grip._entry_id == entry_id:
                    return grip
        return None

    def delete_route_grip(self, grip) -> None:
        """Delete one clicked routing point; the link itself is untouched."""
        if grip is None or self._rbd_ref is None:
            return
        entry = next((a for a in getattr(self._rbd_ref, "link_grips", [])
                      if int(a.get("id", 0)) == grip._entry_id), None)
        if entry is None:
            return
        self._stack.push(RouteGripCmd(self, entry, add=False))
        _view_status(self, "Routing point removed")

    def _apply_route_grip(self, entry: dict, add: bool) -> None:
        """Create or remove one GUI-only link grip (undo/redo body)."""
        aid = int(entry.get("id", 0))
        link_id = int(entry.get("link", 0))
        rbd = self._rbd_ref
        if rbd is None:
            return
        if add:
            if not any(int(a.get("id", 0)) == aid
                       for a in getattr(rbd, "link_grips", [])):
                rbd.link_grips.append(dict(entry))
            link = next((item for item in self._links
                         if item._link_num == link_id), None)
            if link is None:
                return
            grip = _RouteGripItem(
                link, QPointF(float(entry.get("x", 0.0)),
                              float(entry.get("y", 0.0))), entry_id=aid,
                active=bool(entry.get("active", True)))
            grip.setParentItem(link)
            link.add_grip(grip)
            self._route_grips.setdefault(link_id, []).append(grip)
        else:
            rbd.link_grips = [a for a in getattr(rbd, "link_grips", [])
                              if int(a.get("id", 0)) != aid]
            grips = self._route_grips.get(link_id, [])
            found_grip = next((g for g in grips if g._entry_id == aid), None)
            if found_grip is not None:
                grip = found_grip
                link = grip._link
                if link is not None and isValid(link):
                    link.remove_grip(grip)
                grips.remove(grip)
                if isValid(grip):
                    self._scene.removeItem(grip)
            if not grips:
                self._route_grips.pop(link_id, None)

    def _push_handover_grips(self, stack, direction: str) -> None:
        """Pin each wrapped-band hand-over to the sides that face the gap.

        Two routing points per hand-over, just outside the exit and entry
        sides, give one straight run between the bands. The rule is shared
        with the MCP `auto_layout` tool (`raptor.core.fileio.band_handover`).
        The points are stored in the file like any routing point, so the
        route survives a save and reopen; the next Auto Layout removes
        them with the rest."""
        for link in list(self._links):
            source, target = link._from, link._to
            if source is target or (link._link_num or 0) <= 0:
                continue
            points = handover_points(link._box(source), link._box(target),
                                     direction)
            if points is None:
                continue
            for x, y in points:
                entry = {
                    "kind": "route_grip",
                    "id": self._next_anno_id(),
                    "parent": self._current_parent,
                    "link": link._link_num,
                    "x": x, "y": y, "active": True,
                }
                stack.push(RouteGripCmd(self, entry, add=True))

    def _layout_graph(self) -> tuple[dict[tuple[str, int], QGraphicsItem],
                                     list[tuple[tuple[str, int],
                                                tuple[str, int]]]]:
        """This level's layout vertices and edges, keyed ("block", id) etc.

        Shared by Auto Layout and insert-in-parallel's local layout.
        """
        members: dict[tuple[str, int], QGraphicsItem] = {}
        members.update({("block", bid): item
                        for bid, item in self._block_items.items()})
        members.update({("node", nid): item
                        for nid, item in self._node_items.items()})
        members.update({("hier", hid): item
                        for hid, item in getattr(self, "_hier_items",
                                                 {}).items()})
        # Events are flow objects too (Node -> Event -> Node), so they
        # must be laid out as vertices. They used to be left out and
        # shifted by their parent block's delta, but an event's
        # EVENT_DATA ParentId is its hierarchy LEVEL, not a block, so on
        # the home level they never moved at all.
        members.update({("event", eid): item
                        for eid, item in getattr(self, "_event_items",
                                                 {}).items()})

        edges: list[tuple[tuple[str, int], tuple[str, int]]] = []
        link: Any
        for link in self.links:
            a = (link.connect_f_ref.replace("RBD", "", 1).lower(),
                 link.connect_from_id)
            b = (link.connect_t_ref.replace("RBD", "", 1).lower(),
                 link.connect_to_id)
            if a in members and b in members:
                edges.append((a, b))

        return members, edges

    def layout_selection_count(self) -> tuple[int, int]:
        """(selected, total) layout elements on this level. Auto Layout
        arranges only the selection when 0 < selected < total."""
        members, _edges = self._layout_graph()
        # Selection changes fire while a level is rebuilt, when the item
        # tables can still hold deleted items: count only live ones.
        live = [item for item in members.values() if isValid(item)]
        selected = sum(1 for item in live if item.isSelected())
        return selected, len(live)

    def level_label(self) -> str:
        """"Home", or "hierarchy <name>" for the level shown."""
        parent = self._current_parent
        if not parent:
            return "Home"
        hier = self.hiers.get(parent)
        name = getattr(hier, "name", None)
        return f"hierarchy {name}" if name else "this hierarchy"

    def _auto_layout_selection(self, selected, members, edges, *,
                               direction: str,
                               max_columns: int | None,
                               spacing: float,
                               anchor: tuple[str, int] | None = None,
                               text: str = "Auto Layout selection") -> None:
        """Lay out only the selected elements, centred on their old bbox.

        The subset is laid out in isolation -- a selected chain becomes
        the same tidy chain the full pass would draw -- then translated so
        the group's bounding-box centre stays where it was. Edges to
        elements outside the selection are ignored for layering; they do
        not disappear from the model.
        """
        from raptor.core.fileio.legacy_layout import (
            GUI_REFINE_BUDGET_S, layout_container_positions, refine_budget,
        )
        from raptor.core.fileio.element_size import REFERENCE_SCALE

        selected = set(selected)
        inner = [(a, b) for a, b in edges if a in selected and b in selected]
        predecessors = {b for _a, b in inner}
        entries = [k for k in selected if k not in predecessors]
        if not entries:
            entries = [sorted(selected)[0]]
        sizes = {}
        for key in sorted(selected):
            rect = members[key].sceneBoundingRect()
            sizes[key] = (max(rect.width() / 2.0 / REFERENCE_SCALE, 0.1),
                          max(rect.height() / 2.0 / REFERENCE_SCALE, 0.1))
        with refine_budget(GUI_REFINE_BUDGET_S):
            new_pos = layout_container_positions(
                selected, inner, entries, pins=set(), sizes=sizes,
                scale=REFERENCE_SCALE, direction=direction,
                max_columns=max_columns, spacing=spacing)
        old_pts = []
        for key in selected:
            centre = _LinkItem._true_centre(members[key])
            old_pts.append(self._vt.to_model(centre.x(), centre.y()))
        old_x = [p[0] for p in old_pts]
        old_y = [p[1] for p in old_pts]
        new_x = [p[0] for p in new_pos.values()]
        new_y = [p[1] for p in new_pos.values()]
        dx = (min(old_x) + max(old_x)) / 2.0 - (min(new_x) + max(new_x)) / 2.0
        dy = (min(old_y) + max(old_y)) / 2.0 - (min(new_y) + max(new_y)) / 2.0
        if anchor is not None and anchor in new_pos:
            # Keep one element exactly where it was instead of the group's
            # centre (insert-in-parallel pins its split node, so the link
            # into the group stays straight).
            centre = _LinkItem._true_centre(members[anchor])
            ax, ay = self._vt.to_model(centre.x(), centre.y())
            dx = ax - new_pos[anchor][0]
            dy = ay - new_pos[anchor][1]

        before = self._layout_state()
        self._loading = True
        saved_snap = (self._snap_state["x"], self._snap_state["y"])
        self._snap_state["x"] = self._snap_state["y"] = 0.0
        try:
            for key, (mx, my) in new_pos.items():
                item = members[key]
                sx, sy = self._vt.to_scene(mx + dx, my + dy)
                if isinstance(item, _BlockItem):
                    item.setPos(sx - BLOCK_W / 2, sy - BLOCK_H / 2)
                else:
                    item.setPos(sx, sy)
        finally:
            self._snap_state["x"], self._snap_state["y"] = saved_snap
            self._loading = False
        self.sync_model_positions()
        self._user_arranged = True
        stack = getattr(self, "_stack", None)
        if stack is not None:
            stack.push(LayoutCmd(self, text, before, self._layout_state()))
        else:
            self.layout_changed.emit()

    def _refresh_node_badges(self) -> None:
        """The k/n badge inside every connect node on this level ("2/3":
        two of three incoming paths must be good), set or cleared. Called
        on a rebuild, after a k edit and after any change to the links, so
        the badge never shows an old k or n.

        Links are counted only where `connect_t_ref == "RBDNode"`: blocks,
        nodes and events are numbered separately, so a block sharing a
        node's id must not add to that node's n."""
        incoming: dict[int, int] = {}
        for link in self.links:
            if link.connect_t_ref == "RBDNode":
                incoming[link.connect_to_id] = \
                    incoming.get(link.connect_to_id, 0) + 1
        for nid, node in self.nodes.items():
            item = self._node_items.get(nid)
            if item is None or node.type_node != 2:
                continue
            n, k = incoming.get(nid, 0), node.good_paths
            # One path, or a k that makes no sense yet: no badge.
            item.set_label_text(f"{k}/{n}" if n > 1 and 1 <= k <= n else "")

    def _recount_link_ends(self) -> None:
        """Recompute In/Out and node n from the actual link rows.

        The file's `In`/`Out` and node `n` are stored, and Raptor 7
        rejects a model whose declared `n` differs from its real incoming
        link count. The GUI used to change the topology without touching
        those counts, so adding/removing a link (or an element) left a
        valid model looking invalid. `fix_link_counts` is the same owner
        the API/save path uses, so there is one rule.
        """
        rbd = self._rbd_ref
        if rbd is None:
            return

        def rows(section: str) -> list[dict]:
            sec = rbd.sections.get(section)
            return sec.rows if sec is not None else []

        blocks, nodes = rows("BLOCK_DATA"), rows("NODE_DATA")
        events, links = rows("EVENT_DATA"), rows("LINK_DATA")
        from raptor.core.fileio.rbdlib import fix_link_counts
        fix_link_counts(blocks, nodes, events, links)

        b_rows = {_safe_int(r.get("ID")): r for r in blocks}
        n_rows = {_safe_int(r.get("ID")): r for r in nodes}
        e_rows = {_safe_int(r.get("ID")): r for r in events}
        for oid, b in self.blocks.items():
            r = b_rows.get(oid)
            if r is not None:
                b.connect_into_num = _safe_int(r.get("In"))
                b.connect_out_of_num = _safe_int(r.get("Out"))
        for oid, n in self.nodes.items():
            r = n_rows.get(oid)
            if r is not None:
                n.connect_out_of_num = _safe_int(r.get("Out"))
        for oid, event in self.events.items():
            r = e_rows.get(oid)
            if r is not None:
                event.connect_into_num = _safe_int(r.get("In"))
                event.connect_out_of_num = _safe_int(r.get("Out"))
        self._refresh_node_badges()

    def _clear_route_caches(self) -> None:
        """Drop cached centres/port-side decisions after a topology edit."""
        for table in (self._block_items, self._node_items,
                      self._event_items, getattr(self, "_hier_items", {})):
            for item in cast("dict[int, Any]", table).values():
                item._route_centre = None
                item._route_horizontal = None
                item._route_flow_sign = None
                item._route_entry_side = None
                item._route_exit_side = None

    def _remove_link_row_and_item(self, link_num: int) -> None:
        """Remove one link row and its canvas item, without rebuilding the
        whole link layer (used by SplitLinkCmd; `DeleteCmd`'s shared path
        would also discard unrelated live routing-point items)."""
        section = self._link_section()
        if section is not None:
            section.rows[:] = [row for row in section.rows
                               if _safe_int(row.get("ID")) != link_num]
        self.links[:] = [link for link in self.links
                         if link.object_num != link_num]
        for item in list(self._links):
            if item._link_num != link_num:
                continue
            self._links.remove(item)
            if isValid(item):
                self._scene.removeItem(item)
        self._clear_route_caches()
        self._recount_link_ends()

    def _insert_link(self, row: dict) -> int:
        num = _safe_int(row.get("ID"))
        link = link_from_row(row)
        self.links.append(link)
        src = self._resolve_link_endpoint(
            link.connect_f_ref, link.connect_from_id)
        dst = self._resolve_link_endpoint(
            link.connect_t_ref, link.connect_to_id)
        if src is not None and dst is not None:
            li = _LinkItem(src, dst, link_num=num)
            self._links.append(li)
            self._scene.addItem(li)
            # A new link can change its endpoints' entry/exit sides.
            self._clear_route_caches()
            with routing_batch(self._scene):
                for existing in self._links:
                    existing._update_route()
        self._recount_link_ends()
        return num

    # -- insert-on-link -------------------------------------------------
    @staticmethod
    def _item_key(item) -> tuple[str, int] | None:
        if isinstance(item, _BlockItem):
            return ("block", item._block_id)
        if isinstance(item, _NodeItem):
            return ("node", item._node_id)
        if isinstance(item, _EventItem):
            return ("event", item._event_id)
        if isinstance(item, _HierItem):
            return ("hier", item._hier_id)
        return None

    @staticmethod
    def _ref_key(ref: str, oid: int) -> tuple[str, int]:
        return (ref.replace("RBD", "", 1).lower(), oid)

    def _level_items(self) -> dict[tuple[str, int], Any]:
        out: dict[tuple[str, int], Any] = {}
        for kind, table in (
                ("block", self._block_items), ("node", self._node_items),
                ("event", self._event_items),
                ("hier", getattr(self, "_hier_items", {}))):
            for oid, item in table.items():
                out[(kind, oid)] = item
        return out

    def _downstream_items(self, start, exclude=None) -> list[tuple[str, int, Any]]:
        """The start item and everything reachable through outgoing links on
        this level. Unrelated branches and upstream elements are excluded."""
        items = self._level_items()
        start_key = self._item_key(start)
        if start_key is None or start_key not in items:
            return []
        exclude_key = self._item_key(exclude) if exclude is not None else None
        outgoing: dict[tuple[str, int], list[tuple[str, int]]] = {}
        for link in self.links:
            a = self._ref_key(link.connect_f_ref, link.connect_from_id)
            b = self._ref_key(link.connect_t_ref, link.connect_to_id)
            if a in items and b in items:
                outgoing.setdefault(a, []).append(b)
        seen = {start_key}
        queue = [start_key]
        ordered: list[tuple[str, int, Any]] = []
        while queue:
            current = queue.pop(0)
            if current != exclude_key:
                ordered.append((current[0], current[1], items[current]))
            for nxt in outgoing.get(current, ()):
                if nxt in seen or nxt == exclude_key:
                    continue
                seen.add(nxt)
                queue.append(nxt)
        return ordered

    def _provisional_insert_center(self, link) -> QPointF:
        active = [g for g in link.grip_items()
                  if getattr(g, "_active", True)]
        if active:
            return link.path().pointAtPercent(0.5)
        source = _LinkItem._true_centre(link._from)
        target = _LinkItem._true_centre(link._to)
        return QPointF((source.x() + target.x()) / 2.0,
                       (source.y() + target.y()) / 2.0)

    def _final_insert_center(self, link, item, axis: link_insert.FlowAxis
                             ) -> tuple[QPointF, float]:
        source_rect = self._element_shape_rect(link._from)
        target_rect = self._element_shape_rect(link._to)
        new_rect = self._element_shape_rect(item)
        source_span = self._span_of(source_rect, axis.axis)
        target_span = self._span_of(target_rect, axis.axis)
        new_span = self._span_of(new_rect, axis.axis)
        gap = self._standard_insert_gap()
        active = [g for g in link.grip_items()
                  if getattr(g, "_active", True)]
        if active:
            # Dog-legs: stay on the drawn route, midway between the
            # routing points, then clamp to the minimum gap. Preserving
            # the route is more important than equal side gaps here.
            midpoint = link.path().pointAtPercent(0.5)
            half = (new_span[1] - new_span[0]) / 2.0
            centre = midpoint.x() if axis.axis == "x" else midpoint.y()
            proposed = (centre - half, centre + half)
            plan = link_insert.plan_minimum_spacing(
                source_span, target_span, proposed, axis, gap)
            cross = (midpoint.y() if axis.axis == "x" else midpoint.x())
        else:
            new_size = new_span[1] - new_span[0]
            plan = link_insert.plan_equal_spacing(
                source_span, target_span, new_size, axis, gap)
            source_centre = _LinkItem._true_centre(link._from)
            target_centre = _LinkItem._true_centre(link._to)
            cross = ((source_centre.y() + target_centre.y()) / 2.0
                     if axis.axis == "x"
                     else (source_centre.x() + target_centre.x()) / 2.0)
        centre = (QPointF(plan.center, cross) if axis.axis == "x"
                  else QPointF(cross, plan.center))
        return centre, plan.shift

    def _grip_entries_for(self, link) -> list[dict]:
        return [dict(entry) for entry in
                getattr(self._rbd_ref, "link_grips", [])
                if _safe_int(entry.get("link")) == link._link_num]

    def _plan_grip_split(self, link
                         ) -> tuple[list[dict], list[dict], list[dict]]:
        """Split the link's routing points into upstream/downstream groups.

        The path midpoint decides the split, so the new element really is
        inserted between the existing dog-legs. Routing points at the exact
        split point are dropped (undo restores them). Inactive points are
        not part of the drawn route and are dropped as well.
        """
        active = [g for g in link._grips
                  if isValid(g) and getattr(g, "_active", True)]
        entries = self._grip_entries_for(link)
        by_id = {_safe_int(entry.get("id")): entry for entry in entries}
        if not active:
            return ([], [], entries)
        points = [link._p0, *[g.pos() for g in active], link._p3]
        cumulative = [0.0]
        for a, b in itertools.pairwise(points):
            cumulative.append(cumulative[-1] + math.hypot(
                b.x() - a.x(), b.y() - a.y()))
        total = cumulative[-1]
        if total <= 1e-9:
            return ([], [], entries)
        midpoint = total / 2.0
        upstream_active: list[Any] = []
        downstream_active: list[Any] = []
        dropped_active: list[Any] = []
        exact = next((i for i, value in enumerate(cumulative)
                      if abs(value - midpoint) <= 1e-6), None)
        if exact is not None:
            if 1 <= exact <= len(active):
                dropped_active.append(active[exact - 1])
                upstream_active = active[:exact - 1]
                downstream_active = active[exact:]
            else:
                upstream_active = active
        else:
            segment = next(i for i in range(len(cumulative) - 1)
                           if cumulative[i] < midpoint < cumulative[i + 1])
            upstream_active = active[:segment]
            downstream_active = active[segment:]
        matched = {_safe_int(g._entry_id) for g in active}
        upstream = [by_id[_safe_int(g._entry_id)]
                    for g in upstream_active
                    if _safe_int(g._entry_id) in by_id]
        downstream = [by_id[_safe_int(g._entry_id)]
                      for g in downstream_active
                      if _safe_int(g._entry_id) in by_id]
        dropped = [entry for entry in entries
                   if _safe_int(entry.get("id")) not in matched]
        dropped.extend(by_id[_safe_int(g._entry_id)]
                       for g in dropped_active
                       if _safe_int(g._entry_id) in by_id)
        return (upstream, downstream, dropped)

    def _split_link_rows(self, link, kind_token: str, new_id: int,
                         upstream_id: int,
                         downstream_id: int) -> list[dict]:
        section = self._link_section()
        if section is None:
            raise RuntimeError("no LINK_DATA section")
        row = next((r for r in section.rows
                    if _safe_int(r.get("ID")) == link._link_num), None)
        if row is None:
            raise RuntimeError("the connector row is gone")
        # Insert-on-link automates the manual steps (delete the link, draw
        # two new ones), so both halves are built exactly as Connect builds
        # a link: Raptor's fixed defaults, not the split link's settings.
        return [
            self._make_link_row(row.get("FromType", "Node"),
                                _safe_int(row.get("FromID")), kind_token,
                                new_id, upstream_id),
            self._make_link_row(kind_token, new_id,
                                row.get("ToType", "Block"),
                                _safe_int(row.get("ToID")), downstream_id),
        ]

    def _grip_moves_for_split(self, upstream: list[dict],
                              downstream: list[dict], dropped: list[dict],
                              upstream_id: int, downstream_id: int,
                              delta: tuple[float, float]):
        moves: list[tuple[dict, dict | None, int | None]] = []
        for entry in upstream:
            new = dict(entry)
            new["link"] = upstream_id
            moves.append((dict(entry), new, upstream_id))
        dx, dy = delta
        for entry in downstream:
            new = dict(entry)
            new["link"] = downstream_id
            new["x"] = round(float(entry.get("x", 0.0)) + dx, 2)
            new["y"] = round(float(entry.get("y", 0.0)) + dy, 2)
            moves.append((dict(entry), new, downstream_id))
        for entry in dropped:
            moves.append((dict(entry), None, None))
        return moves

    def _insert_moves(self, link, new_item, final_centre: QPointF,
                      delta: tuple[float, float]):
        moves: list[tuple[str, int, tuple[float, float],
                          tuple[float, float]]] = []
        if delta != (0.0, 0.0):
            for kind, oid, item in self._downstream_items(
                    link._to, exclude=link._from):
                old = item.scenePos()
                new = (old.x() + delta[0], old.y() + delta[1])
                moves.append((kind, oid, (old.x(), old.y()), new))
        key = self._item_key(new_item)
        if key is not None:
            old = new_item.scenePos()
            if key[0] == "block":
                new = (final_centre.x() - BLOCK_W / 2.0,
                       final_centre.y() - BLOCK_H / 2.0)
            else:
                new = (final_centre.x(), final_centre.y())
            if (abs(new[0] - old.x()) > 0.5
                    or abs(new[1] - old.y()) > 0.5):
                moves.append((key[0], key[1], (old.x(), old.y()), new))
        return moves

    def _create_inserted_element(self, kind: str, centre: QPointF) -> int:
        mx, my = self.scene_to_model(centre)
        if kind == "block":
            return self.add_block(f"Block_{len(self.blocks) + 1}", mx, my)
        if kind == "node":
            return self.add_node(f"node_{len(self.nodes) + 1}", mx, my)
        if kind == "event":
            return self.add_event("P=0.5", mx, my)
        return self.add_hierarchy(f"Hierarchy_{len(self.hiers) + 1}", mx, my)

    def _item_for_insert_kind(self, kind: str, oid: int):
        table = {"block": self._block_items, "node": self._node_items,
                 "event": self._event_items,
                 "hier": getattr(self, "_hier_items", {})}.get(kind, {})
        return table.get(oid)

    def insert_element_on_link(self, kind: str, link_num: int) -> int | None:
        """Insert a new block/node/event/hierarchy in series on one link.

        One undo step: create the element, move it and the downstream
        subgraph, replace the link with two fresh rows, and split the link's
        routing points. Returns the new element id, or None if refused.
        """
        info = {"block": ("block", "Block", "Block"),
                "node": ("node", "Node", "Node"),
                "event": ("event", "Event", "Event"),
                "hier": ("hier", "Hier", "Hierarchy")}.get(kind)
        if info is None or self._rbd_ref is None:
            return None
        link = next((item for item in self._links
                     if item._link_num == link_num), None)
        if link is None:
            _view_status(self, "Cannot insert here: connector not found")
            return None
        refusal = self._link_insert_refusal(link, info)
        if refusal:
            _view_status(self, f"Cannot insert {info[2]}: {refusal}")
            return None
        axis = self._link_flow_axis(link)
        if axis is None:
            _view_status(self, f"Cannot insert {info[2]}: no flow direction")
            return None
        if self._link_row(link_num) is None:
            _view_status(self, "Cannot insert here: connector row not found")
            return None
        endpoint_label = self._link_endpoint_label(link)
        self._clear_link_insert_hover()

        oid = 0
        self._stack.beginMacro(f"Insert {info[2]} on link")
        try:
            oid = self._insert_on_link_steps(kind, link, info, axis)
        finally:
            self._stack.endMacro()
        item = self._item_for_insert_kind(kind, oid)
        if item is not None:
            self._scene.clearSelection()
            item.setSelected(True)
        self.element_created.emit(kind, oid)
        _view_status(self, f"{info[2]} inserted on {endpoint_label}")
        return oid

    def _link_row(self, link_num: int) -> tuple[dict, int] | None:
        """(copy of the LINK_DATA row, its index), or None."""
        section = self._link_section()
        if section is None:
            return None
        for index, row in enumerate(section.rows):
            if _safe_int(row.get("ID")) == link_num:
                return dict(row), index
        return None

    def _insert_on_link_steps(self, kind: str, link, info, axis) -> int:
        """The undoable steps of a series insert, without a macro: create
        the element, move it and the downstream subgraph, and split the
        link. Shared by `insert_element_on_link` and the parallel insert,
        which wraps several of these in one macro."""
        found = self._link_row(link._link_num)
        if found is None:
            raise RuntimeError("the connector row is gone")
        old_row, old_index = found
        upstream, downstream, dropped = self._plan_grip_split(link)
        provisional = self._provisional_insert_center(link)
        oid = self._create_inserted_element(kind, provisional)
        item = self._item_for_insert_kind(kind, oid)
        if item is None:
            raise RuntimeError("the new element was not created")
        final_centre, shift = self._final_insert_center(link, item, axis)
        delta = axis.delta(shift)
        moves = self._insert_moves(link, item, final_centre, delta)
        if moves:
            self._stack.push(MoveCmd(self, moves))
        upstream_id = self._next_link_id()
        downstream_id = upstream_id + 1
        new_rows = self._split_link_rows(
            link, info[1], oid, upstream_id, downstream_id)
        grip_moves = self._grip_moves_for_split(
            upstream, downstream, dropped, upstream_id, downstream_id,
            delta)
        self._stack.push(SplitLinkCmd(
            self, old_row, old_index, new_rows, grip_moves))
        return oid

    # -- insert in parallel on an element ----------------------------------
    def _item_links(self, item) -> tuple[list, list]:
        """(links into `item`, links out of it) on this level."""
        into, out_of = [], []
        for link in self._links:
            if not isValid(link):
                continue
            if link._to is item:
                into.append(link)
            if link._from is item:
                out_of.append(link)
        return into, out_of

    def _is_regular_node(self, item) -> bool:
        """An ordinary node (Type 2): it can take another link in or out.
        Start/End and a hierarchy's In/Out markers allow only one."""
        if not isinstance(item, _NodeItem):
            return False
        node = self.nodes.get(item._node_id)
        return node is not None and node.type_node == 2

    def _parallel_refusal(self, target, info) -> str | None:
        """Why a new element cannot go in parallel with `target`, or None."""
        if self._rbd_ref is None:
            return "Open a model first"
        if info[0] == "node":
            return ("a node cannot be in parallel; use Add Block, Add Event "
                    "or Add Hierarchy")
        into, out_of = self._item_links(target)
        if len(into) != 1 or len(out_of) != 1:
            return "connect it with one link in and one link out first"
        if into[0]._from is out_of[0]._to:
            return "its link in and its link out share a node"
        if self._link_flow_axis(out_of[0]) is None:
            return "its link out has no clear flow direction"
        if info[0] == "hier":
            from raptor.core.input_rules import MAX_HIERARCHY_DEPTH
            if self._current_depth() >= MAX_HIERARCHY_DEPTH:
                return (f"Hierarchies can nest at most "
                        f"{MAX_HIERARCHY_DEPTH} levels deep")
        return None

    @staticmethod
    def _between(split, join, edges) -> set:
        """`split`, `join` and every element on a path from one to the
        other: the parallel group that is laid out again."""
        forward: dict = {}
        backward: dict = {}
        for a, b in edges:
            forward.setdefault(a, []).append(b)
            backward.setdefault(b, []).append(a)

        def reach(start, stop, adjacency) -> set:
            seen = {start}
            queue = [start]
            while queue:
                current = queue.pop()
                if current == stop:
                    continue
                for nxt in adjacency.get(current, ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
            return seen

        return (reach(split, join, forward) & reach(join, split, backward)
                ) | {split, join}

    def insert_element_in_parallel(self, kind: str, target) -> int | None:
        """Add a new block/event/hierarchy in parallel with `target`.

        The new element gets its own path between a split node before the
        target and a join node after it. An ordinary node already there is
        reused (so a target already in a parallel group gets one more
        branch, and a reused join node's k is left as it is); a Start/In
        marker or a non-node gets a new split node, an End/Out marker or a
        non-node a new join node, each inserted in series with the same
        spacing as insert-on-link. The group between the two nodes is then
        laid out again, and whatever is downstream of the join node moves
        along if the group grew. One undo step.
        """
        info = {"block": ("block", "Block", "Block"),
                "node": ("node", "Node", "Node"),
                "event": ("event", "Event", "Event"),
                "hier": ("hier", "Hier", "Hierarchy")}.get(kind)
        target_key = self._item_key(target)
        if info is None or self._rbd_ref is None or target_key is None \
                or isinstance(target, _NodeItem):
            return None
        target_name = self._endpoint_text(target)
        refusal = self._parallel_refusal(target, info)
        if refusal:
            _view_status(self, f"Cannot add {info[2]} in parallel with "
                               f"{target_name}: {refusal}")
            return None
        self._clear_link_insert_hover()
        oid = 0
        added_nodes = 0
        self._stack.beginMacro(f"Add {info[2]} in parallel")
        try:
            oid, added_nodes = self._parallel_steps(kind, target_key, info)
        finally:
            self._stack.endMacro()
        item = self._item_for_insert_kind(kind, oid)
        if item is not None:
            self._scene.clearSelection()
            item.setSelected(True)
        self.element_created.emit(kind, oid)
        extra = ""
        if added_nodes == 1:
            extra = "; 1 node added"
        elif added_nodes > 1:
            extra = f"; {added_nodes} nodes added"
        _view_status(self, f"{info[2]} added in parallel with "
                           f"{target_name}{extra}")
        return oid

    def _parallel_steps(self, kind: str, target_key, info) -> tuple[int, int]:
        """The undoable steps of `insert_element_in_parallel` (no macro).
        Returns (new element id, number of nodes added)."""
        node_info = ("node", "Node", "Node")
        added = 0

        def target():
            return self._level_items()[target_key]

        # Split side.
        into, _out = self._item_links(target())
        source = into[0]._from
        if self._is_regular_node(source):
            split = ("node", source._node_id)
        else:
            axis = self._link_flow_axis(into[0])
            if axis is None:
                raise RuntimeError("the link in has no flow direction")
            split = ("node", self._insert_on_link_steps(
                "node", into[0], node_info, axis))
            added += 1
        # Join side (looked up again: the split may have rebuilt links).
        _into, out_of = self._item_links(target())
        sink = out_of[0]._to
        if self._is_regular_node(sink):
            join = ("node", sink._node_id)
        else:
            axis = self._link_flow_axis(out_of[0])
            if axis is None:
                raise RuntimeError("the link out has no flow direction")
            join = ("node", self._insert_on_link_steps(
                "node", out_of[0], node_info, axis))
            added += 1

        # The new element, first placed beside the target across the flow;
        # the group layout below puts it in its final place.
        page_flow = self._page_flow()
        centre = _LinkItem._true_centre(target())
        hw, hh = _LinkItem._half_extents(target())
        gap = self._standard_insert_gap()
        if page_flow in ("LR", "RL"):
            provisional = QPointF(centre.x(), centre.y() + 2.0 * hh + gap)
        else:
            provisional = QPointF(centre.x() + 2.0 * hw + gap, centre.y())
        oid = self._create_inserted_element(kind, provisional)
        first = self._next_link_id()
        self._stack.push(AddCmd(self, "link", [
            self._make_link_row("Node", split[1], info[1], oid, first),
            self._make_link_row(info[1], oid, "Node", join[1], first + 1),
        ]))

        self._layout_parallel_group(split, join, page_flow)
        return oid, added

    def _layout_parallel_group(self, split, join, page_flow: str) -> None:
        """Lay out the split node, the join node and every branch between
        them, keeping the split node where it is; then move whatever is
        downstream of the join node by however far the join node moved
        downstream, so the gap after the group is kept."""
        members, edges = self._layout_graph()
        if split not in members or join not in members:
            return
        group = self._between(split, join, edges)
        axis_x = page_flow in ("LR", "RL")
        sign = 1 if page_flow in ("LR", "TB") else -1

        def along(key) -> float:
            c = _LinkItem._true_centre(members[key])
            return c.x() if axis_x else c.y()

        join_before = along(join)
        try:
            spacing = float(app_settings.get("layout/spacing", 1.0) or 1.0)
        except (TypeError, ValueError, OverflowError):
            spacing = 1.0
        if not math.isfinite(spacing) or spacing <= 0.0:
            spacing = 1.0
        self._auto_layout_selection(
            group, members, edges, direction=page_flow, max_columns=None,
            spacing=spacing, anchor=split, text="Lay out parallel group")
        # Item positions changed under the cached route centres.
        for item in members.values():
            cast(Any, item)._route_centre = None
        shift = (along(join) - join_before) * sign
        if shift <= 0.5:
            return
        forward: dict = {}
        for a, b in edges:
            forward.setdefault(a, []).append(b)
        seen = set(group)
        queue = [join]
        moved = []
        while queue:
            current = queue.pop()
            for nxt in forward.get(current, ()):
                if nxt in seen:
                    continue
                seen.add(nxt)
                queue.append(nxt)
                moved.append(nxt)
        dx, dy = (sign * shift, 0.0) if axis_x else (0.0, sign * shift)
        moves = []
        for kind, oid in moved:
            item = members[(kind, oid)]
            old = item.scenePos()
            moves.append((kind, oid, (old.x(), old.y()),
                          (old.x() + dx, old.y() + dy)))
        if moves:
            self._stack.push(MoveCmd(self, moves))

    def add_event(self, name: str, model_x: float, model_y: float,
                  probability: float = 0.5) -> int:
        """Create an event (Bernoulli by default) cloned from an existing
        EVENT_DATA row."""
        if self._rbd_ref is None:
            raise RuntimeError("no file loaded")
        _section = self._rbd_ref.sections["EVENT_DATA"]
        row = self._template_row("EVENT_DATA", "ID") or {
            "eventName": "", "ID": "0", "xPos": "0", "yPos": "0",
            "ParentId": "0", "In": "1", "Out": "1", "Phasing": "0",
            "Distro": "17", "failStream": "203", "failVal": "0.5",
            "InitCost": "1.000000", "OpCost": "1.000000",
            "RepCost": "1.000000",
        }
        eid = self._next_id("EVENT_DATA", "ID", self.events)
        row["eventName"] = f"P={probability:g}"
        row["ID"] = str(eid)
        row["ParentId"] = str(self._current_parent)   # the level shown
        row["xPos"] = f"{model_x:.6f}"
        row["yPos"] = f"{model_y:.6f}"
        row.setdefault("Distro", "17")
        row["failVal"] = f"{probability:.6f}"
        self._stack.push(AddCmd(self, "event", [row]))
        return eid

    def _insert_event(self, row: dict) -> int:
        from raptor.core.fileio.typed import event_from_row
        eid = _safe_int(row.get("ID"))
        event = event_from_row(row)
        if _safe_int(row.get("ParentId", "0")) != self._current_parent:
            return eid   # another level's row -- file only (see _insert_block)
        self.events[eid] = event
        item = _EventItem(eid, event.name,
                          self._tx(event.x_position), self._ty(event.y_position),
                          on_moved=self._item_moved,
                          probability=(event.fail_mean
                                       if event.fail_distro == 17
                                       else None))
        item.set_phased(event.uses_phasing)
        self._scene.addItem(item)
        self._event_items[eid] = item
        self._refresh_lod()
        self._recount_link_ends()
        return eid

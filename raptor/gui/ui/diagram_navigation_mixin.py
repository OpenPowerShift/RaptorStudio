"""Hierarchy levels and finding elements: enter, go up, locate and reveal.

Expects on `self`: the scene, `_rbd`, the undo stack and the item tables."""
from __future__ import annotations


from typing import Any

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtWidgets import (
    QApplication,
)

from raptor.core.fileio.typed import (
    block_from_row, node_from_row,
)
from raptor.gui.ui.diagram_common import (
    _PARENT_COLS,
    _safe_int,
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


class NavigationMixin(_Base):
    """Hierarchy levels and finding elements: enter, go up, locate and reveal."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _current_parent: Any
        _global_name_index: Any
        _loading: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def _current_depth(self) -> int:
        """How many hierarchy levels deep `self._current_parent` is,
        walked fresh from the model's own `parent_id` chain each time --
        Computed fresh rather than from a maintained
        counter/stack, so it cannot desync from direct level jumps such
        as `locate_element`'s. Gates Add Hierarchy and Collapse at
        `input_rules.MAX_HIERARCHY_DEPTH`.
        """
        depth = 0
        seen: set[int] = set()
        hid = self._current_parent
        while hid and hid in self.hiers and hid not in seen:
            seen.add(hid)  # guard against a corrupt cyclic parent chain
            depth += 1
            hid = self.hiers[hid].parent_id
        return depth

    def enter_level(self, hier_id: int):
        """Descend into a hierarchy's child level (double-click oval)."""
        if hier_id not in self.hiers or hier_id == self._current_parent:
            return
        # No depth cap here: any hierarchy that exists can be opened. The
        # nesting limit (`input_rules.MAX_HIERARCHY_DEPTH`) is enforced
        # where hierarchies are made, and a deeper file is reported by
        # validation -- never by a double-click that silently does nothing.
        self._set_level(hier_id)

    def go_up(self, from_level: int | None = None):
        """Ascend one level (Up button / double-click the Out marker).

        `from_level` is the hierarchy to leave, when it is not the one on
        screen: the Explorer lists every level, so an Out marker it shows
        can belong to any hierarchy."""
        level = self._current_parent if from_level is None else from_level
        if not level:
            return
        parent = self.hiers[level].parent_id if level in self.hiers else 0
        self._set_level(parent)

    def go_home(self):
        """Jump straight back to the home level."""
        self._set_level(0)

    def _set_level(self, parent_id: int):
        # A Comment draft belongs to an element on the level being left;
        # flush it before the scene (and Inspector) is rebuilt.
        self.level_about_to_change.emit()
        # A large level still pays for item construction even after the
        # parser and routing caches. Say what is happening and put the wait
        # cursor up before doing it; otherwise the window looks frozen for
        # several seconds.
        sink = getattr(self, "status_message_sink", None)
        target = self.hiers.get(parent_id)
        label = ("Home" if not parent_id
                 else getattr(target, "name", None) or f"hierarchy {parent_id}")
        if sink is not None:
            sink(f"Opening {label}…")
        previous_loading = self._loading
        previous_parent = self._current_parent
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # Inside the try: `processEvents` can dispatch a repaint or a
            # queued slot that raises through the wait-cursor setup.
            QApplication.processEvents()
            self._loading = True
            self._current_parent = parent_id
            self._rebuild_scene()
        except BaseException:
            # A half-built scene belongs to `parent_id`, not the level we
            # came from; leaving the pointer moved would make `go_up`
            # navigate from a level whose items are not on screen.
            self._current_parent = previous_parent
            raise
        finally:
            self._loading = previous_loading
            QApplication.restoreOverrideCursor()
        # Live element colours survive traversal: a run in progress
        # re-paints the freshly built items from the cached live state
        # immediately, so entering a hierarchy mid-run shows its
        # elements' current colours (not defaults) and going back to the
        # parent keeps them.
        self._reapply_live_states()
        # Result visuals survive traversal too: re-apply weak bands, DSI
        # dots, and the capacity/cost/spares overlays to the freshly
        # built items, on top of the live colours.
        self._reapply_result_visuals()
        # The minimap's fit target belonged to the old level; without a
        # reset it kept the old box shape and clipped/emptied the new
        # level until a window resize.
        minimap = getattr(self, "_minimap", None)
        if minimap is not None:
            minimap._fit_target = None
            minimap._last_fitted_rect = None
            minimap._refresh()
        self.level_changed.emit(parent_id)

    def locate_id(self, kind: str, oid: int) -> bool:
        """Select the element `kind` `oid` and centre on it, going to the
        hierarchy level it lives on. By id, so a renamed element or two
        with one name are found exactly."""
        rbd = self._rbd_ref
        spec = self._LOCATE.get(kind)
        if rbd is None or spec is None or oid == 0:
            return False
        section, parent_col, items_attr = spec
        row = next((r for r in getattr(rbd.sections.get(section), "rows", [])
                    if str(r.get("ID")) == str(oid)), None)
        if row is None:
            return False
        try:
            parent = int(float(row.get(parent_col, 0) or 0))
        except ValueError:
            parent = 0
        if parent != self._current_parent:
            self._set_level(parent)
        target = getattr(self, items_attr).get(oid)
        if target is None:
            return False
        for it in list(self._scene.selectedItems()):
            it.setSelected(False)
        target.setSelected(True)
        self.centerOn(target)
        return True

    def locate_link(self, link_num: int) -> bool:
        """Select connector `link_num` and centre on it.

        Links are not in the block/node/event/hier collections `locate_id`
        searches -- they are `_LinkItem` objects rebuilt from the link rows
        on the current level -- so an Issues entry about a link could not
        select anything. Returns False when the connector is not on the
        level currently shown, which the caller treats as "located the
        element instead".
        """
        try:
            wanted = int(link_num)
        except (TypeError, ValueError):
            return False
        for item in self._links:
            if int(getattr(item, "_link_num", -1)) != wanted:
                continue
            for other in list(self._scene.selectedItems()):
                other.setSelected(False)
            item.setSelected(True)
            self.centerOn(item.boundingRect().center())
            return True
        return False

    def reveal_element(self, kind: str, oid: int) -> bool:
        """Show the level `kind`/`oid` lives on ("block", "node", "event"
        or "hier") if it is not the one on screen. The element tables are
        level-scoped, so an element from another level (picked in the
        Explorer, which lists the whole model) has no item until then.
        Returns whether the element is on screen afterwards."""
        def on_screen() -> bool:
            table = {"block": self._block_items, "node": self._node_items,
                     "event": self._event_items,
                     "hier": getattr(self, "_hier_items", {})}.get(kind, {})
            return oid in table

        if on_screen():
            return True
        rbd = self._rbd_ref
        section = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                   "event": "EVENT_DATA", "hier": "HIERARCHY_DATA"}.get(kind)
        if rbd is None or section is None or section not in rbd.sections:
            return False
        parent_col = _PARENT_COLS[section]
        row = next((r for r in rbd.sections[section].rows
                    if _safe_int(r.get("ID")) == oid), None)
        if row is None:
            return False
        parent = _safe_int(row.get(parent_col))
        if parent and parent not in self.hiers:
            return False
        if parent != self._current_parent:
            self._set_level(parent)
        return on_screen()

    def locate_log_element(self, kind: str, name: str) -> bool:
        """Select/centre the element a live-log row names.

        Unlike `locate_element`, this handles every element kind and
        searches the raw rows, so it can navigate into the hierarchy the
        element lives on (and back out again on the next home-level
        event). Trigger/Pool/System rows have no canvas element and
        return False without changing the level.
        """
        spec = self._LOG_ELEMENT_KINDS.get(kind)
        rbd = self._rbd_ref
        if spec is None or rbd is None:
            return False
        element_kind, section, name_col = spec
        sec = rbd.sections.get(section)
        if sec is None:
            return False
        # The log names elements, not ids, and names may repeat. Prefer the
        # one on the level already on screen (so stepping a hierarchy stays
        # put), then the first in the file.
        matches: list[tuple[int, int]] = []
        parent_col = self._LOCATE[element_kind][1]
        for row in sec.rows:
            oid = _safe_int(row.get("ID"))
            if oid <= 0:
                continue  # the ID-0 column template, not an element
            row_name = row.get(name_col) or row.get("Name") or ""
            if row_name == name:
                matches.append((oid, _safe_int(row.get(parent_col))))
        if not matches:
            return False
        here = [oid for oid, parent in matches
                if parent == self._current_parent]
        return self.locate_id(element_kind, (here or [matches[0][0]])[0])

    def locate_element(self, kind: str, name: str) -> bool:
        """Select `name` (a block or node) and centre the view on it,
        navigating into whatever hierarchy level it actually lives on
        first if that's not the level currently shown -- `self.blocks`/
        `self.nodes` are level-scoped (`_rebuild_scene` filters by
        `ParentId == self._current_parent`), so a name from another
        level won't be in `_block_items`/`_node_items` yet. Searches the
        raw model rows (not the level-scoped dicts) so it finds the
        element regardless of which level is currently on screen.
        Returns whether an element was found."""
        rbd = self._rbd_ref
        if rbd is None:
            return False
        section = "BLOCK_DATA" if kind == "block" else "NODE_DATA"
        if section not in rbd.sections:
            return False
        parent_id = None
        oid = None
        for row in rbd.sections[section].rows:
            obj: Any
            try:
                obj = (block_from_row(row) if kind == "block"
                       else node_from_row(row))
            except (ValueError, KeyError):
                continue
            # BLOCK_DATA's ID-0 row is the file's column template, not an
            # element, and it is called "unnamed" -- the same as a new
            # model's block. Matching it first made every click on that
            # block in the event log report "Couldn't find block".
            if obj.id == 0:
                continue
            obj_name = obj.name if kind == "block" else obj.node_name
            if obj_name == name:
                parent_id = obj.parent_id
                oid = obj.id
                break
        if oid is None:
            return False
        if parent_id != self._current_parent:
            self._set_level(parent_id or 0)
        items = self._block_items if kind == "block" else self._node_items
        target = items.get(oid)
        if target is None:
            return False
        for it in list(self._scene.selectedItems()):
            it.setSelected(False)
        target.setSelected(True)
        self.centerOn(target)
        return True

    def _build_global_name_index(self) -> dict[tuple[str, int], str]:
        """(kind, id) -> name across the whole model. Same raw-row scan
        `locate_element` already uses, cached here so
        `_apply_dependency_badges` (one call per level's worth of
        blocks/nodes) doesn't re-scan the whole file per item."""
        rbd = self._rbd_ref
        index: dict[tuple[str, int], str] = {}
        if rbd is None:
            return index
        from raptor.core.fileio.typed import event_from_row
        sources = dict(self._DEP_TARGET_KINDS)
        sources["RBDEvent"] = ("event", "EVENT_DATA", event_from_row,
                               lambda o: o.name)
        for kind, section, from_row, name_of in sources.values():
            if section not in rbd.sections:
                continue
            for row in rbd.sections[section].rows:
                try:
                    obj = from_row(row)
                except (ValueError, KeyError):
                    continue
                index[(kind, obj.id)] = name_of(obj)
        return index

    def _name_index(self) -> dict[tuple[str, int], str]:
        """The whole-model (kind, id) -> name map, built on first use.

        `_rebuild_scene` only pays for this scan when the visible level
        actually has a dependency badge; a dependency added later (an
        Inspector edit, an undo) must not render as "Block #12", so the
        first lookup builds it."""
        if self._global_name_index is None:
            self._global_name_index = self._build_global_name_index()
        return self._global_name_index

"""The ribbon's find box and zoom controls.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations


import re
from typing import TYPE_CHECKING, Any





if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class FindZoomMixin(_Base):
    """The ribbon's find box and zoom controls."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def _find_combo_refresh(self, _filter_text: str = ""):
        """Rebuild the Find Item backing list: every block, node, event
        and hierarchy, prefixed by kind and carrying its id, so Enter can
        resolve either a name or an id. Called whenever the model changes
        (load, edit, undo/redo) -- not on every keystroke; typing is
        handled by the Model Explorer's own filter, which is the only
        place the results are shown."""
        entries = []
        v = getattr(self, "_diagram_dock", None)
        if v is not None:
            v = v.view
            def add(prefix, items):
                for name, eid in items:
                    entries.append(f"{prefix}  {name} ({eid})")
            add("Block", sorted(((b.name, i) for i, b in v.blocks.items()),
                                key=lambda t: t[0].lower()))
            add("Node", sorted(((n.node_name, i) for i, n in v.nodes.items()),
                               key=lambda t: t[0].lower()))
            add("Event", sorted(((e.name, i)
                                 for i, e in v.events.items()),
                                key=lambda t: t[0].lower()))
            add("Hier", sorted(((h.name, i) for i, h in v.hiers.items()),
                               key=lambda t: t[0].lower()))
        self._find_model.setStringList(entries)
        self._find_edit.setEnabled(bool(entries))

    def _on_find_text_changed(self, text: str) -> None:
        """Live-refine the Model Explorer tree from the Find box
        (refine, don't replace; match hierarchy + children). This is
        where the search results appear -- the box itself deliberately
        shows no dropdown, so the list of names is not drawn twice."""
        dock = getattr(self, "_explorer", None)
        if dock is not None:
            dock.filter_items(text)

    def _resolve_find_text(self, text: str) -> tuple[str, str] | None:
        """What the Find box holds -> `(kind, name)`, or None.

        Accepts the prefixed form (`Block  Pump (12)`), a bare name
        (`Pump`), a bare id (`12`), and a name carrying its id
        (`Pump (12)`) -- which is what the Explorer rows now show, so a
        row the user clicked through from is a valid thing to press
        Enter on."""
        text = text.strip()
        if not text:
            return None
        kind = None
        for p in self._FIND_PREFIXES:
            if text.startswith(p + "  "):
                kind, text = p, text[len(p) + 2:].strip()
                break
        if not text:
            return None
        # A bare id first: `12`, or the Explorer row form `Pump (12)`.
        eid = None
        name = text
        if text.isdigit():
            eid, name = int(text), ""
        else:
            m = re.fullmatch(r"(.*?)\s*\((\d+)\)\s*", text)
            if m:
                name, eid = m.group(1).strip(), int(m.group(2))
        v = self._diagram_dock.view
        if eid is not None and not name:
            # An id alone identifies exactly one element, if it exists.
            for k, table, attr in (("Block", v.blocks, "name"),
                                   ("Node", v.nodes, "node_name"),
                                   ("Event", v.events, "name"),
                                   ("Hier", v.hiers, "name")):
                if eid in table:
                    return k, getattr(table[eid], attr)
            return None
        if kind is not None:
            return kind, name
        if eid is not None:
            # Name AND id: the id is authoritative when it names something.
            for k, table, attr in (("Block", v.blocks, "name"),
                                   ("Node", v.nodes, "node_name"),
                                   ("Event", v.events, "name"),
                                   ("Hier", v.hiers, "name")):
                if eid in table and \
                        getattr(table[eid], attr).lower() == name.lower():
                    return k, name
            return None
        # A bare name: first match wins, blocks before nodes (a name is far
        # more often a block's) -- same order the completer listed.
        for k, table, attr in (("Block", v.blocks, "name"),
                               ("Node", v.nodes, "node_name"),
                               ("Event", v.events, "name"),
                               ("Hier", v.hiers, "name")):
            if any(getattr(o, attr).lower() == name.lower()
                   for o in table.values()):
                return k, name
        return None

    def _on_find_submit(self, text: str):
        resolved = self._resolve_find_text(text)
        if resolved is None:
            self._statusbar.showMessage(
                f"No element matches '{text.strip()}'", 4000)
            return
        kind, name = resolved
        v = self._diagram_dock.view

        # Block/Node go through locate_element (navigates into whatever
        # hierarchy level the element actually lives on first, if not
        # the one currently shown -- v.blocks/v.nodes are level-scoped,
        # so a same-level-only lookup would silently do nothing for an
        # element on another level).
        if kind in ("Block", "Node"):
            v.locate_element(kind.lower(), name)
            self._find_edit.clear()
            return

        target = None
        if kind == "Event":
            target = next((it for eid, it in v._event_items.items()
                           if v.events[eid].name == name), None)
        elif kind == "Hier":
            hid = next((h for h in v.hiers if v.hiers[h].name == name),
                       None)
            target = v._hier_items.get(hid)
        if target is None:
            return
        for it in list(v._scene.selectedItems()):
            it.setSelected(False)
        target.setSelected(True)
        v.centerOn(target)
        self._find_edit.clear()

    def _set_zoom_percent(self):
        """Navigate ▸ Zoom Percent: absolute zoom entry."""
        from PySide6.QtWidgets import QInputDialog
        cur = round(self._diagram_dock.view.transform().m11() * 100)
        pct, ok = QInputDialog.getInt(
            self, "Zoom Percent", "Zoom (%):", max(cur, 1), 1, 100000)
        if not ok:
            return
        v = self._diagram_dock.view
        target = pct / 100.0
        curm = v.transform().m11()
        if curm > 0:
            v.scale(target / curm, target / curm)
            v._user_zoomed = True
            v._apply_lod()

    def _fit_all(self):
        self._diagram_dock.view.fit_all()

    def _zoom_to_selection(self):
        """Fit the current selection in the diagram window."""
        n = self._diagram_dock.view.zoom_to_selection()
        if n:
            self.statusBar().showMessage(
                f"Zoomed to {n} selected element(s)", 4000)
        else:
            self.statusBar().showMessage(
                "Select one or more elements first, then Zoom to Selection",
                4000)

    def _zoom_to_hierarchy(self):
        """Descend into the selected hierarchy oval."""
        if not self._diagram_dock.view.zoom_to_hierarchy():
            self.statusBar().showMessage(
                "Select a hierarchy oval first, then Zoom to Hierarchy", 4000)

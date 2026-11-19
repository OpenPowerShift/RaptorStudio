"""The ribbon's find box and zoom controls.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations


from typing import TYPE_CHECKING





from raptor.gui.find_text import find_entries, parse_find_text, resolve_find_text

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtCore
    import PySide6.QtWidgets
    import raptor.gui.ui.diagram
else:
    _Base = object


class FindZoomMixin(_Base):
    """The ribbon's find box and zoom controls."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _diagram_dock: raptor.gui.ui.diagram.DiagramDock
        _find_edit: PySide6.QtWidgets.QLineEdit
        _find_model: PySide6.QtCore.QStringListModel
        _statusbar: PySide6.QtWidgets.QStatusBar

    #: Find box kind -> (section, name column) of the model's rows
    _FIND_ROWS = {"Block": ("BLOCK_DATA", "blockName"),
                  "Node": ("NODE_DATA", "Name"),
                  "Event": ("EVENT_DATA", "eventName"),
                  "Hier": ("HIERARCHY_DATA", "hierarchyName")}

    def _find_names(self) -> dict[str, dict[int, str]]:
        """Every element in the model, on every level, as the plain tables
        `find_text` takes. The view's own tables are level-scoped, so Find
        missed everything inside a hierarchy; they still win for this
        level, as they can hold edits the rows do not have yet."""
        v = self._diagram_dock.view
        names: dict[str, dict[int, str]] = {k: {} for k in self._FIND_ROWS}
        rbd = getattr(v, "_rbd_ref", None)
        sections = getattr(rbd, "sections", None) or {}
        for kind, (section, col) in self._FIND_ROWS.items():
            sec = sections.get(section)
            for row in getattr(sec, "rows", None) or []:
                try:
                    oid = int(float(row.get("ID", 0) or 0))
                except (TypeError, ValueError):
                    continue
                if oid > 0:
                    names[kind][oid] = str(row.get(col, "") or "")
        names["Block"].update({i: b.name for i, b in v.blocks.items()})
        names["Node"].update({i: n.node_name for i, n in v.nodes.items()})
        names["Event"].update({i: e.name for i, e in v.events.items()})
        names["Hier"].update({i: h.name for i, h in v.hiers.items()})
        return names

    def _find_combo_refresh(self, _filter_text: str = ""):
        """Rebuild the Find Item backing list: every block, node, event
        and hierarchy, prefixed by kind and carrying its id, so Enter can
        resolve either a name or an id. Called whenever the model changes
        (load, edit, undo/redo) -- not on every keystroke; typing is
        handled by the Model Explorer's own filter, which is the only
        place the results are shown."""
        # The dock can be absent while the window is still being built.
        entries = (find_entries(self._find_names())
                   if getattr(self, "_diagram_dock", None) is not None else [])
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
        """What the Find box holds -> `(kind, name)`, or None (see `find_text`)."""
        return resolve_find_text(text, self._find_names())

    def _on_find_submit(self, text: str):
        resolved = self._resolve_find_text(text)
        if resolved is None:
            self._statusbar.showMessage(
                f"No element matches '{text.strip()}'", 4000)
            return
        kind, name = resolved
        v = self._diagram_dock.view
        # By id, on whatever level the element lives on (`locate_id` goes
        # there first). Of two elements with one name, the one on this
        # level wins.
        here: dict[str, set[int]] = {
            "Block": set(v.blocks), "Node": set(v.nodes), "Event": set(v.events),
            "Hier": {i for i, h in v.hiers.items()
                     if getattr(h, "parent_id", 0) == v._current_parent}}
        table = self._find_names().get(kind, {})
        ids = [i for i, n in table.items() if n.lower() == name.lower()]
        ids.sort(key=lambda i: (i not in here.get(kind, set()), i))
        parsed = parse_find_text(text)
        if parsed is not None and parsed[2] in ids:
            ids.insert(0, parsed[2])     # the id it was given, exactly
        if not ids or not v.locate_id(kind.lower(), ids[0]):
            self._statusbar.showMessage(
                f"No element matches '{text.strip()}'", 4000)
            return
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

"""The ribbon's find box and zoom controls.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations


from typing import TYPE_CHECKING, Any





from raptor.gui.find_text import find_entries, resolve_find_text

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

    def _find_names(self) -> dict[str, dict[int, str]]:
        """The diagram's current-level elements as the plain tables `find_text` takes."""
        v = self._diagram_dock.view
        return {"Block": {i: b.name for i, b in v.blocks.items()},
                "Node": {i: n.node_name for i, n in v.nodes.items()},
                "Event": {i: e.name for i, e in v.events.items()},
                "Hier": {i: h.name for i, h in v.hiers.items()}}

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

        # Block/Node go through locate_element (navigates into whatever
        # hierarchy level the element actually lives on first, if not
        # the one currently shown -- v.blocks/v.nodes are level-scoped,
        # so a same-level-only lookup would silently do nothing for an
        # element on another level).
        if kind in ("Block", "Node"):
            v.locate_element(kind.lower(), name)
            self._find_edit.clear()
            return

        target: Any = None     # a _EventItem or a _HierItem
        if kind == "Event":
            target = next((it for eid, it in v._event_items.items()
                           if v.events[eid].name == name), None)
        elif kind == "Hier":
            hid = next((h for h in v.hiers if v.hiers[h].name == name),
                       None)
            target = v._hier_items.get(hid) if hid is not None else None
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

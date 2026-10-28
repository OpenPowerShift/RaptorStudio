"""Model explorer dock — tree view of loaded RBD structure.

Reads from DiagramView.blocks/nodes/links (live model objects), not raw RbdFile.
"""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
)

from raptor.gui.ui.diagram import DiagramView


class ExplorerDock(QWidget):
    block_selected = Signal(int)
    node_selected = Signal(int)
    #: Events are listed in this tree alongside blocks and nodes, so the
    #: Find box searches them too.
    event_selected = Signal(int)
    system_config_selected = Signal()
    hier_selected = Signal(int)
    #: Double-click a hierarchy folder (descend) or an HOut marker
    #: (ascend) — MainWindow wires these to the diagram's navigation.
    hier_activated = Signal(int)  # descend into this hierarchy id
    ascend_requested = Signal()   # leave the current level

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view: DiagramView | None = None
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(16)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._tree)
        self._search_widget = None

    def set_search_widget(self, widget) -> None:
        """Install `widget` (MainWindow's own Find combo, built and wired
        there) above the tree. Adding it here rather than to the ribbon
        re-parents it via Qt's own layout machinery."""
        if self._search_widget is not None:
            self._layout.removeWidget(self._search_widget)
        self._search_widget = widget
        if widget is not None:
            self._layout.insertWidget(0, widget)

    def set_view(self, view: DiagramView | None):
        self._view = view
        self._refresh()

    def filter_items(self, text: str) -> None:
        """Refine the tree in place as the user types, and MARK the hits.

        A row survives when its own label matches or any of its
        descendants matches, so matching a hierarchy's name keeps its
        whole subtree visible and matching a child keeps its hierarchy
        chain visible.

        While a search is active the rows are coloured as well as filtered:
        a title that matches the search directly takes the accent colour,
        and the rows kept only to hold a match go muted. So the search
        *results* are what the tree shows -- which is the point, and why
        the Find box no longer also pops up a list of the same names over
        the top of it. Empty text restores the full tree in the normal
        colour.
        """
        from PySide6.QtGui import QBrush, QColor
        from raptor.gui.resources.design_tokens import active_colour
        text = (text or "").strip().lower()
        c = active_colour()
        normal = QBrush(QColor(c["text"]))
        accent = QBrush(QColor(c["accent"]))
        muted = QBrush(QColor(c["muted"]))
        root = self._tree.topLevelItem(0)
        if root is None:
            return
        if not text:
            def show_all(item):
                item.setHidden(False)
                item.setForeground(0, normal)
                if item.text(0).startswith("⬭"):
                    item.setExpanded(True)
                for i in range(item.childCount()):
                    show_all(item.child(i))
            show_all(root)
            root.setExpanded(True)
            return

        def keep(item) -> bool:
            child_keep = False
            for i in range(item.childCount()):
                if keep(item.child(i)):
                    child_keep = True
            own = text in item.text(0).lower()
            visible = own or child_keep
            item.setHidden(not visible)
            if visible:
                # A hit wears the accent; an ancestor kept only to reach a
                # hit recedes. Anything not part of the search is hidden,
                # so the two states here are the whole story.
                item.setForeground(0, accent if own else muted)
                item.setExpanded(True)
            return visible

        keep(root)
        root.setForeground(0, normal)
        root.setExpanded(True)

    def refresh(self):
        """Re-read the view's CURRENT level (called after hierarchy
        navigation -- `_rebuild_scene` runs under `_loading`, so its
        `model_changed` never fires and the tree would go stale)."""
        self._refresh()

    def _refresh(self):
        self._tree.clear()
        if self._view is None:
            return

        # Hierarchy-aware tree: elements nest under their ParentId, hier
        # ovals become folders.
        hiers = getattr(self._view, "hiers", {}) or {}
        children: dict[int, list] = {0: [], **{hid: [] for hid in hiers}}

        def bucket(parent_id: int, make_item):
            key = parent_id if parent_id in children else 0
            children[key].append(make_item())

        # EVERY row shows its id, in brackets. It was only blocks whose name
        # happened to repeat that got one, so `Find` could not be used to
        # look an element up by id at all -- and ids are how the rest of
        # the app (the canvas context menu, the Inspector, the exported
        # tables, the engine's own messages) identifies an element. Showing
        # it on the row also makes the id searchable, since the filter
        # matches the row text.
        def block_label(b, bid):
            return f"{b.name} ({bid})"

        for bid in sorted(self._view.blocks):
            b = self._view.blocks[bid]
            bucket(b.parent_id, lambda b=b, bid=bid: (
                QTreeWidgetItem([block_label(b, bid)]),
                f"block:{bid}", f"Block ID {bid}: {b.name}"))
        type_labels = {1: "Start", 2: "Reg", 3: "End", 4: "HIn", 5: "HOut"}
        for nid in sorted(self._view.nodes):
            n = self._view.nodes[nid]
            t = type_labels.get(n.type_node, "?")
            bucket(n.parent_id, lambda n=n, t=t, nid=nid: (
                QTreeWidgetItem([f"[{t}] {n.node_name} ({nid})"]),
                f"node:{nid}", f"Node ID {nid}: {n.node_name} (type={t})"))
        for eid in sorted(getattr(self._view, "events", {})):
            e = self._view.events[eid]
            bucket(e.parent_id, lambda e=e, eid=eid: (
                QTreeWidgetItem([f"⬡ {e.name} ({eid})"]),
                f"event:{eid}", f"Event ID {eid}: {e.name}"))
        hier_items: dict[int, QTreeWidgetItem] = {}
        for hid in sorted(hiers):
            h = hiers[hid]

            def make(h=h, hid=hid):
                item = QTreeWidgetItem([f"⬭ {h.name} ({hid})"])
                hier_items[hid] = item
                return (item, f"hier:{hid}", f"Hierarchy ID {hid}: {h.name} "
                        f"(level {h.level})")
            bucket(h.parent_id, make)
        # No link/wire rows: the diagram is the link's own presentation, and
        # in the tree they would be unreadable id pairs that drown the
        # elements.

        def build(key: int) -> QTreeWidgetItem | None:
            entries = children.get(key, [])
            if not entries:
                return None
            if key == 0:
                folder = QTreeWidgetItem(["Model"])
                folder.setData(0, Qt.ItemDataRole.UserRole, "root")
                font = folder.font(0)
                font.setBold(True)
                folder.setFont(0, font)
            else:
                # Reuse the hierarchy's own tree item as its folder rather
                # than creating a second item with the same name, which
                # would show every hierarchy's name twice.
                folder = hier_items[key]
            for item, role, tip in entries:
                item.setData(0, Qt.ItemDataRole.UserRole, role)
                if tip:
                    item.setToolTip(0, tip)
                if role.startswith("hier:"):
                    build(int(role.split(":")[1]))
                    item.setExpanded(True)
                folder.addChild(item)
            return folder

        root = build(0)
        if root is not None:
            # Project Settings stays a first-class entry.
            sys = QTreeWidgetItem(["Project Settings"])
            sys.setData(0, Qt.ItemDataRole.UserRole, "system")
            root.insertChild(0, sys)
            self._tree.addTopLevelItem(root)
            root.setExpanded(True)
            for i in range(root.childCount()):
                child = root.child(i)
                if child.text(0).startswith("⬭"):
                    child.setExpanded(True)
        # Re-apply the Find box's live filter after a rebuild, so an
        # active search survives model edits / hierarchy navigation.
        sw = getattr(self, "_search_widget", None)
        if sw is not None and sw.text():
            self.filter_items(sw.text())

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        role = item.data(0, Qt.ItemDataRole.UserRole)
        if role == "system":
            self.system_config_selected.emit()
        elif isinstance(role, str) and role.startswith("block:"):
            self.block_selected.emit(int(role.split(":", 1)[1]))
        elif isinstance(role, str) and role.startswith("node:"):
            self.node_selected.emit(int(role.split(":", 1)[1]))
        elif isinstance(role, str) and role.startswith("event:"):
            self.event_selected.emit(int(role.split(":", 1)[1]))
        elif isinstance(role, str) and role.startswith("hier:"):
            self.hier_selected.emit(int(role.split(":", 1)[1]))

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        role = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(role, str) and role.startswith("hier:"):
            self.hier_activated.emit(int(role.split(":", 1)[1]))
        elif isinstance(role, str) and role.startswith("node:"):
            nid = int(role.split(":", 1)[1])
            n = (self._view.nodes.get(nid) if self._view is not None
                 else None)
            # Double-clicking an Out marker ascends — same as the canvas.
            if n is not None and getattr(n, "type_node", 0) == 5:
                self.ascend_requested.emit()

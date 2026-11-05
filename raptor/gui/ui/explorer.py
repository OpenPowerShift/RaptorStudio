"""Model explorer dock — tree view of loaded RBD structure.

Reads from DiagramView.blocks/nodes/links (live model objects), not raw RbdFile.
"""
from __future__ import annotations
from collections import deque

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QToolButton, QTreeWidget, QTreeWidgetItem,
    QTreeWidgetItemIterator, QVBoxLayout, QWidget,
)

from raptor.core.fileio.typed import row_int
from raptor.gui.ui.diagram import DiagramView

#: Node type -> the tag shown before a node's name.
_NODE_TYPE_LABELS = {1: "Start", 2: "Reg", 3: "End", 4: "HIn", 5: "HOut"}


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
        self._search_widget = None

        # Expand/collapse controls. Expanding is chunked across event-loop
        # turns: `QTreeWidget.expandAll()` on a 10k-row level freezes the
        # window while Qt lays every row out at once.
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 4, 4, 0)
        bar.setSpacing(2)
        self._expand_button = self._tool_button(
            "Expand all",
            "Expand every hierarchy folder, so the whole model is listed")
        self._expand_button.clicked.connect(self.expand_all)
        self._collapse_button = self._tool_button(
            "Collapse all", "Collapse every hierarchy folder")
        self._collapse_button.clicked.connect(self.collapse_all)
        bar.addWidget(self._expand_button)
        bar.addWidget(self._collapse_button)
        bar.addStretch(1)
        self._retheme_buttons()
        from raptor.gui.resources.design_tokens import ThemeManager
        ThemeManager.instance().on_changed(self._retheme_buttons)
        self._layout.addLayout(bar)
        self._layout.addWidget(self._tree)

        self._expand_queue: deque = deque()
        self._expand_timer = QTimer(self)
        self._expand_timer.setInterval(0)
        self._expand_timer.timeout.connect(self._expand_chunk)

    @staticmethod
    def _tool_button(name: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setAutoRaise(True)
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tip)
        button.setAccessibleName(name)
        return button

    def _retheme_buttons(self) -> None:
        from raptor.gui.resources import icons
        from raptor.gui.resources.design_tokens import active_colour
        colour = active_colour()["text"]
        self._expand_button.setIcon(icons.expand_all_icon(colour))
        self._collapse_button.setIcon(icons.collapse_all_icon(colour))

    def set_search_widget(self, widget) -> None:
        """Install `widget` (MainWindow's own Find combo, built and wired
        there) above the tree. Adding it here rather than to the ribbon
        re-parents it via Qt's own layout machinery."""
        if self._search_widget is not None:
            self._layout.removeWidget(self._search_widget)
        self._search_widget = widget
        if widget is not None:
            self._layout.insertWidget(0, widget)

    def expand_all(self) -> None:
        """Expand every folder, in bounded chunks so the UI stays alive."""
        if self._expand_timer.isActive():
            return
        self._expand_queue.clear()
        iterator = QTreeWidgetItemIterator(self._tree)
        while iterator.value() is not None:
            item = iterator.value()
            if item.childCount():
                self._expand_queue.append(item)
            iterator += 1
        if not self._expand_queue:
            return
        self._tree.setUpdatesEnabled(False)
        # Collapse all stays enabled: it is how the user cancels this.
        self._expand_button.setEnabled(False)
        self._expand_timer.start()

    def _expand_chunk(self) -> None:
        # 150 folders per turn: enough to finish a normal level in one
        # frame, small enough that a 10k-element level never blocks input.
        for _ in range(150):
            if not self._expand_queue:
                break
            self._expand_queue.popleft().setExpanded(True)
        if self._expand_queue:
            return
        self._cancel_expand()

    def _cancel_expand(self) -> None:
        self._expand_timer.stop()
        self._expand_queue.clear()
        self._tree.setUpdatesEnabled(True)
        self._expand_button.setEnabled(True)

    def collapse_all(self) -> None:
        """Collapse every folder, cancelling an in-progress Expand all."""
        self._cancel_expand()
        self._tree.collapseAll()

    def set_view(self, view: DiagramView | None):
        self._view = view
        self._refresh()

    def filter_items(self, text: str) -> None:
        """Refine the tree in place as the user types, and MARK the hits.

        A row survives when its own label matches or any of its
        descendants matches, so matching a hierarchy's name keeps its
        whole subtree visible and matching a child keeps its hierarchy
        chain visible.

        The walk visits every descendant, including rows under a collapsed
        folder, and expands the chain to each hit. So a search finds items
        that are not currently visible because their parent is closed;
        no Expand all is needed first.

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
                for i in range(item.childCount()):
                    show_all(item.child(i))
            show_all(root)
            self._apply_default_expansion()
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
        """Rebuild the tree from the whole model (after edits that bypass
        `model_changed`, such as renames and file loads)."""
        self._refresh()

    def _model_elements(self) -> dict[str, dict[int, tuple[str, int, int]]]:
        """Every block, node and event in the WHOLE model:
        kind -> {id: (name, parent hierarchy id, node type)}.

        The canvas's element tables are level-scoped (only the level on
        screen), so building the tree from them listed one level and left
        Expand all and Find blind to everything inside the hierarchies.
        The model rows hold every level. Only the four columns the tree
        shows are read -- not a full parse, which would cost seconds per
        refresh on a large model. The level on screen comes from the live
        objects instead, so an edit not yet written back still shows."""
        view = self._view
        out: dict[str, dict[int, tuple[str, int, int]]] = {
            "block": {}, "node": {}, "event": {}}
        if view is None:
            return out
        rbd = getattr(view, "_rbd_ref", None)
        columns = (("block", "BLOCK_DATA", "blockName", "ParentId",
                    view.blocks, "name"),
                   ("node", "NODE_DATA", "Name", "parent",
                    view.nodes, "node_name"),
                   ("event", "EVENT_DATA", "eventName", "ParentId",
                    getattr(view, "events", {}) or {}, "name"))
        for kind, section, name_col, parent_col, live, attr in columns:
            table = out[kind]
            sec = rbd.sections.get(section) if rbd is not None else None
            for row in (sec.rows if sec is not None else ()):
                oid = row_int(row.get("ID"), 0)
                if oid <= 0:            # ID 0 is the file's column template
                    continue
                table[oid] = (row.get(name_col, ""),
                              row_int(row.get(parent_col), 0),
                              row_int(row.get("Type"), 0)
                              if kind == "node" else 0)
            for oid, obj in live.items():
                table[oid] = (getattr(obj, attr, "") or "",
                              getattr(obj, "parent_id", 0) or 0,
                              getattr(obj, "type_node", 0) or 0)
        return out

    def _refresh(self):
        # A rebuild deletes the items an in-progress Expand all still holds.
        self._cancel_expand()
        self._tree.clear()
        self._hier_tree_items: dict[int, QTreeWidgetItem] = {}
        if self._view is None:
            return

        # Hierarchy-aware tree of the whole model: elements nest under
        # their ParentId, hierarchy ovals become folders.
        hiers = getattr(self._view, "hiers", {}) or {}
        children: dict[int, list] = {0: [], **{hid: [] for hid in hiers}}

        def bucket(parent_id: int, entry):
            key = parent_id if parent_id in children else 0
            children[key].append(entry)

        # EVERY row shows its id, in brackets: ids are how the rest of the
        # app (context menu, Inspector, exported tables, engine messages)
        # identifies an element, and showing it makes it searchable.
        # Items are made lazily (`make`) only for rows that get placed.
        elements = self._model_elements()
        if not hiers and not any(elements.values()):
            return                  # no model open: an empty workbench
        for bid, (name, parent, _t) in sorted(elements["block"].items()):
            bucket(parent, (f"{name} ({bid})", f"block:{bid}",
                            f"Block ID {bid}: {name}"))
        for nid, (name, parent, ntype) in sorted(elements["node"].items()):
            t = _NODE_TYPE_LABELS.get(ntype, "?")
            bucket(parent, (f"[{t}] {name} ({nid})", f"node:{nid}",
                            f"Node ID {nid}: {name} (type={t})"))
        for eid, (name, parent, _t) in sorted(elements["event"].items()):
            bucket(parent, (f"⬡ {name} ({eid})", f"event:{eid}",
                            f"Event ID {eid}: {name}"))
        for hid in sorted(hiers):
            h = hiers[hid]
            bucket(h.parent_id, (f"⬭ {h.name} ({hid})", f"hier:{hid}",
                                 f"Hierarchy ID {hid}: {h.name} "
                                 f"(level {h.level})"))
        # No link/wire rows: the diagram is the link's own presentation, and
        # in the tree they would be unreadable id pairs that drown the
        # elements.

        def build(key: int, folder: QTreeWidgetItem) -> None:
            items = []
            for label, role, tip in children.get(key, ()):
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, role)
                if tip:
                    item.setToolTip(0, tip)
                if role.startswith("hier:"):
                    hid = int(role.split(":")[1])
                    self._hier_tree_items[hid] = item
                    build(hid, item)
                items.append(item)
            folder.addChildren(items)       # one insert per folder

        root = QTreeWidgetItem(["Model"])
        root.setData(0, Qt.ItemDataRole.UserRole, "root")
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)
        # Project Settings stays a first-class entry.
        system = QTreeWidgetItem(["Project Settings"])
        system.setData(0, Qt.ItemDataRole.UserRole, "system")
        root.addChild(system)
        build(0, root)
        self._tree.addTopLevelItem(root)
        self._apply_default_expansion()
        # Re-apply the Find box's live filter after a rebuild, so an
        # active search survives model edits / hierarchy navigation.
        sw = getattr(self, "_search_widget", None)
        if sw is not None and sw.text():
            self.filter_items(sw.text())

    def _apply_default_expansion(self) -> None:
        """Open the root, the top-level hierarchies and the chain down to
        the level on screen. Not every folder: with the whole model listed,
        a large model would lay out thousands of rows on every refresh.
        Expand all opens the rest, in chunks."""
        root = self._tree.topLevelItem(0)
        if root is None:
            return
        root.setExpanded(True)
        for i in range(root.childCount()):
            child = root.child(i)
            role = child.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(role, str) and role.startswith("hier:"):
                child.setExpanded(True)
        self.reveal_level(getattr(self._view, "_current_parent", 0) or 0)

    def reveal_level(self, parent_id: int) -> None:
        """Expand the folders from the root down to hierarchy `parent_id`
        (the level on screen). Nothing is rebuilt: the tree lists the whole
        model whichever level is shown."""
        hiers = getattr(self._view, "hiers", {}) or {}
        items = getattr(self, "_hier_tree_items", {})
        seen: set[int] = set()
        hid = parent_id
        while hid and hid in items and hid not in seen:
            seen.add(hid)
            items[hid].setExpanded(True)
            hid = getattr(hiers.get(hid), "parent_id", 0) or 0

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

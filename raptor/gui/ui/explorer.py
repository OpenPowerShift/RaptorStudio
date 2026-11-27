"""Model explorer dock — tree view of loaded RBD structure.

Reads from DiagramView.blocks/nodes/links (live model objects), not raw RbdFile.
"""
from __future__ import annotations
from collections import deque

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QToolButton, QTreeWidget, QTreeWidgetItem,
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
    ascend_requested = Signal(int)  # leave this hierarchy (the marker's own)
    #: The tag view changed: a set of element refs, or None for all.
    tag_view_changed = Signal(object)
    #: The selector's "Manage tags…" entry was chosen.
    manage_tags_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view: DiagramView | None = None
        #: The last group refs sent to the canvas, so a plain Explorer
        #: refresh does not reset focus dimming by re-emitting "all".
        self._emitted_group: object = object()
        #: A temporary "no elements" row under the root, owned by the tree.
        self._empty_item: QTreeWidgetItem | None = None
        #: The active view's refs, so typing in Find does not rebuild the
        #: tag index on every keystroke. `_refresh` invalidates it.
        self._group_refs: set[str] = set()
        self._group_refs_for: tuple[str, int] | None = None
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
        # A tag view. Tags are navigation metadata, not a second hierarchy:
        # this filters the same whole-model tree. Editable, so a model
        # with hundreds of tags is searched rather than scrolled.
        self._group_combo = QComboBox()
        # Model names, not chrome: never run them through the catalogue.
        self._group_combo.setProperty("raptor_i18n_data_items", True)
        self._group_combo.setToolTip(
            "Show only the elements carrying a tag. Type to search.")
        self._group_combo.setAccessibleName("Filter by tag")
        self._group_combo.setMinimumWidth(120)
        self._group_combo.setMaximumWidth(200)
        self._group_combo.setMaxVisibleItems(20)
        self._group_combo.setEditable(True)
        self._group_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        completer = self._group_combo.completer()
        if completer is not None:
            from PySide6.QtWidgets import QCompleter
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(
                QCompleter.CompletionMode.PopupCompletion)
        # Wide enough for its current entry ("Todos os elementos" was cut
        # off at the minimum width), never wider than the cap above.
        self._group_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._group_combo.currentIndexChanged.connect(
            self._on_group_filter_changed)
        line = self._group_combo.lineEdit()
        if line is not None:
            line.editingFinished.connect(self._restore_combo_text)
        bar.addWidget(self._group_combo)
        # "Tag within a hierarchy": only the tagged elements inside the
        # hierarchy shown on the canvas, at any depth.
        self._within_button = self._tool_button(
            "Here only",
            "Only the tagged elements inside the hierarchy shown on the "
            "canvas (at any depth)")
        self._within_button.setCheckable(True)
        self._within_button.toggled.connect(
            lambda _on: self._on_group_filter_changed())
        bar.addWidget(self._within_button)
        self._retheme_buttons()
        from raptor.gui.resources.design_tokens import ThemeManager
        ThemeManager.instance().on_changed(self._retheme_buttons)
        self._layout.addLayout(bar)
        self._layout.addWidget(self._tree)

        #: The tree item marked as the level on screen (see `mark_current`).
        self._current_item: QTreeWidgetItem | None = None
        self._current_parent_id = 0
        ThemeManager.instance().on_changed(self._restyle_current)
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

    def _retheme_buttons(self, *_theme) -> None:
        from raptor.gui.resources import icons
        from raptor.gui.resources.design_tokens import active_colour
        colour = active_colour()["text"]
        self._expand_button.setIcon(icons.expand_all_icon(colour))
        self._collapse_button.setIcon(icons.collapse_all_icon(colour))
        # An active search paints rows in the theme's accent/muted brushes;
        # repaint them in the new theme's colours.
        sw = getattr(self, "_search_widget", None)
        if getattr(self, "_tree", None) is not None and sw is not None \
                and sw.text():
            self.filter_items(sw.text())

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
        self._emitted_group = object()      # a different model entirely
        self._refresh()

    def _selected_group(self) -> tuple[str, int] | None:
        """The active tag view as `(tag, within)`, or None for the whole
        model. `within` is the canvas hierarchy id with Here only on, else
        0."""
        combo = getattr(self, "_group_combo", None)
        if combo is None:
            return None
        data = combo.currentData()
        if not isinstance(data, str) or not data or data == self._MANAGE:
            return None
        within = 0
        button = getattr(self, "_within_button", None)
        if button is not None and button.isChecked():
            within = self._level_on_screen()
        return data, within

    def _level_on_screen(self) -> int:
        """The hierarchy the canvas shows (0 at Home)."""
        if self._view is not None:
            return int(getattr(self._view, "_current_parent", 0) or 0)
        return int(getattr(self, "_current_parent_id", 0) or 0)

    #: Item data of the selector's last entry, which opens the editor.
    _MANAGE = "\x00manage"

    def _restore_combo_text(self) -> None:
        """Typed text that is not a tag: show the chosen entry again."""
        combo = self._group_combo
        if combo.findText(combo.currentText()) < 0:
            combo.setEditText(combo.itemText(combo.currentIndex()))

    def _on_group_filter_changed(self, _index: int = 0) -> None:
        """A different tag view: rebuild, then re-apply Find."""
        combo = self._group_combo
        if combo.currentData() == self._MANAGE:
            # An action, not a view: put the previous choice back first.
            combo.blockSignals(True)
            combo.setCurrentIndex(getattr(self, "_group_index", 0))
            combo.blockSignals(False)
            self.manage_tags_requested.emit()
            return
        self._group_index = combo.currentIndex()
        self._update_within_button()
        self._refresh()
        # Clearing the selector must also un-dim the canvas; `_refresh`
        # only emits while a tag is active.
        self._emit_group_view()

    def _update_within_button(self) -> None:
        button = getattr(self, "_within_button", None)
        if button is None:
            return
        tag_on = isinstance(self._group_combo.currentData(), str) and \
            self._group_combo.currentData() not in ("", self._MANAGE)
        level = self._level_on_screen()
        button.setEnabled(tag_on and level > 0)
        name = ""
        if level > 0 and self._view is not None:
            rbd = getattr(self._view, "_rbd_ref", None)
            sec = (getattr(rbd, "sections", None) or {}).get("HIERARCHY_DATA")
            from raptor.core.fileio.typed import row_int
            for row in (sec.rows if sec is not None else ()):
                if row_int(row.get("ID"), 0) == level:
                    name = str(row.get("hierarchyName") or "")
                    break
        button.setToolTip(
            f"Only the tagged elements inside “{name}”, at any depth"
            if name else
            "Only the tagged elements inside the hierarchy shown on the "
            "canvas (at any depth); open a hierarchy to use it")

    def _group_refs_now(self) -> set[str]:
        """The active tag view's refs, cached until the view changes."""
        group = self._selected_group()
        if group is None:
            return set()
        if group != self._group_refs_for:
            from raptor.core import tags as tag_model
            rbd = getattr(self._view, "_rbd_ref", None) if self._view else None
            self._group_refs_for = group
            self._group_refs = (tag_model.refs_for_view(rbd, *group)
                                if rbd is not None else set())
        return self._group_refs

    def _emit_group_view(self) -> None:
        group = self._selected_group()
        value = None if group is None else frozenset(self._group_refs_now())
        if value != self._emitted_group:
            self._emitted_group = value
            self.tag_view_changed.emit(set(value) if value is not None else None)

    def current_tag(self) -> str:
        """The tag the Explorer shows, or "" for the whole model."""
        group = self._selected_group()
        return group[0] if group is not None else ""

    def show_tag(self, tag: str) -> bool:
        """Select *tag* in the tag box ("" for All elements): the canvas
        legend's click and the results Tag filter."""
        index = self._group_combo.findData(tag) if tag else 0
        if index < 0:
            return False
        self._group_combo.setCurrentIndex(index)
        return True

    def refresh_groups(self) -> None:
        """Fill the tag selector from the model, keeping the choice."""
        combo = getattr(self, "_group_combo", None)
        if combo is None:
            return
        from raptor.core import tags as tag_model
        from raptor.gui.ui.tags_panel import swatch
        rbd = getattr(self._view, "_rbd_ref", None) if self._view else None
        self._group_refs_for = None    # the model may have changed
        names = tag_model.all_tags(rbd) if rbd is not None else []
        counts = tag_model.tag_index(rbd) if rbd is not None else {}
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        from raptor.gui import i18n
        # The entries are model names, so the widget pass leaves them
        # alone: translate the fixed ones here.
        combo.addItem(i18n.tr("All elements"), None)
        combo.setItemData(0, "Show the whole model", Qt.ItemDataRole.ToolTipRole)
        wanted = 0
        for tag in names:
            combo.addItem(swatch([tag_model.tag_colour(rbd, tag)]), tag, tag)
            note = tag_model.tag_description(rbd, tag)
            count = len(counts.get(tag, ()))
            combo.setItemData(
                combo.count() - 1,
                f"{tag} — {count} element(s)" + (f". {note}" if note else ""),
                Qt.ItemDataRole.ToolTipRole)
            if tag == current:
                wanted = combo.count() - 1
        combo.insertSeparator(combo.count())
        combo.addItem(i18n.tr("Manage tags…"), self._MANAGE)
        combo.setItemData(
            combo.count() - 1,
            i18n.tr("Create tags, apply them, and choose their colours"),
            Qt.ItemDataRole.ToolTipRole)
        combo.setCurrentIndex(wanted)
        self._group_index = wanted
        combo.setEnabled(rbd is not None)
        combo.blockSignals(False)
        self._update_within_button()

    def filter_items(self, text: str) -> None:
        """Refine the tree in place as the user types, and MARK the hits.

        A row survives when its own label matches or any of its
        descendants matches, so matching a hierarchy's name keeps its
        whole subtree visible and matching a child keeps its hierarchy
        chain visible.

        A tag view is applied at the same time: the row must carry the tag
        (or be a folder holding one that does) *and* match the Find text.
        Tags are navigation metadata, so this filters the same whole-model
        tree rather than changing the simulated structure.

        While a search is active the rows are coloured as well as filtered:
        a title that matches the search directly takes the accent colour,
        and the rows kept only to hold a match go muted. Empty text restores
        the full tree in the normal colour.
        """
        from PySide6.QtGui import QBrush, QColor
        from raptor.gui.resources.design_tokens import active_colour
        text = (text or "").strip().lower()
        group = self._selected_group()
        selected = self._group_refs_now() if group is not None else set()
        c = active_colour()
        normal = QBrush(QColor(c["text"]))
        accent = QBrush(QColor(c["accent"]))
        muted = QBrush(QColor(c["muted"]))
        root = self._tree.topLevelItem(0)
        if root is None:
            return
        self._clear_empty_state()
        if not text and group is None:
            def show_all(item):
                item.setHidden(False)
                # No explicit colour: the row follows the palette, so a
                # later theme switch repaints it (a stored brush kept the
                # light theme's dark text on the dark background).
                item.setData(0, Qt.ItemDataRole.ForegroundRole, None)
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
            role = item.data(0, Qt.ItemDataRole.UserRole)
            label_hit = text in item.text(0).lower() if text else True
            if role == "root":
                own = True
            elif role == "system":
                # Project Settings is not a component: hide it while a
                # group view is active, and let Find reach it otherwise.
                own = group is None and (not text or label_hit)
            elif isinstance(role, str) and ":" in role:
                member = group is None or role in selected
                own = member and label_hit
            else:
                own = label_hit
            visible = own or child_keep
            item.setHidden(not visible)
            if visible:
                if text:
                    # A hit wears the accent; an ancestor kept only to
                    # reach a hit recedes.
                    item.setForeground(0, accent if own and role != "root"
                                      else normal if role == "root" else muted)
                    item.setExpanded(True)
                else:
                    item.setData(0, Qt.ItemDataRole.ForegroundRole, None)
            return visible

        keep(root)
        root.setData(0, Qt.ItemDataRole.ForegroundRole, None)
        root.setExpanded(True)
        # A view with nothing in it used to leave an empty panel: the user
        # could not tell "no members" from a failed filter.
        if not any(not root.child(i).isHidden()
                   for i in range(root.childCount())):
            self._show_empty_state(group, text, muted)

    def _clear_empty_state(self) -> None:
        item = getattr(self, "_empty_item", None)
        self._empty_item = None
        if item is None:
            return
        root = self._tree.topLevelItem(0)
        if root is not None:
            root.removeChild(item)

    def _show_empty_state(self, group, text: str, muted) -> None:
        """One muted, unselectable row saying why the tree is empty."""
        if group is not None:
            label = self._group_combo.currentText()
            here = " here" if group[1] else ""
            message = (f"No elements tagged “{label}”{here} match “{text}”."
                       if text else
                       f"No elements tagged “{label}”{here} — tag them in "
                       "Data ▸ Tags…")
        else:
            message = f"No elements match “{text}”."
        root = self._tree.topLevelItem(0)
        if root is None:
            return
        item = QTreeWidgetItem([message])
        item.setData(0, Qt.ItemDataRole.UserRole, "empty")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(0, muted)
        item.setToolTip(0, message)
        root.addChild(item)
        self._empty_item = item

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
        self._empty_item = None        # owned (and deleted) by the tree
        self.refresh_groups()
        self._hier_tree_items: dict[int, QTreeWidgetItem] = {}
        #: node id -> (node type, parent hierarchy id), for double-click.
        self._node_info: dict[int, tuple[int, int]] = {}
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
        self._node_info = {nid: (ntype, parent) for nid, (_n, parent, ntype)
                           in elements["node"].items()}
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
        # Re-apply Find and the group/tag view after a rebuild, so both
        # survive model edits and hierarchy navigation.
        sw = getattr(self, "_search_widget", None)
        self.filter_items(sw.text() if sw is not None else "")
        self._emit_group_view()

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
        self.mark_current(parent_id)

    # ------------------------------------------------- the level on screen
    def _current_tree_item(self, parent_id: int) -> QTreeWidgetItem | None:
        if parent_id:
            return getattr(self, "_hier_tree_items", {}).get(parent_id)
        return self._tree.topLevelItem(0)           # Home is the "Model" root

    def mark_current(self, parent_id: int) -> None:
        """Highlight the hierarchy (or Home) shown on the canvas: bold, a
        tinted background and a tooltip note, apart from the selection
        colour. Only the previous and the new item are touched; the tree is
        not rebuilt, and the item is scrolled into view."""
        from shiboken6 import isValid
        moved = (parent_id or 0) != getattr(self, "_current_parent_id", 0)
        self._current_parent_id = parent_id or 0
        if moved and hasattr(self, "_within_button"):
            self._update_within_button()
            if self._within_button.isChecked():
                self._refresh()
                self._emit_group_view()
        previous = self._current_item
        if previous is not None and isValid(previous):
            self._style_item(previous, False)
        item = self._current_tree_item(self._current_parent_id)
        self._current_item = item
        if item is None:
            return
        self._style_item(item, True)
        self._tree.scrollToItem(item)

    def _restyle_current(self, *_theme) -> None:
        """A theme change: repaint the mark in the new colours."""
        from shiboken6 import isValid
        item = self._current_item
        if item is not None and isValid(item):
            self._style_item(item, True)

    @staticmethod
    def _style_item(item: QTreeWidgetItem, current: bool) -> None:
        from PySide6.QtGui import QBrush, QColor
        from raptor.gui.resources.design_tokens import active_colour
        font = item.font(0)
        role = item.data(0, Qt.ItemDataRole.UserRole)
        # The root is always bold; a hierarchy is bold only while current.
        font.setBold(bool(current or role == "root"))
        item.setFont(0, font)
        tip = item.toolTip(0).replace("  (current level)", "")
        if current:
            tint = QColor(active_colour()["accent"])
            tint.setAlpha(80)
            item.setBackground(0, QBrush(tint))
            item.setToolTip(0, (tip or item.text(0)) + "  (current level)")
        else:
            item.setData(0, Qt.ItemDataRole.BackgroundRole, None)
            item.setToolTip(0, tip)

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
            # Double-clicking an Out marker ascends — same as the canvas —
            # from the marker's OWN hierarchy, which need not be the level
            # on screen: the tree lists the whole model.
            ntype, parent = self._node_info.get(nid, (0, 0))
            if ntype == 5 and parent:
                self.ascend_requested.emit(parent)

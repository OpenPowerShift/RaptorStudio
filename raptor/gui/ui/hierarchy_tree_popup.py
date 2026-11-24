"""A searchable popup for choosing hierarchies (chart Scope and Compare).

A model can hold thousands of hierarchies nested eight deep, which a flat
menu cannot show.  The picker is one pop-up frame holding a search box and a
tree, so it stays a fixed size however big the model is:

* it opens collapsed below the first level, with the current choice's path
  open and the choice itself selected and scrolled into view;
* typing filters by name or by full path (``Intake > Pumps``), keeps each
  match's ancestors visible and open, and shows ``N of M`` (debounced);
* siblings keep the order of the attribution rows, which is largest share of
  downtime first, so the likely targets are on top without a second sort that
  would disagree with the tables;
* the tree is one QTreeWidget item per hierarchy, never one QAction.

Two modes: ``scope`` picks one hierarchy (or the whole system) and closes;
``compare`` toggles up to four and applies as they change.
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from raptor.gui import i18n
from raptor.gui.resources.design_tokens import SPACING, active_colour, px

#: The comparison charts draw at most this many branches.
COMPARE_LIMIT = 4
FILTER_DELAY_MS = 150
_REF = Qt.ItemDataRole.UserRole
_PATH = Qt.ItemDataRole.UserRole + 1
_NAME = Qt.ItemDataRole.UserRole + 2

#: Mode names.
SCOPE = "scope"
COMPARE = "compare"


def _share_text(row: dict[str, Any]) -> str:
    mean = (row.get("dtci") or {}).get("mean")
    if mean is None:
        return ""
    return f"{float(mean) * 100.0:.1f} %"


class HierarchyTreePopup(QFrame):
    """The popup.  Build with a mode, `set_rows`, then `popup_below(button)`."""

    #: A scope was chosen: the hierarchy id, or 0 for the whole system.
    scope_chosen = Signal(int)
    #: The compare set changed (ids in the order they were ticked).
    compare_chosen = Signal(list)

    def __init__(self, mode: str, parent: QWidget | None = None) -> None:  # noqa: PLR0915
        super().__init__(parent, Qt.WindowType.Popup)
        self.mode = mode
        self.setObjectName("HierarchyTreePopup")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._items: dict[int, QTreeWidgetItem] = {}
        self._all: list[QTreeWidgetItem] = []
        self._rows: list[dict[str, Any]] = []
        self._current: list[int] = []
        self._system_item: QTreeWidgetItem | None = None
        self._building = False
        self.last_build_ms = 0.0
        self.last_filter_ms = 0.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(*(SPACING["sm"],) * 4)
        lay.setSpacing(SPACING["sm"])
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search hierarchies…")
        self.search.setAccessibleName("Search hierarchies")
        self.search.setClearButtonEnabled(True)
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setProperty("raptor_i18n_data", True)
        self.tree.setAccessibleName("Hierarchy list" if mode == SCOPE
                                    else "Hierarchies to compare")
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setRootIsDecorated(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.setFixedHeight(px(320))
        self.tree.itemActivated.connect(self._on_activated)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.tree)
        self.count_label = QLabel("")
        self.count_label.setObjectName("SectionNote")
        self.note_label = QLabel("")
        self.note_label.setObjectName("SectionNote")
        self.note_label.setWordWrap(True)
        self.note_label.hide()
        foot = QHBoxLayout()
        self.top_button: QPushButton | None = None
        self.clear_button: QPushButton | None = None
        if mode == COMPARE:
            self.top_button = QPushButton("Top four by downtime")
            self.top_button.setAutoDefault(False)
            self.top_button.clicked.connect(self._top_four)
            self.clear_button = QPushButton("Clear")
            self.clear_button.setAutoDefault(False)
            self.clear_button.clicked.connect(self._clear)
            foot.addWidget(self.top_button, 1)
            foot.addWidget(self.clear_button)
        lay.addWidget(self.count_label)
        if mode == COMPARE:
            lay.addLayout(foot)
        lay.addWidget(self.note_label)
        self.setFixedWidth(px(380))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(FILTER_DELAY_MS)
        self._timer.timeout.connect(lambda: self.apply_filter(self.search.text()))
        self.search.textChanged.connect(lambda _t: self._timer.start())
        self._style()
        from raptor.gui.resources.design_tokens import ThemeManager
        ThemeManager.instance().on_changed(self._on_theme)
        self.setTabOrder(self.search, self.tree)

    # -- theme -----------------------------------------------------------

    def _on_theme(self, _dark: bool) -> None:
        self._style()

    def _style(self) -> None:
        c = active_colour()
        self.setStyleSheet(
            f"QFrame#HierarchyTreePopup {{ background: {c['surface']};"
            f" border: 1px solid {c['border']}; }}"
            f"QTreeWidget {{ background: {c['surface']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; }}"
            f"QTreeWidget::item:selected {{ background: {c['accent']};"
            f" color: {c['surface']}; }}")

    # -- content ---------------------------------------------------------

    def set_rows(self, rows: Sequence[dict[str, Any]], *,
                 current: Sequence[int] = ()) -> None:
        """Build the tree once for these rows (cheap to call again)."""
        began = time.perf_counter()
        self._building = True
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            self._items.clear()
            self._all = []
            self._rows = list(rows)
            self._current = [int(r) for r in current]
            root = self.tree.invisibleRootItem()
            self._system_item = None
            if self.mode == SCOPE:
                item = QTreeWidgetItem(root, [i18n.tr("System"), ""])
                item.setData(0, _REF, 0)
                item.setData(0, _PATH, "")
                self._system_item = item
            for row in self._rows:
                ref = int(row.get("id") or 0)
                item = QTreeWidgetItem(
                    [str(row.get("name") or ""), _share_text(row)])
                item.setData(0, _REF, ref)
                path = " > ".join(str(p) for p in row.get("path") or [])
                item.setData(0, _PATH, (path or str(row.get("name") or "")).lower())
                item.setData(0, _NAME, str(row.get("name") or "").lower())
                item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
                if self.mode == COMPARE:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                self._items[ref] = item
                self._all.append(item)
            for row in self._rows:
                ref = int(row.get("id") or 0)
                item = self._items[ref]
                parent = self._items.get(int(row.get("parent") or 0))
                target = parent if parent is not None and parent is not item else root
                target.addChild(item)
            self.tree.collapseAll()
            self._apply_current()
        finally:
            self.tree.setUpdatesEnabled(True)
            self._building = False
        self._update_count(len(self._all), len(self._all))
        self.last_build_ms = (time.perf_counter() - began) * 1000.0

    def set_current(self, current: Sequence[int]) -> None:
        """Sync the choice (ids) without rebuilding the tree."""
        self._current = [int(r) for r in current]
        self._building = True
        try:
            self._apply_current()
        finally:
            self._building = False

    def _apply_current(self) -> None:
        if self.mode == COMPARE:
            ticked = set(self._current)
            for ref, item in self._items.items():
                want = (Qt.CheckState.Checked if ref in ticked
                        else Qt.CheckState.Unchecked)
                if item.checkState(0) != want:
                    item.setCheckState(0, want)
                if ref in ticked:  # a ticked row is never hidden in a fold
                    parent = item.parent()
                    while parent is not None and not parent.isExpanded():
                        parent.setExpanded(True)
                        parent = parent.parent()
            self._update_note()
            return
        target = (self._items.get(self._current[0]) if self._current
                  else self._system_item)
        if target is None:
            target = self._system_item
        if target is not None:
            parent = target.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self.tree.setCurrentItem(target)
            self.tree.scrollToItem(target, QAbstractItemView.ScrollHint.PositionAtCenter)

    # -- filtering -------------------------------------------------------

    def apply_filter(self, text: str) -> int:
        """Show matches and their ancestors; return the match count."""
        began = time.perf_counter()
        needle = (text or "").strip().lower()
        self.tree.setUpdatesEnabled(False)
        try:
            if not needle:
                for item in self._all:
                    item.setHidden(False)
                if self._system_item is not None:
                    self._system_item.setHidden(False)
                self.tree.collapseAll()
                self._apply_current()
                matches = len(self._all)
            else:
                matches = self._filter(needle)
        finally:
            self.tree.setUpdatesEnabled(True)
        self._update_count(matches, len(self._all))
        self.last_filter_ms = (time.perf_counter() - began) * 1000.0
        return matches

    def _filter(self, needle: str) -> int:
        shown: set[int] = set()
        matches = 0
        # plain text matches a hierarchy's own name (its sub-hierarchies would
        # otherwise all match through the path); a ">" asks for the path
        role = _PATH if ">" in needle else _NAME
        needle = " > ".join(p.strip() for p in needle.split(">"))
        for item in self._all:
            if needle in item.data(0, role):
                matches += 1
                node: QTreeWidgetItem | None = item
                while node is not None and id(node) not in shown:
                    shown.add(id(node))
                    node = node.parent()
        for item in self._all:
            visible = id(item) in shown
            item.setHidden(not visible)
            if visible:
                item.setExpanded(True)
        if self._system_item is not None:
            self._system_item.setHidden(needle not in "system")
        first = next((i for i in self._all if not i.isHidden()
                      and needle in i.data(0, role)), None)
        if first is not None:
            self.tree.setCurrentItem(first)
            self.tree.scrollToItem(first)
        return matches

    def visible_names(self) -> list[str]:
        return [i.text(0) for i in self._all if not i.isHidden()]

    def _update_count(self, shown: int, total: int) -> None:
        if shown == 0:
            self.count_label.setText(i18n.tr("No match"))
        else:
            self.count_label.setText(
                i18n.tr("%1 of %2").replace("%1", str(shown)).replace("%2", str(total)))

    # -- choosing --------------------------------------------------------

    def checked_refs(self) -> list[int]:
        """Ticked ids, keeping the order they were ticked in."""
        ticked = {ref for ref, item in self._items.items()
                  if item.checkState(0) == Qt.CheckState.Checked}
        ordered = [r for r in self._current if r in ticked]
        return ordered + [r for r in ticked if r not in ordered]

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._building or self.mode != COMPARE or column != 0:
            return
        ref = int(item.data(0, _REF) or 0)
        ticked = item.checkState(0) == Qt.CheckState.Checked
        chosen = [r for r in self._current if r != ref]
        if ticked:
            if len(chosen) >= COMPARE_LIMIT:
                self._building = True
                item.setCheckState(0, Qt.CheckState.Unchecked)
                self._building = False
                self._update_note(refused=True)
                return
            chosen.append(ref)
        self._current = chosen
        self._update_note()
        self.compare_chosen.emit(list(chosen))

    def _update_note(self, *, refused: bool = False) -> None:
        full = len(self._current) >= COMPARE_LIMIT
        if refused or full:
            self.note_label.setText(i18n.tr(
                "Four are ticked, the most the comparison charts draw. "
                "Untick one to pick another."))
            self.note_label.show()
        else:
            self.note_label.hide()

    def _on_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if self.mode == SCOPE:
            self._choose_scope(item)

    def _on_activated(self, item: QTreeWidgetItem, column: int) -> None:
        if self.mode == SCOPE:
            self._choose_scope(item)
        elif item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            item.setCheckState(0, Qt.CheckState.Unchecked
                               if item.checkState(0) == Qt.CheckState.Checked
                               else Qt.CheckState.Checked)

    def _choose_scope(self, item: QTreeWidgetItem) -> None:
        self.hide()
        self.scope_chosen.emit(int(item.data(0, _REF) or 0))

    def _top_four(self) -> None:
        self.set_current([])
        self.hide()
        self.compare_chosen.emit([])

    def _clear(self) -> None:
        self._top_four()

    # -- keyboard --------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown,
                       Qt.Key.Key_PageUp):
                self._move(Qt.Key(key))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.tree.currentItem()
                if item is not None:
                    self._on_activated(item, 0)
                return True
        return super().eventFilter(obj, event)

    def _move(self, key: Qt.Key) -> None:
        visible = [i for i in self._visible_items()]
        if not visible:
            return
        current = self.tree.currentItem()
        index = visible.index(current) if current in visible else -1
        step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1,
                Qt.Key.Key_PageDown: 8, Qt.Key.Key_PageUp: -8}[key]
        index = min(len(visible) - 1, max(0, index + step))
        self.tree.setCurrentItem(visible[index])

    def _visible_items(self) -> list[QTreeWidgetItem]:
        out: list[QTreeWidgetItem] = []

        def walk(node: QTreeWidgetItem) -> None:
            for i in range(node.childCount()):
                child = node.child(i)
                if child.isHidden():
                    continue
                out.append(child)
                if child.isExpanded():
                    walk(child)
        walk(self.tree.invisibleRootItem())
        return out

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    # -- showing ---------------------------------------------------------

    def popup_below(self, button: QWidget) -> None:
        i18n.translate_widgets(self)
        pos = button.mapToGlobal(button.rect().bottomLeft())
        self.move(pos)
        self.search.clear()
        self.apply_filter("")
        self.show()
        self.search.setFocus()

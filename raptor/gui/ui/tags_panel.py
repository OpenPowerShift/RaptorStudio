"""Component tags: the panel behind Data > Tags (a dock beside the canvas).

Tags are navigation metadata, not simulation structure. The panel lists the
model's tags, edits one tag's name, colour and description, puts a tag on or
takes it off the elements selected on the canvas, and shows the tag's
elements in a sortable, filterable table (double-click one to find it).

The panel is not modal and follows the canvas selection, so you can select
elements and press **Add to selection** without moving anything out of the
way. There is no OK: every action is one undoable edit, sent out as
`edit_requested((tags, tag_info), text)`. The main window pushes it as a
`TagsCommand` on the view's undo stack, which changes the model and so
refreshes the panel.

Large models can carry hundreds of tags and a tag can sit on thousands of
elements, so the tag list is filtered by its search box and the table draws
at most `_MEMBER_ROWS` rows (the filter narrows what those rows are drawn
from).
"""
from __future__ import annotations

import copy
import re
from types import SimpleNamespace
from typing import Any
from collections.abc import Iterable

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut, QUndoCommand,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from raptor.core import tags as T
from raptor.gui import i18n

_COLOUR_NAMES = {
    "#2F80ED": "Blue", "#27AE60": "Green", "#9B51E0": "Purple",
    "#F2994A": "Orange", "#EB5757": "Red", "#00A3A3": "Teal",
    "#B45309": "Amber", "#6B7280": "Grey",
}

#: Rows the members table draws. A tag may sit on thousands of elements;
#: a table of every one is slow to build and no one reads past the first
#: screens. The table's filter searches all of them.
_MEMBER_ROWS = 1000

#: Member table columns.
_COLUMNS = ("Name", "Kind", "ID", "Level")

_ROLE = Qt.ItemDataRole.UserRole


def swatch(colours: Iterable[str], size: int = 14) -> QIcon:
    """A small square of one colour, or up to three vertical stripes."""
    shades = [c for c in colours if c][:3] or ["#00000000"]
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    width = size / len(shades)
    for i, colour in enumerate(shades):
        p.fillRect(round(i * width), 0,
                   round((i + 1) * width) - round(i * width),
                   size, QColor(colour))
    p.end()
    return QIcon(pix)


def _natural(text: str) -> list[Any]:
    """Sort key: "pump 2" before "pump 10", case ignored."""
    return [int(part) if part.isdigit() else part
            for part in re.split(r"(\d+)", text.casefold())]


def _count(n: int, one: str, many: str) -> str:
    """*one* or *many* (already translated), with %1 the number."""
    return (one if n == 1 else many).replace("%1", str(n))


def _kind_word(kind: str) -> str:
    if kind == "block":
        return i18n.tr("Block")
    if kind == "node":
        return i18n.tr("Node")
    if kind == "event":
        return i18n.tr("Event")
    return i18n.tr("Hierarchy")


class TagsPanel(QWidget):
    """Edit the model's tags and the tags on the canvas selection."""

    #: `((tags, tag_info), undo text)`: one whole new state, one undo step.
    edit_requested = Signal(object, str)
    #: `(kind, id)` of a table row that was double-clicked.
    locate_requested = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TagsPanel")
        self._rbd: Any = None
        self._selected: list[str] = []
        self._current = ""
        self._loading = False
        self._focus_name = False
        self._rows: list[tuple[str, str, int, str, str]] = []
        self._sort = (0, Qt.SortOrder.AscendingOrder)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.refresh)
        self._build()
        self.refresh()

    # -- construction ---------------------------------------------------
    def _build(self) -> None:  # noqa: PLR0915
        # Two panes the user can resize: the tags and their settings above,
        # the chosen tag's elements below. In a short dock the list used to
        # get a third of the members table's share and showed ~3 rows.
        page = QVBoxLayout(self)
        page.setContentsMargins(8, 8, 8, 8)
        self._split = QSplitter(Qt.Orientation.Vertical)
        self._split.setChildrenCollapsible(False)
        page.addWidget(self._split)
        top = QWidget()
        outer = QVBoxLayout(top)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self._split.addWidget(top)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search tags")
        self._search.setAccessibleName("Search tags")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self.refresh())
        outer.addWidget(self._search)
        self._list = QListWidget()
        self._list.setObjectName("TagsList")
        self._list.setAccessibleName("Tags")
        rows = self._list.fontMetrics().height() + 6
        self._list.setMinimumHeight(5 * rows + 4)   # five tags at least
        self._list.setToolTip(
            "The model's tags and how many elements carry each. Selecting "
            "one edits its name, colour and description.")
        self._list.currentItemChanged.connect(self._on_item_changed)
        outer.addWidget(self._list, 1)
        row = QHBoxLayout()
        self._new_button = QPushButton("New")
        self._new_button.setToolTip(
            "Create a tag; it is put on the elements selected on the "
            "canvas, if any")
        self._new_button.clicked.connect(self._new_tag)
        self._delete_button = QPushButton("Delete")
        self._delete_button.setToolTip(
            "Remove the selected tag from every element; the elements are "
            "kept")
        self._delete_button.clicked.connect(self._delete_tag)
        row.addWidget(self._new_button)
        row.addWidget(self._delete_button)
        outer.addLayout(row)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self._name = QLineEdit()
        self._name.setMaxLength(T.TAG_MAX)
        self._name.setAccessibleName("Tag name")
        self._name.setToolTip(
            "Renames the tag on every element. Renaming onto a tag that "
            "already exists merges the two.")
        self._name.editingFinished.connect(self._rename_current)
        form.addRow("Name", self._name)
        self._colour = QComboBox()
        self._colour.setAccessibleName("Tag colour")
        self._colour.currentIndexChanged.connect(self._colour_changed)
        form.addRow("Colour", self._colour)
        self._description = QLineEdit()
        self._description.setMaxLength(T.DESCRIPTION_MAX)
        self._description.setAccessibleName("Tag description")
        self._description.setPlaceholderText(
            "Optional note, shown in the tag's tooltips")
        self._description.editingFinished.connect(self._description_changed)
        form.addRow("Description", self._description)
        outer.addLayout(form)

        self._selection_label = QLabel()
        self._selection_label.setWordWrap(True)
        outer.addWidget(self._selection_label)
        self._add_sel = QPushButton("Add to selection")
        self._add_sel.setToolTip(
            "Put this tag on the elements selected on the canvas")
        self._add_sel.clicked.connect(self._add_selection)
        self._remove_sel = QPushButton("Remove from selection")
        self._remove_sel.setToolTip(
            "Take this tag off the elements selected on the canvas")
        self._remove_sel.clicked.connect(self._remove_selection)
        buttons = QHBoxLayout()
        buttons.addWidget(self._add_sel)
        buttons.addWidget(self._remove_sel)
        outer.addLayout(buttons)

        bottom = QWidget()
        outer = QVBoxLayout(bottom)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self._split.addWidget(bottom)
        self._split.setStretchFactor(0, 1)
        self._split.setStretchFactor(1, 1)
        self._members_label = QLabel("Elements")
        outer.addWidget(self._members_label)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter elements")
        self._filter.setAccessibleName("Filter the tag's elements")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(lambda _t: self._fill_table())
        outer.addWidget(self._filter)
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("TagMembers")
        self._table.setHorizontalHeaderLabels(
            ["Name", "Kind", "ID", "Level"])
        self._table.setAccessibleName("Elements with this tag")
        self._table.setToolTip(
            "Click a heading to sort. Double-click an element to find it "
            "on the canvas.")
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(90)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        header.sectionClicked.connect(self._sort_by)
        self._table.setColumnWidth(0, 130)
        self._table.setColumnWidth(1, 70)
        self._table.setColumnWidth(2, 40)
        self._table.cellDoubleClicked.connect(self._locate_row)
        enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self._table)
        enter.setContext(Qt.ShortcutContext.WidgetShortcut)
        enter.activated.connect(
            lambda: self._locate_row(self._table.currentRow(), 0))
        outer.addWidget(self._table, 3)
        self._more_label = QLabel()
        self._more_label.setWordWrap(True)
        outer.addWidget(self._more_label)

    # -- state in ---------------------------------------------------------
    def set_model(self, rbd: Any) -> None:
        """The model whose tags are shown (None: none open)."""
        self._rbd = rbd

    def set_selection(self, refs: Iterable[str]) -> None:
        """The canvas selection, as tag references; the buttons follow it."""
        refs = list(refs)
        if refs != self._selected:
            self._selected = refs
            self._update_selection_controls()

    def schedule_refresh(self) -> None:
        """Refresh once the current event is done (coalesces a burst)."""
        if not self._timer.isActive():
            self._timer.start()

    def focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    def current_tag(self) -> str:
        return self._current

    # -- helpers --------------------------------------------------------
    def _draft(self) -> Any:
        return SimpleNamespace(
            tags=T.clean_tags(getattr(self._rbd, "tags", None)),
            tag_info=T.clean_tag_info(getattr(self._rbd, "tag_info", None)),
            sections=getattr(self._rbd, "sections", None))

    def _commit(self, draft: Any, text: str, keep: str | None = None) -> None:
        """Send one edit out; the model change refreshes the panel."""
        after = (T.clean_tags(copy.deepcopy(draft.tags)),
                 T.clean_tag_info(copy.deepcopy(draft.tag_info)))
        if keep is not None:
            self._current = keep
        self.edit_requested.emit(after, text)
        self.schedule_refresh()

    def _valid_selected(self) -> list[str]:
        known = T.valid_refs_from_model(self._rbd) if self._rbd else set()
        return [ref for ref in self._selected if ref in known]

    def _fill_colours(self, tag: str) -> None:
        self._colour.clear()
        default = T.default_colour(tag)
        self._colour.addItem(
            swatch([default]),
            i18n.tr("Automatic (%1)").replace(
                "%1", i18n.tr(_COLOUR_NAMES.get(default, default))), "")
        for hex_code in T.TAG_COLOURS:
            self._colour.addItem(
                swatch([hex_code]),
                i18n.tr(_COLOUR_NAMES.get(hex_code, hex_code)), hex_code)
        info = T.clean_tag_info(getattr(self._rbd, "tag_info", None))
        chosen = (info.get(tag) or {}).get("colour", "")
        if chosen and self._colour.findData(chosen) < 0:
            self._colour.addItem(
                swatch([chosen]),
                i18n.tr("Custom (%1)").replace("%1", chosen), chosen)
        self._colour.setCurrentIndex(max(0, self._colour.findData(chosen)))

    # -- list and form ----------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the list, the form and the table from the model."""
        self._timer.stop()
        rbd = self._rbd
        have = rbd is not None
        for widget in (self._search, self._list, self._new_button,
                       self._filter, self._table):
            widget.setEnabled(have)
        wanted = self._search.text().strip().casefold()
        tags = T.all_tags(rbd) if have else []
        index = T.tag_index(rbd) if have else {}
        self._loading = True
        self._list.clear()
        target = None
        for tag in tags:
            if wanted and wanted not in tag.casefold():
                continue
            item = QListWidgetItem(
                swatch([T.tag_colour(rbd, tag)]),
                f"{tag}  ({len(index.get(tag, ()))})")
            item.setData(_ROLE, tag)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, tag)
            description = T.tag_description(rbd, tag)
            if description:
                item.setToolTip(description)
            self._list.addItem(item)
            if tag == self._current:
                target = item
        if target is None and self._list.count() and not wanted:
            target = self._list.item(0)
        self._loading = False
        if target is not None:
            self._loading = True
            self._list.setCurrentItem(target)
            self._loading = False
            self._load_tag(target.data(_ROLE))
        else:
            self._load_tag(self._current if self._current in tags else "")

    def _on_item_changed(self, item: QListWidgetItem | None, _prev) -> None:
        if self._loading:
            return
        self._load_tag(item.data(_ROLE) if item else "")

    def _load_tag(self, tag: str) -> None:
        self._current = tag
        self._loading = True
        for widget in (self._name, self._description, self._colour,
                       self._delete_button):
            widget.setEnabled(bool(tag))
        if not self._name.hasFocus() or self._focus_name:
            self._name.setText(tag)
        if not self._description.hasFocus():
            self._description.setText(
                T.tag_description(self._rbd, tag) if tag else "")
        self._fill_colours(tag)
        self._loading = False
        self._show_members(tag)
        self._update_selection_controls()
        if self._focus_name and tag:
            self._focus_name = False
            self._name.setFocus()
            self._name.selectAll()

    def _update_selection_controls(self) -> None:
        selected = self._valid_selected()
        count = len(selected)
        has_tag = bool(self._current)
        carrying = 0
        if has_tag and count:
            carrying = sum(1 for ref in selected
                           if self._current in T.tags_for(self._rbd, ref))
        lacking = count - carrying
        self._add_sel.setEnabled(has_tag and lacking > 0)
        self._remove_sel.setEnabled(has_tag and carrying > 0)
        suffix = f" ({lacking})" if has_tag and count else ""
        self._add_sel.setText(i18n.tr("Add to selection") + suffix)
        suffix = f" ({carrying})" if has_tag and count else ""
        self._remove_sel.setText(i18n.tr("Remove from selection") + suffix)
        if count:
            self._selection_label.setText(_count(
                count, i18n.tr("%1 element selected on the canvas"),
                i18n.tr("%1 elements selected on the canvas")))
        else:
            self._selection_label.setText(i18n.tr(
                "Select elements on the canvas to tag them here."))

    # -- the members table -------------------------------------------------
    def _show_members(self, tag: str) -> None:
        self._rows = []
        if tag and self._rbd is not None:
            refs = T.tag_index(self._rbd).get(tag, set())
            self._rows = T.member_rows(self._rbd, refs)
        self._members_label.setText(
            i18n.tr("Elements (%1)").replace("%1", str(len(self._rows)))
            if tag else i18n.tr("Elements"))
        self._fill_table()

    def _row_key(self, column: int):
        if column == 0:
            return lambda r: (_natural(r[3]), r[1], r[2])
        if column == 1:
            return lambda r: (_kind_word(r[1]), _natural(r[3]))
        if column == 2:
            return lambda r: (r[2], r[1])
        return lambda r: (_natural(r[4]), _natural(r[3]))

    def _sort_by(self, column: int) -> None:
        old, order = self._sort
        if column == old:
            order = (Qt.SortOrder.DescendingOrder
                     if order == Qt.SortOrder.AscendingOrder
                     else Qt.SortOrder.AscendingOrder)
        else:
            order = Qt.SortOrder.AscendingOrder
        self._sort = (column, order)
        self._table.horizontalHeader().setSortIndicator(column, order)
        self._fill_table()

    def _fill_table(self) -> None:
        table = self._table
        wanted = self._filter.text().strip().casefold()
        rows = self._rows
        if wanted:
            rows = [r for r in rows
                    if wanted in " ".join((r[3], _kind_word(r[1]), str(r[2]),
                                           r[4])).casefold()]
        column, order = self._sort
        rows = sorted(rows, key=self._row_key(column),
                      reverse=order == Qt.SortOrder.DescendingOrder)
        shown = rows[:_MEMBER_ROWS]
        table.setUpdatesEnabled(False)
        table.clearContents()
        table.setRowCount(len(shown))
        for i, (ref, kind, oid, name, level) in enumerate(shown):
            cells = (name, _kind_word(kind), str(oid), level)
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(_ROLE, ref)
                item.setToolTip(ref if c != 3 or not level else level)
                if c == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(i, c, item)
        table.setUpdatesEnabled(True)
        if not self._current:
            note = ""
        elif not self._rows:
            note = i18n.tr("No elements carry this tag yet. Select elements "
                           "on the canvas and press Add to selection.")
        elif not rows:
            note = i18n.tr("No element matches the filter.")
        elif len(rows) > len(shown):
            note = (i18n.tr("Showing the first %1 of %2. Filter to narrow "
                            "the list.")
                    .replace("%1", str(len(shown)))
                    .replace("%2", str(len(rows))))
        else:
            note = ""
        self._more_label.setText(note)
        self._more_label.setVisible(bool(note))

    def _locate_row(self, row: int, _column: int = 0) -> None:
        item = self._table.item(row, 0) if row >= 0 else None
        parsed = T.parse_ref(item.data(_ROLE)) if item is not None else None
        if parsed is not None:
            self.locate_requested.emit(parsed[0], parsed[1])

    # -- edits ------------------------------------------------------------
    def _rename_current(self) -> None:
        if self._loading or not self._current or self._rbd is None:
            return
        new = T.clean_tag(self._name.text())
        if not new or new == self._current:
            self._name.setText(self._current)
            return
        draft = self._draft()
        T.rename_tag(draft, self._current, new)
        self._commit(draft, i18n.tr("Rename tag"), keep=new)

    def _colour_changed(self, _index: int) -> None:
        if self._loading or not self._current or self._rbd is None:
            return
        draft = self._draft()
        T.set_tag_info(draft, self._current,
                       colour=self._colour.currentData() or "")
        self._commit(draft, i18n.tr("Tag colour"))

    def _description_changed(self) -> None:
        if self._loading or not self._current or self._rbd is None:
            return
        if self._description.text() == T.tag_description(self._rbd,
                                                         self._current):
            return
        draft = self._draft()
        T.set_tag_info(draft, self._current,
                       description=self._description.text())
        self._commit(draft, i18n.tr("Tag description"))

    def _new_tag(self) -> None:
        if self._rbd is None:
            return
        draft = self._draft()
        known = T.all_tags(draft)
        name, n = "tag", 2
        while name in known:
            name = f"tag {n}"
            n += 1
        T.set_tag_info(draft, name)
        for ref in self._valid_selected():
            T.add_tag(draft, ref, name)
        T.colour_new_tags(draft, known)     # a colour no other tag shows
        self._search.clear()
        self._focus_name = True
        self._commit(draft, i18n.tr("New tag"), keep=name)

    def _delete_tag(self) -> None:
        tag = self._current
        if not tag or self._rbd is None:
            return
        count = len(T.tag_index(self._rbd).get(tag, ()))
        from raptor.gui.ui.dialogs import question_box
        if not question_box(
                self, "Delete Tag",
                i18n.tr("Remove the tag “%1” from %2?").replace("%1", tag)
                .replace("%2", _count(count, i18n.tr("%1 element"),
                                      i18n.tr("%1 elements")))
                + "\n\n" + i18n.tr("The elements themselves are not "
                                   "changed."),
                yes_text="Delete tag", no_text="Keep tag"):
            return
        draft = self._draft()
        T.delete_tag(draft, tag)
        self._commit(draft, i18n.tr("Delete tag"), keep="")

    def _add_selection(self) -> None:
        if not self._current or self._rbd is None:
            return
        draft = self._draft()
        for ref in self._valid_selected():
            T.add_tag(draft, ref, self._current)
        self._commit(draft, i18n.tr("Add tag to selection"))

    def _remove_selection(self) -> None:
        if not self._current or self._rbd is None:
            return
        draft = self._draft()
        for ref in self._valid_selected():
            T.remove_tag(draft, ref, self._current)
        self._commit(draft, i18n.tr("Remove tag from selection"))


class TagsCommand(QUndoCommand):
    """One undoable step for a tags edit: `(tags, tag_info)` before/after."""

    def __init__(self, view: Any, before: tuple[Any, Any],
                 after: tuple[Any, Any], text: str = "Component tags"):
        super().__init__(text)
        self._view = view
        self._before = copy.deepcopy(before)
        self._after = copy.deepcopy(after)

    def _apply(self, data: tuple[Any, Any]) -> None:
        rbd = getattr(self._view, "_rbd_ref", None)
        if rbd is None:
            return
        rbd.tags, rbd.tag_info = copy.deepcopy(data[0]), copy.deepcopy(data[1])
        emitter = getattr(self._view, "model_changed", None)
        if emitter is not None:
            emitter.emit()

    def redo(self) -> None:
        self._apply(self._after)

    def undo(self) -> None:
        self._apply(self._before)

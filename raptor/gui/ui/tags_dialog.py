"""Component tags: the editor behind Data ▸ Tags.

Tags are navigation metadata, not simulation structure. This dialog edits
a working copy of the tags and the per-tag colour and description; the
caller pushes one undo command, so creating a tag and applying it to a
selection is a single step in History.

Large models can carry hundreds of tags, so the list is filtered by the
search box and the member preview stops after `_MEMBER_ROWS` rows.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any
from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap, QUndoCommand
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from raptor.core import tags as T

_COLOUR_NAMES = {
    "#2F80ED": "Blue", "#27AE60": "Green", "#9B51E0": "Purple",
    "#F2994A": "Orange", "#EB5757": "Red", "#00A3A3": "Teal",
    "#B45309": "Amber", "#6B7280": "Grey",
}

#: Element kind -> the section and column that hold its display name.
_NAME_COLUMNS = (("block", "BLOCK_DATA", "blockName"),
                 ("node", "NODE_DATA", "Name"),
                 ("event", "EVENT_DATA", "eventName"),
                 ("hier", "HIERARCHY_DATA", "hierarchyName"))

#: Rows the member preview draws before summarising. A tag may sit on
#: thousands of elements; laying every one out would freeze the dialog.
_MEMBER_ROWS = 200


def swatch(colours: Iterable[str], size: int = 14) -> QIcon:
    """A small square of one colour, or up to three vertical stripes."""
    shades = [c for c in colours if c][:3] or ["#00000000"]
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QPainter
    p = QPainter(pix)
    width = size / len(shades)
    for i, colour in enumerate(shades):
        p.fillRect(round(i * width), 0, round((i + 1) * width) - round(i * width),
                   size, QColor(colour))
    p.end()
    return QIcon(pix)


class TagsDialog(QDialog):
    """Edit the model's tags and the tags on the current selection."""

    def __init__(self, parent: QWidget | None, rbd: Any,
                 selected_refs: Iterable[str] = ()):
        super().__init__(parent)
        self.setWindowTitle("Component Tags")
        self.setMinimumSize(760, 500)
        self._rbd = rbd
        self._draft = SimpleNamespace(
            tags=T.clean_tags(getattr(rbd, "tags", None)),
            tag_info=T.clean_tag_info(getattr(rbd, "tag_info", None)),
            sections=getattr(rbd, "sections", None))
        self._selected = list(selected_refs)
        self._name_map: dict[str, str] | None = None
        self._current = ""
        self._tags_dirty = False
        self._loading = False
        self._build()
        self._refresh_list()
        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._load_tag("")

    # -- construction ---------------------------------------------------
    def _build(self) -> None:  # noqa: PLR0915
        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        intro = QLabel(
            "Tags are labels on blocks, nodes, events and hierarchies. They "
            "may span hierarchies and overlap, and never change the "
            "simulation. Filter the Model Explorer or a chart by a tag.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(10)
        left = QVBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search tags")
        self._search.setAccessibleName("Search tags")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self._refresh_list())
        left.addWidget(self._search)
        self._list = QListWidget()
        self._list.setAccessibleName("Tags")
        self._list.setToolTip(
            "The model's tags and how many elements carry each. Selecting "
            "one edits its name, colour and description.")
        self._list.currentItemChanged.connect(self._on_item_changed)
        left.addWidget(self._list, 1)
        row = QHBoxLayout()
        new_button = QPushButton("New")
        new_button.setToolTip(
            "Create a tag; it is applied to the elements selected on the "
            "canvas, if any")
        new_button.clicked.connect(self._new_tag)
        self._delete_button = QPushButton("Delete")
        self._delete_button.setToolTip(
            "Remove the selected tag from every element; the elements are "
            "kept")
        self._delete_button.clicked.connect(self._delete_tag)
        row.addWidget(new_button)
        row.addWidget(self._delete_button)
        left.addLayout(row)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
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
        right.addLayout(form)

        self._members_label = QLabel("Elements")
        right.addWidget(self._members_label)
        self._members = QPlainTextEdit()
        self._members.setReadOnly(True)
        self._members.setAccessibleName("Elements with this tag")
        self._members.setMinimumHeight(150)
        right.addWidget(self._members, 1)
        buttons = QHBoxLayout()
        self._add_sel = QPushButton("Add to selection")
        self._add_sel.setToolTip(
            "Put this tag on the elements selected on the canvas")
        self._add_sel.clicked.connect(self._add_selection)
        self._remove_sel = QPushButton("Remove from selection")
        self._remove_sel.setToolTip(
            "Take this tag off the elements selected on the canvas")
        self._remove_sel.clicked.connect(self._remove_selection)
        buttons.addWidget(self._add_sel)
        buttons.addWidget(self._remove_sel)
        right.addLayout(buttons)

        self._tags_label = QLabel()
        self._tags_label.setWordWrap(True)
        right.addWidget(self._tags_label)
        tag_row = QHBoxLayout()
        self._tags_edit = QLineEdit()
        self._tags_edit.setAccessibleName("Tags for the selected elements")
        self._tags_edit.setPlaceholderText("Comma-separated tags")
        self._tags_edit.setToolTip(
            "Tags for the selected elements, separated by commas (up to "
            f"{T.TAG_MAX} characters each). Applying replaces every tag on "
            "them; an empty box removes their tags.")
        self._tags_edit.returnPressed.connect(self._apply_tags)
        self._tags_edit.textEdited.connect(
            lambda _text: setattr(self, "_tags_dirty", True))
        self._tags_button = QPushButton("Apply to selection")
        self._tags_button.setToolTip(
            "Replace the tags on every selected element with this list; "
            "leave the box empty to remove their tags")
        self._tags_button.clicked.connect(self._apply_tags)
        tag_row.addWidget(self._tags_edit, 1)
        tag_row.addWidget(self._tags_button)
        right.addLayout(tag_row)
        body.addLayout(right, 2)
        outer.addLayout(body, 1)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        outer.addWidget(box)

    # -- helpers --------------------------------------------------------
    def _names(self) -> dict[str, str]:
        """`ref -> display name`, read from the model rows once."""
        if self._name_map is not None:
            return self._name_map
        from raptor.core.fileio.typed import row_int
        names: dict[str, str] = {}
        sections = getattr(self._rbd, "sections", None) or {}
        for kind, section, column in _NAME_COLUMNS:
            sec = sections.get(section)
            for row in (sec.rows if sec is not None else ()):
                oid = row_int(row.get("ID"), 0)
                if oid > 0:
                    names[f"{kind}:{oid}"] = str(row.get(column) or "")
        self._name_map = names
        return names

    @staticmethod
    def _elements(count: int) -> str:
        return f"{count} element" + ("" if count == 1 else "s")

    def _tagged(self, tag: str) -> set[str]:
        names = self._names()
        return {ref for ref in T.tag_index(self._draft).get(tag, set())
                if ref in names}

    def _fill_colours(self, tag: str) -> None:
        self._colour.clear()
        default = T.default_colour(tag)
        self._colour.addItem(
            swatch([default]),
            f"Automatic ({_COLOUR_NAMES.get(default, default)})", "")
        for hex_code in T.TAG_COLOURS:
            self._colour.addItem(swatch([hex_code]),
                                 _COLOUR_NAMES.get(hex_code, hex_code),
                                 hex_code)
        chosen = (self._draft.tag_info.get(tag) or {}).get("colour", "")
        if chosen and self._colour.findData(chosen) < 0:
            self._colour.addItem(swatch([chosen]), f"Custom ({chosen})",
                                 chosen)
        self._colour.setCurrentIndex(max(0, self._colour.findData(chosen)))

    def _update_selection_controls(self) -> None:
        count = len(self._selected)
        has_tag = bool(self._current)
        self._add_sel.setEnabled(count > 0 and has_tag)
        self._remove_sel.setEnabled(count > 0 and has_tag)
        self._tags_button.setEnabled(count > 0)
        self._tags_edit.setEnabled(count > 0)
        if count:
            self._tags_label.setText(
                f"Selected: {self._elements(count)}. Applying tags replaces "
                "every tag on them.")
            common = T.tags_for(self._draft, self._selected[0])
            for ref in self._selected[1:]:
                mine = T.tags_for(self._draft, ref)
                common = [t for t in common if t in mine]
            if not self._tags_dirty:
                self._tags_edit.setText(", ".join(common))
        else:
            self._tags_label.setText(
                "Select elements on the canvas to tag them here.")
            self._tags_edit.clear()

    # -- list/form ------------------------------------------------------
    def _refresh_list(self, keep: str | None = None) -> None:
        keep = self._current if keep is None else keep
        wanted = self._search.text().strip().casefold()
        index = T.tag_index(self._draft)
        self._loading = True
        self._list.clear()
        target = None
        for tag in T.all_tags(self._draft):
            if wanted and wanted not in tag.casefold():
                continue
            item = QListWidgetItem(
                swatch([T.tag_colour(self._draft, tag)]),
                f"{tag}  ({len(index.get(tag, ()))})")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            description = T.tag_description(self._draft, tag)
            if description:
                item.setToolTip(description)
            self._list.addItem(item)
            if tag == keep:
                target = item
        self._loading = False
        if target is not None:
            self._list.setCurrentItem(target)
        elif self._list.count() and not wanted:
            self._list.setCurrentRow(0)
        else:
            self._load_tag(keep if keep in T.all_tags(self._draft) else "")

    def _on_item_changed(self, item: QListWidgetItem | None, _prev) -> None:
        if self._loading:
            return
        self._load_tag(item.data(Qt.ItemDataRole.UserRole) if item else "")

    def _load_tag(self, tag: str) -> None:
        self._current = tag
        self._loading = True
        for widget in (self._name, self._description, self._colour,
                       self._delete_button):
            widget.setEnabled(bool(tag))
        self._name.setText(tag)
        self._description.setText(T.tag_description(self._draft, tag)
                                  if tag else "")
        self._fill_colours(tag)
        self._loading = False
        self._show_members(tag)
        self._update_selection_controls()

    def _show_members(self, tag: str) -> None:
        if not tag:
            self._members.clear()
            self._members_label.setText("Elements")
            return
        tagged = sorted(self._tagged(tag))
        names = self._names()
        rows = [f"{ref}  {names.get(ref, '')}".rstrip()
                for ref in tagged[:_MEMBER_ROWS]]
        if len(tagged) > _MEMBER_ROWS:
            rows.append(f"… and {len(tagged) - _MEMBER_ROWS} more")
        if not rows:
            rows.append("No elements carry this tag yet — select elements "
                        "on the canvas and press Add to selection.")
        self._members.setPlainText("\n".join(rows))
        self._members_label.setText(f"Elements ({len(tagged)})")

    def _rename_current(self) -> None:
        if self._loading or not self._current:
            return
        new = T.clean_tag(self._name.text())
        if not new or new == self._current:
            self._name.setText(self._current)
            return
        T.rename_tag(self._draft, self._current, new)
        self._refresh_list(keep=new)

    def _colour_changed(self, _index: int) -> None:
        if self._loading or not self._current:
            return
        T.set_tag_info(self._draft, self._current,
                       colour=self._colour.currentData() or "")
        self._refresh_list()

    def _description_changed(self) -> None:
        if self._loading or not self._current:
            return
        T.set_tag_info(self._draft, self._current,
                       description=self._description.text())
        item = self._list.currentItem()
        if item is not None:
            item.setToolTip(T.tag_description(self._draft, self._current))

    # -- buttons --------------------------------------------------------
    def _new_tag(self) -> None:
        self._rename_current()
        existing = set(T.all_tags(self._draft))
        name, n = "tag", 2
        while name in existing:
            name = f"tag {n}"
            n += 1
        T.set_tag_info(self._draft, name)
        for ref in self._selected:
            T.add_tag(self._draft, ref, name)
        self._search.clear()
        self._refresh_list(keep=name)
        self._name.setFocus()
        self._name.selectAll()

    def _delete_tag(self) -> None:
        tag = self._current
        if not tag:
            return
        count = len(self._tagged(tag))
        from raptor.gui.ui.dialogs import question_box
        if not question_box(
                self, "Delete Tag",
                f"Remove the tag “{tag}” from {self._elements(count)}?\n\n"
                "The elements themselves are not changed.",
                yes_text="Delete tag", no_text="Keep tag"):
            return
        T.delete_tag(self._draft, tag)
        self._refresh_list(keep="")

    def _add_selection(self) -> None:
        for ref in self._selected:
            T.add_tag(self._draft, ref, self._current)
        self._tags_dirty = False
        self._refresh_list()

    def _remove_selection(self) -> None:
        for ref in self._selected:
            T.remove_tag(self._draft, ref, self._current)
        self._tags_dirty = False
        self._refresh_list()

    def _apply_tags(self) -> None:
        if not self._selected:
            return
        values = [part for part in self._tags_edit.text().split(",")]
        for ref in self._selected:
            T.set_tags(self._draft, ref, values)
        self._tags_dirty = False
        self._refresh_list()

    # -- result ---------------------------------------------------------
    def accept(self) -> None:
        self._rename_current()
        self._description_changed()
        if self._selected and self._tags_dirty:
            self._apply_tags()
        super().accept()

    def result_data(self) -> tuple[dict[str, list[str]], dict[str, Any]]:
        """The edited tags and tag info, with references the model no longer
        has dropped (the same prune a file load applies)."""
        draft = SimpleNamespace(tags=copy.deepcopy(self._draft.tags),
                                tag_info=copy.deepcopy(self._draft.tag_info))
        T.prune(draft, T.valid_refs_from_model(self._rbd))
        return (T.clean_tags(draft.tags), T.clean_tag_info(draft.tag_info))


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

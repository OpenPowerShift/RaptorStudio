"""Component groups and tags: the editor behind Data ▸ Groups / Tags.

Groups are navigation metadata, not simulation structure. This dialog
edits a working copy; the caller pushes one undo command, so creating a
group and tagging a selection is a single step in History.
"""
from __future__ import annotations

import copy
from typing import Any
from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from raptor.core import groups as G

_COLOUR_NAMES = {
    "#2F80ED": "Blue", "#27AE60": "Green", "#9B51E0": "Purple",
    "#F2994A": "Orange", "#EB5757": "Red", "#00A3A3": "Teal",
    "#B45309": "Amber", "#6B7280": "Grey",
}


class GroupsDialog(QDialog):
    """Edit groups and the tags on the current selection."""

    def __init__(self, parent: QWidget | None, rbd: Any,
                 selected_refs: Iterable[str] = ()):
        super().__init__(parent)
        self.setWindowTitle("Component Groups and Tags")
        self.setMinimumSize(760, 500)
        self._rbd = rbd
        self._tags: dict[str, list[str]] = copy.deepcopy(
            getattr(rbd, "tags", None) or {})
        self._groups: list[dict[str, Any]] = G.groups_of(rbd)
        self._selected = list(selected_refs)
        self._current_row = -1
        self._loading = False
        self._build()
        self._refresh_list()
        if self._groups:
            self._list.setCurrentRow(0)
        else:
            self._load_row(-1)

    # -- construction ---------------------------------------------------
    def _build(self) -> None:  # noqa: PLR0915
        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        intro = QLabel(
            "Groups are saved views over the model: they may span "
            "hierarchies and do not change the simulation. A group can "
            "hold an explicit list, every element carrying a tag, or both.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(10)
        left = QVBoxLayout()
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_row_changed)
        left.addWidget(self._list, 1)
        row = QHBoxLayout()
        for text, slot in (("New", self._new_group),
                           ("Delete", self._delete_group)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        left.addLayout(row)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self._name = QLineEdit()
        self._name.setMaxLength(64)
        self._name.editingFinished.connect(self._sync_current)
        form.addRow("Name", self._name)
        self._colour = QComboBox()
        for hex_code in G.GROUP_COLOURS:
            self._colour.addItem(_COLOUR_NAMES.get(hex_code, hex_code),
                                 hex_code)
        form.addRow("Colour", self._colour)
        self._tag = QLineEdit()
        self._tag.setPlaceholderText("e.g. rotating — include every element with this tag")
        self._tag.editingFinished.connect(self._sync_current)
        form.addRow("Tag query", self._tag)
        self._description = QLineEdit()
        self._description.editingFinished.connect(self._sync_current)
        form.addRow("Description", self._description)
        right.addLayout(form)

        right.addWidget(QLabel("Members"))
        self._members = QPlainTextEdit()
        self._members.setReadOnly(True)
        self._members.setMinimumHeight(150)
        right.addWidget(self._members, 1)
        buttons = QHBoxLayout()
        self._add_sel = QPushButton("Add selection")
        self._add_sel.setToolTip("Add the elements selected on the canvas")
        self._add_sel.clicked.connect(self._add_selection)
        self._remove_sel = QPushButton("Remove selection")
        self._remove_sel.clicked.connect(self._remove_selection)
        buttons.addWidget(self._add_sel)
        buttons.addWidget(self._remove_sel)
        right.addLayout(buttons)

        self._tags_label = QLabel()
        right.addWidget(self._tags_label)
        tag_row = QHBoxLayout()
        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("Comma-separated tags")
        self._tags_edit.returnPressed.connect(self._apply_tags)
        self._tags_button = QPushButton("Apply to selection")
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
        self._update_selection_controls()

    # -- helpers --------------------------------------------------------
    def _element_name(self, ref: str) -> str:
        parsed = G.parse_ref(ref)
        if parsed is None:
            return ref
        kind, oid = parsed
        section, column = {
            "block": ("BLOCK_DATA", "blockName"),
            "node": ("NODE_DATA", "Name"),
            "event": ("EVENT_DATA", "eventName"),
            "hier": ("HIERARCHY_DATA", "hierarchyName"),
        }[kind]
        from raptor.core.fileio.typed import row_int
        sec = (getattr(self._rbd, "sections", None) or {}).get(section)
        for row in (sec.rows if sec is not None else ()):
            if row_int(row.get("ID"), 0) == oid:
                return str(row.get(column) or ref)
        return ref

    def _tags_for(self, ref: str) -> list[str]:
        return list(self._tags.get(ref, []))

    def _update_selection_controls(self) -> None:
        count = len(self._selected)
        self._add_sel.setEnabled(count > 0 and self._current_row >= 0)
        self._remove_sel.setEnabled(count > 0 and self._current_row >= 0)
        self._tags_button.setEnabled(count > 0)
        if count:
            self._tags_label.setText(
                f"Selected: {count} element(s). Tags apply to all of them.")
            common = self._tags_for(self._selected[0]) if count else []
            for ref in self._selected[1:]:
                common = [t for t in common if t in self._tags_for(ref)]
            self._tags_edit.setText(", ".join(common))
        else:
            self._tags_label.setText(
                "Select elements on the canvas to add them to a group or "
                "edit their tags.")
            self._tags_edit.clear()

    # -- list/form ------------------------------------------------------
    def _refresh_list(self) -> None:
        self._loading = True
        self._list.clear()
        for group in self._groups:
            item = QListWidgetItem(group["name"])
            item.setData(Qt.ItemDataRole.UserRole, group["id"])
            self._list.addItem(item)
        self._loading = False

    def _on_row_changed(self, row: int) -> None:
        if self._loading:
            return
        self._sync_current(self._current_row)
        self._current_row = row
        self._load_row(row)

    def _load_row(self, row: int) -> None:
        exists = 0 <= row < len(self._groups)
        self._loading = True
        for widget in (self._name, self._tag, self._description):
            widget.setEnabled(exists)
        self._colour.setEnabled(exists)
        if not exists:
            self._name.clear()
            self._tag.clear()
            self._description.clear()
            self._members.clear()
        else:
            group = self._groups[row]
            self._name.setText(group["name"])
            self._tag.setText(group.get("tag") or "")
            self._description.setText(group.get("description") or "")
            index = self._colour.findData(group.get("colour"))
            self._colour.setCurrentIndex(max(0, index))
            self._show_members(group)
        self._loading = False
        self._update_selection_controls()

    def _sync_current(self, row: int | None = None) -> None:
        if self._loading:
            return
        index = self._current_row if row is None else row
        if not (0 <= index < len(self._groups)):
            return
        group = self._groups[index]
        name = " ".join(self._name.text().split())
        if name:
            group["name"] = name
        colour = self._colour.currentData()
        if isinstance(colour, str) and colour:
            group["colour"] = colour
        tag = G.clean_tag(self._tag.text())
        if tag:
            group["tag"] = tag
        else:
            group.pop("tag", None)
        description = " ".join(self._description.text().split())
        if description:
            group["description"] = description
        else:
            group.pop("description", None)
        item = self._list.item(index)
        if item is not None:
            item.setText(group["name"])

    def _show_members(self, group: dict[str, Any]) -> None:
        explicit = set(group.get("members") or ())
        tag = G.clean_tag(group.get("tag"))
        rows = []
        for ref in sorted(explicit):
            rows.append(f"{ref}  {self._element_name(ref)}")
        if tag:
            rows.append(f"— every element tagged “{tag}” —")
        self._members.setPlainText("\n".join(rows) or "No members yet.")

    # -- buttons --------------------------------------------------------
    def _new_group(self) -> None:
        from types import SimpleNamespace
        self._sync_current()
        model = SimpleNamespace(tags=self._tags, groups=self._groups)
        name = "Group"
        n = 2
        while G.group_by_name(model, name) is not None:
            name = f"Group {n}"
            n += 1
        group = G.add_group(model, name, self._selected)
        self._tags, self._groups = model.tags, model.groups
        self._refresh_list()
        row = next((i for i, g in enumerate(self._groups)
                    if g["id"] == group["id"]), 0)
        self._list.setCurrentRow(row)

    def _delete_group(self) -> None:
        row = self._list.currentRow()
        if not (0 <= row < len(self._groups)):
            return
        name = self._groups[row]["name"]
        if QMessageBox.question(
                self, "Delete Group",
                f"Delete the group “{name}”?\n\nThe elements and their tags "
                "are not changed.") != QMessageBox.StandardButton.Yes:
            return
        del self._groups[row]
        self._refresh_list()
        self._current_row = -1
        self._list.setCurrentRow(min(row, len(self._groups) - 1))
        if not self._groups:
            self._load_row(-1)

    def _add_selection(self) -> None:
        if not self._selected:
            return
        self._sync_current()
        row = self._list.currentRow()
        if not (0 <= row < len(self._groups)):
            return
        group = self._groups[row]
        members = list(group.get("members") or ())
        for ref in self._selected:
            if ref not in members:
                members.append(ref)
        if members:
            group["members"] = members
        self._show_members(group)

    def _remove_selection(self) -> None:
        if not self._selected:
            return
        self._sync_current()
        row = self._list.currentRow()
        if not (0 <= row < len(self._groups)):
            return
        group = self._groups[row]
        selected = set(self._selected)
        members = [ref for ref in group.get("members") or ()
                   if ref not in selected]
        if members:
            group["members"] = members
        else:
            group.pop("members", None)
        self._show_members(group)

    def _apply_tags(self) -> None:
        if not self._selected:
            return
        values = [G.clean_tag(part) for part in
                  self._tags_edit.text().split(",")]
        values = [v for i, v in enumerate(values) if v and v not in values[:i]]
        for ref in self._selected:
            if values:
                self._tags[ref] = list(values)
            else:
                self._tags.pop(ref, None)
        self._update_selection_controls()
        if 0 <= self._current_row < len(self._groups):
            self._show_members(self._groups[self._current_row])

    # -- result ---------------------------------------------------------
    def result_data(self) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
        self._sync_current()
        return (copy.deepcopy(self._tags),
                G.clean_groups(copy.deepcopy(self._groups)))


class GroupsCommand(QUndoCommand):
    """One undoable step for a groups/tags edit."""

    def __init__(self, view: Any, before: tuple[Any, Any],
                 after: tuple[Any, Any], text: str = "Component groups"):
        super().__init__(text)
        self._view = view
        self._before = copy.deepcopy(before)
        self._after = copy.deepcopy(after)

    def _apply(self, data: tuple[Any, Any]) -> None:
        rbd = getattr(self._view, "_rbd_ref", None)
        if rbd is None:
            return
        rbd.tags, rbd.groups = copy.deepcopy(data[0]), copy.deepcopy(data[1])
        emitter = getattr(self._view, "model_changed", None)
        if emitter is not None:
            emitter.emit()

    def redo(self) -> None:
        self._apply(self._after)

    def undo(self) -> None:
        self._apply(self._before)


#: The old name kept for callers that imported it before this was a real
#: QUndoCommand; both behave the same.
_GroupsUndoCmd = GroupsCommand

"""The Part Library dock: apply, save and sync part types (WP-4.3).

The dock is bookkeeping over ordinary block columns.  Applying a part is one
undo step (a snapshot of the affected rows and the model's link record), and
the canvas reloads from the same model, so the engine never sees anything
but a normal block.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from raptor.api.library.links import (
    apply_part_to_rbd, detach_rbd, link_record, linked_blocks, local_parts,
    pending_updates,
)
from raptor.api.library.model import Library, part_from_block, read_library
from raptor.gui.resources.design_tokens import SPACING
from raptor.gui.ui.diagram_commands import _ViewCmd


class _PartSnapshotCmd(_ViewCmd):
    """One undoable part action: restore the block rows and link record."""

    def __init__(self, view: Any, before: tuple[Any, Any],
                 after: tuple[Any, Any], text: str) -> None:
        super().__init__(view, text)
        self._before = (copy.deepcopy(before[0]), copy.deepcopy(before[1]))
        self._after = (copy.deepcopy(after[0]), copy.deepcopy(after[1]))

    def _apply(self, state: tuple[Any, Any]) -> None:
        rows, extra = state
        rbd = self._v.rbd
        rbd.section("BLOCK_DATA").rows[:] = copy.deepcopy(rows)
        rbd.rbds_extra = copy.deepcopy(extra)
        self._v.load_rbd(rbd, keep_history=True)
        self._done()

    def redo(self) -> None:
        self._apply(self._after)

    def undo(self) -> None:
        self._apply(self._before)


class LibraryDock(QWidget):
    """Parts from the model and from attached `.rbdlib` files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._libraries: list[Library] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACING["sm"], SPACING["sm"],
                               SPACING["sm"], SPACING["sm"])
        lay.setSpacing(SPACING["sm"])
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search parts…")
        self.search.textChanged.connect(self.refresh)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self.apply_selected())
        lay.addWidget(self.list, 1)
        self.follows = QLabel("")
        self.follows.setObjectName("SectionNote")
        self.follows.setWordWrap(True)
        lay.addWidget(self.follows)
        self.status = QLabel("")
        self.status.setObjectName("SectionNote")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        for text, slot in (("Apply to selection", self.apply_selected),
                           ("Save selection as part…", self.save_selection),
                           ("Sync part changes", self.sync_parts),
                           ("Detach", self.detach_selected),
                           ("Add library…", self.add_library)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            lay.addWidget(button)

    # -- context --------------------------------------------------------

    def _window(self) -> Any:
        return self.window()

    def _rbd(self) -> Any:
        return getattr(self._window(), "_rbd", None)

    def _parts(self) -> list[tuple[str, Any]]:
        """(source, PartType) for the model and every attached library."""
        out: list[tuple[str, Any]] = []
        rbd = self._rbd()
        if rbd is not None:
            out.extend(("model", part) for part in
                       local_parts(rbd).values())
        for library in self._libraries:
            out.extend((library.path or library.name, part)
                       for part in library.parts.values())
        return out

    def showEvent(self, event) -> None:  # noqa: N802 - Qt name
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        rbd = self._rbd()
        self.list.clear()
        if rbd is None:
            self.follows.setText("No model open.")
            return
        follows = linked_blocks(rbd)
        counts: dict[str, int] = {}
        for record in follows.values():
            counts[record.get("part", "")] = counts.get(
                record.get("part", ""), 0) + 1
        needle = self.search.text().strip().lower()
        for source, part in self._parts():
            text = f"{part.name}  ·  {part.version}"
            haystack = " ".join((part.id, part.name, part.category,
                                 part.source)).lower()
            if needle and needle not in haystack:
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, (source, part.id))
            count = counts.get(part.id, 0)
            if count:
                item.setText(f"{text}  ·  {count} follow")
            item.setToolTip(f"{part.name}\n{part.category or 'uncategorised'}"
                            f"\n{part.source or 'no source'}")
            self.list.addItem(item)
        pending = 0
        for library in self._libraries:
            pending += len(pending_updates(rbd, library))
        self.follows.setText(
            f"{len(follows)} block(s) follow a part type"
            + (f"; {pending} change(s) pending" if pending else ""))

    def _selected_part(self) -> tuple[str, Any] | None:
        item = self.list.currentItem()
        if item is None:
            return None
        source, part_id = item.data(Qt.ItemDataRole.UserRole)
        for candidate_source, part in self._parts():
            if candidate_source == source and part.id == part_id:
                return source, part
        return None

    def _selected_blocks(self) -> list[int]:
        view = self._window()._diagram_dock.view
        return list(view.selected_elements()[0])

    # -- actions --------------------------------------------------------

    def _snapshot(self) -> tuple[Any, Any]:
        rbd = self._rbd()
        return (copy.deepcopy(rbd.section("BLOCK_DATA").rows),
                copy.deepcopy(getattr(rbd, "rbds_extra", None) or {}))

    def _commit(self, before: tuple[Any, Any], text: str) -> None:
        win = self._window()
        stack = getattr(win, "_undo_stack", None)
        if stack is not None:
            stack.push(_PartSnapshotCmd(win._diagram_dock.view, before,
                                        self._snapshot(), text))
        else:
            win._mark_dirty()
        win._explorer.refresh()
        self.refresh()

    def apply_selected(self) -> None:
        chosen = self._selected_part()
        blocks = self._selected_blocks()
        if chosen is None or not blocks:
            self.status.setText("Select a part and at least one block.")
            return
        source, part = chosen
        before = self._snapshot()
        rbd = self._rbd()
        for block_id in blocks:
            apply_part_to_rbd(rbd, str(block_id), part,
                              library=source if source != "model" else "")
        self._commit(before, f"Apply part type {part.name}")
        self.status.setText(f"Applied {part.name} to {len(blocks)} block(s).")

    def save_selection(self) -> None:
        blocks = self._selected_blocks()
        if not blocks:
            self.status.setText("Select a block to save as a part type.")
            return
        row = next(row for row in self._rbd().section("BLOCK_DATA").rows
                   if str(row.get("ID")) == str(blocks[0]))
        name, ok = QInputDialog.getText(
            self, "Save as part type", "Part type name:",
            text=str(row.get("blockName") or "Part"))
        if not ok or not name.strip():
            return
        part = part_from_block(row, name=name.strip())
        before = self._snapshot()
        apply_part_to_rbd(self._rbd(), str(blocks[0]), part)
        self._commit(before, f"Save part type {part.name}")
        self.status.setText(f"Saved {part.name} and linked the block to it.")

    def sync_parts(self) -> None:
        rbd = self._rbd()
        if rbd is None or not self._libraries:
            self.status.setText("Add a library before syncing.")
            return
        pending = []
        for library in self._libraries:
            pending.extend(pending_updates(rbd, library))
        if not pending:
            self.status.setText("Every linked block matches its part type.")
            return
        answer = QMessageBox.question(
            self, "Sync part types",
            f"{len(pending)} block(s) follow parts that have changed. "
            "Apply the changes, leaving overrides alone?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        before = self._snapshot()
        for entry in pending:
            part = next((part for library in self._libraries
                         for part in library.parts.values()
                         if part.id == entry["part"]), None)
            if part is None:
                continue
            record = link_record(rbd, entry["block"]) or {}
            apply_part_to_rbd(rbd, entry["block"], part,
                              overrides=list(record.get("overrides") or ()),
                              library=record.get("library", ""))
        self._commit(before, "Sync part types")
        self.status.setText(f"Applied {len(pending)} part update(s).")

    def detach_selected(self) -> None:
        blocks = self._selected_blocks()
        if not blocks:
            self.status.setText("Select a linked block.")
            return
        before = self._snapshot()
        changed = 0
        for block_id in blocks:
            if detach_rbd(self._rbd(), str(block_id))["detached"]:
                changed += 1
        if not changed:
            self.status.setText("The selection has no part links.")
            return
        self._commit(before, "Detach part types")
        self.status.setText(f"Detached {changed} block(s); values kept.")

    def add_library(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Add part library", "", "Part libraries (*.rbdlib *.json)")
        if not path:
            return
        try:
            library = read_library(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Library", str(exc))
            return
        self._libraries.append(library)
        self.refresh()
        self.status.setText(f"Added {Path(path).name}: "
                            f"{len(library.parts)} part(s).")

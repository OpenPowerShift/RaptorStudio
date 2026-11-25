"""The Part Library dock: reusable assemblies and part types (WP-4.3).

Two kinds of entry, grouped in one tree:

* **Assemblies** -- structure: any selection (a hierarchy with everything
  inside it, a redundant group with its nodes, an event) saved with its
  internal links.  Drag one onto the canvas, or double-click it, to insert
  a fresh copy (new ids, free names).
* **Part types** -- values: a block's (or node's, event's) data applied to
  elements that already exist, which then follow the part when it changes.

Every action is one undo step.  The engine never sees anything but ordinary
elements.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QMimeData, QObject, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QRadioButton, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from raptor.gui.ui import filedialogs as fdg
from raptor.api.library.assembly import (
    ASSEMBLY, add_local_part, adapted_buffer, assembly_from_buffer, describe,
    renamed_buffer,
)
from raptor.api.library.links import (
    all_links, apply_part_to_rbd, detach_element_rbd, element_record,
    local_parts, pending_updates,
)
from raptor.api.library.model import (
    Library, part_from_element, read_library, write_library,
)
from raptor.gui.resources.design_tokens import SPACING
from raptor.gui.ui import msgbox
from raptor.gui.ui.diagram_commands import _ViewCmd

#: Drag payload: {"source": ..., "id": ...} as JSON.
PART_MIME = "application/x-raptor-studio-part"

#: Tree groups, in display order: (kind, heading).
GROUPS = (
    (ASSEMBLY, "Assemblies"),
    ("block", "Block types"),
    ("node", "Node types"),
    ("event", "Event types"),
    ("hierarchy", "Hierarchy types"),
    ("annotation", "Notes"),
)
_ROLE = Qt.ItemDataRole.UserRole


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


class _PartTree(QTreeWidget):
    """The grouped part list; dragging an entry carries its identity."""

    def mimeData(self, items):  # noqa: N802 - Qt name
        mime = QMimeData()
        data = items[0].data(0, _ROLE) if items else None
        if data:
            mime.setData(PART_MIME, json.dumps(
                {"source": data[0], "id": data[1]}).encode("utf-8"))
        return mime

    def mimeTypes(self):  # noqa: N802 - Qt name
        return [PART_MIME]

    def startDrag(self, actions):  # noqa: N802 - Qt name
        item = self.currentItem()
        if item is None or not item.data(0, _ROLE):
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData([item]))
        drag.exec(Qt.DropAction.CopyAction)


class _CanvasDrop(QObject):
    """Accepts a part dropped on the canvas viewport."""

    def __init__(self, dock: LibraryDock, view: Any) -> None:
        super().__init__(view)
        self._dock, self._view = dock, view

    def eventFilter(self, _obj, event) -> bool:  # noqa: N802 - Qt name
        kind = event.type()
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if event.mimeData().hasFormat(PART_MIME):
                event.acceptProposedAction()
                return True
        elif kind == QEvent.Type.Drop and event.mimeData().hasFormat(PART_MIME):
            try:
                data = json.loads(bytes(event.mimeData().data(PART_MIME).data()))
            except ValueError:
                return False
            scene = self._view.mapToScene(event.position().toPoint())
            self._dock.use_part(str(data.get("source")), str(data.get("id")),
                                at_model=self._view.scene_to_model(scene))
            event.acceptProposedAction()
            return True
        return False


#: (stored basis, what the save dialog shows) for a part type's numbers.
BASIS_CHOICES = (("data", "Data — measured on our own equipment"),
                 ("handbook", "Handbook — a published source"),
                 ("experience", "Experience — the team's estimate"))


class _SaveDialog(QDialog):
    """Name the new entry and, for one block, choose part type or assembly."""

    def __init__(self, parent: QWidget, default: str, single_block: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save to Part Library")
        form = QFormLayout(self)
        self.name = QLineEdit(default)
        form.addRow("Name:", self.name)
        self.category = QLineEdit()
        self.category.setPlaceholderText("optional, e.g. Pumps")
        form.addRow("Category:", self.category)
        self.as_assembly = QRadioButton(
            "Assembly — a copy of the selection to insert anywhere")
        self.as_type = QRadioButton(
            "Part type — this block's values; the block follows it")
        self.as_assembly.setChecked(not single_block)
        self.as_type.setChecked(single_block)
        self.as_type.setEnabled(single_block)
        form.addRow(self.as_assembly)
        form.addRow(self.as_type)
        # where a part type's numbers came from: the reviewer's first question
        self.basis = QComboBox()
        for key, text in BASIS_CHOICES:
            self.basis.addItem(text, key)
        self.basis.setCurrentIndex(len(BASIS_CHOICES) - 1)
        self.basis.setToolTip("Where the failure and repair numbers came from; "
                              "kept with the part type")
        form.addRow("Numbers from:", self.basis)
        self.source = QLineEdit()
        self.source.setPlaceholderText("optional, e.g. OREDA 2015 table 4.2")
        form.addRow("Source:", self.source)
        for widget in (self.basis, self.source):
            widget.setEnabled(single_block)
        self.as_type.toggled.connect(
            lambda on: [w.setEnabled(on) for w in (self.basis, self.source)])
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class LibraryDock(QWidget):
    """Assemblies and part types from the model and attached libraries."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._libraries: list[Library] = []
        self._drop_filters: dict[int, _CanvasDrop] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACING["sm"], SPACING["sm"],
                               SPACING["sm"], SPACING["sm"])
        lay.setSpacing(SPACING["sm"])
        lay.addLayout(self._build_header())
        self._build_tree(lay)
        lay.addLayout(self._build_buttons())
        self._sync_buttons()

    def _build_header(self) -> QHBoxLayout:
        """The search box and the ⋯ menu of library actions."""
        top = QHBoxLayout()
        top.setSpacing(SPACING["xs"])
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search parts…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        top.addWidget(self.search, 1)
        self.more = QToolButton()
        self.more.setText("⋯")
        self.more.setToolTip("Libraries, sync and detach")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setStyleSheet(
            "QToolButton::menu-indicator { image: none; width: 0; }")
        self.more.setFixedWidth(self.search.sizeHint().height())
        menu = QMenu(self.more)
        menu.addAction("Open library file…", self.add_library)
        menu.addAction("Export model parts to a library file…",
                       self.export_library)
        menu.addSeparator()
        menu.addAction("Sync linked elements with their part types",
                       self.sync_parts)
        menu.addAction("Detach the selection from its part types",
                       self.detach_selected)
        menu.addSeparator()
        menu.addAction("Remove this entry from the model", self.remove_selected)
        self.more.setMenu(menu)
        top.addWidget(self.more)
        return top

    def _build_tree(self, lay: QVBoxLayout) -> None:
        self.tree = _PartTree()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.tree.setMinimumHeight(60)
        self.tree.itemDoubleClicked.connect(lambda _i, _c: self.use_selected())
        self.tree.currentItemChanged.connect(lambda *_: self._sync_buttons())
        self.list = self.tree           # the name older callers use
        lay.addWidget(self.tree, 1)

        self.status = QLabel("")
        self.status.setObjectName("SectionNote")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.status)
        self.follows = self.status      # the name older callers use

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACING["sm"])
        self.use_button = QPushButton("Insert")
        self.use_button.setToolTip(
            "Insert an assembly at the view centre, or apply a part type to "
            "the selected elements (you can also drag an entry onto the "
            "canvas)")
        self.use_button.clicked.connect(self.use_selected)
        self.save_button = QPushButton("Save selection…")
        self.save_button.setToolTip(
            "Save the selected elements as an assembly, or one block's "
            "values as a part type")
        self.save_button.clicked.connect(self.save_selection)
        row.addWidget(self.save_button, 1)
        row.addWidget(self.use_button, 1)
        return row

    # -- context --------------------------------------------------------

    def _window(self) -> Any:
        return self.window()

    def _view(self) -> Any:
        dock = getattr(self._window(), "_diagram_dock", None)
        return getattr(dock, "view", None)

    def _rbd(self) -> Any:
        view = self._view()
        rbd = getattr(view, "_rbd_ref", None) if view is not None else None
        return rbd if rbd is not None else getattr(self._window(), "_rbd", None)

    def _parts(self) -> list[tuple[str, Any]]:
        """(source, PartType) for the model and every attached library."""
        out: list[tuple[str, Any]] = []
        rbd = self._rbd()
        if rbd is not None:
            out.extend(("model", part) for part in local_parts(rbd).values())
        for library in self._libraries:
            out.extend((library.path or library.name, part)
                       for part in library.parts.values())
        return out

    def _find(self, source: str, part_id: str) -> Any:
        for candidate_source, part in self._parts():
            if candidate_source == source and part.id == part_id:
                return part
        return None

    def install_canvas_drop(self, view: Any) -> None:
        """Let parts be dropped on `view`'s canvas (once per view)."""
        if id(view) in self._drop_filters:
            return
        view.setAcceptDrops(True)
        view.viewport().setAcceptDrops(True)
        flt = _CanvasDrop(self, view)
        view.viewport().installEventFilter(flt)
        self._drop_filters[id(view)] = flt

    def showEvent(self, event) -> None:  # noqa: N802 - Qt name
        super().showEvent(event)
        view = self._view()
        if view is not None:
            self.install_canvas_drop(view)
        self.refresh()

    # -- listing --------------------------------------------------------

    def refresh(self) -> None:
        rbd = self._rbd()
        self.tree.clear()
        if rbd is None:
            self.status.setText("Open or create a model to use the library.")
            self._fit_status()
            self._sync_buttons()
            return
        counts: dict[str, int] = {}
        follows = all_links(rbd)
        for _kind, _element, record in follows:
            counts[record.get("part", "")] = counts.get(record.get("part", ""), 0) + 1
        needle = self.search.text().strip().lower()
        heads: dict[str, QTreeWidgetItem] = {}
        for source, part in self._parts():
            haystack = " ".join((part.id, part.name, part.category,
                                 part.source, part.kind)).lower()
            if needle and needle not in haystack:
                continue
            head = heads.get(part.kind)
            if head is None:
                title = dict(GROUPS).get(part.kind, part.kind.title())
                head = QTreeWidgetItem([title])
                head.setFlags(Qt.ItemFlag.ItemIsEnabled)
                font = head.font(0)
                font.setBold(True)
                head.setFont(0, font)
                heads[part.kind] = head
            detail = (describe(part) if part.kind == ASSEMBLY
                      else f"{counts.get(part.id, 0)} in use")
            where = "" if source == "model" else f" · {Path(source).stem}"
            item = QTreeWidgetItem([f"{part.name}   — {detail}{where}"])
            item.setData(0, _ROLE, (source, part.id))
            item.setToolTip(0, "\n".join(filter(None, (
                part.name, part.category or None,
                describe(part) if part.kind == ASSEMBLY else
                f"{part.kind} part type, {len(part.fields)} field(s); "
                f"numbers from {part.basis}",
                part.source or None,
                "Drag onto the canvas or double-click to "
                + ("insert a copy" if part.kind == ASSEMBLY
                   else "apply it to the selection")))))
            head.addChild(item)
        order = [kind for kind, _title in GROUPS]
        for kind in sorted(heads, key=lambda k: order.index(k) if k in order else 99):
            head = heads[kind]
            head.setText(0, f"{head.text(0)} ({head.childCount()})")
            self.tree.addTopLevelItem(head)
            head.setExpanded(True)
        if not heads:
            self.status.setText(
                "No parts yet. Select elements on the canvas and choose "
                "Save selection… — a hierarchy brings everything inside it."
                if not needle else "Nothing matches the search.")
        else:
            pending = sum(len(pending_updates(rbd, lib)) for lib in self._libraries)
            linked = len(follows)
            self.status.setText(
                (f"{linked} element(s) follow a part type" if linked else "")
                + (f"; {pending} change(s) to sync" if pending else ""))
        self._fit_status()
        self._sync_buttons()

    def _fit_status(self) -> None:
        """Keep the whole message: a dock's layout does not give a wrapped
        label the height its text needs, so the empty-library message was
        cut off after two lines."""
        width = self.status.width()
        self.status.setMinimumHeight(
            self.status.heightForWidth(width) if self.status.text() and width > 0 else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt name
        super().resizeEvent(event)
        self._fit_status()

    def _selected_part(self) -> tuple[str, Any] | None:
        item = self.tree.currentItem()
        data = item.data(0, _ROLE) if item is not None else None
        if not data:
            return None
        part = self._find(data[0], data[1])
        return (data[0], part) if part is not None else None

    def _sync_buttons(self) -> None:
        chosen = self._selected_part()
        self.use_button.setEnabled(chosen is not None)
        if chosen is None or chosen[1].kind in (ASSEMBLY, "annotation"):
            self.use_button.setText("Insert")
        else:
            self.use_button.setText("Apply to selection")
        self.save_button.setEnabled(self._rbd() is not None)

    def _selected(self, kind: str) -> list[int]:
        blocks, nodes, events, hiers = self._view().selected_elements()
        return {"block": blocks, "node": nodes, "event": events,
                "hierarchy": hiers}.get(kind, [])

    def _selected_annotation(self) -> tuple[int, dict[str, Any]] | None:
        view = self._view()
        chosen = [aid for aid, item in getattr(view, "_anno_items", {}).items()
                  if item.isSelected()]
        if not chosen:
            return None
        for entry in getattr(view, "annotations", []) or []:
            if int(entry.get("id", 0)) in chosen:
                return int(entry["id"]), entry
        return None

    # -- undo -----------------------------------------------------------

    def _snapshot(self) -> tuple[Any, Any]:
        rbd = self._rbd()
        return (copy.deepcopy(rbd.section("BLOCK_DATA").rows),
                copy.deepcopy(getattr(rbd, "rbds_extra", None) or {}))

    def _commit(self, before: tuple[Any, Any], text: str) -> None:
        win = self._window()
        stack = getattr(win, "_undo_stack", None)
        if stack is not None:
            stack.push(_PartSnapshotCmd(self._view(), before,
                                        self._snapshot(), text))
        elif hasattr(win, "_mark_dirty"):
            win._mark_dirty()
        explorer = getattr(win, "_explorer", None)
        if explorer is not None:
            explorer.refresh()
        self.refresh()

    # -- actions --------------------------------------------------------

    def use_selected(self) -> None:
        chosen = self._selected_part()
        if chosen is None:
            self.status.setText("Choose an entry in the library first.")
            return
        self.use_part(chosen[0], chosen[1].id)

    def use_part(self, source: str, part_id: str,
                 at_model: tuple[float, float] | None = None) -> int:
        """Insert an assembly (at `at_model`, else the view centre) or apply
        a part type to the selection.  Returns how many elements changed."""
        part = self._find(source, part_id)
        rbd, view = self._rbd(), self._view()
        if part is None or rbd is None or view is None:
            return 0
        if part.kind == ASSEMBLY:
            buffer, notes = adapted_buffer(rbd, part)
            buffer = renamed_buffer(rbd, buffer)
            if at_model is None:
                centre = view.mapToScene(view.viewport().rect().center())
                at_model = view.scene_to_model(centre)
            n = int(view.paste(at_model=at_model, buffer=buffer) or 0)
            self.status.setText(
                f"Inserted {part.name} ({n} element(s)); connect it in."
                + (" " + " ".join(notes) if notes else ""))
            return n
        before = self._snapshot()
        if part.kind == "annotation":
            view.add_annotation_from_part(part)
            self._commit(before, f"Add note from {part.name}")
            self.status.setText(f"Added a note from {part.name}.")
            return 1
        elements = self._selected(part.kind)
        if not elements:
            self.status.setText(
                f"Select the {part.kind}s to apply {part.name} to.")
            return 0
        for element_id in elements:
            apply_part_to_rbd(rbd, f"{part.kind}:{element_id}", part,
                              library=source if source != "model" else "")
        self._commit(before, f"Apply part type {part.name}")
        self.status.setText(f"Applied {part.name} to {len(elements)} {part.kind}(s).")
        return len(elements)

    def save_selection(self) -> None:
        rbd, view = self._rbd(), self._view()
        if rbd is None or view is None:
            return
        blocks, nodes, events, hiers = view.selected_elements()
        note = self._selected_annotation()
        total = len(blocks) + len(nodes) + len(events) + len(hiers)
        if not total and note is None:
            self.status.setText(
                "Select elements on the canvas first (a hierarchy brings "
                "everything inside it).")
            return
        single_block = total == 1 and len(blocks) == 1 and note is None
        default = "Part"
        row: dict[str, Any]
        if single_block:
            row = next((r for r in rbd.section("BLOCK_DATA").rows
                        if str(r.get("ID")) == str(blocks[0])), {})
            default = str(row.get("blockName") or default)
        elif len(hiers) == 1 and total == 1:
            row = next((r for r in rbd.section("HIERARCHY_DATA").rows
                        if str(r.get("ID")) == str(hiers[0])), {})
            default = str(row.get("hierarchyName") or default)
        dialog = _SaveDialog(self, default[:40], single_block)
        from raptor.gui import i18n
        if i18n.exec_translated(dialog) != QDialog.DialogCode.Accepted:
            return
        name = dialog.name.text().strip() or default
        before = self._snapshot()
        if dialog.as_type.isChecked() and single_block:
            row = next(r for r in rbd.section("BLOCK_DATA").rows
                       if str(r.get("ID")) == str(blocks[0]))
            part = part_from_element("block", row, name=name,
                                     category=dialog.category.text().strip(),
                                     source=dialog.source.text().strip(),
                                     basis=str(dialog.basis.currentData()))
            apply_part_to_rbd(rbd, f"block:{blocks[0]}", part)
            self._commit(before, f"Save part type {part.name}")
            self.status.setText(f"Saved part type {part.name}; the block follows it.")
            return
        buffer = list(view.selection_buffer() or [])
        try:
            part = assembly_from_buffer(buffer, name=name,
                                        category=dialog.category.text().strip())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        add_local_part(rbd, part)
        self._commit(before, f"Save assembly {part.name}")
        self.status.setText(f"Saved assembly {part.name}: {describe(part)}.")

    def remove_selected(self) -> None:
        chosen = self._selected_part()
        if chosen is None or chosen[0] != "model":
            self.status.setText("Choose an entry stored in the model.")
            return
        from raptor.api.library.assembly import remove_local_part
        before = self._snapshot()
        remove_local_part(self._rbd(), chosen[1].id)
        self._commit(before, f"Remove {chosen[1].name} from the library")

    def sync_parts(self) -> None:
        rbd = self._rbd()
        if rbd is None or not self._libraries:
            self.status.setText("Open a library file before syncing.")
            return
        pending = []
        for library in self._libraries:
            pending.extend(pending_updates(rbd, library))
        if not pending:
            self.status.setText("Every linked element matches its part type.")
            return
        answer = QMessageBox.question(
            self, "Sync part types",
            f"{len(pending)} element(s) follow parts that have changed. "
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
            record = element_record(rbd, entry["kind"], entry["element"]) or {}
            apply_part_to_rbd(rbd, entry["element"], part,
                              overrides=list(record.get("overrides") or ()),
                              library=record.get("library", ""))
        self._commit(before, "Sync part types")
        self.status.setText(f"Applied {len(pending)} part update(s).")

    def detach_selected(self) -> None:
        rbd = self._rbd()
        if rbd is None:
            return
        before = self._snapshot()
        changed = 0
        note = self._selected_annotation()
        if note is not None and detach_element_rbd(rbd, "annotation", note[0])["detached"]:
            changed += 1
        for kind in ("block", "node", "event", "hierarchy"):
            for element_id in self._selected(kind):
                if detach_element_rbd(rbd, kind, element_id)["detached"]:
                    changed += 1
        if not changed:
            self.status.setText("The selection has no part links.")
            return
        self._commit(before, "Detach part types")
        self.status.setText(f"Detached {changed} element(s); values kept.")

    def add_library(self) -> None:
        path, _ = fdg.getOpenFileName(
            self, "Open part library", "", "Part libraries (*.rbdlib *.json)")
        if path:
            self.open_library(path)

    def open_library(self, path: str) -> Library | None:
        try:
            library = read_library(path)
        except (OSError, ValueError) as exc:
            msgbox.warning(self, "Library", str(exc))
            return None
        self._libraries = [lib for lib in self._libraries if lib.path != path]
        self._libraries.append(library)
        self.refresh()
        self.status.setText(f"Opened {Path(path).name}: {len(library.parts)} part(s).")
        return library

    def export_library(self) -> None:
        rbd = self._rbd()
        if rbd is None:
            return
        parts = local_parts(rbd)
        if not parts:
            self.status.setText("The model holds no parts to export.")
            return
        path, _ = fdg.getSaveFileName(
            self, "Export parts to a library", "", "Part library (*.rbdlib)")
        if not path:
            return
        if not path.endswith(".rbdlib"):
            path += ".rbdlib"
        try:
            library = read_library(path)
        except (OSError, ValueError):
            library = Library(Path(path).stem, {}, path=path)
        library.parts.update(parts)
        write_library(library, path)
        self.open_library(path)

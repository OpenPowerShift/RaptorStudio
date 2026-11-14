"""Quick Add at the cursor (WP-1.6): kind, existing block or part type."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
)

from raptor.gui.quick_match import Candidate, rank


class QuickAdd(QFrame):
    """A small popup over the canvas; Enter adds the chosen item."""

    def __init__(self, view: Any) -> None:
        super().__init__(view)
        self._view = view
        self._model_xy = (0.0, 0.0)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("QuickAdd")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Add block, node, event, hierarchy, "
                                      "or an existing block…")
        lay.addWidget(self.query)
        self.list = QListWidget()
        self.list.setMinimumWidth(360)
        self.list.setMaximumHeight(240)
        lay.addWidget(self.list)
        self.query.textChanged.connect(self._fill)
        self.query.returnPressed.connect(self._choose)
        self.list.itemActivated.connect(lambda _item: self._choose())
        self._fill("")

    def _candidates(self) -> tuple[list[Candidate], dict[str, Any]]:
        data: dict[str, Any] = {}
        out = []
        for key, label, words in (
                ("pattern:series", "Series of n blocks…",
                 ("series", "chain", "row")),
                ("pattern:parallel", "n in parallel (k of n)…",
                 ("parallel", "redundant", "k of n"))):
            data[key] = key
            out.append(Candidate(label, "Pattern", keywords=words, data=key))
        for kind, label in (("block", "Block"),
                            ("node", "Node"),
                            ("event", "Event"),
                            ("hierarchy", "Hierarchy")):
            data[f"kind:{kind}"] = kind
            out.append(Candidate(label, "Element kind", keywords=(kind,),
                                 data=f"kind:{kind}"))
        for block in getattr(self._view, "blocks", {}).values():
            key = f"block:{block.id}"
            data[key] = block
            out.append(Candidate(f"{block.name} (copy)", "In this model",
                                 keywords=("block", "duplicate"), data=key))
        from raptor.api.library.links import local_parts
        rbd = getattr(self._view, "rbd", None)
        if rbd is not None:
            for part in local_parts(rbd).values():
                key = f"part:{part.id}"
                data[key] = part
                out.append(Candidate(part.name, part.category or "Part type",
                                     keywords=(part.id, "part", "library"),
                                     data=key))
        return out, data

    def _fill(self, query: str) -> None:
        candidates, self._data = self._candidates()
        self.list.clear()
        for candidate in rank(query, candidates):
            item = QListWidgetItem(f"{candidate.text}    "
                                   f"{candidate.category}")
            item.setData(Qt.ItemDataRole.UserRole, candidate.data)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def show_at(self, global_pos: Any, scene_pos: Any) -> None:
        self._model_xy = (
            self._view._vt.x_to_model(scene_pos.x()),
            self._view._vt.y_to_model(scene_pos.y()))
        self.query.clear()
        self._fill("")
        self.adjustSize()
        self.move(global_pos)
        self.show()
        self.query.setFocus()

    def _choose(self) -> None:  # noqa: C901, PLR0912 - one branch per add kind
        item = self.list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        value = self._data.get(key)
        if value is None:
            return
        x, y = self._model_xy
        view = self._view
        self.close()
        if key == "pattern:series":
            from PySide6.QtWidgets import QInputDialog
            count, ok = QInputDialog.getInt(self, "Series", "Blocks:", 3, 1, 50)
            if ok:
                view.add_series(count, x, y)
        elif key == "pattern:parallel":
            from PySide6.QtWidgets import QInputDialog
            count, ok = QInputDialog.getInt(
                self, "Parallel", "Units:", 2, 1, 50)
            if ok:
                k, ok = QInputDialog.getInt(
                    self, "Parallel", "Required (k):", count, 1, count)
                if ok:
                    view.add_parallel(count, k, x, y)
        elif key == "kind:block":
            view.add_block("Block", x, y)
        elif key == "kind:node":
            view.add_node("Node", x, y)
        elif key == "kind:event":
            view.add_event("Event", x, y)
        elif key == "kind:hierarchy":
            view.add_hierarchy("Hierarchy", x, y)
        elif isinstance(key, str) and key.startswith("block:"):
            row = next((r for r in view.rbd.section("BLOCK_DATA").rows
                        if str(r.get("ID")) == str(value.id)), None)
            if row is not None:
                view.add_block_from_template(dict(row), at_model=(x, y))
        elif isinstance(key, str) and key.startswith("part:"):
            block_id = view.add_block(value.name, x, y)
            from raptor.api.library.links import apply_part_to_rbd
            row = next((r for r in view.rbd.section("BLOCK_DATA").rows
                        if str(r.get("ID")) == str(block_id)), None)
            if row is not None:
                apply_part_to_rbd(view.rbd, str(block_id), value)
                view.load_rbd(view.rbd, keep_history=True)

"""Quick Add at the cursor (WP-1.6): kind, existing block or part type."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QLineEdit, QListWidget, QListWidgetItem, QStyle,
    QStyledItemDelegate, QVBoxLayout,
)

from raptor.gui.quick_match import Candidate, rank

#: Item role holding a row's category ("Pattern", "In this model", ...).
CATEGORY_ROLE = Qt.ItemDataRole.UserRole + 1

#: The row shown in place of the patterns when no block is selected.
HINT = "hint:select"

#: key -> (label, search words). "series:n" puts n copies of the block in
#: a row (n - 1 new); "parallel:k:n" n side by side, k of them needed.
PATTERNS: dict[str, tuple[str, tuple[str, ...]]] = {
    "series:2": ("2 in series", ("series", "chain", "row", "two")),
    "series:3": ("3 in series", ("series", "chain", "row", "three")),
    "parallel:1:2": ("2 in parallel, 1 needed",
                     ("parallel", "redundant", "1 of 2", "1oo2", "spare")),
    "parallel:2:3": ("3 in parallel, 2 needed",
                     ("parallel", "redundant", "2 of 3", "2oo3", "voting")),
    "parallel:1:3": ("3 in parallel, 1 needed",
                     ("parallel", "redundant", "1 of 3", "1oo3")),
}


class _CategoryDelegate(QStyledItemDelegate):
    """Name on the left, its category right-aligned and dimmed: the names
    line up in one column whatever their length."""

    def paint(self, painter: Any, option: Any, index: Any) -> None:
        super().paint(painter, option, index)
        category = index.data(CATEGORY_ROLE)
        if not category:
            return
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        palette = option.palette
        painter.setPen(palette.highlightedText().color() if selected
                       else palette.placeholderText().color())
        rect = option.rect.adjusted(0, 0, -8, 0)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter), str(category))
        painter.restore()


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
        self.query.setPlaceholderText("Repeat the selected block, or add "
                                      "a copy or a part type…")
        lay.addWidget(self.query)
        self.list = QListWidget()
        self.list.setMinimumWidth(360)
        self.list.setItemDelegate(_CategoryDelegate(self.list))
        lay.addWidget(self.list)
        self.query.textChanged.connect(self._fill)
        self.query.returnPressed.connect(self._choose)
        self.list.itemActivated.connect(lambda _item: self._choose())
        self._fill("")

    @staticmethod
    def _recent() -> list[str]:
        from PySide6.QtCore import QSettings
        value = QSettings().value("quick_add/recent", [])
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return []

    def _note_recent(self, key: str) -> None:
        from PySide6.QtCore import QSettings
        recent = [key] + [value for value in self._recent() if value != key]
        QSettings().setValue("quick_add/recent", recent[:8])

    def _selected_block(self) -> tuple[int, str] | None:
        """(id, name) of the one selected block, or None."""
        items = getattr(self._view, "_block_items", {})
        chosen = [bid for bid, item in items.items()
                  if bid != 0 and item.isSelected()]
        if len(chosen) != 1:
            return None
        block = self._view.blocks.get(chosen[0])
        return chosen[0], (block.name if block is not None else str(chosen[0]))

    def _candidates(self) -> tuple[list[Candidate], dict[str, Any]]:
        """User testing: the element kinds repeated the ribbon's Add
        buttons, and the series and parallel patterns asked for a count and
        then dropped blank blocks unconnected at the cursor. Now the
        patterns repeat the selected block, wired in where it is, in the
        few arrangements people use."""
        data: dict[str, Any] = {}
        out = []
        recent = {value: index for index, value in
                  enumerate(self._recent())}
        selected = self._selected_block()
        if selected is None:
            data[HINT] = None
            out.append(Candidate("Select a block to repeat it in series "
                                 "or parallel", "", keywords=(
                                     "series", "parallel", "redundant",
                                     "k of n"), data=HINT))
        else:
            name = selected[1]
            for key, (label, words) in PATTERNS.items():
                data[key] = key
                out.append(Candidate(label.format(name=name),
                                     f"Repeat {name}", keywords=words,
                                     data=key,
                                     recent=recent.get(key, 99)))
        rbd = getattr(self._view, "rbd", None)
        # Every block in the model, not only those drawn at this level: a
        # block inside a hierarchy is as good a template as one beside you.
        rows = (rbd.section("BLOCK_DATA").rows
                if rbd is not None and "BLOCK_DATA" in rbd.sections else [])
        for row in rows:
            block_id = str(row.get("ID", ""))
            if not block_id or block_id == "0":
                continue
            key = f"block:{block_id}"
            data[key] = block_id
            name = row.get("blockName") or row.get("Name") or block_id
            out.append(Candidate(f"Copy of {name}", "In this model",
                                 keywords=("block", "copy", "duplicate"),
                                 data=key, recent=recent.get(key, 99)))
        from raptor.api.library.links import local_parts
        if rbd is not None:
            for part in local_parts(rbd).values():
                key = f"part:{part.id}"
                data[key] = part
                out.append(Candidate(part.name, part.category or "Part type",
                                     keywords=(part.id, "part", "library"),
                                     data=key,
                                     recent=recent.get(key, 99)))
        return out, data

    def _fill(self, query: str) -> None:
        candidates, self._data = self._candidates()
        self.list.clear()
        for candidate in rank(query, candidates):
            item = QListWidgetItem(candidate.text)
            item.setData(CATEGORY_ROLE, candidate.category)
            item.setData(Qt.ItemDataRole.AccessibleTextRole,
                         f"{candidate.text}, {candidate.category}")
            item.setData(Qt.ItemDataRole.UserRole, candidate.data)
            if candidate.data == HINT:
                # an instruction, not a choice: shown dimmed, never picked
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setForeground(self.palette().placeholderText())
            self.list.addItem(item)
        # as tall as its rows, up to the old fixed height: two rows sat
        # above a large empty box
        rows = max(self.list.count(), 1)
        height = (rows * max(self.list.sizeHintForRow(0), 1)
                  + 2 * self.list.frameWidth() + 2)
        self.list.setFixedHeight(min(height, 240))
        self.adjustSize()
        for row in range(self.list.count()):
            if self.list.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.list.setCurrentRow(row)
                break

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

    def _choose(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key == HINT:
            return
        value = self._data.get(key)
        if value is None:
            return
        self._note_recent(key)
        self.close()
        _add(self._view, str(key), value, self._model_xy)


def _add(view: Any, key: str, value: Any, at: tuple[float, float]) -> None:
    """Carry out one Quick Add row: a pattern on the selected block, a copy
    of a block, or a part type at `at` (model units)."""
    x, y = at
    if key.startswith("series:"):
        count = int(key.split(":")[1])
        if view.duplicate_series(count - 1):
            _say(view, f"{count} in series")
    elif key.startswith("parallel:"):
        _kind, k, n = key.split(":")
        if view.duplicate_parallel(int(n) - 1, int(k)):
            _say(view, f"{n} in parallel, {k} needed")
    elif key.startswith("block:"):
        row = next((r for r in view.rbd.section("BLOCK_DATA").rows
                    if str(r.get("ID")) == str(value)), None)
        if row is not None:
            view.add_block_from_template(dict(row), at_model=(x, y))
    elif key.startswith("part:"):
        block_id = view.add_block(value.name, x, y)
        from raptor.api.library.links import apply_part_to_rbd
        row = next((r for r in view.rbd.section("BLOCK_DATA").rows
                    if str(r.get("ID")) == str(block_id)), None)
        if row is not None:
            apply_part_to_rbd(view.rbd, str(block_id), value)
            view.load_rbd(view.rbd, keep_history=True)


def _say(view: Any, text: str) -> None:
    from raptor.gui.ui.diagram_items import _view_status
    _view_status(view, text)

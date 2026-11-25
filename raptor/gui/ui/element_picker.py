"""Element lists that can be searched and scoped to a hierarchy (U23).

A study window's list of blocks (or elements) grows with the model; on a
large one, scrolling a few hundred names is the slow part. `searchable`
lets the list be typed into, matching anywhere in a name; `ScopeBox` is a
second list, *Whole model* or one hierarchy, that hides every entry
outside it (the hierarchies inside the chosen one included). The list
keeps its own items, indexes and signals, so a window that reads
`currentIndex()` or `currentText()` works as before.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QCompleter, QListView

from raptor.core.fileio.element_size import NAME_COLUMNS


def searchable(combo: QComboBox) -> QComboBox:
    """Let `combo` be typed into: a popup lists the entries containing
    what was typed, in any case. Text that matches no entry is put back
    to the chosen one when typing stops, so `currentText()` is always an
    entry."""
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    completer = QCompleter(combo.model(), combo)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    combo.setCompleter(completer)
    edit = combo.lineEdit()
    if edit is not None:
        edit.setPlaceholderText("Type to search")
        edit.setClearButtonEnabled(True)

        def settle() -> None:
            index = combo.findText(combo.currentText(), Qt.MatchFlag.MatchFixedString)
            if index >= 0:
                combo.setCurrentIndex(index)
            elif combo.currentIndex() >= 0:
                combo.setEditText(combo.itemText(combo.currentIndex()))
        edit.editingFinished.connect(settle)
    return combo


def hierarchy_paths(rbd: Any) -> dict[int, tuple[str, int]]:
    """hierarchy id -> ("Power ▸ Pumps", parent id), for every hierarchy."""
    rows = {}
    for row in getattr(rbd.sections.get("HIERARCHY_DATA"), "rows", []) if rbd else []:
        try:
            hid = int(float(row.get("ID") or 0))
            parent = int(float(row.get("Parent") or 0))
        except (TypeError, ValueError):
            continue
        if hid:
            rows[hid] = (str(row.get(NAME_COLUMNS["HIERARCHY_DATA"]) or f"Hierarchy {hid}"),
                         parent)
    out: dict[int, tuple[str, int]] = {}
    for hid, (_name, parent) in rows.items():
        names, current, seen = [], hid, set()
        while current in rows and current not in seen:
            seen.add(current)
            names.append(rows[current][0])
            current = rows[current][1]
        out[hid] = (" ▸ ".join(reversed(names)), parent)
    return out


def parent_of(rbd: Any, kind: str, oid: int) -> int:
    """The hierarchy an element sits in directly (0: the top level)."""
    section, column = {"block": ("BLOCK_DATA", "ParentId"), "node": ("NODE_DATA", "parent"),
                       "event": ("EVENT_DATA", "ParentId"),
                       "hier": ("HIERARCHY_DATA", "Parent")}.get(kind, ("", ""))
    for row in getattr(rbd.sections.get(section), "rows", []) if rbd and section else []:
        try:
            if int(float(row.get("ID") or 0)) == oid:
                return int(float(row.get(column) or 0))
        except (TypeError, ValueError):
            continue
    return 0


class ScopeBox(QComboBox):
    """*Whole model* or one hierarchy: hides `combo`'s entries outside it.

    `homes[i]` is the hierarchy entry i sits in directly (0 for the top
    level, None for an entry that belongs to the whole model, such as a
    rate, which is never hidden)."""

    def __init__(self, rbd: Any, combo: QComboBox, homes: list[int | None]) -> None:
        super().__init__()
        # Hierarchy names are model data, not chrome.
        self.setProperty("raptor_ignore_i18n", True)
        self._combo = combo
        self._homes = list(homes)
        self._paths = hierarchy_paths(rbd)
        self.setToolTip("Show only what is inside one hierarchy (and the "
                        "hierarchies inside it)")
        self.addItem("Whole model", None)
        self.addItem("Top level only", 0)
        for hid, (path, _parent) in sorted(self._paths.items(), key=lambda kv: kv[1][0].lower()):
            self.addItem(path, hid)
        self.setVisible(bool(self._paths))
        self.currentIndexChanged.connect(self._apply)

    def set_homes(self, homes: list[int | None]) -> None:
        self._homes = list(homes)
        self._apply()

    def _inside(self, home: int | None, scope: int | None) -> bool:
        if scope is None or home is None:
            return True
        if scope == 0:
            return home == 0
        current, seen = home, set()
        while current and current not in seen:
            if current == scope:
                return True
            seen.add(current)
            current = self._paths.get(current, ("", 0))[1]
        return False

    def visible_rows(self) -> list[int]:
        scope = self.currentData()
        return [i for i in range(self._combo.count())
                if self._inside(self._homes[i] if i < len(self._homes) else None, scope)]

    def _apply(self) -> None:
        shown = set(self.visible_rows())
        view = self._combo.view()
        for i in range(self._combo.count()):
            if isinstance(view, QListView):
                view.setRowHidden(i, i not in shown)
        if shown and self._combo.currentIndex() not in shown:
            self._combo.setCurrentIndex(min(shown))

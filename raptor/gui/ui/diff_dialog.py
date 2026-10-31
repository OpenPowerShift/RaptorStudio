"""Model > Differences: what is different between this model and another --
its own saved file (what have I changed?), any model file, or a recent one.

Parameters and wiring both: `raptor.api.model_diff.diff_models`, the same
comparison as `raptor diff` and the MCP `diff_models` tool. The open model
is compared as it is now, unsaved edits included. Rows are grouped by
element; double-click one to find it on the canvas.
"""
from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QRadioButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)

from raptor.gui.resources.design_tokens import active_colour

_GROUP_TITLES = {
    "elements": "Elements", "links": "Connections",
    "structure": "Structure", "system": "System settings",
    "rates": "Rates", "spare_pools": "Spare pools",
    "resources": "Resources", "triggers": "Triggers", "phases": "Phases",
    "project": "Project",
}
_KIND = {"Block": "block", "Node": "node", "Event": "event",
         "Hier": "hier", "Hierarchy": "hier"}


def load_model(path: str):
    from raptor.core.fileio.model_file import load_model_file
    return load_model_file(path)


def _ids_here(result: dict) -> dict[tuple[str, str], int]:
    """(kind, path shown) -> id in THIS model (the diff's B side): changed,
    renamed and added elements exist here; removed ones only in the
    other."""
    out: dict[tuple[str, str], int] = {}
    el = result.get("elements", {})
    for e in el.get("changed", []) + el.get("added", []):
        if "id" in e:
            out[(e["kind"], e.get("path", e.get("name", "")))] = int(e["id"])
    for e in el.get("renamed", []):
        out[(e["kind"], e["from"])] = int(e["id"])
    return out


class DiffDialog(QDialog):
    """Modeless; compares on every choice (it is quick) and on Refresh."""

    def __init__(self, win, other_path: str | None = None):
        super().__init__(win)
        self._win = win
        self._result: dict | None = None
        self.setWindowTitle("Differences")
        self.setModal(False)
        lay = QVBoxLayout(self)
        self._build_choices(lay, other_path)
        self._build_results(lay)
        self.resize(820, 600)
        self._wire()
        self.compare()

    def _build_choices(self, lay, other_path: str | None) -> None:
        intro = QLabel("Compare this model — as it is now, unsaved edits "
                       "included — with:")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self._saved = QRadioButton("Its saved file — what have I changed?")
        self._other = QRadioButton("Another model:")
        self._recent = QRadioButton("An open or recent model:")
        self._choice = QButtonGroup(self)
        for i, b in enumerate((self._saved, self._other, self._recent)):
            self._choice.addButton(b, i)
        self._other_path = other_path or ""
        self._other_label = QLabel(os.path.basename(self._other_path)
                                   or "(none chosen)")
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        self._recent_combo = QComboBox()
        self._recent_combo.setMinimumContentsLength(30)
        self._fill_recent()
        lay.addWidget(self._saved)
        row = QHBoxLayout()
        row.addWidget(self._other)
        row.addWidget(self._other_label, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self._recent)
        row.addWidget(self._recent_combo, 1)
        lay.addLayout(row)
        self._everything = QCheckBox(
            "Also show layout and computed values (positions, link counts)")
        lay.addWidget(self._everything)

    def _build_results(self, lay) -> None:
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setObjectName("SummarySentence")
        lay.addWidget(self._summary)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Element", "Field", "Other", "This model"])
        self._tree.header().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setUniformRowHeights(True)
        self._tree.itemDoubleClicked.connect(self._locate)
        lay.addWidget(self._tree, 1)

        buttons = QHBoxLayout()
        export = QPushButton("Export…")
        export.clicked.connect(self._export)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Compare again (after more edits)")
        refresh.clicked.connect(self.compare)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(export)
        buttons.addStretch(1)
        buttons.addWidget(refresh)
        buttons.addWidget(close)
        lay.addLayout(buttons)

    def _wire(self) -> None:
        win = self._win
        other_path = self._other_path
        has_file = bool(win._current_file and os.path.isfile(
            win._current_file))
        self._saved.setEnabled(has_file)
        if not has_file:
            self._saved.setToolTip("This model has not been saved yet")
        self._recent.setEnabled(self._recent_combo.count() > 0)
        (self._other if other_path else
         self._saved if has_file else self._other).setChecked(True)
        self._choice.idClicked.connect(lambda _i: self.compare())
        self._recent_combo.currentIndexChanged.connect(
            lambda _i: self._recent.isChecked() and self.compare())
        self._everything.toggled.connect(lambda _on: self.compare())

    def _fill_recent(self) -> None:
        """Models open in other windows first (compared as they are now,
        unsaved edits included), then recent files not open anywhere.
        Refilled each time the window is brought up: windows come and go."""
        from raptor.gui import open_models
        win, combo = self._win, self._recent_combo
        combo.blockSignals(True)
        combo.clear()
        here = os.path.abspath(win._current_file or "")
        open_paths = {here}
        for om in open_models.others(win):
            combo.addItem(om.description, om)
            if om.path:
                open_paths.add(os.path.abspath(om.path))
        for p in win._recent_list() if hasattr(win, "_recent_list") else []:
            if os.path.abspath(p) not in open_paths:
                combo.addItem(os.path.basename(p), p)
        combo.blockSignals(False)
        if hasattr(self, "_recent"):
            self._recent.setEnabled(combo.count() > 0)

    # -- choosing ---------------------------------------------------------
    def _browse(self) -> None:
        from raptor.gui.ui.filedialogs import getOpenFileName
        start = os.path.dirname(self._win._current_file or "") or os.getcwd()
        path, _ = getOpenFileName(self, "Compare with", start,
                                  "Raptor models (*.rbds *.rbd)")
        if path:
            self.set_other(path)

    def set_open_model(self, om) -> None:
        """Compare with a model open in another window."""
        i = next((i for i in range(self._recent_combo.count())
                  if getattr(self._recent_combo.itemData(i), "window", None)
                  is om.window), -1)
        if i < 0:
            self._recent_combo.insertItem(0, om.description, om)
            i = 0
        self._recent_combo.setCurrentIndex(i)
        self._recent.setEnabled(True)
        self._recent.setChecked(True)
        self.compare()

    def set_other(self, path: str) -> None:
        self._other_path = path
        self._other_label.setText(os.path.basename(path))
        self._other.setChecked(True)
        self.compare()

    def _target(self) -> tuple[str, str] | None:
        """(path, match mode) of the model to compare with."""
        if self._saved.isChecked():
            path = self._win._current_file
            return (path, "id") if path else None
        if self._other.isChecked():
            return (self._other_path, "auto") if self._other_path else None
        source = self._recent_combo.currentData()
        return (source, "auto") if source else None

    # -- comparing --------------------------------------------------------
    def compare(self) -> None:
        from raptor.api.model_diff import diff_models
        target = self._target()
        self._tree.clear()
        self._result = None
        if target is None:
            self._summary.setText("Choose a model to compare with.")
            return
        source, match = target
        if isinstance(source, str):
            path, name = source, os.path.basename(source)
            try:
                other = load_model(path)
            except Exception as exc:                      # noqa: BLE001
                self._summary.setText(
                    f"Couldn't read {name}: it is not a model this program "
                    f"can open ({type(exc).__name__}).")
                return
        else:                                    # open in another window
            path, name = source.path, source.label
            try:
                other = source.model()
            except RuntimeError:
                self._summary.setText(f"{name} has been closed.")
                return
        if getattr(self._win, "_rbd", None) is None:
            self._summary.setText("No model is open — close this window "
                                  "or open a model and try again.")
            return
        mine = self._win._model_for_engine(copy=True)
        ignore = () if self._everything.isChecked() else ("layout", "derived")
        # The other model is the "before", this one the "after": a block
        # added since the last save reads "added", not "removed".
        self._result = diff_models(
            other, mine, match=match, ignore=ignore,
            labels=(name, "This model"),
            paths=(path, self._win._current_file))
        self._tree.setHeaderLabels(
            ["Element", "Field", "Saved file" if match == "id" else name,
             "This model"])
        self._show(self._result, name)

    def _show(self, result: dict, other_name: str) -> None:
        from raptor.api.model_diff import flatten
        rows = flatten(result)
        s = result["summary"]
        if result["identical"]:
            what = ", ".join(result.get("ignored") or []) or "nothing"
            self._summary.setText(
                f"No differences from {other_name} (ignoring: {what}).")
            return
        bits = []
        for key, word in (("elements_changed", "changed"),
                          ("elements_added", "new in this model"),
                          ("elements_removed", f"only in {other_name}"),
                          ("renamed", "renamed")):
            if s.get(key):
                bits.append(f"{s[key]} element{'s' if s[key] != 1 else ''} "
                            f"{word}")
        self._summary.setText(
            f"Compared with {other_name}: " + (", ".join(bits) or
                                               "no element changes")
            + f"; connections: {s.get('connectivity', '')}. Double-click an "
              f"element to find it on the canvas.")
        self._fill(rows, _ids_here(result))

    def _fill(self, rows: list[dict], ids: dict) -> None:
        c = active_colour()
        colour = {"added": c["result_green"], "removed": c["result_red"]}
        groups: dict[str, QTreeWidgetItem] = {}
        elements: dict[tuple, QTreeWidgetItem] = {}
        for r in rows:
            g = groups.get(r["group"])
            if g is None:
                g = QTreeWidgetItem([_GROUP_TITLES.get(r["group"], r["group"])])
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                self._tree.addTopLevelItem(g)
                groups[r["group"]] = g
            key = (r["group"], r["kind"], r["path"])
            parent = elements.get(key)
            if parent is None:
                label = f"{r['kind']} {r['path']}".strip()
                parent = QTreeWidgetItem([label])
                oid = ids.get((r["kind"], r["path"]))
                if oid is not None and r["kind"] in _KIND:
                    parent.setData(0, Qt.ItemDataRole.UserRole,
                                   (_KIND[r["kind"]], oid))
                    parent.setToolTip(0, "Double-click to find it")
                g.addChild(parent)
                elements[key] = parent
            item = QTreeWidgetItem(["", r["field"], r["a"], r["b"]])
            # "(added)" = new in this model; "(removed)" = only in the other.
            tint = ("added" if r["field"] == "(added)" else
                    "removed" if r["field"] == "(removed)" else None)
            if tint:
                item.setText(1, "new here" if tint == "added"
                             else f"only in {self._tree.headerItem().text(2)}")
            if tint:
                for col in range(4):
                    item.setForeground(col, QBrush(QColor(colour[tint])))
            parent.addChild(item)
        for g in groups.values():
            g.setText(0, f"{g.text(0)} ({g.childCount()})")
        self._tree.expandAll()

    # -- acting ------------------------------------------------------------
    def _locate(self, item: QTreeWidgetItem, _col: int) -> None:
        ref = item.data(0, Qt.ItemDataRole.UserRole) or (
            item.parent().data(0, Qt.ItemDataRole.UserRole)
            if item.parent() is not None else None)
        if not ref:
            self._win.statusBar().showMessage(
                "That one is only in the other model", 5000)
            return
        kind, oid = ref
        self._win._diagram_dock.view.locate_id(kind, int(oid))

    def _export(self) -> None:
        from raptor.api.markdown import diff_markdown
        from raptor.api.model_diff import flatten
        from raptor.gui.ui.filedialogs import getSaveFileName
        from raptor.gui.ui.table_export import grid_to_csv
        if not self._result:
            return
        start = os.path.dirname(self._win._current_file or "") or os.getcwd()
        path, chosen = getSaveFileName(
            self, "Export differences",
            os.path.join(start, "differences.csv"),
            "CSV (*.csv);;Markdown (*.md)")
        if not path:
            return
        if path.lower().endswith(".md") or "Markdown" in (chosen or ""):
            text = diff_markdown(self._result)
        else:
            cols = ["group", "kind", "path", "field", "category", "a", "b"]
            text = grid_to_csv(cols, [[r[c] for c in cols]
                                      for r in flatten(self._result)])
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        self._win.statusBar().showMessage(
            f"Saved {os.path.basename(path)}", 5000)


def open_diff(win, other_path: str | None = None,
              open_model: Any = None) -> Any:
    dlg = getattr(win, "_diff_dialog", None)
    # The window is kept for the model it was opened on. Another model (a
    # file opened, File > New) gets a fresh one: the saved-file choice and
    # the recent list are about the model, and a kept one offered a saved
    # file that a new model does not have.
    model = (id(win._rbd), win._current_file)
    try:
        if dlg is not None and getattr(dlg, "_model", None) != model:
            dlg.close()
            dlg.deleteLater()
            dlg = None
    except RuntimeError:
        dlg = None
    try:
        if dlg is not None and open_model is not None:
            dlg._fill_recent()
            dlg.set_open_model(open_model)
        elif dlg is not None and other_path:
            dlg.set_other(other_path)
        elif dlg is not None:
            dlg._fill_recent()
            dlg.compare()
    except RuntimeError:
        dlg = None
    if dlg is None:
        dlg = DiffDialog(win, other_path)
        dlg._model = model
        if open_model is not None:
            dlg.set_open_model(open_model)
        win._diff_dialog = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg

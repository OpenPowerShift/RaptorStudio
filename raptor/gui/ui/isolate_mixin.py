"""Extract a hierarchy as a standalone model (a port extension, not a Raptor feature).

The command shows what crosses the hierarchy's boundary and how each kind of hazard will
be treated (`raptor.api.isolate`), then saves the extract as its own `.rbd` / `.rbds`
file and can open it in the window, where it runs like any model. Raptor 7 opens the
saved `.rbd`, so the same file can be checked there.

Expects on `self`: `_rbd`, `_diagram_dock.view`, `_statusbar`, `_current_file`,
`_confirm_discard_unsaved()`, `_load_into_app()`, `_remember_recent()`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.gui.ui import msgbox

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QPlainTextEdit, QVBoxLayout,
)

from raptor.gui.ui import filedialogs as fdg


class IsolateDialog(QDialog):
    """Hazards of one hierarchy, and a treatment for each kind."""

    def __init__(self, parent, rbd, hierarchy_id: int, hierarchy_name: str):
        super().__init__(parent)
        from raptor.api import isolate
        self._rbd, self._hid = rbd, hierarchy_id
        self.setWindowTitle(f"Extract Hierarchy — {hierarchy_name}")
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        note = QLabel(
            f"<b>{hierarchy_name}</b> becomes a standalone model: its input node is the "
            "start and its output node the end, so its input is always available.")
        note.setWordWrap(True)
        lay.addWidget(note)
        form = QFormLayout()
        self.deps = QComboBox()
        self.deps.addItem("Assume outside elements are always up", "up")
        self.deps.addItem("Follow the extract's own end", "system")
        self.deps.addItem("Refuse (stop if any)", "refuse")
        self.pools = QComboBox()
        self.pools.addItem("Give it the whole pool", "full")
        self.pools.addItem("Share by the fraction of users inside", "share")
        self.pools.addItem("Refuse (stop if any)", "refuse")
        self.streams = QComboBox()
        self.streams.addItem("Keep the random streams", "keep")
        self.streams.addItem("Give shared draws their own streams", "separate")
        form.addRow("Outside dependencies:", self.deps)
        form.addRow("Shared spare / resource pools:", self.pools)
        form.addRow("Shared random streams:", self.streams)
        lay.addLayout(form)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        lay.addWidget(self.report, 1)
        self.open_after = QCheckBox("Open the extract in this window after saving")
        self.open_after.setChecked(True)
        lay.addWidget(self.open_after)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        for c in (self.deps, self.pools, self.streams):
            c.currentIndexChanged.connect(self._refresh)
        self._isolate = isolate
        self._refresh()

    def treatments(self) -> dict:
        return {"deps": self.deps.currentData(), "pools": self.pools.currentData(),
                "streams": self.streams.currentData(), "items": {}}

    def _refresh(self) -> None:
        try:
            hazards = self._isolate.scan(self._rbd, self._hid, self.treatments())
        except self._isolate.IsolationError as exc:
            self.report.setPlainText(str(exc))
            return
        if not hazards:
            self.report.setPlainText("Nothing crosses the hierarchy's boundary.")
            return
        lines = []
        for h in hazards:
            line = f"[{h.kind}] {', '.join(h.elements)}\n    {h.detail}"
            if h.treatment:
                line += f"\n    -> {h.treatment}" + (f" ({h.effect})" if h.effect else "")
            lines.append(line)
        self.report.setPlainText("\n\n".join(lines))


class IsolateMixin:
    """The "Extract Hierarchy…" command."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        def __getattr__(self, name: str) -> Any:
            """The window's own attributes, which this mixin reaches through `self`."""

    def _selected_hierarchy(self):
        v = self._diagram_dock.view
        hiers = v.selected_elements()[3]
        if len(hiers) != 1:
            return None
        return hiers[0], v.hiers[hiers[0]].name

    def _on_extract_hierarchy(self) -> None:
        if self._rbd is None:
            return
        picked = self._selected_hierarchy()
        if picked is None:
            self._statusbar.showMessage(
                "Extract Hierarchy: select one hierarchy oval on the canvas first", 6000)
            return
        hid, name = picked
        dlg = IsolateDialog(self, self._rbd, hid, name)
        dlg.show()
        self._isolate_dialog = dlg
        dlg.accepted.connect(lambda: self._finish_extract(dlg, hid, name))

    def _finish_extract(self, dlg: IsolateDialog, hid: int, name: str) -> None:
        from raptor.api import isolate
        from raptor.core.fileio.builder import save as save_rbd, sync_counts, validate
        from raptor.core.fileio.rbds import save_rbds
        try:
            extracted, record = isolate.extract(self._rbd, hid, dlg.treatments())
        except isolate.IsolationError as exc:
            msgbox.warning(self, "Extract Hierarchy", str(exc))
            return
        stem = Path(self._current_file).stem if self._current_file else "model"
        suggested = f"{stem}_{name}.rbd".replace(" ", "_")
        path, _ = fdg.getSaveFileName(
            self, "Save the extracted hierarchy as", suggested,
            "RBD files (*.rbd);;RBDS files (*.rbds)")
        if not path:
            return
        if not path.lower().endswith((".rbd", ".rbds")):
            path += ".rbd"
        try:
            sync_counts(extracted)
            if path.lower().endswith(".rbd"):
                errors = [e for e in validate(extracted) if e.startswith("ERROR")]
                if errors:
                    raise ValueError("\n".join(errors))
                save_rbd(extracted, path)
            else:
                save_rbds(extracted, path, meta={"file_name": extracted.file_name})
        except Exception as exc:       # noqa: BLE001 - shown to the user
            msgbox.critical(self, "Extract Hierarchy", f"Could not save:\n{exc}")
            return
        try:
            Path(path).with_suffix(".isolation.txt").write_text(record.text())
        except OSError:
            pass
        self._statusbar.showMessage(
            f"Saved {Path(path).name} — {record.banner}", 10000)
        if dlg.open_after.isChecked() and self._confirm_discard_unsaved():
            from raptor.core.fileio.builder import load
            self._load_into_app(load(path) if path.lower().endswith(".rbd")
                                else _load_rbds(path), path)
            self._remember_recent(path)


def _load_rbds(path: str):
    from raptor.core.fileio.rbds import load_rbds
    return load_rbds(path)

"""File > Export > Export Tables...

Pick which tables (the model's inputs and, after a run, its results), in
which formats (CSV, text, one spreadsheet with a tab per table), and where.
Everything about WHAT a table is comes from :mod:`raptor.api.tables`, the
same code the MCP server uses, so the two never disagree.

The dialog is a plain object with properties (`selected_keys`, `options`,
`folder`) so it can be driven without running its modal loop.
"""
from __future__ import annotations
from raptor.gui.ui.modality import exec_dialog

from raptor.gui.ui import msgbox

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from raptor.api.table_export import (
    ExportOptions,
    default_folder,
    export_tables,
    plan_files,
    safe_stem,
)
from raptor.api.tables import (
    INPUTS,
    RESULTS,
    ResultsBundle,
    Table,
    TableInfo,
    all_tables,
    catalogue,
    collect,
)
from raptor.gui.ui import settings

_KEY_ROLE = Qt.ItemDataRole.UserRole

#: (settings key, label, tooltip) for the format checkboxes, in order.
FORMATS: tuple[tuple[str, str, str], ...] = (
    ("xlsx", "Excel workbook (.xlsx) — one tab per table",
     "One workbook with a tab for every table: numbers as numbers, "
     "filters, frozen headers, a glossary and pages set up to print."),
    ("ods", "OpenDocument spreadsheet (.ods) — one tab per table",
     "The same workbook for LibreOffice, OpenOffice and other "
     "OpenDocument applications."),
    ("csv", "CSV — one file per table",
     "Comma-separated files any spreadsheet or script can read."),
    ("txt_files", "Text — one file per table",
     "Fixed-width plain text, one file for each table."),
    ("txt_report", "Text — one combined report",
     "Every chosen table in a single plain-text report."),
)


class ExportTablesDialog(QDialog):
    def __init__(self, win, rbd, bundle: ResultsBundle | None,
                 model_path: str | None, model_name: str):
        super().__init__(win)
        self.setWindowTitle("Export Tables")
        self.setModal(True)
        self.resize(560, 640)
        self._rbd, self._bundle = rbd, bundle
        self._stem = safe_stem(model_name)
        self._model_name = model_name
        # Built once: every click on the tree used to rebuild every table
        # (the whole event log on a large run), twice, to count file names.
        self._tables = all_tables(rbd, bundle)
        self._infos = catalogue(rbd, bundle, tables=self._tables)

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "Choose the tables to write. Inputs are the model's data; "
            "Results are what the last run produced."))

        # -- the tables ------------------------------------------------
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Table", "Rows"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAccessibleName("Tables to export")
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header()
                                                .ResizeMode.Stretch)
        self._fill_tree()
        outer.addWidget(self.tree, 1)

        pick = QHBoxLayout()
        for text, slot in (("Select All", self.select_all),
                           ("Select None", self.select_none),
                           ("Defaults", self.select_defaults)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            pick.addWidget(b)
        pick.addStretch(1)
        outer.addLayout(pick)

        outer.addWidget(self._build_formats())
        outer.addLayout(self._build_folder_row(model_path))

        self.open_after = QCheckBox("Open the folder when done")
        self.open_after.setChecked(settings.get_bool("tables/open_folder"))
        outer.addWidget(self.open_after)

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setObjectName("SectionNote")
        outer.addWidget(self.preview)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel)
        self.export_button = self.buttons.addButton(
            "Export", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)

        self.tree.itemChanged.connect(self._update_preview)
        self.select_defaults()
        self._update_preview()

    def _build_formats(self) -> QGroupBox:
        box = QGroupBox("Format")
        layout = QVBoxLayout(box)
        self.format_boxes: dict[str, QCheckBox] = {}
        for key, label, tip in FORMATS:
            cb = QCheckBox(label)
            cb.setToolTip(tip)
            cb.setChecked(settings.get_bool(f"tables/{key}"))
            cb.toggled.connect(self._update_preview)
            layout.addWidget(cb)
            self.format_boxes[key] = cb
        return box

    def _build_folder_row(self, model_path: str | None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Folder:"))
        self.folder_edit = QLineEdit(str(default_folder(model_path)))
        self.folder_edit.setAccessibleName("Folder to write the tables to")
        self.folder_edit.setToolTip(
            "Created if it does not exist. Files of the same names are "
            "replaced.")
        self.folder_edit.textChanged.connect(self._update_preview)
        row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        return row

    # -- tree ---------------------------------------------------------------

    def _fill_tree(self) -> None:
        self.tree.blockSignals(True)
        for group in (INPUTS, RESULTS):
            top = QTreeWidgetItem([group, ""])
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable
                         | Qt.ItemFlag.ItemIsAutoTristate)
            top.setCheckState(0, Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(top)
            for info in (i for i in self._infos if i.group == group):
                child = QTreeWidgetItem(
                    [info.title, str(info.rows) if info.available else ""])
                child.setData(0, _KEY_ROLE, info.key)
                child.setTextAlignment(1, Qt.AlignmentFlag.AlignRight)
                if info.available:
                    child.setFlags(child.flags()
                                   | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    child.setFlags(child.flags()
                                   & ~Qt.ItemFlag.ItemIsEnabled
                                   & ~Qt.ItemFlag.ItemIsUserCheckable)
                    child.setText(0, f"{info.title} — {info.reason}")
                    child.setToolTip(0, info.reason.capitalize())
                top.addChild(child)
            top.setExpanded(True)
        self.tree.blockSignals(False)

    def _groups(self) -> list[QTreeWidgetItem]:
        items = (self.tree.topLevelItem(i)
                 for i in range(self.tree.topLevelItemCount()))
        return [t for t in items if t is not None]

    @staticmethod
    def _kids(top: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        kids = (top.child(j) for j in range(top.childCount()))
        return [k for k in kids if k is not None]

    def _children(self):
        for top in self._groups():
            yield from self._kids(top)

    def _set_checked(self, predicate) -> None:
        self.tree.blockSignals(True)
        for child in self._children():
            key = child.data(0, _KEY_ROLE)
            if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                child.setCheckState(
                    0, Qt.CheckState.Checked if predicate(key)
                    else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._refresh_group_boxes()
        self._update_preview()

    def _refresh_group_boxes(self) -> None:
        """Group rows show all/some/none from their children."""
        self.tree.blockSignals(True)
        for top in self._groups():
            states = [k.checkState(0) for k in self._kids(top)
                      if k.flags() & Qt.ItemFlag.ItemIsUserCheckable]
            if not states or all(s == Qt.CheckState.Unchecked
                                 for s in states):
                top.setCheckState(0, Qt.CheckState.Unchecked)
            elif all(s == Qt.CheckState.Checked for s in states):
                top.setCheckState(0, Qt.CheckState.Checked)
            else:
                top.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self.tree.blockSignals(False)

    def select_all(self) -> None:
        self._set_checked(lambda _k: True)

    def select_none(self) -> None:
        self._set_checked(lambda _k: False)

    def select_defaults(self) -> None:
        defaults = {i.key for i in self._infos if i.default and i.available}
        self._set_checked(lambda k: k in defaults)

    # -- what was chosen ------------------------------------------------------

    def selected_keys(self) -> list[str]:
        return [c.data(0, _KEY_ROLE) for c in self._children()
                if (c.flags() & Qt.ItemFlag.ItemIsUserCheckable)
                and c.checkState(0) == Qt.CheckState.Checked]

    def chosen_tables(self) -> list[Table]:
        """The ticked tables, in catalogue order (already built)."""
        return collect(self._rbd, self._bundle, self.selected_keys(),
                       tables=self._tables)

    def options(self) -> ExportOptions:
        b = self.format_boxes
        return ExportOptions(csv=b["csv"].isChecked(),
                             txt_files=b["txt_files"].isChecked(),
                             txt_report=b["txt_report"].isChecked(),
                             xlsx=b["xlsx"].isChecked(),
                             ods=b["ods"].isChecked())

    def folder(self) -> Path:
        return Path(self.folder_edit.text().strip()).expanduser()

    def info_for(self, key: str) -> TableInfo:
        return next(i for i in self._infos if i.key == key)

    # -- feedback ---------------------------------------------------------------

    def _update_preview(self, *_args) -> None:
        self._refresh_group_boxes()
        keys, opts = self.selected_keys(), self.options()
        if not keys:
            text, ok = "Choose at least one table.", False
        elif not opts.any():
            text, ok = "Choose at least one format.", False
        elif not self.folder_edit.text().strip():
            text, ok = "Choose a folder.", False
        else:
            tables = self.chosen_tables()
            names = plan_files(tables, opts, self._stem)
            existing = [n for n in names if (self.folder() / n).exists()]
            text = (f"{len(tables)} table(s) → {len(names)} file(s) in "
                    f"{self.folder()}")
            if existing:
                text += (f"\n{len(existing)} existing file(s) will be "
                         f"replaced.")
            ok = True
        self.preview.setText(text)
        self.export_button.setEnabled(ok)

    def _browse(self) -> None:
        from raptor.gui.ui import filedialogs as fdg
        path = fdg.getExistingDirectory(
            self, "Folder for the tables", str(self.folder()))
        if path:
            self.folder_edit.setText(path)

    def remember_choices(self) -> None:
        for key, cb in self.format_boxes.items():
            settings.set_value(f"tables/{key}", cb.isChecked())
        settings.set_value("tables/open_folder", self.open_after.isChecked())


# -- entry point ---------------------------------------------------------------

def gather(win):
    """(rbd, results bundle or None, model path, model name) from the window,
    with the canvas's current edits written back first."""
    win._diagram_dock.view.write_back_to_rbd(win._rbd)
    name = Path(win._current_file).name if win._current_file else "Untitled"
    units = ""
    sec = win._rbd.sections.get("SYSTEM_DATA")
    if sec and sec.rows:
        units = str(sec.rows[0].get("units", "")).strip()
    bundle = win._results.results_bundle(model_name=name, units=units)
    return win._rbd, bundle, win._current_file, name


class _Background:
    """`work(tell)` on a worker thread while the main thread keeps the
    window painted. `tell(text)` only stores the text (the worker must not
    touch Qt); the main thread shows it in the busy indicator and the
    status bar."""

    def __init__(self, win, work, message: str) -> None:
        import threading
        self.win, self.work = win, work
        self.text, self.shown = message, ""
        self.result: Any = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="export-tables")

    def tell(self, text: str) -> None:
        self.text = text

    def _run(self) -> None:
        try:
            self.result = self.work(self.tell)
        except BaseException as e:      # handed back to the main thread
            self.error = e

    def _show(self) -> None:
        if self.text == self.shown:
            return
        self.shown = self.text
        dlg = getattr(self.win, "_busy_dialog", None)
        if dlg is not None:
            dlg.setLabelText(self.shown)
        status = getattr(self.win, "_statusbar", None)
        if status is not None:
            status.showMessage(self.shown)

    def wait(self) -> Any:
        from PySide6.QtCore import QEventLoop, QTimer
        loop, timer = QEventLoop(), QTimer()

        def poll() -> None:
            self._show()
            if not self.thread.is_alive():
                loop.quit()

        timer.setInterval(50)
        timer.timeout.connect(poll)
        self.thread.start()
        timer.start()
        try:
            if self.thread.is_alive():
                loop.exec()
            self.thread.join()
        finally:
            timer.stop()
        if self.error is not None:
            raise self.error
        return self.result


def _in_background(win, work, message: str) -> Any:
    """Run `work(tell)` on a worker thread behind the window's busy
    indicator and wait for it; return what it returned, re-raise what it
    raised."""
    busy = getattr(win, "_begin_busy", None)
    created = busy(message) if busy else False
    try:
        return _Background(win, work, message).wait()
    finally:
        if created:
            win._end_busy(created)


def perform_export(win, dlg: ExportTablesDialog):
    """Write what the dialog chose. Returns the ExportResult, or None if it
    failed (the failure has been shown). The files are written on a worker
    thread, so the window stays responsive and says what it is writing."""
    tables = dlg.chosen_tables()
    folder, options = dlg.folder(), dlg.options()
    try:
        info = (win._results.run_info(dlg._rbd, model=dlg._model_name,
                                      file=win._current_file or "")
                if dlg._bundle is not None else None)

        def work(tell):
            return export_tables(
                tables, folder, options, model_name=dlg._model_name,
                stem=dlg._stem, info=info,
                progress=lambda name: tell(f"Writing {name}…"))

        result = _in_background(
            win, work, f"Exporting {len(tables)} table(s)…")
    except Exception as e:      # file I/O: never let it cross the slot
        msgbox.critical(
            win, "Export Tables Failed",
            f"Could not write the tables:\n\n{e}")
        win._statusbar.showMessage("Table export failed", 8000)
        return None
    dlg.remember_choices()
    win._statusbar.showMessage(
        f"Exported {result.tables} table(s) as {len(result.files)} file(s) "
        f"to {result.folder}", 10000)
    if dlg.open_after.isChecked():
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.folder)))
    return result


def open_export_tables_dialog(win) -> None:
    """The window's `Export Tables...` command."""
    if win._rbd is None:
        return
    # Reading a large run's results into tables takes a moment: say so.
    busy = getattr(win, "_begin_busy", None)
    created = busy("Preparing the tables…") if busy else False
    try:
        rbd, bundle, path, name = gather(win)
        dlg = ExportTablesDialog(win, rbd, bundle, path, name)
    finally:
        if created:
            win._end_busy(created)
    if exec_dialog(dlg) == QDialog.DialogCode.Accepted:
        perform_export(win, dlg)

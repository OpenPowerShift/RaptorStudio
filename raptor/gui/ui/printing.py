"""Print and Export PDF: the options dialog they share, and Print itself.

Both go through `_prompt_export_options` (which sections, the title block,
the page orientation) and then `report_pdf`, which makes one PDF report;
Print sends that report's pages to the printer. So a printed report and an
exported one are the same document."""
from __future__ import annotations

from typing import Any

from raptor.gui.ui.modality import exec_dialog


def _default_project_name(win) -> str:
    """The open file's own name, as the title block's Project default.

    Better than a blank field (which is all this used to ever show) and
    better than a global setting, since a printout is attributable to
    the model it came from."""
    path = getattr(win, "_current_path", None)
    if path:
        from pathlib import Path
        stem = Path(path).stem
        if stem:
            return stem
    return "NewModel"


def _apply_print_title_block(win, name: str, revision: str,
                             date: str) -> None:
    """Persist the title block onto the OPEN MODEL, so a second print
    (or a colleague's print of the same file) carries the same
    attribution.

    Stored on the `RbdFile` rather than in QSettings: it describes this
    model, and two models open side by side must not overwrite each
    other's title block. It rides in `.rbds` (the `project_meta` key);
    a legacy `.rbd` has nowhere to put it, so it is simply not saved
    there -- same precedent as annotations and `display_unit`."""
    rbd = getattr(win, "_rbd", None)
    if rbd is None:
        return
    meta = {"name": name, "revision": revision, "date": date}
    if getattr(rbd, "project_meta", None) == meta:
        return
    rbd.project_meta = meta
    win._mark_dirty()


def collect_raw_report_sections(win) -> list[tuple[str, list[str]]]:
    """`(title, lines)` for each non-empty raw report view -- End of Run,
    Final Results, Interim Cost. The text is the engine's report output as
    written, not reflowed. The one collector: the PDF report takes its raw
    reports from here. It reads `win._results` privates; that is the
    contract between the Results dock and the report.
    """
    out = []
    views = dict(getattr(win._results, "_raw_views", {}) or {})
    interim = getattr(win._results, "_interim_cost_view", None)
    if interim is not None:
        views["Interim Cost"] = interim
    for name, view in views.items():
        text = view.toPlainText()
        if text.strip():
            out.append((f"Raw Report — {name}", text.splitlines()))
    return out



#: (opts key, checkbox label, requires a completed run, ticked by default)
EXPORT_SECTIONS = [
    ("diagram", "Diagram (every hierarchy level)", False, True),
    ("data_tables", "Model data (blocks, nodes, pools, ...)", False, True),
    ("results", "Results (summary, logistics, sparing, cost, ...)", True,
     True),
    ("weak", "Weak link analysis", True, True),
    ("graphs", "Graph (the one on the Graphs tab)", True, True),
    ("event_log", "Event log (the current run and filter; long)", True,
     False),
    ("raw", "Raw reports (End of Run, Final Results, Interim Cost; long)",
     True, False),
]


def _prompt_export_options(win, title: str) -> dict | None:
    """Shared section picker for Print and Export PDF -- one place so
    both cover the same full set of result surfaces instead of drifting
    apart. Returns None on Cancel."""
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QFormLayout, QCheckBox, QLineEdit,
        QPushButton, QHBoxLayout, QGroupBox, QSpinBox, QLabel,
    )
    import time as _time

    has_results = bool(getattr(win._results, "_report_data", None))
    dlg = QDialog(win)
    dlg.setWindowTitle(title)
    outer = QVBoxLayout(dlg)

    sections = QGroupBox("Include")
    sform = QVBoxLayout(sections)
    checks: dict[str, QCheckBox] = {}
    tiles_spin = None
    for key, label, needs_run, default in EXPORT_SECTIONS:
        cb = QCheckBox(label)
        enabled = has_results if needs_run else True
        cb.setChecked(enabled and default)
        cb.setEnabled(enabled)
        if needs_run and not enabled:
            cb.setToolTip("Run a simulation first")
        sform.addWidget(cb)
        checks[key] = cb
        if key == "diagram":
            # Raptor 7's Print Window dialog: "specify the number
            # of pages to print the window across" -- 1 (default)
            # keeps the shrink-to-fit behaviour; >1 tiles a large
            # diagram across that many pages at one consistent,
            # legible scale instead.
            tile_row = QHBoxLayout()
            tile_row.setContentsMargins(20, 0, 0, 0)
            tile_row.addWidget(QLabel("Pages across:"))
            tiles_spin = QSpinBox()
            tiles_spin.setRange(1, 20)
            tiles_spin.setValue(1)
            tiles_spin.setToolTip(
                "Tile a large diagram across this many pages, each "
                "printed at the same legible scale, instead of "
                "shrinking the whole thing onto one page")
            tile_row.addWidget(tiles_spin)
            tile_row.addStretch(1)
            tiles_spin.setEnabled(cb.isChecked())
            cb.toggled.connect(tiles_spin.setEnabled)
            sform.addLayout(tile_row)
    outer.addWidget(sections)

    meta_box = QGroupBox("Title block")
    mform = QFormLayout(meta_box)
    # Pre-fill from the OPEN FILE, not from a global QSettings blob.
    # `project_meta` was read here and never written anywhere in the
    # tree, so Project and Revision opened blank on every launch and the
    # title block -- the thing that makes a printout attributable -- was
    # permanently missing its identifying content. The natural default
    # for "Project" is the file's own name, which we have; and the edits
    # belong with the model (see the save side in
    # `_apply_print_title_block`).
    saved = dict(getattr(win._rbd, "project_meta", None) or {})
    name_edit = QLineEdit(saved.get("name") or _default_project_name(win))
    rev_edit = QLineEdit(saved.get("revision", ""))
    date_edit = QLineEdit(saved.get("date") or _time.strftime("%Y-%m-%d"))
    mform.addRow("Project:", name_edit)
    mform.addRow("Revision:", rev_edit)
    mform.addRow("Date:", date_edit)
    outer.addWidget(meta_box)

    # The default page orientation.
    orient_box = QGroupBox("Page")
    oform = QFormLayout(orient_box)
    from PySide6.QtWidgets import QComboBox as _QComboBox
    orient = _QComboBox()
    # Portrait by default: a wide table, a diagram or report text gets a
    # landscape page of its own whichever is chosen.
    orient.addItem("Portrait", "portrait")
    orient.addItem("Landscape", "landscape")
    oform.addRow("Orientation:", orient)
    outer.addWidget(orient_box)

    buttons = QHBoxLayout()
    ok = QPushButton("OK")
    cancel = QPushButton("Cancel")
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    buttons.addStretch(1)
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    outer.addLayout(buttons)

    if exec_dialog(dlg) != QDialog.DialogCode.Accepted:
        return None
    opts: dict[str, Any] = {key: cb.isChecked() for key, cb in checks.items()}
    opts["name"] = name_edit.text()
    opts["revision"] = rev_edit.text()
    opts["date"] = date_edit.text()
    opts["diagram_tiles"] = tiles_spin.value() if tiles_spin else 1
    opts["orientation"] = orient.currentData() or "portrait"
    # Write the title block back onto the model so it sticks (it was
    # read-only-from-a-key-nobody-wrote before, so it never did).
    _apply_print_title_block(win, opts["name"], opts["revision"],
                             opts["date"])
    return opts


def export_pdf(win, path: str, opts: dict) -> None:
    """Export PDF (see `report_pdf`)."""
    from raptor.gui.ui.report_pdf import export_pdf as _export
    _export(win, path, opts)


def open_print_dialog(win) -> None:
    """Ribbon "Print…" / Ctrl+P entry point: the Export PDF report, sent
    to the printer page by page (`report_pdf.print_report`)."""
    if win._rbd is None:
        return
    opts = _prompt_export_options(win, "Print")
    if opts is None:
        return
    from PySide6.QtPrintSupport import QPrintDialog, QPrinter
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    pd = QPrintDialog(printer, win)
    pd.setWindowTitle("Print")
    if pd.exec() != QPrintDialog.DialogCode.Accepted:
        return
    win._diagram_dock.view.write_back_to_rbd(win._rbd)
    from raptor.gui.ui.report_pdf import print_report
    print_report(win, printer, opts)
    win._statusbar.showMessage("Sent to printer")

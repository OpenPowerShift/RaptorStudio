"""Results raw-report tab and the Appendix C file exports.

Expects on `self`: the results tabs and the report data held by `ResultsDock`."""
from __future__ import annotations

from raptor.gui.ui.results_common import (
    _mono_font,
)

import os



from raptor.gui.ui import msgbox

from typing import Any, cast

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPlainTextEdit,
)

from raptor.gui.ui import filedialogs as fdg
from raptor.gui.resources.design_tokens import SPACING
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
else:
    _Base = object


class ResultsRawMixin(_Base):
    """Results raw-report tab and the Appendix C file exports."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _appendix_c_checks: Any
        _appendix_c_dialog: Any
        _interim_cost_view: Any
        _raw_page: Any
        _raw_sources: Any
        _raw_tabs: Any
        _raw_views: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def _build_raw_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QHBoxLayout()
        bar.setSpacing(SPACING["sm"])
        bar.setContentsMargins(SPACING["md"], SPACING["xs"],
                               SPACING["md"], 0)
        from PySide6.QtWidgets import QPushButton
        save_btn = QPushButton("Save As…")
        save_btn.setToolTip(
            "Save the currently selected sub-tab's report text "
            "verbatim as a .txt file.")
        save_btn.clicked.connect(self._save_raw_report)
        appc_btn = QPushButton("Export Data Files…")
        appc_btn.setToolTip(
            "Data files: Key Parameters, Ending "
            "Simulation Times, Availability Plot Data (per-trial data "
            "already computed for this run, just never written to "
            "disk in these formats)")
        appc_btn.clicked.connect(self._export_appendix_c_files)
        bar.addStretch(1)
        bar.addWidget(appc_btn)
        bar.addWidget(save_btn)
        outer.addLayout(bar)

        raw_tabs = QTabWidget()
        raw_tabs.setDocumentMode(True)
        #: Full backing sources for the raw tabs (list or ResultStream).
        #: The view shows only a bounded preview; Save Raw Report writes
        #: the source, so a huge End-of-Run report is never materialised
        #: in the widget.
        self._raw_sources: dict[str, object] = {}
        self._raw_views: dict[str, QPlainTextEdit] = {}
        for name in ("End of Run", "Final Results"):
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setFont(_mono_font())
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self._raw_views[name] = view
            raw_tabs.addTab(view, name)
        # Interim Cost Report: the engine's periodic
        # cost snapshots (`_cost_sink` rows), byte-exact.
        self._interim_cost_view = QPlainTextEdit()
        self._interim_cost_view.setReadOnly(True)
        self._interim_cost_view.setFont(_mono_font())
        self._interim_cost_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._interim_cost_view.hide()
        raw_tabs.addTab(self._interim_cost_view, "Interim Cost")
        self._raw_tabs = raw_tabs
        outer.addWidget(raw_tabs, 1)
        self._raw_page = page

    def _export_appendix_c_files(self) -> None:
        """Appendix C "Files and Reports": the data files Raptor 7 writes
        beside its reports -- Key Parameters, All System Failure Times, All
        System Down Times, Ending Simulation Times and Availability Plot Data.
        The first four are built by `raptor.core.engine.data_files` from this
        run's own `RunReportData` and match Raptor 7's files byte for byte (CRLF);
        the CLI and API use the same functions."""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout as _VBox, QCheckBox, QLabel as _Label,
            QPushButton as _Btn, QHBoxLayout as _HBox,
        )
        if not self._report_data:
            msgbox.information(
                self, "Export Data Files",
                "Run the simulation first — these files are built "
                "from this run's own per-trial results.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Export Data Files")
        lay = _VBox(dlg)
        note = _Label(
            "One file per dataset, from this run's own results.")
        note.setWordWrap(True)
        lay.addWidget(note)
        cb_key = QCheckBox("Key Parameters (Ao, Do, R, RCond, MTBDE, "
                           "MDT, MTBM, MMT — one row per trial)")
        cb_fail = QCheckBox("All System Failure Times (time to each system "
                            "failure, from when statistics start)")
        cb_down = QCheckBox("All System Down Times (time to each system "
                            "recovery, from when statistics start)")
        cb_end = QCheckBox("Ending Simulation Times (one value per "
                           "trial)")
        for cb in (cb_key, cb_fail, cb_down, cb_end):
            cb.setChecked(True)
        cb_ao = QCheckBox("Availability Plot Data (time-truncated "
                          "runs only)")
        cb_ao.setChecked(bool(getattr(self, "_ao_lines", None)))
        cb_ao.setEnabled(bool(getattr(self, "_ao_lines", None)))
        if not cb_ao.isEnabled():
            cb_ao.setToolTip(
                "Not available for this run — Availability Plot Data "
                "only generates for time-truncated simulations")
        for cb in (cb_key, cb_fail, cb_down, cb_end, cb_ao):
            lay.addWidget(cb)

        buttons = _HBox()
        cancel = _Btn("Cancel")
        export = _Btn("Export…")
        cancel.clicked.connect(dlg.reject)
        export.clicked.connect(dlg.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(export)
        lay.addLayout(buttons)

        # Non-modal (`.show()`, not `.exec()`): `.exec()` blocks with
        # nothing able to dismiss it under headless/offscreen test
        # automation. `self._appendix_c_dialog` keeps a reference so the
        # dialog isn't garbage-collected before `accepted` fires.
        self._appendix_c_dialog = dlg
        self._appendix_c_checks = (cb_key, cb_fail, cb_down, cb_end, cb_ao)
        dlg.accepted.connect(self._do_export_appendix_c_files)
        dlg.show()

    def _do_export_appendix_c_files(self) -> None:
        from raptor.gui.ui import filedialogs as fdg
        directory = fdg.getExistingDirectory(
            self, "Export Data Files To…")
        if not directory:
            return
        import os
        from raptor.core.engine import data_files
        cb_key, cb_fail, cb_down, cb_end, cb_ao = self._appendix_c_checks
        wanted = [("key_parameters", cb_key), ("failure_times", cb_fail),
                  ("down_times", cb_down), ("end_times", cb_end)]
        written = []
        try:
            for kind, cb in wanted:
                if not cb.isChecked():
                    continue
                name = f"rpt_{kind}.txt"
                lines = data_files.DATA_FILES[kind](self._report_data)
                with open(os.path.join(directory, name), "w",
                          encoding="latin-1", newline="") as f:
                    f.write(data_files.legacy_text(lines))
                written.append(name)
            if cb_ao.isChecked() and getattr(self, "_ao_lines", None):
                name = "rpt_availability_plot.txt"
                with open(os.path.join(directory, name), "w",
                          encoding="utf-8", newline="") as f:
                    f.write("\r\n".join(self._ao_lines) + "\r\n")
                written.append(name)
        except OSError as e:
            msgbox.critical(self, "Export Failed",
                                 f"Could not write to {directory}:\n{e}")
            return
        if written:
            sb = getattr(self.window(), "statusBar", None)
            if sb is not None:
                sb().showMessage(
                    f"Exported {', '.join(written)} to {directory}")

    def _save_raw_report(self) -> None:
        idx = self._raw_tabs.currentIndex()
        if idx < 0:
            return
        name = self._raw_tabs.tabText(idx)
        view = self._raw_tabs.widget(idx)
        source = getattr(self, "_raw_sources", {}).get(name)
        text = cast(QPlainTextEdit, view).toPlainText() if view is not None else ""
        if source is None and not text.strip():
            return
        if source is not None and len(source) == 0:
            return
        default = name.replace(" ", "_").lower() + ".txt"
        path, _f = fdg.getSaveFileName(
            self, "Save Raw Report", default, "Text files (*.txt)")
        if not path:
            return
        try:
            if source is not None and hasattr(source, "export_to"):
                if hasattr(source, "path") and not os.path.exists(source.path):
                    # A new run closed this batch's temp stream while its
                    # results were still on screen. Say so instead of an
                    # "OSError errno 2" dialog; Export Tables still has the
                    # constructed tables.
                    msgbox.warning(
                        self, "Export Failed",
                        "The full report for these results is no longer "
                        "available (a new run has started). The on-screen "
                        "preview is kept.")
                    return
                source.export_to(path)
            elif source is not None:
                # Write in chunks; a list-backed report may still be huge
                # and joining it into one string is the bug this avoids.
                # Match `"\n".join(lines)` exactly (no trailing newline).
                with open(path, "w", encoding="utf-8") as f:
                    for i, line in enumerate(source):
                        if i:
                            f.write("\n")
                        f.write(line)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
        except OSError as e:
            msgbox.critical(self, "Export Failed",
                                 f"Could not write {path}:\n{e}")

"""Empirical Data dialog — Raptor 7 "Empirical Data Entry".

Enter data points one at a time (Accept), load a single-column .txt
file, delete points, and OK to return the sorted table. The legacy app
sorts ascending and computes the mean; the engine's
`RaptorStream.empirical` interpolates between the ordered points."""

from __future__ import annotations
from raptor.gui.ui.modality import exec_dialog

from raptor.gui.ui import msgbox

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QLineEdit,
    QPushButton, QLabel,
)

from raptor.gui.ui import filedialogs as fdg


def open_empirical_dialog(parent, values: list[float],
                          units: str = "") -> list[float] | None:
    """Open the Empirical Data editor with `values` pre-loaded.
    Returns the sorted data list on OK, or None on cancel."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Empirical Data")
    v = QVBoxLayout(dlg)

    head = QLabel(
        "Failure/repair sample values for this element's empirical "
        "distribution."
        + (f" Units: {units}" if units else ""))
    head.setObjectName("SectionNote")
    head.setWordWrap(True)
    v.addWidget(head)

    items = QListWidget()
    items.setMinimumHeight(200)
    for val in sorted(values):
        items.addItem(f"{val:.6g}")
    v.addWidget(items)

    entry_row = QHBoxLayout()
    entry = QLineEdit()
    entry.setPlaceholderText("Enter a data point…")
    entry.setToolTip("Type a value and press Accept (or Enter)")
    accept_b = QPushButton("Accept")
    load_b = QPushButton("Load from file…")
    del_b = QPushButton("Delete selected")
    entry_row.addWidget(entry, 1)
    entry_row.addWidget(accept_b)
    v.addLayout(entry_row)

    file_row = QHBoxLayout()
    file_row.addWidget(load_b)
    file_row.addWidget(del_b)
    file_row.addStretch(1)
    stats_lbl = QLabel("")
    stats_lbl.setObjectName("SectionNote")
    file_row.addWidget(stats_lbl)
    v.addLayout(file_row)

    state: dict[str, list[float]] = {"values": sorted(values)}
    widgets: dict = {}

    def refresh_stats():
        vals = state["values"]
        # No points, no distribution: OK waits for one (Raptor 7 treats
        # OK on an empty table as Cancel).
        if "ok_b" in widgets:
            widgets["ok_b"].setEnabled(bool(vals))
            widgets["ok_b"].setToolTip(
                "" if vals else "Add at least one data point")
        if not vals:
            stats_lbl.setText("0 data points")
            return
        mean = sum(vals) / len(vals)
        sd = (sum((x - mean) ** 2 for x in vals) / len(vals)) ** 0.5
        stats_lbl.setText(
            f"{len(vals)} points · Mean {mean:.4g}"
            f" · Std Dev {sd:.4g}")
        _suffix = f" {units}" if units else ""

    def _resort_list():
        items.clear()
        vals: list = sorted(state["values"])
        for val in vals:
            items.addItem(f"{val:.6g}")
        refresh_stats()

    def accept_point():
        text = entry.text().strip().rstrip(",")
        try:
            val = float(text)
        except ValueError:
            msgbox.warning(dlg, "Empirical Data",
                                f"'{text}' is not a number")
            return
        if not 0.0 <= val < 999999999.999999:
            # Raptor 7's own range for a data point.
            msgbox.warning(dlg, "Empirical Data",
                           "A data point must be between 0 and "
                           "999,999,999.999999.")
            return
        assert isinstance(state["values"], list)
        state["values"].append(val)
        entry.clear()
        _resort_list()

    def delete_selected():
        row = items.currentRow()
        if row < 0:
            return
        # By position: the list shows the sorted values, so row i is
        # values[i]. Matching the shown text (6 significant figures)
        # against the stored value missed any point it rounds, and the
        # delete silently did nothing.
        state["values"] = sorted(state["values"])
        del state["values"][row]
        _resort_list()

    def load_file():
        path, _f = fdg.getOpenFileName(
            dlg, "Select Empirical Data File Name", "",
            "Text files (*.txt);;All files (*)")
        if not path or not path[0]:
            return
        vals2: list[float] = []
        try:
            with open(path[0], encoding="utf-8") as f:
                for line in f:
                    line = line.strip().rstrip(",")
                    if not line:
                        continue
                    vals2.append(float(line))
        except OSError as e:
            msgbox.warning(dlg, "Empirical Data",
                                f"Could not read file:\n{e}")
            return
        except ValueError as e:
            msgbox.warning(
                dlg, "Empirical Data",
                f"File must be a single column of numbers:\n{e}")
            return
        bad = [x for x in vals2 if not 0.0 <= x < 999999999.999999]
        if bad:
            msgbox.warning(dlg, "Empirical Data",
                           f"{bad[0]:g} is outside 0 to 999,999,999.999999; "
                           f"nothing was loaded.")
            return
        # legacy max 999 points per distribution
        if len(vals2) > 999:
            msgbox.warning(dlg, "Empirical Data",
                                "Maximum 999 data points")
            return
        state["values"] = sorted(state["values"] + vals2)
        _resort_list()

    accept_b.clicked.connect(accept_point)
    entry.returnPressed.connect(accept_point)
    del_b.clicked.connect(delete_selected)
    load_b.clicked.connect(load_file)

    buttons = QHBoxLayout()
    ok_b = QPushButton("OK")
    widgets["ok_b"] = ok_b
    cancel_b = QPushButton("Cancel")
    ok_b.clicked.connect(dlg.accept)
    cancel_b.clicked.connect(dlg.reject)
    buttons.addStretch(1)
    buttons.addWidget(cancel_b)
    buttons.addWidget(ok_b)
    v.addLayout(buttons)

    _resort_list()
    dlg.resize(420, 420)
    accepted = exec_dialog(dlg) == QDialog.DialogCode.Accepted
    if not accepted:
        return None
    return sorted(state["values"])

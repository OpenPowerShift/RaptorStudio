"""Dialog builders extracted from main_window.

Each function takes the MainWindow instance (`win`) and builds/opens
one dialog. MainWindow keeps thin delegating methods so existing
wiring and tests keep working."""
from __future__ import annotations
from raptor.gui.ui.modality import exec_dialog

from raptor.gui.ui.qtcompat import req

from raptor.gui.ui import msgbox

import json
from typing import Any
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox,
    QGroupBox, QWidget, QListWidget, QTabWidget, QMessageBox, QScrollArea, QFrame,
)
from raptor.gui.ui import filedialogs as fdg
from raptor.gui.ui import settings
from raptor.gui.ui.diagram_commands import apply_row_edits
from raptor.gui.ui.main_window import _TERMINATION_CHOICES


def question_box(parent, title: str, text: str, *,
                 yes_text: str = "Yes", no_text: str = "No",
                 default_yes: bool = False) -> bool:
    """The standard Yes/No confirmation.

    - The buttons are labelled with the action each affirms ("Discard
      changes" / "Keep editing"), never a bare Yes/No -- the clicked
      button states its own consequence.
    - Each button gets an explicit standard icon: with the app-wide
      stylesheet active, Qt's QStyleSheetStyle returns null icons for
      QMessageBox's own standard buttons, so the Yes checkmark would
      otherwise be blank in the light theme.
    - Callers import this function from this module at call time, so a
      headless test can stub one thing
      (`monkeypatch.setattr("raptor.gui.ui.dialogs.question_box", ...)`)
      and no real modal can hang the run. Do not call
      `QMessageBox.question` or `.exec()` on a fresh box elsewhere.

    Returns True only when the user took the affirming action."""
    from PySide6.QtWidgets import QStyle
    box = QMessageBox(QMessageBox.Icon.Question, title, text,
                      QMessageBox.StandardButton.NoButton, parent)
    style = box.style()
    yes = box.addButton(yes_text, QMessageBox.ButtonRole.YesRole)
    yes.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogYesButton))
    no = box.addButton(no_text, QMessageBox.ButtonRole.NoRole)
    no.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogNoButton))
    box.setDefaultButton(no if not default_yes else yes)
    box.exec()
    return box.clickedButton() is yes




def _add_help_button(dlg, win, buttons_lay):
    """'?' button: opens the reference manual at the section covering
    this dialog (same anchors as F1 context help)."""
    def open_help():
        win._open_context_help()
    b = QPushButton("Help")
    b.clicked.connect(lambda: (dlg.setFocus(), open_help()))
    buttons_lay.addWidget(b)
    return b


def export_block_library(win):
    if win._rbd is None:
        return
    from raptor.gui.ui.block_library import export_library
    rows = win._rbd.sections["BLOCK_DATA"].rows
    if not any(str(r.get("ID", "0")).strip() not in ("0", "") for r in rows):
        msgbox.information(win, "Export Library", "There are no blocks to export.")
        return
    path, _ = fdg.getSaveFileName(
        win, "Save Block Library", "blocks.rbl",
        "Block libraries (*.rbl *.json)")
    if not path:
        return
    n = export_library(path, rows, Path(win._current_file).name if win._current_file
                       else "Unnamed RBD")
    win._statusbar.showMessage(
        f"Library saved: {Path(path).name} ({n} templates)", 6000)



def import_block_library(win):
    if win._rbd is None:
        return
    path, _ = fdg.getOpenFileName(
        win, "Import Block Library", "",
        "Block libraries (*.rbl *.json)")
    if not path:
        return
    from raptor.gui.ui.block_library import load_library
    try:
        templates = load_library(path)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        msgbox.warning(win, "Import Library",
                            f"Could not read library:\n{e}")
        return
    if not templates:
        msgbox.information(win, "Import Library",
                                "That library has no templates.")
        return
    # Kept for the session, like Raptor 7's loaded library: the Block Defaults
    # dialog offers its blocks as "Block Template" starting points.
    win._block_library = templates

    dlg = QDialog(win)
    dlg.setWindowTitle("Import Block Library")
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(f"{len(templates)} template(s) in "
                         f"{Path(path).name}"))
    items = QListWidget()
    for t in templates:
        row = t.get("row", {})
        items.addItem(f"{t.get('name', '?')}   "
                    f"(FDist {row.get('FDist', '?')})")
    items.setCurrentRow(0)
    lay.addWidget(items, 1)
    buttons = QHBoxLayout()
    add_btn = QPushButton("Add Block")
    upd_btn = QPushButton("Update Matching")
    close_btn = QPushButton("Close")
    close_btn.clicked.connect(dlg.reject)

    def selected_template():
        i = items.currentRow()
        return templates[i] if 0 <= i < len(templates) else None

    def do_add():
        t = selected_template()
        if t is None:
            return
        v = win._diagram_dock.view
        centre = v.mapToScene(v.viewport().rect().center())
        bid = v.add_block_from_template(
            t["row"], at_model=v.scene_to_model(centre))
        if bid:
            win._mark_dirty()
            win._statusbar.showMessage(
                f"Added {t.get('name')} as block {bid}", 6000)

    def do_update():
        t = selected_template()
        if t is None:
            return
        n = win._diagram_dock.view.update_blocks_from_library([t])
        win._mark_dirty()
        win._statusbar.showMessage(
            f"Updated {n} same-named block(s) from "
            f"{t.get('name')}", 6000)

    add_btn.clicked.connect(do_add)
    upd_btn.clicked.connect(do_update)
    buttons.addStretch(1)
    buttons.addWidget(add_btn)
    buttons.addWidget(upd_btn)
    buttons.addWidget(close_btn)
    lay.addLayout(buttons)
    dlg.resize(360, 300)
    exec_dialog(dlg)



def show_spof_dialog(win):
    """Raptor 7 single-point-failure listing + Export."""
    if win._rbd is None:
        return
    from raptor.core.engine.spf_fev import single_point_failures
    _v = win._diagram_dock.view
    try:
        rows = single_point_failures(win._rbd)
    except Exception as e:  # structural surprises shouldn't crash UI
        rows = []
        msgbox.warning(win, "Single-Point Failures",
                            f"Analysis failed: {e}")
        return
    dlg = QDialog(win)
    dlg.setWindowTitle("Single Point Failure File")
    lay = QVBoxLayout(dlg)
    head = QLabel(
        f"{len(rows)} failure(s) take the system down on their own "
        f"(found by failing each component in Failure Effects View, so "
        f"dependencies and standby count).")
    head.setWordWrap(True)
    lay.addWidget(head)
    items = QListWidget()
    for d in rows:
        where = f"{d['phase']}: " if d.get("phase") else ""
        items.addItem(f"{where}{d['name']}  ({d['kind']} #{d['id']})")
    lay.addWidget(items, 1)
    buttons = QHBoxLayout()
    export_btn = QPushButton("Export…")
    close_btn = QPushButton("Close")
    close_btn.clicked.connect(dlg.reject)

    def do_export():
        path, _ = fdg.getSaveFileName(
            win, "Export Single Point Failure File",
            "single_point_failures.txt", "Text files (*.txt)")
        if not path:
            return
        from raptor.core.engine.spf import spf_date_string
        from raptor.core.engine.spf_fev import build_spf_listing
        from raptor.gui import dev_settings
        text = build_spf_listing(win._rbd, Path(path).name, spf_date_string(),
                                 password=dev_settings.current_options().show_criticality)
        with open(path, "wb") as f:
            f.write(text.encode("latin-1"))
        win._statusbar.showMessage(
            f"Saved: {Path(path).name}", 6000)
    export_btn.clicked.connect(do_export)
    buttons.addStretch(1)
    buttons.addWidget(export_btn)
    buttons.addWidget(close_btn)
    lay.addLayout(buttons)
    dlg.resize(380, 320)
    exec_dialog(dlg)



def _pref_row(form, label, widget, why, scope, reset=None, builtins=()):
    """One preference row: label, control, and a plain-English "why".

    The old dialog showed bare labels with the explanation hidden in a
    tooltip -- but a preference is exactly the thing a user opens this
    dialog to READ, not to hover. The explanation is now visible, and a
    per-row Reset puts a single changed value back without a hunt.
    """
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    # Every control is named for a screen reader and carries its explanation as a tooltip, whatever the caller set.
    name = label.rstrip(":").strip() or (widget.text() if hasattr(widget, "text") else "")
    for target in (widget, widget.lineEdit() if isinstance(widget, QComboBox) and widget.isEditable() else None):
        if target is not None:
            if not target.accessibleName():
                target.setAccessibleName(name)
            if not target.toolTip():
                target.setToolTip(why)
    if isinstance(widget, (QLineEdit, QComboBox)):
        # text and choice fields take the room (a 90 px text box cannot show a file name or a trigger name)
        widget.setMinimumWidth(300)
        lay.addWidget(widget, 1)
    else:
        lay.addWidget(widget)
        lay.addStretch(1)
    if reset is not None:
        btn = QPushButton("Reset")
        btn.setToolTip(f"Restore the default ({', '.join(builtins) or 'default'})")
        btn.setFixedWidth(56)
        btn.clicked.connect(lambda _=False, w=widget, r=reset: r(w))
        lay.addWidget(btn)
    form.addRow(label, box)
    why_lbl = QLabel(why)
    why_lbl.setObjectName("SectionNote")
    why_lbl.setWordWrap(True)
    form.addRow("", why_lbl)
    return widget


class _SectionPages(QWidget):
    """Preferences as sections on ONE scrolling page, with a list on the left that jumps to them.

    Tabs hid most settings behind a click, which made the filter box a trap: a match could be on a tab you were
    not looking at. Here every section is on the page, the list jumps to a section, and the filter hides
    non-matching rows, empty sections and their list entries in one view. Same small API as the tab widget it
    replaced (`addTab`, `count`, `widget`, `tabText`).
    """

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self._nav = QListWidget()
        self._nav.setObjectName("PreferencesSections")
        self._nav.setAccessibleName("Preference sections")
        self._nav.setFixedWidth(150)
        self._nav.currentRowChanged.connect(self._jump)
        # clicking the entry that is already selected must still jump (the row did not change)
        self._nav.itemClicked.connect(lambda item: self._jump(self._nav.row(item)))
        row.addWidget(self._nav)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self._column = QVBoxLayout(body)
        self._column.setSpacing(4)
        # Room under the last section, so every section (the last one too) can be scrolled up to the top.
        self._tail = QWidget()
        self._column.addWidget(self._tail)
        self._scroll.setWidget(body)
        row.addWidget(self._scroll, 1)
        self._titles: list[tuple[QLabel, QWidget]] = []

    def addTab(self, page: QWidget, title: str) -> int:
        head = QLabel(title)
        head.setObjectName("PreferencesSectionTitle")
        font = head.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.2)
        head.setFont(font)
        head.setContentsMargins(0, 12, 0, 2)
        at = self._column.count() - 1                     # before the tail spacer
        self._column.insertWidget(at, head)
        self._column.insertWidget(at + 1, page)
        self._titles.append((head, page))
        self._nav.addItem(title)
        if self._nav.currentRow() < 0:
            self._nav.setCurrentRow(0)
        return len(self._titles) - 1

    def resizeEvent(self, event) -> None:               # noqa: N802
        super().resizeEvent(event)
        self._tail.setFixedHeight(max(0, self._scroll.viewport().height() - 120))

    def count(self) -> int:
        return len(self._titles)

    def widget(self, i: int) -> QWidget:
        return self._titles[i][1]

    def tabText(self, i: int) -> str:
        return self._titles[i][0].text()

    def set_section_visible(self, i: int, on: bool) -> None:
        head, page = self._titles[i]
        head.setVisible(on)
        page.setVisible(on)
        self._nav.item(i).setHidden(not on)

    def _jump(self, i: int) -> None:
        if 0 <= i < len(self._titles):
            head = self._titles[i][0]
            if head.isVisibleTo(self):
                self._column.activate()                 # the layout may not have caught up with a filter or resize
                self._scroll.verticalScrollBar().setValue(head.y())


def _filter_tabs(tabs, filter_box: QLineEdit) -> None:
    """Hide the rows that do not match the filter text.

    A preference list is a LIST: users arrive looking for one thing
    ("where do I turn off auto-save"), and 12 rows across 3 tabs is
    small enough that scrolling is a worse answer than typing. Matching
    is against the row's visible label AND its explanation, so typing
    "autosave" finds the row whether you remember the label or the
    reason. Hidden rows hide their widget AND their explanation together,
    which is why both are tracked per page.
    """
    def parts(form, i):
        """(label widget or None, field widget or None) of form row `i`; a row added with an empty label has no
        label item at all, so each side is looked up on its own."""
        lab = form.itemAt(i, form.ItemRole.LabelRole)
        field = form.itemAt(i, form.ItemRole.FieldRole)
        return (lab.widget() if lab is not None else None), (field.widget() if field is not None else None)

    def is_note(label_w, field_w) -> bool:
        return label_w is None and isinstance(field_w, QLabel) and field_w.objectName() == "SectionNote"

    def apply(text: str) -> None:
        needle = text.strip().lower()
        for page in range(tabs.count()):
            w = tabs.widget(page)
            if w is None:
                continue
            any_match = not needle
            for form in w.findChildren(QFormLayout):
                # Group the rows into settings: a setting row, then the explanation row(s) that follow it.
                groups: list[list[int]] = []
                for i in range(form.rowCount()):
                    label_w, field_w = parts(form, i)
                    if label_w is None and field_w is None:
                        continue
                    if is_note(label_w, field_w) and groups:
                        groups[-1].append(i)
                    else:
                        groups.append([i])
                for rows in groups:
                    pieces = []
                    for i in rows:
                        label_w, field_w = parts(form, i)
                        pieces.append((label_w.text() if label_w is not None else "") + " "
                                      + (_widget_text(field_w) if field_w is not None else ""))
                    show = (not needle) or (needle in " ".join(pieces).lower())
                    for i in rows:
                        for widget in parts(form, i):
                            if widget is not None:
                                widget.setVisible(show)
                    any_match = any_match or show
            # A section with nothing left to show leaves the page and the list too.
            if hasattr(tabs, "set_section_visible"):
                tabs.set_section_visible(page, any_match)
    filter_box.textChanged.connect(apply)


def _row_widgets(form, index: int) -> tuple[Any, Any]:
    """(label widget, field widget) for one QFormLayout row.

    Returns Nones rather than raising for a row that has one without the
    other -- which is exactly the shape the explanation rows use (an
    empty label with a note as the field), so treating that as a special
    case is the whole point."""
    lab = form.itemAt(index, form.ItemRole.LabelRole)
    field = form.itemAt(index, form.ItemRole.FieldRole)
    if lab is None or field is None:
        return None, None
    return lab.widget(), field.widget()


def _widget_text(widget) -> str:
    """Whatever text a filter should match on for a given control."""
    for attr in ("text", "title", "toolTip", "placeholderText"):
        fn = getattr(widget, attr, None)
        if callable(fn):
            try:
                v = fn()
            except TypeError:
                continue
            if isinstance(v, str) and v:
                return v
    return ""


def _build_developer_rows(form) -> dict:
    """One row per developer option (`core/dev_options.py`); returns
    {key: reader()} for `_store_developer_rows`."""
    from raptor.core import dev_options as dev
    from raptor.gui import dev_settings
    cur = dev_settings.current_options().to_dict()
    readers: dict = {}
    w: Any
    for f in dev.DEV_FEATURES:
        if f.kind == "bool":
            w = QCheckBox(f.label)
            w.setChecked(bool(cur[f.key]))
            readers[f.key] = w.isChecked
            _pref_row(form, "", w, f.description, "app",
                      reset=lambda ww: ww.setChecked(False),
                      builtins=("off",))
        elif f.kind == "int":
            w = QSpinBox()
            w.setRange(0, 1_000_000)
            w.setValue(int(cur[f.key]))
            readers[f.key] = w.value
            _pref_row(form, f.label + ":", w, f.description, "app",
                      reset=lambda ww: ww.setValue(0), builtins=("0",))
        elif f.kind == "float":
            w = QDoubleSpinBox()
            w.setRange(0.0, 1e9)
            w.setDecimals(3)
            w.setValue(float(cur[f.key]))
            readers[f.key] = w.value
            _pref_row(form, f.label + ":", w, f.description, "app",
                      reset=lambda ww: ww.setValue(0.0), builtins=("0",))
        elif f.kind == "set":
            box = QWidget()
            lay = QHBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            checks = {}
            for c in dev.EVENT_CATEGORIES:
                cb = QCheckBox(c.capitalize())
                cb.setChecked(c in cur[f.key])
                checks[c] = cb
                lay.addWidget(cb)
            readers[f.key] = lambda checks=checks: [
                c for c, cb in checks.items() if cb.isChecked()]
            _pref_row(form, f.label + ":", box, f.description, "app")
        else:
            w = QLineEdit(str(cur[f.key]))
            readers[f.key] = w.text
            _pref_row(form, f.label + ":", w, f.description, "app",
                      reset=lambda ww: ww.setText(""), builtins=("empty",))
    return readers


def _store_developer_rows(readers: dict) -> None:
    from raptor.gui import dev_settings
    dev_settings.store({k: r() for k, r in readers.items()})


def show_preferences(win):
    """Preferences, in sections by SCOPE on one scrolling page (a list on the left jumps to a section).

    The old dialog was one flat ten-row form mixing three different
    lifetimes -- an application default, a per-model setting, and two
    per-run engine intervals -- so "where does this save?" had no answer
    visible in the UI. Now the tab a setting is on IS the answer:

    * **New models**  -- what a freshly created model starts with
      (application scope; seeds every new file)
    * **Working**     -- how the app treats files and runs, now
    * **This model**  -- the one setting that belongs to the open model
    * **Advanced**    -- how distribution numbers are typed

    The per-run sampling intervals are GONE from here on purpose: they
    describe what a run records, so they live with the run experiment
    (the Run panel / `core/run_experiment.py`), not in global settings
    where two open models fought over them.
    """
    from raptor.gui.ui import settings as _settings

    prefs = win._prefs()
    dlg = QDialog(win)
    dlg.setWindowTitle("Preferences")
    dlg.setObjectName("PreferencesDialog")
    outer = QVBoxLayout(dlg)

    filter_box = QLineEdit()
    filter_box.setPlaceholderText("Filter settings…")
    filter_box.setObjectName("PreferencesFilter")
    filter_box.setAccessibleName("Filter settings")
    filter_box.setToolTip(
        "Show only the settings whose name or description matches")
    outer.addWidget(filter_box)

    tabs = _SectionPages()
    tabs.setObjectName("PreferencesSections")
    outer.addWidget(tabs, 1)
    dlg.resize(880, 640)

    # ---- New models -------------------------------------------------
    new_page = QWidget()
    new_form = QFormLayout(new_page)
    new_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    units = QComboBox()
    units.setEditable(True)
    for u in ("hours", "days", "weeks", "cycles"):
        units.addItem(u)
    units.setCurrentText(prefs["units"])
    units.setToolTip("Time unit a newly created model starts in")
    _pref_row(
        new_form, "Time units for new models:", units,
        "The native time unit a NEW model starts with. Changing this "
        "never alters an existing model — use This model ▸ display unit, "
        "or the model's own Project Settings, for those.",
        "new model",
        reset=lambda w: w.setCurrentText("hours"), builtins=("hours",))
    from raptor.core import run_experiment as _rx
    prof = QComboBox()
    for key, spec in _rx.PROFILES.items():
        prof.addItem(spec["label"], key)
    prof.setCurrentIndex(max(0, prof.findData(
        _settings.get("experiments/last_used", _rx.DEFAULT_PROFILE))))
    prof.setToolTip(
        "Which starting point a new model's experiment is seeded from")
    _pref_row(
        new_form, "Starting experiment:", prof,
        "Seeds a new model's run settings: trial count, run length, "
        "analyses and recording. You can change any of it afterwards "
        "in the Run panel.",
        "new model",
        reset=lambda w: w.setCurrentIndex(
            max(0, w.findData(_rx.DEFAULT_PROFILE))),
        builtins=(_rx.PROFILES[_rx.DEFAULT_PROFILE]["label"],))
    tabs.addTab(new_page, "New models")

    # ---- Working ----------------------------------------------------
    work_page = QWidget()
    work_form = QFormLayout(work_page)
    work_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    autosave = QCheckBox("Auto-save the current file periodically")
    autosave.setChecked(bool(prefs["autosave"]))
    _pref_row(
        work_form, "Auto-save:", autosave,
        "Writes the open file to a SEPARATE recovery copy on a timer, "
        "never over your own file. A crash-recovery net, not a backup.",
        "app")
    mins = QSpinBox()
    mins.setRange(1, 120)
    mins.setValue(int(prefs["autosave_min"]))
    mins.setEnabled(autosave.isChecked())
    autosave.toggled.connect(mins.setEnabled)
    mins.setToolTip("How often the recovery copy is written")
    _pref_row(
        work_form, "Auto-save every:", mins,
        "Minutes between recovery copies. Only active while auto-save "
        "is on.", "app",
        reset=lambda w: w.setValue(5), builtins=("5",))

    save_before_run = QComboBox()
    save_before_run.addItem("Don't save automatically", "never")
    save_before_run.addItem("Ask before running", "ask")
    save_before_run.addItem("Always save silently before running", "always")
    save_before_run.setCurrentIndex(max(0, save_before_run.findData(
        prefs.get("save_before_run", "ask"))))
    save_before_run.setToolTip(
        "What to do with unsaved changes when you press Run")
    _pref_row(
        work_form, "Unsaved changes before running:", save_before_run,
        "Results otherwise silently reflect a different model than what "
        "is on disk — a real reproducibility hazard, so 'ask' is the "
        "default.", "app",
        reset=lambda w: w.setCurrentIndex(
            max(0, w.findData("ask"))), builtins=("Ask before running",))

    open_in = QComboBox()
    open_in.addItem("A new window", "new")
    open_in.addItem("This window (closing the open model)", "this")
    open_in.setCurrentIndex(max(0, open_in.findData(
        _settings.get("files/open_in", "new"))))
    open_in.setToolTip("Where Open, New and the examples put a model while "
                       "this window already holds one")
    _pref_row(
        work_form, "Open another model in:", open_in,
        "A new window keeps the open model as it is, so two models can be "
        "compared side by side. A window that holds nothing, or only an "
        "untouched new model, is always reused.", "app",
        reset=lambda w: w.setCurrentIndex(max(0, w.findData("new"))),
        builtins=("A new window",))

    from raptor.gui import cores as _cores
    workers = QSpinBox()
    workers.setRange(0, _cores.available())
    workers.setSpecialValueText(f"Automatic ({_cores.automatic()})")
    workers.setSuffix(f" of {_cores.available()}")
    workers.setValue(_settings.get_int(_cores.KEY, 0))
    # One editor: the Run panel. This is a read-only readout so a
    # Preferences save cannot silently change the next run's cores.
    workers.setReadOnly(True)
    workers.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    workers.setToolTip(
        "Cores a full-speed run uses — change this in the Run panel")
    _pref_row(
        work_form, "Cores for full-speed runs:", workers,
        "Also set from the Cores box beside Run on the Simulate tab. "
        "Automatic uses every core. Any number from 2 up gives the same "
        "results; one core gives Raptor 7's exact sequence, statistically "
        "the same answer but not bit-identical. "
        "An animated run always uses one core.", "app",
        reset=lambda w: w.setValue(0), builtins=("Automatic",))

    animate = QCheckBox("Animate new runs by default")
    animate.setChecked(_settings.get_bool("execution/default_animate", False))
    animate.setToolTip("The Animate toggle's state on a new launch")
    _pref_row(
        work_form, "Animate by default:", animate,
        "Whether a run animates when you press Run: one core, every "
        "event shown. Off means full speed on the cores above; the speed "
        "dial only applies while animating.", "app")
    tabs.addTab(work_page, "Working")

    # ---- This model -------------------------------------------------
    model_page = QWidget()
    model_form = QFormLayout(model_page)
    model_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    from raptor.gui.ui.units_conv import DISPLAY_UNIT_CHOICES
    from raptor.gui.ui.units_conv import parse_display_unit
    display_unit_combo = QComboBox()
    display_unit_combo.setEditable(True)
    for choice in DISPLAY_UNIT_CHOICES:
        display_unit_combo.addItem(choice.title() if choice else "(native units)", choice)
    current_du = getattr(win._rbd, "display_unit", None) if win._rbd else None
    found = display_unit_combo.findData(current_du)
    if found >= 0:
        display_unit_combo.setCurrentIndex(found)
    else:
        display_unit_combo.setCurrentText(str(current_du))      # the user's own unit name
    has_model = win._rbd is not None
    display_unit_combo.setEnabled(has_model)
    display_unit_combo.setToolTip(
        "Pick a unit to convert to, or type any unit name to relabel without converting")
    _pref_row(
        model_form, "Display unit:", display_unit_combo,
        "View and enter time values in a different unit than this model's "
        "own native one — years instead of hours, say. Pick a listed unit "
        "(minutes to years) to convert, or type any other name (miles, "
        "flight hours...) to relabel the unit without converting. It never "
        "changes a stored number or the model's native unit, only how "
        "fields show and accept values."
        + ("" if has_model else
           "  (No model is open, so this is unavailable.)"),
        "model")
    model_note = QLabel(
        "This setting is saved in the MODEL FILE (.rbds), not in "
        "application settings — it travels with the model and is per-model.")
    model_note.setObjectName("SectionNote")
    model_note.setWordWrap(True)
    model_form.addRow("", model_note)
    tabs.addTab(model_page, "This model")

    # ---- Advanced ---------------------------------------------------
    adv_page = QWidget()
    adv_form = QFormLayout(adv_page)
    adv_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    exp_combo = QComboBox()
    exp_combo.addItem("Mean", "mean")
    exp_combo.addItem("Failure rate (1/mean)", "rate")
    exp_combo.setCurrentIndex(max(0,
        exp_combo.findData(prefs.get("exp_mode", "mean"))))
    exp_combo.setToolTip("How exponential distribution numbers are typed")
    _pref_row(
        adv_form, "Exponential entered as:", exp_combo,
        "How you TYPE exponential distributions. Values are always stored "
        "as a mean, whichever you pick here — this only changes the entry "
        "form.", "app",
        reset=lambda w: w.setCurrentIndex(0), builtins=("Mean",))
    ln_combo = QComboBox()
    ln_combo.addItem("Mean + StDev", "mean_sd")
    ln_combo.addItem("Mu + Sigma (normal space)", "mu_sigma")
    ln_combo.setCurrentIndex(max(0,
        ln_combo.findData(prefs.get("ln_mode", "mean_sd"))))
    ln_combo.setToolTip("How lognormal distribution numbers are typed")
    _pref_row(
        adv_form, "Lognormal entered as:", ln_combo,
        "How you TYPE lognormal distributions. Always stored canonically "
        "as mean + standard deviation.", "app",
        reset=lambda w: w.setCurrentIndex(0), builtins=("Mean + StDev",))

    digits = QSpinBox()
    digits.setRange(0, 6)
    digits.setValue(_settings.get_int("appearance/weak_link_digits", 3))
    digits.setSuffix(" digits")
    digits.setToolTip(
        "Decimal places on the Weak Link value printed under each "
        "element. Raptor 7's default is 3.")
    _pref_row(
        adv_form, "Weak Link value digits:", digits,
        "How many digits after the decimal point are shown under blocks, "
        "nodes, events and hierarchies when Weak Links is on. Three "
        "matches Raptor 7.", "app",
        reset=lambda w: w.setValue(3), builtins=("3 digits",))
    tabs.addTab(adv_page, "Advanced")

    # ---- Developer --------------------------------------------------
    dev_page = QWidget()
    dev_form = QFormLayout(dev_page)
    dev_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    dev_note = QLabel(
        "Developer options: extras that are off by default. They change "
        "which files are written or what a report contains, never the "
        "simulation itself.")
    dev_note.setObjectName("SectionNote")
    dev_note.setWordWrap(True)
    dev_form.addRow("", dev_note)
    dev_widgets = _build_developer_rows(dev_form)
    tabs.addTab(dev_page, "Developer")

    _filter_tabs(tabs, filter_box)

    buttons = QHBoxLayout()
    ok = QPushButton("OK")
    cancel = QPushButton("Cancel")
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    _add_help_button(dlg, win, buttons)
    buttons.addStretch(1)
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    outer.addLayout(buttons)

    if exec_dialog(dlg) != QDialog.DialogCode.Accepted:
        return

    # Write through the one accessor per key, so nothing can be dropped
    # the way the old write-the-whole-blob call dropped
    # `parallel_workers` (it was never IN the blob).
    _settings.set_value("new_model/units",
                        units.currentText().strip() or "hours")
    _settings.set_value("experiments/last_used", prof.currentData())
    _settings.set_value("files/autosave", autosave.isChecked())
    _settings.set_value("files/autosave_min", mins.value())
    _settings.set_value("files/save_before_run", save_before_run.currentData())
    _settings.set_value("files/open_in", open_in.currentData())
    _settings.set_value(_cores.KEY, workers.value())
    # One sync for every Cores surface (ribbon, Run panel, status chip),
    # not just the ribbon spin. Preference changes used to leave the
    # panel and chip showing the previous count while the run used the
    # new one.
    sync_cores = getattr(win, "_sync_cores_box", None)
    if sync_cores is not None:
        sync_cores()
    _settings.set_value("execution/default_animate", animate.isChecked())
    old_exp = _settings.get("editing/exp_mode", "mean")
    old_ln = _settings.get("editing/ln_mode", "mean_sd")
    new_exp = exp_combo.currentData()
    new_ln = ln_combo.currentData()
    _settings.set_value("editing/exp_mode", new_exp)
    _settings.set_value("editing/ln_mode", new_ln)
    if (new_exp, new_ln) != (old_exp, old_ln):
        # Entry-mode wording/values changed: repaint the surfaces that
        # show distribution parameters now, not only after a reselect.
        win._refresh_inspector()
        win._data_tables.refresh()
    _settings.set_value("appearance/weak_link_digits", digits.value())
    if getattr(getattr(win, "_diagram_dock", None), "view", None) is not None \
            and getattr(win._diagram_dock.view, "_weak_colors_on", False):
        win._toggle_weak_colors()
        win._toggle_weak_colors()
    _store_developer_rows(dev_widgets)
    win._apply_autosave_timer()
    if win._rbd is not None:
        new_du = parse_display_unit(display_unit_combo.currentText())
        if new_du != getattr(win._rbd, "display_unit", None):
            win._inspector.set_display_unit(new_du)
            win._data_tables.set_display_unit(new_du)
            # The Run panel and readiness line are time surfaces too;
            # they used to stay on the native unit until a reload.
            win._sync_run_panel_units()
            win._update_readiness()
            shown = new_du or win._current_units()
            if hasattr(win, "_units_chip"):
                win._units_chip.setText(f" units: {shown} "
                                        if shown else "")
            win._mark_dirty()
            win._refresh_inspector()
    win._statusbar.showMessage("Preferences saved")



def show_system_config(win):
    if win._rbd is None:
        return
    section = win._rbd.sections.get("SYSTEM_DATA")
    if not section or not section.rows:
        return
    from raptor.core.fileio.typed import system_config_from_row
    try:
        cfg = system_config_from_row(section.rows[0])
    except (ValueError, KeyError):
        return
    dlg = QDialog(win)
    dlg.setWindowTitle("Project Settings")
    form = QFormLayout(dlg)

    def fspin(value):
        s = QDoubleSpinBox()
        s.setRange(-1e15, 1e15)
        s.setDecimals(6)
        s.setValue(float(value))
        return s

    units_edit = QLineEdit(cfg.units)
    # Canonical codes stay in the file (engine keys off them); the
    # dialog shows what each termination actually finishes on.
    term_combo = QComboBox()
    for code, words in _TERMINATION_CHOICES:
        term_combo.addItem(words, code)
    term_combo.setCurrentIndex(
        max(0, term_combo.findData(cfg.termination_type)))
    # Display-unit override: Statistics start time is
    # unconditionally a sim-time value, unlike Term condition just
    # below (its label is ambiguously "time / count" depending on
    # Termination type -- left unconverted rather than guessing, the
    # same call the (now removed) Simulation Options form resolved via
    # its dynamic per-mode
    # label/value switch).
    from raptor.gui.ui.units_conv import native_to_display, display_to_native
    native_units = cfg.units
    display_unit = getattr(win._rbd, "display_unit", None)
    shown_units = display_unit or native_units

    term_cond = fspin(cfg.term_condition)
    start_stats = fspin(native_to_display(cfg.start_stats, native_units,
                                          display_unit))
    num_runs = QSpinBox()
    num_runs.setRange(1, 1_000_000)
    num_runs.setValue(cfg.num_runs)
    flow_gen = QSpinBox()
    flow_gen.setRange(0, 1_000_000_000)
    flow_gen.setValue(int(cfg.flow_generated))
    red_cost = fspin(cfg.system_red_cost)
    lost_cost = fspin(cfg.lost_flow_unit_cost)
    gy = fspin(cfg.gy_threshold)
    yr = fspin(cfg.yr_threshold)
    # Original ranges (Raptor 7's own validation): GY in 0.01..1.0,
    # YR within 0..GY.
    gy.setRange(0.01, 1.0)
    yr.setRange(0.0, 1.0)

    form.addRow("Time units:", units_edit)
    form.addRow("Termination type:", term_combo)
    form.addRow("Term condition (time / count):", term_cond)
    form.addRow(f"Statistics start time ({shown_units}):" if shown_units
               else "Statistics start time:", start_stats)
    form.addRow("Number of runs:", num_runs)
    form.addRow("Flow generated:", flow_gen)
    form.addRow("System red cost:", red_cost)
    form.addRow("Cost of lost flow:", lost_cost)
    form.addRow("Green/Yellow threshold:", gy)
    form.addRow("Yellow/Red threshold:", yr)

    # General-tab extras (Raptor 7 manual Fig. 20.3).
    neg_cb = QCheckBox()
    neg_cb.setChecked(bool(cfg.show_neg_err_msg))
    form.addRow("Ignore negative value warning:", neg_cb)
    startfail_cb = QCheckBox()
    startfail_cb.setChecked(bool(cfg.ignore_start_fail))
    form.addRow("Ignore start failures:", startfail_cb)
    comment_edit = QLineEdit()
    comment_section = win._rbd.sections.get("COMMENTS")
    sys_comment = ""
    if comment_section:
        for r in comment_section.rows:
            if r.get("Type") == "System":
                sys_comment = r.get("Comment", "")
                break
    comment_edit.setText(sys_comment)
    comment_edit.setToolTip(
        "System comment (stored with the model's Comments)")
    form.addRow("System comment:", comment_edit)

    checks = {}
    for label, attr in (("Weak Link analysis", "weak_analysis"),
                        ("Cost analysis", "cost_analysis"),
                        ("Capacity analysis", "capacity_analysis")):
        cb = QCheckBox()
        cb.setChecked(bool(getattr(cfg, attr)))
        checks[attr] = (cb, label)
        form.addRow(label + ":", cb)

    # System Streams tab (Raptor 7 manual Fig. 20.5): A-J + Ancillary.
    stream_spins = []
    stream_anti = []
    stream_box = QGroupBox("System streams")
    sform = QFormLayout(stream_box)
    letters = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J",
               "Anc"]
    for i, letter in enumerate(letters):
        raw = cfg.sys_streams[i] if i < len(cfg.sys_streams) else 0
        try:
            raw_i = int(raw)
        except (TypeError, ValueError):
            raw_i = 0
        sp = QSpinBox()
        sp.setRange(0, 10_000)
        sp.setValue(abs(raw_i))
        # Per-stream Antithetic flag (Raptor 7 manual Fig. 20.5): stored in
        # `SysStreamsArray` as a NEGATIVE stream number (`rng.py`'s
        # `make_rng` reads `stream_num < 0` as antithetic).
        anti = QCheckBox("antithetic")
        anti.setChecked(raw_i < 0)
        anti.setToolTip(
            "Antithetic variates for this stream (paired draws). Stored "
            "as a negative stream number.")
        row_w = QWidget()
        rl = QHBoxLayout(row_w)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(sp, 1)
        rl.addWidget(anti)
        # Wrapped in a container, so the form label attaches to the
        # container and the spin box would be announced unnamed.
        sp.setAccessibleName(f"System stream {letter}")
        anti.setAccessibleName(f"Stream {letter} antithetic")
        stream_spins.append(sp)
        stream_anti.append(anti)
        sform.addRow(f"Stream {letter}:", row_w)
    form.addRow(stream_box)

    buttons = QHBoxLayout()
    ok = QPushButton("Apply")
    cancel = QPushButton("Cancel")
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    _add_help_button(dlg, win, buttons)
    buttons.addStretch(1)
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    form.addRow(buttons)
    if exec_dialog(dlg) != QDialog.DialogCode.Accepted:
        return

    # Cross-field rule from Raptor 7: Yellow/Red must sit below
    # Green/Yellow, and GY must be a valid ratio. (Ranges alone can't
    # express the ordering.)
    if not (0.0 <= yr.value() < gy.value() <= 1.0):
        msgbox.warning(
            win, "Invalid thresholds",
            "Green/Yellow threshold must be in 0.01..1.0 and the "
            "Yellow/Red threshold must be lower than it.")
        return

    # Write back: one macro undo covering every changed cell.
    row = section.rows[0]
    v = win._diagram_dock.view
    edits = [
        ("units", str(units_edit.text()).strip() or "hours"),
        ("TerminationType", term_combo.currentData()),
        ("TermCondition", f"{term_cond.value():.6f}"),
        ("StartStats", f"{display_to_native(start_stats.value(), native_units, display_unit):.6f}"),
        ("NumRuns", str(num_runs.value())),
        ("flowGenerated", str(flow_gen.value())),
        ("systemRedCost", f"{red_cost.value():.6f}"),
        ("sysLostCost", f"{lost_cost.value():.6f}"),
        ("GYthreshold", f"{gy.value():.6f}"),
        ("YRthreshold", f"{yr.value():.6f}"),
        ("weakAnalysis", "1" if checks["weak_analysis"][0].isChecked()
         else "0"),
        ("costAnalysis", "1" if checks["cost_analysis"][0].isChecked()
         else "0"),
        ("capacityAnalysis",
         "1" if checks["capacity_analysis"][0].isChecked() else "0"),
        ("ShowNegErrMsg",
         "1" if neg_cb.isChecked() else "0"),
        ("IgnoreStartFail",
         "1" if startfail_cb.isChecked() else "0"),
    ]
    new_comment = comment_edit.text().strip()
    if new_comment != sys_comment and comment_section is not None:
        for r in comment_section.rows:
            if r.get("Type") == "System":
                r["Comment"] = new_comment
                break
    new_stream_str = " ".join(
        str(-sp.value() if cb.isChecked() else sp.value())
        for sp, cb in zip(stream_spins, stream_anti))
    if row.get("SysStreamsArray", "") != new_stream_str:
        edits.append(("SysStreamsArray", new_stream_str))
    # Formatting-tolerant equality and the macro bookkeeping now live in
    # `apply_row_edits` (review E-06): the file may store "20.000000" where
    # we'd write "20" -- same number, no reason to dirty the row.
    apply_row_edits(v, "SYSTEM_DATA", 0, row, edits, "System settings")
    # This dialog writes SYSTEM_DATA directly (it is the legacy system
    # form and owns ~20 fields, not just the run ones), so the run
    # experiment has to be told -- otherwise it keeps the old trials and
    # the panel and the ribbon show a value the model no longer has.
    # `_sync_experiment_from_row` is the single place that direction is
    # allowed to flow.
    win._sync_experiment_from_row()
    win._inspector.show_system_config()
    win._statusbar.showMessage("System settings updated")



class _DistGuard:
    """Keeps a dialog's distribution fields valid, for dialogs that apply
    everything on OK (Block Defaults, Mass Edit).

    Changing the distribution re-seeds the fields with valid values and the
    old mean (`distro_rules.params_after_change`); a set that is still
    invalid is named in `message` and the OK button stays off. Both
    dialogs used to apply the spin boxes as they stood, so Binomial with
    N Trials 2.5 and Probability 1.5 reached the model.

    `active()` says whether this distribution will be applied at all
    (Mass Edit applies only ticked fields)."""

    def __init__(self, combo, spins, use: str, message: QLabel, ok,
                 active=lambda: True, guards: list | None = None,
                 to_canonical=None):
        self.combo, self.spins, self.use = combo, spins, use
        self.message, self.ok, self.active = message, ok, active
        self.guards = guards if guards is not None else []
        self.to_canonical = to_canonical or (lambda _code, vals: list(vals))
        self.guards.append(self)
        self._code = combo.currentData()
        combo.currentIndexChanged.connect(lambda _i: self._on_code())
        for sp in spins:
            sp.valueChanged.connect(lambda _v: self.check())

    def _on_code(self) -> None:
        from raptor.core.distro_rules import params_after_change
        new = self.combo.currentData()
        if new is None or new == self._code:
            return
        seeded = params_after_change(
            self._code,
            self.to_canonical(self._code, [sp.value() for sp in self.spins]),
            new, self.use)
        self._code = new
        if seeded is not None:
            from raptor.gui.ui import param_entry
            shown = param_entry.to_entry(int(new), list(seeded))
            for sp, v in zip(self.spins, shown):
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
        self.check()

    def error(self) -> str | None:
        """Why this distribution cannot be applied, or None."""
        from raptor.core.distro_rules import validate_distro_params
        code = self.combo.currentData()
        if not self.active() or code in (None, 16, 18):
            return None       # Empirical and None take no typed values
        return validate_distro_params(
            int(code),
            self.to_canonical(int(code), [sp.value() for sp in self.spins]),
            self.use)

    def check(self) -> None:
        """Show the first problem of any guard and gate OK on all of them."""
        errors = [e for e in (g.error() for g in self.guards) if e]
        self.message.setText(errors[0] if errors else "")
        self.message.setVisible(bool(errors))
        self.ok.setEnabled(not errors)


def _dist_message() -> QLabel:
    from raptor.gui.resources.design_tokens import active_colour
    msg = QLabel()
    msg.setWordWrap(True)
    msg.setStyleSheet(f"color: {active_colour()['warn']}")
    msg.setVisible(False)
    return msg


def _add_stats_rows(form, win, combo, spins, *, to_canonical=None):
    """Mean and Std Dev rows under a distribution + parameters, as every other distribution entry shows them;
    returns the function that recomputes them. Parameters not offered (a one-parameter dialog) count as 0."""
    from raptor.gui.ui.inspector import compute_stats
    units = (win._current_units() if hasattr(win, "_current_units") else "") or ""
    suffix = f" ({units})" if units else ""
    mean_l, sd_l = QLabel("—"), QLabel("—")
    form.addRow(f"Mean{suffix}:", mean_l)
    form.addRow(f"Std Dev{suffix}:", sd_l)

    def refresh(*_):
        values = [s.value() for s in spins] + [0.0] * (3 - len(spins))
        if to_canonical is not None:
            values = to_canonical(int(combo.currentData()), values)
        stats = compute_stats(combo.currentData(), values)
        for lab, val in zip((mean_l, sd_l), stats if stats is not None else (None, None)):
            lab.setText("—" if val is None else (val if isinstance(val, str) else f"{val:.4g}"))
    combo.currentIndexChanged.connect(refresh)
    for s in spins:
        s.valueChanged.connect(refresh)
    refresh()
    return refresh


def edit_block_defaults(win):
    """Default failure/repair distribution + sparing applied to every
    NEW block: "A modified Block Properties dialog box
    is displayed... The Reset Defaults button will reconfigure the
    block defaults to the Raptor factory settings." This is a small,
    focused dialog covering only the fields `add_block` seeds
    independently of the cloned template row; everything else a new
    block inherits from the file's own ID=0 template row, see
    `DiagramView._template_row`."""
    from raptor.core.distro_rules import DISTRO_NAMES
    current = win._block_defaults()
    dlg = QDialog(win)
    dlg.setWindowTitle("Block Defaults")
    outer = QVBoxLayout(dlg)
    form = QFormLayout()
    outer.addLayout(form)

    # Block Template: start from a block of the library loaded with Import
    # Library (LoadDefs). Greyed, with the reason, until one is.
    library = list(getattr(win, "_block_library", None) or [])
    template_combo = QComboBox()
    template_combo.setAccessibleName("Block template")
    template_combo.addItem("Default", None)
    for t in library:
        template_combo.addItem(str(t.get("name", "?")), t.get("row", {}))
    if not library:
        template_combo.setEnabled(False)
        template_combo.setToolTip(
            "Import a block library (Data ▸ Import Library) to start from one of its blocks.")
    else:
        template_combo.setToolTip(
            "Fill the failure and repair distributions from a block of the imported library. "
            "Default returns to the factory settings.")
    form.addRow("Block template:", template_combo)

    from raptor.gui.ui.distro_combo import fill_distro_combo
    from raptor.gui.ui import param_entry

    def to_entry(code, values):
        return param_entry.to_entry(int(code), list(values))

    def to_canonical(code, values):
        return param_entry.from_entry(int(code), list(values))

    def distro_block(title: str, is_failure: bool):
        """A distribution combo, its (up to three) parameter fields relabelled for the chosen law as in
        Raptor 7's `GetParameters`, and the Mean / Std Dev rows."""
        # No explicit `current`: these dialogs present the FIRST entry as the proposed default and the user
        # picks a replacement (`test_blocks_grid_survives_mass_edit_default_invalid_beta_params` guards that).
        c = fill_distro_combo(QComboBox(), None, is_failure=is_failure)
        form.addRow(f"{title} distribution:", c)
        spins, labels = [], []
        for _ in range(3):
            sp = QDoubleSpinBox()
            sp.setRange(-1e12, 1e12)
            sp.setDecimals(6)
            lab = QLabel("")
            lab.setBuddy(sp)   # a QLabel passed to addRow has none
            form.addRow(lab, sp)
            spins.append(sp)
            labels.append(lab)
        refresh = _add_stats_rows(
            form, win, c, spins,
            to_canonical=to_canonical)

        def relabel():
            names = param_entry.labels(c.currentData())
            for i, (lab, sp) in enumerate(zip(labels, spins)):
                has = bool(names[i])
                lab.setText(f"{title} {names[i]}:" if has else "")
                lab.setVisible(has)
                sp.setVisible(has)
                if not has:
                    sp.setValue(0.0)
        c.currentIndexChanged.connect(lambda _i: relabel())
        return c, spins, relabel, refresh

    combo, fspins, _relabel_f, refresh_f = distro_block("Failure", True)
    rcombo, rspins, _relabel_r, refresh_r = distro_block("Repair", False)

    spare_combo = QComboBox()
    # Only the 4 sparing types a file can carry: the loader maps the
    # ColdPool/Resource strings to None (verified against),
    # matching the single-block Inspector's own combo restriction.
    for file_string in ("None", "Infinite", "Custom", "Pooled"):
        spare_combo.addItem(file_string, file_string)
    form.addRow("Sparing:", spare_combo)

    def populate(values: dict):
        combo.setCurrentIndex(max(combo.findData(values["fdist"]), 0))
        rcombo.setCurrentIndex(max(rcombo.findData(values["rdist"]), 0))
        fail_entry = param_entry.to_entry(int(values["fdist"]),
                                     list(values["fparams"]))
        repair_entry = param_entry.to_entry(int(values["rdist"]),
                                     list(values["rparams"]))
        for sp, v in zip(fspins, fail_entry):
            sp.setValue(v)
        for sp, v in zip(rspins, repair_entry):
            sp.setValue(v)
        si = spare_combo.findData(values["sparing"])
        spare_combo.setCurrentIndex(max(si, 0))
        _relabel_f()
        _relabel_r()
        refresh_f()
        refresh_r()

    populate(current)

    def _template_changed(_i: int) -> None:
        row = template_combo.currentData()
        if row is None:
            populate({**win._BLOCK_FACTORY_DEFAULTS, "sparing": spare_combo.currentData()})
            return
        try:
            values = {"fdist": int(float(row["FDist"])),
                      "fparams": [float(row.get(f"FParam{i}") or 0.0) for i in (1, 2, 3)],
                      "rdist": int(float(row["RDist"])),
                      "rparams": [float(row.get(f"RParam{i}") or 0.0) for i in (1, 2, 3)],
                      "sparing": spare_combo.currentData()}
        except (KeyError, TypeError, ValueError):
            return
        populate(values)
    template_combo.currentIndexChanged.connect(_template_changed)

    buttons = QHBoxLayout()
    reset = QPushButton("Reset Defaults")
    reset.setToolTip("Revert to the factory settings "
                     "(Exponential/Lognormal/Infinite spares)")
    def _reset() -> None:
        template_combo.setCurrentIndex(0)
        populate(win._BLOCK_FACTORY_DEFAULTS)
    reset.clicked.connect(_reset)
    ok = QPushButton("OK")
    cancel = QPushButton("Cancel")
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    dist_msg = _dist_message()
    outer.addWidget(dist_msg)
    guards: list = []
    _DistGuard(combo, fspins, "Failure", dist_msg, ok, guards=guards,
               to_canonical=to_canonical)
    _DistGuard(rcombo, rspins, "Repair", dist_msg, ok, guards=guards,
               to_canonical=to_canonical)
    guards[0].check()
    _add_help_button(dlg, win, buttons)
    buttons.addWidget(reset)
    buttons.addStretch(1)
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    outer.addLayout(buttons)
    if exec_dialog(dlg) != QDialog.DialogCode.Accepted:
        return
    bad = next((e for e in (g.error() for g in guards) if e), None)
    if bad:
        win._statusbar.showMessage(f"Block defaults not changed: {bad}", 8000)
        return
    values = {
        "fdist": combo.currentData(),
        "fparams": param_entry.from_entry(
            int(combo.currentData()), [sp.value() for sp in fspins]),
        "rdist": rcombo.currentData(),
        "rparams": param_entry.from_entry(
            int(rcombo.currentData()), [sp.value() for sp in rspins]),
        "sparing": spare_combo.currentData(),
    }
    settings.set_value("editing/block_defaults", values)
    # Live view so the next placed block uses them immediately.
    v = win._diagram_dock.view
    v.default_block_fdist = values["fdist"]
    v.default_block_fparams = values["fparams"]
    v.default_block_rdist = values["rdist"]
    v.default_block_rparams = values["rparams"]
    v.default_block_sparing = values["sparing"]
    win._statusbar.showMessage(
        f"New blocks will use {DISTRO_NAMES.get(values['fdist'], '?')} "
        f"failure / {DISTRO_NAMES.get(values['rdist'], '?')} repair / "
        f"{values['sparing']} sparing")



def _mass_edit_stream_picker():
    """Deferred-apply stream picker for Mass Edit (System A..J vs
    Independent N + antithetic) -- same semantics as Inspector's own
    `_stream_editor_row` (Raptor 7's SysCombo), but reading its
    final state on Apply instead of emitting live on every change (this
    dialog collects everything and applies it all at once)."""
    from raptor.core.rng import FETCH_RAPTOR_SEED
    max_ind = len(FETCH_RAPTOR_SEED) - 1
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    mode = QComboBox()
    mode.addItem("System", "sys")
    mode.addItem("Independent", "ind")
    lay.addWidget(mode)
    letter = QComboBox()
    for i in range(10):
        letter.addItem(chr(ord("A") + i), 201 + i)
    lay.addWidget(letter)
    spin = QSpinBox()
    spin.setRange(1, max_ind)
    spin.setValue(1)
    spin.setVisible(False)
    lay.addWidget(spin)
    anti = QCheckBox("Anti")
    anti.setVisible(False)
    lay.addWidget(anti)
    lay.addStretch(1)

    def on_mode(_i=None):
        is_sys = mode.currentData() == "sys"
        letter.setVisible(is_sys)
        spin.setVisible(not is_sys)
        anti.setVisible(not is_sys)
    mode.activated.connect(on_mode)
    on_mode()

    def get_value() -> int:
        if mode.currentData() == "sys":
            return letter.currentData()
        return spin.value() * (-1 if anti.isChecked() else 1)

    return w, get_value


def mass_rename(old: str, text: str, mode: str) -> str:
    """The new name for one element in a Mass Edit rename.

    `mode` is "replace" (the whole name), "prefix" or "suffix". Blank
    `text` changes nothing. Replace sets the SAME name on every selected
    element, which is rarely what anyone wants for more than one -- the
    original offered append-as-prefix / append-as-suffix / replace in
    entirety, and this port only had the last."""
    if not text:
        return old
    if mode == "prefix":
        return text + old
    if mode == "suffix":
        return old + text
    return text


def _mass_rename_row(what: str):
    """(row widget, mode combo, text edit) for a Mass Edit tab."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    mode = QComboBox()
    mode.addItem("Add as suffix", "suffix")
    mode.addItem("Add as prefix", "prefix")
    mode.addItem("Replace name", "replace")
    mode.setToolTip(
        "Suffix / prefix keep each element's own name and add your text; "
        "Replace gives every selected element the SAME name.")
    mode.setAccessibleName(f"Rename mode for {what}")
    edit = QLineEdit()
    edit.setPlaceholderText("(leave blank to keep existing names)")
    edit.setAccessibleName(f"Rename text for {what}")
    lay.addWidget(mode)
    lay.addWidget(edit, 1)
    return row, mode, edit


def on_mass_edit(win):
    """Mass Edit: tabbed dialog -- one tab per kind
    present in the selection. Changes are permanent (the manual's
    own warning) -- but undoable here via a macro."""
    if win._rbd is None:
        return
    v = win._diagram_dock.view
    sel_b, sel_n, sel_e, sel_h = v.selected_elements()
    if len(sel_b) + len(sel_n) + len(sel_e) + len(sel_h) < 2:
        win._statusbar.showMessage(
            "Mass Edit: select at least two elements first")
        return

    from PySide6.QtWidgets import QRadioButton, QButtonGroup
    from raptor.core.model import SparingType
    dlg = QDialog(win)
    dlg.setWindowTitle(
        f"Mass Edit — {len(sel_b)} block(s), {len(sel_n)} node(s), "
        f"{len(sel_e)} event(s), {len(sel_h)} hierarchy(ies)")
    outer = QVBoxLayout(dlg)
    tabs = QTabWidget()

    # Blocks tab -------------------------------------------------
    block_page = QWidget()
    block_form = QFormLayout(block_page)
    # DELEGATED (review E-14); a FAILURE law, so no code-18 relabel.
    from raptor.gui.ui.distro_combo import fill_distro_combo
    # No explicit `current`: these dialogs present the FIRST entry as
    # the proposed default and the user picks a replacement. Beta with an
    # invalid 2nd shape is what that lands on, which is the case
    # `test_blocks_grid_survives_mass_edit_default_invalid_beta_params`
    # exists to cover -- so selecting something else here would delete
    # the branch that test is guarding.
    combo = fill_distro_combo(QComboBox(), None, is_failure=True)
    # Each label is a tick box: only ticked fields apply, and editing a
    # field ticks it. Every field used to apply unconditionally, so an
    # untouched Apply wrote this dialog's starting values (Beta 100, 0)
    # over every selected block's failure and repair data.
    change_failure = QCheckBox("Failure distribution:")
    block_form.addRow(change_failure, combo)
    params = []
    for i in (1, 2, 3):
        spin = QDoubleSpinBox()
        spin.setRange(-1e12, 1e12)
        spin.setDecimals(6)
        spin.setValue(100.0 if i == 1 else 0.0)
        params.append(spin)
    # DELEGATED (review E-14). This applied the code-18 relabel
    # UNCONDITIONALLY; the shared rule decides it from `is_failure`, which
    # is False here because the label sits under "Repair distribution:".
    from raptor.gui.ui.distro_combo import fill_distro_combo
    repair_combo = fill_distro_combo(QComboBox(), None, is_failure=False)
    change_repair = QCheckBox("Repair distribution:")
    block_form.addRow(change_repair, repair_combo)
    repair_params = []
    for i in (1, 2, 3):
        spin = QDoubleSpinBox()
        spin.setRange(-1e12, 1e12)
        spin.setDecimals(6)
        spin.setValue(10.0 if i == 1 else 0.0)
        repair_params.append(spin)
    # Param names and quantity follow the selected distribution --
    # Weibull shows Shape/Scale, Triangular Min/Mode/Max, a 1-param
    # Exponential hides slots 2-3. Port of Raptor 7's GetParameters
    # relabelling; the Inspector's failure/repair groups behave the same.
    from raptor.gui.ui import param_entry
    f_labels = [QLabel("Param 1:"), QLabel("Param 2:"), QLabel("Param 3:")]
    r_labels = [QLabel("Repair param 1:"), QLabel("Repair param 2:"),
                QLabel("Repair param 3:")]

    def _to_canonical(code, values):
        return param_entry.from_entry(int(code), list(values))

    def _relabel_dist():
        fail_labels = param_entry.labels(combo.currentData())
        repair_labels = param_entry.labels(repair_combo.currentData())
        for i in range(3):
            f_has = bool(fail_labels[i])
            f_labels[i].setText(f"{fail_labels[i]}:" if f_has else "")
            f_labels[i].setVisible(f_has)
            params[i].setVisible(f_has)
            r_has = bool(repair_labels[i])
            r_labels[i].setText(f"Repair {repair_labels[i]}:" if r_has else "")
            r_labels[i].setVisible(r_has)
            repair_params[i].setVisible(r_has)
    for i in range(3):
        f_labels[i].setBuddy(params[i])
        block_form.addRow(f_labels[i], params[i])
    for i in range(3):
        r_labels[i].setBuddy(repair_params[i])
        block_form.addRow(r_labels[i], repair_params[i])
    combo.currentIndexChanged.connect(lambda _i: _relabel_dist())
    repair_combo.currentIndexChanged.connect(lambda _i: _relabel_dist())
    _relabel_dist()
    _add_stats_rows(block_form, win, combo, params, to_canonical=_to_canonical)
    _add_stats_rows(block_form, win, repair_combo, repair_params, to_canonical=_to_canonical)
    b_stream_w, b_stream_get = _mass_edit_stream_picker()
    change_block_stream = QCheckBox("System stream (F & R):")
    block_form.addRow(change_block_stream, b_stream_w)
    spare_combo = QComboBox()
    # Only the 4 sparing types a file can carry (ColdPool/Resource load
    # as None) -- see `edit_block_defaults`'s identical restriction above.
    for st in (SparingType.NONE, SparingType.SPARE_POOL,
              SparingType.INFINITE, SparingType.CUSTOM):
        spare_combo.addItem(st.name.replace("_", " ").title(), st.name)
    change_sparing = QCheckBox("Sparing:")
    block_form.addRow(change_sparing, spare_combo)
    # Pool Name: only meaningful for Sparing=SPARE_POOL, but always
    # applied when the Blocks tab is present, matching this dialog's
    # "whole tab applies unconditionally" convention for
    # Fail-dist/Repair-dist/Stream/Sparing above. A Pooled block with
    # no pool assigned has no SPARE_POOL_DATA row to resolve; the
    # validator reports it before Run.
    pool_combo = QComboBox()
    pool_combo.addItem("(none)", "")
    sec_pools = win._rbd.sections.get("SPARE_POOL_DATA")
    for r4 in (sec_pools.rows if sec_pools else []):
        spare_pool_name = r4.get("PoolName", "")
        if spare_pool_name:
            pool_combo.addItem(spare_pool_name, spare_pool_name)
    block_form.addRow("Pool:", pool_combo)
    b_rename_row, b_rename_mode, b_name_edit = \
        _mass_rename_row("blocks")
    block_form.addRow("Rename:", b_rename_row)
    tabs.addTab(block_page,
                f"Blocks ({len(sel_b)})" if sel_b else "Blocks")

    # Events tab ---------------------------------------------------
    event_page = QWidget()
    event_form = QFormLayout(event_page)
    event_param = QDoubleSpinBox()
    event_param.setRange(0.0, 1.0)
    event_param.setDecimals(6)
    event_param.setSingleStep(0.05)
    event_param.setValue(0.5)
    change_event_param = QCheckBox("Success probability (Param):")
    event_form.addRow(change_event_param, event_param)
    e_stream_w, e_stream_get = _mass_edit_stream_picker()
    change_event_stream = QCheckBox("System stream:")
    event_form.addRow(change_event_stream, e_stream_w)
    e_rename_row, e_rename_mode, e_name_edit = \
        _mass_rename_row("events")
    event_form.addRow("Rename:", e_rename_row)
    tabs.addTab(event_page,
                f"Events ({len(sel_e)})" if sel_e else "Events")

    # Nodes tab ----------------------------------------------------
    node_page = QWidget()
    node_form = QFormLayout(node_page)
    node_k = QSpinBox()
    node_k.setRange(1, 999)
    node_k.setValue(1)
    change_k = QCheckBox("Required good paths (k):")
    node_form.addRow(change_k, node_k)
    dep_grp_box = QGroupBox("Dependency")
    dep_lay = QVBoxLayout(dep_grp_box)
    r_indep = QRadioButton("Independent")
    r_local = QRadioButton("Locally dependent")
    r_sys = QRadioButton("System dependent")
    r_on = QRadioButton("Dependent on:")
    r_indep.setChecked(True)
    dep_group = QButtonGroup(dep_grp_box)
    for r in (r_indep, r_local, r_sys, r_on):
        dep_group.addButton(r)
        dep_lay.addWidget(r)
    dep_combo = QComboBox()
    dep_blocks: list[tuple[int, str]] = []
    sec_blk = win._rbd.sections.get("BLOCK_DATA")
    if sec_blk is not None:
        for row in sec_blk.rows:
            try:
                bid_ = int(float(row.get("ID", "-1")))
            except (ValueError, KeyError):
                continue
            name_ = row.get("blockName", "")
            # Same unnamed-master guard as the Inspector's own
            # _dependency_group -- blank names aren't valid masters.
            if not name_ or not name_.strip():
                continue
            dep_blocks.append((bid_, name_))
    dep_blocks.sort()
    for bid_, name_ in dep_blocks:
        dep_combo.addItem(f"{name_} block - {bid_:03d}", bid_)
    if not dep_blocks:
        r_on.setEnabled(False)
        r_on.setToolTip("No blocks in this model")
    dep_lay.addWidget(dep_combo)
    change_dependency = QCheckBox("Change the dependency")
    node_form.addRow(change_dependency)
    node_form.addRow(dep_grp_box)
    n_rename_row, n_rename_mode, n_name_edit = \
        _mass_rename_row("nodes")
    node_form.addRow("Rename:", n_rename_row)
    tabs.addTab(node_page, f"Nodes ({len(sel_n)})" if sel_n else "Nodes")

    # Hierarchies tab ------------------------------------------------
    hier_page = QWidget()
    hier_form = QFormLayout(hier_page)
    h_rename_row, h_rename_mode, h_name_edit = \
        _mass_rename_row("hierarchies")
    hier_form.addRow("Rename:", h_rename_row)
    h_note = QLabel(
        "Dependency isn't simulated for hierarchies (no engine support "
        "-- HIERARCHY_DATA has no field to persist it in). Use the "
        "Phasing tab above to mark selected hierarchies phased.")
    h_note.setWordWrap(True)
    hier_form.addRow(h_note)
    tabs.addTab(hier_page,
                f"Hierarchies ({len(sel_h)})" if sel_h else "Hierarchies")

    # Phasing tab --------------------------------------------------
    ph_page = None
    ph_editors = []
    ph_check = None
    sec_ph = win._rbd.sections.get("PHASES") if win._rbd else None
    if sec_ph and sec_ph.rows and (sel_b or sel_n or sel_e or sel_h):
        ph_page = QWidget()
        pform = QFormLayout(ph_page)
        from PySide6.QtWidgets import QCheckBox as _CB
        ph_check = _CB("Apply phasing to all selected")
        pform.addRow(ph_check)
        phase_names = [r.get("PhaseName", f"P{i+1}")
                       for i, r in enumerate(sec_ph.rows)]
        for phase_name in phase_names:
            # Named `ph_combo`/`ph_spin`, not `combo`/`spin`: those
            # names belong to the Blocks tab's own failure-distribution
            # widgets above, and sharing the same function scope would
            # overwrite them before the apply step reads `combo`, which
            # would corrupt `fail_distro` with a phasing code.
            ph_combo = QComboBox()
            for code, label in (("A", "Active stress"), ("L", "Linked"),
                                ("C", "Cut"), ("K", "k paths")):
                ph_combo.addItem(label, code)
            ph_spin = QDoubleSpinBox()
            ph_spin.setRange(-1e9, 1e9)
            ph_spin.setDecimals(3)
            ph_spin.setValue(1.0)
            pform.addRow(f"{phase_name}:", ph_combo)
            pform.addRow("", ph_spin)
            ph_editors.append((ph_combo, ph_spin))
        note2 = QLabel(
            "Codes: A=Active stress / L=Linked / C=Cut / K=k paths "
            "(nodes). Applies to ALL selected elements.")
        note2.setWordWrap(True)
        pform.addRow(note2)
        tabs.addTab(ph_page, "Phasing")

    # Hide tabs whose kind isn't selected (real hides them too).
    if not sel_b:
        tabs.removeTab(tabs.indexOf(block_page))
    if not sel_e:
        tabs.removeTab(tabs.indexOf(event_page))
    if not sel_n:
        tabs.removeTab(tabs.indexOf(node_page))
    if not sel_h:
        tabs.removeTab(tabs.indexOf(hier_page))
    outer.addWidget(tabs)

    # Start from the first selected block's own values, so ticking a
    # field starts from something real rather than the dialog's defaults.
    first = next((r for r in (win._rbd.sections.get("BLOCK_DATA").rows
                              if sel_b else [])
                  if str(r.get("ID")) == str(sel_b[0])), None) if sel_b else None
    if first is not None:
        for c, key, spins, pre in ((combo, "FDist", params, "FParam"),
                                   (repair_combo, "RDist", repair_params, "RParam")):
            idx = c.findData(int(float(first.get(key, 0) or 0)))
            if idx >= 0:
                c.setCurrentIndex(idx)
            for i, sp in enumerate(spins, 1):
                try:
                    sp.setValue(float(first.get(f"{pre}{i}", 0) or 0))
                except ValueError:
                    pass
    # Editing a field ticks it (connected after the pre-fill above).
    from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox
    def _ticks(box, *widgets):
        for widget in widgets:
            for x in [widget, *widget.findChildren(QWidget)]:
                if isinstance(x, QComboBox):
                    x.activated.connect(lambda _i, b=box: b.setChecked(True))
                elif isinstance(x, QAbstractSpinBox):
                    x.editingFinished.connect(lambda b=box: b.setChecked(True))
                elif isinstance(x, QAbstractButton) and x is not box:
                    x.clicked.connect(lambda _c=False, b=box: b.setChecked(True))
    _ticks(change_failure, combo, *params)
    _ticks(change_repair, repair_combo, *repair_params)
    _ticks(change_block_stream, b_stream_w)
    _ticks(change_sparing, spare_combo, pool_combo)
    _ticks(change_event_param, event_param)
    _ticks(change_event_stream, e_stream_w)
    _ticks(change_k, node_k)
    _ticks(change_dependency, dep_grp_box)

    note = QLabel(
        "Only TICKED fields change, on every selected element (editing a "
        "field ticks it). Undoable in this session.")
    note.setWordWrap(True)
    outer.addWidget(note)
    buttons = QHBoxLayout()
    ok = QPushButton("Apply")
    cancel = QPushButton("Cancel")
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    dist_msg = _dist_message()
    outer.addWidget(dist_msg)
    guards: list = []
    _DistGuard(combo, params, "Failure", dist_msg, ok,
               active=change_failure.isChecked, guards=guards,
               to_canonical=_to_canonical)
    _DistGuard(repair_combo, repair_params, "Repair", dist_msg, ok,
               active=change_repair.isChecked, guards=guards,
               to_canonical=_to_canonical)
    for box in (change_failure, change_repair):
        box.toggled.connect(lambda _on: guards[0].check())
    guards[0].check()
    _add_help_button(dlg, win, buttons)
    buttons.addStretch(1)
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    outer.addLayout(buttons)
    if exec_dialog(dlg) != QDialog.DialogCode.Accepted:
        return
    bad = next((e for e in (g.error() for g in guards) if e), None)
    if bad:
        win._statusbar.showMessage(f"Mass Edit not applied: {bad}", 8000)
        return

    v._stack.beginMacro("Mass edit")
    applied = []
    try:
        if sel_b and (change_failure.isChecked() or change_repair.isChecked()
                      or change_block_stream.isChecked() or change_sparing.isChecked()
                      or b_name_edit.text().strip()):
            fail_dist = combo.currentData()
            vals = _to_canonical(fail_dist, [spin.value() for spin in params])
            nparams = 2 if fail_dist == 4 else 3
            vals = vals[:nparams] + [0.0] * (3 - nparams)
            repair_dist = repair_combo.currentData()
            repair_entry = _to_canonical(repair_dist, [spin.value() for spin in repair_params])
            rnparams = 2 if repair_dist == 4 else 3
            repair_entry = repair_entry[:rnparams] + [0.0] * (3 - rnparams)
            b_stream = b_stream_get()
            spare_type = spare_combo.currentData()
            pool_name = pool_combo.currentData()
            b_name = b_name_edit.text().strip()
            b_mode = b_rename_mode.currentData()
            for bid in sel_b:
                if change_failure.isChecked():
                    v.set_block_dist(bid, "F", fail_dist)
                    v.set_block_fail_params(bid, vals)
                if change_repair.isChecked():
                    v.set_block_dist(bid, "R", repair_dist)
                    v.set_block_repair_params(bid, repair_entry)
                if change_block_stream.isChecked():
                    v.set_block_stream(bid, "FR", b_stream)
                if change_sparing.isChecked():
                    v.set_block_sparing_type(bid, spare_type)
                    v.set_block_pool_name(bid, pool_name)
                new = mass_rename(v.blocks[bid].name, b_name, b_mode)
                if new != v.blocks[bid].name:
                    v.rename_block(bid, new)
            applied.append(f"{len(sel_b)} block(s)")
        if sel_e and (change_event_param.isChecked() or change_event_stream.isChecked()
                      or e_name_edit.text().strip()):
            e_stream = e_stream_get()
            e_name = e_name_edit.text().strip()
            e_mode = e_rename_mode.currentData()
            for eid in sel_e:
                if change_event_param.isChecked():
                    v.set_event_field(eid, "param", event_param.value())
                if change_event_stream.isChecked():
                    v.set_event_field(eid, "stream", e_stream)
                new = mass_rename(v.events[eid].name, e_name, e_mode)
                if new != v.events[eid].name:
                    v.rename_event(eid, new)
            applied.append(f"{len(sel_e)} event(s)")
        if sel_n and (change_k.isChecked() or change_dependency.isChecked()
                      or n_name_edit.text().strip()):
            if r_indep.isChecked():
                dep_num, dep_type = 0, ""
            elif r_local.isChecked():
                dep_num, dep_type = -1, ""
            elif r_sys.isChecked():
                dep_num, dep_type = -2, ""
            elif dep_blocks:
                dep_num, dep_type = dep_combo.currentData(), "RBDBlock"
            else:
                dep_num, dep_type = 0, ""
            n_name = n_name_edit.text().strip()
            n_mode = n_rename_mode.currentData()
            for nid in sel_n:
                if change_k.isChecked():
                    v.set_node_good_paths(nid, node_k.value())
                if change_dependency.isChecked():
                    v.set_node_dep(nid, dep_num, dep_type)
                new = mass_rename(v.nodes[nid].node_name, n_name, n_mode)
                if new != v.nodes[nid].node_name:
                    v.rename_node(nid, new)
            applied.append(f"{len(sel_n)} node(s)")
        if sel_h:
            h_name = h_name_edit.text().strip()
            h_mode = h_rename_mode.currentData()
            if h_name:
                for hid in sel_h:
                    new = mass_rename(v.hiers[hid].name, h_name, h_mode)
                    if new != v.hiers[hid].name:
                        v.rename_hier(hid, new)
                applied.append(f"{len(sel_h)} hierarchy(ies)")
        if ph_page is not None and req(ph_check).isChecked():
            entries = [(combo.currentData(), spin.value())
                       for combo, spin in ph_editors]
            count = 0
            for bid in sel_b:
                if v.set_element_phasing("block", bid, True, entries):
                    count += 1
            for eid in sel_e:
                if v.set_element_phasing("event", eid, True, entries):
                    count += 1
            node_entries = []
            for code, val in entries:
                if code == "A":
                    node_entries.append(("K", float(val)))
                else:
                    node_entries.append((code, float(val)))
            for nid in sel_n:
                if v.set_element_phasing("node", nid, True,
                                         node_entries):
                    count += 1
            for hid in sel_h:
                if v.set_element_phasing("hier", hid, True, []):
                    count += 1
            if count:
                applied.append(f"phasing on {count} element(s)")
    finally:
        v._stack.endMacro()
    win._statusbar.showMessage(
        "Mass edit applied to " + ", ".join(applied) if applied else
        "Mass edit: nothing was ticked, so nothing changed")

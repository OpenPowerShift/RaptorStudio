"""The Run panel — one home for every run setting.

Why a panel and not the ribbon
------------------------------
The Simulate tab had 35 controls in 6 flat ribbon groups, four of which
edited the same handful of `SYSTEM_DATA` flags. Four problems, all
traceable to those values being on a ribbon:

* **The run count had three editors** (a ribbon spinner, Sim Options and
  Project Settings) with a hand-maintained two-way sync, and Project
  Settings never pushed back — so they could and did drift.
* **Dependent settings were tooltip prose.** The Capacity Plot toggle
  said "Requires Capacity Analysis" in a group 600px from the toggle
  that set it, and nothing enforced it.
* **Ribbon height was a flat 188px for every tab** — Home's two buttons
  cost exactly what Simulate's thirty-five did. The ribbon is now
  content-driven, but a ribbon is still the wrong container for eight
  numbers.
* **A batch run needed three changes in three places**, one of which
  (Sim Options ▸ Graphics) wrote a column nothing read.

Why the BOTTOM dock and not a new right-side panel
---------------------------------------------------
Because there is no width left. The design spec sets a minimum canvas of
700×500 (the internal UI blueprint); at 1280px, Explorer (260)
+ Inspector (340) already leaves 680. A third right-side panel of ~320
takes the canvas to 360 — less than half the spec minimum, at exactly
the width where compact behaviour is not yet active (`:46`). The bottom
strip is already one tabified group with one splitter, so a third tab
there costs no new width, and run *configuration* reads better beside
run *output* (Results) than beside the Inspector, which is about the
selected model object.

The panel is a `QDockWidget` taking its collaborators by injection — the
same shape `InspectorDock` uses — rather than reaching into MainWindow.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow, QScrollArea,
    QSpinBox, QTabBar, QVBoxLayout, QWidget,
)

from raptor.core import run_experiment as rx
from raptor.gui.resources.design_tokens import SPACING, active_colour


class RunPanel(QWidget):
    """The experiment editor.

    Emits `experiment_changed` with a normalised dict; the owner decides
    whether that becomes a model edit (it does -- the panel is a view,
    MainWindow is the writer, so undo, dirty-tracking and the legacy
    column mirror all stay in one place)."""

    experiment_changed = Signal(dict)
    #: Emitted when a dependent plot is requested without its analysis.
    dependency_blocked = Signal(str, str)   # (plot key, analysis label)
    #: The Cores field changed. Not part of the experiment: how many cores
    #: this machine gives a run is an app setting, shared with the ribbon's
    #: Cores box (`cores.KEY`), so it never goes into the model file.
    cores_changed = Signal(int)
    #: The panel's content height changed -- the dock caps its
    #: height to the content and must recompute (see
    #: `RunPanelDock.fit_height`).
    size_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._exp = rx.defaults()
        self._loading = False
        self._enabled = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING["sm"], SPACING["sm"],
                                 SPACING["sm"], SPACING["sm"])
        outer.setSpacing(SPACING["xs"])

        # -- readiness line ------------------------------------------------
        # The design spec requires every disabled action to explain its
        # unmet condition (`blueprint:125`) and a six-state readiness
        # machine (`blueprint:484-491`). A bare greyed Run button tells
        # the user nothing; this line does.
        self._readiness = QLabel()
        self._readiness.setObjectName("RunReadiness")
        self._readiness.setWordWrap(True)
        self._readiness.setAccessibleName("Run readiness")
        outer.addWidget(self._readiness)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        # `QScrollArea.sizeHint()` is a fixed ~400 px guess, not the
        # content's height, so the dock cannot size itself from it
        # (`content_height`). Keep the widget to measure from.
        self._scroll = scroll
        # Deferred re-fit after a resize (see `resizeEvent`). An owned
        # timer, not `QTimer.singleShot`, so a pending tick cannot fire
        # into a panel whose C++ object is already gone.
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.size_changed)
        body = QWidget()
        scroll.setWidget(body)
        # The panel lives in the BOTTOM dock: wide and short. One tall
        # column of four sections left three rows visible at 1280x800
        # and stretched a trials spin box across 1800 px at 1920. The
        # sections are laid out as columns instead, as many as the width
        # allows (`_arrange`), and fall back to a stack when the dock is
        # floated or docked narrow.
        self._grid = QGridLayout(body)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SPACING["sm"])
        self._grid.setVerticalSpacing(SPACING["sm"])
        self._sections = [
            self._build_experiment_box(),
            self._build_analysis_box(),
            self._build_recording_box(),
            self._build_debugger_box(),
        ]
        self._columns = 0
        self._arrange(1)

        self._label_all_controls()
        self._refresh()

    #: Below this width per column a section's notes wrap to one word
    #: a line, so fewer, wider columns read better.
    SECTION_MIN_WIDTH = 340

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cols = max(1, min(len(self._sections),
                          self.width() // self.SECTION_MIN_WIDTH))
        # 3 columns would strand the fourth section alone on a row.
        if cols == 3:
            cols = 2
        self._arrange(cols)
        # `body.sizeHint()` still reports the OLD width at this point --
        # the children are resized after this event -- so a fit computed
        # now is a column count out of date, and left the dock ~350 px too
        # tall at 1920 (it had measured the two-column layout). Re-fit
        # once the layout has settled; `fit_height` is a no-op when the
        # answer has not changed, so this cannot spin.
        self._fit_timer.start()

    def content_height(self) -> int:
        """The height the panel's contents actually need at this width.

        `QScrollArea.sizeHint()` is a fixed guess (about 400 px) that has
        nothing to do with what is in it, so sizing the dock from the
        panel's default hint left ~200 px of dead space under the last
        section. Measure the scrolled body's layout instead: its hint
        already accounts for the current column count and wrapped notes.
        """
        outer = self.layout()
        if outer is None:
            return super().sizeHint().height()
        m = outer.contentsMargins()
        ready = max(self._readiness.height(),
                    self._readiness.sizeHint().height())
        body = self._scroll.widget()
        body_h = body.sizeHint().height() if body is not None else 0
        return (m.top() + m.bottom() + ready + outer.spacing() + body_h
                + 2 * self._scroll.frameWidth())

    def sizeHint(self):  # noqa: N802 (Qt spelling)
        hint = super().sizeHint()
        hint.setHeight(max(self.content_height(),
                           self.minimumSizeHint().height()))
        return hint

    def _arrange(self, cols: int) -> None:
        """Place the sections in `cols` columns, top-aligned."""
        if cols == self._columns:
            return
        self._columns = cols
        for box in self._sections:
            self._grid.removeWidget(box)
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for i, box in enumerate(self._sections):
            self._grid.addWidget(box, i // cols, i % cols,
                                 Qt.AlignmentFlag.AlignTop)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)
        rows = (len(self._sections) + cols - 1) // cols
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 0)
        self._grid.setRowStretch(rows, 1)
        # A different column count rewraps every note, so the content's
        # height changed too -- and the dock caps itself to that height.
        self.size_changed.emit()

    # -- construction --------------------------------------------------

    def _build_experiment_box(self) -> QGroupBox:
        box = QGroupBox("Experiment")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._profile = QComboBox()
        for key, spec in rx.PROFILES.items():
            self._profile.addItem(spec["label"], key)
        self._profile.setToolTip(
            "A starting point for these settings. It only overwrites what "
            "it names, so the rest of your experiment is kept.")
        self._profile.currentIndexChanged.connect(self._on_profile)
        form.addRow("Profile:", self._profile)

        self._trials = QSpinBox()
        self._trials.setRange(1, 1_000_000)
        self._trials.setToolTip(
            "How many times to run the model. More trials shrink the "
            "uncertainty on the reported numbers.")
        # editingFinished, not valueChanged: a spin box emitting on every
        # keystroke would push an undo entry per digit.
        self._trials.editingFinished.connect(
            lambda: self._emit_change("trials", self._trials.value()))
        form.addRow("Trials:", self._trials)

        from raptor.gui import cores
        self._cores = QSpinBox()
        self._cores.setRange(1, cores.available())
        self._cores.setSuffix(f" of {cores.available()}")
        self._cores.setAccessibleName("Cores for full-speed runs")
        self._cores.setMinimumWidth(96)
        # The Run panel is the one editor for Cores. The ribbon box is a
        # read-only readout; Preferences shows the same value without a
        # second write path.
        self._cores.editingFinished.connect(
            lambda: self.cores_changed.emit(self._cores.value()))
        # Why it is greyed, in words beside it (a tooltip alone went unseen).
        self._cores_note = QLabel()
        self._cores_note.setObjectName("SectionNote")
        self._cores_note.setWordWrap(True)
        cores_row = QHBoxLayout()
        cores_row.setContentsMargins(0, 0, 0, 0)
        cores_row.addWidget(self._cores)
        cores_row.addWidget(self._cores_note, 1)
        form.addRow("Cores:", cores_row)

        self._term_type = QComboBox()
        for code, words in (("TimeSim", "Time"),
                            ("FailSim", "System failures"),
                            ("CycleSim", "Phase cycles")):
            self._term_type.addItem(words, code)
        self._term_type.currentIndexChanged.connect(
            lambda _i: self._on_term_type())
        form.addRow("Stop at:", self._term_type)

        self._term_value = QDoubleSpinBox()
        self._term_value.setDecimals(6)
        self._term_value.setRange(0.0, 1e15)
        self._term_value.setToolTip("The run length, in the unit named")
        self._term_value.editingFinished.connect(
            lambda: self._commit_time_field(
                "term_condition", self._term_value,
                self._exp["termination_type"]))
        # Keep the label widget itself (QFormLayout::addRow returns it),
        # so the run-length row can be RELABELLED when the termination
        # type changes: run length is a sim time under TimeSim but a plain
        # count under FailSim/CycleSim, and a field labelled "Run length"
        # holding "10" of failures is actively misleading.
        self._term_label = QLabel("Run length:")
        self._term_label.setObjectName("SectionNote")
        form.addRow(self._term_label, self._term_value)

        self._start_stats = QDoubleSpinBox()
        self._start_stats.setDecimals(6)
        self._start_stats.setRange(0.0, 1e15)
        self._start_stats.setToolTip(
            "Statistics gathering starts here, so start-up transients are "
            "excluded from the reported numbers. Must be earlier than the "
            "run length.")
        self._start_stats.editingFinished.connect(
            lambda: self._commit_time_field(
                "start_stats", self._start_stats, "TimeSim"))
        form.addRow("Statistics from:", self._start_stats)

        self._unit_note = QLabel()
        self._unit_note.setObjectName("SectionNote")
        form.addRow("", self._unit_note)
        return box

    def _build_analysis_box(self) -> QGroupBox:
        box = QGroupBox("Analysis")
        outer = QVBoxLayout(box)
        outer.setSpacing(2)
        self._analysis_checks: dict[str, QCheckBox] = {}
        grid = self._check_grid(outer)
        for r, (key, label, hint) in enumerate((
                ("weak", "Weak Link",
                 "Track per-element state times, for the Weak Link view"),
                ("cost", "Cost",
                 "Accumulate life-cycle cost over the run"),
                ("capacity", "Capacity",
                 "Record flow and capacity against the flow limits"))):
            cb = QCheckBox(label)
            cb.setToolTip(hint)
            cb.toggled.connect(
                lambda on, k=key: self._on_analysis(k, on))
            self._add_check_row(grid, r, cb, hint)
            self._analysis_checks[key] = cb

        # --- importance measures -------------------------------------
        # Its own row because its cost is of a different order: every other
        # analysis adds work INSIDE a run, this one multiplies the number of
        # runs. The note states that before it is switched on.
        self._importance_check = QCheckBox("Importance measures")
        self._importance_check.setToolTip(
            "IEC 61078 Annex D importance factors, estimated by re-simulation: "
            "one extra batch per block, two with Birnbaum.")
        self._importance_check.toggled.connect(self._on_importance_toggled)
        # The cost note goes in the SAME grid column as every other
        # analysis note: a free-standing label started at the group's left
        # margin and broke the one-column alignment the panel guarantees.
        self._importance_note = self._add_check_row(
            grid, 3, self._importance_check, "")
        self._importance_mode = QComboBox()
        for mode, label in (("improvement", "Improvement (RRW, Vesely-Fussell)"),
                            ("risk", "Risk (RAW)"),
                            ("both", "Both (adds Birnbaum)")):
            self._importance_mode.addItem(label, mode)
        # Start on the agreed default (Both). Left on index 0 the picker
        # meant "improvement" and ticking the box silently gave a different
        # analysis from the one the note priced.
        _default = self._importance_mode.findData(rx.IMPORTANCE_DEFAULT_MODE)
        if _default >= 0:
            self._importance_mode.setCurrentIndex(_default)
        self._importance_mode.currentIndexChanged.connect(
            self._on_importance_mode)
        grid.addWidget(self._importance_mode, 4, 1,
                       Qt.AlignmentFlag.AlignLeft)
        self._importance_blocks = 0
        self._sync_importance_rows()
        return box

    def _sync_importance_rows(self) -> None:
        """Show/hide the mode picker and state the cost for this model."""
        from raptor.api import importance as imp

        experiment = getattr(self, "_exp", None) or rx.defaults()
        mode = str(experiment.get("importance", "none"))
        on = mode != "none"
        self._importance_check.blockSignals(True)
        self._importance_check.setChecked(on)
        self._importance_check.blockSignals(False)
        self._importance_mode.setVisible(on)
        # The note stays visible either way: the cost is what a user needs
        # BEFORE ticking the box, and an empty hidden label laid out at the
        # group margin broke the one-column alignment.
        if not on:
            self._importance_note.setText(
                "One extra batch per block (two for Both), on top of the "
                "ordinary run. Set Cores above 1.")
            return
        idx = self._importance_mode.findData(mode)
        if idx >= 0 and idx != self._importance_mode.currentIndex():
            self._importance_mode.blockSignals(True)
            self._importance_mode.setCurrentIndex(idx)
            self._importance_mode.blockSignals(False)
        blocks = int(getattr(self, "_importance_blocks", 0) or 0)
        trials = int(experiment.get("trials", 1))
        if blocks:
            batches = imp.batch_count(blocks, mode)
            runs = imp.estimate_runs(blocks, mode, trials)
            self._importance_note.setText(
                f"{blocks} block(s) → {batches} batches, about "
                f"{runs:,} simulated runs. Set Cores above 1; each batch is a "
                f"small share of the total. Variant batches record no logs or "
                f"plots.")
        else:
            self._importance_note.setText(
                "One extra batch per block (two for Both), on top of the "
                "ordinary run. Set Cores above 1.")
        # The mode picker and the second note line appear/disappear here, so
        # the content height changed.
        self.size_changed.emit()

    def set_importance_blocks(self, count: int) -> None:
        """How many blocks the open model has, for the cost estimate."""
        self._importance_blocks = int(count or 0)
        self._sync_importance_rows()

    def _on_importance_toggled(self, on: bool) -> None:
        if self._loading or not self._enabled:
            return
        experiment = self.experiment()
        if on:
            experiment["importance"] = (self._importance_mode.currentData()
                                 or rx.IMPORTANCE_DEFAULT_MODE)
        else:
            experiment["importance"] = "none"
        self.set_experiment(experiment)
        self.experiment_changed.emit(self.experiment())

    def _on_importance_mode(self, _index: int) -> None:
        if self._loading or not self._enabled:
            return
        experiment = self.experiment()
        experiment["importance"] = (self._importance_mode.currentData()
                             or rx.IMPORTANCE_DEFAULT_MODE)
        self.set_experiment(experiment)
        self.experiment_changed.emit(self.experiment())

    @staticmethod
    def _check_grid(outer: QVBoxLayout) -> QGridLayout:
        """Checkboxes in one column, their notes lined up in the next.
        Each row used to lay itself out, so every note started wherever
        its own checkbox's label ended."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)
        return grid

    @staticmethod
    def _add_check_row(grid: QGridLayout, r: int, cb: QCheckBox,
                       hint: str) -> QLabel:
        note = QLabel(hint)
        note.setObjectName("SectionNote")
        note.setWordWrap(True)
        top = Qt.AlignmentFlag.AlignTop
        grid.addWidget(cb, r, 0, top)
        grid.addWidget(note, r, 1, top)
        return note

    def _build_recording_box(self) -> QGroupBox:
        box = QGroupBox("Recording")
        outer = QVBoxLayout(box)
        outer.setSpacing(2)

        # Additive checkboxes with a per-row note, NOT a single
        # "recording level" dropdown. The design spec's five levels are
        # a volume hierarchy (`blueprint:462-468`) but the engine
        # accumulates INDEPENDENT sinks, so a radio would impose an
        # exclusivity that does not exist and make "state intervals, but
        # no multi-gigabyte event log" inexpressible.
        self._recording_checks: dict[str, QCheckBox] = {}
        self._recording_notes: dict[str, QLabel] = {}
        self._recording_hints = {key: hint
                                 for key, _label, hint in rx.RECORDING_FIELDS}
        grid = self._check_grid(outer)
        for r, (key, label, hint) in enumerate(rx.RECORDING_FIELDS):
            cb = QCheckBox(label)
            cb.toggled.connect(
                lambda on, k=key: self._on_recording(k, on))
            note = self._add_check_row(grid, r, cb, hint)
            self._recording_checks[key] = cb
            self._recording_notes[key] = note

        iv = QFormLayout()
        self._ao_interval = QDoubleSpinBox()
        self._ao_interval.setDecimals(3)
        self._ao_interval.setRange(0.001, 1e6)
        self._ao_interval.setToolTip(
            "How often the Availability plot is sampled, in the model's "
            "time units. Smaller = smoother, larger files.")
        self._ao_interval.editingFinished.connect(
            self._on_ao_interval)
        iv.addRow("Ao sample every:", self._ao_interval)

        self._cost_interval = QDoubleSpinBox()
        self._cost_interval.setDecimals(3)
        self._cost_interval.setRange(0.001, 1e9)
        self._cost_interval.setToolTip(
            "How often interim cost totals are recorded, in the model's "
            "time units.")
        self._cost_interval.editingFinished.connect(
            self._on_cost_interval)
        iv.addRow("Cost every:", self._cost_interval)
        outer.addLayout(iv)

        # The two intervals are useless unless their plot is ticked, so
        # they follow it rather than sitting permanently enabled.
        self._interval_rows = (self._ao_interval, self._cost_interval)
        return box

    # -- accessibility --------------------------------------------------
    #
    # `setAccessibleName` appeared exactly ONCE in the whole GUI before
    # this panel (ribbon.py:39, derived from the action text), so every
    # control added here is a first. The design spec makes this
    # mandatory, not polish: "The system must support ... screen-reader
    # descriptions for controls" (the internal UI strategy notes) and
    # "Every icon-only control has tooltip, accessible name and keyboard
    # route" (`blueprint:165`).
    #
    # Names say what the control DOES, not what it is called, because the
    # visible label is not always adjacent once a group is filtered or a
    # note wraps beside it.

    def _label_all_controls(self) -> None:
        pairs = (
            (self._profile, "Starting experiment"),
            (self._trials, "Number of trials"),
            (self._term_type, "Stop condition"),
            (self._term_value, "Run length"),
            (self._start_stats, "Statistics start time"),
            (self._ao_interval, "Availability plot sample interval"),
            (self._cost_interval, "Interim cost sample interval"),
        )
        for w, name in pairs:
            w.setAccessibleName(name)
        for cb in self._analysis_checks.values():
            cb.setAccessibleName(f"{cb.text()} analysis")
        for key, cb in self._recording_checks.items():
            label = next(lbl for k, lbl, _h in rx.RECORDING_FIELDS
                         if k == key)
            cb.setAccessibleName(f"Record {label.lower()}")
        for key, note in self._recording_notes.items():
            label = next(lbl for k, lbl, _h in rx.RECORDING_FIELDS
                         if k == key)
            note.setAccessibleName(f"{label} note")
        self._readiness.setAccessibleName("Run readiness")
        # The dependent-plot explanation is the ONLY thing standing
        # between a screen-reader user and an unexplained disabled box.
        self._term_label.setAccessibleName("Run length label")
        self._unit_note.setAccessibleName("Unit note")

    def _build_debugger_box(self) -> QGroupBox:
        """The stepping debugger's PARAMETERS.

        These three used to sit on the Simulate ribbon interleaved with
        the step buttons, which is why that group was the widest on the
        page: a number on a ribbon has nowhere to be labelled properly
        and nowhere to explain itself. They are here instead, and the
        ribbon's More Steps menu names the value each of its items uses.
        """
        box = QGroupBox("Stepping (debugger)")
        box.setToolTip(
            "Settings for the interactive step debugger. Used by the "
            "Debug tab's More Steps menu; a stepped session always runs a "
            "single replication.")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._step_count = QSpinBox()
        self._step_count.setRange(1, 100_000)
        self._step_count.setValue(10)
        self._step_count.setToolTip(
            "How many events the Multi command advances")
        self._step_count.setAccessibleName("Events per multi-step")
        form.addRow("Multi-step events:", self._step_count)

        self._run_to = QDoubleSpinBox()
        self._run_to.setDecimals(3)
        self._run_to.setRange(0.0, 1e12)
        self._run_to.setValue(100.0)
        self._run_to.setToolTip(
            "Sim time the Run-to command advances to")
        self._run_to.setAccessibleName("Run-to target time")
        self._run_to_row_note = QLabel()
        self._run_to_row_note.setObjectName("SectionNote")
        form.addRow("Run to time:", self._run_to)
        form.addRow("", self._run_to_row_note)

        self._breakpoint = QComboBox()
        self._breakpoint.addItem("No breakpoint")
        self._breakpoint.setToolTip(
            "Stop stepping when this block enters Repairing (fails)")
        self._breakpoint.setAccessibleName("Breakpoint block")
        form.addRow("Breakpoint on:", self._breakpoint)
        return box

    # -- debugger parameters ---------------------------------------------

    def step_count(self) -> int:
        return int(self._step_count.value())

    def run_to_time(self) -> float:
        return float(self._run_to.value())

    def breakpoint_block(self):
        """The chosen breakpoint, or None. Read from the combo itself, so
        there is no second copy to fall out of step."""
        return self._breakpoint.currentData()

    def set_debugger_state(self, *, step_count=None, run_to=None) -> None:
        """Push the debugger parameters in from the model, without
        echoing them back as edits."""
        self._loading = True
        try:
            if step_count is not None:
                self._step_count.setValue(int(step_count))
            if run_to is not None:
                self._run_to.setValue(float(run_to))
        finally:
            self._loading = False
        self._refresh()

    def set_breakpoint_options(self, ids_and_names) -> None:
        """(id, name) pairs for the breakpoint combo, keeping the current
        selection if it survives a model load.

        The selection is read from the COMBO, not from a shadow
        attribute: an earlier version read a separate
        `_breakpoint_id` that the combo's own change never updated, so a
        re-populate always saw None and reset the choice."""
        current = self._breakpoint.currentData()
        self._breakpoint.blockSignals(True)
        self._breakpoint.clear()
        self._breakpoint.addItem("No breakpoint", None)
        for bid, name in ids_and_names:
            self._breakpoint.addItem(name, bid)
        idx = self._breakpoint.findData(current)
        self._breakpoint.setCurrentIndex(idx if idx > 0 else 0)
        self._breakpoint.blockSignals(False)

    # -- state in -------------------------------------------------------

    def set_experiment(self, exp: dict, *, enabled: bool = True) -> None:
        """Show `exp` without emitting a change back."""
        self._loading = True
        try:
            self._exp = rx.normalise(exp)
            # The profile combo is a PRESETTER, not a read-out: it has no
            # "current profile" in a model, because a profile is a set of
            # overrides applied once. Showing it as a value the user
            # might think is stored would be a lie, so it always reads
            # as "choose one to start from".
            self._trials.setValue(int(self._exp["trials"]))
            t = self._term_type.findData(self._exp["termination_type"])
            self._term_type.setCurrentIndex(max(0, t))
            self._on_term_type(update_value=True)
            self._start_stats.setValue(self._to_display(
                float(self._exp["start_stats"]), "TimeSim"))
            for key, cb in self._analysis_checks.items():
                cb.setChecked(key in self._exp["analyses"])
            for key, cb in self._recording_checks.items():
                cb.setChecked(bool(self._exp["recording"].get(key)))
            self._ao_interval.setValue(
                float(self._exp["recording"]["ao_interval"]))
            self._cost_interval.setValue(
                float(self._exp["recording"]["cost_interval"]))
            self._sync_importance_rows()
        finally:
            self._loading = False
        self.set_enabled(enabled)
        self._refresh()

    def experiment(self) -> dict:
        return rx.normalise(self._exp)

    def set_units(self, units: str, display_unit: str | None) -> None:
        """Show the time unit on the run-length and statistics rows, and
        convert the time-valued fields into it.

        Without the unit "1000.0" is ambiguous, and the spec requires
        units visible wherever a number can be misunderstood
        (the internal UI strategy notes).

        The conversion itself used to live in the Simulation Options form,
        which was a duplicate editor of these same fields and has been
        removed. It is restored HERE, on the one surface that edits run
        length and statistics start, with the same rules:

        * `StartStats` is unconditionally a sim time, so it always
          converts;
        * the run length converts only under `TimeSim`, because under
          FailSim/CycleSim the same column holds a plain COUNT -- and
          "2 years" of system failures would be nonsense. The row label
          already changes with the termination type to say which it is;
        * FailSim/CycleSim counts are never converted.

        Values are stored natively throughout; only what the spin boxes
        SHOW is converted, and every write converts back.
        """
        self._native_units = units or ""
        self._display_unit = display_unit
        self._shown_units = display_unit or units or ""
        # Re-show the converted values. Guarded so setting the units
        # during construction cannot fire the write-back signals.
        if self._loading:
            return
        experiment = self.experiment()
        conv = self._to_display
        self._term_value.blockSignals(True)
        self._start_stats.blockSignals(True)
        self._term_value.setValue(conv(experiment["term_condition"],
                                       experiment["termination_type"]))
        self._start_stats.setValue(conv(experiment["start_stats"], "TimeSim"))
        self._term_value.blockSignals(False)
        self._start_stats.blockSignals(False)
        self._refresh()

    def _commit_time_field(self, key: str, spin, termination_type: str) -> None:
        """Write a unit-converted spin box back, but only if the USER
        changed it.

        `editingFinished` also fires on a bare focus-out. The box shows the
        native value rounded to its decimals in the display unit, so
        converting that straight back would rewrite the stored number with
        rounding error (10000 h -> years -> 9999.99...) and dirty the model
        for nothing."""
        shown_now = round(spin.value(), spin.decimals())
        unchanged = round(self._to_display(
            float(self._exp[key]), termination_type), spin.decimals())
        if shown_now == unchanged:
            return
        self._emit_change(key, self._to_native(
            spin.value(), termination_type))

    def _to_display(self, native: float, termination_type: str) -> float:
        """Native -> shown, honouring the same rule the writes use."""
        from raptor.gui.ui.units_conv import native_to_display
        if not getattr(self, "_display_unit", None):
            return float(native)
        if termination_type == "TimeSim":
            return native_to_display(
                float(native), getattr(self, "_native_units", ""),
                self._display_unit)
        return float(native)

    def _to_native(self, shown: float, termination_type: str) -> float:
        """Shown -> native, the exact inverse of `_to_display`."""
        from raptor.gui.ui.units_conv import display_to_native
        if not getattr(self, "_display_unit", None):
            return float(shown)
        if termination_type == "TimeSim":
            return display_to_native(
                float(shown), getattr(self, "_native_units", ""),
                self._display_unit)
        return float(shown)

    def set_cores(self, value: int, enabled: bool, why: str, tip: str) -> None:
        """Show the cores setting; `why` says why it cannot be changed now
        (empty when it can)."""
        self._cores.blockSignals(True)
        self._cores.setValue(int(value))
        self._cores.blockSignals(False)
        self._cores.setEnabled(enabled)
        self._cores.setToolTip(tip)
        self._cores_note.setText(why)
        self._cores_note.setVisible(bool(why))

    def set_readiness(self, ok: bool, message: str, detail: str = "",
                      link=None) -> None:
        """The ready / not-ready line, with the reason (never just a
        greyed button). `detail` (all non-blocking model warnings, one per
        line) goes in the tooltip. `link`, when given, is called with the
        href of a link in `message` (rich text)."""
        # One handler at a time; disconnect only what was connected (a bare
        # disconnect with nothing attached prints a RuntimeWarning).
        old = getattr(self, "_readiness_link", None)
        if old is not None:
            self._readiness.linkActivated.disconnect(old)
            self._readiness_link = None
        if link is not None:
            self._readiness.setTextFormat(Qt.TextFormat.RichText)
            self._readiness.linkActivated.connect(link)
            self._readiness_link = link
        else:
            self._readiness.setTextFormat(Qt.TextFormat.PlainText)
        self._readiness.setText(message)
        self._readiness.setToolTip(detail)
        c = active_colour()
        colour = c.get("operating") if ok else c.get("failed")
        self._readiness.setStyleSheet(
            f"QLabel {{ color: {colour}; font-weight: 600; }}")

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable every control.

        Called with False while a run is in progress. This is the
        run-time-edit safety the review called out: a 20-minute batch
        must not have its own settings changed underneath it, because
        "what did that run use?" with a different answer than the panel
        shows is the worst outcome available in this domain.

        The debugger parameters stay editable: they take effect on the
        NEXT step press, and a session in progress is stepped by hand, so
        locking them would prevent the very use they exist for."""
        self._enabled = enabled
        for w in (self._profile, self._trials, self._term_type,
                  self._term_value, self._start_stats,
                  self._ao_interval, self._cost_interval,
                  self._importance_check, self._importance_mode,
                  *self._analysis_checks.values(),
                  *self._recording_checks.values()):
            w.setEnabled(enabled)
        self._refresh()

    def is_enabled(self) -> bool:
        return self._enabled

    # -- state out ------------------------------------------------------

    def _emit_change(self, key, value) -> None:
        if self._loading or not self._enabled:
            return
        experiment = self.experiment()
        if experiment.get(key) == value:
            return
        experiment[key] = value
        self._exp = experiment
        self._refresh()
        self.experiment_changed.emit(experiment)

    def _on_profile(self) -> None:
        if self._loading or not self._enabled:
            return
        name = self._profile.currentData()
        self._exp = rx.apply_profile(name, self._exp)
        self.set_experiment(self._exp)
        self.experiment_changed.emit(self.experiment())

    def _on_term_type(self, update_value: bool = False) -> None:
        """Relabel the run-length row for the chosen termination, and
        re-interpret its number.

        Run length is a sim TIME under TimeSim but a plain COUNT under
        FailSim/CycleSim, so the label and the unit both change. The
        same switch already exists in Sim Options; this is the panel's
        copy of that rule."""
        t = self._term_type.currentData()
        label = {"TimeSim": "Run length:", "FailSim": "System failures:",
                 "CycleSim": "Phase cycles:"}.get(t, "Stop at:")
        self._term_label.setText(label)
        if update_value:
            # Re-shown converted, and with the new type's rule: the same
            # stored number is a time under TimeSim and a plain count
            # under FailSim/CycleSim.
            self._term_value.setValue(self._to_display(
                float(self._exp["term_condition"]),
                self._exp["termination_type"]))
        elif not self._loading and self._enabled:
            # A USER change must reach the model. This used to only
            # relabel the field, so the combo said FailSim while the row,
            # the engine and the readiness line still said TimeSim.
            self._emit_change("termination_type", t)
            self._on_term_type(update_value=True)
            return
        self._refresh()

    def _on_analysis(self, key: str, on: bool) -> None:
        if self._loading or not self._enabled:
            return
        experiment = self.experiment()
        analyses = [a for a in experiment["analyses"] if a != key]
        if on:
            analyses.append(key)
        experiment["analyses"] = analyses
        # Turning an analysis off must not leave a dependent plot ticked:
        # that combination is the unenforced dependency the review found.
        for plot_key, (need, _label) in rx.RECORDING_REQUIRES.items():
            if need == key and not on and experiment["recording"].get(plot_key):
                experiment["recording"][plot_key] = False
                self.dependency_blocked.emit(plot_key, _label)
        self.set_experiment(experiment)
        self.experiment_changed.emit(self.experiment())

    def _on_recording(self, key: str, on: bool) -> None:
        if self._loading or not self._enabled:
            return
        need = rx.RECORDING_REQUIRES.get(key)
        if on and need:
            want, label = need
            if want not in self.experiment()["analyses"]:
                # Refuse in the widget AND say why, instead of accepting
                # a combination that yields an empty plot.
                self._recording_checks[key].blockSignals(True)
                self._recording_checks[key].setChecked(False)
                self._recording_checks[key].blockSignals(False)
                self.dependency_blocked.emit(key, label)
                self._refresh()
                return
        experiment = self.experiment()
        experiment["recording"][key] = on
        if key == "ao_plot" and on:
            # A 10-unit default sample interval is meaningless on a
            # 1-unit mission. Seed a reasonable interval when the current
            # one cannot fit, unless the user has already chosen a
            # smaller one.
            length = float(experiment["term_condition"])
            current = float(experiment["recording"]["ao_interval"])
            default_interval = float(
                rx.RUN_DEFAULTS["recording"]["ao_interval"])
            # Only replace the factory default. A custom interval is the
            # user's choice: if it is too long, the warning tells them
            # where to change it rather than silently rewriting it.
            if experiment["termination_type"] == "TimeSim" and length > 0.0 \
                    and current >= length \
                    and abs(current - default_interval) < 1e-12:
                experiment["recording"]["ao_interval"] = max(length / 100.0,
                                                     0.001)
                self._ao_interval.blockSignals(True)
                self._ao_interval.setValue(
                    experiment["recording"]["ao_interval"])
                self._ao_interval.blockSignals(False)
        self._exp = experiment
        self._refresh()
        self.experiment_changed.emit(experiment)

    def _on_ao_interval(self) -> None:
        if self._loading or not self._enabled:
            return
        experiment = self.experiment()
        experiment["recording"]["ao_interval"] = self._ao_interval.value()
        self._exp = experiment
        self.experiment_changed.emit(experiment)

    def _on_cost_interval(self) -> None:
        if self._loading or not self._enabled:
            return
        experiment = self.experiment()
        experiment["recording"]["cost_interval"] = self._cost_interval.value()
        self._exp = experiment
        self.experiment_changed.emit(experiment)

    # -- derived display -------------------------------------------------

    def _refresh(self) -> None:
        """Re-derive everything that depends on the current values."""
        experiment = self._exp
        shown = getattr(self, "_shown_units", "")
        t = experiment["termination_type"]
        if t == "TimeSim" and shown:
            self._unit_note.setText(
                f"Times in {shown}. Statistics must start before the run "
                f"ends.")
        else:
            self._unit_note.setText(
                "Statistics start is only meaningful for a time-terminated "
                "run.")
        # Warn inline rather than blocking with a modal: the spec asks
        # for a field-level message plus a fix, not an interruption
        # (the internal UI blueprint).
        # DELEGATED (review E-15): same predicate, same wording, one owner.
        bad = rx.stats_start_after_run_length(experiment)
        self._start_stats.setToolTip(rx.stats_start_tooltip(experiment))
        self._start_stats.setStyleSheet(
            f"QDoubleSpinBox {{ border: 2px solid {active_colour()['failed']}; }}"
            if bad else "")

        # Dependent plots: disabled AND explained while their analysis is
        # off, rather than merely annotated in a tooltip.
        for key, (need, label) in rx.RECORDING_REQUIRES.items():
            has = need in experiment["analyses"]
            cb = self._recording_checks.get(key)
            if cb is None:
                continue
            cb.setEnabled(bool(self._enabled and has))
            note = self._recording_notes.get(key)
            if note is not None:
                note.setText(
                    f"Needs the {label} analysis" if not has
                    else self._recording_hints.get(key, ""))
        # Sampling intervals follow their plot.
        self._ao_interval.setEnabled(
            bool(self._enabled and experiment["recording"].get("ao_plot")))
        self._cost_interval.setEnabled(
            bool(self._enabled and experiment["recording"].get("cost_plot")))


class RunPanelDock(QDockWidget):
    """The panel as a dockable, tabbable widget."""

    def __init__(self, parent=None):
        super().__init__("Run", parent)
        self.setObjectName("RunDock")
        self.panel = RunPanel(self)
        self.setWidget(self.panel)
        # A splitter-friendly width, not a hard-coded minimum: the app
        # already reserves 260 (Explorer) and 340 (Inspector) and the spec
        # requires 100/150/200% scaling with no clipping
        # (`blueprint:676`), which a third fixed pixel minimum works
        # against. NB: the 0 height keeps `setMinimumWidth`'s Qt
        # internals from pairing it with the unset `-1` sentinel.
        self.setMinimumSize(240, 0)
        # The dock filled the whole Simulate tab and left a stretch of empty
        # space under the last section. Cap it to the content's height and
        # lift the cap when it is floating (there the user owns the size).
        self.panel.size_changed.connect(self.fit_height)
        self.fit_height()

    def fit_height(self) -> None:
        """Match the dock's height to what the panel actually shows.

        The cap is the content plus the title bar. It has to be the
        content's own height and not the dock's `sizeHint()`: the dock
        sits in a tab group whose height Qt sizes from the tallest tab,
        so an inflated hint here is what left the empty field under the
        last section.

        A tab that is not the visible one does not constrain the group --
        otherwise a short Run tab would squeeze the Results table and a
        tall one would re-open the gap -- so the cap only applies while
        this dock is the tab on show.
        """
        try:
            hidden = self.isHidden()
        except RuntimeError:           # C++ object gone (window closing)
            return
        if self.isFloating() or hidden:
            self.setMaximumHeight(16777215)
            return
        tb = self.titleBarWidget()
        chrome = 0 if tb is None else max(tb.height(), tb.sizeHint().height())
        want = max(120, self.panel.content_height() + chrome)
        if want != self.maximumHeight():
            self.setMaximumHeight(want)
        # A cap alone only stops the dock being TOO TALL. `_give_canvas_room`
        # sets the shared bottom strip to 150 px at startup, so with a model
        # open the panel was clipped to 132 px of 295 and scrolled -- the
        # inverse fault, and what "not the right height yet" was seeing.
        # Grow the strip to the content when it is shorter, never shrink it:
        # a user who has dragged it taller keeps their size.
        if not getattr(self, "_growing", False) and self.height() < want:
            self._grow_to(want)

    def _grow_to(self, want: int) -> None:
        """Ask the window for a taller bottom strip (one shared strip)."""
        win = self.window()
        if not isinstance(win, QMainWindow):
            return
        if win.dockWidgetArea(self) == Qt.DockWidgetArea.NoDockWidgetArea:
            return          # not in the layout: resizeDocks would only warn
        self._growing = True
        try:
            # `resizeDocks` size is the dock area's, which includes the
            # tab strip above a tabified group; `want` is the dock's own
            # content + title bar. Ask for both, or growing from a
            # small strip lands exactly one tab strip short and the
            # panel scrolls (the reported Simulate-tab bug).
            strip = 0
            if isinstance(win, QMainWindow) and win.tabifiedDockWidgets(self):
                for tb in win.findChildren(QTabBar):
                    if tb.parentWidget() is win and tb.isVisible():
                        strip = tb.height()
                        break
            win.resizeDocks([self], [want + strip], Qt.Orientation.Vertical)
        except Exception:                                  # noqa: BLE001
            pass
        finally:
            self._growing = False

    def showEvent(self, event):  # noqa: N802 (Qt spelling)
        super().showEvent(event)
        self.fit_height()

    def set_experiment(self, exp, *, enabled: bool = True) -> None:
        self.panel.set_experiment(exp, enabled=enabled)

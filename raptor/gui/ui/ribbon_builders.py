"""Composite ribbon controls: the parts that are not a plain button.

`command_table.py` describes every plain command as data. What is left is
controls that are more than one action -- a dropdown with a menu, a combo,
a spin box with a label, a two-widget row -- and state the ribbon code used
to create as a side effect. They live here as named methods, lifted from
`MainWindow._setup_ribbon` unchanged, so the layout table can refer to them
by name (`Custom("_build_align_button")`) and the window can shrink.

A mixin, not a class of its own: these reach a dozen pieces of window
state, and an injected interface would have moved that coupling rather
than removed it (the same call `run_experiment_mixin.py` made).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtGui import QAction, QUndoStack
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QSpinBox, QWidget,
)

from raptor.gui.resources import icons
from raptor.gui.resources.design_tokens import RIBBON, active_colour
from raptor.gui.ui.run_experiment_mixin import _ANALYSIS_TIPS


def _labeled_box(text: str, widget: QWidget, label_width: int = 0) -> QWidget:
    """A ribbon input with its label in front ("Trials: [10]"), sized to
    one small row. `label_width` lines up a stack of them."""
    box = QWidget()
    box.setObjectName("RibbonField")
    box.setFixedHeight(RIBBON["small_height"])
    lay = QHBoxLayout(box)
    lay.setContentsMargins(4, 0, 2, 0)
    lay.setSpacing(4)
    lbl = QLabel(text)
    lbl.setObjectName("RibbonFieldLabel")
    if label_width:
        lbl.setFixedWidth(label_width)
    lay.addWidget(lbl)
    widget.setFixedHeight(RIBBON["small_height"] - 2)
    lay.addWidget(widget)
    return box


if TYPE_CHECKING:
    # For the type checker only: the mixin is used as `self` where Qt wants
    # a parent object. At runtime it must stay a plain mixin, or the MRO of
    # `MainWindow(RibbonBuilders, RunExperimentMixin, QMainWindow)` breaks.
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class RibbonBuilders(_Base):
    """Mixin for `MainWindow`: builders referenced by the layout table."""

    if TYPE_CHECKING:
        # The mixin uses window state it does not declare (the same is true
        # of `RunExperimentMixin`); this stops mypy calling each of them an
        # error without hiding the ones in code that IS typed.
        def __getattr__(self, name: str) -> Any: ...

    def _init_edit_and_run_state(self) -> None:
        """State the ribbon code used to create as a side effect: the undo
        stack that the Undo / Redo actions wrap, and the run / step /
        export flags the other groups read."""
        self._undo_stack = QUndoStack(self)
        # Analyses live in the Run panel now; keep the dict present so
        # every sync path can treat the ribbon mirror as optional.
        self._analysis_actions: dict[str, QAction] = {}
        # Model history and the run experiment/mirrors must move
        # together. Pushing a command edits the row; undo/redo edits the
        # row back, so re-derive the experiment from the row after every
        # history change (deferred, so a macro is fully pushed/popped
        # first).
        self._undo_stack.indexChanged.connect(
            self._schedule_settings_resync)
        self._step_session = None
        self._step_sink: list = []
        self._edit_during_run_guard = False
        self._edit_guard_reentrancy = False
        self._edit_guard_prompt_times: list[float] = []
        self._edit_run_abandoned = False
        self._bg_thread = None
        self._bg_state: dict = {}
        # True for the whole lifetime of a sync or background run --
        # _sync_simulate_enabled_state (the periodic validation-timer
        # callback) checks this so it doesn't clobber the run/pause/stop
        # actions' own enabled state mid-run.
        self._run_in_progress = False
        #: True while a print/PDF export is pumping the event loop
        #: (`report_pdf`); File lifecycle actions are refused then too.
        self._export_in_progress = False
        self._fev_status_dialog = None
        self._fev_status_text = None



    def _build_goto_combo(self, group) -> None:
        """Model > Hierarchy: jump straight to a hierarchy by name."""
        # The manual's "Goto Hierarchy drop down list box" (App. B
        # Navigate menu): jump straight to any hierarchy by name.
        self._goto_combo = QComboBox()
        self._goto_combo.setToolTip("Go to a hierarchy")
        # Without a placeholder this reads as a blank, broken widget; and
        # after a refresh it used to sit on the first hierarchy in the
        # list, implying that is where you are.
        self._goto_combo.setPlaceholderText("Go to hierarchy…")
        self._goto_combo.setAccessibleName("Go to hierarchy")
        self._goto_combo.setFixedWidth(110)
        self._goto_combo.setFixedHeight(RIBBON["small_height"] - 2)
        self._goto_combo.setEnabled(False)
        self._goto_combo.currentIndexChanged.connect(self._on_goto_hier)
        group.add_widget(self._goto_combo)

    def _build_pool_buttons(self, group) -> None:
        """Data > Reliability Data: one direct button per section."""
        self._pool_actions: dict[str, QAction] = {}
        for sec, label, tip in (
                ("SPARE_POOL_DATA", "Spare Pools…",
                 "Spare Pools — stock levels, arrival cadence, "
                 "emergency shipping"),
                ("RESOURCE_DATA", "Resources…",
                 "Resource Pools — shared crew/equipment, cost per use"),
                ("TRIGGERS", "Triggers…",
                 "Triggers — schedules Preventive Maintenance (PM)")):
            action = QAction(label, self)
            action.setToolTip(tip)
            action.triggered.connect(
                lambda _c=False, s2=sec: self._open_pool_dialog(s2))
            action.setEnabled(False)
            group.add_action(action)
            self._pool_actions[sec] = action
            self._commands.track(action, "model_open")

    def _build_rates_button(self, group) -> None:
        """Data > Reliability Data: the Rates library."""
        self._rates_action = QAction(
            icons.rates_icon(active_colour()["text"]), "Rates…", self)
        self._rates_action.setToolTip(
            "Rates library — named, reusable value expressions (e.g. a "
            "failure rate derived from km/yr usage) that Failure/Repair "
            "Param fields can link to instead of retyping the same "
            "derivation on every block")
        self._rates_action.triggered.connect(self._open_rates_dialog)
        self._rates_btn = group.add_action(self._rates_action, large=True)
        self._commands.track(self._rates_action, "model_open")



    def _build_align_button(self, group) -> None:
        """Arrange > Align: one dropdown of alignment commands."""
        self._align_action = QAction(
            icons.align_hcenter_icon(active_colour()["accent"]), "Align", self)
        self._align_action.setToolTip(
            "Align or evenly distribute the selected elements and "
            "annotations (select 2+ to align, 3+ to distribute)")
        amenu = QMenu(self)

        def v_align(mode):
            self._diagram_dock.view.align_selected(mode)
        def v_distribute(axis):
            self._diagram_dock.view.distribute_selected(axis)

        # (icon-fn name, label, callback) -- kept on self so _apply_theme
        # can repaint every icon on a theme switch, same as every other
        # ribbon glyph.
        self._align_menu_actions = []
        for icon_fn, lbl, mode in (
                ("align_left_icon", "Align Left", "left"),
                ("align_right_icon", "Align Right", "right"),
                ("align_top_icon", "Align Top", "top"),
                ("align_bottom_icon", "Align Bottom", "bottom"),
                ("align_hcenter_icon", "Centre Horizontally", "hcenter"),
                ("align_vcenter_icon", "Centre Vertically", "vcenter")):
            act = amenu.addAction(lbl, lambda m=mode: v_align(m))
            self._align_menu_actions.append((icon_fn, act))
        amenu.addSeparator()
        for icon_fn, lbl, axis in (
                ("distribute_h_icon", "Distribute Horizontally", "h"),
                ("distribute_v_icon", "Distribute Vertically", "v")):
            act = amenu.addAction(lbl, lambda a=axis: v_distribute(a))
            self._align_menu_actions.append((icon_fn, act))
        self._align_action.setMenu(amenu)
        # Tracked on the ACTION: a button tracked directly is re-enabled
        # from its action whenever the action changes (an icon repaint).
        self._commands.track(self._align_action, "can_align")
        self._align_btn = group.add_action(self._align_action, large=True)

    def _build_trials_box(self, group) -> None:
        """Simulate > Run: the trial count, editable beside Run.

        It was a read-only read-out (the value was once editable in three
        places that drifted apart). There is one writer now,
        `_set_exp_field`; this box and the Run panel are both its callers
        and both follow the setting, so editing it here cannot drift --
        and a read-only number beside the Run button read as broken."""
        self._run_count_spin = QSpinBox()
        self._run_count_spin.setRange(1, 1_000_000)
        self._run_count_spin.setValue(1)
        self._run_count_spin.setGroupSeparatorShown(True)
        self._run_count_spin.setFixedWidth(84)
        self._run_count_spin.setAccessibleName("Trials")
        self._run_count_spin.setToolTip(
            "Trials the next run will use. Edit them in the Run panel "
            "(Simulate ▸ Run Settings), which owns every run setting.")
        # Read-only mirror: the Run panel is the single editor. This
        # spinner used to be a second writer with its own undo path.
        self._run_count_spin.setReadOnly(True)
        self._run_count_spin.setButtonSymbols(
            QSpinBox.ButtonSymbols.NoButtons)
        self._run_count_spin.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        group.add_widget(_labeled_box("Trials", self._run_count_spin))

    def _build_cores_box(self, group) -> None:
        """Simulate > Run: how many cores a full-speed run uses, in plain
        sight beside Run ("8 of 8"). It used to be a preference, default 1,
        that only a background run with Animate off obeyed -- so nobody's
        runs used more than one core."""
        from raptor.gui import cores
        self._cores_spin = QSpinBox()
        self._cores_spin.setRange(1, cores.available())
        self._cores_spin.setValue(cores.chosen())
        self._cores_spin.setSuffix(f" of {cores.available()}")
        self._cores_spin.setFixedWidth(84)
        self._cores_spin.setAccessibleName("Cores for full-speed runs")
        # Cores is edited in the Run panel; this ribbon box is a
        # read-only readout.
        self._cores_spin.setReadOnly(True)
        self._cores_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._cores_spin.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        group.add_widget(_labeled_box("Cores", self._cores_spin))
        self._sync_cores_box()

    def _on_cores_changed(self, n: int) -> None:
        from raptor.gui import cores
        from raptor.gui.ui import settings
        settings.set_value(cores.KEY, int(n))
        state = getattr(self, "_bg_state", None)
        if state and not state.get("done") and int(state.get("workers", 1)) > 1:
            # A multi-core batch is running: give cores back (or take them
            # again, up to the number it started with) without restarting.
            state["workers_now"] = max(1, min(int(n), int(state["workers"])))
        self._sync_cores_box()
        if hasattr(self, "_update_readiness"):
            self._update_readiness()

    def _sync_cores_box(self) -> None:
        """The ribbon's Cores box and the Run panel's Cores field, one
        setting. Animate runs on one core, and a running batch keeps its
        cores, so both grey out then and say why."""
        from raptor.gui import cores
        animate = bool(getattr(self, "_live_sim_action", None)
                       and self._live_sim_action.isChecked())
        running = bool(getattr(self, "_run_in_progress", False))
        state = getattr(self, "_bg_state", None)
        # A multi-core background batch can be told to use fewer cores (or
        # more again, up to what it started with) while it runs.
        adjustable = bool(running and state and not state.get("done")
                          and int(state.get("workers", 1)) > 1)
        chip = getattr(self, "_cores_chip", None)
        if chip is not None:
            n = 1 if animate else cores.chosen()
            chip.setText(f"{n}/{cores.available()} cores"
                         + (" (Animate)" if animate else ""))
            chip.setToolTip(cores.describe(animate)
                            + ". Change it in the Run panel.")
        started_with = int(state.get("workers", 1)) if state else 1
        why = ("Animate is on: an animated run uses one core." if animate
               else f"Takes effect while the run is going (up to {started_with})."
               if adjustable
               else "A run is using these cores." if running else "")
        tip = (
            "Animate is on: an animated run uses one core, so you can watch "
            "each event. Turn Animate off to run at full speed on these "
            "cores." if animate else
            f"Lower this to give cores back to the machine while the run "
            f"goes on: it takes effect within a run or two and the results "
            f"do not change. Raise it again up to the {started_with} the run "
            f"started with. It is also your setting for the next run."
            if adjustable else
            "A run is using these cores. Change the number when it has "
            "finished; it applies to the next run." if running else
            f"A full-speed run splits its trials across this many cores "
            f"(this machine has {cores.available()}). Sweep and Compare use "
            f"them too. Any number from 2 up gives the same results; one "
            f"core gives Raptor 7's exact sequence, statistically the same "
            f"answer but not bit-identical.")
        spin = getattr(self, "_cores_spin", None)
        if spin is not None:
            spin.blockSignals(True)
            spin.setValue(cores.chosen())
            spin.blockSignals(False)
            spin.setEnabled(not animate and not running)   # the read-out
            spin.setToolTip(tip)
        panel = getattr(self, "_run_panel", None)
        if panel is not None:
            panel.set_cores(cores.chosen(),
                            not animate and (not running or adjustable),
                            why, tip)

    def _build_step_menu(self, group) -> None:
        """Debug > Step: the More Steps menu (next change, multi-step, run to time)."""
        # Debugger parity with legacy Raptor (pause/step/jump): multi-step,
        # run-to-time jump, and a stop-on-block-failure breakpoint. The
        # actions are built here (unchanged) but placed in the More Steps
        # menu rather than the ribbon, since each needs a parameter that
        # now lives in the Run panel.
        self._step_count_spin = QSpinBox()
        self._step_count_spin.setRange(1, 100_000)
        self._step_count_spin.setValue(10)
        self._step_count_spin.setToolTip("Events per Multi step")
        self._step_multi_action = QAction(
            icons.step_multi_icon(active_colour()["accent"]),
            f"Multi ({self._step_count_spin.value()})…", self)
        self._step_multi_action.setToolTip(
            "Advance the number of events set in the Run panel "
            "(stops early at the breakpoint)")
        self._step_multi_action.triggered.connect(self._on_step_multi)
        self._step_multi_action.setEnabled(False)
        self._commands.track(self._step_multi_action, "can_simulate")

        self._step_change_action = QAction("Step to next change", self)
        self._step_change_action.setToolTip(
            "Step to the next change: run on until an instant in which something changed state "
            "has been analysed, then pause (Step itself advances one calendar entry)")
        self._step_change_action.triggered.connect(self._on_step_change)
        self._step_change_action.setEnabled(False)
        self._commands.track(self._step_change_action, "can_simulate")

        self._run_to_time_spin = QDoubleSpinBox()
        self._run_to_time_spin.setDecimals(3)
        self._run_to_time_spin.setRange(0.0, 1e12)
        self._run_to_time_spin.setValue(100.0)
        self._run_to_time_spin.setToolTip("Target sim time for Run To")
        # Label carries the model's own time units once a file is open
        # (e.g. "Hours:") -- see `_load_into_app`'s units-chip refresh,
        # which updates this same label; falls back to "Time:" with none
        # loaded yet.
        self._run_to_label = QLabel("Time:")
        self._run_to_label.setObjectName("SectionNote")

        self._breakpoint_combo = QComboBox()
        self._breakpoint_combo.addItem("No breakpoint")
        self._breakpoint_combo.setToolTip(
            "Stop stepping when this block enters Repairing (fails)")
        self._breakpoint_combo.setToolTip(
            "Stop stepping when the chosen block fails. Chosen in the Run "
            "panel's Debugger group")

        # More Steps: the ways to advance that are not the default
        # one-at-a-time click. Each is a verb whose PARAMETER now lives in
        # the Run panel, so the menu item says which parameter it uses
        # rather than pretending to be self-contained. The spec sanctions
        # exactly this: "Less common commands live in galleries"
        # (the internal UI strategy notes).
        self._step_menu = QMenu(self)
        self._step_menu.addAction(self._step_change_action)
        self._step_menu.addAction(self._step_multi_action)
        self._step_menu.addAction(self._run_to_action)
        self._step_menu_action = QAction(
            icons.step_multi_icon(active_colour()["accent"]), "More Steps",
            self)
        self._step_menu_action.setToolTip(
            "Other ways to advance: to the next change, several events at "
            "once, or run to a target time")
        self._step_menu_action.setMenu(self._step_menu)
        self._step_menu_btn = group.add_action(self._step_menu_action)
        self._step_menu_btn.setAccessibleName(
            "More steps — other ways to advance")

    def _build_speed_controls(self, group) -> None:
        """Simulate > Animation: the speed dropdown and read-out."""
        # Sim speed (manual B-11 Increase/Decrease Sim Speed): log-scale
        # slower/faster with resets to 1 ev/s and Max.
        #
        # Raptor 7's "time slice" is a DISPLAY pacing control, not a
        # compute throttle, and the source is unambiguous about it: its
        # label is "1 sec of real time is approximately N units of
        # simulation time", it lives in a
        # column named `graphTimeSlice`, and Raptor 7
        # DEACTIVATES it whenever graphics are off -- then forces
        # maximum speed (`timeSlice := 0.0000001`).
        #
        # So: speed only means anything when the user is watching an
        # animation. The dial is therefore gated on Animate and the run
        # paths force full speed when animation is off -- see
        # `_effective_event_delay`.
        self._speed_state = {"delay": None}
        # The four dials go in ONE dropdown rather than four ribbon
        # buttons: they are a single "animation speed" value, and four
        # separate targets for a logarithmic value is the wrong shape --
        # a menu can show the CURRENT rate in each item, which four bare
        # buttons cannot.
        _speed_menu = QMenu(self)
        for a in (self._speed_slower, self._speed_reset_1,
                  self._speed_faster, self._speed_reset_max):
            _speed_menu.addAction(a)
        self._speed_action = QAction(
            icons.stopwatch_icon(active_colour()["text"]), "Speed", self)
        self._speed_action.setToolTip(
            "How fast an animated run is drawn. Applies only while "
            "Animate is on")
        self._speed_action.setMenu(_speed_menu)
        self._speed_btn = group.add_action(self._speed_action)
        self._speed_btn.setAccessibleName("Animation speed")
        # The current speed, under the dial.
        self._speed_label = QLabel("Speed: max")
        self._speed_label.setObjectName("RibbonFieldLabel")
        self._speed_label.setFixedHeight(RIBBON["small_height"])
        self._speed_label.setContentsMargins(6, 0, 4, 0)
        group.add_widget(self._speed_label)
        # Animate is checked at construction, so this is the initial
        # enablement of the dial (idle: nothing is animating, but the
        # dial stays enabled so a user can pre-set the rate before
        # pressing Run -- `_set_speed_state_enabled` narrows it only
        # when Animate is OFF, where speed is meaningless by the
        # original's own rule).
        self._speed_slower.setEnabled(True)

    def _build_fev_log_button(self, group) -> None:
        """Debug > Failure Effects: the Interactive Log menu."""
        # Raptor 7 "Interactive Log": append the stepped session's system
        # status summary to a text file as often as the user wants;
        # closes automatically when the session ends.
        self._fev_log_menu = QMenu(self)
        self._fev_log_open_action = self._fev_log_menu.addAction(
            "Open Interactive Log…")
        self._fev_log_write_action = self._fev_log_menu.addAction(
            "Write to Log")
        self._fev_log_close_action = self._fev_log_menu.addAction(
            "Close Interactive Log")
        self._fev_log_write_action.setEnabled(False)
        self._fev_log_close_action.setEnabled(False)
        self._fev_log_open_action.triggered.connect(
            self._on_fev_log_open)
        self._fev_log_write_action.triggered.connect(
            self._on_fev_log_write)
        self._fev_log_close_action.triggered.connect(
            self._on_fev_log_close)
        self._fev_log_action = QAction(
            icons.log_icon(active_colour()["text"]), "Log", self)
        self._fev_log_action.setToolTip(
            "Interactive Log: write this session's system "
            "status summary to a text file")
        self._fev_log_action.setMenu(self._fev_log_menu)
        self._fev_log: dict | None = None
        self._commands.track(self._fev_log_action, "fev_active")
        self._fev_log_btn = group.add_action(self._fev_log_action)

    def _build_hidden_analysis_actions(self) -> None:
        """Create the analysis mirror actions without a ribbon group.

        The Run panel owns the three analysis switches. These actions are
        the one internal representation every sync/test path already
        uses; keeping them (unparented to any layout) avoids a second
        visible editor while preserving that API.
        """
        from raptor.gui.ui.main_window import _ANALYSIS_ICONS
        from raptor.gui.ui.run_experiment_mixin import _ANALYSIS_TIPS
        for col, label in (
                ("weakAnalysis", "Weak Link"),
                ("costAnalysis", "Cost"),
                ("capacityAnalysis", "Capacity")):
            act = QAction(getattr(icons, _ANALYSIS_ICONS[col])(
                active_colour()["accent"]), label, self)
            act.setCheckable(True)
            act.setToolTip(_ANALYSIS_TIPS[col])
            act.toggled.connect(
                lambda on, c=col, l=label:
                self._set_analysis_flag(c, on, l))
            self._analysis_actions[col] = act

    def _build_analysis_toggles(self, group) -> None:
        """Simulate > Analyses: the three analysis toggles."""
        self._analysis_actions: dict[str, QAction] = {}
        from raptor.gui.ui.main_window import _ANALYSIS_ICONS
        for col, label in (
            ("weakAnalysis", "Weak Link"),
            ("costAnalysis", "Cost"),
            ("capacityAnalysis", "Capacity")):
            act = QAction(getattr(icons, _ANALYSIS_ICONS[col])(
                active_colour()["accent"]), label, self)
            act.setCheckable(True)
            # Tooltip from the shared table rather than spelled out again
            # here: `_set_analysis_toggles_enabled` re-templates these on
            # every run's end, and a second copy of the text would leave
            # the control describing the locked state until the next run.
            act.setToolTip(_ANALYSIS_TIPS[col])
            act.toggled.connect(
                lambda on, c=col, l=label:
                self._set_analysis_flag(c, on, l))
            group.add_action(act)
            self._analysis_actions[col] = act

    def _build_block_labels_settings(self, group) -> None:
        """View > Appearance: what the canvas writes under a block name,
        and which phase's law it describes.

        Both are view settings, not model data: changing either repaints
        the labels and leaves the run untouched.
        """
        from raptor.gui.ui import block_labels

        self._block_label_combo = QComboBox()
        for key, label in block_labels.MODES:
            self._block_label_combo.addItem(label, key)
        self._block_label_combo.setAccessibleName("Block labels")
        self._block_label_combo.setToolTip(
            "What the small lines under each block name show: the mean, "
            "the mean and standard deviation, the failure/repair rate, "
            "or nothing")
        self._block_label_combo.setFixedWidth(118)
        self._block_label_combo.currentIndexChanged.connect(
            lambda _i: self._on_block_label_mode_changed())

        self._block_phase_combo = QComboBox()
        self._block_phase_combo.setAccessibleName("Show phase on block labels")
        self._block_phase_combo.setToolTip(
            "Which phase's law the block labels describe. 'Model' shows "
            "the model's own unphased data; a phase shows that phase's "
            "stress-adjusted failure time, or that the block is cut.")
        self._block_phase_combo.setFixedWidth(190)
        self._block_phase_combo.currentIndexChanged.connect(
            lambda _i: self._on_block_label_phase_changed())

        self._sync_block_label_controls()
        for text, widget in (("Labels", self._block_label_combo),
                             ("Show Phase", self._block_phase_combo)):
            group.add_widget(_labeled_box(text, widget, label_width=76))

    def _sync_block_label_controls(self) -> None:
        """Match the combos to the model that is open and the saved mode."""
        if not hasattr(self, "_block_label_combo"):
            return
        from raptor.gui.ui import block_labels
        from raptor.gui.ui import settings as app_settings

        mode = str(app_settings.get("appearance/block_labels",
                                    block_labels.DEFAULT_MODE))
        self._block_label_combo.blockSignals(True)
        idx = self._block_label_combo.findData(mode)
        if idx >= 0 and idx != self._block_label_combo.currentIndex():
            self._block_label_combo.setCurrentIndex(idx)
        self._block_label_combo.blockSignals(False)

        combo = self._block_phase_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Model", -1)
        rbd = getattr(self, "_rbd", None)
        sec = rbd.sections.get("PHASES") if rbd is not None else None
        phase_rows = list(getattr(sec, "rows", []) or [])
        for i, row in enumerate(phase_rows):
            name = row.get("PhaseName") or f"Phase {i + 1}"
            combo.addItem(f"P{i + 1} {name}", i)
        combo.setEnabled(bool(phase_rows))
        combo.setToolTip(
            "Which phase's law the block labels describe. 'Model' shows "
            "the model's own unphased data; a phase shows that phase's "
            "stress-adjusted failure time, or that the block is cut. "
            "Disabled when the model has no phases.")
        view = getattr(getattr(self, "_diagram_dock", None), "view", None)
        phase = getattr(view, "_label_phase", -1)
        idx = combo.findData(phase)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _on_block_label_mode_changed(self) -> None:
        from raptor.gui.ui import settings as app_settings
        key = self._block_label_combo.currentData()
        app_settings.set_value("appearance/block_labels", key)
        view = getattr(getattr(self, "_diagram_dock", None), "view", None)
        if view is not None:
            view.refresh_block_labels()

    def _on_block_label_phase_changed(self) -> None:
        view = getattr(getattr(self, "_diagram_dock", None), "view", None)
        if view is None:
            return
        view.set_label_phase(int(self._block_phase_combo.currentData()))

    def _build_weak_link_settings(self, group) -> None:
        """Results > Weak Link: metric and G/Y, Y/R thresholds."""
        self._weak_type_combo = QComboBox()
        self._weak_type_combo.addItem("Availability")
        self._weak_type_combo.addItem("Dependability")
        self._weak_type_combo.addItem("Reliability")
        self._weak_type_combo.setAccessibleName("Weak Link metric")
        self._weak_type_combo.setToolTip(
            "Which metric the Weak Link bands use: dependability "
            "excludes standby/PM/dependency time")
        self._weak_type_combo.currentTextChanged.connect(
            lambda t: self._on_weak_type_changed(t))
        self._weak_type_combo.setFixedWidth(118)
        self._gy_spin = QDoubleSpinBox()
        # Six decimals: a highly available model's blocks differ in the
        # fourth (0.99968 vs 0.99961), which three could not set apart.
        self._gy_spin.setRange(0.0, 1.0); self._gy_spin.setDecimals(6)
        self._gy_spin.setSingleStep(0.01); self._gy_spin.setValue(0.95)
        self._gy_spin.setAccessibleName("Green/Yellow threshold")
        self._gy_spin.setToolTip(
            "Green/Yellow band: elements at or above this metric are "
            "Green")
        self._yr_spin = QDoubleSpinBox()
        self._yr_spin.setRange(0.0, 1.0); self._yr_spin.setDecimals(6)
        self._yr_spin.setSingleStep(0.01); self._yr_spin.setValue(0.90)
        self._yr_spin.setAccessibleName("Yellow/Red threshold")
        self._yr_spin.setToolTip(
            "Yellow/Red band: elements below this metric are Red")

        def commit_bands(_value: float | None = None) -> None:
            """Write a user edit; this is the canonical band editor.

            The two spin boxes used to be decorative: nothing was
            connected, so the value changed on screen and nowhere else.
            Ordering is checked here because the row rules reject
            YR >= GY and a rejected command must leave the widget on the
            model's value.
            """
            gy, yr = self._gy_spin.value(), self._yr_spin.value()
            if not (0.0 <= yr < gy <= 1.0):
                self._statusbar.showMessage(
                    "Green/Yellow must be above Yellow/Red and at most 1.0",
                    8000)
                self._sync_analysis_checkboxes()
                return
            self._on_bands_edited(gy, yr)

        # Arrow clicks and committed typing update the canvas immediately;
        # keyboard tracking off means one commit per typed value, not one
        # per digit/keystroke.
        for spin in (self._gy_spin, self._yr_spin):
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(commit_bands)
        # Stacked three high with their labels lined up, like any other
        # column of small controls.
        for text, widget in (("Metric", self._weak_type_combo),
                             ("Green ≥", self._gy_spin),
                             ("Red <", self._yr_spin)):
            if widget is not self._weak_type_combo:
                widget.setFixedWidth(118)
            group.add_widget(_labeled_box(text, widget, label_width=48))

    def _init_find_box(self) -> None:
        """The Find box. Not on the ribbon: _setup_docks re-parents it into the Explorer."""
        # Manual 20-12 "Find Item dialog box": type to filter every
        # element (block/node/event/hierarchy); picking one selects and
        # centres it on the canvas. Not added to the ribbon:
        # `_setup_docks` re-parents it into `self._explorer`'s own layout
        # once that dock exists (it's built later).
        #
        # A plain QLineEdit + QCompleter rather than an editable
        # QComboBox that clears and rebuilds its whole item list on every
        # keystroke: tearing a live, focused editable combo's popup model
        # down mid-edit is fragile. The completer's popup filters
        # internally, so the backing model only needs refreshing when the
        # model changes, never on each keystroke.
        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText(
            "Find item…  (name or id)")
        self._find_edit.setClearButtonEnabled(True)
        self._find_edit.setToolTip(
            "Find item — type a name or an element id; the Model Explorer "
            "below filters to the matches and marks them. Enter jumps to "
            "the first one.")
        self._find_edit.setEnabled(False)
        self._find_model = QStringListModel(self)
        # NO completer. It listed the same names the Model Explorer tree --
        # directly below the box -- was already showing, so the results
        # appeared twice and the box covered the thing it was filtering.
        # The tree is now the only place results appear, and it marks the
        # hits. `_find_model` is kept as the flat element index that Enter
        # resolves against (`_resolve_find_text`), and the sweeps read it
        # to enumerate what is findable.
        self._find_edit.returnPressed.connect(
            lambda: self._on_find_submit(self._find_edit.text()))
        # Typing also refines the Model Explorer tree in place, matching
        # hierarchy names and their children. The dock doesn't exist yet
        # at this point (built in _setup_docks), hence the attribute
        # check.
        self._find_edit.textChanged.connect(self._on_find_text_changed)

        self._find_combo_refresh()

    def _init_report_stream_actions(self) -> None:
        """Canonical holders of the four recording choices (kept in sync by _sync_run_mirrors)."""
        # Files & Reports (mirroring Raptor 7's Simulation Options ▸
        # Files & Reports tab): plot/log collection is opt-in. The
        # original writes these files only when ticked and streams them
        # to disk; collecting every plot row in RAM unconditionally lets
        # a long simulation balloon memory and freeze the Graphs tab.
        # Off by default like Raptor 7; the Event Log stays on (the
        # Results tab's live view depends on it).
        #
        # Storage moved from `QSettings "reports/<key>"` to the RUN
        # EXPERIMENT. These are engineering choices about a run -- which
        # plots get recorded, at what density -- not properties of the
        # machine, and the design spec is explicit that QSettings is for
        # "user layout/preferences only, not engineering model data"
        # (the internal UI strategy notes). A one-shot migration carries
        # an existing user's ticks across, so nothing silently turns off
        # (see `_migrate_report_stream_settings`, run once at load).
        #
        # NOT a ribbon group any more: recording is edited in the Run
        # panel's Recording section, where each toggle can show its own
        # cost and be disabled with a stated reason when its analysis is
        # off. These QActions survive as the canonical holders of the
        # four values (the run paths and the tests read them) and are
        # kept in sync by `_sync_run_mirrors`, so the experiment remains
        # the single writer.
        self._report_stream_actions = {}
        experiment = self._current_experiment()
        for key, label, _default, tip in (
            ("event_log", "Event Log", True,
             "Collect each run's event log for the Results ▸ Event Log "
             "tab. Unchecked: no log collection (faster large batches)."),
            ("ao_plot", "Availability Plot", False,
             "Collect the Availability plot data (the rpt_"
             "availability_plot file): the Graphs tab's Per Trial / "
             "Interval / Multi-run Ao plots. Streamed to a temp file — "
             "never held in RAM."),
            ("capacity_plot", "Capacity Plot", False,
             "Collect capacity plot data (the rpt_capacity_plot "
             "file) for the Graphs tab's Capacity (Flow) plot and the "
             "live capacity readouts. Requires Capacity Analysis."),
            ("cost_plot", "Cost Plot", False,
             "Collect interim cost data (the rpt_interim_cost "
             "file). Requires Cost Analysis."),
        ):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(bool(experiment["recording"].get(key, False)))
            act.setToolTip(tip)
            act.toggled.connect(
                lambda checked, k=key: self._set_recording(k, checked))
            self._report_stream_actions[key] = act


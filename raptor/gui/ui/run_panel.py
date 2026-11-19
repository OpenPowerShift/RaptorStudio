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
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
    QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QMainWindow, QMenu, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from raptor.core import run_experiment as rx
from raptor.core.model import SystemConfig
from raptor.gui.resources.design_tokens import SPACING, active_colour
from raptor.gui.widgets.tidy_spin import TidySpinBox



def _importance_estimate(blocks: int, mode: str,
                         trials: int) -> tuple[int, int] | None:
    """(batches, simulated runs) the importance measures add, or None when
    the block count is unknown or the estimate fails. It is display-only:
    a failing estimate must not break the panel, and the run itself
    reports any real failure."""
    if not blocks:
        return None
    from raptor.api import importance as imp
    try:
        return (int(imp.batch_count(blocks, mode)),
                int(imp.estimate_runs(blocks, mode, trials)))
    except Exception:  # noqa: BLE001 - display-only estimate
        return None

class RunPanel(QWidget):
    """The experiment editor.

    Emits `experiment_changed` with a normalised dict; the owner decides
    whether that becomes a model edit (it does -- the panel is a view,
    MainWindow is the writer, so undo, dirty-tracking and the legacy
    column mirror all stay in one place)."""

    experiment_changed = Signal(dict)
    #: The run scope changed: 0 = whole model, otherwise a hierarchy id.
    scope_changed = Signal(int)
    #: A model-level system value edited here (flowGenerated/systemRedCost/
    #: sysLostCost). Kept separate from `experiment_changed`: these are
    #: SYSTEM_DATA columns, not experiment fields, even though the panel
    #: is where they are now edited.
    system_config_changed = Signal(dict)
    #: Emitted when a dependent plot is requested without its analysis.
    dependency_blocked = Signal(str, str)   # (plot key, analysis label)
    #: The Cores field changed. Not part of the experiment: how many cores
    #: this machine gives a run is an app setting, shared with the ribbon's
    #: Cores box (`cores.KEY`), so it never goes into the model file.
    cores_changed = Signal(int)
    #: The Precision… button beside Trials: size the run by a precision.
    precision_requested = Signal()
    #: The panel's content height changed -- the dock caps its
    #: height to the content and must recompute (see
    #: `RunPanelDock.fit_height`).
    size_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._exp = rx.defaults()
        self._loading = False
        self._enabled = True
        self._hierarchies: list[tuple[int, str]] = []
        self._scope_hid = 0
        # Mirrors of the SYSTEM_DATA cells shown in this panel. Kept here
        # (not in `_exp`) so the experiment stays a run-settings object;
        # MainWindow remains the writer of the row.
        self._flow_generated = 0
        self._system_red_cost = 0.0
        self._lost_flow_unit_cost = 0.0
        self._system_rows_visible: tuple[bool, ...] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING["sm"], SPACING["sm"],
                                 SPACING["sm"], SPACING["sm"])
        outer.setSpacing(SPACING["xs"])

        self._build_top_line(outer)

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
        ]
        self._columns = 0
        self._arrange(1)

        self._label_all_controls()
        # Run is the panel's last stop on Tab, after every setting: it is
        # built first (it sits on the top line) so it would otherwise be
        # the FIRST.
        QWidget.setTabOrder(self._cost_interval, self._run_button)
        self._refresh()

    def _build_top_line(self, outer: QVBoxLayout) -> None:
        """The readiness line and the Run button."""
        # -- readiness line ------------------------------------------------
        # The design spec requires every disabled action to explain its
        # unmet condition (`blueprint:125`) and a six-state readiness
        # machine (`blueprint:484-491`). A bare greyed Run button tells
        # the user nothing; this line does.
        self._readiness = QLabel()
        self._readiness.setObjectName("RunReadiness")
        self._readiness.setWordWrap(True)
        self._readiness.setAccessibleName("Run readiness")
        # The Run button sits on the readiness line: a new user set the
        # trials here and then had to find Run on another ribbon tab (or
        # know F5). It is the ribbon's own action (`set_run_action`), so it
        # is enabled, disabled and explained in one place.
        #
        # Run is a filled primary button at the right edge, lined up with
        # the last section (review #286, second round): a fixed place the
        # eye learns, as on a desktop dialog, which does not move when the
        # readiness text changes length. (As a flat tool button in the
        # same corner it had read as a link.) The label's width is capped
        # so a long warning wraps instead of running under the button.
        # A QToolButton, not a QPushButton: only a tool button can take
        # the ribbon's action (`setDefaultAction`), which keeps its
        # enabled state, text and tooltip in step with the ribbon's Run.
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(SPACING["sm"])
        self._readiness.setMaximumWidth(760)
        top.addWidget(self._readiness, 0, Qt.AlignmentFlag.AlignVCenter)
        self._run_button = QToolButton()
        self._run_button.setObjectName("RunPanelRun")
        # Text only: the ribbon's green Run icon does not read on the
        # filled accent button.
        self._run_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._run_button.setAutoRaise(False)     # a button, not a link
        self._run_button.setMinimumWidth(96)
        self._run_button.setVisible(False)
        self._style_run_button()
        from raptor.gui.resources.design_tokens import ThemeManager
        ThemeManager.instance().on_changed(self._on_theme_changed)
        top.addStretch(1)
        top.addWidget(self._run_button, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(top)


    #: Below this width per column a section's notes wrap to one word
    #: a line, so fewer, wider columns read better.
    SECTION_MIN_WIDTH = 340

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cols = max(1, min(len(self._sections),
                          self.width() // self.SECTION_MIN_WIDTH))
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
                    self._readiness.sizeHint().height(),
                    self._run_button.sizeHint().height()
                    if not self._run_button.isHidden() else 0)
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

    def _build_scope_rows(self) -> QHBoxLayout:
        """One row: the model-scope selector and, for a hierarchy, Options.

        The bottom dock is short, so a hierarchy run adds NO height here:
        the explanation and boundary treatments live in a small dialog
        behind the Options button, and the choice is flagged by the
        selector's amber outline and the box title instead of extra rows
        that would push Trials below the fold.
        """
        from raptor.gui.widgets.hierarchy_picker import HierarchyPicker
        self._scope = HierarchyPicker()
        self._scope.setToolTip(
            "Run the whole model, or one hierarchy on its own. A hierarchy "
            "run is isolated: its input is assumed always available and "
            "the results describe that hierarchy only. Type to search by "
            "name.")
        self._scope.currentIndexChanged.connect(self._on_scope_changed)
        self._scope.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Fixed)

        self._scope_button = QPushButton("Isolation options…")
        self._scope_button.setToolTip(
            "How the hierarchy's connections to the rest of the model are "
            "treated for this isolated run.")
        self._scope_button.clicked.connect(self._open_scope_options)
        # Its place is kept while it is hidden, so the home button beside it
        # does not jump when a hierarchy is chosen and Options appears.
        self._keep_place_when_hidden(self._scope_button)

        self._scope_home = self._build_scope_home()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._scope, 1)
        row.addWidget(self._scope_home)
        row.addWidget(self._scope_button)

        # The explanation and treatments, shown on demand.
        self._scope_dialog = QDialog(self)
        self._scope_dialog.setWindowTitle("Isolated hierarchy run")
        dlg = QVBoxLayout(self._scope_dialog)
        self._scope_warning = QLabel()
        self._scope_warning.setObjectName("RunScopeWarning")
        self._scope_warning.setWordWrap(True)
        self._scope_warning.setTextFormat(Qt.TextFormat.RichText)
        self._scope_warning.setMinimumWidth(420)
        self._scope_warning.setStyleSheet(
            "QLabel#RunScopeWarning { background: rgba(245, 158, 11, 36); "
            "border: 1px solid #b45309; border-radius: 3px; padding: 6px; }")
        dlg.addWidget(self._scope_warning)

        self._scope_options = QWidget()
        opt = QFormLayout(self._scope_options)
        opt.setContentsMargins(0, 0, 0, 0)
        self._scope_deps = QComboBox()
        self._scope_deps.addItem("Assume outside elements are always up", "up")
        self._scope_deps.addItem("Follow the extract's own end", "system")
        self._scope_deps.addItem("Refuse (stop if any)", "refuse")
        self._scope_pools = QComboBox()
        self._scope_pools.addItem("Give it the whole pool", "full")
        self._scope_pools.addItem("Share by the fraction of users inside",
                                  "share")
        self._scope_pools.addItem("Refuse (stop if any)", "refuse")
        self._scope_streams = QComboBox()
        self._scope_streams.addItem("Keep the random streams", "keep")
        self._scope_streams.addItem("Give shared draws their own streams",
                                    "separate")
        opt.addRow("Outside dependencies:", self._scope_deps)
        opt.addRow("Shared spare / resource pools:", self._scope_pools)
        opt.addRow("Shared random streams:", self._scope_streams)
        dlg.addWidget(self._scope_options)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self._scope_dialog.close)
        dlg.addWidget(buttons)
        return row

    @staticmethod
    def _keep_place_when_hidden(widget) -> None:
        policy = widget.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        widget.setSizePolicy(policy)
        widget.setVisible(False)

    def _build_scope_home(self):
        """One click back to the whole model, from wherever the list is."""
        from raptor.gui.widgets.hierarchy_picker import home_button
        return home_button(self._scope)

    def _open_scope_options(self) -> None:
        self._scope_dialog.show()
        self._scope_dialog.raise_()
        self._scope_dialog.activateWindow()

    #: Preferences key: whether the Experiment box's More options are open.
    MORE_OPTIONS_KEY = "run_panel/more_options"

    #: Fields stop growing at this width: stretched across a 500 px column
    #: a four-digit number read as a bar, and the box grew no shorter for it.
    FIELD_MAX_WIDTH = 200

    @staticmethod
    def _describe(widget: QWidget, text: str) -> None:
        """A tooltip that keyboard and screen-reader users get too."""
        widget.setToolTip(text)
        widget.setAccessibleDescription(text)

    def _cap(self, widget: QWidget, width: int | None = None) -> QWidget:
        widget.setMaximumWidth(width or self.FIELD_MAX_WIDTH)
        return widget

    def _build_more_options(self, form: QFormLayout) -> None:
        """Model scope and Cores, behind a disclosure.

        Review #286: both are for a second look, not a first run, and Scope
        was the box's FIRST row, ahead of Trials. They are never out of
        sight, though, because each changes what the numbers mean (a
        hierarchy run describes that hierarchy alone; one core gives a
        different random sequence from two or more): the closed header
        always says both ("Whole model · 8 cores"), the readiness line says
        both, and choosing a hierarchy opens the section whatever was
        remembered.

        A checkable QToolButton with an arrow, flat in both states (a
        filled "pill" read as a selected tab), not a checkable QGroupBox,
        whose check box means "enabled".
        """
        self._more_button = QToolButton()
        self._more_button.setObjectName("RunMoreOptions")
        self._more_button.setCheckable(True)
        self._more_button.setAutoRaise(True)
        self._more_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._more_button.setStyleSheet(
            "QToolButton#RunMoreOptions, QToolButton#RunMoreOptions:checked "
            "{ background: transparent; border: none; padding: 2px 0; }")
        self._more_button.setAccessibleName("More run options")
        self._more_button.setToolTip(
            "Model scope (the whole model or one hierarchy) and the cores a "
            "full-speed run uses")
        self._more_button.toggled.connect(self._on_more_toggled)
        form.addRow(self._more_button)

        self._more_body = QWidget()
        more = QFormLayout(self._more_body)
        more.setContentsMargins(SPACING["md"], 0, 0, 0)
        more.setVerticalSpacing(SPACING["xs"])
        more.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self._build_scope_and_cores_row(more)
        self._build_seed_row(more)
        self._build_streams_row(more)
        self._more_body.setVisible(False)
        form.addRow(self._more_body)

        from raptor.gui.ui import settings
        self._more_button.setChecked(
            settings.get_bool(self.MORE_OPTIONS_KEY, False))
        self._refresh_more_options()

    def _build_scope_and_cores_row(self, form: QFormLayout) -> None:
        """'Model scope: [Whole model ▾] ⌂  Cores: [8 of 8]', one row."""
        row = self._build_scope_rows()
        # A fixed width: sized to its text, the picker changed width with
        # the name shown and moved the home button beside it.
        self._scope.setFixedWidth(210)
        self._build_cores_widgets()
        cores_label = QLabel("Cores:")
        cores_label.setBuddy(self._cores)
        # Cores goes beside the scope picker, BEFORE Isolation options:
        # that button keeps its place while hidden (so nothing jumps when a
        # hierarchy is chosen), and an empty place before Cores read as a
        # gap in the row.
        at = row.indexOf(self._scope_button)
        row.setStretch(row.indexOf(self._scope), 0)
        row.insertSpacing(at, SPACING["md"])
        row.insertWidget(at + 1, cores_label)
        row.insertWidget(at + 2, self._cores)
        row.insertSpacing(at + 3, SPACING["md"])
        row.addStretch(1)
        form.addRow("Model scope:", row)
        form.addRow(self._cores_note)

    def _build_seed_row(self, form: QFormLayout) -> None:
        """'Seed: [Raptor 7 (fixed) ▾] [1234]' (#292).

        Fixed is the default: runs repeat exactly, and paired studies (What
        If, Sensitivity) stay paired. New draws a seed for each run and the
        results' run-settings line shows it, so that run can be repeated."""
        self._seed_mode = QComboBox()
        for label, mode, tip in (
                ("Raptor 7 (fixed)", "fixed",
                 "Raptor 7's seed table: every run draws the same random "
                 "numbers and matches Raptor 7. Two runs are the same "
                 "experiment twice, not two independent samples."),
                ("New each run", "new",
                 "A fresh seed every run, so each result is an independent "
                 "sample. The seed is shown in the results; choose Number "
                 "afterwards to repeat that run."),
                ("Number", "number",
                 "A seed you choose, saved with the model: the same number "
                 "repeats a run. The CLI (raptor run --seed N) and the AI "
                 "Assistant (seed=N) give the same numbers on the same "
                 "number of cores.")):
            self._seed_mode.addItem(label, mode)
            self._seed_mode.setItemData(self._seed_mode.count() - 1, tip,
                                        Qt.ItemDataRole.ToolTipRole)
        self._cap(self._seed_mode, 160)
        self._seed_value = QSpinBox()
        self._seed_value.setRange(1, rx.SEED_MAX)
        self._seed_value.setAccessibleName("Seed number")
        self._cap(self._seed_value, 130)
        self._seed_value.setVisible(False)
        self._seed_mode.currentIndexChanged.connect(self._on_seed_mode)
        self._seed_value.editingFinished.connect(
            lambda: self._emit_change("seed", int(self._seed_value.value())))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING["xs"])
        row.addWidget(self._seed_mode)
        row.addWidget(self._seed_value)
        row.addStretch(1)
        form.addRow("Seed:", row)
        self._describe(self._seed_mode, self._seed_mode.itemData(
            0, Qt.ItemDataRole.ToolTipRole))

    def _build_streams_row(self, form: QFormLayout) -> None:
        """'Streams: [As in the model ▾]' (#292): a run-time choice, so the
        model's own block streams are never rewritten. Data ▸ Bulk Edit ▸
        Separate Streams writes them into the model instead."""
        self._streams_mode = QComboBox()
        for label, mode, tip in (
                ("As in the model", "model",
                 "Each block draws from the streams set in its data. By "
                 "default every block shares one stream, as in Raptor 7, "
                 "and the results match Raptor 7's."),
                ("Separate for each block", "separate",
                 "For this run, every block that shares a stream gets a "
                 "failure and repair stream of its own (reused after 49 "
                 "blocks), so changing one block does not shift the "
                 "others' draws. The model is not edited. Results no "
                 "longer match Raptor 7's. raptor run --block-streams "
                 "separate and the AI Assistant (block_streams) give the "
                 "same numbers.")):
            self._streams_mode.addItem(label, mode)
            self._streams_mode.setItemData(
                self._streams_mode.count() - 1, tip,
                Qt.ItemDataRole.ToolTipRole)
        self._streams_mode.setAccessibleName("Block random streams")
        self._cap(self._streams_mode, 190)
        self._streams_mode.currentIndexChanged.connect(self._on_streams_mode)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._streams_mode)
        row.addStretch(1)
        form.addRow("Streams:", row)
        self._describe(self._streams_mode, self._streams_mode.itemData(
            0, Qt.ItemDataRole.ToolTipRole))

    def _on_streams_mode(self, _index: int) -> None:
        self._describe(self._streams_mode, self._streams_mode.itemData(
            self._streams_mode.currentIndex(), Qt.ItemDataRole.ToolTipRole))
        if self._loading:
            return
        self._emit_change("streams", str(self._streams_mode.currentData()))
        self._refresh_more_options()

    def _on_seed_mode(self, _index: int) -> None:
        mode = str(self._seed_mode.currentData())
        self._seed_value.setVisible(mode == "number")
        self._describe(self._seed_mode, self._seed_mode.itemData(
            self._seed_mode.currentIndex(), Qt.ItemDataRole.ToolTipRole))
        if self._loading:
            return
        if mode == "number":
            # Start from the last run's seed: choosing Number is how a
            # New run is repeated.
            last = int(getattr(self, "last_seed", 0) or 0)
            value = last or int(self._exp.get("seed") or 0) or 1
            self._seed_value.setValue(value)
            self._emit_changes({"seed_mode": "number", "seed": value})
        else:
            self._emit_change("seed_mode", mode)
        self._refresh_more_options()

    def _build_cores_widgets(self) -> None:
        from raptor.gui import cores
        self._cores = QSpinBox()
        self._cores.setRange(1, cores.available())
        self._cores.setSuffix(f" of {cores.available()}")
        self._cores.setAccessibleName("Cores for full-speed runs")
        self._cores.setMinimumWidth(80)
        self._cap(self._cores, 110)
        # The Run panel is the one editor for Cores. The ribbon box is a
        # read-only readout; Preferences shows the same value without a
        # second write path.
        self._cores.editingFinished.connect(
            lambda: self.cores_changed.emit(self._cores.value()))
        # Why Cores is greyed, in words (a tooltip alone went unseen),
        # shown only when there is something to say; the same reason is in
        # the More options heading and the readiness line.
        self._cores_note = QLabel()
        self._cores_note.setObjectName("SectionNote")
        self._cores_note.setWordWrap(True)
        self._cores_note.setVisible(False)

    def _build_trials_row(self, form: QFormLayout) -> None:
        """'Trials: [100] [Precision…] [Presets ▾]'.

        Presets replaced a Profile combo (review #286, second round): a
        profile is applied ONCE, so a permanent field naming one went stale
        the moment anything was edited, cost two rows with its hint, and
        original Raptor had none. A one-shot menu beside the other way of
        choosing a run's size keeps the guidance (how long to run, when to
        start the statistics) without the field.
        """
        trials_row = QHBoxLayout()
        trials_row.setContentsMargins(0, 0, 0, 0)
        trials_row.setSpacing(SPACING["xs"])
        trials_row.addWidget(self._cap(self._trials, 120))
        # a precision instead of a count: Simulate > Trials to Precision
        self._precision_btn = QPushButton()
        self._precision_btn.setText("Precision…")
        self._precision_btn.setToolTip(
            "Find how many trials a stated precision needs (runs a copy), "
            "then use that count")
        self._precision_btn.clicked.connect(self.precision_requested.emit)
        trials_row.addWidget(self._precision_btn)
        # A QPushButton with a menu, so it has the same frame as
        # Precision… beside it (a flat tool button read as a label).
        self._presets_button = QPushButton("Presets")
        self._presets_button.setToolTip(
            "Start from a preset: it sets the run's length, trials and "
            "statistics start, and adds the analyses it needs. It never "
            "turns off one you chose.")
        self._presets_menu = QMenu(self._presets_button)
        self._preset_actions: dict[str, QAction] = {}
        for key, spec in rx.PROFILES.items():
            action = self._presets_menu.addAction(
                f"{spec['label']} — {spec['hint']}")
            action.setToolTip(spec["hint"])
            action.triggered.connect(
                lambda _checked=False, k=key: self.apply_preset(k))
            self._preset_actions[key] = action
        self._presets_button.setMenu(self._presets_menu)
        trials_row.addWidget(self._presets_button)
        trials_row.addStretch(1)
        form.addRow("Trials:", trials_row)

    def _build_experiment_box(self) -> QGroupBox:
        box = QGroupBox("Experiment")
        self._experiment_box = box
        form = QFormLayout(box)
        form.setVerticalSpacing(SPACING["xs"])
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        self._trials = QSpinBox()
        self._trials.setRange(1, 1_000_000)
        self._trials.setToolTip(
            "How many times to run the model. More trials shrink the "
            "uncertainty on the reported numbers.")
        # editingFinished, not valueChanged: a spin box emitting on every
        # keystroke would push an undo entry per digit.
        self._trials.editingFinished.connect(
            lambda: self._emit_change("trials", self._trials.value()))
        self._build_trials_row(form)

        # "Stop at: [Time ▾] after [1000] hours" -- one row. The word after
        # the number says what kind of number it is (hours, system
        # failures, phase cycles) and changes with the stop type: run
        # length is a sim time under TimeSim but a plain count under
        # FailSim/CycleSim, and "1000" with the wrong word is misleading.
        self._term_type = QComboBox()
        for code, words in (("TimeSim", "Time"),
                            ("FailSim", "System failures"),
                            ("CycleSim", "Phase cycles")):
            self._term_type.addItem(words, code)
        self._term_type.currentIndexChanged.connect(
            lambda _i: self._on_term_type())
        self._term_value = TidySpinBox()
        self._term_value.setDecimals(6)
        self._term_value.setRange(0.0, 1e15)
        self._term_value.setToolTip("The run length, in the unit named")
        self._term_value.editingFinished.connect(
            lambda: self._commit_time_field(
                "term_condition", self._term_value,
                self._exp["termination_type"]))
        self._term_label = QLabel("hours")
        stop_row = QHBoxLayout()
        stop_row.setContentsMargins(0, 0, 0, 0)
        stop_row.setSpacing(SPACING["xs"])
        stop_row.addWidget(self._cap(self._term_type, 150))
        after = QLabel("after")
        stop_row.addWidget(after)
        stop_row.addWidget(self._cap(self._term_value, 120))
        stop_row.addWidget(self._term_label)
        stop_row.addStretch(1)
        form.addRow("Stop at:", stop_row)

        self._start_stats = TidySpinBox()
        self._start_stats.setDecimals(6)
        self._start_stats.setRange(0.0, 1e15)
        self._start_stats.setToolTip(
            "Statistics gathering starts here, so start-up transients are "
            "excluded from the reported numbers. Must be earlier than the "
            "run length.")
        self._start_stats.editingFinished.connect(
            lambda: self._commit_time_field(
                "start_stats", self._start_stats, "TimeSim"))
        # The same unit as the run length (always a time), said the same way.
        self._stats_unit = QLabel("time units")
        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(SPACING["xs"])
        stats_row.addWidget(self._cap(self._start_stats, 120))
        stats_row.addWidget(self._stats_unit)
        stats_row.addStretch(1)
        form.addRow("Statistics from:", stats_row)

        # Shown only when the start time is invalid; the rest of what it
        # used to say is in the field tooltips, so it costs no height.
        self._unit_note = QLabel()
        self._unit_note.setObjectName("SectionNote")
        self._unit_note.setWordWrap(True)
        self._unit_note.setVisible(False)
        form.addRow(self._unit_note)

        self._build_more_options(form)
        return box

    def _on_more_toggled(self, on: bool) -> None:
        self._more_body.setVisible(on)
        self._more_button.setArrowType(
            Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self._more_button.setAccessibleDescription(
            "expanded" if on else "collapsed")
        if not getattr(self, "_forcing_more", False):
            from raptor.gui.ui import settings
            settings.set_value(self.MORE_OPTIONS_KEY, bool(on))
        self.size_changed.emit()

    def more_options_open(self) -> bool:
        return not self._more_body.isHidden()

    def _cores_words(self) -> str:
        """'8 cores', or '1 core (Animate)': what the run will use."""
        if getattr(self, "_cores_animate", False):
            return "1 core (Animate)"
        n = int(self._cores.value())
        return f"{n} core{'s' if n != 1 else ''}"

    def more_options_summary(self) -> str:
        """The closed header's text: scope and cores, always; the seed
        too when it is not Raptor 7's fixed table."""
        scope = (self._scope.currentText().strip() if self._scope_hid
                 else "Whole model")
        text = f"{scope} · {self._cores_words()}"
        exp = getattr(self, "_exp", None)
        if exp and exp.get("seed_mode", "fixed") != "fixed":
            text += f" · {rx.seed_words(exp)}"
        if exp and rx.streams_words(exp):
            text += f" · {rx.streams_words(exp)}"
        return text

    def _refresh_more_options(self) -> None:
        button = getattr(self, "_more_button", None)
        if button is None:
            return
        button.setText(f"More options: {self.more_options_summary()}")
        button.setArrowType(Qt.ArrowType.DownArrow if button.isChecked()
                            else Qt.ArrowType.RightArrow)
        button.setAccessibleDescription(
            "expanded" if button.isChecked() else "collapsed")

    def _build_analysis_box(self) -> QGroupBox:
        # "Results to calculate", not "Analysis": the box is chosen by the
        # results a user wants, and every note says which (review #286).
        box = QGroupBox("Results to calculate")
        outer = QVBoxLayout(box)
        outer.setSpacing(2)
        self._analysis_checks: dict[str, QCheckBox] = {}
        grid = self._check_grid(outer)
        # The run-wide model values (SYSTEM_DATA) each sit under the
        # analysis that spends them, and only while it is ticked: flow
        # generated is the demand Capacity distributes, the red cost prices
        # failed time, and the lost-flow cost needs Capacity and Cost.
        self._flow_gen = QSpinBox()
        self._flow_gen.setRange(0, 1_000_000_000)
        self._flow_gen.setValue(int(self._flow_generated))
        self._flow_gen.setToolTip(
            "Demand offered at the Start node each time unit; the "
            "capacity analysis distributes it across the network.")
        self._flow_gen.editingFinished.connect(self._on_flow_generated)
        self._system_red_cost_field = self._cost_field(
            "System red cost", self._system_red_cost,
            self._on_system_red_cost,
            "Cost per time unit while the whole system is down.")
        self._lost_flow_cost_field = self._cost_field(
            "Cost of lost flow", self._lost_flow_unit_cost,
            self._on_lost_flow_cost,
            "Price per unit of demand the capacity analysis cannot "
            "deliver. Needs Capacity and Cost.")
        self._system_rows: dict[str, QWidget] = {}
        row = 0
        for key, label in (("weak", "Weak Link"), ("cost", "Cost"),
                           ("capacity", "Capacity")):
            cb = QCheckBox(label)
            cb.toggled.connect(
                lambda on, k=key: self._on_analysis(k, on))
            # What it gives is the box's tooltip and accessible
            # description, not a line of text beside every box: these
            # start off, and four "will be empty" lines were noise.
            grid.addWidget(cb, row, 0, 1, 2, Qt.AlignmentFlag.AlignTop)
            self._analysis_checks[key] = cb
            row += 1
            values = {"cost": (("red", "System red cost:",
                                self._system_red_cost_field),),
                      "capacity": (("flow", "Flow generated:", self._flow_gen),
                                   ("lost", "Cost of lost flow:",
                                    self._lost_flow_cost_field))}.get(key, ())
            for name, text, field in values:
                self._system_rows[name] = self._value_row(grid, row, text,
                                                          field)
                row += 1

        # --- importance measures -------------------------------------
        # Its own row because its cost is of a different order: every other
        # analysis adds work INSIDE a run, this one multiplies the number of
        # runs. The note says so in sight, before it is switched on.
        self._importance_check = QCheckBox("Importance measures")
        self._importance_check.toggled.connect(self._on_importance_toggled)
        grid.addWidget(self._importance_check, row, 0, 1, 2,
                       Qt.AlignmentFlag.AlignTop)
        row += 1
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
        self._importance_cost = QLabel()
        self._importance_cost.setObjectName("SectionNote")
        self._importance_cost.setWordWrap(True)
        # The mode picker and the run-cost line, under the box and only
        # while it is ticked: the cost sets how long the run takes.
        holder = QWidget()
        self._importance_details = holder
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(self.INDENT, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self._importance_mode, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self._importance_cost)
        grid.addWidget(holder, row, 0, 1, 2, Qt.AlignmentFlag.AlignTop)
        self._importance_blocks = 0
        self._sync_importance_rows()
        return box

    def _value_row(self, grid: QGridLayout, row: int, text: str,
                   field: QWidget) -> QWidget:
        """A value under its option, indented: 'System red cost: [13300]'."""
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(self.INDENT, 0, 0, 0)
        label = QLabel(text)
        label.setBuddy(field)
        lay.addWidget(label)
        lay.addWidget(self._cap(field, 140))
        lay.addStretch(1)
        grid.addWidget(holder, row, 0, 1, 2, Qt.AlignmentFlag.AlignTop)
        holder.setVisible(False)
        return holder

    def _cost_field(self, name: str, value: float, on_commit,
                    tooltip: str) -> QDoubleSpinBox:
        """A compact cost field with the dialog's old numeric range."""
        spin = TidySpinBox()
        spin.setDecimals(6)
        spin.setRange(-1e15, 1e15)
        spin.setValue(float(value))
        spin.setAccessibleName(name)
        spin.setToolTip(tooltip)
        spin.editingFinished.connect(on_commit)
        return spin

    def _sync_importance_rows(self) -> None:
        """Show/hide the mode picker and state the cost for this model."""
        from raptor.api import run_needs

        experiment = getattr(self, "_exp", None) or rx.defaults()
        mode = str(experiment.get("importance", "none"))
        on = mode != "none"
        self._importance_check.blockSignals(True)
        self._importance_check.setChecked(on)
        self._importance_check.blockSignals(False)
        self._importance_details.setVisible(on)
        blocks = int(getattr(self, "_importance_blocks", 0) or 0)
        trials = int(experiment.get("trials", 1))
        if on:
            idx = self._importance_mode.findData(mode)
            if idx >= 0 and idx != self._importance_mode.currentIndex():
                self._importance_mode.blockSignals(True)
                self._importance_mode.setCurrentIndex(idx)
                self._importance_mode.blockSignals(False)
        estimate = _importance_estimate(blocks, mode if on else
                                        rx.IMPORTANCE_DEFAULT_MODE, trials)
        if on and estimate:
            batches, runs = estimate
            cost = (f"Adds {batches:,} batches for {blocks:,} "
                    f"block{'s' if blocks != 1 else ''}: about {runs:,} "
                    f"simulated runs. Variant batches record no logs or "
                    f"plots.")
        elif estimate:
            batches = estimate[0]
            cost = (f"Adds about {batches:,} batches here (one per block, "
                    f"two with Birnbaum), on top of the ordinary run.")
        else:
            cost = ("Adds one batch per block (two with Birnbaum), on top "
                    "of the ordinary run.")
        self._set_importance_note(
            run_needs.feeds_note("importance", on) + " " + cost)
        self.size_changed.emit()

    def unit_hint(self) -> str:
        """Units and when Statistics-from applies (shown as tooltips, so
        it costs the short bottom dock no height)."""
        return getattr(self, "_unit_hint_text", "")

    def _set_importance_note(self, text: str) -> None:
        """The importance note: what it gives and what it costs, in sight
        (it was a tooltip, on the costliest box in the panel)."""
        self._importance_note_text = text
        self._importance_cost.setText(text)
        self._describe(self._importance_check,
                       "IEC 61078 Annex D importance factors, estimated by "
                       "re-simulation.\n\n" + text)
        self._importance_mode.setToolTip(text)

    def importance_runs(self) -> int | None:
        """Simulated runs the importance batches add, or None when off (or
        the model's block count is not known yet)."""
        mode = str(self._exp.get("importance", "none"))
        blocks = int(getattr(self, "_importance_blocks", 0) or 0)
        if mode == "none":
            return None
        estimate = _importance_estimate(blocks, mode, int(self._exp["trials"]))
        return estimate[1] if estimate else None

    def importance_note(self) -> str:
        return getattr(self, "_importance_note_text", "")

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
        grid.setContentsMargins(0, SPACING["xs"], 0, 0)
        grid.setHorizontalSpacing(12)
        # Room between the rows: at 2 px the check boxes crowded each
        # other and a click could land on the wrong one (user feedback).
        grid.setVerticalSpacing(SPACING["sm"])
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)
        return grid

    #: How far a row that belongs to a check box (its value, its warning)
    #: is indented under it: past the indicator, to the label's text.
    INDENT = 24

    def _add_check_row(self, grid: QGridLayout, r: int,
                       cb: QCheckBox) -> QLabel:
        """The check box, and under it a warning line (hidden unless there
        is something to warn about). Two rows: the warning used to sit
        beside the box in a second column, a paragraph per option."""
        note = QLabel()
        note.setObjectName("SectionNote")
        note.setWordWrap(True)
        note.setContentsMargins(self.INDENT, 0, 0, 0)
        note.setVisible(False)
        top = Qt.AlignmentFlag.AlignTop
        grid.addWidget(cb, r, 0, 1, 2, top)
        grid.addWidget(note, r + 1, 0, 1, 2, top)
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
        self._ao_interval = TidySpinBox()
        self._ao_interval.setDecimals(3)
        self._ao_interval.setRange(0.001, 1e6)
        self._ao_interval.setToolTip(
            "How often the Availability plot is sampled, in the model's "
            "time units. Smaller = smoother, larger files.")
        self._ao_interval.editingFinished.connect(
            self._on_ao_interval)
        self._cost_interval = TidySpinBox()
        self._cost_interval.setDecimals(3)
        self._cost_interval.setRange(0.001, 1e9)
        self._cost_interval.setToolTip(
            "How often interim cost totals are recorded, in the model's "
            "time units.")
        self._cost_interval.editingFinished.connect(
            self._on_cost_interval)
        # Each interval sits under its own plot and is shown only while
        # that plot is ticked: two greyed rows at the bottom of the box,
        # away from the boxes they belonged to, read as broken.
        intervals = {"ao_plot": ("Sample every:", self._ao_interval),
                     "cost_plot": ("Record every:", self._cost_interval)}
        self._interval_holders: dict[str, QWidget] = {}
        grid = self._check_grid(outer)
        row = 0
        for key, label, _hint in rx.RECORDING_FIELDS:
            cb = QCheckBox(label)
            cb.toggled.connect(
                lambda on, k=key: self._on_recording(k, on))
            note = self._add_check_row(grid, row, cb)
            self._recording_checks[key] = cb
            self._recording_notes[key] = note
            row += 2
            if key in intervals:
                text, spin = intervals[key]
                self._interval_holders[key] = self._value_row(
                    grid, row, text, spin)
                row += 1

        # What the ticked recordings will take on disk for this many trials
        # of this length. A plot sampled every 10 hours over a 20-year run
        # is 17,520 rows a trial; nobody can see that from the tick box.
        self._storage_note = QLabel("")
        # Not "SectionNote": that name marks the per-checkbox notes, which
        # sit in the grid's second column; this line spans the box.
        self._storage_note.setObjectName("StorageNote")
        self._storage_note.setWordWrap(True)
        self._storage_note.setAccessibleName("Estimated size on disk")
        outer.addSpacing(SPACING["sm"])
        outer.addWidget(self._storage_note)

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
            (self._presets_button, "Experiment presets"),
            (self._trials, "Number of trials"),
            (self._precision_btn, "Size trials by precision"),
            (self._term_type, "Stop condition"),
            (self._term_value, "Run length"),
            (self._start_stats, "Statistics start time"),
            (self._scope, "Model scope"),
            (self._scope_button, "Isolation options"),
            (self._ao_interval, "Availability plot sample interval"),
            (self._cost_interval, "Interim cost sample interval"),
            (self._flow_gen, "Flow generated"),
            (self._system_red_cost_field, "System red cost"),
            (self._lost_flow_cost_field, "Cost of lost flow"),
            (self._importance_mode, "Importance measure set"),
            (self._importance_cost, "Importance measures note"),
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
        self._unit_note.setAccessibleName("Unit note")
        self._sync_term_label_name()

    def _sync_term_label_name(self) -> None:
        """The run-length unit is read out by what it says ("system
        failures"), not as "Run length label"."""
        self._term_label.setAccessibleName(
            f"Run length unit: {self._term_label.text()}")

    # -- state in -------------------------------------------------------

    def set_experiment(self, exp: dict, *, enabled: bool = True) -> None:
        """Show `exp` without emitting a change back."""
        self._loading = True
        try:
            self._exp = rx.normalise(exp)
            # A preset is not stored: it is a set of overrides applied
            # once, from the Presets menu.
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
            mode_index = self._seed_mode.findData(self._exp["seed_mode"])
            self._seed_mode.setCurrentIndex(max(0, mode_index))
            if self._exp["seed"]:
                self._seed_value.setValue(int(self._exp["seed"]))
            self._seed_value.setVisible(self._exp["seed_mode"] == "number")
            self._streams_mode.setCurrentIndex(max(0, self._streams_mode.findData(
                self._exp["streams"])))
        finally:
            self._loading = False
        self.set_enabled(enabled)
        self._refresh()

    def set_system_config(self, cfg: SystemConfig | None) -> None:
        """Show the model's system values without emitting edits back.

        `None` (no model open) resets the three fields to their neutral
        defaults. Called by MainWindow after every row/undo sync, so the
        panel and the model cannot drift apart.
        """
        self._loading = True
        try:
            self._flow_generated = int(
                getattr(cfg, "flow_generated", 0) or 0)
            self._system_red_cost = float(
                getattr(cfg, "system_red_cost", 0.0) or 0.0)
            self._lost_flow_unit_cost = float(
                getattr(cfg, "lost_flow_unit_cost", 0.0) or 0.0)
            self._flow_gen.setValue(self._flow_generated)
            self._system_red_cost_field.setValue(self._system_red_cost)
            self._lost_flow_cost_field.setValue(self._lost_flow_unit_cost)
        finally:
            self._loading = False
        self._refresh()

    def experiment(self) -> dict:
        return rx.normalise(self._exp)

    def set_hierarchies(self, rows: list[tuple[int, str]]) -> None:
        """Fill the model-scope selector without changing the user's pick.

        `rows` is (hierarchy id, indented display path), already ordered by
        the owner. A hierarchy that disappeared (undo, delete, another
        model) falls back to Whole model.
        """
        rows = [(int(h), str(label)) for h, label in rows]
        if rows == self._hierarchies:
            return
        self._hierarchies = rows
        keep = self._scope_hid
        self._scope.blockSignals(True)
        try:
            self._scope.set_hierarchies(rows)
            index = self._scope.findData(keep)
            self._scope.setCurrentIndex(max(0, index))
            self._scope_hid = int(self._scope.currentData() or 0)
        finally:
            self._scope.blockSignals(False)
        self._refresh_scope()
        if self._scope_hid != keep:
            self.scope_changed.emit(self._scope_hid)

    def hierarchy_scope(self) -> int:
        """0 for the whole model, otherwise the selected hierarchy id."""
        return int(self._scope_hid)

    def scope_treatments(self) -> dict:
        """Boundary treatments for the selected hierarchy, in the shape
        `raptor.api.isolate.extract` expects."""
        return {"deps": self._scope_deps.currentData() or "up",
                "pools": self._scope_pools.currentData() or "full",
                "streams": self._scope_streams.currentData() or "keep",
                "items": {}}

    def _on_scope_changed(self, _index: int = 0) -> None:
        hid = int(self._scope.currentData() or 0)
        if hid == self._scope_hid:
            return
        self._scope_hid = hid
        self._refresh_scope()
        self.scope_changed.emit(hid)

    def _refresh_scope(self) -> None:
        hierarchy = bool(self._scope_hid)
        self._scope_button.setVisible(hierarchy)
        self._scope_home.setEnabled(hierarchy and self._enabled)
        from raptor.gui.widgets.hierarchy_picker import picker_style
        self._scope.setStyleSheet(
            picker_style("border: 2px solid #b45309;" if hierarchy else ""))
        box = self._experiment_box
        box.setTitle("Experiment — one hierarchy, isolated"
                     if hierarchy else "Experiment")
        if hierarchy and not self._more_button.isChecked():
            # A hierarchy run describes that hierarchy alone: never leave
            # the choice folded away, whatever was remembered. Not saved as
            # the user's preference -- they did not open it.
            self._forcing_more = True
            try:
                self._more_button.setChecked(True)
            finally:
                self._forcing_more = False
        self._refresh_more_options()
        if hierarchy:
            from html import escape
            name = escape(self._scope.currentText().strip())
            self._scope_warning.setText(
                "<b>Isolated hierarchy run.</b> Results describe "
                f"<b>{name}</b> alone, not the whole model. Its input is "
                "assumed always available. Node status changes do not cross "
                "the hierarchy boundary, and phase Cut/Linked behaviour "
                "inside the hierarchy is ignored. Importance measures, when "
                "enabled, rank blocks within this hierarchy only.")
        else:
            self._scope_dialog.close()
        self.size_changed.emit()

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
        self._relabel_term()
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
        from raptor.core.units_conv import native_to_display
        if not getattr(self, "_display_unit", None):
            return float(native)
        if termination_type == "TimeSim":
            return native_to_display(
                float(native), getattr(self, "_native_units", ""),
                self._display_unit)
        return float(native)

    def _to_native(self, shown: float, termination_type: str) -> float:
        """Shown -> native, the exact inverse of `_to_display`."""
        from raptor.core.units_conv import display_to_native
        if not getattr(self, "_display_unit", None):
            return float(shown)
        if termination_type == "TimeSim":
            return display_to_native(
                float(shown), getattr(self, "_native_units", ""),
                self._display_unit)
        return float(shown)

    def set_cores(self, value: int, enabled: bool, why: str, tip: str,
                  *, animate: bool = False) -> None:
        """Show the cores setting; `why` says why it cannot be changed now
        (empty when it can). `animate`: the run will use one core whatever
        the field says, and the summary says so."""
        self._cores.blockSignals(True)
        self._cores.setValue(int(value))
        self._cores.blockSignals(False)
        self._cores.setEnabled(enabled)
        self._cores.setToolTip(tip)
        self._cores_note.setText(why)
        self._cores_note.setVisible(bool(why))
        self._cores_animate = bool(animate)
        self._refresh_more_options()

    def _on_theme_changed(self, _dark: bool) -> None:
        """Light/dark switch: the Run button's fill is a stylesheet, so it
        is re-made; the readiness colour follows on its next update."""
        self._style_run_button()

    def _style_run_button(self) -> None:
        """The panel's primary action looks like one: filled with the
        accent colour, white text (a flat tool button read as a link)."""
        c = active_colour()
        self._run_button.setStyleSheet(
            "QToolButton#RunPanelRun { "
            f"background: {c.get('accent', '#2563eb')}; "
            f"color: {self.run_button_text_colour()}; "
            "font-weight: 600; border: none; border-radius: 4px; "
            "padding: 4px 14px; } "
            "QToolButton#RunPanelRun:disabled { "
            f"background: {c.get('border', '#9ca3af')}; "
            f"color: {c.get('muted', '#e5e7eb')}; }}")

    @staticmethod
    def run_button_text_colour() -> str:
        """White on the light theme's deep blue; near-black on the dark
        theme's light blue, where white was about 2.9:1."""
        from raptor.gui.resources.design_tokens import ThemeManager
        return "#0b1220" if ThemeManager.instance().is_dark else "#ffffff"

    def set_run_action(self, action) -> None:
        """Show `action` (the ribbon's Run) as this panel's Run button."""
        self._run_button.setDefaultAction(action)
        self._run_button.setVisible(True)

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
        # A word-wrapped label asks for a narrow width, so it wrapped a
        # one-line message onto two; ask for the line's own width (to the
        # cap set in __init__), and wrap only past that.
        from PySide6.QtGui import QTextDocument
        plain = message
        if self._readiness.textFormat() == Qt.TextFormat.RichText:
            doc = QTextDocument()
            doc.setHtml(message)
            plain = doc.toPlainText()
        bold = self._readiness.font()
        bold.setWeight(bold.Weight.DemiBold)
        from PySide6.QtGui import QFontMetrics
        want = QFontMetrics(bold).horizontalAdvance(plain) + 8
        self._readiness.setMinimumWidth(
            min(want, self._readiness.maximumWidth()))
        c = active_colour()
        # "go", not "operating": the state colour is a bright canvas green,
        # about 2:1 on the light theme's white -- too faint for text.
        colour = c.get("go") if ok else c.get("failed")
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
        for w in (self._scope, self._scope_deps, self._scope_pools,
                  self._scope_streams, self._presets_button, self._trials,
                  self._precision_btn,
                  self._term_type, self._term_value, self._start_stats,
                  self._ao_interval, self._cost_interval,
                  self._flow_gen, self._system_red_cost_field,
                  self._lost_flow_cost_field,
                  self._importance_check, self._importance_mode,
                  self._seed_mode, self._seed_value, self._streams_mode,
                  *self._analysis_checks.values(),
                  *self._recording_checks.values()):
            w.setEnabled(enabled)
        self._refresh()

    def is_enabled(self) -> bool:
        return self._enabled

    # -- state out ------------------------------------------------------

    def _emit_change(self, key, value) -> None:
        self._emit_changes({key: value})

    def _emit_changes(self, changes: dict) -> None:
        """Several fields as ONE change (one undo step)."""
        if self._loading or not self._enabled:
            return
        experiment = self.experiment()
        changes = {k: v for k, v in changes.items() if experiment.get(k) != v}
        if not changes:
            return
        experiment.update(changes)
        self._exp = experiment
        self._refresh()
        self.experiment_changed.emit(experiment)

    def _emit_system_change(self, column: str, value: str) -> None:
        """One SYSTEM_DATA cell edited here -> the model's single writer."""
        if self._loading or not self._enabled:
            return
        self.system_config_changed.emit({column: value})

    def _on_flow_generated(self) -> None:
        if self._loading or not self._enabled:
            return
        value = int(self._flow_gen.value())
        if value == self._flow_generated:
            return
        self._flow_generated = value
        self._emit_system_change("flowGenerated", str(value))

    def _on_system_red_cost(self) -> None:
        if self._loading or not self._enabled:
            return
        value = float(self._system_red_cost_field.value())
        if value == self._system_red_cost:
            return
        self._system_red_cost = value
        self._emit_system_change("systemRedCost", f"{value:.6f}")

    def _on_lost_flow_cost(self) -> None:
        if self._loading or not self._enabled:
            return
        value = float(self._lost_flow_cost_field.value())
        if value == self._lost_flow_unit_cost:
            return
        self._lost_flow_unit_cost = value
        self._emit_system_change("sysLostCost", f"{value:.6f}")

    def apply_preset(self, key: str) -> None:
        """Apply preset `key` once (the Presets menu)."""
        if self._loading or not self._enabled or key not in rx.PROFILES:
            return
        self._exp = self._with_profile(key, self._exp)
        self.set_experiment(self._exp)
        self.experiment_changed.emit(self.experiment())

    def _relabel_term(self) -> None:
        """The word after the run length: the time unit, or the count."""
        t = self._term_type.currentData()
        label = {"TimeSim": getattr(self, "_shown_units", "") or "time units",
                 "FailSim": "system failures",
                 "CycleSim": "phase cycles"}.get(t, "")
        self._term_label.setText(label)
        self._stats_unit.setText(
            getattr(self, "_shown_units", "") or "time units")
        self._sync_term_label_name()

    def _on_term_type(self, update_value: bool = False) -> None:
        """Relabel the run-length row for the chosen termination, and
        re-interpret its number.

        Run length is a sim TIME under TimeSim but a plain COUNT under
        FailSim/CycleSim, so the label and the unit both change. The
        same switch already exists in Sim Options; this is the panel's
        copy of that rule."""
        t = self._term_type.currentData()
        self._relabel_term()
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
        self._refresh_storage()
        self.experiment_changed.emit(experiment)

    def _on_cost_interval(self) -> None:
        if self._loading or not self._enabled:
            return
        experiment = self.experiment()
        experiment["recording"]["cost_interval"] = self._cost_interval.value()
        self._exp = experiment
        self._refresh_storage()
        self.experiment_changed.emit(experiment)

    # -- derived display -------------------------------------------------

    def set_storage_model(self, cycle_rate: float | None,
                          elements: int, blocks: int = 0) -> None:
        """What the open model contributes to the size estimate: expected
        block failures per unit time and the number of elements
        (`raptor.core.storage_estimate`). None when no model is open."""
        self._storage_cycle_rate = cycle_rate
        self._storage_elements = int(elements)
        self._storage_blocks = int(blocks)
        self._refresh_storage()

    def storage_estimate(self):
        """The current `StorageEstimate` for the panel's settings."""
        from raptor.core.storage_estimate import estimate_storage
        return estimate_storage(
            getattr(self, "_exp", None) or rx.defaults(),
            cycle_rate=getattr(self, "_storage_cycle_rate", None),
            elements=getattr(self, "_storage_elements", 0),
            blocks=getattr(self, "_storage_blocks", 0))

    def _refresh_storage(self) -> None:
        note = getattr(self, "_storage_note", None)
        if note is None:
            return
        from raptor.core.storage_estimate import format_bytes
        est = self.storage_estimate()
        text = est.summary()
        if est.total_bytes >= 1e9:
            text += (". Lengthen a sample interval or untick a recording "
                     "to reduce it.")
        lines = []
        for item in est.items:
            size = ("no figure" if item.bytes is None
                    else ("about " if item.approximate else "")
                    + format_bytes(item.bytes))
            lines.append(f"{item.label}: {size}"
                         + (f" ({item.note})" if item.note else ""))
        if any(i.key == "event_log" for i in est.items):
            lines.append("The event log figure is rough: it assumes every "
                         "block runs all the time.")
        if lines:
            lines.append("Recordings are written to disk as the run goes, "
                         "not held in memory.")
        note.setToolTip("\n".join(lines))
        if text != note.text():
            wrapped_before = note.heightForWidth(max(note.width(), 1))
            note.setText(text)
            if note.heightForWidth(max(note.width(), 1)) != wrapped_before:
                self.size_changed.emit()

    def _refresh(self) -> None:
        """Re-derive everything that depends on the current values."""
        experiment = self._exp
        self._refresh_storage()
        shown = getattr(self, "_shown_units", "")
        t = experiment["termination_type"]
        if t == "TimeSim" and shown:
            self._unit_hint_text = (
                f"Times in {shown}. Statistics must start before the run "
                f"ends.")
        else:
            self._unit_hint_text = (
                "Statistics start is only meaningful for a time-terminated "
                "run.")
        self._term_value.setToolTip(self._unit_hint_text)
        # Warn inline rather than blocking with a modal: the spec asks
        # for a field-level message plus a fix, not an interruption
        # (the internal UI blueprint).
        # DELEGATED (review E-15): same predicate, same wording, one owner.
        bad = rx.stats_start_after_run_length(experiment)
        self._start_stats.setToolTip(
            self._unit_hint_text + "\n" + rx.stats_start_tooltip(experiment))
        self._unit_note.setText(rx.stats_start_tooltip(experiment) if bad
                                else "")
        self._unit_note.setVisible(bool(bad))
        self._start_stats.setStyleSheet(
            f"QDoubleSpinBox {{ border: 2px solid {active_colour()['failed']}; }}"
            if bad else "")

        self._refresh_notes(experiment)

        # Rows that mean nothing now are hidden, not greyed: an irrelevant
        # number is still a misleading one. Each sampling interval follows
        # its plot; the model's flow/cost values follow their analyses (a
        # new user met "System red cost 13300" on a model with no costs).
        cost_on = "cost" in experiment["analyses"]
        flow_on = "capacity" in experiment["analyses"]
        visible = (bool(experiment["recording"].get("ao_plot")),
                   bool(experiment["recording"].get("cost_plot")),
                   flow_on, cost_on, flow_on and cost_on)
        if visible != self._system_rows_visible:
            self._system_rows_visible = visible
            ao, cost_plot, flow, red, lost = visible
            self._interval_holders["ao_plot"].setVisible(ao)
            self._interval_holders["cost_plot"].setVisible(cost_plot)
            self._system_rows["flow"].setVisible(flow)
            self._system_rows["red"].setVisible(red)
            self._system_rows["lost"].setVisible(lost)
            # Hiding a row changes the panel's preferred height, and the
            # dock caps itself to that height.
            self.size_changed.emit()
        self._refresh_more_options()

    def _refresh_notes(self, experiment: dict) -> None:
        """What each option feeds, from one table (`run_needs.FEEDS`), as
        its tooltip and accessible description.

        Text stays in sight only where it matters (review #286, second
        round): a plot that cannot be ticked says what it needs, in its own
        label ("Capacity plot (needs Capacity)"), and the event log -- on
        by default, with five results depending on it -- warns on a line
        of its own when it is off."""
        from raptor.api import run_needs
        for key, cb in self._analysis_checks.items():
            self._describe(cb, run_needs.feeds_note(
                key, key in experiment["analyses"]))
        changed = False
        labels = {k: lbl for k, lbl, _h in rx.RECORDING_FIELDS}
        for key, note in self._recording_notes.items():
            on = bool(experiment["recording"].get(key))
            cb = self._recording_checks[key]
            requires = rx.RECORDING_REQUIRES.get(key)
            blocked = bool(requires and requires[0]
                           not in experiment["analyses"])
            cb.setText(f"{labels[key]} (needs {requires[1]})"
                       if blocked and requires else labels[key])
            self._describe(cb, (
                f"Needs the {requires[1]} analysis, under Results to "
                f"calculate. " if blocked and requires else "")
                + run_needs.feeds_note(key, on))
            if requires is not None:
                cb.setEnabled(bool(self._enabled and not blocked))
            text = (run_needs.feeds_note(key, False)
                    if key == "event_log" and not on else "")
            if text != note.text() or bool(text) == note.isHidden():
                note.setText(text)
                note.setVisible(bool(text))
                changed = True
        if changed:
            self.size_changed.emit()

    # -- presets ----------------------------------------------------------

    @staticmethod
    def _with_profile(name: str, experiment: dict) -> dict:
        """`experiment` with preset `name` applied the panel's way: its
        run settings replace the current ones, but its analyses are ADDED,
        so choosing a preset never unticks an analysis the user turned on
        (Steady state used to drop Cost and Capacity)."""
        before = rx.normalise(experiment)
        after = rx.apply_profile(name, before)
        after["analyses"] = list(dict.fromkeys(
            [*before["analyses"], *after["analyses"]]))
        return rx.normalise(after)


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
        win = self.window()
        # A tab behind Results is moved off screen, not hidden, so
        # isHidden() alone kept the cap on it, and the shared strip could
        # not be dragged taller than the Run panel while Results was on show.
        active = getattr(win, "_bottom_active_dock", None)
        behind = active is not self if active is not None else self._behind_a_tab(win)
        if self.isFloating() or hidden or behind:
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

    def _behind_a_tab(self, win) -> bool:
        """True when a tab bar holds this dock and another tab is on show
        (a restored layout has no active bottom tab recorded yet)."""
        from PySide6.QtWidgets import QTabBar
        title = self.windowTitle()
        for bar in win.findChildren(QTabBar):
            titles = [bar.tabText(i) for i in range(bar.count())]
            if title in titles and bar.parent() is win:
                return bar.tabText(bar.currentIndex()) != title
        return False

    def _grow_to(self, want: int) -> None:
        """Ask the window for a taller bottom strip (one shared strip)."""
        win = self.window()
        if not isinstance(win, QMainWindow):
            return
        if win.dockWidgetArea(self) == Qt.DockWidgetArea.NoDockWidgetArea:
            return          # not in the layout: resizeDocks would only warn
        active = getattr(win, "_bottom_active_dock", None)
        if active is not None and active is not self:
            # Results/Data Tables are the front tab: the Run panel is
            # behind them and must not size the shared strip from its
            # hidden content.
            return
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
            # Never ask for more than the window can spare above the
            # ribbon, status bar and canvas minimum: an oversized Run
            # strip pushes the status bar off-screen and stops the window
            # shrinking (#231).
            limit = getattr(win, "_max_bottom_dock_height", None)
            request = want + strip
            if callable(limit):
                request = min(request, max(120, int(limit())))
            resize = getattr(win, "_resize_docks", win.resizeDocks)
            resize([self], [request], Qt.Orientation.Vertical)
        except Exception:                                  # noqa: BLE001
            pass
        finally:
            self._growing = False

    def showEvent(self, event):  # noqa: N802 (Qt spelling)
        super().showEvent(event)
        self.fit_height()

    def set_experiment(self, exp, *, enabled: bool = True) -> None:
        self.panel.set_experiment(exp, enabled=enabled)

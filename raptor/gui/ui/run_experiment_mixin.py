"""Run-experiment ownership: the single writer for every run setting.

Extracted from `main_window.py`, which had grown to 8230 lines and 241
methods. This is the one part of it with a genuinely clean boundary --
everything here is about "what is this run's configuration, and who is
allowed to change it" -- and extracting it is what makes that rule
enforceable rather than aspirational.

It is a MIXIN, not a service class, deliberately. These methods reach
into a dozen pieces of MainWindow state (the model, the undo stack, the
panel, the ribbon's mirror actions, the status bar, the current time
units), and turning them into a collaborator with an eight-argument
injected interface would have moved the coupling rather than removed
it. As a mixin the dependency is still explicit and greppable, but the
file is now readable on its own and `MainWindow` is 390 lines smaller.

The rule this module exists to enforce: exactly ONE writer
(`set_experiment_field`) for trials, termination, statistics start and
the analysis flags, and one mirror sync after every change. The failure
mode it replaces was three editors of the trial count with a
hand-maintained two-way sync, one of which (Project Settings) never
took part.

Collaborators it expects on `self`:
  _rbd, _diagram_dock.view (for the undo stack), _run_panel,
  _run_dock, _run_count_spin, _analysis_actions,
  _report_stream_actions, _statusbar, _inspector, _mark_dirty(),
  _current_units(), _validation_issues(), _sync_dock_toggles()
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QSpinBox

from raptor.core import run_experiment
from raptor.core import run_manifest
from raptor.core.fileio.rbd import RbdFile
from raptor.gui.ui import settings
from raptor.gui.ui.diagram_commands import apply_row_edits
from raptor import __version__


def _engine_version() -> str:
    """The version string recorded in a run manifest, so a result can be
    traced back to the engine that produced it."""
    return f"raptor-studio {__version__}"


#: `SYSTEM_DATA` column -> its human label. Used when a disabled analysis
#: toggle has to explain itself.
_ANALYSIS_LABELS = {
    "weakAnalysis": "Weak Link",
    "costAnalysis": "Cost",
    "capacityAnalysis": "Capacity",
}

#: The ENABLED-state tooltip for each analysis toggle, in full.
#:
#: Authoritative for both the ribbon (at construction) and the unlock
#: path, so re-enabling can never leave a control describing the locked
#: state. The trailing clause is load-bearing: these flags ARE written to
#: the model, because the engine reads them from `SYSTEM_DATA`
#: (`core/engine/load.py:176`). Unlike the Animate toggle they are
#: engineering data, not display state -- which is exactly why they must
#: be locked during a run rather than merely written.
_ANALYSIS_TIPS = {
    "weakAnalysis": "Track per-element state times for the Weak Link "
                    "view — writes this model's settings",
    "costAnalysis": "Accumulate life-cycle cost so the Cost tab "
                    "populates — writes this model's settings",
    "capacityAnalysis": "Record flow/capacity samples for the Capacity "
                        "plot — writes this model's settings",
}


class RunExperimentMixin:
    """Every read and write of the run experiment.

    `MainWindow` inherits this; the methods keep their leading
    underscore so the public surface of the window is unchanged and
    every existing call site and test keeps working untouched."""

    # The interface this mixin expects. Declared rather than left
    # implicit so the contract is visible at the top of the file AND so
    # the type checker can see it -- a mixin that reaches for
    # `self._rbd` without saying so is 40 "has no attribute" errors and
    # a dependency list you only find by reading every method.
    # All are provided by MainWindow; none is defined here.
    if TYPE_CHECKING:      # pragma: no cover - typing only
        _rbd: RbdFile | None
        _diagram_dock: Any
        _inspector: Any
        _statusbar: Any
        _run_panel: Any
        _run_dock: Any
        _run_count_spin: QSpinBox
        _analysis_actions: dict[str, QAction]
        _report_stream_actions: dict[str, QAction]
        _ribbon: Any
        _simulate_page_index: int

        def _mark_dirty(self) -> None: ...
        def _current_units(self) -> str: ...
        def _validation_issues(self) -> list[str]: ...
        def _run_or_step_active(self) -> bool: ...

    def _current_experiment(self) -> dict:
        """The open model's experiment, or a fresh default when none is
        open. Always returns a normalised copy, so a caller cannot
        corrupt the stored object by mutating what it is handed."""
        rbd = self._rbd
        if rbd is None:
            return run_experiment.defaults()
        if rbd.experiment is None:
            # No block yet (a legacy .rbd, or a .rbds written before the
            # block existed): derive one from the SYSTEM_DATA columns the
            # legacy format does have, so the model does not silently
            # report defaults that contradict its own row.
            sec = rbd.sections.get("SYSTEM_DATA")
            row = sec.rows[0] if sec and sec.rows else {}
            rbd.experiment = run_experiment.from_system_row(row)
        return run_experiment.normalise(rbd.experiment)

    def _set_recording(self, key: str, value: bool) -> None:
        """One recording/plot toggle -> the experiment, then every mirror
        (the panel's checkboxes, the ribbon's toggles) is re-synced from
        it, so no caller can leave a display out of step with the value
        the run will actually use."""
        if self._rbd is None:
            return
        experiment = self._current_experiment()
        if bool(experiment["recording"].get(key)) == bool(value):
            return
        experiment["recording"][key] = bool(value)
        self._rbd.experiment = experiment
        self._sync_run_mirrors()
        self._mark_dirty()

    def _effective_run_interval(self, key: str) -> float:
        """The sampling interval a run will actually use.

        Read from the model with a documented fallback, never from app
        settings: these were global QSettings values, so two models open
        side by side fought over them and neither was written down
        anywhere. `_migrate_run_interval` seeds the open model once from
        whatever the user had tuned, so upgrading does not silently
        change the resolution of anyone's existing runs.
        """
        exp: dict = self._current_experiment()
        try:
            return float(exp["recording"].get(
                key, run_experiment.RUN_DEFAULTS["recording"][key]))
        except (TypeError, ValueError):
            return float(run_experiment.RUN_DEFAULTS["recording"][key])

    def _set_exp_field(self, key: str, value) -> None:
        """One experiment field (trials, termination, ...) -> the
        experiment, AND into the legacy SYSTEM_DATA columns, as a single
        undoable macro.

        Both destinations are written because both are read: the engine
        still reads trials/termination/analyses from SYSTEM_DATA (a
        legacy file, or the MCP/API path, sees no experiment block), and
        the recording selection has no legacy column at all. Keeping
        them in step here is what stops the two from drifting -- the
        failure mode that had the run count editable in three places.
        """
        if self._rbd is None:
            return
        experiment = self._current_experiment()
        if experiment.get(key) == value:
            return
        # Mark the writes as run-setting writes BEFORE the first undoable
        # command lands: `_mark_dirty` reads this to avoid invalidating a
        # still-good validity answer (see `_carry_validity_across_run_
        # setting_edit`).
        self._in_run_setting_edit = True
        try:
            experiment[key] = value
            self._rbd.experiment = experiment

            sec = self._rbd.sections.get("SYSTEM_DATA")
            if not sec or not sec.rows:
                self._mark_dirty()
                return
            row = sec.rows[0]
            wanted = dict(run_experiment.to_system_row(experiment))
            v = self._diagram_dock.view
            # Run settings are NOT structural: the rule gate would
            # deep-copy the whole model and run every link/block rule
            # twice (before and after) to prove that changing a trial
            # count broke no link -- over a second per edit on a large
            # model. The panel's widgets constrain every value they can
            # write, and `_carry_validity_across_run_setting_edit` still
            # re-checks the SYSTEM_DATA rules afterwards, so an invalid
            # value disables Run rather than being refused here.
            with v._stack.ungated():
                apply_row_edits(v, "SYSTEM_DATA", 0, row, wanted.items(),
                                "Run settings")
            # The ribbon's read-only mirrors and the Inspector both show
            # values that came from this row.
            self._sync_run_mirrors()
            self._inspector.show_system_config()
            self._mark_dirty()
        finally:
            self._in_run_setting_edit = False
        # Changing a run setting cannot break the model's structure, so
        # do not pay for a whole-model re-validation 600 ms later. See
        # `_carry_validity_across_run_setting_edit`.
        self._carry_validity_across_run_setting_edit()

    def _sync_run_mirrors(self) -> None:
        """Push the experiment onto the ribbon's read-only display
        widgets and the run panel, if they exist."""
        experiment = self._current_experiment()
        spin = getattr(self, "_run_count_spin", None)
        if spin is not None and spin.value() != experiment["trials"]:
            spin.blockSignals(True)
            spin.setValue(int(experiment["trials"]))
            spin.blockSignals(False)
        for key, act in (getattr(self, "_analysis_actions", None) or {}).items():
            col = {"weakAnalysis": "weak", "costAnalysis": "cost",
                   "capacityAnalysis": "capacity"}[key]
            want = col in experiment["analyses"]
            if act.isChecked() != want:
                act.blockSignals(True)
                act.setChecked(want)
                act.blockSignals(False)
        for key, act in (getattr(self, "_report_stream_actions", None)
                         or {}).items():
            want = bool(experiment["recording"].get(key, False))
            if act.isChecked() != want:
                act.blockSignals(True)
                act.setChecked(want)
                act.blockSignals(False)
        panel = getattr(self, "_run_panel", None)
        if panel is not None:
            # A push that lands after File > Close must not re-enable the
            # panel: there is no model underneath it any more. This used to
            # depend on event ordering, so a closed window could come back
            # with an editable run panel.
            panel.set_experiment(
                experiment, enabled=self._rbd is not None
                and not getattr(self, "_run_in_progress", False))
            self._sync_run_panel_units()

    def _sync_run_panel_units(self) -> None:
        """Show the model's time unit on the panel's time-valued rows."""
        panel = getattr(self, "_run_panel", None)
        if panel is None:
            return
        if self._rbd is None:
            # Called during dock setup, before any model is open.
            panel.set_units("", None)
            if hasattr(panel, "set_importance_blocks"):
                panel.set_importance_blocks(0)
            return
        units = self._current_units() or ""
        panel.set_units(units, getattr(self._rbd, "display_unit", None))
        # The importance cost estimate is per block, so the panel needs the
        # count of the open model. `block_rows` is the same list the
        # analysis walks, so the estimate cannot disagree with the work.
        if hasattr(panel, "set_importance_blocks"):
            from raptor.api.importance import block_rows
            panel.set_importance_blocks(len(block_rows(self._rbd)))

    def _migrate_report_stream_settings(self) -> None:
        """Carry an upgrading user's `reports/*` recording choices onto
        the model being opened, ONCE.

        Without this, moving recording out of application settings into
        the model would silently switch every plot OFF for every model a
        returning user opens: their ticked boxes are in a `reports/`
        key that nothing reads any more, and the model's own experiment
        has no record of them. The review named this as the
        highest-probability silent regression in the whole change.

        Guarded by a one-shot marker so a deliberately-cleared recording
        is not re-applied by the next file the user opens, and so a
        SECOND model does not inherit the first's plots.
        """
        if settings.get_bool("experiments/recording_migrated"):
            return
        legacy = {}
        for key in ("event_log", "ao_plot", "capacity_plot", "cost_plot"):
            if settings.contains(f"reports/{key}"):
                legacy[key] = settings.get(f"reports/{key}")
        if legacy and self._rbd is not None:
            experiment = self._current_experiment()
            for key, value in legacy.items():
                experiment["recording"][key] = bool(value)
            self._rbd.experiment = experiment
            self._statusbar.showMessage(
                "Carried your previous plot/report choices onto this model", 6000)
        for key in ("event_log", "ao_plot", "capacity_plot", "cost_plot"):
            settings.remove(f"reports/{key}")
        settings.set_value("experiments/recording_migrated", True)

    def _build_run_manifest(self) -> dict:
        """Freeze what this run is, for the results to carry.

        Built on the calling thread, once, before the engine starts. It
        records the model's ENGINEERING content hash (which deliberately
        excludes the trial count, run length and analysis flags), so
        changing a run setting does not retroactively invalidate results
        already produced, while an actual change to the reliability model
        does. See `core/run_manifest.py` for why the ordering matters."""
        if self._rbd is None:
            return {}
        try:
            m = run_manifest.build_manifest(
                self._rbd, self._current_experiment(),
                profile=self._run_panel_profile()
                if getattr(self, "_run_panel", None) else "",
                engine_version=_engine_version())
            m["digest"] = run_manifest.manifest_digest(m)
            return m
        except Exception as exc:      # a manifest must never block a run
            self._statusbar.showMessage(
                f"Could not build the run manifest: {exc}", 6000)
            return {}

    def _run_panel_profile(self) -> str:
        panel = getattr(self, "_run_panel", None)
        if panel is None:
            return ""
        try:
            return str(panel._profile.currentData() or "")
        except AttributeError:
            return ""

    def _show_run_panel(self) -> None:
        """Bring the Run panel up.

        The ribbon's read-only trial spinner and the More Steps menu items
        both point here, which is how a value can stay out of the ribbon
        without becoming hard to reach: the control you click is adjacent
        to the setting it stands for, and the panel is where the edit
        happens."""
        dock = getattr(self, "_run_dock", None)
        if dock is None:
            return
        # The bottom slot's chrome is keyed by its first dock ("Results"),
        # not by whichever tab happens to be in front.
        chrome = (getattr(self, "_dock_chrome", None) or {}).get("Results")
        if chrome is not None and chrome["get"]()["minimized"]:
            chrome["set_minimized"](False)
        dock.show()
        dock.raise_()
        sync = (getattr(self, "_dock_state_sync", None) or {}).get(dock)
        if sync is not None:
            sync(True)
        if self._ribbon.tab_bar.currentIndex() != \
                getattr(self, "_simulate_page_index", -1):
            self._ribbon.select_page("Simulate")
        panel = getattr(self, "_run_panel", None)
        if panel is not None:
            panel.setFocus()

    def _on_panel_experiment(self, experiment: dict) -> None:
        """The panel edited something -> the model, through the one
        writer. The panel is a view; it never touches the row itself, so
        undo, dirty-tracking and the legacy-column mirror all stay in one
        place.

        Run settings are not structural, so this takes the same shortcut
        as `_set_exp_field`: the rule gate would deep-copy the model and
        run every link/block rule twice (about a second on a large model)
        to prove that ticking Weak Link broke no link, and the debounced
        whole-model validation would then run again 600 ms later. The
        SYSTEM_DATA rules are still re-checked, and a value that breaks
        them keeps the full validation queued.
        """
        if self._rbd is None:
            return
        self._rbd.experiment = run_experiment.normalise(experiment)
        sec = self._rbd.sections.get("SYSTEM_DATA")
        self._in_run_setting_edit = True
        try:
            if sec and sec.rows:
                row = sec.rows[0]
                wanted = dict(run_experiment.to_system_row(
                    self._rbd.experiment))
                v = self._diagram_dock.view
                with v._stack.ungated():
                    apply_row_edits(v, "SYSTEM_DATA", 0, row, wanted.items(),
                                    "Run settings")
            self._sync_run_mirrors()
            self._inspector.show_system_config()
            self._mark_dirty()
        finally:
            self._in_run_setting_edit = False
        self._carry_validity_across_run_setting_edit()
        self._update_readiness()

    def _on_panel_dependency(self, key: str, label: str) -> None:
        """The panel refused a dependent plot. Say why, and point at the
        fix, rather than silently unticking it."""
        self._statusbar.showMessage(
            f"{label} plot needs the {label} analysis — tick it above "
            f"first", 8000)

    def _sync_run_settings_lock(self) -> None:
        """Derive the run-settings lock from live state, and apply it.

        DERIVED, not remembered, on purpose. The lock has several entries
        (batch run start, batch run end) and many ways to end a step
        session (finish, terminate, discard-on-edit, Esc, restart).
        Tracking it as a flag is how a control ends up greyed out
        forever after a run that ended oddly; re-reading
        `_run_or_step_active()` every time means the only way to be wrong
        is if the thing being read is wrong.

        Safe to call anywhere, including from inside the run lifecycle's
        own start/end code -- which is why the two wrappers below both
        delegate here rather than each setting the state themselves.
        """
        active = bool(self._run_or_step_active())
        # Two independent reasons the panel must be read-only: a run is in
        # flight, OR there is no model underneath it. The second used to be
        # missing here, so any late call to this method -- it runs off
        # `_validation_timer`, which a queued event can land after File ▸
        # Close -- re-enabled an editable Run panel over an empty window.
        # `_sync_run_mirrors` had the same guard; this is the other entry.
        editable = self._rbd is not None and not active
        panel = getattr(self, "_run_panel", None)
        if panel is not None:
            panel.set_enabled(editable)
        # The ribbon's analysis toggles keep their old rule (a run locks
        # them, nothing else does): with no model open they are inert
        # anyway -- every writer they reach guards on `_rbd` -- and the
        # ribbon snapshot pins their post-close state.
        self._set_analysis_toggles_enabled(not active)
        if active:
            self._statusbar.showMessage(
                "Run settings are locked while the run is in progress")
        # Whether a stepped session exists (and has a later run to Jump to) is part of command availability.
        refresh = getattr(self, "_refresh_commands", None)
        if refresh is not None:
            refresh()

    def _sync_experiment_from_row(self) -> None:
        """Adopt the `SYSTEM_DATA` run fields into the run experiment.

        The chokepoint for a dialog that writes the row directly.

        Project Settings (`show_system_config`) is the legacy
        system-settings form and still owns termination, run length,
        statistics start and the analysis flags, so it cannot simply stop
        editing them. But it writes the ROW, while the run panel and the
        ribbon read the EXPERIMENT -- and nothing joined the two. That is
        how the trial count ended up showing 20 in Project Settings and 1
        in the panel at the same time.

        Called after such a dialog commits, so there is exactly one
        direction of flow: row -> experiment -> every mirror. The
        experiment stays the single reader for the run, and the legacy
        columns stay writable for the engine and for older tools.
        """
        if self._rbd is None:
            return
        sec = self._rbd.sections.get("SYSTEM_DATA")
        if not sec or not sec.rows:
            return
        row = sec.rows[0]
        experiment = run_experiment.normalise(
            run_experiment.from_system_row(row))
        # Recording is not in the row, so carry it over rather than
        # resetting the plot selection to defaults behind the user's
        # back.
        current = self._current_experiment()
        experiment["recording"] = dict(current["recording"])
        # `importance` is experiment-only in exactly the same way: no
        # SYSTEM_DATA column exists for it, so a row -> experiment sync
        # reset it to "none" behind the user's back. Ticking the box and
        # pressing Run could therefore run without the analysis (observed
        # as `mode: none` with the box visibly ticked).
        experiment["importance"] = current.get("importance", "none")
        # A plot whose analysis is off cannot be collected. The modal path
        # used to carry its tick over unconditionally, leaving a checked
        # plot that produced nothing.
        for plot_key, (need, _label) in run_experiment.RECORDING_REQUIRES.items():
            if need not in experiment["analyses"]:
                experiment["recording"][plot_key] = False
        if experiment == current:
            return
        self._rbd.experiment = experiment
        self._sync_run_mirrors()
        self._update_readiness()

    def _schedule_settings_resync(self, _index: int | None = None) -> None:
        """Queue one resync after an undo/redo (or any stack change).

        The row is the persisted truth; the experiment and every mirror
        must follow it. Deferred one event-loop turn so a macro is fully
        pushed/popped before it is read.
        """
        if getattr(self, "_settings_resync_pending", False):
            return
        self._settings_resync_pending = True
        QTimer.singleShot(0, self._sync_settings_from_model)

    def _sync_settings_from_model(self, *, refresh_inspector: bool = True) -> None:
        self._settings_resync_pending = False
        if getattr(self, "_rbd", None) is None:
            return
        try:
            self._sync_experiment_from_row()
            # Thresholds/metric are not part of the run experiment; read
            # them from the same row, then refresh every consumer.
            self._sync_analysis_checkboxes()
            self._sync_run_panel_units()
            self._push_weak_settings()
            # Only refresh the Inspector if it is actually showing
            # Project Settings; an undo while a block/node is selected
            # must not yank the user to the system page. A config edit
            # made IN the Inspector skips this, so the page does not
            # rebuild (and jump to the top) under the cursor.
            if refresh_inspector and \
                    self._inspector.current_target() == ("system", None):
                self._inspector.show_system_config()
            self._update_readiness()
        except RuntimeError:
            # The deferred singleShot can fire after the window's C++
            # widgets were deleted (tests close windows aggressively).
            return

    def _disable_run_panel_for_run(self) -> None:
        """A run has started, so lock the run settings.

        Delegates to `_sync_run_settings_lock`, which derives rather than
        assumes: at this point a run IS active, so the derived answer is
        "locked", and the same call at the end of the run unlocks without
        needing a paired method that has to stay in step with it.
        """
        self._sync_run_settings_lock()
        self._update_readiness()

    def _reenable_run_panel(self) -> None:
        self._sync_run_settings_lock()
        self._update_readiness()

    def _set_analysis_toggles_enabled(self, enabled: bool) -> None:
        """Enable/disable the Simulate tab's analysis toggles in step
        with the run panel's Analysis group.

        The same three flags, two places -- so they must move together.
        A disabled toggle says WHY, per the spec requirement that every
        disabled control explain its unmet condition
        (the internal UI blueprint); the reason here is the one
        thing a user cannot guess, because the controls work perfectly
        well the moment the run ends.
        """
        acts = getattr(self, "_analysis_actions", None) or {}
        for col, act in acts.items():
            act.setEnabled(bool(enabled))
            if enabled:
                act.setToolTip(_ANALYSIS_TIPS.get(col, ""))
            else:
                act.setToolTip(
                    f"{_ANALYSIS_LABELS.get(col, col)} analysis is a run "
                    f"setting, so it is locked while a simulation is in "
                    f"progress or a stepped session is open. It applies to "
                    f"the NEXT run.")

    def _update_readiness(self) -> None:
        """The ready / not-ready line, carrying the REASON.

        The spec requires a disabled action to explain itself
        (`blueprint:125`) and a readiness state per the job machine
        (`blueprint:484-491`). A greyed Run button with a generic tooltip
        is what the review called the worst discoverability gap, so the
        reason is computed here and shown, not merely set on a tooltip.
        """
        panel = getattr(self, "_run_panel", None)
        if panel is None:
            return
        if self._rbd is None:
            panel.set_readiness(
                False, "No model open — File ▸ Open or New to begin.")
            return
        # Validity is memoised per model revision and costs nothing when
        # it is already known. Ask for the whole issue LIST only when the
        # model is actually invalid: readiness needs the count, not the
        # list, and rebuilding the list on every Simulate-tab click is
        # what made opening the tab pause on a large model.
        valid = self._model_is_valid()
        issues = [] if valid else self._validation_issues()
        experiment = self._current_experiment()
        summary = (f"{experiment['trials']:,} trial(s) · "
                   f"{self._term_words(experiment)}")
        if getattr(self, "_run_or_step_active", lambda: False)():
            stepped = (getattr(self, "_step_session", None) is not None
                       and not getattr(self, "_run_in_progress", False))
            if stepped:
                panel.set_readiness(
                    False,
                    "Step session in progress — Finish or Terminate to run "
                    "again.")
                return
            paused = bool((getattr(self, "_bg_state", None) or {}).get(
                "paused"))
            panel.set_readiness(
                False,
                f"{'Paused' if paused else 'Running'} — {summary}. "
                "Run settings are locked until it finishes.")
            return
        if issues:
            # The count and a way to the list -- the Issues list is where
            # problems are read. This line quoted the first one in full,
            # which made the Run panel read like a validation report.
            n = len(issues)
            panel.set_readiness(
                False,
                f"Not ready — the model has {n} problem"
                f"{'s' if n != 1 else ''}. <a href='issues'>Show them</a>",
                link=lambda _href: self._validate_model(quiet=False))
            return
        # DELEGATED (review E-15). This used to report the panel as *not
        # ready* -- a blocking red banner -- while `run_panel`'s field
        # styling drew a red border and explained itself. Same condition,
        # same screen, two severities. `stats_start_is_blocking()` is the
        # severity question, answered once: it is a warning, because the run
        # still executes and still produces numbers.
        from raptor.core.run_experiment import (
            stats_start_is_blocking, stats_start_warning,
        )
        warn = stats_start_warning(experiment)
        # Model warnings are NOT repeated here: they are the Issues list's
        # (the ribbon's Issues button and the status-bar badge). This line
        # is about the RUN -- its size and its own settings.
        # How it will run, in plain sight: one core animated, or N cores.
        from raptor.gui import cores
        animate = bool(getattr(self, "_live_sim_action", None)
                       and self._live_sim_action.isChecked())
        text = f"Ready. {summary} · {cores.describe(animate)}."
        if warn:
            text += f" Warning: {warn}"
        panel.set_readiness(not (warn and stats_start_is_blocking()), text)

    def _term_words(self, experiment: dict) -> str:
        units = self._current_units() or ""
        t = experiment["termination_type"]
        n = experiment["term_condition"]
        if t == "TimeSim":
            return f"{n:g} {units or 'time units'}"
        if t == "FailSim":
            return f"{n:g} system failures"
        return f"{n:g} phase cycles"

    def _on_run_count_spin_edited(self):
        """Ribbon "Trials" spinner -> the run experiment.

        This used to write `SYSTEM_DATA/NumRuns` directly, which is how
        the same value ended up editable in three places (this spinner,
        Sim Options, and Project Settings) with a hand-maintained
        two-way sync between them -- and Project Settings did not push
        back here at all, so the two could and did drift. There is now
        ONE writer (`_set_exp_field`), and the spinner is one of its
        callers, along with the run panel.

        `editingFinished`, not `valueChanged`, is the signal: a spin box
        emitting on every keystroke would push an undo command per
        digit."""
        if self._rbd is None:
            return
        self._set_exp_field("trials", self._run_count_spin.value())

    def _set_analysis_flag(self, column: str, on: bool, label: str):
        """One analysis toggle -> the run experiment's `analyses` list
        (undoable, and mirrored into the legacy flags by
        `_set_exp_field`).

        Also drives the dependent recording row: turning Capacity off
        while the capacity plot is ticked is exactly the unenforced
        dependency the review found, so it is resolved here -- at the
        point the user acts -- as well as in the command layer.

        Refuses while a run or step session is open. Disabling the
        actions is what stops a *mouse*; this stops everything else, and
        it is the same rule the Run panel already applies to its own
        controls (`RunPanel._emit_change` returns early when disabled).
        A `QAction` disabled via `setEnabled(False)` still emits
        `toggled` if anything sets its checked state in code, and the
        write that follows would raise the edit-during-run guard's modal
        -- the exact "pile of dialogs" this locking exists to remove.
        The checkbox is put back, so the button cannot be left showing
        a state the model does not have.
        """
        if self._rbd is None:
            return
        name = {"weakAnalysis": "weak", "costAnalysis": "cost",
                "capacityAnalysis": "capacity"}.get(column)
        if name is None:
            return
        if self._run_or_step_active():
            # Snap the button back to what the MODEL says, not to `not on`.
            # By the time `toggled` fires, Qt has already set the action to
            # the new value, so `on` is the wrong thing to compare
            # against: the action reads True while the model still reads
            # 0, and reverting to `not on` would leave the control showing
            # a state the model does not have -- and, worse, a later
            # `setChecked` to that same value would then be a no-op and
            # emit nothing, so the toggle would appear stuck off. Reading
            # the experiment keeps the control a mirror of the truth even
            # when it refuses.
            act = (getattr(self, "_analysis_actions", None) or {}).get(column)
            if act is not None:
                want = name in self._current_experiment()["analyses"]
                if act.isChecked() != want:
                    act.blockSignals(True)
                    act.setChecked(want)
                    act.blockSignals(False)
            return
        experiment = self._current_experiment()
        analyses = [a for a in experiment["analyses"] if a != name]
        if on:
            analyses.append(name)
        self._set_exp_field("analyses", analyses)

        if column == "capacityAnalysis" and hasattr(self, "_results"):
            # Capacity plot availability follows the flag live (grey the
            # mode out when the analysis is off).
            self._results.set_capacity_available(on)
        # Drop a now-impossible dependent plot rather than letting the
        # run silently produce nothing.
        dep = {"capacity": ("capacity_plot", "Capacity"),
               "cost": ("cost_plot", "Cost")}.get(name)
        if dep and not on:
            key, label2 = dep
            if experiment["recording"].get(key):
                self._set_recording(key, False)
                self._statusbar.showMessage(
                    f"{label2} plot recording turned off — it needs the "
                    f"{label2} analysis", 6000)
                return
        self._statusbar.showMessage(
            f"{label} analysis {'enabled' if on else 'disabled'} "
            f"for the NEXT run", 6000)

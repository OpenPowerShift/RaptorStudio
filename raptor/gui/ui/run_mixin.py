"""Starting a run: scope, model preparation, the foreground simulation and its results.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations

from raptor.gui.run_text import run_outcome_text
from raptor.gui.ui.foreground_run import ForegroundRun

from raptor.gui.ui.qtcompat import req

from raptor.gui.ui import msgbox
import time
from typing import TYPE_CHECKING, Any
from pathlib import Path
from PySide6.QtCore import (Qt, QEventLoop)
from PySide6.QtWidgets import (
    QApplication, QMessageBox,
)

from raptor.gui.ui import filedialogs as fdg
from raptor.core.fileio.typed import system_config_from_row
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.engine.errors import RunAborted




def _system_units(rbd):
    """The model's display units from its SYSTEM_DATA row, or None."""
    try:
        cfg_section = rbd.sections.get("SYSTEM_DATA")
        if cfg_section and cfg_section.rows:
            return system_config_from_row(cfg_section.rows[0]).units
    except (ValueError, KeyError):
        pass
    return None


if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtGui
    import PySide6.QtWidgets
    import raptor.gui.ui.diagram
    import raptor.gui.ui.results
    import raptor.gui.ui.run_panel
else:
    _Base = object


class RunMixin(_Base):
    """Starting a run: scope, model preparation, the foreground simulation and its results."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        def _apply_hier_status(self, report_data: Any) -> None: ...
        def _begin_importance_progress(self, total: Any) -> None: ...
        def _build_run_manifest(self, rbd: Any = ..., isolation: Any = ...) -> dict: ...
        def _checked_overlay_state(self) -> dict[str, bool]: ...
        def _clear_run_eta(self) -> None: ...
        def _current_experiment(self) -> dict: ...
        _current_file: str | None
        _diag_name: str
        _diagram_dock: raptor.gui.ui.diagram.DiagramDock
        def _disable_run_panel_for_run(self) -> None: ...
        @staticmethod
        def _discard_report_streams(streams: Any) -> None: ...
        _edit_run_abandoned: bool
        def _effective_run_interval(self, key: str) -> float: ...
        def _hide_phase_labels(self) -> None: ...
        _imp_eta: Any  # really None
        _imp_last_key: Any
        def _importance_for(self, run_model: Any, report_data: Any) -> Any: ...
        _importance_overlay_action: Any
        _importance_running: bool
        def _invalidate_step_session(self) -> Any: ...
        _last_run_model: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _leave_fev(self) -> None: ...
        _live_sig: Any  # really None
        _live_sim_action: PySide6.QtGui.QAction
        def _mark_dirty(self) -> Any: ...
        def _maybe_save_before_run(self) -> bool: ...
        def _note_results_basis(self) -> None: ...
        def _note_run_profile(self, done_runs: int) -> None: ...
        def _open_report_streams(self, choices: dict[str, bool] | None = ...) -> dict: ...
        _pause_action: Any
        def _preflight_issues(self, rbd: Any = ...) -> list[str]: ...
        def _present_results(self) -> Any: ...
        def _push_weak_settings(self) -> Any: ...
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _reapply_checked_overlays(self, state: dict[str, bool] | None) -> None: ...
        def _reenable_run_panel(self) -> None: ...
        def _refresh_commands(self, state: Any = ..., only: set[str] | None = ...) -> None: ...
        def _remember_report_streams(self, streams: dict) -> None: ...
        def _report_stage_progress(self, done: int, total: int, label: str) -> None: ...
        _report_stream_actions: dict[str, PySide6.QtGui.QAction]
        def _reset_live_log(self) -> Any: ...
        def _reset_status_summary(self) -> Any: ...
        _results: raptor.gui.ui.results.ResultsDock
        _results_float_action: Any
        _results_show_action: Any
        _run_full_speed: bool
        _run_in_progress: bool
        _run_panel: raptor.gui.ui.run_panel.RunPanel
        _run_progress: PySide6.QtWidgets.QProgressBar
        _run_progress_label: PySide6.QtWidgets.QLabel
        _run_scope_return_level: int | None
        def _set_results_with_progress(self, *args: Any, follow_up: bool = ..., **kwargs: Any) -> Any: ...
        def _start_background_run(self, num_runs: int) -> Any: ...
        def _start_run_eta(self) -> None: ...
        _statusbar: PySide6.QtWidgets.QStatusBar
        _step_finalize_state: Any
        def _sync_overlay_actions(self, report_data: Any) -> None: ...
        _sync_stop: bool
        _undo_stack: PySide6.QtGui.QUndoStack
        def _update_status_summary(self, report_data: list, units: str | None) -> Any: ...

    def _model_for_engine(self, copy: bool = False):
        """The model as the user sees it, for the engine.

        Edits made on the canvas or in the Inspector live in the view's
        element objects until something writes them back to the file rows
        -- and the engine reads the rows. Nothing did before a run, so the
        first run after an edit simulated the PREVIOUS model (a PM interval
        changed to 300 ran at 400; the next run was right). Every engine
        entry point takes its model from here.

        `copy=True` (the background run) hands the engine a private copy: a
        worker thread reading the live model raced any edit made while it
        ran and failed mid-run on a half-edited diagram."""
        import copy as _copy
        rbd = req(self._rbd)
        view = self._diagram_dock.view
        view.flush_pending_moves()     # before the run, not during it
        view.write_back_to_rbd(rbd)
        return _copy.deepcopy(rbd) if copy else rbd

    def _refresh_run_scope_options(self) -> None:
        """Keep the Run panel's scope list in step with the model."""
        panel = getattr(self, "_run_panel", None)
        if panel is None:
            return
        if self._rbd is None:
            panel.set_hierarchies([])
            return
        view = self._diagram_dock.view
        rows: list[tuple[int, str]] = []
        for hid in view.hiers:
            names: list[str] = []
            cur = hid
            seen: set[int] = set()
            while cur and cur in view.hiers and cur not in seen:
                seen.add(cur)
                names.append(view.hiers[cur].name or f"Hierarchy {cur}")
                cur = view.hiers[cur].parent_id
            rows.append((hid, "  " * (len(names) - 1) + " / ".join(
                reversed(names))))
        rows.sort(key=lambda r: r[1].lower())
        panel.set_hierarchies(rows)

    def _on_run_scope_changed(self, hid: int) -> None:
        """Follow the Run panel's scope: show the hierarchy, or go back."""
        view = self._diagram_dock.view
        if hid:
            if self._run_scope_return_level is None:
                self._run_scope_return_level = view._current_parent
            if hid in view.hiers:
                view._set_level(hid)
            self._statusbar.showMessage(
                "Running one hierarchy: results will be scoped to it", 6000)
        else:
            level = self._run_scope_return_level
            self._run_scope_return_level = None
            if level is not None:
                view._set_level(level if (not level or level in view.hiers)
                                else 0)
            self._statusbar.showMessage("Run scope: whole model", 4000)
        # Scoped and whole-model results must never sit side by side as
        # though they described the same system.
        if self._results.has_results():
            self._results.clear()
            view.clear_result_overlays()
            self._statusbar.showMessage(
                "Results cleared: the run scope changed", 6000)

    def _prepare_run_model(self, copy: bool = False):
        """The model the next run should use, plus its isolation record.

        A hierarchy scope extracts in memory: the source model and file
        are never replaced, so Save can not write the extract by mistake.
        Returns (None, None) after telling the user why the run was
        cancelled.
        """
        model = self._model_for_engine(copy=copy)
        hid = self._run_panel.hierarchy_scope() \
            if getattr(self, "_run_panel", None) is not None else 0
        if not hid:
            return model, None
        from raptor.api import isolate
        try:
            extracted, record = isolate.extract(
                model, hid, self._run_panel.scope_treatments())
        except isolate.IsolationError as exc:
            msgbox.warning(self, "Run Hierarchy", str(exc))
            return None, None
        from raptor.api.validate import issues
        errors = [issue for issue in issues(extracted)
                  if getattr(issue, "severity", "") == "error"]
        if errors:
            msgbox.critical(
                self, "Run Hierarchy",
                "The isolated hierarchy failed validation; fix these "
                "before running:\n\n"
                + "\n".join(str(issue) for issue in errors[:10]))
            return None, None
        return extracted, record

    def _open_diff_dialog(self, other_path: str | None = None,
                          open_model=None):
        """Model > Differences: this model against its saved file, another
        model file, or a model open in another window
        (`ui/diff_dialog.py`)."""
        from raptor.gui.ui.diff_dialog import open_diff
        return open_diff(self, other_path, open_model)

    def _open_sweep_dialog(self):
        """Simulate > Batch > Sweep: one number over a range, at full speed."""
        return self._open_batch_dialog("_sweep_dialog", "SweepDialog")

    def _open_compare_dialog(self):
        """Simulate > Batch > Compare: this model beside other model files."""
        return self._open_batch_dialog("_compare_dialog", "CompareDialog")

    def _open_batch_dialog(self, attr: str, cls_name: str):
        # One window of each kind. It is kept -- with its results and
        # inputs -- unless the model has changed since it was built (another
        # file, or an edit), when its element list would be stale. A
        # running one is always kept.
        from raptor.gui.ui import batch_dialogs
        dlg = getattr(self, attr, None)
        key = (id(self._rbd), self._undo_stack.index(), self._current_file)
        running = dlg is not None and dlg._job is not None \
            and dlg._job.is_running()
        if dlg is not None and not running and dlg._model_key != key:
            dlg.close()
            dlg.deleteLater()
            dlg = None
        if dlg is None:
            self._model_for_engine()          # the rows match the canvas
            dlg = getattr(batch_dialogs, cls_name)(self)
            dlg._model_key = key
            setattr(self, attr, dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _on_run(self):
        """Run (F5). Animate on: the one-core run you watch, here on the
        GUI thread with every event drawn. Animate off: full speed, in the
        background on the cores in the Cores box (Pause, Stop and Jump all
        work). There is no separate "Run in Background" switch any more:
        whether a run is in the background follows from how it runs."""
        self._run_full_speed = not self._live_sim_action.isChecked()
        try:
            self._run_simulation()
        finally:
            self._run_full_speed = False

    def _present_baseline_results(self, *, logs, eor, fin, report_data,
                                  notes, ao_lines, cap_lines, cost_lines,
                                  units, manifest, importance_total=None):
        """Put the finished baseline on screen while importance runs.

        Called once per run, on the GUI thread. `follow_up=True` keeps the
        progress bar alive and below 100%: the next thing the user sees is
        the importance phase, not a `100/100` that then sits there. The
        final refresh is still allowed to replace this population with one
        that includes the Importance tab.
        """
        if getattr(self, "_results_populating", False):
            return False
        self._results._source_file_name = self._current_file or ""
        ok = self._set_results_with_progress(
            logs, eor, fin, report_data=report_data, units=units,
            notes=notes, ao_lines=ao_lines, cap_lines=cap_lines,
            cost_lines=cost_lines, manifest=manifest, importance=None,
            follow_up=True)
        if not ok:
            return False
        self._note_results_basis()
        self._results_show_action.setEnabled(bool(report_data))
        self._results_float_action.setEnabled(bool(report_data))
        self._sync_overlay_actions(report_data)
        self._apply_hier_status(report_data)
        self._present_results()
        self._begin_importance_progress(importance_total or 1)
        return True

    def _present_background_baseline(self, state) -> bool:
        """The background worker's baseline, rendered by the GUI thread.

        The worker deliberately keeps `done` False until importance ends,
        so this is the only chance to show the baseline before the extra
        batches are spent. Returns True only when the population actually
        ran; the poll retries on the next tick if another population was
        still in flight.
        """
        result = state.get("result")
        if not result:
            return False
        try:
            (logs, eor, fin, report_data, report_notes, ao_lines,
             cap_lines, cost_lines, units) = result
        except (TypeError, ValueError):
            return False
        self._push_weak_settings()
        result_streams = state.get("streams") or {}
        eor_source = result_streams.get("end_of_run")
        if eor_source is None:
            eor_source = eor
        total = state.get("importance_total")
        if not total:
            progress = state.get("importance_progress")
            total = progress[1] if progress else 1
        return self._present_baseline_results(
            logs=logs, eor=eor_source, fin=fin, report_data=report_data,
            notes=report_notes, ao_lines=ao_lines, cap_lines=cap_lines,
            cost_lines=cost_lines, units=units,
            manifest=(state.get("run_metadata") or {}).get("manifest"),
            importance_total=total)

    #: Ask before a run that a MEASURED previous run of this model says
    #: will take longer than this many seconds.
    _LONG_RUN_SECONDS = 30.0
    #: With nothing measured, ask only when the rough guess is this long:
    #: twenty times the limit above, so a guess that is badly high still
    #: means a run worth asking about.
    _LONG_RUN_ROUGH_SECONDS = 600.0

    def _confirm_long_batch(self, num_runs: int) -> bool:
        """Ask before a batch that will run for a long time.

        Not a pilot: a pilot on a slow model costs as much as the short run
        it would protect. The time quoted, when one is, is this model's own
        last run scaled to the new size.
        """
        from raptor.gui import cores
        try:
            experiment = self._current_experiment()
            horizon = float(experiment.get("term_condition") or 0.0)
        except (TypeError, ValueError, AttributeError):
            return True
        if horizon <= 0:
            return True
        # Importance measures are extra batches after the baseline; the
        # time until completion includes them, so the guard must too.
        importance_batches = 0
        importance_mode = "none"
        try:
            exp_mode = str(experiment.get("importance", "none") or "none")
            if exp_mode not in ("", "none") and self._rbd is not None:
                from raptor.api import importance as _imp
                importance_mode = exp_mode
                importance_batches = max(
                    0, _imp.batch_count(
                        len(_imp.block_rows(self._rbd)), exp_mode) - 1)
        except (ImportError, KeyError, ValueError, TypeError):
            importance_batches = 0
        batches = 1 + importance_batches
        # Asked only when the run is expected to take minutes. The guard
        # used to be run-hours alone (50,000 and up), which says nothing
        # about time: it stopped the shipped examples, which finish in
        # seconds, behind a warning about "tens of minutes".
        expected: float | None = None
        try:
            from raptor.core.storage_estimate import (cycles_per_time,
                                                     element_count,
                                                     rough_run_seconds)
            sized = dict(experiment, trials=max(1, int(num_runs)))
            full_speed = bool(getattr(self, "_run_full_speed", False))
            expected = rough_run_seconds(
                sized, cycle_rate=cycles_per_time(self._rbd)[0],
                elements=element_count(self._rbd), batches=batches,
                cores=cores.chosen() if full_speed else 1)
        except Exception:  # noqa: BLE001 - fall back to the coarse guard
            expected = None
        # A figure is only quoted when it was measured: the last run of this
        # model, scaled to this one's size. Without one there is only the
        # rough guess from how often the blocks fail, which can be several
        # times out either way, so it is never shown and has to be very
        # large before it is worth interrupting for. Asking is a nag; it
        # must not happen for a run that turns out to take seconds.
        measured: float | None = None
        previous = getattr(self, "_last_run_profile", None)
        if previous and getattr(self, "_last_run_profile_rbd", None) == id(self._rbd):
            try:
                ph, pn, pwall, pworkers, pbatches = previous
            except ValueError:
                ph, pn, pwall, pworkers = previous
                pbatches = 1
            if ph and pn and pwall:
                measured = (pwall * (horizon / ph) * (num_runs / pn)
                            * (batches / max(1, pbatches))
                            * (max(1, pworkers) / max(1, cores.chosen())))
        if measured is not None:
            if measured < self._LONG_RUN_SECONDS:
                return True
        elif expected is None or expected < self._LONG_RUN_ROUGH_SECONDS:
            return True
        extra = (f"\n\nImportance measures are on: {importance_mode}, "
                 f"{importance_batches} extra batch(es), {batches} total. "
                 f"The time until completion includes them."
                 if importance_batches else "")
        reference = ""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Long simulation")
        size = (f"{num_runs:,} trial(s) × {horizon:,.0f} "
                f"{self._current_units() or 'time units'}")
        if measured is not None:
            about = (f"about {measured:.0f} seconds" if measured < 90
                     else f"about {measured / 60:.0f} minutes"
                     if measured < 5400
                     else f"about {measured / 3600:.1f} hours")
            lead = (f"This run ({size}) should take {about}, going by this "
                    f"model's last run ({pn:,} trial(s) × {ph:,.0f} took "
                    f"{pwall:.0f} s).")
        else:
            lead = (f"This run ({size}) is likely to take several minutes "
                    f"or longer. The time left is shown in the status bar "
                    f"once it is under way.")
        box.setText(
            f"{lead}{extra}{reference}\n\n"
            "You can stop it at any time and keep the trials that have "
            "finished. For a quick check, lower Run length or Trials.")
        run_btn = box.addButton("Run", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(run_btn)
        box.exec()
        if box.clickedButton() is run_btn:
            return True
        self._statusbar.showMessage("Run cancelled — nothing was started", 5000)
        return False

    def _run_may_start(self) -> bool:
        """The early exits shared by every foreground run."""
        if getattr(self, "_results_populating", False):
            # The previous batch's results are still being built and the
            # population stages pump the event loop (paint feedback).
            # Starting a run now would clear the dock under its own
            # half-built tables. The click is queued, not lost.
            self._statusbar.showMessage(
                "Still rendering the previous results — try again in a "
                "moment.", 5000)
            return False
        if self._step_finalize_state is not None:
            self._statusbar.showMessage(
                "Finalizing the stepped run — wait for its results", 5000)
            return False
        return self._rbd is not None

    def _confirm_unsupported_features(self, run_model) -> bool:
        """Warn about features the engine will reject mid-run with
        NotImplementedError; False when the user cancels."""
        preflight = self._preflight_issues(run_model)
        if not preflight:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsupported Model Features")
        box.setDetailedText("\n".join(preflight))
        from raptor.gui.ui.preflight import run_dialog_text
        box.setText(run_dialog_text(preflight))
        run_btn = box.addButton("Run Anyway", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not run_btn:
            self._statusbar.showMessage("Run cancelled — unsupported "
                                        "features present")
            return False
        return True

    def _run_simulation(self):
        # Synchronous/blocking: the engine runs on the GUI thread, and
        # the progress/event callbacks pump the event loop so the
        # window stays responsive and Stop can land mid-run.
        if not self._run_may_start():
            return
        # A spin box commits on editingFinished, which F5 does not cause.
        # Clear focus so the number on screen is the number the run uses.
        focused = QApplication.focusWidget()
        if focused is not None:
            focused.clearFocus()
            QApplication.processEvents()
        # Failure Effects View holds live engine objects of its own. A
        # new run supersedes it; leave it explicitly instead of making
        # Run unavailable because a toggle on another tab is on.
        if getattr(self, "_fev", None) is not None:
            self._leave_fev()
            self._statusbar.showMessage(
                "Left Failure Effects View to start the run", 5000)
        if not self._maybe_save_before_run():
            self._statusbar.showMessage("Run cancelled")
            return
        run_model, isolation = self._prepare_run_model()
        if run_model is None:
            return
        self._last_run_model = run_model
        # Read the experiment, not the ribbon mirror: the mirror can be
        # stale after undo/redo or an external model edit, and the next
        # run must use the same value the panel shows and the manifest
        # records.
        num_runs = int(self._current_experiment()["trials"])
        self._write_dev_trees()
        from raptor.core import diagnostics as _dg
        self._diag(*_dg.pre_sim_lines(), _dg.start_simulation_line())
        mission_path = self._ask_mission_path()
        cumulative_trial_path = self._ask_cumulative_trial_path()
        cumulative_time_path = self._ask_cumulative_time_path()
        self._invalidate_step_session()
        # A new run resets the edit-during-run acknowledgement (the
        # previous run's "cancel" choice must not silence this run's
        # own guard).
        self._edit_run_abandoned = False
        if not self._confirm_unsupported_features(run_model):
            return
        if getattr(self, "_run_full_speed", False):
            if not self._confirm_long_batch(num_runs):
                return
            self._start_background_run(num_runs)
            return
        if not self._confirm_long_batch(num_runs):
            return
        self._statusbar.showMessage(f"Running {num_runs} run(s)...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # Units are needed by the per-run status refresh, so parse them
        # before the batch starts.
        units = _system_units(self._rbd)
        self._lock_for_foreground_run()
        report_data: list = []
        report_notes: list[str] = []
        # Files & Reports opt-ins: plot sinks are only created -- and
        # only handed to the engine -- when their Simulate ▸ Files &
        # Reports checkbox is ticked; each one is a disk-backed
        # ResultStream, so the engine's rows never accumulate in RAM.
        # Empty lists stay for the disabled ones so the code below can
        # treat them uniformly.
        streams = self._open_report_streams()
        # Note: keep the raw stream objects (a fresh stream is falsy —
        # never let `or []` substitute a list, or set_results loses the
        # disk-backed sink).
        ao_lines = streams.get("ao")
        cap_lines = streams.get("capacity")
        run_metadata: dict = {}
        # Freeze the manifest BEFORE the engine is handed anything, so it
        # describes the run that actually happened even if the model is
        # edited while the engine works.
        run_metadata["manifest"] = self._build_run_manifest(
            run_model, isolation)
        hooks = ForegroundRun(
            self, num_runs=num_runs, units=units,
            live_view=self._live_sim_action.isChecked(),
            report_data=report_data, run_metadata=run_metadata,
            ao_lines=ao_lines, cap_lines=cap_lines)
        try:
            event_logs, end_of_run_lines, final_results_lines = run_simulation_with_reports(
                run_model, num_runs=num_runs, report_sink=report_data,
                progress=hooks.progress, notes_sink=report_notes,
                on_event=hooks.on_event,
                # Enabled sinks pass their ResultStream (never truthiness
                # tests — a fresh empty stream must still collect);
                # disabled ones pass None so the engine skips them.
                ao_sink=streams.get("ao"),
                capacity_sink=streams.get("capacity"),
                cost_sink=streams.get("cost"),
                end_of_run_sink=streams.get("end_of_run"),
                report_progress=self._report_stage_progress,
                collect_logs=self._report_stream_actions[
                    "event_log"].isChecked(),
                metadata_sink=run_metadata,
                ao_plot_interval=self._effective_run_interval("ao_interval"),
                cost_plot_interval=self._effective_run_interval(
                    "cost_interval"),
                count_draws=self._dev_options().random_draw_count,
                sim_details_path=self._sim_details_path(),
                sim_details_append=True,
                mission_path=mission_path,
                defer_trigger=self._dev_options().defer_trigger,
                cumulative_trial_path=cumulative_trial_path,
                cumulative_trial_interval=self._dev_options().cumulative_by_trial,
                cumulative_time_path=cumulative_time_path,
                cumulative_time_mesh=self._dev_options().cumulative_by_time,
                keep_partial_logs=True,
            )
        except RunAborted:
            # Stop pressed (between runs via progress, or now mid-run via the
            # event hook): keep completed results and fall through to the normal
            # results path (flag already set).
            pass
        except NotImplementedError as e:
            self._abandon_foreground_run(streams)
            msgbox.warning(
                self, "Unsupported Model Feature",
                f"This model uses a feature the simulation engine doesn't support yet:\n\n{e}",
            )
            self._statusbar.showMessage("Run failed — unsupported feature")
            return
        except Exception as e:
            self._abandon_foreground_run(streams)
            msgbox.critical(self, "Simulation Failed", f"The run failed:\n\n{e}")
            self._statusbar.showMessage("Run failed")
            return
        QApplication.restoreOverrideCursor()
        self._present_foreground_run(
            run_model, event_logs, end_of_run_lines, final_results_lines,
            streams=streams, report_data=report_data, report_notes=report_notes,
            run_metadata=run_metadata)

    def _lock_for_foreground_run(self) -> None:
        """Re-entrancy guard and a clean slate for a synchronous batch: Run is
        disabled for its duration, and the previous run's numbers are wiped so they
        cannot be mistaken for this batch's."""
        self._run_in_progress = True
        self._refresh_commands()
        self._disable_run_panel_for_run()
        self._results.clear()
        # The live-frame fingerprint belongs to the previous run (a
        # stopped run left its last state in it).
        self._live_sig = None
        # A new run invalidates the old Weak Link labels, including on a
        # Start/End node where they would collide with the live capacity
        # readout.
        self._diagram_dock.view.clear_result_overlays()
        self._reset_status_summary()
        self._start_run_eta()
        # ...and the stepping Live Log dock.
        self._reset_live_log()
        # Sync runs pump the event loop between runs, so a Stop press
        # can land: flag checked in the progress hook, engine aborts cleanly.
        self._sync_stop = False

    def _unlock_after_run(self) -> None:
        self._run_in_progress = False
        self._refresh_commands()
        # Without this the run settings stay locked after a run or an error.
        self._reenable_run_panel()
        self._run_progress.setVisible(False)
        self._run_progress_label.setVisible(False)

    def _abandon_foreground_run(self, streams) -> None:
        """A failed run is over too: restore the window and drop its report streams."""
        QApplication.restoreOverrideCursor()
        self._unlock_after_run()
        self._clear_run_eta()
        self._hide_phase_labels()
        self._discard_report_streams(streams)

    def _present_foreground_run(self, run_model, event_logs, end_of_run_lines,
                                final_results_lines, *, streams, report_data,
                                report_notes, run_metadata) -> None:
        """Put a finished (or stopped) batch's results on screen and release the UI."""
        ao_lines = streams.get("ao")
        cap_lines = streams.get("capacity")
        cost_lines = streams.get("cost")
        # Importance variants are still to come. Keep the run "in progress"
        # for the whole of them: Run must not start a second batch over the
        # baseline on screen, Stop must still land (the worker reads
        # `_sync_stop`), and the run-settings lock must stay on. Ordinary
        # runs unlock here exactly as before.
        importance_mode = str(
            (self._current_experiment() or {}).get("importance", "none")
            or "none")
        will_run_importance = (importance_mode not in ("", "none")
                               and bool(report_data))
        try:
            if not will_run_importance:
                self._unlock_after_run()
            else:
                # The bar belongs to the importance phase from now on.
                self._run_progress.setVisible(True)
                self._run_progress_label.setVisible(True)
            self._clear_run_eta()
            # Raptor 7 clears its cycle/phase panels at batch end; same
            # here. Clear the live fills/Linked/Cut badges too: the
            # synchronous (Animate) path used to leave them on the canvas, so
            # a finished run still showed white Linked squares.
            self._hide_phase_labels()
            self._diagram_dock.view.clear_live_states()
            # Status bar says "Processing results" between the run(s)
            # finishing and the results actually being parsed/rendered;
            # without this, the bar would show a stale "Running — run N of
            # N" message for however long set_results/_present_results took
            # on a large model, looking frozen.
            self._statusbar.showMessage("Processing results…")
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            units = _system_units(run_model)
            self._push_weak_settings()
            # FailSim/CycleSim anti-runaway bound hit: surface it loudly
            # instead of silently reporting numbers from a truncated
            # simulation.
            self._notify_negative_times(
                run_metadata.get("negative_draws", []), report_notes)
            if run_metadata.get("hit_safety_bound"):
                report_notes.append(
                    "Note: the run reached the simulation safety bound "
                    "(t = 10,000,000) WITHOUT meeting its termination "
                    "condition — results cover only the simulated portion. "
                    "Check the model's termination condition; see Help ▸ "
                    "Report Issue if this model should have ended earlier.")
                self._statusbar.showMessage(
                    "WARNING: run stopped at the t=10,000,000 safety bound — "
                    "termination condition never met", 12000)
            self._results._source_file_name = self._current_file or ""
            event_logs = self._dev_filtered_event_logs(event_logs)
            eor_source = streams.get("end_of_run")
            if eor_source is None:
                eor_source = end_of_run_lines
            baseline_presented = False
            importance = None
            # Everything from the importance bookkeeping call through the
            # final population is inside this try: a failure in any part of
            # the epilogue must still hit the `finally` that unlocks Run and
            # clears the stop/importance state.
            if will_run_importance and not getattr(self, "_sync_stop", False):
                from raptor.api import importance as _imp
                importance_total = _imp.batch_count(
                    len(_imp.block_rows(run_model)), importance_mode)
                baseline_presented = self._present_baseline_results(
                    logs=event_logs, eor=eor_source,
                    fin=final_results_lines, report_data=report_data,
                    notes=report_notes, ao_lines=ao_lines,
                    cap_lines=cap_lines, cost_lines=cost_lines,
                    units=units, manifest=run_metadata.get("manifest"),
                    importance_total=importance_total)
                importance = self._importance_for(run_model, report_data)
            overlays = (self._checked_overlay_state()
                        if baseline_presented else None)
            if baseline_presented and importance is not None:
                # Same run, same baseline numbers: only the Importance tab
                # is new. Rebuild that one page instead of parsing every
                # result table a second time.
                self._results.set_importance(importance)
                self._importance_overlay_action.setEnabled(True)
                self._note_results_basis()
                self._remember_report_streams(streams)
                self._results_show_action.setEnabled(bool(report_data))
                self._results_float_action.setEnabled(bool(report_data))
                self._apply_hier_status(report_data)
            else:
                self._set_results_with_progress(
                    event_logs, eor_source, final_results_lines,
                    report_data=report_data, units=units,
                    notes=report_notes, ao_lines=ao_lines,
                    cap_lines=cap_lines, cost_lines=cost_lines,
                    manifest=run_metadata.get("manifest"),
                    importance=importance)
                self._note_results_basis()
                self._remember_report_streams(streams)
                self._results_show_action.setEnabled(bool(report_data))
                self._results_float_action.setEnabled(bool(report_data))
                self._sync_overlay_actions(report_data)
                self._reapply_checked_overlays(overlays)
                # Do NOT auto-enable a canvas overlay here. A run should
                # not silently repaint the model (and must never override
                # the overlay the user explicitly chose). The Results
                # ribbon offers Weak Links/Capacity/Cost/Spares once their
                # data exists.
                self._apply_hier_status(report_data)
                # Present on the baseline population. Re-presenting after
                # the importance refresh would yank the user out of
                # whatever tab they had moved to while waiting.
                if not baseline_presented:
                    self._present_results()
            self._note_run_profile(len(report_data))
            self._update_status_summary(report_data, units)
            from raptor.core import diagnostics as _dg
            self._diag(_dg.end_simulation_line())
            self._show_run_outcome(event_logs, len(report_data))
        finally:
            # The run is over whether or not presenting its results raised:
            # leaving Run disabled (or `_sync_stop` latched) is the stranding
            # bug this guard exists for.
            if will_run_importance:
                self._run_in_progress = False
                self._refresh_commands()
                self._reenable_run_panel()
            self._imp_eta = None
            self._imp_last_key = None
            self._importance_running = False
            self._pause_action.setText("Pause")
            self._sync_stop = False

    def _show_run_outcome(self, event_logs, completed: int) -> None:
        """Status line for how the batch ended: stopped (and where) or completed."""
        text, timeout = run_outcome_text(
            stopped=bool(getattr(self, "_sync_stop", False)), completed=completed,
            logs=len(event_logs),
            event_log_lines=sum(len(lines) for lines in event_logs))
        self._statusbar.showMessage(text, timeout)

    def _current_units(self):
        return _system_units(req(self._rbd))

    def _diag(self, *lines: str) -> None:
        """Append to the `/d` diagnostics file beside the model when `diagnostics_log` is on."""
        if not self._dev_options().diagnostics_log or not self._current_file:
            return
        from raptor.core import diagnostics
        folder = Path(self._current_file).parent
        if getattr(self, "_diag_name", None) is None:
            now = time.ctime()
            self._diag_name = diagnostics.diagnostics_file_name(now)
            first = diagnostics.startup_lines(now, install_path=str(folder),
                                              file_to_open=self._current_file)
        else:
            first = []
        try:
            with open(folder / self._diag_name, "ab") as f:
                for ln in [*first, *lines]:
                    f.write(ln.encode("latin-1", "replace") + b"\r\n")
        except OSError as exc:
            self._statusbar.showMessage(f"Diagnostics file could not be written: {exc}", 8000)

    def _write_dev_trees(self) -> None:
        """`reports/DomainTree.TXT` and `reports/CoreFile.TXT` beside the model when the
        `domain_tree` / `core_file` options are on (Raptor 7 writes them at run start,
        relative to its working directory)."""
        opts = self._dev_options()
        if not (opts.domain_tree or opts.core_file) or not self._current_file \
                or self._rbd is None:
            return
        from raptor.core.engine.domain_tree import domain_tree_lines
        folder = Path(self._current_file).parent / "reports"
        today = time.ctime()
        name = Path(self._current_file).name
        try:
            folder.mkdir(exist_ok=True)
            for want, file_name, core in ((opts.domain_tree, "DomainTree.TXT", False),
                                      (opts.core_file, "CoreFile.TXT", True)):
                if want:
                    text = "\r\n".join(domain_tree_lines(self._rbd, name, today, core=core))
                    (folder / file_name).write_bytes((text + "\r\n").encode("latin-1", "replace"))
        except OSError as exc:
            self._statusbar.showMessage(f"Domain tree file could not be written: {exc}", 8000)

    def _ask_mission_path(self) -> str | None:
        """The `mission_file` option's Save-As prompt (the original asks at run start; Cancel
        means no mission file for this run)."""
        if not self._dev_options().mission_file:
            return None
        start = (str(Path(self._current_file).with_name(Path(self._current_file).stem
                                                       + "_mission.txt"))
                 if self._current_file else "mission.txt")
        path, _ = fdg.getSaveFileName(self, "Mission File", start, "Text files (*.txt)")
        return path or None

    def _on_y_axis_min_changed(self, value: float) -> None:
        """The Ao graphs' Y-axis minimum was set: it is the model's `graphYmin` (saved, so the
        model is changed -- as in Raptor 7, whose Set Y-Axis edits the same variable)."""
        if self._rbd is None:
            return
        self._rbd.section("SYSTEM_DATA").rows[0]["graphYmin"] = f"{value:.6f}"
        self._mark_dirty()

    def _notify_negative_times(self, per_run, report_notes: list[str]) -> None:
        """After a batch that drew negative times (clamped to 0.000001): a note in the results and,
        unless the model ignores the warning, Raptor 7's dialog with its "Don't show this message
        again" box -- ticking it sets the model's `ShowNegErrMsg` (and marks it changed)."""
        from raptor.core.engine import negative_draws as neg
        text = neg.note(per_run)
        if text is None or self._rbd is None:
            return
        if neg.ignoring(self._rbd):
            return
        report_notes.append("Note: " + text)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(neg.DIALOG_TITLE)
        box.setText(neg.DIALOG_BODY)
        from PySide6.QtWidgets import QCheckBox
        again = QCheckBox(neg.DIALOG_DONT_SHOW)
        box.setCheckBox(again)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if again.isChecked():
            self._rbd.section("SYSTEM_DATA").rows[0]["ShowNegErrMsg"] = "1"
            self._mark_dirty()

    def _ask_cumulative_trial_path(self) -> str | None:
        """The `cumulative_by_trial` option's Save-As prompt ("Cumulative By Trial File"; Cancel
        means none for this run)."""
        if self._dev_options().cumulative_by_trial <= 0:
            return None
        start = (str(Path(self._current_file).with_name(Path(self._current_file).stem
                                                       + "_cumulative_by_trial.txt"))
                 if self._current_file else "cumulative_by_trial.txt")
        path, _ = fdg.getSaveFileName(self, "Cumulative By Trial File", start,
                                      "Text files (*.txt)")
        return path or None

    def _ask_cumulative_time_path(self) -> str | None:
        """The `cumulative_by_time` option's Save-As prompt ("Cumulative By Time File")."""
        if self._dev_options().cumulative_by_time <= 0:
            return None
        start = (str(Path(self._current_file).with_name(Path(self._current_file).stem
                                                       + "_cumulative_by_time.txt"))
                 if self._current_file else "cumulative_by_time.txt")
        path, _ = fdg.getSaveFileName(self, "Cumulative By Time File", start,
                                      "Text files (*.txt)")
        return path or None

    def _sim_details_path(self) -> str | None:
        """`simDetails.TXT` beside the model when the `sim_details` option is on. The file
        grows across runs and launches, as Raptor 7's does."""
        if not self._dev_options().sim_details or not self._current_file:
            return None
        return str(Path(self._current_file).parent / "simDetails.TXT")

    @staticmethod
    def _dev_options():
        from raptor.gui import dev_settings
        return dev_settings.current_options()

    @classmethod
    def _dev_filtered_event_logs(cls, event_logs):
        """The event logs through the Developer tab's category filter and one-component
        detail (Raptor 7's Avant-garde boxes); unchanged when neither is set."""
        from raptor.core.engine.eventlog import filter_event_log
        opts = cls._dev_options()
        if not (opts.event_log_filter or opts.event_log_component):
            return event_logs
        cats = opts.event_log_filter
        return [filter_event_log(
            run, blocks="block" in cats, nodes="node" in cats, system="system" in cats,
            phases="phase" in cats, component=opts.event_log_component)
            for run in event_logs]

    def _present_run_results(self, event_logs, eor_lines, final_lines,
                             report_data, ao_lines: list[str] | None = None,
                             cap_lines: list[str] | None = None,
                             manifest=None):
        units = self._current_units()
        self._push_weak_settings()
        self._results._source_file_name = self._current_file or ""
        iso = (manifest or {}).get("isolation") or {}
        if iso.get("banner"):
            # The End-of-Run text is what leaves the app with the report;
            # label it before the engine's own header.
            eor_lines = [iso["banner"], ""] + list(eor_lines)
        event_logs = self._dev_filtered_event_logs(event_logs)
        self._results.set_results(event_logs, eor_lines, final_lines,
                                  report_data=report_data, units=units,
                                  ao_lines=ao_lines, cap_lines=cap_lines,
                                  manifest=manifest)
        self._note_results_basis()
        self._results_show_action.setEnabled(bool(report_data))
        self._results_float_action.setEnabled(bool(report_data))
        self._apply_hier_status(report_data)
        self._present_results()
        self._note_run_profile(len(report_data))
        self._update_status_summary(report_data, units)
        total_lines = sum(len(lines) for lines in event_logs)
        self._statusbar.showMessage(
            f"Completed {len(event_logs)} run(s) successfully, "
            f"{total_lines} event-log lines")

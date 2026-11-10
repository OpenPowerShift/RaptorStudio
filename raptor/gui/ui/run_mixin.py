"""Starting a run: scope, model preparation, the foreground simulation and its results.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations

from raptor.gui.ui.window_support import _live_state_signature

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




#: Auto Layout's tooltip when it arranges the whole level (no selection,
#: or everything selected); the selection variant is built per count.

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class RunMixin(_Base):
    """Starting a run: scope, model preparation, the foreground simulation and its results."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _diag_name: Any
        _edit_run_abandoned: Any
        _imp_eta: Any
        _imp_last_key: Any
        _importance_running: Any
        _last_run_model: Any
        _live_sig: Any
        _rbd: Any
        _results: Any
        _run_full_speed: Any
        _run_in_progress: Any
        _run_scope_return_level: Any
        _sync_stop: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

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

    def _confirm_long_batch(self, num_runs: int) -> bool:
        """Ask before a batch that is likely to run for tens of minutes.

        The default `ProjectX`-class model is 10 trials x 175,200 h; at four
        cores that is roughly an hour, and the old UI only said "Ready".
        This is deliberately a coarse run-hours guard, not a pilot: a pilot
        on such a model costs as much as the short run it would protect.
        A previous run's measured wall time, when one is known, is shown as
        a scale reference.
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
        run_hours = horizon * max(1, int(num_runs)) * batches
        if run_hours < 50_000:
            return True
        previous = getattr(self, "_last_run_profile", None)
        reference = ""
        if previous:
            try:
                ph, pn, pwall, pworkers, pbatches = previous
            except ValueError:
                ph, pn, pwall, pworkers = previous
                pbatches = 1
            if ph and pn and pwall:
                scale = ((horizon / ph) * (num_runs / pn)
                         * (batches / max(1, pbatches))
                         * (max(1, pworkers) / max(1, cores.chosen())))
                reference = (
                    f"\n\nA previous {pn} × {ph:g} h run took "
                    f"~{pwall:.0f} s; at this size expect roughly "
                    f"{pwall * scale / 60:.0f} min (order of magnitude).")
        extra = (f"\n\nImportance measures are on: {importance_mode}, "
                 f"{importance_batches} extra batch(es), {batches} total. "
                 f"The time until completion includes them."
                 if importance_batches else "")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Long simulation")
        box.setText(
            f"This run is {num_runs} trial(s) × {horizon:,.0f} hours "
            f"({run_hours:,.0f} run-hours including every batch)."
            f"{extra}{reference}\n\n"
            "For an interactive check, lower Run length or Trials. "
            "Run the full operation anyway?")
        run_btn = box.addButton("Run anyway", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is run_btn

    def _run_simulation(self):
        # Synchronous/blocking: the engine runs on the GUI thread, and
        # the progress/event callbacks below pump the event loop so the
        # window stays responsive and Stop can land mid-run.
        if getattr(self, "_results_populating", False):
            # The previous batch's results are still being built and the
            # population stages pump the event loop (paint feedback).
            # Starting a run now would clear the dock under its own
            # half-built tables. The click is queued, not lost.
            self._statusbar.showMessage(
                "Still rendering the previous results — try again in a "
                "moment.", 5000)
            return
        if self._step_finalize_state is not None:
            self._statusbar.showMessage(
                "Finalizing the stepped run — wait for its results", 5000)
            return
        if self._rbd is None:
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
        # Warn before the run about features the engine will reject
        # mid-run with NotImplementedError.
        preflight = self._preflight_issues(run_model)
        if preflight:
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
        units = None
        try:
            cfg_section = self._rbd.sections.get("SYSTEM_DATA")
            if cfg_section and cfg_section.rows:
                units = system_config_from_row(cfg_section.rows[0]).units
        except (ValueError, KeyError):
            pass
        # Keep the UI responsive during the synchronous batch: the
        # engine's progress hook fires after every completed run, and the
        # loop is pumped there; without it, long runs would freeze the
        # window into "Not Responding". Re-entrancy guarded by disabling
        # Run for the duration.
        self._run_in_progress = True
        self._refresh_commands()
        self._disable_run_panel_for_run()
        # Fresh run: wipe stale results so old numbers can't be mistaken
        # for the new batch.
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
        # can land: flag checked in _progress, engine aborts cleanly.
        self._sync_stop = False
        runs_done = [0]
        live_logs: list[list[str]] = []
        status_updates = True   # readouts update per-run, always
        live_view = self._live_sim_action.isChecked()

        # Live intra-run view (Raptor 7's Simulation View behaviour):
        # every processed event re-colours the blocks by their current
        # state (manual Appendix D colours). Throttled to ~30 UI
        # updates/second so huge models don't drown in repaints.
        # Note: the status bar does not show within-run values -- Raptor 7
        #reads cross-run monitors there, refreshed as each run
        # finishes, so the averages update per run via _progress below,
        # not per event.
        import time as _time
        last_paint = [_time.monotonic()]

        # Long single runs (or a big model generating lots of events)
        # would freeze the window for the run's entire duration if only
        # `_progress` (fired between whole runs) pumped the event loop,
        # leaving paint/input/OS keepalive pings blocked while a run was
        # actually in progress. Both event callbacks below pump the loop
        # themselves, throttled so the extra per-event cost stays
        # negligible next to the engine work; this also lets a Stop press
        # land mid-run instead of only between runs.
        # There is one status-bar text format, always both the run number
        # and the current time: while Live Sim is on, _on_event is the
        # sole writer (it is far more frequent and has the live time
        # _progress never does) -- _progress only writes text when Live
        # Sim is off, the one case _on_event never fires.
        def _on_event(scheduler, system_state, blocks, cap_data=None,
                      model=None):
            now = _time.monotonic()
            if now - last_paint[0] < 1 / 30:
                return
            last_paint[0] = now
            # System-status square in the status bar: the live
            # SystemColor straight from the running cascade.
            self._update_system_swatch(getattr(system_state, "color", None))
            # Raptor 7's status panels 2/3/4: cycles completed, the
            # current phase name, and MISSION/Nonmission (phased models).
            self._update_phase_labels(system_state)
            # cap_data (when capacity analysis is enabled): the engine's
            # live per-node flow dicts, painted under the nodes Raptor 7
            # `ShowFlow` style. model (engine's on_event 5th arg): the
            # live engine model -- node/link/event/hierarchy colours are
            # painted from it (the canvas's own objects are a stale
            # separate parse).
            #
            # Repaint only when something painted actually changed
            # (`_live_state_signature`). The kickoff burst at t=0 is
            # thousands of scheduler steps with an identical picture;
            # repainting each one looked frozen and dominated the run.
            sig = _live_state_signature(blocks, cap_data, model, system_state)
            if sig != getattr(self, "_live_sig", None):
                self._live_sig = sig
                self._diagram_dock.view.update_live_states(
                    blocks, cap_data=cap_data, model=model,
                    phase=getattr(system_state, "current_phase", None))
            if status_updates:
                self._statusbar.showMessage(self._run_status_text(
                    f"Running ({self._threads_label(1)}) — run "
                    f"{runs_done[0] + 1} of {num_runs}, "
                    f"t = {scheduler.time:.3f}",
                    runs_done[0], num_runs))
            QApplication.processEvents()
            if getattr(self, "_sync_stop", False):
                raise RunAborted()

        def _on_event_keepalive(scheduler, system_state, blocks,
                                cap_data=None, model=None):
            # Animate off: the run is at full speed, so skip the
            # repaint/status-text work _on_event does, but still pump the
            # loop occasionally so a long run at max speed doesn't go
            # fully unresponsive either.
            now = _time.monotonic()
            if now - last_paint[0] < 0.5:
                return
            last_paint[0] = now
            QApplication.processEvents()
            if getattr(self, "_sync_stop", False):
                raise RunAborted()

        def _throttle() -> None:
            """Animation pacing for the SYNCHRONOUS path.

            The speed dial used to be read only inside the background
            worker, so in the default (foreground) run mode all four
            buttons did nothing but move a label. This applies the same
            per-event delay here, via `_effective_event_delay` (which
            returns 0 whenever Animate is off, matching Raptor 7's
            "no graphics = full speed" rule)."""
            d = self._effective_event_delay()
            if d:
                _time.sleep(d)

        runaway_asked = [False]

        def _progress(completed: int, total: int):
            runs_done[0] = completed
            if run_metadata.get("runaway_runs") and not runaway_asked[0]:
                runaway_asked[0] = True
                if completed < total and not self._ask_keep_going_after_runaway(completed, total):
                    from raptor.core.engine.errors import RunAborted
                    raise RunAborted()
            if getattr(self, "_sync_stop", False):
                # mimic the background path's abort: raise through the
                # engine so it stops between draws with results kept
                from raptor.core.engine.errors import RunAborted
                raise RunAborted()
            pct = 100 * completed // max(total, 1)
            if status_updates and not live_view:
                self._statusbar.showMessage(self._run_status_text(
                    f"Running ({self._threads_label(1)}) — run "
                    f"{completed} of {total}", completed, total))
            self._run_progress.setValue(pct)
            self._run_progress.setVisible(True)
            self._run_progress_label.setText(f"{pct}%")
            self._run_progress_label.setVisible(True)
            # Cross-run averages (AvailMon.Mean semantics): refresh the
            # permanent readouts from every completed run's data,
            # independent of any toggle.
            self._update_status_summary(report_data, units)
            if live_view and live_logs:
                # Stream the completed runs' logs into the Event Log tab
                # so the user watches the simulation arrive run-by-run.
                if not self._results_dock.isVisible():
                    self._results_dock.show()
                self._results.show_live_logs(live_logs, total)
            if cap_lines:
                # Capacity analysis enabled: stream the capacity
                # snapshots completed so far into the Graphs tab's
                # Capacity plot, so the capacity is visible during the
                # run.
                self._results.update_live_capacity(cap_lines)
                if not self._results_dock.isVisible():
                    self._results_dock.show()
            if ao_lines:
                # Live Ao plot (Raptor 7 displays it during a run).
                self._results.update_live_ao(ao_lines)
                if not self._results_dock.isVisible():
                    self._results_dock.show()
            if status_updates or live_view:
                QApplication.processEvents()

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
        cost_lines = streams.get("cost")
        run_metadata: dict = {}
        # Freeze the manifest BEFORE the engine is handed anything, so it
        # describes the run that actually happened even if the model is
        # edited while the engine works.
        run_metadata["manifest"] = self._build_run_manifest(
            run_model, isolation)
        # Wrap the per-event hook so the animation delay is applied on
        # EVERY event, not just the ones that pass the 30 Hz paint
        # throttle inside `_on_event`. `_effective_event_delay` returns
        # 0.0 while Animate is off, so a non-animated run pays nothing.
        _raw_on_event = _on_event if live_view else _on_event_keepalive

        def _on_event_throttled(*a, **kw):
            _throttle()
            return _raw_on_event(*a, **kw)
        try:
            event_logs, end_of_run_lines, final_results_lines = run_simulation_with_reports(
                run_model, num_runs=num_runs, report_sink=report_data,
                progress=_progress, notes_sink=report_notes,
                on_event=_on_event_throttled,
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
            # Stop pressed (between runs via _progress, or now mid-run
            # via _on_event/_on_event_keepalive): keep completed results
            # and fall through to the normal results path (flag already
            # set).
            pass
        except NotImplementedError as e:
            QApplication.restoreOverrideCursor()
            self._run_in_progress = False
            self._refresh_commands()
            # A failed run is over too: without this the run settings
            # stayed locked after an error.
            self._reenable_run_panel()
            self._run_progress.setVisible(False)
            self._run_progress_label.setVisible(False)
            self._clear_run_eta()
            self._hide_phase_labels()
            self._discard_report_streams(streams)
            msgbox.warning(
                self, "Unsupported Model Feature",
                f"This model uses a feature the simulation engine doesn't support yet:\n\n{e}",
            )
            self._statusbar.showMessage("Run failed — unsupported feature")
            return
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self._run_in_progress = False
            self._refresh_commands()
            # A failed run is over too: without this the run settings
            # stayed locked after an error.
            self._reenable_run_panel()
            self._run_progress.setVisible(False)
            self._run_progress_label.setVisible(False)
            self._clear_run_eta()
            self._hide_phase_labels()
            self._discard_report_streams(streams)
            msgbox.critical(self, "Simulation Failed", f"The run failed:\n\n{e}")
            self._statusbar.showMessage("Run failed")
            return
        QApplication.restoreOverrideCursor()
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
                self._run_in_progress = False
                self._refresh_commands()
                self._reenable_run_panel()
                self._run_progress.setVisible(False)
                self._run_progress_label.setVisible(False)
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
            units = None
            try:
                cfg_section = run_model.sections.get("SYSTEM_DATA")
                if cfg_section and cfg_section.rows:
                    units = system_config_from_row(cfg_section.rows[0]).units
            except (ValueError, KeyError):
                pass
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
            total_lines = sum(len(lines) for lines in event_logs)
            if getattr(self, "_sync_stop", False):
                completed = len(report_data)
                if completed < len(event_logs):
                    self._statusbar.showMessage(
                        "Stopped — partial event log kept for the interrupted "
                        f"run, {total_lines} event-log lines", 10000)
                else:
                    self._statusbar.showMessage(
                        f"Stopped after {completed} run(s), "
                        f"{total_lines} event-log lines")
            else:
                # State success explicitly so a finished run is never
                # mistaken for an aborted one.
                self._statusbar.showMessage(
                    f"Completed {len(event_logs)} run(s) successfully, "
                    f"{total_lines} event-log lines")
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

    def _current_units(self):
        try:
            cfg_section = req(self._rbd).sections.get("SYSTEM_DATA")
            if cfg_section and cfg_section.rows:
                return system_config_from_row(cfg_section.rows[0]).units
        except (ValueError, KeyError):
            pass
        return None

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

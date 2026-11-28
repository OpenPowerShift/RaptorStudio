"""Restart, Terminate and Finish of a stepped run; Terminate and Finish run off the GUI thread.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations


from raptor.gui.ui import msgbox
import threading
import time
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from PySide6.QtWidgets import QApplication

from raptor.gui.ui.window_support import (
    _resume_cyclic_gc, _suppress_cyclic_gc)
from raptor.core.engine.reports import (
    build_end_of_run_report,
    build_final_results_report,
)




if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtCore
    import PySide6.QtWidgets
    import raptor.gui.ui.diagram
    import raptor.gui.ui.results
else:
    _Base = object


class StepTerminateMixin(_Base):
    """Restart, Terminate and Finish of a stepped run; Terminate and Finish run off the GUI thread."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _diagram_dock: raptor.gui.ui.diagram.DiagramDock
        def _build_run_manifest(self, rbd: Any = ..., isolation: Any = ..., seed: int = ...) -> dict: ...
        def _clear_failed_step(self) -> None: ...
        def _clear_run_eta(self) -> None: ...
        _last_run_model: Any  # really raptor.core.fileio.rbd.RbdFile | None
        _results: raptor.gui.ui.results.ResultsDock
        _run_progress: PySide6.QtWidgets.QProgressBar
        _run_progress_label: PySide6.QtWidgets.QLabel
        def _run_status_text(self, prefix: str, done: Any, total: Any) -> str: ...
        def _start_run_eta(self) -> None: ...
        _step_isolation: Any
        def _end_step_session(self, *, clear_sink: bool = ...) -> bool: ...
        def _ensure_step_session(self) -> Any: ...
        def _invalidate_step_session(self) -> Any: ...
        def _on_fev_log_close(self) -> None: ...
        def _present_run_results(self, event_logs: Any, eor_lines: Any, final_lines: Any, report_data: Any, ao_lines: list[str] | None = ..., cap_lines: list[str] | None = ..., manifest: Any = ...) -> Any: ...
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _refresh_commands(self, state: Any = ..., only: set[str] | None = ...) -> None: ...
        def _refresh_live_step_view(self, session: Any) -> Any: ...
        def _reset_live_log(self) -> Any: ...
        _run_in_progress: bool
        _statusbar: PySide6.QtWidgets.QStatusBar
        _step_ao: list[str]
        _step_finalize_state: Any
        _step_finalize_thread: Any
        _step_finalize_timer: PySide6.QtCore.QTimer
        _step_session: Any
        _step_sink: list
        def _sync_run_settings_lock(self) -> None: ...

    def _on_step_restart(self):
        """B-11 Restart: tear down the current stepped session and start
        an identical fresh one (kickoff resets everything; RNG streams
        reseed from the table, matching a brand-new session)."""
        if self._rbd is None or self._step_finalize_state is not None:
            return
        self._invalidate_step_session()
        if self._ensure_step_session() is None:
            return
        self._refresh_live_step_view(self._step_session)
        self._statusbar.showMessage(
            "Stepped run restarted — t = 0", 6000)

    def _step_finalize_label(self) -> str:
        """"Finish" or "Early-terminate": what the running finalize is, for its messages."""
        state = self._step_finalize_state or {}
        return "Finish" if state.get("kind") == "finish" else "Early-terminate"

    def _discard_step_finalize(self, message: str | None = None) -> bool:
        """Mark a running Terminate or Finish's results as discarded.

        A Finish is also asked to stop (its worker checks after every
        event); a Terminate cannot be cancelled. Either way its state,
        timer and thread stay alive until the worker reports done, when
        `_step_finalize_poll_tick` clears them and unlocks the window.
        Keeping them until then means a new finalize/background worker
        cannot overlap the old one."""
        state = self._step_finalize_state
        if state is None:
            return False
        state["discard"] = True
        stop = state.get("stop_ev")
        if stop is not None:
            stop.set()
        self._diagram_dock.view.clear_live_states()
        if message:
            state["discard_message"] = message
            self._statusbar.showMessage(message, 6000)
        if state.get("done") and not self._step_finalize_timer.isActive():
            # The worker finished between the last poll and this call.
            self._step_finalize_timer.start()
        return True

    def _stop_step_finalize(self) -> bool:
        """Stop pressed while a stepped run finalizes. A Finish stops after
        the event in hand (True); a Terminate cannot be cancelled (False)."""
        state = self._step_finalize_state
        stop = (state or {}).get("stop_ev")
        if stop is None:
            return False
        if not stop.is_set():
            stop.set()
            self._statusbar.showMessage(
                "Stop requested — ending Finish after the current event…", 6000)
        return True

    def _wait_step_finalize(self, timeout: float = 15.0) -> bool:
        """Pump events until a running Terminate/Finish worker is done and
        its result handled (or `timeout` seconds pass). True when no
        finalize is left."""
        thread = self._step_finalize_thread
        deadline = time.monotonic() + timeout
        while thread is not None and thread.is_alive() \
                and time.monotonic() < deadline:
            QApplication.processEvents()
            thread.join(0.02)
        state = self._step_finalize_state
        if state is not None and state.get("done"):
            self._step_finalize_poll_tick()
        return self._step_finalize_state is None

    def _launch_step_finalize(self, state: dict, job: Callable[[], Any]) -> None:
        """Run `job` on a worker thread and poll `state` for its result.

        `job` returns the result tuple the poll presents; an exception is
        kept as `state["error"]` (message and traceback). The worker only
        writes plain data into `state`; every widget is touched by
        `_step_finalize_poll_tick`, on the GUI thread."""
        def worker():
            # Same cyclic-GC rule as the background run: never let a pass
            # walk Qt wrappers owned by the GUI thread. Reference-counted
            # so an overlapping background worker cannot have GC re-enabled
            # by this worker's exit (or vice versa).
            _suppress_cyclic_gc()
            try:
                state["result"] = job()
            except Exception as e:
                import traceback
                traceback.print_exc()
                state["error"] = "".join(
                    traceback.format_exception_only(type(e), e)).strip() \
                    + "\n" + traceback.format_exc(limit=8)
            finally:
                _resume_cyclic_gc()
                state["done"] = True

        try:
            thread = threading.Thread(target=worker, daemon=True,
                                      name=f"raptor-step-{state['kind']}")
            self._step_finalize_thread = thread
            thread.start()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._step_finalize_thread = None
            state["error"] = "".join(
                traceback.format_exception_only(type(e), e)).strip() \
                + "\n" + traceback.format_exc(limit=8)
            state["done"] = True
        self._step_finalize_timer.start()

    def _begin_step_finalize(self, kind: str, session) -> dict:
        """The shared start of Terminate and Finish: the worker owns
        `session` from here, the GUI-side session ends (keeping the report
        sink the worker fills) and the window locks as for a run."""
        self._on_fev_log_close()
        state = {
            "kind": kind,
            "done": False,
            "error": None,
            "result": None,
            "session": session,
            "t": session.time,
            "rbd": self._rbd,
            "sink": self._step_sink,
            "ao_sink": self._step_ao,
            "discard": False,
            "discard_message": None,
        }
        self._step_finalize_state = state
        self._end_step_session(clear_sink=False)
        self._run_in_progress = True
        # `_end_step_session` unlocked the panel while the flag was still
        # false; lock it again now the finalize owns the run.
        self._sync_run_settings_lock()
        self._refresh_commands()
        return state

    def _on_step_terminate(self):
        """Manual B-11 Early Terminate on the stepped session.

        The expensive part (finalize passes, report tables, results
        construction) runs on a worker thread; the GUI only presents the
        finished result on its own thread. See
        `_step_finalize_poll_tick`. No hierarchy level is entered or left.
        """
        if self._step_session is None or self._step_finalize_state is not None:
            return
        session = self._step_session
        self._reset_live_log()
        state = self._begin_step_finalize("terminate", session)
        t = state["t"]
        self._statusbar.showMessage(
            f"Finalizing early terminate at t = {t:.3f} — results appear "
            "when ready")

        def job():
            logs, report = session.early_terminate()
            report_data = list(state["sink"] or [])
            eor_lines: list[str] = []
            final_lines: list[str] = []
            if report is not None:
                model = session.model
                date = time.ctime()
                file_name = session.rbd_file.file_name
                eor_lines = [
                    f"End_Of_Run Report for {file_name}  {date}",
                    "(Early Terminate)", ""]
                eor_lines.extend(build_end_of_run_report(
                    report, model.blocks_by_id))
                final_lines = build_final_results_report(
                    [report], model.blocks_by_id, file_name,
                    model.run_length, date,
                    termination_type=model.termination_type)
            return ([logs], eor_lines, final_lines, report_data,
                    list(state["ao_sink"] or []), session.cap_lines)

        self._launch_step_finalize(state, job)

    def _on_step_finish(self):
        """Complete the stepped run (remaining events + closing passes) and
        every later run, then present results exactly like a normal batch.

        The runs go by on a worker thread, as a background batch does, with
        its progress bar, estimate and Stop (#386): on the GUI thread the
        window could not repaint until the last run ended. Stop abandons the
        run in hand, as a batch Stop does; the finished runs are presented."""
        if self._step_session is None or self._step_finalize_state is not None:
            return
        session = self._step_session
        # Read on the GUI thread before the worker owns the session. The
        # manifest says how many runs the numbers on screen come from.
        manifest = self._build_run_manifest(
            self._last_run_model if getattr(self, "_step_isolation", None)
            else None, getattr(self, "_step_isolation", None))
        first_run = session.run_index
        state = self._begin_step_finalize("finish", session)
        stop_ev = threading.Event()
        state.update({"stop_ev": stop_ev, "manifest": manifest,
                      "progress": (first_run, session.num_runs),
                      "first_run": first_run, "stopped": False})
        self._start_run_eta()
        self._run_progress.setValue(100 * first_run // max(session.num_runs, 1))
        self._run_progress.setVisible(True)
        self._run_progress_label.setText(f"{self._run_progress.value()}%")
        self._run_progress_label.setVisible(True)
        self._show_step_finish_progress(state)

        def progress(done, total):
            state["progress"] = (done, total)

        def should_stop() -> bool:
            # Called after every event, on the worker: plain data only.
            state["t"] = session.time
            return stop_ev.is_set()

        def job():
            event_logs, _ = session.finish_all(progress=progress,
                                               should_stop=should_stop)
            state["stopped"] = session.stopped
            eor_lines: list[str] = []
            final_lines: list[str] = []
            report_data = list(state["sink"] or [])
            if report_data:
                model = session.model
                date = time.ctime()
                file_name = session.rbd_file.file_name
                eor_lines = [f"End_Of_Run Report for {file_name}  {date}", ""]
                for report in report_data:
                    eor_lines.extend(build_end_of_run_report(
                        report, model.blocks_by_id))
                final_lines = build_final_results_report(
                    report_data, model.blocks_by_id, file_name,
                    model.run_length, date,
                    termination_type=model.termination_type,
                )
            return (event_logs, eor_lines, final_lines, report_data,
                    list(state["ao_sink"] or []), session.cap_lines)

        self._launch_step_finalize(state, job)

    def _show_step_finish_progress(self, state) -> None:
        """A running Finish's progress line, bar and estimate (the
        background run's wording)."""
        done, total = state.get("progress") or (0, 0)
        total = max(int(total), 1)
        first = int(state.get("first_run", 0))
        if state.get("stop_ev") is not None and state["stop_ev"].is_set():
            return          # "Stop requested" stays up until it lands
        # Short: the status line shares the bar with the progress bar.
        prefix = (f"Finishing — run {min(done + 1, total)} of {total}, "
                  f"t = {float(state.get('t') or 0.0):.1f}")
        self._statusbar.showMessage(self._run_status_text(
            prefix, done - first, total - first))
        pct = 100 * int(done) // total
        if self._run_progress.value() != pct:
            self._run_progress.setValue(pct)
            self._run_progress_label.setText(f"{pct}%")

    def _step_finalize_poll_tick(self):
        """Present a finished Terminate or Finish on the GUI thread (and a
        running Finish's progress)."""
        state = self._step_finalize_state
        if state is None:
            self._step_finalize_timer.stop()
            return
        finish = state.get("kind") == "finish"
        if not state.get("done"):
            if finish and not state.get("discard"):
                self._show_step_finish_progress(state)
            return
        # Stop before any presentation: `set_results` pumps the event loop
        # and must not re-enter this tick.
        self._step_finalize_timer.stop()
        label = "Finish" if finish else "Early-terminate"
        try:
            # The model/window went away (or was replaced) while the
            # worker ran: drop the result rather than painting it over
            # another model.
            if state.get("discard") or self._rbd is not state.get("rbd"):
                self._diagram_dock.view.clear_live_states()
                if not state.get("discard_message"):
                    self._statusbar.showMessage(
                        f"{label} results discarded", 6000)
                return
            err = state.get("error")
            if err:
                if finish:
                    # A normal run's failure, as the synchronous Finish
                    # reported it.
                    self._clear_failed_step()
                    msgbox.critical(self, "Simulation Failed",
                                    f"The run failed:\n\n{err}")
                    self._statusbar.showMessage("Run failed")
                else:
                    self._diagram_dock.view.clear_live_states()
                    msgbox.critical(self, "Terminate Failed",
                                    f"Early terminate failed:\n\n{err}")
                    self._statusbar.showMessage("Early terminate failed")
                return
            if state.get("result") is None:
                self._diagram_dock.view.clear_live_states()
                return
            event_logs, eor_lines, final_lines, report_data, ao_lines, \
                cap_lines = state["result"]
            self._diagram_dock.view.clear_live_states()
            if finish:
                self._present_step_finish(state, event_logs, eor_lines,
                                          final_lines, report_data,
                                          ao_lines, cap_lines)
                return
            self._present_run_results(event_logs, eor_lines, final_lines,
                                      report_data,
                                      ao_lines=ao_lines,
                                      cap_lines=cap_lines)
            self._statusbar.showMessage(
                f"Early Terminate at t = {state['t']:.3f} — partial results "
                "kept")
        except Exception as e:
            import traceback
            traceback.print_exc()
            what = ("stepped run finished" if finish
                    else "early-terminated run finished")
            msgbox.critical(self, "Results Failed",
                            f"The {what}, but its results could not be "
                            f"shown:\n\n{e}")
            self._statusbar.showMessage(f"{label} results failed")
        finally:
            # Unlock only after presentation (or failure) has finished.
            self._step_finalize_state = None
            self._step_finalize_thread = None
            self._run_in_progress = False
            if finish:
                # The progress line belongs to the finishing work, not to
                # the idle results screen (#230).
                self._run_progress.setVisible(False)
                self._run_progress_label.setVisible(False)
                self._clear_run_eta()
            try:
                self._refresh_commands()
                self._sync_run_settings_lock()
            except Exception:
                import traceback
                traceback.print_exc()

    def _present_step_finish(self, state, event_logs, eor_lines, final_lines,
                             report_data, ao_lines, cap_lines) -> None:
        """A finished (or stopped) Finish's results, on the GUI thread."""
        stopped = bool(state.get("stopped"))
        total = int((state.get("progress") or (0, 0))[1] or 0)
        if stopped and not report_data:
            self._statusbar.showMessage(
                "Finish stopped before any run completed — no results; "
                "the stepped session has ended", 10000)
            return
        manifest = state.get("manifest")
        if manifest:
            manifest["trials"] = len(report_data) if stopped else len(event_logs)
        if stopped:
            self._results.set_requested_runs(total, "cancelled")
        self._present_run_results(event_logs, eor_lines, final_lines,
                                  report_data, ao_lines=ao_lines,
                                  cap_lines=cap_lines, manifest=manifest)
        if stopped:
            self._statusbar.showMessage(
                f"Stopped after {len(report_data)} of {total} run(s) — "
                "results ready; the stepped session has ended", 10000)

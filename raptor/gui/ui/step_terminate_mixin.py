"""Restart and Terminate of a stepped run, finalising off the GUI thread.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations


from raptor.gui.ui import msgbox
import threading
import time
from typing import TYPE_CHECKING, Any

from raptor.gui.ui.window_support import (
    _resume_cyclic_gc, _suppress_cyclic_gc)
from raptor.core.engine.reports import (
    build_end_of_run_report,
    build_final_results_report,
)




if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class StepTerminateMixin(_Base):
    """Restart and Terminate of a stepped run, finalising off the GUI thread."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _run_in_progress: Any
        _step_finalize_state: Any
        _step_finalize_thread: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

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

    def _discard_step_finalize(self, message: str | None = None) -> bool:
        """Mark a running Terminate finalize's results as discarded.

        The worker cannot be cancelled; its state, timer and thread stay
        alive until it reports done, when `_step_finalize_poll_tick`
        clears them and unlocks the window. Keeping them until then means
        a new finalize/background worker cannot overlap the old one."""
        state = self._step_finalize_state
        if state is None:
            return False
        state["discard"] = True
        self._diagram_dock.view.clear_live_states()
        if message:
            state["discard_message"] = message
            self._statusbar.showMessage(message, 6000)
        if state.get("done") and not self._step_finalize_timer.isActive():
            # The worker finished between the last poll and this call.
            self._step_finalize_timer.start()
        return True

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
        t = session.time
        self._reset_live_log()
        self._on_fev_log_close()
        state = {
            "done": False,
            "error": None,
            "result": None,
            "session": session,
            "t": t,
            "rbd": self._rbd,
            "sink": self._step_sink,
            "ao_sink": self._step_ao,
            "discard": False,
            "discard_message": None,
        }
        self._step_finalize_state = state
        # The worker owns `session` from here; end the GUI-side session
        # (keeps the sink for the report the worker is building) and show
        # the Inspector again.
        self._end_step_session(clear_sink=False)
        self._run_in_progress = True
        # `_end_step_session` unlocked the panel while the flag was still
        # false; lock it again now the finalize owns the run.
        self._sync_run_settings_lock()
        self._refresh_commands()
        self._statusbar.showMessage(
            f"Finalizing early terminate at t = {t:.3f} — results appear "
            "when ready")

        def worker():
            # Same cyclic-GC rule as the background run: never let a pass
            # walk Qt wrappers owned by the GUI thread. Reference-counted
            # so an overlapping background worker cannot have GC re-enabled
            # by this worker's exit (or vice versa).
            _suppress_cyclic_gc()
            try:
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
                state["result"] = (
                    [logs], eor_lines, final_lines, report_data,
                    list(state["ao_sink"] or []), session.cap_lines)
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
            thread = threading.Thread(target=worker, daemon=True)
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

    def _step_finalize_poll_tick(self):
        """Present a finished Terminate finalize on the GUI thread."""
        state = self._step_finalize_state
        if state is None:
            self._step_finalize_timer.stop()
            return
        if not state.get("done"):
            return
        # Stop before any presentation: `set_results` pumps the event loop
        # and must not re-enter this tick.
        self._step_finalize_timer.stop()
        try:
            # The model/window went away (or was replaced) while the
            # worker ran: drop the result rather than painting it over
            # another model.
            if state.get("discard") or self._rbd is not state.get("rbd"):
                self._diagram_dock.view.clear_live_states()
                if not state.get("discard_message"):
                    self._statusbar.showMessage(
                        "Early-terminate results discarded", 6000)
                return
            err = state.get("error")
            if err:
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
            msgbox.critical(self, "Results Failed",
                            "The early-terminated run finished, but its "
                            f"results could not be shown:\n\n{e}")
            self._statusbar.showMessage("Early terminate results failed")
        finally:
            # Unlock only after presentation (or failure) has finished.
            self._step_finalize_state = None
            self._step_finalize_thread = None
            self._run_in_progress = False
            try:
                self._refresh_commands()
                self._sync_run_settings_lock()
            except Exception:
                import traceback
                traceback.print_exc()

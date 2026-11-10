"""The callbacks and bookkeeping of one synchronous (foreground) batch.

The engine runs on the GUI thread, so the hooks below pump the event loop to keep
the window responsive and let Stop land mid-run. `RunMixin._run_simulation` builds one
`ForegroundRun` per batch and hands its methods to the engine.
"""
from __future__ import annotations

import time
from typing import Any

from PySide6.QtWidgets import QApplication

from raptor.core.engine.errors import RunAborted
from raptor.gui.ui.window_support import _live_state_signature


class ForegroundRun:
    """State shared by the engine's event and progress hooks for one batch."""

    #: Live Sim repaints at most this often (seconds); ~30 updates a second so
    #: huge models do not drown in repaints.
    PAINT_INTERVAL = 1 / 30
    #: With Live Sim off the run only needs to keep the loop alive.
    KEEPALIVE_INTERVAL = 0.5

    def __init__(self, window: Any, *, num_runs: int, units: Any, live_view: bool,
                 report_data: list, run_metadata: dict, ao_lines: Any,
                 cap_lines: Any) -> None:
        self.window = window
        self.num_runs = num_runs
        self.units = units
        self.live_view = live_view
        self.report_data = report_data
        self.run_metadata = run_metadata
        self.ao_lines = ao_lines
        self.cap_lines = cap_lines
        self.runs_done = 0
        self.runaway_asked = False
        self.live_logs: list[list[str]] = []
        self._last_paint = time.monotonic()

    # -- engine hooks ---------------------------------------------------------------
    @property
    def on_event(self):
        """The per-event hook, with the animation delay applied to EVERY event (not
        just those passing the paint throttle). The delay is 0.0 while Animate is
        off, so a non-animated run pays nothing."""
        raw = self._on_event if self.live_view else self._on_event_keepalive

        def throttled(*args, **kwargs):
            self._throttle()
            return raw(*args, **kwargs)
        return throttled

    def _stop_requested(self) -> bool:
        return bool(getattr(self.window, "_sync_stop", False))

    def _on_event(self, scheduler, system_state, blocks, cap_data=None, model=None):
        now = time.monotonic()
        if now - self._last_paint < self.PAINT_INTERVAL:
            return
        self._last_paint = now
        w = self.window
        # System-status square and Raptor 7's status panels 2/3/4: cycles completed,
        # the current phase name, and MISSION/Nonmission (phased models).
        w._update_system_swatch(getattr(system_state, "color", None))
        w._update_phase_labels(system_state)
        # Repaint only when something painted actually changed. The kickoff burst at
        # t=0 is thousands of scheduler steps with an identical picture; repainting
        # each one looked frozen and dominated the run. `model` is the live engine
        # model: node/link/event/hierarchy colours are painted from it (the canvas's
        # own objects are a stale separate parse).
        sig = _live_state_signature(blocks, cap_data, model, system_state)
        if sig != getattr(w, "_live_sig", None):
            w._live_sig = sig
            w._diagram_dock.view.update_live_states(
                blocks, cap_data=cap_data, model=model,
                phase=getattr(system_state, "current_phase", None))
        # While Live Sim is on this is the sole writer of the status text.
        w._statusbar.showMessage(w._run_status_text(
            f"Running ({w._threads_label(1)}) — run "
            f"{self.runs_done + 1} of {self.num_runs}, "
            f"t = {scheduler.time:.3f}",
            self.runs_done, self.num_runs))
        QApplication.processEvents()
        if self._stop_requested():
            raise RunAborted()

    def _on_event_keepalive(self, scheduler, system_state, blocks, cap_data=None,
                            model=None):
        # Animate off: skip the repaint and status work, but still pump the loop
        # occasionally so a long run at max speed does not go unresponsive.
        now = time.monotonic()
        if now - self._last_paint < self.KEEPALIVE_INTERVAL:
            return
        self._last_paint = now
        QApplication.processEvents()
        if self._stop_requested():
            raise RunAborted()

    def _throttle(self) -> None:
        """Animation pacing: the speed dial's per-event delay, via
        `_effective_event_delay` (0 whenever Animate is off, matching Raptor 7's
        "no graphics = full speed" rule)."""
        delay = self.window._effective_event_delay()
        if delay:
            time.sleep(delay)

    def progress(self, completed: int, total: int) -> None:
        """Fired by the engine after every completed run."""
        w = self.window
        self.runs_done = completed
        if self.run_metadata.get("runaway_runs") and not self.runaway_asked:
            self.runaway_asked = True
            if completed < total and not w._ask_keep_going_after_runaway(completed, total):
                raise RunAborted()
        if self._stop_requested():
            # Raise through the engine so it stops between draws with results kept.
            raise RunAborted()
        pct = 100 * completed // max(total, 1)
        if not self.live_view:
            w._statusbar.showMessage(w._run_status_text(
                f"Running ({w._threads_label(1)}) — run {completed} of {total}",
                completed, total))
        w._run_progress.setValue(pct)
        w._run_progress.setVisible(True)
        w._run_progress_label.setText(f"{pct}%")
        w._run_progress_label.setVisible(True)
        # Cross-run averages (AvailMon.Mean semantics): refresh the permanent readouts
        # from every completed run's data, independent of any toggle.
        w._update_status_summary(self.report_data, self.units)
        if self.live_view and self.live_logs:
            self._show_results_dock()
            w._results.show_live_logs(self.live_logs, total)
        if self.cap_lines:
            # Capacity analysis on: stream the snapshots so far into the Graphs tab.
            w._results.update_live_capacity(self.cap_lines)
            self._show_results_dock()
        if self.ao_lines:
            # Live Ao plot (Raptor 7 displays it during a run).
            w._results.update_live_ao(self.ao_lines)
            self._show_results_dock()
        QApplication.processEvents()

    def _show_results_dock(self) -> None:
        if not self.window._results_dock.isVisible():
            self.window._results_dock.show()

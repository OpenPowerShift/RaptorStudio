"""The background (threaded) run: start, poll, pause, stop and animation speed.

Expects on `self`: `_bg_thread` and the run state, `_diagram_dock`, `_statusbar`."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.gui.ui.window_support import _resume_cyclic_gc, _suppress_cyclic_gc

from raptor.gui.ui.qtcompat import req

from raptor.gui.ui import msgbox
import threading
import time
from PySide6.QtCore import (QEventLoop)
from PySide6.QtWidgets import (
    QApplication,
)

from raptor.gui.ui import settings
from raptor.core.engine.runner import run_simulation_with_reports




if TYPE_CHECKING:
    # Mixin methods call Qt on `self`; type-check them as a main window.
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtCore
    import PySide6.QtGui
    import PySide6.QtWidgets
    import raptor.gui.ui.diagram
    import raptor.gui.ui.inspector
    import raptor.gui.ui.results
else:
    _Base = object


class BackgroundRunMixin(_Base):
    """The background (threaded) run: start, poll, pause, stop and animation speed."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _BG_POLL_INTERVAL_DEFAULT: int
        _BG_POLL_INTERVAL_FLOOR: int
        def _apply_hier_status(self, report_data: Any) -> None: ...
        def _apply_importance_progress(self, state: Any) -> None: ...
        _bg_baseline_presented: bool
        _bg_baseline_presenting: bool
        _bg_last_bar: Any
        _bg_last_progress: Any
        _bg_last_report_count: int
        _bg_last_t: Any
        _bg_poll: PySide6.QtCore.QTimer
        _bg_state: dict
        _bg_thread: Any
        def _build_run_manifest(self, rbd: Any = ..., isolation: Any = ...) -> dict: ...
        def _checked_overlay_state(self) -> dict[str, bool]: ...
        def _clear_run_eta(self) -> None: ...
        _current_file: str | None
        def _current_units(self) -> Any: ...
        _diagram_dock: raptor.gui.ui.diagram.DiagramDock
        def _disable_run_panel_for_run(self) -> None: ...
        @staticmethod
        def _discard_report_streams(streams: Any) -> None: ...
        def _effective_run_interval(self, key: str) -> float: ...
        def _ensure_step_session(self) -> Any: ...
        def _hide_phase_labels(self) -> None: ...
        _imp_eta: Any  # really None
        _imp_last_key: Any
        _importance_overlay_action: Any
        _importance_running: bool
        _inspector: raptor.gui.ui.inspector.InspectorDock
        _last_run_model: Any  # really raptor.core.fileio.rbd.RbdFile | None
        _live_sim_action: PySide6.QtGui.QAction
        def _note_results_basis(self) -> None: ...
        def _note_run_profile(self, done_runs: int) -> None: ...
        def _on_step_jump(self) -> Any: ...
        def _open_report_streams(self, choices: dict[str, bool] | None = ...) -> dict: ...
        _pause_action: Any
        def _prepare_run_model(self, copy: bool = ...) -> Any: ...
        def _present_background_baseline(self, state: Any) -> bool: ...
        def _present_results(self) -> Any: ...
        def _push_weak_settings(self) -> Any: ...
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _reapply_checked_overlays(self, state: dict[str, bool] | None) -> None: ...
        def _reenable_run_panel(self) -> None: ...
        def _refresh_commands(self, state: Any = ..., only: set[str] | None = ...) -> None: ...
        def _refresh_live_step_view(self, session: Any) -> Any: ...
        def _remember_report_streams(self, streams: dict) -> None: ...
        def _report_choices(self) -> dict[str, bool]: ...
        def _reset_live_log(self) -> Any: ...
        def _reset_status_summary(self) -> Any: ...
        _results: raptor.gui.ui.results.ResultsDock
        _results_dock: PySide6.QtWidgets.QDockWidget
        _results_float_action: Any
        _results_show_action: Any
        _run_in_progress: bool
        _run_progress: PySide6.QtWidgets.QProgressBar
        _run_progress_label: PySide6.QtWidgets.QLabel
        def _run_status_text(self, prefix: str, done: Any, total: Any) -> str: ...
        def _set_results_with_progress(self, *args: Any, follow_up: bool = ..., **kwargs: Any) -> Any: ...
        def _show_live_log_dock(self, show: bool, *, tabify: bool = ..., raise_: bool = ...) -> Any: ...
        _speed_faster: Any
        _speed_reset_1: Any
        _speed_reset_max: Any
        _speed_slower: Any
        _speed_state: Any  # really dict[str, None]
        def _start_run_eta(self) -> None: ...
        _statusbar: PySide6.QtWidgets.QStatusBar
        _step_finalize_state: Any
        _step_session: Any
        def _sync_cores_box(self) -> None: ...
        def _sync_overlay_actions(self, report_data: Any) -> None: ...
        _sync_stop: bool
        def _track_paused_eta(self, paused: bool) -> None: ...
        def _update_phase_labels(self, system_state: Any) -> None: ...
        def _update_readiness(self) -> None: ...
        def _update_status_summary(self, report_data: list, units: str | None) -> Any: ...
        def _update_system_swatch(self, colour: Any, force: bool = ...) -> None: ...
        current_phase: Any
        op_status: Any
        status: Any
        term_counter: Any
        value: Any

    def _start_background_run(self, num_runs: int):
        if self._step_finalize_state is not None:
            self._statusbar.showMessage(
                "Finalizing the stepped run — wait for its results", 5000)
            return
        if self._bg_thread is not None and self._bg_thread.is_alive():
            return
        # Taken on the GUI thread, before the worker starts.
        bg_model, isolation = self._prepare_run_model(copy=True)
        if bg_model is None:
            return
        self._last_run_model = bg_model
        main_manifest = self._build_run_manifest(bg_model, isolation)
        # Plain-data snapshots for the worker: QAction/QSettings reads
        # must stay on this thread.
        report_choices = self._report_choices()
        event_delay = self._effective_event_delay()
        state: dict = {"done": False, "progress": (0, num_runs),
                       "snapshot": None, "paused": False,
                       # Explicit worker phase. The poll uses it to stop
                       # rendering the baseline's last (100/100) stage once
                       # the ordinary batch is over, and to present the
                       # baseline exactly once while importance runs.
                       "phase": "baseline", "baseline_ready": False}
        # `resume_ev` cleared = paused (Event.wait blocks while clear);
        # Resume sets it, Pause clears it again.
        resume_ev = threading.Event()
        resume_ev.set()
        stop_ev = threading.Event()
        skip_ev = threading.Event()
        self._results.clear()   # fresh batch: no stale numbers
        self._diagram_dock.view.clear_result_overlays()
        self._reset_live_log()
        state["resume_ev"] = resume_ev
        state["stop_ev"] = stop_ev
        state["skip_ev"] = skip_ev
        self._reset_status_summary()
        self._start_run_eta()
        # Fresh batch: reset the "what did we last paint" trackers so the
        # first poll shows the new run's progress.
        self._bg_last_progress = None
        self._bg_last_bar = None
        self._bg_last_report_count = -1
        self._bg_baseline_presented = False
        self._bg_baseline_presenting = False
        self._bg_state = state

        units = self._current_units()
        live_view = self._live_sim_action.isChecked()
        # Parallel workers: only when the user asked for them and no live
        # stepping view (the on_event hook cannot cross process
        # boundaries). Independent stream segments make replications
        # statistically independent; results are not bit-identical to a
        # sequential batch by design.
        from raptor.gui import cores
        workers = 1 if live_view else cores.chosen()
        # What the engine will actually use: `run_simulation` only takes
        # the parallel path when num_workers>1 and runs>1, so a
        # single-replication batch is shown as 1 thread even if the
        # preference is higher.
        state["workers"] = workers if (workers > 1 and num_runs > 1) else 1
        # How many of those may be busy right now. The Cores box can lower it
        # (and raise it again, up to `workers`) while the batch runs; the
        # engine reads it between runs. Plain int, written on the GUI thread,
        # read from the worker thread: an assignment is atomic.
        state["workers_now"] = state["workers"]

        def worker():
            # Held outside the try so a failure before the batch is
            # remembered can close this batch's temp streams (every batch
            # now opens an End-of-Run stream, even with no Files & Reports
            # boxes ticked).
            streams: dict = {}
            # A cyclic-GC pass here walks every Python object in the
            # process, including PySide wrappers owned by the GUI thread.
            # Clearing a Qt cycle on this thread while the canvas paints
            # its C++ object is a use-after-free (seen as a segfault in
            # `Garbage-collecting` on a large model). The run's own objects
            # are reference-counted; defer only the cyclic collector and
            # restore it when the worker exits. Reference-counted because
            # a Terminate finalize worker may overlap this one.
            _suppress_cyclic_gc()
            try:
                report_data: list = []
                report_notes: list[str] = []
                # Same Files & Reports opt-in + disk-streaming as the
                # synchronous path (created on the main thread via the
                # signal-connected helper, appended to from this worker
                # thread — file appends are GIL-atomic, same rule as the
                # list sinks below).
                streams = self._open_report_streams(report_choices)
                # keep the raw stream objects (fresh stream is falsy —
                # never `or []` it, or the sink is lost)
                ao_lines = streams.get("ao")
                cap_lines = streams.get("capacity")
                cost_lines = streams.get("cost")
                run_metadata: dict = {}
                # Frozen on the MAIN thread before the worker starts --
                # the manifest must describe the model as it was when the
                # run was launched, not as it is when the worker gets
                # scheduled.
                run_metadata["manifest"] = main_manifest
                # Same "only plain data crosses the thread boundary" rule
                # as `state["snapshot"]`/`state["progress"]` below --
                # `report_sink` is appended to in place by the engine as
                # each run finishes, so exposing this same list reference
                # lets `_bg_poll_tick` (main thread) read the latest
                # cumulative stats without any cross-thread Qt calls.
                # `list.append()` is GIL-atomic; a poll landing mid-append
                # just reads one run behind, same as `progress` already can.
                state["report_data"] = report_data
                state["cap_lines"] = cap_lines
                state["ao_lines"] = ao_lines

                def progress(done, total):
                    state["progress"] = (done, total)
                    if (run_metadata.get("runaway_runs") and done < total
                            and not state.get("runaway_asked")):
                        # Pause the worker; the poll on the GUI thread asks (a thread cannot open a modal).
                        state["runaway_ask"] = (done, total)
                        resume_ev.clear()

                def on_event(sched, system_state, blocks, cap_data=None,
                             model=None):
                    # Only plain data crosses the thread boundary.
                    state["snapshot"] = (
                        sched.time,
                        [(b.id, getattr(b.op_status, "value",
                                        str(b.op_status)))
                         for b in blocks],
                        str(getattr(system_state, "color", "")))
                    # Raptor 7's status panels 2/3/4 (cycles /
                    # phase name / MISSION flag), plain tuples -- same
                    # thread rule as `snapshot`.
                    from raptor.core.engine.system_status import MissionStatus
                    state["phase_snapshot"] = (
                        int(getattr(system_state, "current_phase", 1) or 1),
                        getattr(system_state, "mission_status",
                                None) is MissionStatus.MISSION,
                        int(getattr(system_state, "term_counter", 0) or 0))
                    # Node/link/event/hier live statuses, plain maps --
                    # the canvas's own objects are a stale separate
                    # parse, so without these the background live view
                    # could only animate blocks.
                    if model is not None:
                        def _val(v):
                            return getattr(v, "value", str(v))

                        state["element_snapshot"] = (
                            {nid: _val(getattr(n, "status", None))
                             for nid, n in model.nodes_by_id.items()},
                            {link.object_num: _val(getattr(link, "status", None))
                             for link in model.links_by_id.values()},
                            {eid: _val(getattr(e, "op_status", None))
                             for eid, e in model.events_by_id.items()},
                            {hid: _val(getattr(h, "status", None))
                             for hid, h in model.hiers_by_id.items()})
                    # Live capacity for the diagram's node readouts
                    # (plain dict copies -- same thread rule).
                    if cap_data is not None:
                        state["cap_snapshot"] = cap_data
                    # Animation pacing: delay per processed event
                    # (manual B-11 Increase/Decrease Sim Speed). Uses the
                    # SAME helper as the synchronous path, which returns
                    # 0.0 while Animate is off — Raptor 7's rule
                    # (Raptor 7 deactivates its time slice
                    # without graphics forces max speed). Reading
                    # `_speed_state` directly here is what made the dial
                    # apply to background runs but not to the default
                    # foreground mode.
                    d = event_delay
                    if d:
                        time.sleep(d)

                def stop_check():
                    if not resume_ev.is_set():
                        state["paused"] = True
                        resume_ev.wait()
                        state["paused"] = False
                    return stop_ev.is_set()

                def skip_check():
                    # Clear-once: exactly one run is abandoned per Jump
                    # press (otherwise every later run would be skipped
                    # too, since the flag stays latched).
                    if skip_ev.is_set():
                        skip_ev.clear()
                        return True
                    return False

                def report_progress(done, total, label):
                    # Plain data only: the poll (main thread) renders it.
                    state["stage"] = (label, done, total)

                logs, eor, fin = run_simulation_with_reports(
                    bg_model, num_runs=num_runs,
                    report_sink=report_data, notes_sink=report_notes,
                    progress=progress,
                    on_event=on_event if live_view else None,
                    ao_sink=streams.get("ao"),
                    capacity_sink=streams.get("capacity"),
                    cost_sink=streams.get("cost"),
                    end_of_run_sink=streams.get("end_of_run"),
                    report_progress=report_progress,
                    collect_logs=report_choices.get("event_log", True),
                    metadata_sink=run_metadata,
                    # `workers` is already forced to 1 when live_view is
                    # on (above).
                    num_workers=workers,
                    worker_limit=lambda: state["workers_now"],
                    stop_check=stop_check,
                    skip_check=skip_check,
                    ao_plot_interval=self._effective_run_interval(
                        "ao_interval"),
                    cost_plot_interval=self._effective_run_interval(
                        "cost_interval"),
                    keep_partial_logs=True)
                state["result"] = (logs, eor, fin, report_data,
                                   report_notes, ao_lines, cap_lines,
                                   cost_lines, units)
                # Publish the artefacts the baseline presentation needs
                # before the long importance wait, so the interim results
                # carry the stream and manifest exactly as the final
                # refresh will.
                state["streams"] = streams
                state["run_metadata"] = run_metadata
                # Importance measures run here, in the worker, after the
                # ordinary batch -- which is their baseline. Same rule as
                # `api.run`: the user's own results exist before the extra
                # batches are spent.
                _mode = str((getattr(bg_model, "experiment", None) or {})
                            .get("importance", "none"))
                if _mode not in ("none", "") and report_data:
                    # The baseline is complete and must stay on screen even
                    # if the importance bookkeeping (block_rows/batch_count)
                    # or the analysis itself fails. Publish the phase in ONE
                    # update, before either can raise, and treat everything
                    # below as note-only.
                    state.update({"baseline_ready": True,
                                  "phase": "importance"})
                    try:
                        from raptor.api import importance as _imp
                        state["importance_total"] = _imp.batch_count(
                            len(_imp.block_rows(bg_model)), _mode)
                        state["importance"] = _imp.analyse(
                            bg_model, report_data, mode=_mode,
                            runs=len(report_data), workers=workers,
                            # `stop_check`, not `stop_ev.is_set`: Pause is
                            # checked between variants (and blocks there),
                            # so a paused run neither advances nor spins to
                            # completion. Stop still sets `stop_ev`.
                            stop_check=stop_check,
                            progress=lambda d, t, lab: state.__setitem__(
                                "importance_progress", (d, t, lab)))
                    except Exception as exc:      # noqa: BLE001
                        # A failed importance pass (or its bookkeeping) must
                        # not lose the batch the user actually asked for.
                        report_notes.append(
                            f"importance measures were not calculated: {exc}")
                        state["importance"] = None
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._discard_report_streams(streams)
                state["error"] = "".join(
                    traceback.format_exception_only(type(e), e)).strip() \
                    + "\n" + traceback.format_exc(limit=8)
            finally:
                _resume_cyclic_gc()
                state.update({"phase": "done", "done": True})

        self._bg_thread = threading.Thread(target=worker, daemon=True)
        self._bg_thread.start()
        self._bg_poll.start()
        self._run_in_progress = True
        self._refresh_commands()
        # Lock AFTER the flag exists: `_disable_run_panel_for_run` derives
        # the lock from `_run_or_step_active`, which is false until this
        # point. Calling it first left the whole panel editable during a
        # background batch.
        self._disable_run_panel_for_run()
        self._run_progress.setValue(0)
        self._run_progress.setVisible(True)
        self._run_progress_label.setText("0%")
        self._run_progress_label.setVisible(True)
        self._statusbar.showMessage(
            f"Background run started — {num_runs} run(s). Partial results "
            f"appear as runs complete; full results when the batch finishes.",
            10000)

    @staticmethod
    def _threads_label(n: int) -> str:
        """Status-bar worker count: "1 thread" / "N threads" (the parallel
        batch's processes; the label follows the UI's own "worker thread"
        wording)."""
        return f"{n} core" if n == 1 else f"{n} cores"

    def _ask_keep_going_after_runaway(self, done: int, total: int) -> bool:
        """A run ended on the anti-runaway bound: the model's termination condition may never be met, and every
        later run will take as long. True to go on with the remaining runs."""
        from raptor.gui.ui.dialogs import question_box
        # On several cores a worker reports the run-away run before the runs
        # around it are merged, so "run 0 of 40" is all `done` can say.
        which = (f"Run {done} of {total}" if done >= 1
                 else f"A run (of {total})")
        rest = (f"the other {total - done} run(s)" if done >= 1
                else "the remaining runs")
        return question_box(
            self, "Run did not finish normally",
            f"{which} did not meet its termination condition and was ended by the safety limit, so "
            f"its figures are not those of a completed run. Every remaining run is likely to take as long.\n\n"
            f"Keep going with {rest}?",
            yes_text="Keep going", no_text="Stop here")

    def _bg_poll_tick(self):
        if getattr(self, "_results_populating", False):
            return
        state = self._bg_state
        if not state:
            return
        phase = str(state.get("phase") or "baseline")
        importance_phase = phase == "importance"
        baseline_phase = phase == "baseline"
        self._importance_running = importance_phase and not state.get("done")
        # Baseline finished, importance variants still running: put the
        # baseline's Final Results on screen ONCE, so the user can read them
        # instead of staring at "Processing results". `presenting` is set
        # before the population: `_present_results` pumps the event loop,
        # and a re-entrant poll must not start a second population.
        if (not state.get("done") and state.get("baseline_ready")
                and importance_phase
                and not getattr(self, "_bg_baseline_presented", False)
                and not getattr(self, "_bg_baseline_presenting", False)):
            self._bg_baseline_presenting = True
            try:
                presented = self._present_background_baseline(state)
            finally:
                self._bg_baseline_presenting = False
            if presented:
                self._bg_baseline_presented = True
                result = state.get("result")
                if result:
                    # The baseline is now on screen; do not let the live
                    # Ao/Capacity catch-up re-show the dock or steal the
                    # Graphs tab while importance runs.
                    self._bg_last_report_count = len(result[3] or [])
        # Importance owns the status line and the bar for this phase. The
        # ordinary-batch progress/stage below are stale now (the worst is
        # the runner's "Final results report 100/100"), so they must not be
        # rendered on top on every 100 ms poll.
        if importance_phase:
            self._apply_importance_progress(state)
        if state.get("runaway_ask") and not state.get("runaway_asked"):
            state["runaway_asked"] = True
            done, total = state["runaway_ask"]
            if not self._ask_keep_going_after_runaway(done, total):
                state["stop_ev"].set()
            state["resume_ev"].set()
        stage = state.get("stage")
        if stage and not state.get("done") and baseline_phase:
            label, done, total = stage
            total = max(int(total), 1)
            self._statusbar.showMessage(
                f"Processing results — {label} {done}/{total}")
            pct = 100 * int(done) // total
            self._run_progress.setValue(pct)
            self._run_progress.setVisible(True)
            self._run_progress_label.setText(f"{pct}%")
            self._run_progress_label.setVisible(True)
        live = self._live_sim_action.isChecked()
        if state.get("snapshot"):
            snapshot = state["snapshot"]
            # Third slot (str(SystemColor)) feeds the status-bar square;
            # tolerate older 2-tuples (tests).
            if len(snapshot) == 3:
                t, statuses, sys_color = snapshot
            else:
                t, statuses = snapshot
                sys_color = None
            # System-status square: the snapshot's third slot carries
            # str(SystemColor) from the worker thread.
            from raptor.core.engine.system_status import SystemColor
            try:
                self._update_system_swatch(
                    SystemColor(sys_color) if sys_color else None)
            except ValueError:
                self._update_system_swatch(None)
            # Raptor 7's status panels 2/3/4 during a backgrounded
            # live run: plain tuple -> shim object, same reader the
            # synchronous path uses.
            phase_snap = state.get("phase_snapshot")
            if phase_snap is not None:
                class _Ph:
                    def __init__(self, idx, is_mission, cycles):
                        self.current_phase = idx
                        self.term_counter = cycles
                    mission_status = None

                ph = _Ph(*phase_snap)
                from raptor.core.engine.system_status import MissionStatus
                ph.mission_status = (MissionStatus.MISSION
                                     if phase_snap[1]
                                     else MissionStatus.NON_MISSION)
                self._update_phase_labels(ph)
            # Remembered across ticks (not just used on this one) so the
            # text-format decision below never depends on whether a
            # fresh snapshot happens to exist on this particular poll,
            # which would flip the status bar between two message formats
            # within the same run.
            self._bg_last_t = t
            if live:
                by_id = dict(statuses)

                class _Val:
                    def __init__(self, value):
                        self.value = value

                class _Shim:
                    def __init__(self, value):
                        self.op_status = _Val(value)

                shim = {bid: _Shim(by_id.get(bid, "Running"))
                        for bid in self._diagram_dock.view.blocks}
                if not self._diagram_dock.isVisible():
                    self._diagram_dock.show()
                # element_snapshot (plain maps) -> shim objects with the
                # same .status/.op_status attribute shape update_live_
                # states reads engine model objects with, so node/link/
                # event/hier colours animate in the background live view
                # too.
                elem = state.get("element_snapshot")
                dict_model = None
                if elem is not None:
                    nstat, lstat, estat, hstat = elem

                    class _S:
                        def __init__(self, v):
                            self.status = _Val(v)
                            self.op_status = _Val(v)

                    dict_model = {
                        "nodes": {nid: _S(v) for nid, v in nstat.items()},
                        "links": {num: _S(v) for num, v in lstat.items()},
                        "events": {eid: _S(v) for eid, v in estat.items()},
                        "hiers": {hid: _S(v) for hid, v in hstat.items()},
                    }
                self._diagram_dock.view.update_live_states(
                    shim, cap_data=state.get("cap_snapshot"),
                    model=dict_model)
        if state.get("progress") and baseline_phase:
            done, total = state.get("progress", (0, 0))
            paused = bool(state.get("paused"))
            pool = state.get("workers", 1)
            now = state.get("workers_now", pool)
            threads = (self._threads_label(pool) if now >= pool else
                       f"{now} of {pool} cores")
            self._track_paused_eta(paused)
            # Only repaint when the count (or paused state) actually
            # changed -- the poll runs at 10 Hz and would otherwise
            # rewrite the same status text many times per batch.
            key = (done, total, paused)
            if live:
                # Live time advances every tick, so this one still
                # refreshes each poll.
                t = getattr(self, "_bg_last_t", 0.0)
                self._statusbar.showMessage(self._run_status_text(
                    f"Background ({threads}) — run {min(done + 1, total)} "
                    f"of {total}, t = {t:.3f}", done, total))
                self._bg_last_progress = key
            elif key != getattr(self, "_bg_last_progress", None):
                self._bg_last_progress = key
                self._statusbar.showMessage(self._run_status_text(
                    f"Background ({threads}) — {done} of {total} run(s) "
                    f"complete"
                    + (" (PAUSED)" if paused else ""), done, total))
            if (done, total) != getattr(self, "_bg_last_bar", None):
                self._bg_last_bar = (done, total)
                pct = 100 * done // max(total, 1)
                self._run_progress.setValue(pct)
                self._run_progress.setVisible(True)
                self._run_progress_label.setText(f"{pct}%")
                self._run_progress_label.setVisible(True)

        # Cross-run permanent readouts (Ao/MTBDE/MDT/G-Y-R) -- same "every
        # completed run's data, independent of any toggle" rule the
        # synchronous run path's own `_progress` callback already applies
        # (see that callback's comment: "readouts appeared frozen during
        # runs when this was gated on the checkbox"). The background path
        # had the identical bug: `report_data` grows inside the worker
        # thread, but nothing here ever read it before `state["done"]`,
        # so backgrounded runs left the status bar's Ao/MTBDE/MDT/G-Y-R
        # readouts frozen at
        # their pre-run values for the whole run, only jumping to the
        # final numbers at completion -- looked like "doesn't update".
        # Refreshed only when another run lands (report_data grows); no
        # point recomputing against unchanged data every 100 ms.
        report_data = state.get("report_data")
        if (report_data and not importance_phase
                and len(report_data) !=
                getattr(self, "_bg_last_report_count", -1)):
            self._bg_last_report_count = len(report_data)
            self._update_status_summary(report_data, self._current_units())
            cap_lines = state.get("cap_lines")
            if cap_lines:
                # Live capacity in the background path too. Gated on
                # "a run landed", not every 100 ms poll.
                self._results.update_live_capacity(cap_lines)
                if not self._results_dock.isVisible():
                    self._results_dock.show()
            ao_lines = state.get("ao_lines")
            if ao_lines:
                # Live Ao too (Raptor 7 shows it; only Capacity had a
                # live update before).
                self._results.update_live_ao(ao_lines)
                if not self._results_dock.isVisible():
                    self._results_dock.show()

        if not state.get("done"):
            return
        self._bg_poll.stop()
        # Keep the run locked until the final results are on screen. A Run
        # click landing in the population gap used to start a second batch
        # over the old one; user input is excluded from the pumps below for
        # the same reason.
        self._run_progress.setVisible(False)
        self._run_progress_label.setVisible(False)
        self._clear_run_eta()
        self._hide_phase_labels()
        self._diagram_dock.view.clear_live_states()
        self._importance_running = False
        baseline_presented = bool(
            getattr(self, "_bg_baseline_presented", False))
        try:
            err = state.get("error")
            if err:
                msgbox.critical(self, "Simulation Failed",
                                f"The background run failed:\n\n{err}")
                self._statusbar.showMessage("Run failed")
                return
            stopped = state["stop_ev"].is_set()
            importance = state.get("importance")
            # Same gap as the synchronous run path (see its own comment):
            # results still need parsing/rendering below, which can take a
            # visible moment on a large model -- without this the bar was
            # left showing the last "Background — ..." progress line.
            self._statusbar.showMessage(
                "Importance measures complete — refreshing results…"
                if importance is not None else "Processing results…")
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            (logs, eor, fin, report_data, report_notes, ao_lines, cap_lines,
             cost_lines, units) = state["result"]
            self._push_weak_settings()
            # Same safety-bound surfacing as the synchronous path.
            if state.get("run_metadata", {}).get("hit_safety_bound"):
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
            result_streams = state.get("streams") or {}
            eor_source = result_streams.get("end_of_run")
            if eor_source is None:
                eor_source = eor
            if baseline_presented and importance is not None:
                # Same run: only the Importance page is new. Rebuilding
                # every table a second time both costs a full report parse
                # and clears the overlay/tab the user chose while waiting.
                self._results.set_importance(importance)
                self._importance_overlay_action.setEnabled(True)
                self._note_results_basis()
                self._remember_report_streams(state.get("streams") or {})
                self._results_show_action.setEnabled(bool(report_data))
                self._results_float_action.setEnabled(bool(report_data))
                self._apply_hier_status(report_data)
            else:
                overlays = (self._checked_overlay_state()
                            if baseline_presented else None)
                self._set_results_with_progress(
                    logs, eor_source, fin, report_data=report_data,
                    units=units, notes=report_notes,
                    ao_lines=ao_lines, cap_lines=cap_lines,
                    cost_lines=cost_lines,
                    manifest=(state.get("run_metadata") or {}).get("manifest"),
                    importance=importance)
                self._note_results_basis()
                self._remember_report_streams(state.get("streams") or {})
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
                # The baseline presentation already surfaced Results.
                # Re-presenting would yank the user out of the tab they
                # moved to while waiting; the refresh preserves it.
                if not baseline_presented:
                    self._present_results()
            self._note_run_profile(len(report_data))
            self._update_status_summary(report_data, units)
            if stopped:
                if not report_data:
                    self._statusbar.showMessage(
                        "Stopped before any run completed — no results", 10000)
                elif len(report_data) < len(logs):
                    self._statusbar.showMessage(
                        "Stopped — partial event log kept for the interrupted "
                        "run", 10000)
                else:
                    self._statusbar.showMessage(
                        f"Stopped after {len(logs)} run(s) — results ready")
            else:
                self._statusbar.showMessage(
                    f"Completed {len(logs)} run(s) successfully — results "
                    f"ready" + (" (importance measures included)"
                                if importance is not None else ""))
        finally:
            # Unlock only now: the run is over whether it succeeded, failed
            # or was stopped. Every flag a later run could inherit is reset
            # here, not only on the happy path.
            self._run_in_progress = False
            self._refresh_commands()
            self._reenable_run_panel()
            self._imp_eta = None
            self._imp_last_key = None
            self._importance_running = False
            self._pause_action.setText("Pause")
            self._bg_baseline_presented = False
            self._bg_baseline_presenting = False

    def _toggle_bg_pause(self):
        # Pre-arm: Pause before any Run kicks off a stepped session
        # paused at t = 0, ready for Step/Multi/Run-To.
        bg_active = bool(getattr(self, "_bg_state", None))
        if (getattr(self, "_step_session", None) is None
                and not bg_active
                and self._rbd is not None):
            if self._ensure_step_session() is None:
                return
            self._show_live_log_dock(True)
            self._refresh_live_step_view(self._step_session)
            return
        state = getattr(self, "_bg_state", {})
        event = state.get("resume_ev")
        if event is None:
            return
        if event.is_set():
            event.clear()          # pause
            self._pause_action.setText("Resume")
        else:
            event.set()            # resume
            self._pause_action.setText("Pause")
        self._update_readiness()

    def _stop_bg_run(self):
        # Sync (in-thread) runs pump the event loop and check this flag
        # between runs; threaded runs use their own event below.
        #
        # "This is a sync run, not a background one" is decided from
        # `_run_in_progress` (True for the whole lifetime of either run
        # kind) combined with a genuinely live (not-yet-`done`)
        # `_bg_state` check -- the same "bg_running" test
        # `keyPressEvent`'s own Esc handler uses.
        if getattr(self, "_step_finalize_state", None) is not None:
            self._statusbar.showMessage(
                "Finalizing the stepped run — results appear when ready", 4000)
            return
        bg_state = getattr(self, "_bg_state", None)
        bg_running = bool(bg_state and not bg_state.get("done"))
        if not bg_running:
            if getattr(self, "_sync_stop", False) or \
                    getattr(self, "_run_in_progress", False):
                self._sync_stop = True
                self._statusbar.showMessage("Stop requested — finishing "
                                            "the current run…", 6000)
            return
        event = req(bg_state).get("resume_ev")
        if event is not None:
            event.set()            # wake a paused worker so it sees the stop
        stop = req(bg_state).get("stop_ev")
        if stop is not None:
            stop.set()

    def _jump_bg_run(self):
        if getattr(self, "_step_session", None) is not None:
            self._on_step_jump()
            return
        state = getattr(self, "_bg_state", {})
        event = state.get("skip_ev")
        if event is not None:
            res = state.get("resume_ev")
            if res is not None:
                res.set()       # wake a paused worker so it sees the jump
            event.set()
            self._statusbar.showMessage(
                "Jump — abandoning current run, starting next")

    def _sync_bg_poll_interval(self, delay):
        """`_bg_poll` (which samples the worker thread's latest event
        onto the canvas) would otherwise be fixed at 100ms regardless of
        the configured per-event delay, so any speed faster than 10
        events/sec (delay < 0.1s, reachable via Faster or Max) would
        have multiple real events overwrite `state["snapshot"]` between
        polls, dropping every one but the last and updating the canvas at
        a flat ~10Hz. Shrinks the poll interval to track the configured
        speed (never below the floor, to avoid hammering the event loop
        at Max)."""
        interval = (self._BG_POLL_INTERVAL_FLOOR if delay is None
                   else max(self._BG_POLL_INTERVAL_FLOOR,
                           min(self._BG_POLL_INTERVAL_DEFAULT,
                               int(delay * 1000))))
        self._bg_poll.setInterval(interval)

    def _set_animate(self, on: bool) -> None:
        """Animate toggle -> the engine's `live_view` and the speed dial's
        enabled state.

        This is APPLICATION state, and deliberately not a model edit.

        An earlier version wrote `SYSTEM_DATA/graphicsUse` here, to keep
        that column meaningful for a legacy Raptor opening our file. It
        was wrong, in a way only real use exposed: writing the model
        dirties the file and trips the edit-during-run guard, so with a
        step session live, clicking Animate threw a modal
        "Simulation in progress -- editing now will cancel it" every
        single time. A display toggle that interrupts you is not a
        display toggle.

        Raptor 7 is on the same side of this line. Its graphics
        checkbox lives in the Display module, and the setting it gates --
        the time slice -- is stored in `graphTimeSlice`, a VIEW column,
        alongside `zoomVal` and `xOrigin`. Panning the canvas and animating the run are the same
        category of decision: how to show the model, not what it is.

        So the state lives in the settings tree, under the same key the
        Preferences dialog's "Animate by default" row writes. `graphicsUse`
        stays in the file as the read-but-ignored legacy column the
        formatter round-trips, and nothing offers a control for it.
        """
        # With animation off the speed dial is meaningless (the original
        # deactivates its time slice on the same condition), so it goes
        # with it.
        self._set_speed_state_enabled(on)
        if not on and self._speed_state.get("delay") is not None:
            # Don't silently discard a rate the user dialled in; just
            # stop applying it. Re-enabling Animate restores it.
            self._sync_speed_label()
        settings.set_value("execution/default_animate", bool(on))
        # The Inspector's Display group shows the live state, which is
        # not on the model, so it has to be told.
        self._inspector.refresh_animate_row()
        self._sync_cores_box()
        self._update_readiness()
        from raptor.gui import cores
        self._statusbar.showMessage(cores.describe(on), 4000)

    def _set_speed_state_enabled(self, on: bool) -> None:
        """Enable/disable the speed dial with the Animate toggle.

        The disabled tooltip has to say WHY (the spec requires every
        disabled action to explain its unmet condition; see the internal
        UI blueprint), and re-enabling has to
        restore the button's OWN tooltip -- so the originals are captured
        once, not re-read from the action after being overwritten."""
        for act in (self._speed_slower, self._speed_reset_1,
                    self._speed_faster, self._speed_reset_max):
            if not hasattr(act, "_speed_tip"):
                act._speed_tip = act.toolTip()   # type: ignore[attr-defined]
            act.setEnabled(bool(on))
            if not on:
                act.setToolTip(
                    "Animation is off — the run goes at full speed. "
                    "Turn Animate on to set an animation speed.")
            else:
                act.setToolTip(act._speed_tip)   # type: ignore[attr-defined]

    def _effective_event_delay(self) -> float:
        """Seconds of real time to sleep per processed event, for BOTH
        run paths.

        Returns 0.0 (no throttle) whenever animation is off, matching the
        original's rule that a non-graphics run always goes at full
        speed (sets `timeSlice := 0.0000001` when
        graphics are off, having deactivated the field).

        Previously `_speed_state` was consulted ONLY inside the
        background worker, so the dial did nothing at all in the default
        foreground run mode.
        """
        if not self._live_sim_action.isChecked():
            return 0.0
        d = self._speed_state.get("delay")
        return float(d) if d else 0.0

    def _set_speed(self, delay):
        """`delay` = seconds per processed event; None = as fast as
        possible. Log-scale steps via _adjust_speed."""
        self._speed_state["delay"] = delay
        if self._bg_state.get("event_delay") is not None or delay:
            self._bg_state["event_delay"] = delay
        self._sync_bg_poll_interval(delay)
        self._sync_speed_label()

    def _sync_speed_label(self) -> None:
        lbl = getattr(self, "_speed_label", None)
        if not self._live_sim_action.isChecked():
            if lbl:
                lbl.setText("Speed: max (no animation)")
            return
        delay = self._speed_state.get("delay")
        if delay is None:
            self._statusbar.showMessage("Sim speed: maximum")
            if lbl:
                lbl.setText("Speed: max")
        else:
            self._statusbar.showMessage(
                f"Sim speed: ~{1.0 / delay:g} events/sec")
            if lbl:
                lbl.setText(f"Speed: {1.0 / delay:g} ev/s "
                            f"({delay:g}s/ev)")

    def _adjust_speed(self, direction: int):
        if not self._live_sim_action.isChecked():
            self._statusbar.showMessage(
                "Animation is off — the run goes at full speed")
            return
        d = self._speed_state.get("delay")
        if d is None:
            d = 0.1 if direction > 0 else None
            if d is None:
                self._set_speed(None)
                return
        else:
            d = d * (10.0 ** direction)
        d = max(0.001, min(d, 2.0))
        self._set_speed(d)

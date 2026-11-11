"""The stepping debugger: step, run-to, breakpoints, jump, finish and terminate.

Expects on `self`: `_step_session`, `_rbd`, `_diagram_dock`, `_statusbar` and the run state."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any


from raptor.gui.ui import msgbox
import time
from PySide6.QtCore import (Qt)
from PySide6.QtWidgets import (
    QApplication,
)

from raptor.core.fileio.typed import system_config_from_row
from raptor.core.model import BlockStatus
from raptor.core.engine.step_session import StepSession
from raptor.core.engine.reports import (
    build_end_of_run_report,
    build_final_results_report,
)




if TYPE_CHECKING:
    # Mixin methods call Qt on `self`; type-check them as a main window.
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtGui
    import PySide6.QtWidgets
    import raptor.gui.ui.diagram
    import raptor.gui.ui.live_log_panel
    import raptor.gui.ui.results
    import raptor.gui.ui.run_panel
else:
    _Base = object


class StepMixin(_Base):
    """The stepping debugger: step, run-to, breakpoints, jump, finish and terminate."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _NO_BREAKPOINT: str
        _breakpoint_combo: PySide6.QtWidgets.QComboBox
        def _build_run_manifest(self, rbd: Any = ..., isolation: Any = ...) -> dict: ...
        def _clear_run_eta(self) -> None: ...
        def _command_state(self, *, reuse_validity: bool = ...) -> Any: ...
        _compact_minimized: set[str]
        def _current_experiment(self) -> dict: ...
        _diagram_dock: raptor.gui.ui.diagram.DiagramDock
        def _disable_run_panel_for_run(self) -> None: ...
        _dock_chrome: dict
        _dock_stubs: dict
        _edit_run_abandoned: bool
        def _end_step_session(self, *, clear_sink: bool = ...) -> bool: ...
        _focus_event_action: Any
        _inspector_dock: PySide6.QtWidgets.QDockWidget
        _last_run_model: Any  # really raptor.core.fileio.rbd.RbdFile | None
        _live_log_dock: PySide6.QtWidgets.QDockWidget
        _live_log_panel: raptor.gui.ui.live_log_panel.LiveLogPanel
        _live_log_session_shown: bool
        _live_log_tree: PySide6.QtWidgets.QTreeWidget
        _live_sim_action: PySide6.QtGui.QAction
        def _on_fev_log_close(self) -> None: ...
        def _prepare_run_model(self, copy: bool = ...) -> Any: ...
        def _present_run_results(self, event_logs: Any, eor_lines: Any, final_lines: Any, report_data: Any, ao_lines: list[str] | None = ..., cap_lines: list[str] | None = ..., manifest: Any = ...) -> Any: ...
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _refresh_commands(self, state: Any = ..., only: set[str] | None = ...) -> None: ...
        def _reset_live_log(self) -> Any: ...
        _results: raptor.gui.ui.results.ResultsDock
        _results_dock: PySide6.QtWidgets.QDockWidget
        _run_panel: raptor.gui.ui.run_panel.RunPanel
        _run_progress: PySide6.QtWidgets.QProgressBar
        _run_progress_label: PySide6.QtWidgets.QLabel
        _run_to_time_spin: PySide6.QtWidgets.QDoubleSpinBox
        _statusbar: PySide6.QtWidgets.QStatusBar
        _step_ao: list[str]
        _step_count_spin: PySide6.QtWidgets.QSpinBox
        _step_finalize_state: Any
        _step_isolation: Any  # really None
        _step_session: Any
        _step_sink: list
        def _sync_dock_tab_bars(self) -> Any: ...
        def _sync_run_settings_lock(self) -> None: ...
        def _update_system_swatch(self, colour: Any, force: bool = ...) -> None: ...

    def _ensure_step_session(self):
        """Lazily build the interactive single-run session. Full kickoff
        (resets + synchronous initialization) happens HERE, synchronously,
        without advancing the clock."""
        if self._step_session is None or self._step_sink is None:
            # Step honours the Run panel's scope like Run does: a hierarchy
            # scope steps the isolated extract. The extract keeps the source
            # model's element ids, so the live colours land on the canvas.
            step_model, self._step_isolation = self._prepare_run_model()
            if step_model is None:
                return None
            self._last_run_model = step_model
            self._step_sink = []
            self._step_ao: list[str] = []
            self._results.clear()   # new debug session: clear stale results
            self._diagram_dock.view.clear_result_overlays()
            self._edit_run_abandoned = False
            # The session spans the configured runs, as Raptor 7's does: Jump goes on to the next run
            # and Finish completes every run that is left.
            self._step_session = StepSession(
                step_model, report_sink=self._step_sink,
                ao_sink=self._step_ao,
                num_runs=int(self._current_experiment()["trials"]))
            # A stepped session is a run for as long as it is open -- it
            # holds live engine objects and sits idle between clicks. The
            # run settings lock with it, exactly as for a batch; without
            # this the Simulate tab's Analyses toggles stay live, and each one
            # writes the model and so trips the edit-during-run guard's
            # modal.
            self._disable_run_panel_for_run()
            # A stepped run can be started from any tab (F6), and what it
            # needs next -- Finish, Restart, Terminate, the other steps --
            # is on Debug. Starting one used to leave the ribbon where it
            # was, with nothing on screen to end the session by.
            try:
                self._ribbon.select_page("Debug")
            except (AttributeError, KeyError):
                pass
        return self._step_session

    def _bp_name(self):
        """The chosen breakpoint block name, from whichever control holds
        it. The combo now lives in the Run panel (the ribbon has no room
        for it), and `_breakpoint_combo` is kept as an alias so the
        existing reads keep working."""
        bid = self._breakpoint_combo.currentData()
        if bid is None or self._rbd is None:
            return None
        live = self._diagram_dock.view.blocks.get(bid)
        if live is not None:
            return live.name
        row = next((r for r in self._rbd.sections["BLOCK_DATA"].rows
                    if str(r.get("ID")) == str(bid)), None)
        return row.get("blockName") if row is not None else None

    def _breakpoint_hit(self, session, prev_status) -> bool:
        """True when the selected block newly entered Repairing during
        the last step (a block "fails" = Running→Repairing transition;
        BlockStatus has no FAILED state of its own)."""
        bid = self._breakpoint_combo.currentData()
        if bid is None:
            return False
        for b in session.blocks:
            if b.id == bid:          # by id: names need not be unique
                return (b.op_status == BlockStatus.REPAIRING
                        and prev_status.get(b.id) != BlockStatus.REPAIRING)
        return False

    def _show_inspector_dock(self, *, expand_minimized: bool = False):
        """Bring the Inspector dock to front -- same show()+raise_()
        pattern used elsewhere for a tabbed dock (`_show_live_log_dock`).

        `show()` alone is not enough: a *closed* Inspector is collapsed
        to its rotated stub by hiding its content, and the QDockWidget
        stays visible, so `show()` was a no-op and right-click >
        Properties appeared to do nothing. Go through the stub's own
        restore -- but only when it is actually closed, and never touch
        a minimised dock's constraints unless an explicit reveal asks to
        expand it (`set_minimized(False)` restores its content first)."""
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(self._inspector_dock)) or {}
        if entry.get("closed"):
            stub = (self._dock_stubs or {}).get(self._inspector_dock)
            if stub is not None:
                stub(False)
        if expand_minimized and entry.get("minimized"):
            try:
                self._compact_minimized.discard("Inspector")
            except AttributeError:
                pass
            chrome = (self._dock_chrome or {}).get("Inspector")
            if chrome is not None:
                chrome["set_minimized"](False)
        self._inspector_dock.show()
        self._inspector_dock.raise_()

    def _show_live_log_dock(self, show: bool, *, tabify: bool = True,
                            raise_: bool = True):
        """Swap right-side docks: Live Log during stepping, Inspector
        otherwise. Tabified on the same area so the user's layout is
        preserved. Repeated steps pass `tabify=False, raise_=False`:
        re-tabifying or raising on every event would pull the tab back
        from the Inspector and rebuild tab bars needlessly."""
        if show:
            already = self._live_log_dock in \
                self.tabifiedDockWidgets(self._inspector_dock)
            if tabify and not already:
                self.tabifyDockWidget(self._live_log_dock,
                                      self._inspector_dock)
                self._sync_dock_tab_bars()
            if not self._live_log_dock.isVisible():
                self._live_log_dock.show()
            if raise_:
                self._live_log_dock.raise_()
            self._live_log_session_shown = True
        else:
            self._live_log_dock.hide()
            self._show_inspector_dock()
        self._refresh_commands(self._command_state(reuse_validity=True),
                               only={"live_session"})

    def _reset_right_docks(self):
        """The one right-hand dock policy for an unrun/closed session.

        A new or opened model, and the end of a stepped run, show the
        Inspector; the Live Log is hidden and emptied until a session
        starts. Other docks are deliberately left alone -- a user's
        Results/Run/Explorer arrangement is not this method's business.
        The corresponding View > Panels actions are synced from the live
        dock state (and the Live Log action's availability from the
        session predicate)."""
        live = getattr(self, "_live_log_dock", None)
        if live is not None:
            try:
                if live.isFloating():
                    # A restored layout can leave the Live Log as its own
                    # window. It belongs back in the right dock before it
                    # is hidden, or the next Show reopens a floating
                    # panel over the Inspector.
                    live.setFloating(False)
                    self.addDockWidget(
                        Qt.DockWidgetArea.RightDockWidgetArea, live)
                live.hide()
                self._reset_live_log()
            except RuntimeError:
                pass
        inspector = getattr(self, "_inspector_dock", None)
        if inspector is not None:
            try:
                self._show_inspector_dock()
            except RuntimeError:
                pass
        self._live_log_session_shown = False
        for dock in (live, inspector):
            if dock is None:
                continue
            fn = (getattr(self, "_dock_state_sync", None) or {}).get(dock)
            if fn is not None:
                try:
                    fn(dock.isVisible())
                except RuntimeError:
                    pass
        self._refresh_commands(self._command_state(reuse_validity=True),
                               only={"live_session"})

    def _on_live_log_row_clicked(self):
        """Focus the canvas on the selected live-log row's element."""
        items = self._live_log_tree.selectedItems()
        if not items:
            return
        name = items[0].text(2).strip()
        if not name:
            return
        # `_on_result_element_clicked` locates a block or node by name and
        # says so in the status bar when there is no match; a system-level
        # row ("System") simply has nothing to locate.
        for kind in ("block", "node"):
            if self._diagram_dock.view.locate_element(kind, name):
                return
        self._statusbar.showMessage(
            f"Live log row has no canvas element named '{name}'", 4000)

    def _note_whole_model_tool(self, tool: str) -> None:
        """Failure Effects and the batch tools always use the whole model;
        say so when a hierarchy run scope is selected, rather than leave
        the user to assume they follow it."""
        panel = getattr(self, "_run_panel", None)
        if panel is not None and panel.hierarchy_scope():
            self._statusbar.showMessage(
                f"{tool} uses the whole model; the Run panel's hierarchy "
                "scope applies to Run and Step only", 8000)

    def _step_scope_suffix(self) -> str:
        iso = getattr(self, "_step_isolation", None)
        name = getattr(iso, "hierarchy", "") if iso is not None else ""
        return f" hierarchy “{name}”" if name else ""

    def _refresh_live_step_view(self, session):
        # Refresh after every step (no throttle -- stepping is user-paced,
        # the whole point is watching each event land).
        live = self._live_sim_action.isChecked()
        if live and not self._diagram_dock.isVisible():
            self._diagram_dock.show()
        # Manual stepping is user-paced: paint the live colours (and
        # cache them for level traversal) even with Animate off. Finishing
        # and terminating clear them explicitly.
        self._diagram_dock.view.update_live_states(
            session.blocks, model=session.model,
            phase=getattr(getattr(session.cascade, "system_state", None),
                          "current_phase", None))
        # Status-bar system square tracks stepping too, including with
        # Animate off (force shows it without the live-run gate).
        self._update_system_swatch(
            getattr(getattr(session.cascade, "system_state", None),
                    "color", None), force=True)
        # The Live Log belongs to the stepped session, not to the Animate
        # canvas overlay: show it even when Animate is off, so Step always
        # has an event list to read. Tabify/raise only on the session's
        # first step, so repeated steps do not steal the Inspector tab.
        first_view = not getattr(self, "_live_log_session_shown", False)
        self._show_live_log_dock(True, tabify=first_view,
                                 raise_=first_view)
        if first_view:
            self._live_log_session_shown = True
        # Follows the run being stepped, across run boundaries; the
        # selector keeps the finished runs readable.
        iso = getattr(self, "_step_isolation", None)
        self._live_log_panel.set_scope_note(
            getattr(iso, "hierarchy", None) if iso is not None else None)
        self._live_log_panel.update_session(
            [lg for lg, _ in getattr(session, "completed", [])],
            session.logs, getattr(session, "num_runs", 1), session.time)
        logs = session.logs
        self._statusbar.showMessage(
            f"Stepping{self._step_scope_suffix()} — t = {session.time:.3f} "
            f"({len(logs)} event-log lines)"
            f"{self._phase_status_suffix(session)}")
        # Live progress in the Live Log dock/tab title -- the tabified
        # label doubles as the tab text. The Live Log dock uses the plain
        # Qt titlebar, which re-reads windowTitle() on show, so there is
        # no cached custom-titlebar label to keep in sync.
        self._live_log_dock.setWindowTitle(
            f"Live Log — t = {session.time:.3f} ({len(logs)} events)")
        if self._results_dock.isVisible() and live:
            self._results.show_live_logs(
                [lg for lg, _ in session.completed] + [logs],
                session.num_runs)
        QApplication.processEvents()

    def _describe_and_focus_step(self, session, prev_statuses,
                                 prev_line_count):
        """After one step: status-bar wording for WHAT changed + optional
        canvas centring on that element (Raptor 7's step semantics —
        'advance to the next event that results in a color change')."""
        new_lines = session.logs[prev_line_count:]
        v = self._diagram_dock.view
        target_item = None
        message = None
        focus_index = None
        # The event described is the LAST new log line that is not System
        # bookkeeping. Keep its absolute index: that exact Live Log row is
        # selected, whether or not the element lives on the canvas level
        # currently shown (a block inside a hierarchy has no current-level
        # item; the row must still be the described one).
        parsed: list[tuple[int, list[str]]] = []
        for offset, line in enumerate(new_lines):
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) >= 4:
                parsed.append((prev_line_count + offset,
                               parts))  # time, Kind, Name, Status, [comment]
        described: tuple[int, list[str]] | None = None
        for index, parts in reversed(parsed):
            if parts[1] == "System" or parts[2] in ("--",):
                continue
            described = (index, parts)
            break
        if described is None and parsed:
            # Only System bookkeeping in this step: describe/select the
            # newest one rather than leaving the previous row current.
            described = parsed[-1]
        if described is not None:
            focus_index, parts = described
            kind, name, status = parts[1], parts[2], parts[3]
            message = f"{kind} {name} → {status}  (t = {parts[0]})"
            # Best effort at centring the canvas: a hierarchy target (or a
            # trigger) simply has no item on the level shown -- do not
            # navigate to find it.
            target_item = self._canvas_item_for_log(kind, name)
        # Fall back to block-status diff when log parsing finds nothing.
        if message is None:
            for b in session.blocks:
                if prev_statuses.get(b.id) != b.op_status:
                    message = (f"{b.name} → "
                               f"{getattr(b.op_status, 'value', b.op_status)}"
                               f"  (t = {session.time:.3f})")
                    target_item = v._block_items.get(b.id)
                    focus_index = None
                    break
        if self._focus_event_action.isChecked():
            # Navigate to the level the event actually belongs to. This
            # enters a hierarchy for an inner block/node/event and leaves
            # it again when the next event is at Home or in a sibling.
            navigated = False
            if described is not None:
                navigated = v.locate_log_element(described[1][1],
                                                 described[1][2])
            if not navigated and target_item is not None:
                v.centerOn(target_item)
            if focus_index is not None:
                self._live_log_panel.focus_event(focus_index)
        shown = (message or f"Step — t = {session.time:.3f}") \
            + self._phase_status_suffix(session)
        self._statusbar.showMessage(shown)
        return message

    def _phase_status_suffix(self, session) -> str:
        """Raptor 7 status-bar readout during step sim: current
        phase name + Mission/Non-Mission flag (`SystemState.current_
        phase`/`mission_status`, both live-updated by the engine). Empty
        for a non-phased model -- it's always implicitly "phase 1",
        nothing worth surfacing."""
        phases = getattr(session.model, "phases", None)
        if not phases:
            return ""
        ss = session.cascade.system_state
        idx = ss.current_phase
        name = (phases[idx - 1].phase_name
                if 1 <= idx <= len(phases) else "")
        mission = getattr(ss.mission_status, "value", ss.mission_status)
        label = f'Phase {idx}/{len(phases)}'
        if name:
            label += f' "{name}"'
        return f" — {label} ({mission})"

    def _canvas_item_for_log(self, kind: str, name: str):
        """The canvas item a log row names, by the row's OWN kind. No
        cross-kind fallback: a `Hier` row must not select a same-named
        node, and a Trigger/Pool/System row has no canvas target."""
        v = self._diagram_dock.view
        if kind == "Block":
            return next((it for bid, it in v._block_items.items()
                         if v.blocks[bid].name == name), None)
        if kind == "Node":
            return next((it for nid, it in v._node_items.items()
                         if v.nodes[nid].node_name == name), None)
        if kind == "Event":
            return next((it for eid, it in v._event_items.items()
                         if v.events[eid].name == name), None)
        if kind == "Hier":
            hid = next((h for h in v.hiers if v.hiers[h].name == name), None)
            return v._hier_items.get(hid) if hid is not None else None
        return None

    def _clear_failed_step(self) -> None:
        """A step path failed: drop its live colours and stale Live Log."""
        self._diagram_dock.view.clear_live_states()
        self._reset_live_log()
        self._sync_run_settings_lock()

    def _on_step(self):
        if self._rbd is None or self._step_finalize_state is not None:
            return
        try:
            session = self._ensure_step_session()
            if session is None:           # isolation refused; user told why
                return
            prev = {b.id: b.op_status for b in session.blocks}
            prev_lines = len(session.logs)
            # Step pauses only when system state actually changed (Raptor 7's
            # TimeAdvance `IF SystemStateChange`): silent
            # calendar wakes (GenerateAoPlotData's AoPlotInterval timer,
            # CalculateInterimCost's CostPlotInterval timer) produce no
            # log lines and no pause. Skip them, or Next lands on an
            # AoPlot wake with nothing in the live log.
            while True:
                advanced = session.step()
                if not advanced or len(session.logs) > prev_lines:
                    break
            self._refresh_live_step_view(session)
            if not advanced:
                self._end_of_stepped_run(session)
                return
            self._describe_and_focus_step(session, prev, prev_lines)
            if self._breakpoint_hit(session, prev):
                self._statusbar.showMessage(
                    f"BREAKPOINT — {self._bp_name()} entered Repairing at "
                    f"t = {session.time:.3f}")
        except Exception:
            import traceback
            QApplication.restoreOverrideCursor()
            msgbox.critical(
                self, "Step Failed",
                "The step could not be executed:\n\n"
                + traceback.format_exc(limit=4))
            self._end_step_session()
            self._reset_live_log()

    def _on_step_change(self):
        """Step to the next system-state change (Raptor 7's Step; `StepSession.step_to_change`)."""
        if self._rbd is None or self._step_finalize_state is not None:
            return
        try:
            session = self._ensure_step_session()
            if session is None:           # isolation refused; user told why
                return
            prev = {b.id: b.op_status for b in session.blocks}
            prev_lines = len(session.logs)
            advanced = session.step_to_change()
            self._refresh_live_step_view(session)
            if not advanced:
                self._end_of_stepped_run(session)
                return
            self._describe_and_focus_step(session, prev, prev_lines)
            if self._breakpoint_hit(session, prev):
                self._statusbar.showMessage(
                    f"BREAKPOINT — {self._bp_name()} entered Repairing at "
                    f"t = {session.time:.3f}")
        except Exception:
            import traceback
            QApplication.restoreOverrideCursor()
            msgbox.critical(
                self, "Step Failed",
                "The step could not be executed:\n\n" + traceback.format_exc(limit=4))
            self._end_step_session()
            self._reset_live_log()

    def _advance_until(self, *, max_steps=None, target_time=None,
                       until_time_changes=False):
        """Shared Multi/Run-To/Next-Time driver: steps until whichever
        comes first -- step budget exhausted, breakpoint hit, target
        time reached, sim time advances past its starting value
        (`until_time_changes`), or the run ends. Focuses/describes only
        the LAST change (per-event centring during a multi-step would
        be seizure-inducing). Returns True if a breakpoint fired."""
        if self._step_finalize_state is not None:
            return False
        session = self._ensure_step_session()
        if session is None:               # isolation refused; user told why
            return False
        start_time = session.time if until_time_changes else None
        fired = False
        done = False
        prev_statuses = {b.id: b.op_status for b in session.blocks}
        prev_lines = len(session.logs)
        while not done:
            prev = {b.id: b.op_status for b in session.blocks}
            advanced = session.step()
            if advanced and len(session.logs) == prev_lines \
                    and target_time is None and max_steps is None:
                # Step pauses only on a system state change; a silent
                # calendar wake (AoPlot/InterimCost interval timers, no
                # log lines) is sailed through -- skipping it keeps Next
                # Time/stepping off nonexistent times. Run-To and
                # budgeted Multi keep their exact one-entry semantics.
                continue
            if not advanced:
                done = True          # run out of events -> auto-finish below
                break
            if self._breakpoint_hit(session, prev):
                fired = True
                break
            if max_steps is not None:
                max_steps -= 1
                if max_steps <= 0:
                    break
            if target_time is not None and session.time >= target_time:
                break
            if until_time_changes and session.time > start_time:
                break
        if done:
            self._end_of_stepped_run(session)
        else:
            # Multi/Run-To/Next-Time are one user action: paint and tell
            # the Live Log only the final state. Per-internal-event
            # repaints now that stepping always colours would animate the
            # whole batch and slow it down.
            self._refresh_live_step_view(session)
            self._describe_and_focus_step(session, prev_statuses,
                                          prev_lines)
        if fired:
            # After the description: said before it, the narration of the
            # last change overwrote it and the stop looked unexplained.
            self._statusbar.showMessage(
                f"BREAKPOINT — {self._bp_name()} entered Repairing at "
                f"t = {session.time:.3f}")
        return fired

    def _on_step_next_time(self):
        """"Next Time": several events often share the exact same SimTime
        (simultaneous transitions), so stepping one event at a time
        means clicking through all of them before anything visually new
        happens. This advances through every event at the current time
        and stops as soon as sim time actually moves forward, or a
        breakpoint/the run's end intervenes first."""
        if self._rbd is None:
            return
        self._advance_until(until_time_changes=True)

    def _on_step_multi(self):
        if self._rbd is None:
            return
        n = self._run_panel.step_count() if self._run_panel is not None \
            else self._step_count_spin.value()
        self._advance_until(max_steps=n)

    def _on_run_to(self):
        if self._rbd is None:
            return
        from raptor.core.units_conv import display_to_native
        try:
            native_units = system_config_from_row(
                self._rbd.sections["SYSTEM_DATA"].rows[0]).units
        except (ValueError, KeyError, IndexError, AttributeError):
            native_units = ""
        raw = self._run_panel.run_to_time() if self._run_panel is not None \
            else self._run_to_time_spin.value()
        target = display_to_native(
            raw, native_units,
            getattr(self._rbd, "display_unit", None))
        self._advance_until(target_time=target)

    def _populate_breakpoints(self):
        """(Re)build the breakpoint list from EVERY block in the model.

        It read the view's blocks -- the current level only -- and only on
        load, so a model whose blocks all sit inside hierarchies (the data
        centre) offered nothing to stop on, and a renamed block kept its
        old name. Blocks inside a hierarchy are labelled with their path
        ("IT_sub / Server_Row_1"); the choice is kept by block id, so it
        survives a rename and is refreshed after every edit."""
        rbd = self._rbd
        hier_name: dict[int, tuple[str, int]] = {}
        hier_section = (rbd.sections.get("HIERARCHY_DATA")
                        if rbd is not None else None)
        for r in (hier_section.rows if hier_section else []):
            try:
                hier_name[int(float(r["ID"]))] = (
                    r.get("hierarchyName", ""), int(float(r.get("Parent", 0) or 0)))
            except (KeyError, ValueError):
                continue

        def path(parent: int) -> str:
            parts, seen = [], set()
            while parent and parent in hier_name and parent not in seen:
                seen.add(parent)
                name, parent = hier_name[parent]
                parts.append(name)
            return " / ".join(reversed(parts))

        options: list[tuple[int, str]] = []
        for r in (rbd.sections["BLOCK_DATA"].rows
                  if rbd is not None and "BLOCK_DATA" in rbd.sections else []):
            try:
                bid = int(float(r["ID"]))
                parent = int(float(r.get("ParentId", 0) or 0))
            except (KeyError, ValueError):
                continue
            if bid == 0:
                continue
            where = path(parent)
            live = self._diagram_dock.view.blocks.get(bid)
            # The canvas holds this level's edits (a rename) until they
            # are written back to the rows.
            name = live.name if live is not None else r.get("blockName", "")
            options.append((bid, f"{where} / {name}" if where else name))
        options.sort(key=lambda o: o[1].lower())
        combo = self._breakpoint_combo
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(self._NO_BREAKPOINT, None)
        for bid, label in options:
            combo.addItem(label, bid)
        idx = combo.findData(current) if current is not None else -1
        combo.setCurrentIndex(idx if idx > 0 else 0)
        combo.blockSignals(False)
        panel = getattr(self, "_run_panel", None)
        if panel is not None and panel._breakpoint is not combo:
            panel.set_breakpoint_options(options)

    def _end_of_stepped_run(self, session) -> None:
        """A stepped run has no events left. Raptor 7 stays in step mode
        across trials: the next trial starts and pauses at its first
        change (what Jump does). Only after the last trial do the results
        come up. This used to call Finish, which ran every remaining trial
        at full speed -- one stepped run, then hundreds unseen."""
        if not session.has_next_run:
            self._on_step_finish()
            return
        session.jump_to_next_run_start()
        # Snapshot AFTER the jump: it replaces the driver's log list, so
        # a `prev_lines` taken before points past the new run's lines and
        # Focus Event would have no row to select.
        prev = {b.id: b.op_status for b in session.blocks}
        prev_lines = len(session.logs)
        session.step_to_change()
        if len(session.logs) == prev_lines:
            # The new run paused on its kickoff rows (they were already in
            # the fresh log); they are the event to show.
            prev_lines = 0
        self._refresh_live_step_view(session)
        description = self._describe_and_focus_step(session, prev, prev_lines)
        self._statusbar.showMessage(
            f"Run {session.run_index} finished — now stepping run "
            f"{session.run_index + 1} of {session.num_runs} "
            f"(Finish runs the rest without stopping)"
            + (f" — {description}" if description else ""), 8000)
        self._refresh_commands()

    def _on_step_jump(self):
        """Jump: the rest of this run goes by, and the next run stops at its first
        change (`StepSession.jump_to_next_run_start`)."""
        session = self._step_session
        if session is None or not session.has_next_run:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                session.jump_to_next_run_start()
                prev = {b.id: b.op_status for b in session.blocks}
                prev_lines = len(session.logs)
                session.step_to_change()
                if len(session.logs) == prev_lines:
                    # Kickoff already logged this run's first rows: they
                    # are the step Focus Event should reveal.
                    prev_lines = 0
            finally:
                QApplication.restoreOverrideCursor()
            self._refresh_live_step_view(session)
            description = self._describe_and_focus_step(
                session, prev, prev_lines)
            self._statusbar.showMessage(
                f"Jump — run {session.run_index + 1} of {session.num_runs}, "
                f"t = {session.time:.3f}"
                + (f" — {description}" if description else ""))
            self._refresh_commands()
        except Exception:
            import traceback
            msgbox.critical(
                self, "Jump Failed",
                "The jump could not be executed:\n\n" + traceback.format_exc(limit=4))
            self._end_step_session()
            self._reset_live_log()

    def _on_step_finish(self):
        """Complete the stepped run (remaining events + closing passes),
        then present results exactly like a normal batch run."""
        if self._step_session is None:
            return
        session = self._step_session
        self._on_fev_log_close()
        self._end_step_session(clear_sink=False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            event_logs, _ = session.finish_all()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            msgbox.critical(self, "Simulation Failed",
                                 f"The run failed:\n\n{e}")
            self._clear_failed_step()
            self._run_progress.setVisible(False)
            self._run_progress_label.setVisible(False)
            self._clear_run_eta()
            self._statusbar.showMessage("Run failed")
            return
        eor_lines: list[str] = []
        final_lines: list[str] = []
        report_data = list(self._step_sink)
        if report_data:
            model = session.model
            date = time.ctime()
            file_name = session.rbd_file.file_name
            eor_lines = [f"End_Of_Run Report for {file_name}  {date}", ""]
            for report in report_data:
                eor_lines.extend(build_end_of_run_report(report, model.blocks_by_id))
            final_lines = build_final_results_report(
                report_data, model.blocks_by_id, file_name,
                model.run_length, date,
                termination_type=model.termination_type,
            )
        QApplication.restoreOverrideCursor()
        self._diagram_dock.view.clear_live_states()
        # The manifest says how many runs the numbers on screen come from.
        step_manifest = self._build_run_manifest(
            self._last_run_model if getattr(self, "_step_isolation", None)
            else None, getattr(self, "_step_isolation", None))
        if step_manifest:
            step_manifest["trials"] = len(event_logs)
        try:
            self._present_run_results(event_logs, eor_lines, final_lines,
                                      report_data,
                                      ao_lines=list(self._step_ao or []),
                                      cap_lines=session.cap_lines,
                                      manifest=step_manifest)
        finally:
            # Finish is the end of the stepped run: the progress line
            # belongs to the finishing/population work, not to the idle
            # results screen (#230).
            self._run_progress.setVisible(False)
            self._run_progress_label.setVisible(False)
            self._clear_run_eta()

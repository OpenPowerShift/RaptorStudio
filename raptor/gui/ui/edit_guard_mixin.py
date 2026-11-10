"""Guards for editing while a run is active or results are on screen.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req

import time
from typing import TYPE_CHECKING, Any
from PySide6.QtCore import (QTimer)
from PySide6.QtWidgets import (
    QMessageBox,
)

from raptor.core import run_manifest




if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class EditGuardMixin(_Base):
    """Guards for editing while a run is active or results are on screen."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        _EDIT_GUARD_MAX_PROMPTS: Any
        _EDIT_GUARD_WINDOW: Any
        _diagram_dock: Any
        def _discard_step_finalize(self, message: str | None = ...) -> bool: ...
        _edit_during_run_guard: Any
        _edit_guard_prompt_times: list[float]
        _edit_guard_reentrancy: Any
        _edit_run_abandoned: Any
        def _invalidate_step_session(self) -> Any: ...
        _rbd: Any
        def _reset_status_summary(self) -> Any: ...
        _results: Any
        _results_basis: Any
        _results_guard_pending: Any
        _results_guard_reentrancy: Any
        def _run_or_step_active(self) -> bool: ...
        _statusbar: Any
        _step_finalize_state: Any
        def _stop_bg_run(self) -> Any: ...
        def _sync_overlay_actions(self, report_data: Any) -> None: ...
        def _sync_run_settings_lock(self) -> None: ...
        def _update_system_swatch(self, colour: Any, force: bool = ...) -> None: ...

    def _results_basis_hash(self) -> str | None:
        """Hash of the model as the DIAGRAM currently holds it."""
        if self._rbd is None:
            return None
        try:
            self._diagram_dock.view.write_back_to_rbd(self._rbd)
            return run_manifest.model_results_hash(self._rbd)
        except Exception:
            return None

    def _note_results_basis(self) -> None:
        """Remember which model the results now on screen came from.
        Called wherever results are handed to the Results dock."""
        self._results_basis = self._results_basis_hash()

    def _results_are_displayed(self) -> bool:
        return self._results.has_results()

    def _results_are_stale(self) -> bool:
        """Results are on screen and the model no longer matches them."""
        if getattr(self, "_results_guard_reentrancy", False):
            return False
        if not self._results_are_displayed():
            return False
        basis = getattr(self, "_results_basis", None)
        if basis is None:
            return False        # unknown origin: never nag on a guess
        now = self._results_basis_hash()
        return now is not None and now != basis

    def _arm_results_guard(self) -> None:
        if getattr(self, "_results_guard_pending", False):
            return
        self._results_guard_pending = True
        QTimer.singleShot(0, self._confirm_edit_discarding_results)

    def _confirm_edit_discarding_results(self) -> None:
        self._results_guard_pending = False
        if getattr(self, "_results_guard_reentrancy", False):
            return
        if not self._results_are_stale():
            return              # undone, or a burst that netted out
        self._results_guard_reentrancy = True
        try:
            stack = getattr(self._diagram_dock.view, "_stack", None)
            can_revert = bool(stack and stack.canUndo())
            box = QMessageBox(self)
            box.setWindowTitle("Results on screen")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(
                "These results were produced from the model as it was "
                "before this edit.\n\nKeeping the edit means discarding "
                "them; they would no longer match the model.")
            keep_btn = None
            if can_revert:
                keep_btn = box.addButton(
                    "Keep results (undo this edit)",
                    QMessageBox.ButtonRole.NoRole)
            discard_btn = box.addButton(
                "Discard results && edit", QMessageBox.ButtonRole.YesRole)
            box.setDefaultButton(keep_btn or discard_btn)
            box.exec()
            if box.clickedButton() is discard_btn:
                self._discard_results()
                self._statusbar.showMessage(
                    "Results discarded — the model was edited", 6000)
            elif keep_btn is not None and stack is not None:
                stack.undo()
                self._statusbar.showMessage(
                    "Edit undone — results kept", 6000)
            else:
                # Nothing to undo and the box was dismissed: the edit
                # already stands, so the results must go.
                self._discard_results()
        finally:
            self._results_guard_reentrancy = False

    def _discard_results(self) -> None:
        """Every surface that shows the old model's numbers."""
        self._results.clear()
        self._results_basis = None
        self._reset_status_summary()
        self._update_system_swatch(None)
        self._diagram_dock.view.clear_live_states(keep_result_visuals=False)
        self._diagram_dock.view._weak_colors_on = False
        self._sync_overlay_actions([])

    def _arm_edit_guard(self) -> None:
        """Queue the edit-during-run prompt, coalescing a burst of edits
        into one dialog.

        The zero-timer is what does the coalescing: a multi-element paste
        calls `_mark_dirty` once per command, and the queued callback runs
        only once, after the whole burst has been pushed and the last
        command is the top of the undo stack. That is also why the dialog
        cannot be shown inline here.
        """
        QTimer.singleShot(0, self._confirm_edit_during_run)

    def _edit_guard_prompt_budget(self) -> int:
        """Prompts left before the guard stops asking.

        Prunes expired timestamps as it goes, so the window is applied to
        the recorded prompts themselves rather than through a separate
        "last prompt" field that could disagree with the list.
        """
        now = time.monotonic()
        recent = [t for t in getattr(self, "_edit_guard_prompt_times", [])
                  if now - t <= self._EDIT_GUARD_WINDOW]
        self._edit_guard_prompt_times = recent
        return self._EDIT_GUARD_MAX_PROMPTS - len(recent)

    def _note_edit_guard_prompt(self) -> None:
        self._edit_guard_prompt_times.append(time.monotonic())

    def _confirm_edit_during_run(self):
        """Model edit while a simulation is running/stepping: confirm
        BEFORE anything consumes the edit. Scheduled by `_arm_edit_guard`
        via singleShot(0) so this runs AFTER the push completed (the edit
        is then the top of the undo stack); the re-entrant model_changed a
        revert emits re-enters `_mark_dirty`, which sees the guard flag
        and stays out of the way.

        Two protections, both about the *general* problem rather than any
        one operation:

        * **Re-entrancy.** `_edit_guard_reentrancy` makes the body
          non-re-entrant. Without it, anything the dialog's own handlers
          do -- `stack.undo()`, `_stop_run_for_edit()` -- can schedule
          another copy of this method while the first is still running,
          and since `box.exec()` pumps the event loop that second copy
          runs *inside* the first.
        * **A cap.** After `_EDIT_GUARD_MAX_PROMPTS` prompts in the
          window, the guard stops asking and simply stops the run. The
          prompt is a courtesy about a consequence the app has to enforce
          anyway (a model edit cannot leave a run executing against stale
          engine objects), so silently doing the enforced thing is right,
          and a bounded number of dialogs is a promise the UI can keep.
        """
        if getattr(self, "_edit_guard_reentrancy", False):
            return          # already inside a prompt; never stack
        if not getattr(self, "_edit_during_run_guard", False):
            # Direct call (not via the deferred scheduling path).
            self._edit_during_run_guard = True
        if not self._run_or_step_active():
            # The run finished between arming and firing. Nothing to ask.
            self._edit_during_run_guard = False
            return
        if self._edit_guard_prompt_budget() <= 0:
            # Out of budget: do the enforced thing without asking.
            self._edit_during_run_guard = False
            importance_only = bool(
                getattr(self, "_importance_running", False)
                or (getattr(self, "_bg_state", None) or {}).get("phase")
                == "importance")
            self._stop_run_for_edit()
            self._statusbar.showMessage(
                "Importance measures stopped — the model was edited"
                if importance_only else
                "Simulation stopped — the model was edited", 6000)
            return
        self._edit_guard_reentrancy = True
        self._note_edit_guard_prompt()
        try:
            importance_only = bool(
                getattr(self, "_importance_running", False)
                or (getattr(self, "_bg_state", None) or {}).get("phase")
                == "importance")
            stack = getattr(self._diagram_dock.view, "_stack", None)
            can_revert = bool(stack and stack.canUndo())
            box = QMessageBox(self)
            box.setWindowTitle(
                "Importance measures in progress" if importance_only
                else "Simulation in progress")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(
                "Importance measures are still running against the "
                "baseline results already on screen. Keeping this edit "
                "stops the measures; the baseline results stay."
                if importance_only else
                "A simulation is running (or being stepped), and it was "
                "built from the model as it was. Keeping this edit stops it.")
            # Two choices, each saying what it does. There was a third,
            # plain "Cancel" -- the default, and what Esc chose -- which
            # read as "never mind" but stopped the run and kept the edit.
            keep_btn = None
            if can_revert:
                keep_btn = box.addButton(
                    "Keep measuring (undo this edit)" if importance_only
                    else "Keep running (undo this edit)",
                    QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(keep_btn)
                box.setEscapeButton(keep_btn)
            stop_btn = box.addButton(
                "Stop measuring, keep the edit" if importance_only
                else "Stop the run, keep the edit",
                QMessageBox.ButtonRole.AcceptRole)
            if keep_btn is None:
                box.setDefaultButton(stop_btn)
                box.setEscapeButton(stop_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is keep_btn and keep_btn is not None:
                # Undo the just-pushed edit command; the re-entrant
                # model_changed this emits hits the guard flag above
                # and returns immediately. The run keeps going on the
                # unmodified model.
                req(stack).undo()
                self._statusbar.showMessage(
                    "Model edit reverted — importance measures continue"
                    if importance_only else
                    "Model edit reverted — simulation continues", 6000)
                return
            # "Stop the run, keep the edit": the run dies through the same
            # paths the Stop button uses.
            self._stop_run_for_edit()
        finally:
            # The re-entrancy latch is released last, in its own step, so
            # it is still held while `_stop_run_for_edit` and
            # `stack.undo()` above run their own `model_changed` cascade.
            # Releasing both together would let the last of those calls
            # schedule a fresh prompt.
            self._edit_during_run_guard = False
            self._edit_guard_reentrancy = False

    def _stop_run_for_edit(self):
        """The edit won: stop whatever run/step is active, using the
        Stop button's own mechanisms (sync flag / bg event / step
        session teardown)."""
        self._edit_run_abandoned = True
        if self._step_finalize_state is not None:
            # Terminate's finalize cannot be cancelled; drop its results
            # and let the worker wind down. The status says so instead of
            # silently losing the early-terminate report.
            self._discard_step_finalize(
                "Early-terminate results discarded — model edited")
            self._sync_run_settings_lock()
            return
        if getattr(self, "_step_session", None) is not None:
            self._invalidate_step_session()
            self._statusbar.showMessage(
                "Simulation cancelled — model edited", 6000)
            return
        self._stop_bg_run()

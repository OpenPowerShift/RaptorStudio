"""Run progress: the ETA, importance-analysis progress and the off-thread importance pass.

Expects on `self`: `_statusbar`, `_run_panel`, the background-run state."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any


import threading
from PySide6.QtCore import (QTimer, QEventLoop)
from PySide6.QtWidgets import (
    QApplication,
)





if TYPE_CHECKING:
    # Mixin methods call Qt on `self`; type-check them as a main window.
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtWidgets
else:
    _Base = object


class RunProgressMixin(_Base):
    """Run progress: the ETA, importance-analysis progress and the off-thread importance pass."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _bg_last_paused: bool
        def _current_experiment(self) -> dict: ...
        _imp_eta: Any  # really None
        _imp_last_key: Any
        _importance_running: bool
        _last_run_profile: tuple[float, int, float, int, int]
        _last_run_profile_rbd: int
        _pause_action: Any
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        _run_eta: Any  # really raptor.gui.ui.run_eta.RunEstimate | None
        _run_progress: PySide6.QtWidgets.QProgressBar
        _run_progress_label: PySide6.QtWidgets.QLabel
        _statusbar: PySide6.QtWidgets.QStatusBar

    def _start_run_eta(self) -> None:
        """Begin timing a batch for the "approximately N left" readout."""
        from raptor.gui.ui.run_eta import RunEstimate
        self._run_eta: RunEstimate | None = RunEstimate(
            extra_batches=self._importance_extra_batches())
        self._bg_last_paused = False
        # A previous run may have ended while paused; the button must not
        # come back reading "Resume" for a run that is not paused.
        self._pause_action.setText("Pause")
        # Importance has its own estimate (started when the baseline is
        # presented) and its own deduplicated status key.
        self._imp_eta: RunEstimate | None = None
        self._imp_last_key = None
        self._importance_running = False

    def _importance_extra_batches(self) -> int:
        """Batches importance measures add after the baseline (0 when off)."""
        try:
            mode = str(self._current_experiment().get("importance", "none")
                       or "none")
            model = getattr(self, "_last_run_model", None) or self._rbd
            if mode in ("", "none") or model is None:
                return 0
            from raptor.api import importance as _imp
            return max(0, _imp.batch_count(
                len(_imp.block_rows(model)), mode) - 1)
        except (ImportError, KeyError, ValueError, TypeError, AttributeError):
            return 0

    def _note_run_profile(self, done_runs: int) -> None:
        """Remember how long this batch actually took, for the next
        long-run confirmation's order-of-magnitude reference."""
        est = getattr(self, "_run_eta", None)
        if est is None or done_runs <= 0:
            return
        try:
            horizon = float(self._current_experiment().get("term_condition")
                            or 0.0)
        except (TypeError, ValueError, AttributeError):
            return
        if horizon <= 0:
            return
        from raptor.gui import cores
        batches = 1
        try:
            mode = str(self._current_experiment().get("importance", "none")
                       or "none")
            if mode not in ("", "none") and self._rbd is not None:
                from raptor.api import importance as _imp
                batches = _imp.batch_count(
                    len(_imp.block_rows(self._rbd)), mode)
        except (ImportError, KeyError, ValueError, TypeError):
            batches = 1
        # Which model it was timed on: another model's time says nothing.
        self._last_run_profile_rbd = id(self._rbd)
        self._last_run_profile = (horizon, int(done_runs),
                                  float(est.active_seconds()),
                                  int(cores.chosen()), int(batches))

    def _run_eta_text(self, done, total) -> str:
        """The estimate sentence for a run status message ("" while the
        average is not yet meaningful, and once the batch is finished)."""
        est = getattr(self, "_run_eta", None)
        if est is None:
            return ""
        return est.text(done, total) or ""

    def _run_status_text(self, prefix: str, done, total) -> str:
        """`prefix`, plus the estimate when there is one."""
        eta = self._run_eta_text(done, total)
        return f"{prefix} · {eta}" if eta else prefix

    def _clear_run_eta(self) -> None:
        self._run_eta = None

    def _track_paused_eta(self, paused: bool) -> None:
        """Paused time must not inflate the remaining estimate (a long
        pause would otherwise claim the batch is slower than it is)."""
        est = getattr(self, "_run_eta", None)
        if est is None:
            return
        was = bool(getattr(self, "_bg_last_paused", False))
        if paused and not was:
            est.pause()
        elif not paused and was:
            est.resume()
        self._bg_last_paused = paused

    def _update_importance_progress(self, done, total, label="", *,
                                    paused: bool = False) -> None:
        """Show importance progress on the run progress bar and in the bar.

        The same widgets the simulation uses: with Animate off (the default)
        there is no canvas activity at all, so without this a run that then
        spends minutes on variants looks hung.

        The bar is driven by the VARIANT fraction, ``(done - 1) /
        (total - 1)``: `done` counts batches finished with the baseline as
        the first, so the old ``done / total`` formula showed 1/N before a
        single variant had run and then jumped. It is deliberately capped
        at 99% here -- the last variant's callback fires before the metric
        rows are assembled, and showing 100% while work remained is exactly
        the "stuck at 100%" bug this replaces.

        Writes are deduplicated on ``(done, total, label, paused)`` and on
        the rendered sentence: the background poll calls this ten times a
        second, and rewriting an identical sentence made the status line
        flicker. If another writer replaced that sentence in the meantime,
        the same key reasserts it rather than leaving a stale
        "Processing results" up. An ETA rides the same message once two
        variants have completed.
        """
        total = max(int(total), 1)
        done = max(int(done), 1)
        variants_total = max(total - 1, 1)
        variants_done = min(max(done - 1, 0), variants_total)
        if variants_done <= 0:
            pct = 0
        else:
            # Ceiling, not floor: with more than 100 variants a floor
            # leaves the bar reading 0% after the first one has finished.
            pct = min(99, max(1, (100 * variants_done + variants_total - 1)
                              // variants_total))
        key = (done, total, str(label), bool(paused))
        # Timed from the first report: the estimate was dropped here before,
        # which left the slowest part of a run (one batch per block per
        # measure) with no time at all.
        imp = getattr(self, "_imp_eta", None)
        if imp is None:
            from raptor.gui.ui.run_eta import RunEstimate
            imp = RunEstimate()
            self._imp_eta = imp
        if paused:
            imp.pause()
        else:
            imp.resume()
        eta = None if paused else imp.text(variants_done, variants_total)
        if paused:
            what = (f"Importance measures paused — variant {variants_done} "
                    f"of {variants_total}")
        elif variants_done <= 0 and not label:
            what = (f"Importance measures running — variant 0 of "
                    f"{variants_total}")
        else:
            what = (f"Importance measures — variant {variants_done} of "
                    f"{variants_total}")
        detail = f" ({label})" if label else ""
        message = (f"{what}{detail} ({pct}%)"
                   + (f" · {eta}" if eta else ""))
        # Deduplicate the WRITE, not the state: another writer may have
        # replaced the status line between polls (the results stage does),
        # so the same key must reassert its own sentence rather than leave
        # a stale "Processing results" up for minutes.
        if (key == getattr(self, "_imp_last_key", None)
                and self._statusbar.currentMessage() == message):
            return
        self._imp_last_key = key
        self._run_progress.setVisible(True)
        self._run_progress.setValue(pct)
        self._run_progress_label.setVisible(True)
        self._run_progress_label.setText(f"{pct}%")
        self._clear_run_eta()      # the baseline batch is over
        self._statusbar.showMessage(message, 0)

    def _begin_importance_progress(self, total) -> None:
        """Baseline is on screen; make the wait for the variants explicit.

        Called once per run, on the GUI thread, by whichever run path
        presented the baseline results. `_imp_eta` exists from here on, so
        `_importance_for` can tell a baseline-first presentation from a
        direct call (tests, old callers) that still needs to start it.
        """
        self._imp_last_key = None
        self._imp_eta = None
        self._importance_running = True
        self._update_importance_progress(1, max(int(total or 1), 1), "")

    def _end_importance_progress(self, message: str) -> None:
        self._imp_eta = None
        self._imp_last_key = None
        self._importance_running = False
        self._run_progress.setVisible(False)
        self._run_progress_label.setVisible(False)
        self._statusbar.showMessage(message, 8000)

    def _apply_importance_progress(self, state) -> None:
        """The background worker's importance progress, applied on the GUI
        thread. Kept as its own method so the poll path is testable without
        standing up a whole worker run."""
        state = state or {}
        progress = state.get("importance_progress")
        paused = bool(state.get("paused"))
        if progress:
            self._update_importance_progress(*progress, paused=paused)
        elif state.get("baseline_ready"):
            # No variant finished yet (or the worker is paused before the
            # first one): show that importance is running, not the stale
            # baseline stage that preceded it.
            total = state.get("importance_total") or 1
            self._update_importance_progress(1, total, "", paused=paused)

    def _analyse_importance_off_thread(self, run_model, report_data, *,
                                       mode: str, batches: int):
        """Run `importance.analyse` on a worker; pump this thread until done.

        Importance is the longest stretch of a run (one batch per block per
        measure) and `analyse` is pure engine work -- there is no Qt in it.
        Running it on the GUI thread froze the Help viewer and the whole
        window for minutes on the synchronous path. The worker gets a
        private snapshot for the same reason the background batch does: the
        canvas may keep painting, and a user may inspect results, while it
        reads the model.

        The snapshot itself still costs one deep copy on the GUI thread.
        Say so and paint it before starting: a silent multi-second freeze
        here looked like the Help bug this method fixes.
        """
        import copy as _copy


        from raptor.api import importance as _imp

        self._statusbar.showMessage("Preparing importance measures…", 0)
        QApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        worker_model = _copy.deepcopy(run_model)
        # Back to the real phase message (the same key now reasserts).
        self._update_importance_progress(1, batches, "")
        # Read the setting HERE, on the GUI thread; the worker must not
        # touch the model/experiment.
        workers = max(1, int(
            (self._current_experiment() or {}).get("workers", 1) or 1))
        state: dict = {"done": False, "progress": None, "error": None,
                       "cancel": False}

        def stopped() -> bool:
            return bool(state["cancel"]
                        or getattr(self, "_sync_stop", False))

        def worker():
            try:
                state["data"] = _imp.analyse(
                    worker_model, report_data, mode=mode,
                    runs=len(report_data), workers=workers,
                    progress=lambda d, t, lab: state.__setitem__(
                        "progress", (d, t, lab)),
                    # Stop must stop this too. It is the same button and the
                    # same wait; without it an "aborted" run ground through
                    # every remaining batch with no way out.
                    stop_check=stopped)
            except Exception as exc:            # noqa: BLE001
                state["error"] = exc
            finally:
                state["done"] = True

        thread = threading.Thread(target=worker, daemon=True,
                                  name="raptor-importance")
        thread.start()
        loop = QEventLoop()
        timer = QTimer(self)
        timer.setInterval(50)

        def tick():
            try:
                progress = state.get("progress")
                if progress is not None:
                    self._update_importance_progress(*progress)
            except Exception as exc:            # noqa: BLE001
                # A failed repaint must not leave the nested loop running
                # forever: record it, cancel the worker, quit.
                state["ui_error"] = exc
                state["cancel"] = True
                loop.quit()
            finally:
                # Checked in a `finally`, so an update failure cannot skip
                # the only exit from `loop.exec()`.
                if state.get("done"):
                    loop.quit()

        timer.timeout.connect(tick)
        timer.start()
        try:
            if not state.get("done"):
                loop.exec()
        finally:
            state["cancel"] = True
            timer.stop()
            timer.deleteLater()
            thread.join(timeout=5.0)
        if state.get("ui_error") is not None:
            raise state["ui_error"]
        if state.get("error") is not None:
            raise state["error"]
        return state.get("data")

    def _importance_for(self, run_model, report_data):
        """Importance measures for a finished batch, or None.

        Called by BOTH run paths. The analysis itself always runs on a
        worker (see `_analyse_importance_off_thread`); this method owns the
        user-facing start/finish messages so neither path has to know how
        the wait is presented. Returns None when the experiment did not ask
        for it, and records a note rather than losing the batch if the
        analysis itself fails.
        """
        mode = str((self._current_experiment() or {}).get("importance",
                                                          "none"))
        if mode in ("none", "") or not report_data:
            return None
        from raptor.api import importance as _imp
        batches = _imp.batch_count(len(_imp.block_rows(run_model)), mode)
        if getattr(self, "_imp_eta", None) is None:
            self._begin_importance_progress(batches)
        try:
            data = self._analyse_importance_off_thread(
                run_model, report_data, mode=mode, batches=batches)
        except RuntimeError:
            self._end_importance_progress(
                "Importance measures cancelled — the simulation results are "
                "unaffected")
            return None
        except Exception as exc:            # noqa: BLE001
            self._end_importance_progress(f"Importance measures failed: {exc}")
            return None
        self._end_importance_progress(
            f"Importance measures complete — Results ▸ Final Results ▸ "
            f"Importance ({batches} batches)")
        return data

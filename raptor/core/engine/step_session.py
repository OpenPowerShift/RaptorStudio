"""Step-through simulation for the GUI: one run advanced an event at a time.
"""
from __future__ import annotations

from raptor.core.engine.orchestrator import _Cascade
from raptor.core.engine.driver import SingleRunDriver, build_engine_ctx
from raptor.core.fileio.rbd import RbdFile
from raptor.core.scheduler import Scheduler

class StepSession:
    """Interactive single-replication simulation (the GUI's step-through
    debugger). Construction performs the full kickoff without advancing the
    clock; `step()` processes one event at a time; `finish_run()` completes
    the run exactly like the batch path and returns
    `(event_log_lines, RunReportData | None)`.

    `num_runs` (default 1) lets the session go on to later replications with `jump_to_next_run_start()`; the
    random streams carry over from run to run exactly as in a batch.

    Not thread-safe, single-use: build a fresh session per debugging session."""

    def __init__(self, rbd_file: RbdFile, *, on_event=None, report_sink=None,
                 ao_sink=None, num_runs: int = 1):
        _, ctx = build_engine_ctx(rbd_file, num_runs=num_runs, on_event=on_event,
                                   report_sink=report_sink, ao_sink=ao_sink)
        self.ctx = ctx
        self.model = ctx.model
        self.rbd_file = rbd_file
        self.num_runs = num_runs
        self.run_index = 0
        #: One `(event_log_lines, RunReportData | None)` per run finished so far (the current run is not in it).
        self._cap_done: list[str] = []
        self.completed: list[tuple[list[str], object]] = []
        self._driver = SingleRunDriver(ctx, 0)
        self._driver.kickoff()

    @property
    def logs(self) -> list[str]:
        """The current (possibly partial) event-log lines."""
        return self._driver.logs

    @property
    def cap_lines(self) -> list[str]:
        """This run's capacity-plot rows (`time  flow  capacity`), only
        when the model has Capacity Analysis enabled."""
        cs = self._driver.capacity_state
        if cs is None:
            return []
        if not self._cap_done:
            return list(cs.cap_file_lines)
        return (self._cap_done + list(cs.cap_file_lines)
                + ["", "*" * 30 + f"End_of_Run_{self.run_index + 1}" + "*" * 30, ""])

    def _bank_capacity(self) -> None:
        """Keep a finished run's capacity rows the way the batch writes them (the header once, a separator after every run)."""
        cs = self._driver.capacity_state
        if cs is None:
            return
        if not self._cap_done:
            self._cap_done.append("SimulationTime        Flow       Capacity")
        self._cap_done.extend(cs.cap_file_lines)
        self._cap_done.extend(["", "*" * 30 + f"End_of_Run_{self.run_index + 1}" + "*" * 30, ""])

    def early_terminate(self):
        """Manual B-11 Early Terminate: finalize the run at the current sim
        time -- closing passes stamp End_sim/Done lines here and monitors are
        read now, so partial-run results are usable. Returns
        `(logs, RunReportData | None)`; the session is finished afterwards."""
        driver = self._driver
        if driver.finalized:
            return driver.logs, getattr(self, "_last_report", None)
        self._last_report = driver.finalize(
            terminate_at=driver.scheduler.time)
        return driver.logs, getattr(self, "_last_report", None)

    # Live-view accessors ---------------------------------------------------
    @property
    def scheduler(self) -> Scheduler:
        return self._driver.scheduler

    @property
    def cascade(self) -> _Cascade:
        return self._driver.cascade

    @property
    def blocks(self) -> list:
        return list(self._driver.all_blocks_list)

    @property
    def time(self) -> float:
        return self._driver.scheduler.time

    @property
    def done(self) -> bool:
        """No more events within the horizon (or the termination callback
        already fired)."""
        return self._driver.stop_requested or self._driver.finalized

    def step(self) -> bool:
        """Advance one calendar entry; False when the run has nothing left.
        Callers should follow a False with `finish_run()`."""
        # A finalized session is immutable.
        if self._driver.finalized:
            return False
        return self._driver.step_once()

    def step_to_change(self) -> bool:
        """Raptor 7's Step (`TimeAdvance`): run on until an instant in
        which something changed state has been analysed (`AnalyzeSystem`, then the pause), i.e. the
        point just before time moves on. `step()` is the finer, one-calendar-entry advance.
        False when the run has nothing left."""
        cascade = self.cascade
        scheduler = self._driver.scheduler
        checks = cascade.status_checks if self.time > 0.0 or cascade.status_checks else -1
        while True:
            # The pause is at the END of an instant that changed something, before time moves on.
            changed = cascade._system_state_changed or cascade.status_checks != checks
            nxt = scheduler.next_time()
            if changed and (nxt is None or nxt > scheduler.time):
                return True
            if not self.step():
                return False

    @property
    def has_next_run(self) -> bool:
        return self.run_index + 1 < self.num_runs

    def jump_to_next_run_start(self) -> bool:
        """Raptor 7's Jump: the rest of this run goes by without a
        pause, the next run is kicked off, and it stops at its first system change (`jumpStop`). The new
        run is built but not advanced: `step_to_change()` reaches that first stop, as it does for a fresh session. Returns False, with the run finished, when this was the last run."""
        self.finish_run()
        if not self.has_next_run:
            return False
        self._bank_capacity()
        self.completed.append((self._driver.logs, getattr(self, "_last_report", None)))
        self.run_index += 1
        self._last_report = None
        self._driver = SingleRunDriver(self.ctx, self.run_index)
        self._driver.kickoff()
        return True

    def finish_all(self):
        """Finish this run and every later one (Raptor 7's Run once stepping is switched off). Returns
        `(logs_per_run, reports)` for all runs, earlier jumped-over ones included."""
        self.finish_run()
        while self.jump_to_next_run_start():
            self.finish_run()
        runs = self.completed + [(self._driver.logs, getattr(self, "_last_report", None))]
        return [lg for lg, _ in runs], [r for _, r in runs]

    def finish_run(self):
        """Complete the run (any remaining events + closing passes) and
        produce `(logs, RunReportData | None)`. Idempotent-guarded."""
        driver = self._driver
        if not driver.finalized:
            if not driver.stop_requested:
                driver.advance_to_end()
            self._last_report = driver.finalize()
        return driver.logs, getattr(self, "_last_report", None)

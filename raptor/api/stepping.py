"""Step a model one state change (or one calendar entry) at a time and report what changed.

The command-line twin of the window's Step button: `StepSession.step_to_change` stops where Raptor 7's Step
stops; `granularity="entry"` is the finer one-calendar-entry advance. `run=N` jumps
over runs 1 to N-1 first (Raptor 7's Jump, `StepSession.jump_to_next_run_start`), so run N starts from the
random-stream positions a batch would give it.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from raptor.core.engine.step_session import StepSession
from raptor.core.fileio.rbd import RbdFile


class StepError(ValueError):
    pass


def _status(block) -> str:
    s: Any = getattr(block, "op_status", None)
    return str(s.value) if hasattr(s, "value") else str(s)


def _name(block) -> str:
    return getattr(block, "name", "") or f"Block {block.id}"


def _find(blocks, selector: str):
    sel = selector.strip()
    if sel.lstrip("-").isdigit():
        hits = [b for b in blocks if b.id == int(sel)]
    else:
        hits = [b for b in blocks if _name(b) == sel]
    if not hits:
        raise StepError(f"{selector!r} is not a block of this model")
    if len(hits) > 1:
        raise StepError(f"{selector!r} names {len(hits)} blocks; use the block number")
    return hits[0]


def _open_session(rbd: RbdFile, granularity: str, run: int) -> StepSession:
    if granularity not in ("change", "entry"):
        raise StepError("granularity is 'change' or 'entry'")
    if run < 1:
        raise StepError("the run number starts at 1")
    session = StepSession(rbd, num_runs=run)
    if run > session.model.num_runs:
        raise StepError(f"run {run} does not exist: the model has {session.model.num_runs} run(s)")
    for _ in range(run - 1):
        session.jump_to_next_run_start()
    return session


class _Recorder:
    """Builds one record per stop: what moved since the previous one."""

    def __init__(self, session: StepSession, run: int) -> None:
        self.session, self.run = session, run
        self.blocks = session.blocks
        self.prev = {b.id: _status(b) for b in self.blocks}
        self.seen_log = 0

    def record(self, step: int, done: bool) -> dict:
        changes = []
        for b in self.blocks:
            now = _status(b)
            if now != self.prev[b.id]:
                changes.append({"id": b.id, "name": _name(b), "from": self.prev[b.id], "to": now})
                self.prev[b.id] = now
        logs = self.session.logs
        new, self.seen_log = list(logs[self.seen_log:]), len(logs)
        st = self.session.cascade.system_state
        out = {"run": self.run, "step": step, "time": self.session.time, "system": "up" if st.system_up else "down",
               "color": st.color.value, "failed": st.num_currently_failed, "log": new, "changes": changes}
        if done:
            out["done"] = True
        return out


def trace(rbd: RbdFile, *, until: float | None = None, events: int | None = None,
          break_on: str | None = None, granularity: str = "change", run: int = 1) -> Iterator[dict]:
    """Yield one record per stop: `{"run", "step", "time", "system", "failed", "log", "changes"}`.

    `changes` lists `{"id", "name", "from", "to"}` for every block whose status moved since the previous stop;
    `log` holds the event-log lines written since then. Stops after `events` records, at the first stop at or past
    `until`, when the block `break_on` changes status, or when the run has nothing left (then one final record
    with `"done": true` follows the closing passes)."""
    session = _open_session(rbd, granularity, run)
    recorder = _Recorder(session, run)
    watched = _find(recorder.blocks, break_on) if break_on else None
    advance = session.step_to_change if granularity == "change" else session.step
    n = 0
    while events is None or n < events:
        more = advance()
        n += 1
        if not more:
            session.finish_run()
            yield recorder.record(n, True)
            return
        record = recorder.record(n, False)
        yield record
        if until is not None and record["time"] >= until:
            return
        if watched is not None and any(c["id"] == watched.id for c in record["changes"]):
            return

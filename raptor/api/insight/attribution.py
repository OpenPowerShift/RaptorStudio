"""Downtime attribution: which element caused each system outage, and what
kind of waiting it was.

The engine writes one event-log line per state change. This module replays a
run's lines and integrates time. It reads; it never writes to the log, the
model or a random stream, so switching it on cannot change a result.

Definitions (also in docs/formulas.adoc):

* An **outage** starts when the system line turns Red from another colour
  and ends when it leaves Red (or the run ends).
* Its **initiator** is the first element that went down at the instant the
  outage started: a failure if there is one, else a preventive maintenance,
  else whatever went down first.
* **DTCI** of an element = system Red time in outages it initiated, over all
  Red time. **DECI** = outages it initiated over all outages. **FCI** = the
  same counting only outages started by a failure.
* The **cause** of each instant of an outage is the state of the element
  holding the system down at that instant: the initiator while it is still
  down, otherwise the element that most recently went down.

Block states come from the line's action, sub-divided by its comment:

    Repairing            -> repair          RepHold / Waiting_for_Spare    -> wait_spare
    RepHold / LDT        -> logistics       RepHold / Waiting_for_resource -> wait_resource
    RepHold / postLDT    -> logistics       PM -> pm         pmHold -> pm_wait
    Idle                 -> dependency      Done -> done     Failed -> (instant; next line decides)
    Good -> up           Standby -> standby Cut / Linked -> out of the diagram for the phase
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

CAUSES = ("repair", "wait_spare", "wait_resource", "logistics", "pm", "pm_wait",
          "dependency", "done", "other")
CAUSE_LABELS = {
    "repair": "Hands-on repair", "wait_spare": "Waiting for a spare",
    "wait_resource": "Waiting for people or equipment", "logistics": "Logistics delay",
    "pm": "Planned maintenance", "pm_wait": "Waiting to start maintenance",
    "dependency": "Held by another element", "done": "Not repairable", "other": "Other",
}
_NOT_DOWN = ("up", "standby", "bypass")
#: The longest outage sequence kept for the outage-anatomy view.
MAX_STEPS = 6
#: Outage durations and sequences kept per trial.
MAX_OUTAGES_KEPT = 2000

Key = tuple[str, int]
SYSTEM_KEY: Key = ("system", 0)


_REP_HOLD = {"Waiting_for_Spare": "wait_spare", "LDT": "logistics", "postLDT": "logistics"}
_SIMPLE = {"Good": "up", "Standby": "standby", "Repairing": "repair", "PM": "pm",
           "pmHold": "pm_wait", "Idle": "dependency", "Cut": "bypass", "Linked": "bypass",
           # for an instant: the next line says what kind of wait it is
           "Failed": "other"}


def block_state(action: str, comment: str, previous: str) -> str | None:
    """The state a Block line puts its block in, or None when the line is not
    a state change (a spare arriving, a PM falling due...)."""
    if action in _SIMPLE:
        return _SIMPLE[action]
    if action == "RepHold":
        if comment.startswith("Waiting_for_resource"):
            return "wait_resource"
        return _REP_HOLD.get(comment, previous if previous in CAUSES else "other")
    if action == "Done":
        return previous if comment == "End_sim" else "done"
    return None


def event_state(action: str) -> str | None:
    return {"Failed": "done", "Success": "up", "Armed": "up", "Cut": "bypass"}.get(action)


@dataclass
class Initiated:
    outages: int = 0
    failures: int = 0
    red_time: float = 0.0


@dataclass
class Outage:
    start: float
    end: float
    initiator: Key
    by_failure: bool
    steps: tuple[tuple[Key, str], ...]

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class TrialAttribution:
    """One run's attribution: plain, picklable data."""
    span: float = 0.0                                   # time statistics were collected
    red_time: float = 0.0
    outages: int = 0
    failure_outages: int = 0
    initiated: dict[Key, Initiated] = field(default_factory=dict)
    holding: dict[Key, float] = field(default_factory=dict)       # red time while holding the system down
    cause_time: dict[str, float] = field(default_factory=dict)    # red time by cause
    initiator_cause: dict[Key, dict[str, float]] = field(default_factory=dict)
    own_time: dict[Key, dict[str, float]] = field(default_factory=dict)
    names: dict[Key, str] = field(default_factory=dict)
    kept: list[Outage] = field(default_factory=list)


class _Replay:
    """The state kept while one run's lines are replayed."""

    def __init__(self, start_stats: float) -> None:
        self.out = TrialAttribution()
        self.start_stats = start_stats
        self.state: dict[Key, str] = {}
        self.order: dict[Key, int] = {}      # when each element last went down (a counter)
        self.clock = 0
        self.now = 0.0
        self.red = False
        # the timestamp group being read: who went down at `group_time`, and how
        self.group_time = -math.inf
        self.group_down: list[tuple[Key, str]] = []
        self.current: Outage | None = None
        self.steps: list[tuple[Key, str]] = []

    def holder(self) -> tuple[Key, str]:
        """The element holding the system down now, and its state."""
        current = self.current
        if current is not None and self.state.get(current.initiator, "up") not in _NOT_DOWN:
            return current.initiator, self.state[current.initiator]
        down = [(self.order.get(k, 0), k) for k, s in self.state.items() if s not in _NOT_DOWN]
        if not down:
            return SYSTEM_KEY, "other"
        key = max(down)[1]
        return key, self.state[key]

    def advance(self, to: float) -> None:
        out = self.out
        dt = max(to, self.start_stats) - max(self.now, self.start_stats)
        if dt > 0:
            out.span += dt
            for key, s in self.state.items():
                bucket = out.own_time.setdefault(key, {})
                bucket[s] = bucket.get(s, 0.0) + dt
            if self.red and self.current is not None:
                key, cause = self.holder()
                cause = cause if cause in CAUSES else "other"
                initiator = self.current.initiator
                out.red_time += dt
                out.initiated.setdefault(initiator, Initiated()).red_time += dt
                out.holding[key] = out.holding.get(key, 0.0) + dt
                out.cause_time[cause] = out.cause_time.get(cause, 0.0) + dt
                per = out.initiator_cause.setdefault(initiator, {})
                per[cause] = per.get(cause, 0.0) + dt
        self.now = max(self.now, to)

    def note_step(self) -> None:
        step = self.holder()
        if step[0] == SYSTEM_KEY and self.steps:
            return            # the last element just came back; the outage is about to end
        if (not self.steps or self.steps[-1] != step) and len(self.steps) < MAX_STEPS:
            self.steps.append(step)

    def close(self, at: float) -> None:
        current = self.current
        if current is None:
            return
        current.end = at
        current.steps = tuple(self.steps)
        if (current.end > max(current.start, self.start_stats)
                and len(self.out.kept) < MAX_OUTAGES_KEPT):
            self.out.kept.append(current)
        self.current, self.red, self.steps = None, False, []

    def element_line(self, line: Any, is_block: bool) -> None:
        element = getattr(line, "element", None)
        if element is None:
            return
        key: Key = (element[0], int(element[1]))
        self.out.names.setdefault(key, line.name)
        previous = self.state.get(key, "up")
        new = (block_state(line.action, line.comment, previous) if is_block
               else event_state(line.action))
        if new is None or line.comment == "End_sim":
            return
        was_down = previous not in _NOT_DOWN
        self.state[key] = new
        if new not in _NOT_DOWN:
            if not was_down:
                self.clock += 1
                self.order[key] = self.clock
            failed = line.action == "Failed"
            if not was_down or failed:
                how = "failed" if failed else "pm" if new in ("pm", "pm_wait") else "other"
                self.group_down.append((key, how))
        if self.red:
            self.note_step()

    def system_line(self, line: Any) -> None:
        t = float(line.time)
        if line.name == "Simulation_Terminate":
            self.close(t)
        elif line.name != "State_change":
            return
        elif line.action == "Red" and not self.red:
            self.start_outage(t)
        elif line.action != "Red" and self.red:
            self.close(t)

    def start_outage(self, t: float) -> None:
        failed = [k for k, how in self.group_down if how == "failed"]
        pm = [k for k, how in self.group_down if how == "pm"]
        first = failed or pm or [k for k, _ in self.group_down]
        initiator = first[0] if first else self.holder()[0]
        self.red = True
        self.current = Outage(t, t, initiator, bool(failed), ())
        self.steps = []
        self.note_step()
        if t >= self.start_stats:
            out = self.out
            out.outages += 1
            out.failure_outages += bool(failed)
            rec = out.initiated.setdefault(initiator, Initiated())
            rec.outages += 1
            rec.failures += bool(failed)


def attribute_trial(lines: Iterable[Any], start_stats: float = 0.0) -> TrialAttribution:
    """Replay one run's event-log lines (`LogLine`s; plain strings are skipped)."""
    replay = _Replay(start_stats)
    for line in lines:
        kind = getattr(line, "type", None)
        if kind is None:
            continue
        t = float(line.time)
        replay.advance(t)
        if t != replay.group_time:
            replay.group_time, replay.group_down = t, []
        if kind in ("Block", "Event"):
            replay.element_line(line, kind == "Block")
        elif kind == "System":
            replay.system_line(line)
    replay.close(replay.now)
    return replay.out


# ---- across runs -------------------------------------------------------------

def _stat(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"mean": None, "sem": None, "min": None, "max": None, "n": 0}
    mean = sum(values) / n
    sem = (math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1) / n) if n > 1 else None)
    return {"mean": mean, "sem": sem, "min": min(values), "max": max(values), "n": n}


def _label(key: Key, names: dict[Key, str]) -> str:
    if key == SYSTEM_KEY:
        return "(no single element)"
    return names.get(key, f"{key[0]} {key[1]}")


def _percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    i = min(len(sorted_values) - 1, max(0, math.ceil(p * len(sorted_values)) - 1))
    return sorted_values[i]


def _element_row(key: Key, trials: list[TrialAttribution], names: dict[Key, str]) -> dict[str, Any]:
    n = len(trials)
    dtci, deci, fci, red, started, held, own_down = [], [], [], [], [], [], []
    causes: dict[str, list[float]] = {c: [] for c in CAUSES}
    for trial in trials:
        rec = trial.initiated.get(key, Initiated())
        red.append(rec.red_time)
        started.append(float(rec.outages))
        held.append(trial.holding.get(key, 0.0))
        if trial.red_time > 0:
            dtci.append(rec.red_time / trial.red_time)
        if trial.outages:
            deci.append(rec.outages / trial.outages)
        if trial.failure_outages:
            fci.append(rec.failures / trial.failure_outages)
        mine = trial.initiator_cause.get(key, {})
        for cause in CAUSES:
            causes[cause].append(mine.get(cause, 0.0))
        if trial.span > 0:
            own = trial.own_time.get(key, {})
            own_down.append(sum(v for s, v in own.items() if s not in _NOT_DOWN) / trial.span)
    cause_means = {c: sum(v) / n for c, v in causes.items() if n and sum(v) > 0}
    total_cause = sum(cause_means.values())
    return {
        "kind": key[0], "id": key[1], "name": _label(key, names),
        "dtci": _stat(dtci), "deci": _stat(deci), "fci": _stat(fci),
        "red_time": _stat(red), "outages": _stat(started), "holding": _stat(held),
        "own_unavailability": _stat(own_down),
        "causes": {c: v / total_cause for c, v in cause_means.items()} if total_cause else {},
        "main_cause": max(cause_means, key=lambda c: cause_means[c]) if cause_means else None,
    }


def _sequences(trials: list[TrialAttribution], names: dict[Key, str]) -> list[dict[str, Any]]:
    found: dict[tuple, list[float]] = {}
    for trial in trials:
        for outage in trial.kept:
            path = tuple((_label(k, names), cause) for k, cause in outage.steps)
            entry = found.setdefault(path, [0.0, 0.0])
            entry[0] += outage.duration
            entry[1] += 1
    total = sum(v[0] for v in found.values())
    return [{"steps": [{"element": e, "cause": c} for e, c in path],
             "share": v[0] / total if total else 0.0, "outages": int(v[1]), "time": v[0]}
            for path, v in sorted(found.items(), key=lambda kv: -kv[1][0])[:50]]


def aggregate(trials: list[TrialAttribution]) -> dict[str, Any]:
    """Statistics over runs, JSON-safe. One sample per run for every measure;
    a run with no outage gives no sample to the shares (DTCI, DECI, FCI)."""
    names: dict[Key, str] = {}
    for trial in trials:
        names.update(trial.names)
    keys = sorted({k for trial in trials for k in trial.initiated} |
                  {k for trial in trials for k in trial.holding})
    elements = [_element_row(key, trials, names) for key in keys]
    elements.sort(key=lambda e: (-(e["red_time"]["mean"] or 0.0), e["name"]))
    cause_totals = {c: sum(t.cause_time.get(c, 0.0) for t in trials) for c in CAUSES}
    all_red = sum(cause_totals.values())
    durations = sorted(o.duration for t in trials for o in t.kept)
    return {
        "schema": "raptor-attribution/1",
        "runs": len(trials),
        "runs_with_outage": sum(1 for t in trials if t.outages),
        "system": {"red_time": _stat([t.red_time for t in trials]),
                   "outages": _stat([float(t.outages) for t in trials]),
                   "failure_outages": _stat([float(t.failure_outages) for t in trials]),
                   "span": _stat([t.span for t in trials])},
        "elements": elements,
        "causes": {c: v / all_red for c, v in cause_totals.items() if all_red and v > 0},
        "outage_durations": {"count": len(durations), "p50": _percentile(durations, 0.5),
                             "p90": _percentile(durations, 0.9),
                             "max": durations[-1] if durations else None},
        "sequences": _sequences(trials, names),
    }


def attribute_logs(logs: Iterable[Iterable[Any]], start_stats: float = 0.0) -> dict[str, Any]:
    """Attribution for a whole batch from its per-run event logs."""
    return aggregate([attribute_trial(lines, start_stats) for lines in logs])

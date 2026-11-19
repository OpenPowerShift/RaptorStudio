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

from raptor.api.insight.capacity_attribution import (
    CapacityRun, CapacityTracker, aggregate_capacity,
)

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
#: States of a second element, out during another's outage, that say why it
#: could not cover: held by a dependency, or waiting for a spare or for crew.
ALONGSIDE = ("dependency", "wait_spare", "wait_resource")
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
    #: steps past MAX_STEPS, not listed
    more_steps: int = 0
    #: still going when the run ended: its duration is a lower bound
    open_at_end: bool = False

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
    #: red time of outages each element started, while another element was
    #: also out held by a dependency or waiting: {initiator: {(other, cause): t}}
    alongside: dict[Key, dict[tuple[Key, str], float]] = field(default_factory=dict)
    names: dict[Key, str] = field(default_factory=dict)
    kept: list[Outage] = field(default_factory=list)
    #: every system-down interval as a flat [start, end, start, end...] list,
    #: clipped to the time statistics were collected; and that time's bounds
    red: list[float] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0
    #: Lost-capacity attribution, when the run recorded a capacity sink.
    capacity: Any = None
    #: Phased models: time spent in each phase, the system's down time in
    #: it, and that down time by the element that started each outage.
    phase_time: dict[str, float] = field(default_factory=dict)
    phase_red: dict[str, float] = field(default_factory=dict)
    phase_initiated: dict[str, dict[Key, float]] = field(default_factory=dict)


class _Replay:
    """The state kept while one run's lines are replayed."""

    def __init__(self, start_stats: float,
                 capacity: CapacityRun | None = None,
                 first_phase: str | None = None) -> None:
        self.out = TrialAttribution()
        #: the phase now running; phase 1 starts at time 0 without a log line
        self.phase = first_phase
        self.start_stats = start_stats
        self.capacity = (CapacityTracker(capacity, start_stats)
                         if capacity is not None else None)
        self.state: dict[Key, str] = {}
        self.since: dict[Key, float] = {}    # when each element entered its current state
        self.down: dict[Key, int] = {}       # elements down now -> when they went down (a counter)
        self.clock = 0
        self.now = 0.0
        self.red = False
        # the timestamp group being read: who went down at `group_time`, and how
        self.group_time = -math.inf
        self.group_down: list[tuple[Key, str]] = []
        self.current: Outage | None = None
        self.steps: list[tuple[Key, str]] = []
        #: steps past MAX_STEPS in the outage being read
        self.more_steps = 0

    def holder(self) -> tuple[Key, str]:
        """The element holding the system down now, and its state."""
        current = self.current
        if current is not None and self.state.get(current.initiator, "up") not in _NOT_DOWN:
            return current.initiator, self.state[current.initiator]
        if not self.down:
            return SYSTEM_KEY, "other"
        key = max(self.down, key=lambda k: (self.down[k], k))
        return key, self.state[key]

    def advance(self, to: float) -> None:
        out = self.out
        if self.capacity is not None:
            self.capacity.advance(to, self.down, self.state)
        dt = max(to, self.start_stats) - max(self.now, self.start_stats)
        if dt > 0:
            out.span += dt
            phase = self.phase
            if phase is not None:
                out.phase_time[phase] = out.phase_time.get(phase, 0.0) + dt
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
                self.note_alongside(initiator, dt)
                if phase is not None:
                    out.phase_red[phase] = out.phase_red.get(phase, 0.0) + dt
                    by = out.phase_initiated.setdefault(phase, {})
                    by[initiator] = by.get(initiator, 0.0) + dt
        if self.red and self.current is not None and to > self.now:
            self.note_step()
        self.now = max(self.now, to)

    def note_alongside(self, initiator: Key, dt: float) -> None:
        """Another element out at the same time because it is held or
        waiting: a backup that cannot cover (`ALONGSIDE`)."""
        for key in self.down:
            cause = self.state.get(key, "")
            if key != initiator and cause in ALONGSIDE:
                by = self.out.alongside.setdefault(initiator, {})
                by[(key, cause)] = by.get((key, cause), 0.0) + dt

    def book_own_time(self, key: Key, until: float) -> None:
        """Credit `key`'s time in its current state up to `until`. Done only
        when an element changes state (and once at the end), so the replay
        costs one step per log line whatever the size of the model."""
        start = max(self.since.get(key, 0.0), self.start_stats)
        dt = until - start
        if dt > 0:
            bucket = self.out.own_time.setdefault(key, {})
            state = self.state[key]
            bucket[state] = bucket.get(state, 0.0) + dt
        self.since[key] = until

    def finish(self) -> None:
        self.close(self.now)
        if self.capacity is not None:
            self.capacity.finish(self.now, self.down, self.state)
            self.out.capacity = self.capacity.out
        self.out.start = min(self.start_stats, self.now)
        self.out.end = self.now
        for key in self.state:
            self.book_own_time(key, self.now)

    def note_step(self) -> None:
        """Record who holds the system down over the span being advanced.
        Called only when time passes: a block's instant "Failed" line
        before its "Repairing" line is not a step ("Junk (other)")."""
        key, cause = self.holder()
        step = (key, cause if cause in CAUSES else "other")
        if step[0] == SYSTEM_KEY and self.steps:
            return            # the last element just came back; the outage is about to end
        if self.steps and self.steps[-1] == step:
            return
        if len(self.steps) < MAX_STEPS:
            self.steps.append(step)
        else:
            self.more_steps += 1

    def close(self, at: float) -> None:
        current = self.current
        if current is None:
            return
        current.end = at
        current.steps = tuple(self.steps)
        current.more_steps = self.more_steps
        lo = max(current.start, self.start_stats)
        if at > lo:
            self.out.red += [lo, at]
        if (current.end > max(current.start, self.start_stats)
                and len(self.out.kept) < MAX_OUTAGES_KEPT):
            self.out.kept.append(current)
        self.current, self.red, self.steps = None, False, []
        self.more_steps = 0

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
        if key in self.state:
            self.book_own_time(key, float(line.time))
        else:
            self.since[key] = 0.0
            self.state[key] = "up"               # up from the start until this first line
            self.book_own_time(key, float(line.time))
        self.state[key] = new
        if new in _NOT_DOWN:
            self.down.pop(key, None)
        else:
            if not was_down:
                self.clock += 1
                self.down[key] = self.clock
            failed = line.action == "Failed"
            if not was_down or failed:
                how = "failed" if failed else "pm" if new in ("pm", "pm_wait") else "other"
                self.group_down.append((key, how))

    def system_line(self, line: Any) -> None:
        t = float(line.time)
        if line.name == "Simulation_Terminate":
            if self.current is not None:
                self.current.open_at_end = True
            self.close(t)
        elif line.name == "Phase_change":
            comment = str(line.comment)
            if comment.startswith(PHASE_PREFIX):
                self.phase = comment[len(PHASE_PREFIX):]
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
        self.more_steps = 0
        if t >= self.start_stats:
            out = self.out
            out.outages += 1
            out.failure_outages += bool(failed)
            rec = out.initiated.setdefault(initiator, Initiated())
            rec.outages += 1
            rec.failures += bool(failed)


#: The comment of the event-log line that starts a phase: `Begin_phase_<name>`.
PHASE_PREFIX = "Begin_phase_"
#: The name used for phase 1 when the model's phase list is not given: the
#: engine starts phase 1 at time 0 without logging it.
FIRST_PHASE = "(first phase)"


def model_phases(rbd: Any) -> list[dict[str, Any]] | None:
    """The model's phases in order, `[{name, mission}]`, or None when it is
    not phased.  Pass it to `attribute_logs` so phase 1 has its name."""
    section = getattr(rbd, "sections", {}).get("PHASES") if rbd is not None else None
    rows = getattr(section, "rows", None) or []
    out = [{"name": str(row.get("PhaseName") or f"Phase {i}"),
            "mission": str(row.get("Mission", "1")).strip() not in ("0", "FALSE", "False")}
           for i, row in enumerate(rows, start=1)]
    return out or None


def model_hierarchies(rbd: Any) -> dict[str, Any] | None:
    """The model's hierarchies, for the roll-up in `aggregate`: ``{names:
    {hid: name}, parent: {hid: parent hid}, of: {(kind, id): parent hid}}``,
    or None when the model has no hierarchy."""
    sections = getattr(rbd, "sections", {}) if rbd is not None else {}

    def rows(name: str) -> list[dict]:
        section = sections.get(name)
        return [r for r in (getattr(section, "rows", None) or [])
                if str(r.get("ID", "0")) != "0"]

    def number(value: Any) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    hiers = rows("HIERARCHY_DATA")
    if not hiers:
        return None
    of: dict[Key, int] = {}
    for kind, section, column in (("block", "BLOCK_DATA", "ParentId"),
                                  ("event", "EVENT_DATA", "ParentId")):
        for row in rows(section):
            parent = number(row.get(column))
            if parent:
                of[(kind, number(row.get("ID")))] = parent
    return {"names": {number(r.get("ID")): str(r.get("hierarchyName") or r.get("Name")
                                                  or f"Hierarchy {r.get('ID')}")
                      for r in hiers},
            "parent": {number(r.get("ID")): number(r.get("Parent")) for r in hiers},
            "of": of}


def _hierarchy_rows(trials: list[TrialAttribution],
                    hierarchies: dict[str, Any]) -> list[dict[str, Any]]:
    """Each hierarchy's share of system downtime: the Red time of the
    outages started by anything inside it (at any depth), pooled over the
    runs like an element's DTCI, so its interval is honest (the inside
    elements' errors are correlated; summing their intervals would not be)."""
    names, parent, of = hierarchies["names"], hierarchies["parent"], hierarchies["of"]

    def chain(hid: int) -> list[int]:
        out: list[int] = []
        while hid and hid not in out and len(out) < 16:
            out.append(hid)
            hid = parent.get(hid, 0)
        return out

    inside: dict[int, set[Key]] = {hid: set() for hid in names}
    for key, hid in of.items():
        for ancestor in chain(hid):
            inside.setdefault(ancestor, set()).add(key)
    den = [t.red_time for t in trials]
    n = len(trials)
    rows = []
    for hid, keys in inside.items():
        if not keys:
            continue
        red = [sum(t.initiated[k].red_time for k in keys if k in t.initiated) for t in trials]
        if not any(red):
            continue
        causes = {c: sum(t.initiator_cause.get(k, {}).get(c, 0.0)
                         for t in trials for k in keys) / n for c in CAUSES}
        causes = {c: v for c, v in causes.items() if v > 0}
        total = sum(causes.values())
        started = {k for t in trials for k in keys if k in t.initiated}
        rows.append({
            "kind": "hier", "id": hid, "name": names.get(hid, f"Hierarchy {hid}"),
            "depth": len(chain(hid)), "parent": parent.get(hid, 0),
            "elements": len(started),
            "dtci": _ratio_stat(red, den), "red_time": _stat(red),
            "causes": {c: v / total for c, v in causes.items()} if total else {},
            "main_cause": max(causes, key=lambda c: causes[c]) if causes else None,
        })
    rows.sort(key=lambda r: (-(r["dtci"]["mean"] or 0.0), r["name"]))
    return rows


def attribute_trial(lines: Iterable[Any], start_stats: float = 0.0,
                    capacity: CapacityRun | None = None,
                    phases: list[dict[str, Any]] | None = None) -> TrialAttribution:
    """Replay one run's event-log lines (`LogLine`s; plain strings are skipped).

    `capacity` joins a capacity-analysis run's flow track so the lost flow is
    charged to the elements that were down (`raptor.api.insight.capacity_attribution`).
    `phases` (`model_phases`) names phase 1, which the log does not announce;
    without it a phased run books phase 1's time as FIRST_PHASE.
    """
    if phases is None and not isinstance(lines, list):
        lines = list(lines)           # scanned twice: once for a phase line
    first = phases[0]["name"] if phases else (
        FIRST_PHASE if any(getattr(line, "name", None) == "Phase_change"
                           and str(getattr(line, "comment", "")).startswith(PHASE_PREFIX)
                           for line in lines) else None)
    replay = _Replay(start_stats, capacity, first)
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
    replay.finish()
    return replay.out


# ---- across runs -------------------------------------------------------------

def _stat(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"mean": None, "sem": None, "min": None, "max": None, "n": 0}
    mean = sum(values) / n
    sem = (math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1) / n) if n > 1 else None)
    return {"mean": mean, "sem": sem, "min": min(values), "max": max(values), "n": n}


def _ratio_stat(num: list[float], den: list[float]) -> dict[str, Any]:
    """A share pooled over the runs: sum(num) / sum(den), the measure's own
    formula ("its outages' Red time / all Red time").  Averaging each run's
    own share instead weighs a run with a minute of downtime like one with
    a day, and disagrees with the time-weighted views beside it.

    `sem` is the delta-method standard error of a ratio estimator; `min` and
    `max` stay the per-run shares; `n` counts the runs with a denominator."""
    pairs = [(y, x) for y, x in zip(num, den, strict=True) if x > 0]
    n = len(pairs)
    total = sum(x for _, x in pairs)
    if n == 0 or total <= 0:
        return {"mean": None, "sem": None, "min": None, "max": None, "n": 0}
    ratio = sum(y for y, _ in pairs) / total
    sem = None
    if n > 1:
        xbar = total / n
        resid = [y - ratio * x for y, x in pairs]
        var = sum(r * r for r in resid) / (n - 1)
        sem = math.sqrt(var / n) / xbar
    shares = [y / x for y, x in pairs]
    return {"mean": ratio, "sem": sem, "min": min(shares), "max": max(shares), "n": n}


def distinct_names(names: dict[Key, str]) -> dict[Key, str]:
    """Names a reader can tell apart: an element whose name is empty,
    "unnamed" or shared with another gets its kind and id added
    ("unnamed (block 3)"). Three lines of "unnamed: 66.5 %" in a report
    left a manager no way to tell which unit to fix."""
    from collections import Counter
    counts = Counter(name.strip().lower() for name in names.values())
    out = {}
    for key, name in names.items():
        plain = name.strip()
        if not plain:
            out[key] = f"{key[0]} {key[1]}"
        elif plain.lower() == "unnamed" or counts[plain.lower()] > 1:
            out[key] = f"{plain} ({key[0]} {key[1]})"
        else:
            out[key] = name
    return out


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
    red, started, held, own_down = [], [], [], []
    failures: list[float] = []
    causes: dict[str, list[float]] = {c: [] for c in CAUSES}
    for trial in trials:
        rec = trial.initiated.get(key, Initiated())
        red.append(rec.red_time)
        started.append(float(rec.outages))
        failures.append(float(rec.failures))
        held.append(trial.holding.get(key, 0.0))
        mine = trial.initiator_cause.get(key, {})
        for cause in CAUSES:
            causes[cause].append(mine.get(cause, 0.0))
        if trial.span > 0:
            own = trial.own_time.get(key, {})
            own_down.append(sum(v for s, v in own.items() if s not in _NOT_DOWN) / trial.span)
    cause_means = {c: sum(v) / n for c, v in causes.items() if n and sum(v) > 0}
    total_cause = sum(cause_means.values())
    together: dict[tuple[Key, str], float] = {}
    for trial in trials:
        for pair, t in trial.alongside.get(key, {}).items():
            together[pair] = together.get(pair, 0.0) + t
    own_red = sum(red)
    alongside = [{"element": _label(k, names), "kind": k[0], "id": k[1], "cause": c,
                  "share": t / own_red}
                 for (k, c), t in sorted(together.items(), key=lambda kv: -kv[1])[:3]
                 if own_red > 0]
    return {
        "alongside": alongside,
        "kind": key[0], "id": key[1], "name": _label(key, names),
        "dtci": _ratio_stat(red, [t.red_time for t in trials]),
        "deci": _ratio_stat(started, [float(t.outages) for t in trials]),
        "fci": _ratio_stat(failures, [float(t.failure_outages) for t in trials]),
        "red_time": _stat(red), "outages": _stat(started), "holding": _stat(held),
        "own_unavailability": _stat(own_down),
        "causes": {c: v / total_cause for c, v in cause_means.items()} if total_cause else {},
        "main_cause": max(cause_means, key=lambda c: cause_means[c]) if cause_means else None,
    }


def _sequences(trials: list[TrialAttribution], names: dict[Key, str]) -> list[dict[str, Any]]:
    found: dict[tuple, list[float]] = {}
    for trial in trials:
        for outage in trial.kept:
            path = (tuple((_label(k, names), cause) for k, cause in outage.steps)
                    + ((("", "more"),) if outage.more_steps else ()))
            entry = found.setdefault(path, [0.0, 0.0, 0.0])
            entry[0] += outage.duration
            entry[1] += 1
            entry[2] += outage.more_steps
    total = sum(v[0] for v in found.values())
    out = []
    for path, v in sorted(found.items(), key=lambda kv: -kv[1][0])[:50]:
        more = bool(path) and path[-1] == ("", "more")
        row: dict[str, Any] = {
            "steps": [{"element": e, "cause": c} for e, c in (path[:-1] if more else path)],
            "share": v[0] / total if total else 0.0, "outages": int(v[1]), "time": v[0]}
        if more:
            # the chain went on: the average count of steps not listed
            row["more_steps"] = v[2] / v[1]
        out.append(row)
    return out


def _duration_hist(durations: list[float], bins: int = 12) -> list[dict[str, float]]:
    """Log-spaced histogram of outage durations (C6's data).

    Durations span orders of magnitude, so equal-width bins would put every
    short outage in the first bar.  Empty input returns no bins.
    """
    import math

    positive = sorted(d for d in durations if d > 0)
    if not positive:
        return []
    low, high = positive[0], positive[-1]
    if high <= low:
        return [{"lo": low, "hi": high, "count": float(len(positive))}]
    edges = [low * (high / low) ** (i / bins) for i in range(bins + 1)]
    counts = [0.0] * bins
    for value in positive:
        index = min(bins - 1, max(0, int(
            bins * math.log(value / low) / math.log(high / low))))
        counts[index] += 1.0
    return [{"lo": edges[i], "hi": edges[i + 1], "count": counts[i]}
            for i in range(bins)]


def aggregate(trials: list[TrialAttribution],
              phases: list[dict[str, Any]] | None = None,
              hierarchies: dict[str, Any] | None = None) -> dict[str, Any]:
    """Statistics over runs, JSON-safe. One sample per run for every measure;
    a run with no outage gives no sample to the shares (DTCI, DECI, FCI).
    `hierarchies` (`model_hierarchies(rbd)`) adds `hierarchies`: each
    hierarchy's share of downtime, rolled up from everything inside it."""
    names: dict[Key, str] = {}
    for trial in trials:
        names.update(trial.names)
    names = distinct_names(names)
    capacity = aggregate_capacity(trials, names)
    # An element can lose capacity without starting a system outage (a
    # parallel unit still within the demand): keep it in the table, where its
    # lost-capacity columns are the only nonzero ones.
    capacity_keys = ({(row["kind"], row["id"]) for row in capacity["elements"]}
                     if capacity else set())
    keys = sorted({k for trial in trials for k in trial.initiated} |
                  {k for trial in trials for k in trial.holding} |
                  capacity_keys)
    elements = [_element_row(key, trials, names) for key in keys]
    elements.sort(key=lambda e: (-(e["red_time"]["mean"] or 0.0), e["name"]))
    if capacity is not None:
        by_key = {(row["kind"], row["id"]): row for row in capacity["elements"]}
        for element in elements:
            row = by_key.get((element["kind"], element["id"]))
            if row is not None:
                element["lost_capacity"] = row["lost_energy"]
                element["lost_capacity_share"] = row["share"]
                element["lost_capacity_through_dependents"] = row["through_dependents"]
    cause_totals = {c: sum(t.cause_time.get(c, 0.0) for t in trials) for c in CAUSES}
    all_red = sum(cause_totals.values())
    durations = sorted(o.duration for t in trials for o in t.kept)
    result = {
        "schema": "raptor-attribution/1",
        "runs": len(trials),
        "runs_with_outage": sum(1 for t in trials if t.outages),
        "system": {"red_time": _stat([t.red_time for t in trials]),
                   "outages": _stat([float(t.outages) for t in trials]),
                   "failure_outages": _stat([float(t.failure_outages) for t in trials]),
                   "span": _stat([t.span for t in trials])},
        "elements": elements,
        "causes": {c: v / all_red for c, v in cause_totals.items() if all_red and v > 0},
        "outage_durations": {"count": len(durations),
                             "open_at_end": sum(1 for t in trials for o in t.kept
                                                if o.open_at_end),
                             "p50": _percentile(durations, 0.5),
                             "p90": _percentile(durations, 0.9),
                             "max": durations[-1] if durations else None,
                             "hist": _duration_hist(durations)},
        "sequences": _sequences(trials, names),
    }
    if capacity is not None:
        result["capacity"] = capacity
    phase_rows = _phase_rows(trials, names, phases)
    if phase_rows:
        result["phases"] = phase_rows
    if hierarchies:
        result["hierarchies"] = _hierarchy_rows(trials, hierarchies)
    return result


#: Elements listed per phase.
PHASE_TOP = 5


def _phase_rows(trials: list[TrialAttribution], names: dict[Key, str],
                order: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Per phase, over all runs: time in it, the system's down time in it,
    availability = 1 - down / time (pooled over runs, so long runs weigh
    more), its share of all down time, and the elements that started the
    down time in that phase with their shares of it."""
    seen: list[str] = [p["name"] for p in order or []]
    for trial in trials:
        for name in trial.phase_time:
            if name not in seen:
                seen.append(name)
    if not seen:
        return []
    mission = {p["name"]: p.get("mission", True) for p in order or []}
    all_red = sum(sum(t.phase_red.values()) for t in trials)
    rows = []
    for name in seen:
        time = sum(t.phase_time.get(name, 0.0) for t in trials)
        red = sum(t.phase_red.get(name, 0.0) for t in trials)
        by: dict[Key, float] = {}
        for trial in trials:
            for key, value in trial.phase_initiated.get(name, {}).items():
                by[key] = by.get(key, 0.0) + value
        top = sorted(by.items(), key=lambda kv: (-kv[1], _label(kv[0], names)))[:PHASE_TOP]
        rows.append({
            "name": name,
            "mission": mission.get(name),
            "time": _stat([t.phase_time.get(name, 0.0) for t in trials]),
            "red_time": _stat([t.phase_red.get(name, 0.0) for t in trials]),
            "availability": 1.0 - red / time if time > 0 else None,
            "share_of_downtime": red / all_red if all_red > 0 else 0.0,
            "drivers": [{"name": _label(key, names), "kind": key[0], "id": key[1],
                         "share": value / red if red > 0 else 0.0}
                        for key, value in top],
        })
    return rows


#: More system-down intervals than this in one batch are not stored.
MAX_INTERVALS_STORED = 2_000_000


def outage_record(trials: list[TrialAttribution]) -> dict[str, Any] | None:
    """Every run's system-down intervals, for slicing by period later
    (`raptor.api.insight.periods`). None when there are too many to store."""
    if sum(len(t.red) for t in trials) > 2 * MAX_INTERVALS_STORED:
        return None
    return {"schema": "raptor-outages/1",
            "runs": [{"start": t.start, "end": t.end, "red": t.red} for t in trials]}


ATTRIBUTE_LABEL = "Working out what caused the downtime"


def attribute_logs(logs: Iterable[Iterable[Any]], start_stats: float = 0.0, *,
                   progress: Any = None, abort: Any = None,
                   total: int | None = None, with_outages: bool = False,
                   capacity: Iterable[CapacityRun | None] | None = None,
                   phases: list[dict[str, Any]] | None = None,
                   hierarchies: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attribution for a whole batch from its per-run event logs.

    `progress` (a `raptor.api.progress.ProgressFn`) is told after each run;
    `abort` (a `threading.Event`) stops it with `raptor.api.progress.Cancelled`.
    `with_outages` adds `outages`: every run's system-down intervals, from
    which `raptor.api.insight.periods` computes yearly availability.
    `capacity` gives each run's flow track (the capacity sink), in run order,
    so the lost flow is attributed to the elements that were down.
    `phases` (`model_phases(rbd)`) adds `phases`: downtime by mission phase.
    `hierarchies` (`model_hierarchies(rbd)`) adds the hierarchy roll-up.
    """
    from raptor.api.progress import check, report
    if total is None and hasattr(logs, "__len__"):
        total = len(logs)                                   # type: ignore[arg-type]
    capacity_runs = iter(capacity) if capacity is not None else None
    trials = []
    for i, lines in enumerate(logs, start=1):
        check(abort)
        cap_run = next(capacity_runs, None) if capacity_runs is not None else None
        trials.append(attribute_trial(lines, start_stats, cap_run, phases))
        report(progress, "attribute", ATTRIBUTE_LABEL, i, total)
    result = aggregate(trials, phases, hierarchies)
    if with_outages:
        result["outages"] = outage_record(trials)
    return result

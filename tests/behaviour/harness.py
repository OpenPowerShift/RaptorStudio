"""The behaviour-suite harness: build a model in memory, run it, assert on it.

Rules this file exists to enforce (see `README.md`):

* **B3 -- built in memory.** There is one constructor, `run()`, and it calls
  `build_from_spec(spec, auto_layout=False)`. No `.rbd` on disk, no
  `Workspace`, no `load_rbd`, and -- verified, not assumed -- nothing here
  imports `raptor.gui` or Qt.
* **B2 -- `Fixed` by default.** `FIXED` is the distribution code whose draw is
  `param[0]` and which touches no stream, so a `Fixed` run is exact and
  `Run.draws == 0`. `draws` is a *measurement* (`driver.count_stream_draws`),
  never an input; the harness never seeds or otherwise owns the RNG.
* **The harness extracts, it does not reimplement.** `events()` pulls fields
  out of the event-log lines the engine produced, `state_at()` reads the same
  lines back. Neither knows what a status *means*.

The one piece of instrumentation is `_counted_draws`, which subclasses the
engine's own `EngineCtx` for the duration of one `run_simulation` call and asks
the engine's own `count_stream_draws` to wrap that context's streams. It is
restored in a `finally`, it changes no drawn value (`CountingStream.uniform` is
`RaptorStream.uniform` plus a counter), and a run with it produces
byte-identical logs to a run without it.
"""
from __future__ import annotations

import contextlib
import difflib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from raptor.api.build import build_from_spec
from raptor.core.engine import driver as _driver
from raptor.core.engine import runner as _runner

#: Distribution code 19. `DrawNumber`'s `WHEN 19` returns `Param(1)`
#: (`raptor/core/rng.py`, `_dispatch_draw`), so a `Fixed` draw is exact and
#: consumes no randomness at all.
FIXED = 19

#: The date stamped on the End-of-Run and Final-Results reports. `run` passes
#: this explicitly rather than letting the reports call `time.ctime()`: the
#: reports must be reproducible (B7 -- SimTime is the only clock a test reads)
#: and a test that asserts on a report row must not depend on today's date.
REPORT_DATE = "Thu Jan  1 00:00:00 1970"

#: Event-log object types the timeline records. Every other line kind (the
#: cost/weak-link rows, which are off unless the analysis is requested) is not
#: a state and is not part of a timeline.
_OBJECT_TYPES = ("Block", "Node", "System", "Hier", "Event", "Pool", "Trigger")

#: What `old` shows for an object's first logged state. `"--"` is Raptor's own
#: "unknown" convention (the `Simulation_Terminate` line uses it), so it reads
#: as the sentinel it is.
FIRST_STATE = "--"


def block(name: str, fail: float, repair: float | None = None,
          **extra: Any) -> dict[str, Any]:
    """One block row with a `Fixed` life, and a `Fixed` repair when given.

    The behaviour suites build dozens of these; four copies of this body
    had drifted only in their docstrings (one defaulted the repair), so
    the shape lives here and the call site passes what it means.
    """
    row: dict[str, Any] = {"name": name, "fail_dist": FIXED,
                           "fail_params": [fail]}
    if repair is not None:
        row["repair_dist"] = FIXED
        row["repair_params"] = [repair]
    row.update(extra)
    return row


def chain(*names: str) -> tuple[list[dict], list[dict]]:
    """`Start -> names -> End` in series, one k=1 node between each pair.

    Node ids are 1 (Start), then 2, 3, ... between the blocks, and the End
    node last, so a fixture can name a node id as a dependency target. With
    one name there is no intermediate node: Start and End are 1 and 2, and
    `DepNum = -2` is the model's End node either way.
    """
    nodes = [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1}]
    links: list[dict] = [{"from": "Start", "to": names[0]}]
    previous, nid = names[0], 2
    for name in names[1:]:
        nodes.append({"id": nid, "name": f"N{nid}", "type": 2, "n": 1,
                      "out": 1, "k": 1})
        links.append({"from": previous, "to": f"N{nid}"})
        links.append({"from": f"N{nid}", "to": name})
        previous, nid = name, nid + 1
    nodes.append({"id": nid, "name": "End", "type": 3, "n": 1, "out": 0})
    links.append({"from": previous, "to": "End"})
    return nodes, links


def details(result: Any, kind: str, name: str) -> list[str]:
    """Every event-log detail word for one object, in log order.

    ``result.log`` is a flat tab-separated table, so an object's name is a
    *column*, not a substring: selecting by column avoids other objects'
    lines. The detail column is the one the state machine writes and the
    timeline never shows.
    """
    return [line.split("\t")[4].strip() for line in result.log
            if line.split("\t")[1].strip() == kind
            and line.split("\t")[2].strip() == name]


def cross_run_view(result: Any) -> Any:
    """*result* re-presented with its `cross_run` timeline as `events`.

    `assert_timeline` reads `Run.events`, which is run 1's timeline; a
    claim about the chain across a run boundary needs the harness's own
    `cross_run` list instead. Nothing is recomputed, and `transitions` is
    rebuilt from the same list so a failure still prints the offending
    object's rows.
    """
    import dataclasses

    per_object: dict[str, list[tuple[float, str, str]]] = {}
    for event in result.cross_run:
        per_object.setdefault(event.obj, []).append(
            (event.time, event.old, event.new))
    return dataclasses.replace(result, events=result.cross_run,
                               transitions=per_object)


def fixed(v: float) -> dict[str, Any]:
    """A `Fixed` failure life of *v*, as a block patch.

    >>> fixed(50.0) == {"fail_dist": 19, "fail_params": [50.0]}
    True
    """
    return {"fail_dist": FIXED, "fail_params": [v]}


def dist(code: int, *params: float) -> dict[str, Any]:
    """A failure life from distribution *code*, as a block patch.

    Stochastic codes consume randomness, so a test using one states a
    tolerance in its docstring (B2). Reach for this only when the
    *distribution* is the subject.
    """
    return {"fail_dist": int(code), "fail_params": [float(p) for p in params]}


class Event(NamedTuple):
    """One state change out of an event log.

    ``time`` is sim time in hours; ``kind`` and ``name`` are the log's own
    object type and object name (`"Block"/"A"`, `"Node"/"End"`,
    `"System"/"State_change"`), so the qualified name ``f"{kind}:{name}"``
    identifies one object. ``old`` is that object's previously logged state
    and ``new`` is the state this line logged -- both are the log's own
    vocabulary, which is what the captured corpora use.

    The object *kind* is carried explicitly because the whole point of the
    suite is to pin transitions of blocks, nodes, systems and the rest, and a
    bare name cannot tell `Node:End` from `Block:End`.
    """
    time: float
    kind: str
    name: str
    old: str
    new: str

    @property
    def obj(self) -> str:
        """The qualified object name, ``"Kind:name"``."""
        return f"{self.kind}:{self.name}"

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return (f"{self.time:g} {self.kind}:{self.name} "
                f"{self.old} -> {self.new}")


@dataclass(frozen=True)
class Run:
    """What one behaviour run produced, in the shapes tests assert on.

    Attributes:
        events: every state change of **run 1**, in event-log order (which is
            sim-time order), as `Event(time, kind, name, old, new)`. With
            `runs > 1` this is still run 1's: the aggregation a multi-run
            batch exists to measure is what `eor` and `final` are for.
        per_run: every run's own timeline, chained within that run. Use it to
            assert on run *n* in isolation.
        cross_run: every run's events chained **continuously across run
            boundaries**, per object. This is the corpus's own rule, and the
            reason it exists: real Raptor never re-announces an object's
            state at `Begin_Run_N`, so the transition it logs in run 2 is
            measured from whatever run 1 last said. Fifteen corpus
            transitions exist only because of this — `System Green →
            Mission` is written at each run's kickoff and can only be seen
            with a run 1 before it. `behaviour_inventory.py:26-30` chains the
            same way, which is why the two agree.
        log: run 1's raw event-log lines, for a test that wants the text.
        eor: the End-of-Run report lines.
        final: the Final-Results report lines.
        transitions: per qualified object name (`"Block:A"`, `"Node:End"`,
            `"System:State_change"`), its own `(time, old, new)` transitions --
            what `assert_timeline` prints when a timeline goes wrong.
        draws: RNG draws consumed by the whole batch, summed over every
            stream. `0` for an all-`Fixed` model, which is what rule B2 is
            for and what `test_fixed_lives_consume_no_randomness` asserts.
        runs: how many replications ran (`1` under B4).
        rbd: the built model, for a test that wants to inspect the rows.
    """
    events: list[Event]
    log: list[str]
    eor: list[str]
    final: list[str]
    transitions: dict[str, list[tuple[float, str, str]]]
    draws: int
    runs: int = 1
    per_run: list[list[Event]] = field(default_factory=list)
    cross_run: list[Event] = field(default_factory=list)
    rbd: Any = field(default=None, repr=False, compare=False)


@contextlib.contextmanager
def _counted_draws(into: list) -> Iterator[None]:
    """Wrap every `EngineCtx` built inside the block in counting streams.

    `run_simulation` builds the batch's context itself, so the only way to see
    its streams without touching the engine is to be the `EngineCtx` it builds:
    a subclass that asks `driver.count_stream_draws` for its streams as soon as
    the real constructor has finished wiring them. Counting is a measurement --
    `CountingStream.uniform` is `RaptorStream.uniform` plus an integer -- so
    the run's output is unchanged.
    """
    real = _runner.EngineCtx

    class _CountingCtx(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            into.append(_driver.count_stream_draws(self))

    _runner.EngineCtx = _CountingCtx
    try:
        yield
    finally:
        _runner.EngineCtx = real


def _fields(line: Any) -> tuple[float, str, str, str] | None:
    """`(time, type, name, action)` for one event-log line, or `None`.

    A line the engine produced is a `LogLine`, which carries those fields
    already; a line that has been through a process boundary is the same
    text without them, so the padded columns are read back instead. Extraction only.

    `None` means *this is not an event row*, which is a real case rather
    than a malformed one: the engine writes two kinds of line. `WriteEvents`
    emits the padded, tab-separated form this reads. `WriteString` emits
    bare text, and the delayed-statistics banner is the one that matters --
    `ResetAllStatistics` writes a blank line and then
    `"25.000000  Started Gathering Statistics"` with no tab columns at all
    (ported at `orchestrator.py:303-304`).

    The first version of this function raised on anything it could not read,
    which meant `run()` could not build a model with `StartStats` set: the
    engine was right and the reader was too strict.

    **The action is read truncated, to the log's own 15-character width.**
    `LogLine` is a `str` subclass: its text is already
    `_pad(action, 15)`-formatted, but it *also* carries the untruncated
    `action` field, and reading the field instead of the text gives a
    different vocabulary from the one Raptor records. `End_Partial_post-LDT`
    is the case that matters -- the log says `End_Partial_pos`, and so does
    every legacy capture, because that is the width `WriteEvents` writes.
    Reading `.action` gave `End_Partial_pre-LDT` and the harness could not
    express four corpus rows the port demonstrably produces.
    """
    time = getattr(line, "time", None)
    if time is not None:
        return (float(time), str(line.type), str(line.name),
                # `(s + " " * width)[:width]`, stripped -- see above.
                (str(line.action)[:15]).strip())
    columns = str(line).split("\t")
    if len(columns) < 4:
        return None
    return (float(columns[0].strip()), columns[1].strip(),
            columns[2].strip(), columns[3].strip())


def _timeline(lines: Sequence[str]) -> list[Event]:
    """Every state change in *lines*, chained per object.

    Each object's ``old`` is the state it logged last, so a status that is
    logged twice in a row shows up as a real ``X -> X`` transition rather than
    being quietly deduplicated -- this harness reports what the engine logged.

    Lines that are not event rows (`WriteString` output -- the
    delayed-statistics banner, a run marker) are skipped, not rejected; see
    `_fields`.
    """
    previous: dict[str, str] = {}
    events: list[Event] = []
    for line in lines:
        parsed = _fields(line)
        if parsed is None:
            continue
        time, kind, name, action = parsed
        if kind not in _OBJECT_TYPES:
            continue
        obj = f"{kind}:{name}"
        events.append(Event(time, kind, name, previous.get(obj, FIRST_STATE),
                            action))
        previous[obj] = action
    return events


def run(spec: dict[str, Any], *, runs: int = 1, length: float | None = None,
        seed: int | None = None, **system: Any) -> Run:
    """Build *spec* in memory, run it, and return what came out.

    Args:
        spec: a `build_from_spec` spec dict, e.g. from `spine.spine(rung)`.
        runs: replications. B4 says 1 unless the property is about aggregation.
        length: overrides `system.run_length` (hours) -- shorter runs are the
            cheapest way to keep a test under its 100 ms budget.
        seed: salts every stream, so a stochastic model is reproducible. Left
            unset under B2, when nothing is stochastic.
        **system: any other `SYSTEM_DATA` override (`termination`,
            `weak_analysis`, `cost_analysis`, ...).

    Returns:
        A `Run`.
    """
    spec = {**spec, "system": {**spec.get("system", {}), **system}}
    # Not part of the model: which trigger a `maintDefer` block waits for (the
    # developer option; see `raptor.core.engine.load.resolve_defer_trigger`).
    defer_trigger = spec.pop("defer_trigger", "")
    if length is not None:
        spec["system"]["run_length"] = float(length)
    if runs != 1:
        spec["system"]["num_runs"] = int(runs)
    rbd = build_from_spec(spec, auto_layout=False)

    counted: list = []
    with _counted_draws(counted):
        logs, eor, final = _runner.run_simulation_with_reports(
            rbd, int(runs), date=REPORT_DATE, seed=seed, defer_trigger=defer_trigger)
    if not counted:  # pragma: no cover - only if the engine stops building one
        raise AssertionError(
            "the engine built no EngineCtx, so no draws were counted -- the "
            "harness's measurement hook missed a code path")

    lines = list(logs[0]) if logs else []
    events = _timeline(lines)
    per_object: dict[str, list[tuple[float, str, str]]] = {}
    for event in events:
        per_object.setdefault(event.obj, []).append(
            (event.time, event.old, event.new))
    # Every run's own timeline, and every run's chained continuously. See the
    # `per_run` / `cross_run` docstrings for why the second is not a
    # convenience: it is the corpus's chaining rule, and 15 of the 318
    # transitions are reachable only through it.
    per_run = [_timeline(list(r)) for r in logs] if logs else []
    cross_run = _timeline([line for r in logs for line in r]) if logs else []
    draws = sum(stream.draws for batch in counted for stream in batch)
    return Run(events=events, log=lines, eor=list(eor), final=list(final),
               transitions=per_object, draws=draws, runs=int(runs),
               per_run=per_run, cross_run=cross_run, rbd=rbd)


def events(run: Run, block: str | None = None,
           between: tuple[float, float] | None = None) -> list[Event]:
    """A filtered view of *run*'s timeline -- the thing a test asserts on.

    Args:
        block: keep only this one object. Matches the qualified name
            (``"Block:A"``, ``"Node:End"``, ``"System:State_change"``) or the
            bare name (``"A"``). A bare name matches any object type, so
            `block="A"` is only unambiguous while the model has one `A` --
            prefer the qualified form when it might not.
        between: keep only events in ``(t0, t1)``, both ends included.

    Returns:
        The matching events, in event-log order (which is sim-time order).
    """
    result = run.events
    if block is not None:
        wanted = block.strip()
        result = [e for e in result
                  if e.obj.lower() == wanted.lower()
                  or e.name.lower() == wanted.lower()]
    if between is not None:
        low, high = between
        result = [e for e in result if low <= e.time <= high]
    return list(result)


def state_at(run: Run, t: float) -> dict[str, str]:
    """Every object's logged state at sim time *t*.

    Read back from the event log: the state of each object at the last line it
    logged at or before *t*. An object that has not logged yet is absent, so a
    key that is missing means "not initialised yet", not "unknown".
    """
    states: dict[str, str] = {}
    for event in run.events:
        if event.time > t:
            break
        states[event.obj] = event.new
    return states


#: Row layout, shared by the diff and the per-object trace below.
_TIME_WIDTH = 9


def _row(event: Event | Sequence) -> str:
    """One timeline row, aligned so two columns read side by side."""
    time, kind, name, old, new = event
    return (f"{float(time):{_TIME_WIDTH}.3f}  {kind:<7} {name:<16} "
            f"{old} -> {new}")


def _as_tuple(item: Event | Sequence) -> tuple:
    """A literal timeline row, or an `Event`, as one five-element tuple.

    The plain form is `(time, kind, name, old, new)`. A four-element literal
    `(time, name, old, new)` is shorthand for a **block** row, because rule B5
    describes the expected value as a list of `(time, block, from, to)` tuples
    and most claims are about blocks. Nothing else is inferred -- a node or
    system row must name its type, since a bare name cannot tell `Node:End`
    from `Block:End`.
    """
    values = tuple(item)
    if len(values) == 5:
        return values
    if len(values) == 4:
        return (values[0], "Block", values[1], values[2], values[3])
    raise ValueError(
        f"a timeline literal is (time, kind, name, old, new); got {values!r}")


def _obj_of(values: tuple) -> str:
    return f"{values[1]}:{values[2]}"


def _elided(count: int) -> str:
    return f"  ... {count} unchanged event(s) elided"


def _diff_lines(wanted: list[tuple], got: list[tuple],
                limit: int = 60) -> list[str]:
    """The symmetric difference of two timelines, in sim-time order.

    Unchanged runs between differences collapse to one elision marker, so a
    2,000-event timeline with three wrong rows prints three wrong rows and two
    markers -- not 4,000 rows.
    """
    rows_wanted = [_row(e) for e in wanted]
    rows_got = [_row(e) for e in got]
    matcher = difflib.SequenceMatcher(a=rows_wanted, b=rows_got,
                                      autojunk=False)
    out: list[str] = []
    first_change = True
    previous_equal_end = 0
    truncated = False
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            previous_equal_end = i2
            continue
        if i1 > previous_equal_end:
            out.append(_elided(i1 - previous_equal_end))
        marker = ">>" if first_change else "  "
        first_change = False
        for index in range(i1, i2):
            if len(out) >= limit:
                truncated = True
                break
            out.append(f"  {marker} expected only  {rows_wanted[index]}")
        if not truncated:
            for index in range(j1, j2):
                if len(out) >= limit:
                    truncated = True
                    break
                out.append(f"  {marker} actual only    {rows_got[index]}")
        previous_equal_end = i2
        if truncated:
            out.append("  ... (difference truncated; this is what the "
                       "symmetric difference looks like at full length)")
            break
    return out


def _trace_lines(run: Run, obj: str) -> list[str]:
    trace = run.transitions.get(obj) or []
    if not trace or len(trace) > 16:
        return []
    lines = ["", f"actual transitions for {obj} "
                 f"({len(trace)} rows; the rung's literal is above):"]
    for time, old, new in trace:
        lines.append(f"  {time:{_TIME_WIDTH}.3f}  {old} -> {new}")
    return lines


def assert_timeline(run: Run, expected: Sequence[Event | Sequence],
                    *, where: str | None = None) -> None:
    """Assert that *run*'s timeline is exactly *expected*.

    *expected* is a literal list of ``(time, kind, name, old, new)`` tuples
    (rule B5: the expectation lives in the test, next to the claim, and is
    never recomputed by the code under test). A four-element literal
    ``(time, name, old, new)`` is accepted as shorthand for a block row.

    On failure this prints the **symmetric difference** in sim-time order with
    the first divergence marked, not two full lists side by side, and follows
    it with the affected object's own transitions -- which is usually the
    answer, because a wrong timeline is nearly always one object's trace that
    grew a row or lost one. Designed to be read at 2am: the first divergence is
    three lines from the top, and every row is aligned.
    """
    wanted = [_as_tuple(e) for e in expected]
    got = [tuple(e) for e in run.events]
    if wanted == got:
        return

    label = f" [{where}]" if where else ""
    first = 0
    while first < min(len(wanted), len(got)) and wanted[first] == got[first]:
        first += 1
    head = wanted[first] if first < len(wanted) else None
    tail = got[first] if first < len(got) else None

    lines = [f"timeline mismatch{label}: {len(wanted)} expected, "
             f"{len(got)} actual"]
    if head is not None or tail is not None:
        lines.append(
            f"first divergence at event {first}, sim time "
            f"{float((head or tail)[0]):g}")
        lines.append(f"  expected  {_row(head) if head else '(no event)'}")
        lines.append(f"  actual    {_row(tail) if tail else '(no event)'}")

    lines.append("")
    lines.append("symmetric difference (sim-time order, '>>' = first "
                 "divergence):")
    lines.extend(_diff_lines(wanted, got))

    diverged = tail if tail is not None else head
    if diverged is not None:
        lines.extend(_trace_lines(run, _obj_of(diverged)))

    raise AssertionError("\n".join(lines))
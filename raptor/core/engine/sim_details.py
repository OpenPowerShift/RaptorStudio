"""Port of `simDetails.TXT` (`/sd` flag) -- the exact value of every RNG
draw, tagged by name and `SimTime`, that the Raptor 7 engine writes when
launched with `/sd` (Raptor 7's `DrawNumber`, the single chokepoint
every distribution call goes through):

```
IF (simDetails AND (currentDistro<>19))
   ...WriteString(REALTOSTR(SimTime)+"  "+gettingDraw+"  "+REALTOSTR(drawn));
```

(`gettingDraw` is the `name` argument passed at each call site -- the
owning block/event/trigger/phase's own name.) `Distribution.FIXED` (19,
a constant, not a real draw) is excluded, matching `currentDistro<>19`.

Useful for RNG-position debugging: rather than inferring draw order and
values from an event log's timestamps and comment strings (ambiguous once
multiple distributions share one stream), enable this during a run and diff
the result line-for-line against a real `/sd` capture of the same `.rbd` file.

`RandomGen.draw_number` holds the pure math; this module is the separate
engine-layer logging around it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable

from raptor.core.rng import Distribution, RandomSource


def format_draw(drawn: float) -> str:
    """A draw as Raptor 7 writes it: six decimals, and the Microsoft runtime's `1.#INF00` for infinity
    (an Erlang draw whose product of uniforms underflowed, `LargeShape1000Probe`)."""
    if drawn == float("inf"):
        return "1.#INF00"
    if drawn == float("-inf"):
        return "-1.#INF00"
    return f"{drawn:.6f}"


@dataclass
class SimDetailsRecorder:
    """Accumulates `(sim_time, name, drawn)` triples in draw order,
    writing them out in the exact real `simDetails.TXT` format
    (`"{sim_time:.6f}  {name}  {drawn:.6f}"`, CRLF line endings) via
    `write`. Pass the SAME instance to every `NamedRandomGen` in a run
    (or across multiple runs) to build one combined trace."""

    lines: list[str] = field(default_factory=list)
    #: Draws seen -- Raptor 7's `randNumCount` (Raptor 7, `INC` before the
    #: `/sd` write, same `currentDistro<>19` test), which the Final Results report prints
    #: when the master switch is on. `keep_lines=False` counts without keeping the text.
    count: int = 0
    keep_lines: bool = True

    def record(self, sim_time: float, name: str, drawn: float) -> None:
        self.count += 1
        if self.keep_lines:
            self.lines.append(f"{sim_time:.6f}  {name}  {format_draw(drawn)}")

    def write(self, path: str, *, append: bool = False) -> None:
        """`append=True` is Raptor 7's behaviour (`Open("simDetails.TXT", Append)`: the
        file grows across launches)."""
        with open(path, "ab" if append else "wb") as f:
            for line in self.lines:
                f.write(line.encode("latin-1") + b"\r\n")


@dataclass
class NamedRandomGen:
    """Wraps a real `RandomGen`, recording every draw into a shared
    `SimDetailsRecorder` before returning it -- a transparent drop-in
    replacement anywhere a plain `RandomGen` is used (every caller in
    this project only ever calls `.draw_number(distro, params)`, never
    touches `.stream`/other `RandomGen`-specific attributes directly, so
    wrapping is safe everywhere `make_rng` constructs one).

    `name`: either a fixed string (the common case -- one `RandomGen` per
    block/event, tagged with that object's own name once) or a zero-arg
    callable returning the CURRENT name to use for the next draw (the
    shared-stream case, e.g. `SysStream[11]`'s own phase-duration draws,
    each tagged with whichever phase is CURRENTLY being drawn for --
    `runner.run_simulation`'s own construction site supplies the
    live lookup).

    `sim_time_fn`: zero-arg callable returning the CURRENT `SimTime` to
    tag each draw with -- a callable, not a fixed value, since the same
    wrapped `RandomGen` instance is reused across an entire run (or
    multiple runs) and `SimTime` advances between draws.
    """

    rng: RandomSource
    recorder: SimDetailsRecorder
    name: str | Callable[[], str]
    sim_time_fn: Callable[[], float]

    #: ONE-SHOT call-site name for the very next draw, consumed by it
    #: (`tag_next_draw` below). Raptor 7's `gettingDraw` is an argument of
    #: the `DrawNumber` CALL, so a shared stream's name is whatever the
    #: current call site passes -- not a property of the stream. A
    #: shared stream whose `name` is a live lookup (`SysStream[11]`'s
    #: phase-duration tag) would otherwise leak the last tag set into
    #: every LATER draw from a different call site.
    next_name: str | None = None

    def draw_number(self, distro: Distribution, params: list[float]) -> float:
        drawn = self.rng.draw_number(distro, params)
        one_shot, self.next_name = self.next_name, None
        if distro != Distribution.FIXED:
            if one_shot is not None:
                resolved_name = one_shot
            else:
                resolved_name = self.name() if callable(self.name) else self.name
            self.recorder.record(self.sim_time_fn(), resolved_name, drawn)
        return drawn


def tag_next_draw(rng: RandomSource, name: str) -> None:
    """Name the NEXT draw taken from `rng` after Raptor 7's own per-call-site
    `gettingDraw` argument, when `/sd` recording is on -- a no-op on a
    plain `RandomGen` (recording off), so call sites need no `if
    sim_details` branch of their own.

    Needed only where a SHARED stream serves several distinct call sites
    that Raptor 7 names differently: `SysStream[11]` carries phase durations
    (tagged by phase name), `Link.SetSwitchTime`'s switch-probability
    roll (tagged `link <objectNum>`) and generic callers (untagged).
    """
    if isinstance(rng, NamedRandomGen):
        rng.next_name = name

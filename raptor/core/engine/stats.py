"""`RunningStat`: a minimal Sum/Count/Min/Max accumulator matching the
Modsim runtime's `RStatObj` (`StatMod`, a built-in statistics-monitor
primitive with no source in this tree, like `TriggerObj`/`ResourceObj`).
`RStatObj.StdDev` is population (N-divisor) standard deviation;
`report_format.pop_to_sample` converts it where a displayed StDev/SEM column
needs a sample value.

Used both for within-run monitors (`GreenMon`/`RedMon`/`SysFailMon`/
`CompFailMon`/`CompRepairMon`, reset every run) and for the recursive
cross-run monitors `Final_Results` uses (`AvailMon`/`MTBDEMon`/etc., one sample fed per run and never reset).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# Empirical, not a ported mechanism: the monitor clock is rounded to 9 decimals. Raptor 7's per-run float noise in
# `UnitsProduced`/`Mean_Flow` (FactoryDemo's cross-run St.Dev) is reproduced by 3, 6 or 9 decimals but by no other
# choice, and only 9 also matches LadL8C's End-of-Run (6 gives 95 last-digit diffs over 300 runs; 9 leaves 3 node-capacity lines).
_DEC = 9


@dataclass
class RunningStat:
    count: int = 0
    sum: float = 0.0
    sum_sq: float = 0.0
    minimum: float = field(default=math.inf)
    maximum: float = field(default=-math.inf)

    def reset(self) -> None:
        """Back to the empty state (`ResetAllStatistics`)."""
        self.count = 0
        self.sum = 0.0
        self.sum_sq = 0.0
        self.minimum = math.inf
        self.maximum = -math.inf

    def add(self, value: float) -> None:
        self.count += 1
        self.sum += value
        self.sum_sq += value * value
        if value < self.minimum:
            self.minimum = value
        if value > self.maximum:
            self.maximum = value

    @property
    def mean(self) -> float:
        return self.sum / self.count

    @property
    def population_stdev(self) -> float:
        variance = self.sum_sq / self.count - self.mean ** 2
        return math.sqrt(max(variance, 0.0))


@dataclass
class TimedStat:
    """Time-weighted standing for the Modsim runtime's `ITimedStatObj`
    (`StatMod`, no source in this tree) -- the monitor type behind the
    `LMONITORED` `NodeFlow`/`NodeCapacity` arrays in Raptor 7
    (read with `GETMONITOR(NodeFlow[i], ITimedStatObj)`).
    Unlike `RStatObj` (one sample per assignment), a timed monitor's `.Mean`
    is the time-integrated average: each value holds from its update until the
    next, so `.Mean` over a window is the integral of the held value divided
    by window length. Min/Max are plain extrema over every value assigned
    after the reset; the value merely held at reset time is not itself a
    sample (see `reset`). Used by capacity analysis (`engine/capacity.py`):
    `NodeAveFlow`/`NodeMinCap`/etc. read these at run end.

    Modsim's timed monitors tick on simulation time and are re-created fresh
    at each run boundary; here,
    `reset(initial_value, sim_time)` starts a new run's monitor.
    `update(value, sim_time)` assigns to the monitored variable, closing out
    the interval the previous value held; `sim_time` must not go backwards
    within a run. `mean_at(end_time)` reads the integral lazily without
    mutating state, matching reading `.Mean` off the live monitor.
    """

    #: Current held value (what the monitored variable equals right now --
    #: the raw array element, e.g. `NodeFlow[seq]`).
    value: int = 0
    #: Time this monitor started integrating (its run's t=0).
    start_time: float = 0.0
    _epoch: float = 0.0
    _area: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf

    def reset(self, initial_value: int = 0, sim_time: float = 0.0) -> None:
        self.value = initial_value
        self.start_time = sim_time
        self._epoch = sim_time
        self._area = 0.0
        # Not observations. `Reset` clears the monitor's statistics; the value
        # merely held at reset time is not itself a sample, and the first real
        # assignment establishes both extrema. Capacity monitors are reset to
        # 0 before the run's first `DistributeFlow`, so counting the reset
        # value would pin every MinFlow/MinCapacity at 0.
        self.minimum = math.inf
        self.maximum = -math.inf

    def update(self, value: int, sim_time: float) -> None:
        sim_time = round(sim_time, _DEC)
        self._area += self.value * max(sim_time - self._epoch, 0.0)
        self._epoch = sim_time
        self.value = value
        if value < self.minimum:
            self.minimum = value
        if value > self.maximum:
            self.maximum = value

    @property
    def observed_minimum(self) -> float:
        """`minimum`, falling back to the currently-held value when the
        monitor was never assigned to after its reset."""
        return self.value if self.minimum == math.inf else self.minimum

    @property
    def observed_maximum(self) -> float:
        return self.value if self.maximum == -math.inf else self.maximum

    def mean_at(self, end_time: float) -> float:
        span = max(end_time - self.start_time, 0.0)
        if span <= 0.0:
            return float(self.value)
        total = self._area + self.value * max(end_time - self._epoch, 0.0)
        return total / span

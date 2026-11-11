"""How much disk a run's recordings will take, before it is run.

The recordings are written to disk as the engine produces them, so what a
user needs to know beforehand is how big they will be: an availability
plot sampled every 10 hours over a 20-year horizon is 17,520 rows a run,
and 1,000 runs of that is over a gigabyte.

Two kinds of figure:

* The availability plot and the interim cost report are sampled on a
  clock, so for a time-terminated run their size follows from the run
  length, the interval and the number of runs. These are close to exact.
* The event log has a line or more for every failure and repair, so its
  size follows from how often the blocks fail. That is estimated from each
  block's mean time to failure and mean repair time, treating the blocks
  as independent and always running. It is a rough figure (standby,
  dependency, phasing and spares all move it), good to within a small
  factor, and is labelled as approximate wherever it is shown.

A run that stops on a number of failures or cycles has no known length, so
the clocked recordings have no figure and say so.

Pure functions, no Qt and no engine load: this runs on every edit of the
Run panel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Bytes in one availability-plot row: a 20-character time column, its
#: two-space separator, five `d.dddddd` values with two-space separators,
#: and the line end (`kickoff.ao_plot_process`).
AO_ROW_BYTES = 22 + 4 * 10 + 8 + 1

#: Bytes the engine writes at the end of each run's availability rows: the
#: final row, a blank line, the `End_of_Run_N` marker and a blank line.
AO_RUN_TAIL_BYTES = AO_ROW_BYTES + 1 + 75 + 1

#: Measured on the shipped examples: event-log bytes per block failure-and-
#: repair cycle. A cycle writes the block's own failure and repair lines
#: plus, on average, about that much again in node and system lines.
EVENT_BYTES_PER_CYCLE = 350.0

#: Fixed lines at the head and foot of each run's event log.
EVENT_RUN_OVERHEAD_BYTES = 400.0

#: Interim cost: every sample writes several tables with a row for every
#: block, which is why this recording is large on a big model. Measured:
#: 6.4 MB for 2 runs x 12 samples of a 1,000-block model, 145 KB for the
#: same of a 17-block one.
COST_BYTES_PER_BLOCK = 262.0
COST_SAMPLE_OVERHEAD_BYTES = 1600.0

#: Capacity plot: a row only when the flow changes, which a few per cent of
#: block failures and repairs do. Small beside the others.
CAPACITY_BYTES_PER_CYCLE = 2.0
CAPACITY_RUN_OVERHEAD_BYTES = 150.0


@dataclass
class StorageItem:
    key: str                 # the recording key: "ao_plot", "event_log", ...
    label: str
    #: None when there is no figure (the run has no fixed length).
    bytes: float | None
    rows: float | None = None
    approximate: bool = False
    note: str = ""


@dataclass
class StorageEstimate:
    items: list[StorageItem] = field(default_factory=list)

    def item(self, key: str) -> StorageItem | None:
        return next((i for i in self.items if i.key == key), None)

    @property
    def total_bytes(self) -> float:
        return sum(i.bytes or 0.0 for i in self.items)

    @property
    def unknown(self) -> bool:
        """True when a ticked recording has no figure, so the total is a
        lower bound."""
        return any(i.bytes is None for i in self.items)

    @property
    def approximate(self) -> bool:
        return self.unknown or any(i.approximate for i in self.items)

    def summary(self) -> str:
        """For the Run panel: the total, then what each recording adds,
        largest first (`About 1.2 GB on disk: interim cost 900 MB, ...`)."""
        if not self.items:
            return "No recordings ticked: nothing extra is written."
        if self.unknown and self.total_bytes <= 0.0:
            return ("Size on disk depends on how long each run lasts "
                    "(the run does not stop at a fixed time).")
        lead = "At least" if self.unknown else (
            "About" if self.approximate else "")
        text = f"{lead} {format_bytes(self.total_bytes)} on disk".strip()
        sized = sorted((i for i in self.items if i.bytes is not None),
                       key=lambda i: -(i.bytes or 0.0))
        if len(sized) > 1:
            text += ": " + ", ".join(
                f"{i.label.lower()} {format_bytes(i.bytes or 0.0)}"
                for i in sized)
        return text


def format_bytes(n: float) -> str:
    """`532 bytes`, `14 KB`, `1.2 GB`: two significant figures past a
    kilobyte, which is all an estimate supports."""
    n = max(0.0, float(n))
    if n < 1000:
        return f"{round(n)} bytes"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1000.0
        if n < 999.5 or unit == "TB":
            return f"{n:.1f} {unit}" if n < 9.95 else f"{n:,.0f} {unit}"
    raise AssertionError("unreachable")    # pragma: no cover


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def cycles_per_time(rbd: Any) -> tuple[float, int]:
    """(expected block failure-and-repair cycles per unit time, blocks).

    Each block contributes `1 / (mean life + mean repair)`; a block whose
    life has no finite mean (or is not a failing block at all) contributes
    nothing. Reads the block rows directly rather than loading the engine
    model, which would cost seconds on a large model for a figure that is
    rough anyway.
    """
    from raptor.core.analytical.survival import mean as dist_mean

    sections = getattr(rbd, "sections", None) or {}
    sec = sections.get("BLOCK_DATA")
    rows = list(getattr(sec, "rows", None) or [])
    rate = 0.0
    for row in rows:
        try:
            f_code = int(_number(row.get("FDist"), 0))
            r_code = int(_number(row.get("RDist"), 0))
            f_n = int(_number(row.get("#FParam"), 0))
            r_n = int(_number(row.get("#RParam"), 0))
            f_params = [_number(row.get(f"FParam{i}")) for i in range(1, f_n + 1)]
            r_params = [_number(row.get(f"RParam{i}")) for i in range(1, r_n + 1)]
            life = dist_mean(f_code, f_params)
        except Exception:      # noqa: BLE001 - an estimate must never fail an edit
            continue
        if life is None or not life > 0.0 or life == float("inf"):
            continue
        try:
            repair = dist_mean(r_code, r_params)
        except Exception:      # noqa: BLE001
            repair = None
        if repair is None or not repair >= 0.0 or repair == float("inf"):
            repair = 0.0
        rate += 1.0 / (life + repair)
    return rate, len(rows)


def block_count(rbd: Any) -> int:
    sections = getattr(rbd, "sections", None) or {}
    return len(getattr(sections.get("BLOCK_DATA"), "rows", None) or [])


def element_count(rbd: Any) -> int:
    sections = getattr(rbd, "sections", None) or {}
    return sum(len(getattr(sections.get(name), "rows", None) or [])
               for name in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                            "HIERARCHY_DATA"))


def experiment_of(rbd: Any) -> dict:
    """The model's run experiment: its own block, or (a legacy `.rbd`, an
    old `.rbds`) the one its SYSTEM_DATA row implies."""
    from raptor.core import run_experiment as rx
    experiment = getattr(rbd, "experiment", None)
    if isinstance(experiment, dict) and experiment:
        return rx.normalise(experiment)
    sec = (getattr(rbd, "sections", None) or {}).get("SYSTEM_DATA")
    rows = getattr(sec, "rows", None) or []
    return rx.normalise(rx.from_system_row(rows[0] if rows else {}))


_NO_LENGTH = "depends on how long each run lasts"


def _clocked(key: str, label: str, runs: float, length: float | None,
             interval: float, row_bytes: float, tail_bytes: float,
             note: str, approximate: bool) -> StorageItem:
    """A recording sampled every `interval`: `row_bytes` a sample."""
    if length is None:
        return StorageItem(key, label, bytes=None, approximate=approximate,
                           note=_NO_LENGTH)
    samples = float(int(length / interval + 1e-9))
    return StorageItem(
        key, label, bytes=runs * (samples * row_bytes + tail_bytes),
        rows=runs * samples, approximate=approximate,
        note=f"{samples:,.0f} samples a run{note}")


def _by_failures(key: str, label: str, runs: float, length: float | None,
                 cycle_rate: float | None, cycle_bytes: float,
                 run_bytes: float, note: str | None) -> StorageItem:
    """A recording that grows with how often the blocks fail."""
    if length is None or cycle_rate is None:
        return StorageItem(key, label, bytes=None, approximate=True,
                           note=_NO_LENGTH)
    cycles = cycle_rate * length
    return StorageItem(
        key, label, bytes=runs * (cycles * cycle_bytes + run_bytes),
        rows=runs * cycles, approximate=True,
        note=note if note is not None
        else f"about {cycles:,.0f} failures a run")


def estimate_storage(experiment: dict, rbd: Any = None, *,
                     recording: dict | None = None,
                     cycle_rate: float | None = None,
                     elements: int | None = None,
                     blocks: int | None = None) -> StorageEstimate:
    """The disk each ticked recording will take for `experiment`.

    `recording` overrides the experiment's own recording flags.
    `cycle_rate`, `elements` and `blocks` are `cycles_per_time(rbd)[0]`,
    `element_count(rbd)` and `block_count(rbd)` when the caller has them
    cached; otherwise they are read from `rbd`.
    """
    experiment = experiment or {}
    rec = dict(experiment.get("recording") or {})
    if recording:
        rec.update(recording)
    runs = max(0.0, _number(experiment.get("trials"), 1.0))
    length: float | None = _number(experiment.get("term_condition"), 0.0)
    if experiment.get("termination_type") != "TimeSim" or not length or length <= 0.0:
        length = None       # no fixed length: the clocked figures are unknown
    analyses = experiment.get("analyses") or ()
    if cycle_rate is None and rbd is not None and (
            rec.get("event_log") or rec.get("capacity_plot")):
        cycle_rate = cycles_per_time(rbd)[0]
    out = StorageEstimate()

    if rec.get("ao_plot"):
        out.items.append(_clocked(
            "ao_plot", "Availability plot", runs, length,
            max(1e-6, _number(rec.get("ao_interval"), 10.0)),
            AO_ROW_BYTES, AO_RUN_TAIL_BYTES, "", approximate=False))
    if rec.get("event_log"):
        out.items.append(_by_failures(
            "event_log", "Detailed event log", runs, length, cycle_rate,
            EVENT_BYTES_PER_CYCLE, EVENT_RUN_OVERHEAD_BYTES, None))
    if rec.get("capacity_plot") and "capacity" in analyses:
        out.items.append(_by_failures(
            "capacity_plot", "Capacity plot", runs, length, cycle_rate,
            CAPACITY_BYTES_PER_CYCLE, CAPACITY_RUN_OVERHEAD_BYTES,
            "a row only when the flow changes"))
    if rec.get("cost_plot") and "cost" in analyses:
        if blocks is None:
            blocks = block_count(rbd) if rbd is not None else (elements or 0)
        out.items.append(_clocked(
            "cost_plot", "Interim cost", runs, length,
            max(1e-6, _number(rec.get("cost_interval"), 720.0)),
            COST_SAMPLE_OVERHEAD_BYTES + COST_BYTES_PER_BLOCK * max(blocks, 1),
            0.0, ", each with a row for every block in several tables",
            approximate=True))
    return out


#: Measured on the shipped examples and a 1,000-block model: seconds of one
#: core per block failure-and-repair cycle, which grows with the model
#: because each state change is propagated through it.
SECONDS_PER_CYCLE = 3e-4
SECONDS_PER_CYCLE_PER_ELEMENT = 1.3e-6
#: Seconds per availability-plot sample.
SECONDS_PER_AO_SAMPLE = 2e-5


def rough_run_seconds(experiment: dict, *, cycle_rate: float | None,
                      elements: int = 0, batches: int = 1,
                      cores: int = 1) -> float | None:
    """About how long the run will take, from the same failure rate the
    event-log figure uses. None when there is nothing to go on (no model
    facts, or a run that does not stop at a fixed time).

    Good to within a small factor, which is all its one use needs: deciding
    whether to ask before starting a run that will take minutes. Run-hours
    alone cannot say that: 200 trials of a year is 1.75 million run-hours
    and takes ten seconds on a model whose blocks fail a few times a year.
    """
    if cycle_rate is None or (experiment or {}).get("termination_type") != "TimeSim":
        return None
    runs = max(1.0, _number((experiment or {}).get("trials"), 1.0))
    length = _number((experiment or {}).get("term_condition"), 0.0)
    if length <= 0.0:
        return None
    per_cycle = SECONDS_PER_CYCLE + SECONDS_PER_CYCLE_PER_ELEMENT * max(0, elements)
    per_run = cycle_rate * length * per_cycle
    rec = (experiment or {}).get("recording") or {}
    if rec.get("ao_plot"):
        interval = max(1e-6, _number(rec.get("ao_interval"), 10.0))
        per_run += (length / interval) * SECONDS_PER_AO_SAMPLE
    # Extra cores help less than linearly (set-up and merging are serial).
    speedup = 1.0 + 0.7 * (max(1, int(cores)) - 1)
    return runs * max(1, int(batches)) * per_run / speedup

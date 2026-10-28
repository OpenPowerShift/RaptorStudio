"""Parse Ao / capacity sink text into decimated series.

Parses the availability-over-time and capacity-over-time text outputs
into ``{t: list[float], values: list[float]}`` and decimates to N
points.  This module is the Qt-free owner of both sink formats: it used
to be written locally rather than importing
``raptor/gui/ui/plots.py`` (which pulls in Qt), and the GUI kept its own
second copy of the same parsers as a result.  The GUI now adapts these
run dicts instead.

The engine availability-plot format (``*_ao_sink`` rows) is::

    SimulationTime        Ao        Green     Yellow    Red       Interval
    10.000000             0.900000  0.600000  0.300000  0.100000  0.900000
    100.000000            0.850000  0.550000  0.300000  0.150000          <- run-end, no Interval
    ******************************End_of_Run_1******************************

Rows are whitespace-separated: 6 columns mid-run, 5 at the run-end.
Per-run sections are separated by ``*...End_of_Run_N...*`` markers.
:func:`parse_ao_runs` keeps every column of a row (``t``, ``values`` for
the ``Ao`` column, plus ``green``/``yellow``/``red``/``interval``, None
where the row stops short) so a consumer that paints the G/Y/R bands does
not have to re-parse the text; only ``t`` and ``values`` are required of
a run, so the rest are additive.
"""
from __future__ import annotations

import math
from typing import Any

#: Substring that marks a run boundary in both the Ao and capacity sinks.
_RUN_MARKER = "End_of_Run_"


# ---------------------------------------------------------------------------
# Ao (availability-over-time)
# ---------------------------------------------------------------------------

def parse_ao_text(text: str) -> dict[str, Any]:
    """Parse Ao (availability over time) text into a single series.

    Handles the real availability-plot format: a ``SimulationTime``
    header, 5/6-column whitespace-separated rows, and ``End_of_Run_N``
    separators (all runs are concatenated).  Returns ``{"t": [...],
    "values": [...]}`` where *values* is the ``Ao`` column.
    """
    times: list[float] = []
    values: list[float] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("*") or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        try:
            t = float(parts[0])
            v = float(parts[1])
        except (ValueError, IndexError):
            continue
        times.append(t)
        values.append(v)

    return {"t": times, "values": values}


def _num(token: str) -> float | None:
    """``float(token)``, or None when the token is not a number."""
    try:
        return float(token)
    except ValueError:
        return None


def parse_ao_runs(text: str) -> list[dict[str, Any]]:
    """Parse Ao text into one run dict per run.

    Runs are split on the ``End_of_Run_N`` markers -- the boundary check
    must precede the generic ``*`` skip, because the separator line ITSELF
    is the boundary.  *values* is the availability (``Ao``) column.

    The remaining columns of the same row are kept alongside it as
    ``green``/``yellow``/``red``/``interval``, so a consumer that paints
    the Green/Yellow/Red bands (the Graphs tab) does not have to parse the
    row a second time.  A column the row does not carry -- the run-end row
    has no ``Interval`` -- is ``None``, and so is one that does not parse
    as a number: the availability series is unaffected either way, because
    only ``t`` and ``values`` are ever required of a run.
    """
    runs: list[dict[str, Any]] = []
    current: dict[str, list[Any]] | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _RUN_MARKER in stripped:
            if current is not None:
                runs.append(current)
                current = None
            continue
        if stripped.startswith("*") or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        t, ao = _num(parts[0]), _num(parts[1])
        if t is None or ao is None:
            continue
        # Columns 3..6 of the same row, padded so every list is the same
        # length as `t`: a run's last row stops at Red.
        cols = [_num(tok) for tok in parts[2:6]]
        cols += [None] * (4 - len(cols))
        green, yellow, red, interval = cols
        if current is None:
            current = {"t": [], "values": [], "green": [], "yellow": [],
                       "red": [], "interval": []}
        current["t"].append(t)
        current["values"].append(ao)
        current["green"].append(green)
        current["yellow"].append(yellow)
        current["red"].append(red)
        current["interval"].append(interval)

    if current is not None:
        runs.append(current)
    return runs


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation, clamped at both ends (runs may share the
    same cadence but differ in final sample time)."""
    if not xs:
        return 0.0
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    frac = 0.0 if span <= 0 else (x - xs[lo]) / span
    return ys[lo] + frac * (ys[hi] - ys[lo])


def mean_envelope(
    runs: list[dict[str, Any]],
    points: int = 200,
) -> dict[str, list[float]]:
    """Across-run mean plus min/max envelope on a common time grid.

    Every run is linearly interpolated onto a shared grid spanning
    ``0..min(last_t)`` so each run covers it fully.  Returns
    ``{"t": [...], "mean": [...], "min": [...], "max": [...]}``.
    """
    usable = [r for r in runs if r.get("t")]
    if not usable:
        return {"t": [], "mean": [], "min": [], "max": []}

    horizon = min(r["t"][-1] for r in usable)
    base_t = usable[0]["t"]
    if len(base_t) > 1:
        step = base_t[1] - base_t[0]
    else:
        step = horizon
    if step <= 0:
        step = horizon / points if horizon > 0 else 1.0

    if horizon <= 0:
        times = [0.0]
    else:
        count = max(2, min(points, int(horizon / step) + 1))
        times = [horizon * i / (count - 1) for i in range(count)]

    means: list[float] = []
    mins: list[float] = []
    maxs: list[float] = []
    for x in times:
        vals = [_interp(r["t"], r["values"], x) for r in usable]
        means.append(sum(vals) / len(vals))
        mins.append(min(vals))
        maxs.append(max(vals))

    return {"t": times, "mean": means, "min": mins, "max": maxs}


def capacity_envelope(
    runs: list[dict[str, Any]],
    points: int = 200,
) -> dict[str, list[float]]:
    """Across-run mean/min/max of a step-like capacity series.

    Unlike :func:`mean_envelope`, this does **not** resample onto a
    uniform grid: the sink records only change-points, so a uniform grid
    (or a horizon clamped to the shortest run) would collapse a run with
    few changes to a single sample.  Instead the shared time axis is the
    union of every run's change times and each run is held at its last
    value between changes.  Returns the same
    ``{"t", "mean", "min", "max"}`` shape as :func:`mean_envelope`.

    Implemented as a sweep over the union times: each run's current value
    is advanced at its own change-points, so the cost is proportional to
    the total number of recorded rows, not ``runs × union_times``.

    ``points`` is accepted for signature parity with
    :func:`mean_envelope`; the union is returned whole and the caller
    decimates it (which keeps the change-points).
    """
    usable = [r for r in runs if r.get("t")]
    if not usable:
        return {"t": [], "mean": [], "min": [], "max": []}

    times = sorted({x for r in usable for x in r["t"]})

    # Group each run's change-points by time so the sweep only touches
    # real events.  The value before a run's first row is its first row.
    by_time: dict[float, list[tuple[int, float]]] = {}
    for ri, run in enumerate(usable):
        for t, v in zip(run["t"], run["values"]):
            by_time.setdefault(t, []).append((ri, v))
    current = [run["values"][0] for run in usable]

    means: list[float] = []
    mins: list[float] = []
    maxs: list[float] = []
    for x in times:
        for ri, v in by_time.get(x, ()):
            current[ri] = v
        means.append(sum(current) / len(current))
        mins.append(min(current))
        maxs.append(max(current))

    return {"t": times, "mean": means, "min": mins, "max": maxs}



# ---------------------------------------------------------------------------
# Capacity-over-time
# ---------------------------------------------------------------------------

def parse_capacity_runs(text: str) -> list[dict[str, Any]]:
    """Parse capacity text into one ``{t, flow, capacity}`` dict per run.

    The capacity sink's real format (deduplicated
    on flow change) is whitespace-separated::

        SimulationTime        Flow       Capacity
        0.000000              2050       2050
        ******************************End_of_Run_1******************************

    Rows are split on the same ``End_of_Run_N`` markers as the Ao sink.
    A ``SimulationTime`` header (or any other non-numeric line) is skipped.
    Returns one dict per run; each value list is the same length as ``t``.
    """
    runs: list[dict[str, list[float]]] = []
    current: dict[str, list[float]] | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # The boundary check must precede the generic "*" skip: the
        # separator line itself is the run boundary.
        if _RUN_MARKER in stripped:
            if current is not None:
                runs.append(current)
                current = None
            continue
        if stripped.startswith("*") or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        try:
            t = float(parts[0])
            flow = float(parts[1])
            capacity = float(parts[2])
        except (ValueError, IndexError):
            continue
        if current is None:
            current = {"t": [], "flow": [], "capacity": []}
        current["t"].append(t)
        current["flow"].append(flow)
        current["capacity"].append(capacity)

    if current is not None:
        runs.append(current)
    return runs


def parse_capacity_text(text: str) -> dict[str, Any]:
    """Parse capacity-over-time text into a single ``{t, values}`` series.

    ``values`` is the capacity (third) column, concatenated across runs.
    Kept for callers that only want one flat series; use
    :func:`parse_capacity_runs` when the run boundaries or the flow column
    matter.
    """
    times: list[float] = []
    values: list[float] = []
    for run in parse_capacity_runs(text):
        times.extend(run["t"])
        values.extend(run["capacity"])
    return {"t": times, "values": values}


# ---------------------------------------------------------------------------
# Decimation
# ---------------------------------------------------------------------------

def _lttb_indices(t: list[float], v: list[float], n: int) -> list[int]:
    """Largest-Triangle-Three-Buckets sample indices (n >= 3).

    Always includes the first and last sample; returns exactly *n*
    indices (the original algorithm's own behaviour, including any
    repeated index a degenerate bucket may pick).
    """
    result = [0]
    bucket_size = (len(t) - 2) / (n - 2)
    prev_index = 0
    for i in range(1, n - 1):
        start = math.floor((i - 1) * bucket_size) + 1
        end = min(math.floor(i * bucket_size) + 1, len(t))
        next_start = math.floor(i * bucket_size) + 1
        next_end = min(math.floor((i + 1) * bucket_size) + 1, len(t))

        avg_t = sum(t[next_start:next_end]) / max(next_end - next_start, 1)
        avg_v = sum(v[next_start:next_end]) / max(next_end - next_start, 1)

        max_area = -1.0
        max_idx = start
        for j in range(start, end):
            area = abs(
                (t[prev_index] - avg_t) * (v[j] - v[prev_index])
                - (t[prev_index] - t[j]) * (avg_v - v[prev_index])
            ) * 0.5
            if area > max_area:
                max_area = area
                max_idx = j
        result.append(max_idx)
        prev_index = max_idx
    result.append(len(t) - 1)
    return result


def decimate_indices(t: list[float], v: list[float], n: int) -> list[int]:
    """Choose at most *n* sample indices, keeping every change-point.

    Capacity/cost sinks record only change-points, so a series can be
    long with very few steps.  LTTB alone can smear those steps away when
    the surrounding flat runs dominate the triangle area.  This keeps the
    first/last samples and every index where the value changes whenever
    they fit in the budget, then fills the remainder with LTTB picks; if
    there are more change-points than *n* it falls back to plain LTTB.
    """
    if not t or n <= 0:
        return []
    if n == 1:
        return [0]
    if n == 2:
        return [0, len(t) - 1]
    if len(t) <= n:
        return list(range(len(t)))

    change = [0]
    change.extend(i for i in range(1, len(v)) if v[i] != v[i - 1])
    change.append(len(t) - 1)
    change = sorted(set(change))

    if len(change) <= n:
        chosen = set(change)
        if len(chosen) < n:
            for i in _lttb_indices(t, v, n):
                if len(chosen) >= n:
                    break
                chosen.add(i)
        return sorted(chosen)

    return _lttb_indices(t, v, n)


def decimate(data: dict[str, Any], n: int) -> dict[str, Any]:
    """Decimate a time series to at most *n* points.

    Keeps the first and last samples, never returns more than *n* points,
    and preserves change-points (see :func:`decimate_indices`).
    """
    t = list(data.get("t", []))
    v = list(data.get("values", []))
    if not t or n <= 0:
        return {"t": [], "values": []}
    idx = decimate_indices(t, v, n)
    return {"t": [t[i] for i in idx], "values": [v[i] for i in idx]}


def parse_and_decimate(
    text: str,
    n: int = 200,
    series_type: str = "ao",
) -> dict[str, Any]:
    """Parse text and decimate to *n* points in one call.

    *series_type* is ``"ao"`` for availability-over-time or
    ``"capacity"`` for capacity-over-time.
    """
    if series_type == "capacity":
        data = parse_capacity_text(text)
    else:
        data = parse_ao_text(text)

    return decimate(data, n)
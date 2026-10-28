"""Importance measures for a model's blocks, estimated by re-simulation.

IEC 61078 Annex D defines importance factors as derivatives of the system
function. A Monte Carlo simulator has no derivative to offer, so this module
estimates the quantities those factors describe by running the model with
**one block altered at a time**:

===========  ===============================================  ================
variant      how the block is altered                         batches
===========  ===============================================  ================
baseline     as authored                                      1
``perfect``  failure law ``Fixed`` beyond the run length      one per block
``failed``   failure law ``Fixed`` at 0, repair law ``None``  one per block
===========  ===============================================  ================

Nothing here changes the engine: a variant is the parsed model with one
``BLOCK_DATA`` row rewritten, which is the same edit the GUI makes.

Why those two edits mean what they say
--------------------------------------
* ``perfect`` -- a ``Fixed`` failure time of ``run_length + 1`` can never be
  reached inside a run, so the block cannot fail. It is not ``FDist = None``
  (code 18): the original ``DrawNumber`` CASE has no ``WHEN 18``
 and the failure-law list does not offer it, so
  the engine is right to refuse it.
* ``failed`` -- ``Fixed`` at 0 fails the block at the first instant, and
  repair law ``None`` (18) is the documented "never repaired" law (the
  ``repair_distro == NONE`` branches in ``block_lifecycle.py``), so it stays
  down for the whole run.

The measures
------------
With ``A`` the chosen metric's across-run mean, ``A0`` the baseline and
``u = 1 - A`` the corresponding *un*availability:

* Birnbaum        ``A_i^perfect - A_i^failed``      needs both variants
* RAW             ``u_i^failed / u0``               needs ``failed``
* RRW             ``u0 / u_i^perfect``              needs ``perfect``
* Vesely-Fussell  ``(A_i^perfect - A0) / u0``       needs ``perfect``

RAW, RRW and Vesely-Fussell are undefined when the baseline has no
unavailability (``A0 == 1``): there is no risk to reduce or achieve. They are
reported as ``None`` and rendered as an em dash, never as 0 or infinity.

These are simulation estimates, not the standard's analytical factors. Their
error is the combined sampling error of the batches they rest on, reported per
block as ``sem`` (``sqrt(sem_a**2 + sem_b**2)``, the *unpaired* combination --
variant runs are not common-random-number pairs, so pairing them would
overstate the precision).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from collections.abc import Callable, Sequence
from typing import Any

from raptor.core.run_experiment import IMPORTANCE_MODES

#: The analysis modes the GUI, CLI and MCP share. ``"none"`` is the off state.
#: Defined with the rest of the experiment (`core/run_experiment.py`) so the
#: panel, the CLI and this module cannot spell them differently.
MODES: tuple[str, ...] = IMPORTANCE_MODES

#: Which variant kinds a mode needs, in run order.
_MODE_VARIANTS: dict[str, tuple[str, ...]] = {
    "none": (),
    "improvement": ("perfect",),
    "risk": ("failed",),
    "both": ("perfect", "failed"),
}

#: Selectable metrics: report-data attribute and the label shown on screen.
METRICS: tuple[tuple[str, str], ...] = (
    ("availability", "Availability"),
    ("reliability", "Reliability"),
    ("dependability", "Dependability"),
)

#: The metric the GUI, CLI and MCP rank by unless told otherwise.
DEFAULT_METRIC = "Availability"

_D_FIXED = 19
_D_NONE = 18


@dataclass(frozen=True)
class Row:
    """One block's measures for one metric.

    ``perfect``/``failed`` are the variant means the measures came from, so a
    reader can check the arithmetic; ``sem`` is the combined standard error of
    the difference the primary measure rests on.
    """

    block: str
    block_id: int
    perfect: float | None
    failed: float | None
    birnbaum: float | None
    raw: float | None
    rrw: float | None
    vesely_fussell: float | None
    sem: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "block": self.block,
            "block_id": self.block_id,
            "perfect": self.perfect,
            "failed": self.failed,
            "birnbaum": self.birnbaum,
            "raw": self.raw,
            "rrw": self.rrw,
            "vesely_fussell": self.vesely_fussell,
            "sem": self.sem,
        }


def variants_for(mode: str) -> tuple[str, ...]:
    """The variant kinds *mode* needs ("perfect"/"failed"), in run order."""
    try:
        return _MODE_VARIANTS[mode]
    except KeyError:
        raise ValueError(
            f"unknown importance mode {mode!r}; expected one of "
            f"{', '.join(MODES)}") from None


def batch_count(n_blocks: int, mode: str) -> int:
    """Batches *mode* needs for a model with *n_blocks* blocks, baseline
    included."""
    return 1 + n_blocks * len(variants_for(mode))


def estimate_runs(n_blocks: int, mode: str, runs: int) -> int:
    """Total simulated runs *mode* costs: ``batches * runs``."""
    return batch_count(n_blocks, mode) * int(runs)


def block_rows(rbd) -> list[tuple[int, str]]:
    """``(id, name)`` of every real block, in model order.

    Excludes the ``ID=0`` sentinel row every Raptor file carries.
    """
    out: list[tuple[int, str]] = []
    for row in rbd.section("BLOCK_DATA").rows:
        if str(row.get("ID", "")) == "0":
            continue
        out.append((int(float(row["ID"])), str(row.get("blockName", ""))))
    return out


def run_length_of(rbd) -> float:
    """The model's simulated run length (`SYSTEM_DATA.TermCondition`)."""
    rows = rbd.section("SYSTEM_DATA").rows
    try:
        return float(rows[0]["TermCondition"])
    except (KeyError, IndexError, TypeError, ValueError):
        return 0.0


def _write_dist(row: dict[str, str], prefix: str, code: int,
                values: Sequence[float]) -> None:
    """Set one distribution's code, parameter count and its three columns."""
    n = len(values)
    row[f"{prefix}Dist"] = str(code)
    row[f"#{prefix}Param"] = str(n)
    for i in range(1, 4):
        row[f"{prefix}Param{i}"] = (f"{values[i - 1]:.6f}" if i <= n
                                    else "0.000000")


def variant(rbd, block_id: int, kind: str, run_length: float):
    """A copy of *rbd* with one block made *kind* (``"perfect"``/``"failed"``).

    Only the ``BLOCK_DATA`` row changes, so the variant is the same model in
    every respect the measure is supposed to hold fixed.
    """
    if kind not in ("perfect", "failed"):
        raise ValueError(f"unknown variant kind {kind!r}")
    out = copy.deepcopy(rbd)
    for row in out.section("BLOCK_DATA").rows:
        if str(row.get("ID", "")) != str(block_id):
            continue
        if kind == "perfect":
            # Beyond the end of every run: this block can never fail.
            cutoff = (run_length if run_length > 0 else 1.0e9) + 1.0
            _write_dist(row, "F", _D_FIXED, [cutoff])
        else:
            _write_dist(row, "F", _D_FIXED, [0.0])
            _write_dist(row, "R", _D_NONE, [])
        return out
    raise KeyError(f"no BLOCK_DATA row with ID {block_id}")


def _present(values) -> list[float]:
    return [v for v in values if v is not None and math.isfinite(v)]


def _mean(values) -> float | None:
    present = _present(values)
    return sum(present) / len(present) if present else None


def _sem(values) -> float | None:
    present = _present(values)
    if len(present) < 2:
        return None
    mean = sum(present) / len(present)
    var = sum((v - mean) ** 2 for v in present) / (len(present) - 1)
    return math.sqrt(var / len(present))


def _metric_of(report_data, attr: str) -> list[float | None]:
    return [getattr(r, attr, None) for r in report_data or ()]


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den <= 0.0:
        return None
    return num / den


def _combine(*sems: float | None) -> float | None:
    """Unpaired combined standard error, or ``None`` if any part is unknown."""
    if any(s is None for s in sems):
        return None
    return math.sqrt(sum(float(s) ** 2 for s in sems if s is not None))


def _row(bid: int, name: str, *, base: Sequence[float | None],
         perfect: Sequence[float | None] | None,
         failed: Sequence[float | None] | None) -> Row:
    """One block's measures from its three batches' per-run values."""
    a0 = _mean(base)
    sem0 = _sem(base)
    p = _mean(perfect) if perfect is not None else None
    sp = _sem(perfect) if perfect is not None else None
    f = _mean(failed) if failed is not None else None
    sf = _sem(failed) if failed is not None else None
    u0 = (1.0 - a0) if a0 is not None else None
    birnbaum = (p - f) if (p is not None and f is not None) else None
    if birnbaum is not None:
        sem = _combine(sp, sf)
    elif p is not None:
        sem = _combine(sp, sem0)
    elif f is not None:
        sem = _combine(sf, sem0)
    else:
        sem = None
    return Row(
        block=name, block_id=bid, perfect=p, failed=f, birnbaum=birnbaum,
        raw=_ratio((1.0 - f) if f is not None else None, u0),
        rrw=_ratio(u0, (1.0 - p) if p is not None else None),
        vesely_fussell=_ratio(
            (p - a0) if (p is not None and a0 is not None) else None, u0),
        sem=sem,
    )


def analyse(rbd, baseline, *, mode: str = "both", runs: int | None = None,
            workers: int = 1, seed: int | None = None,
            progress: Callable[[int, int, str], None] | None = None,
            stop_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Run the variants and return every metric's measures.

    *baseline* is the ordinary batch's report data -- the run the user just
    did -- so the baseline costs no extra simulation. Variant batches run with
    logging and plots off: those are passive (``costs.py``, ``capacity.py`` and
    ``reporting.py`` draw no random numbers), so switching them off cannot
    move a number, and it keeps an importance run from writing dozens of event
    logs.

    Returns ``{"mode", "runs", "batches", "blocks", "metrics": {label:
    {"baseline", "rows"}}, "notes"}``.
    """
    kinds = variants_for(mode)
    names = block_rows(rbd)
    if not kinds:
        return {"mode": mode, "runs": 0, "batches": 0, "blocks": len(names),
                "metrics": {}, "notes": []}
    if not baseline:
        raise ValueError("importance needs the baseline batch's report data")
    n_runs = int(runs or len(baseline))
    total = batch_count(len(names), mode)
    variants = _run_variants(
        rbd, kinds, names, runs=n_runs, length=run_length_of(rbd),
        workers=workers, seed=seed, progress=progress,
        stop_check=stop_check, total=total)
    metrics = _metric_rows(baseline, variants, names)
    return {"mode": mode, "runs": n_runs, "batches": total,
            "blocks": len(names), "metrics": metrics,
            "notes": _sampling_notes(metrics)}


def _run_variants(rbd, kinds: Sequence[str], names, *, runs: int, length: float,
                  workers: int, seed: int | None, progress, stop_check,
                  total: int) -> dict[str, dict[int, list[Any]]]:
    """Run one batch per block per measure, reporting completed batches."""
    from raptor.core.engine.runner import run_simulation

    variants: dict[str, dict[int, list[Any]]] = {k: {} for k in kinds}
    done = 1                      # the baseline counts as batch 1
    for kind in kinds:
        for bid, name in names:
            if stop_check is not None and stop_check():
                raise RuntimeError("importance analysis cancelled")
            sink: list[Any] = []
            run_simulation(variant(rbd, bid, kind, length), num_runs=runs,
                           num_workers=workers, seed=seed,
                           collect_logs=False, _report_sink=sink,
                           stop_check=stop_check)
            variants[kind][bid] = sink
            done += 1
            if progress is not None:
                progress(done, total, f"{kind}:{name}")
    return variants


def _metric_rows(baseline, variants: dict[str, dict[int, list[Any]]],
                 names) -> dict[str, Any]:
    """Every metric's baseline mean and per-block measure rows."""
    metrics: dict[str, Any] = {}
    for attr, label in METRICS:
        base = _metric_of(baseline, attr)
        rows = [
            _row(bid, name, base=base,
                 perfect=(_metric_of(variants["perfect"][bid], attr)
                          if "perfect" in variants else None),
                 failed=(_metric_of(variants["failed"][bid], attr)
                         if "failed" in variants else None))
            for bid, name in names
        ]
        metrics[label] = {"baseline": _mean(base),
                          "rows": [r.to_dict() for r in rows]}
    return metrics


def _sampling_notes(metrics: dict[str, Any]) -> list[str]:
    """The caveats a reader needs to not over-read a noisy ranking."""
    notes: list[str] = []
    # A measure at or below zero is "no measurable effect", not "harmful".
    # The sign on a block that does not gate the system is sampling noise
    # (a variant batch draws its own numbers, so it is not a paired
    # comparison), and reading RRW < 1 literally would say "fixing this
    # block makes the system worse", which is not what happened.
    for label, entry in metrics.items():
        rows = entry.get("rows") or []
        if any((r.get("vesely_fussell") is not None
                and r["vesely_fussell"] <= 0.0) for r in rows):
            notes.append(
                f"{label}: a Vesely-Fussell value at or below 0 (and RRW at "
                f"or below 1) means no measurable effect. The sign on those "
                f"blocks is sampling noise, not evidence that fixing them "
                f"would make the system worse.")
            break

    if all(entry["baseline"] in (None, 1.0) for entry in metrics.values()):
        notes.append(
            "The baseline never failed, so RAW, RRW and Vesely-Fussell are "
            "undefined: there is no unavailability to reduce or achieve.")
    elif all((entry["baseline"] or 0.0) > 0.999
             for entry in metrics.values() if entry["baseline"]):
        # A near-perfect baseline makes every ratio a ratio of noise: RAW
        # explodes (u_failed / 0.0004) and RRW can come out below 1, which
        # reads as "improving this block would make things worse".
        notes.append(
            "The baseline was almost never down, so these ratios rest on a "
            "handful of failure events and the ranking is mostly sampling "
            "noise. Raise the trials, lengthen the run, or trust the "
            "standard error instead of the order.")
    return notes


#: Order in which a ranking falls back when a measure is undefined. Birnbaum
#: is NOT the first choice: in a series system every block's variant moves the
#: system from ~0 to ~1, so Birnbaum saturates near 1 for all of them and ranks
#: nothing. Vesely-Fussell ("how much of the downtime is this block's fault")
#: separates them, and RRW agrees with it.
RANKING_ORDER: tuple[str, ...] = ("vesely_fussell", "rrw", "birnbaum", "raw")

RANKING_LABELS = {
    "vesely_fussell": "Vesely-Fussell",
    "rrw": "RRW",
    "birnbaum": "Birnbaum",
    "raw": "RAW",
}


def ranking_measure(rows: Sequence[dict[str, Any]]) -> str:
    """The measure a table of *rows* is best ranked by (see RANKING_ORDER)."""
    for key in RANKING_ORDER:
        if any(r.get(key) is not None for r in rows):
            return key
    return RANKING_ORDER[0]


def ranked(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """*rows* sorted by `ranking_measure`, largest first, undefined last."""
    key = ranking_measure(rows)
    return sorted(rows, key=lambda r: (r.get(key) is None,
                                       -(r.get(key) or 0.0),
                                       r.get("block", "")))


def _fmt(value: float | None, places: int = 4) -> str:
    return "—" if value is None else f"{value:,.{places}f}"


def table_lines(analysis: dict[str, Any], metric: str | None = None,
                *, limit: int | None = None) -> list[str]:
    """The report section: one table per metric, ranked by Birnbaum.

    Written into the run directory as `importance.txt`, so the same text is
    available through `get_report`, the exporters and the MCP resource.
    """
    if not analysis or not analysis.get("metrics"):
        return ["Importance measures were not calculated for this run."]
    labels = ([metric] if metric else list(analysis["metrics"]))
    out: list[str] = []
    for label in labels:
        entry = analysis["metrics"].get(label)
        if entry is None:
            continue
        rows = entry["rows"]
        measure_key = ranking_measure(rows)
        ordered = ranked(rows)
        if limit is not None:
            ordered = ordered[:limit]
        out.append(f"***IMPORTANCE_{label.upper()}***")
        out.append("")
        out.append(f"Baseline {label.lower()}: {_fmt(entry['baseline'])}   "
                   f"batches: {analysis['batches']}   "
                   f"trials per batch: {analysis['runs']}   "
                   f"ranked by: {RANKING_LABELS[measure_key]}")
        out.append("")
        out.append(f"{'Block':<24}{'Birnbaum':>11}{'RAW':>9}{'RRW':>7}"
                   f"{'Vesely-Fussell':>16}{'SEM':>9}")
        for r in ordered:
            out.append(
                f"{r['block'][:23]:<24}"
                f"{_fmt(r['birnbaum']):>11}"
                f"{_fmt(r['raw'], 2):>9}"
                f"{_fmt(r['rrw'], 2):>7}"
                f"{_fmt(r['vesely_fussell'], 3):>16}"
                f"{_fmt(r['sem']):>9}")
        out.append("")
        out.extend(analysis.get("notes", []))
        out.append("")
    return out

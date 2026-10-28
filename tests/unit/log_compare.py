"""The ONE line-pair classifier the end-to-end replay tests share.

Eleven test modules diff a generated report/event-log against a captured
legacy file. Each of them used to carry its own private copy of the same
comparator, and they did not agree:

* eight copies used a flat ``abs(fa - fb) > 1.5e-6``;
* three copies used ``abs(fa - fb) > max(1.5e-6, abs(fb) * 2e-6)``.

Two tests could therefore call the same large-magnitude draw either
fp32-noise (pass) or a structural regression (fail). The magnitude-scaled
form is the one that survives contact with the captures: SpaceStation's
larger-magnitude Gamma/Pearson5 draws leave residuals of a few 1e-6, and
those are float32 rounding whose size scales with the value itself. A flat
absolute tolerance is too tight for them and far too loose for a
large-magnitude *regression*.

So the scaled form is the single rule, and every replay test imports it
from here. The helpers below are the other half of the same duplication --
a dozen re-derivations of "read a latin-1, CRLF-terminated legacy capture
and split it into runs".

`tests/__init__.py` and `tests/unit/__init__.py` both exist, so sibling
imports (`from tests.unit.log_compare import classify`) work; the earlier
"duplicated locally since pytest's default import mode doesn't expose
sibling test modules as importable packages" note in `test_reports.py` was
simply wrong for this repo -- `test_block_lifecycle.py` was already
importing `tests.unit.single_block` that way.
"""
from __future__ import annotations

import gzip
import re
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import NamedTuple

import pytest

#: The numeric field pattern shared by every event-log and report line.
_NUM_RE = re.compile(r"[\d.]+")

class Comparison(NamedTuple):
    """Per-bucket tally of one line-by-line diff.

    A NamedTuple so a caller keeps the shape the per-file `_compare`
    helpers used to return: ``counts, first = compare_runs(mine, theirs)``.
    """

    counts: dict[str, int]
    first_structural: int | None


#: Absolute floor of the tolerance, in the same units as a log line's
#: 6-decimal numeric fields.
ABS_TOL = 1.5e-6
#: Relative term of the tolerance: float32 carries ~7 decimal digits, so
#: the last couple of ulps of a value of magnitude M are ~M * 1e-7. 2e-6
#: of M is a deliberately looser band that still refuses a real
#: large-magnitude regression.
REL_TOL = 2e-6

#: The two run-boundary landmarks a legacy capture uses, named so each
#: call site has to declare which one it means. They are not
#: interchangeable: an `End_sim` log is per-block (the orchestrator emits
#: it), a `Begin_Run` log is whole-simulation (system orchestration emits
#: it).
LANDMARK_END_SIM = "End_sim"
LANDMARK_BEGIN_RUN = "Begin_Run"


def classify(mine: str, theirs: str) -> str:
    """Classify a line pair as ``match`` / ``fp32-noise`` / ``structural``.

    ``match``: byte-identical.
    ``fp32-noise``: same non-numeric text, same number of numeric fields,
    and every numeric field within the magnitude-scaled tolerance.
    ``structural``: anything else -- a different number of numeric fields,
    a differing non-numeric field, or a numeric field outside tolerance.
    """
    if mine == theirs:
        return "match"
    mine_nums = _NUM_RE.findall(mine)
    their_nums = _NUM_RE.findall(theirs)
    if len(mine_nums) != len(their_nums):
        return "structural"
    for a, b in zip(mine_nums, their_nums):
        try:
            fa, fb = float(a), float(b)
        except ValueError:
            if a != b:
                return "structural"
            continue
        if abs(fa - fb) > max(ABS_TOL, abs(fb) * REL_TOL):
            return "structural"
    return "fp32-noise"


def compare_runs(mine: Sequence[str], theirs: Sequence[str]) -> Comparison:
    """Diff two line sequences with :func:`classify`.

    Only the first ``min(len(mine), len(theirs))`` lines are compared --
    the line-count check stays the caller's, because "too few" and "too
    many" are different failures and each test words its own assertion.
    """
    counts = {"match": 0, "fp32-noise": 0, "structural": 0}
    first_structural: int | None = None
    for i in range(min(len(mine), len(theirs))):
        kind = classify(mine[i], theirs[i])
        counts[kind] += 1
        if kind == "structural" and first_structural is None:
            first_structural = i
    return Comparison(counts=counts, first_structural=first_structural)


def read_capture_bytes(path: Path | str) -> bytes:
    """A legacy capture's bytes. Captures over 1 MB are stored gzipped, so a
    plain path whose file exists only as ``<path>.gz`` is read from there,
    and a ``.gz`` is decompressed."""
    path = Path(path)
    if not path.exists() and path.suffix != ".gz":
        path = path.with_name(path.name + ".gz")
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as f:
            return f.read()
    return path.read_bytes()


def read_capture_text(path: Path | str, encoding: str = "latin-1") -> str:
    """`read_capture_bytes`, decoded (latin-1 keeps every byte)."""
    return read_capture_bytes(path).decode(encoding)


def read_lines(path: Path | str, *,
               keep: Callable[[str], bool] | None = None,
               required: bool = True) -> list[str]:
    """Read a legacy capture as a list of decoded lines.

    Captures are latin-1 (byte-preserving, so every byte survives) and
    CRLF-terminated, so they are split on ``b"\\r\\n"`` rather than
    ``splitlines()`` -- a lone ``\\r`` inside a field is data, not a line
    break. A ``.gz`` capture is decompressed first, and a plain path whose
    file is stored gzipped (``<path>.gz``) is read from there.

    ``keep`` filters whole lines (e.g. block-only lines). It is applied
    after decoding; latin-1 is a bijection on bytes, so a byte-level
    ``b"Block" in line`` test and a str-level ``"Block" in line`` test
    select exactly the same lines.

    A missing capture is a ``pytest.skip`` when ``required`` (it is an
    optional fixture, not a failure) and an ``OSError`` otherwise.
    """
    path = Path(path)
    if not path.exists() and not path.with_name(path.name + ".gz").exists():
        if required:
            pytest.skip(f"golden fixture not present: {path}")
        raise FileNotFoundError(path)
    raw = read_capture_bytes(path)
    lines = [l.decode("latin-1") for l in raw.split(b"\r\n")]
    return [l for l in lines if keep(l)] if keep is not None else lines


def split_runs(lines: Sequence[str], landmark: str, *,
               num_runs: int | None = None,
               max_runs: int | None = None,
               keep_blank: bool = False) -> list[list[str]]:
    """Split a capture into per-run line lists at ``landmark``.

    Two shapes, both driven off one landmark parameter:

    * ``landmark=LANDMARK_BEGIN_RUN`` -- a *whole-simulation* log. The
      landmark line itself is the run header and is dropped; each run runs
      from just after one landmark up to the next (the last one to EOF).
      Use ``num_runs=`` to assert how many the capture holds.
    * ``landmark=LANDMARK_END_SIM`` -- a *per-block* log. There is no
      header; the landmark line is the run's own ``End_sim`` terminator
      and IS kept. Use ``max_runs=`` to stop early.

    Blank lines are dropped unless ``keep_blank``: they carry no
    structure and their count varies with the capture's trailing
    newlines.
    """
    hits = [i for i, l in enumerate(lines) if landmark in l]

    if landmark == LANDMARK_END_SIM:
        # Landmark is a terminator, not a header: a run opens at the first
        # line (or just after the previous run's terminator) and closes ON
        # the terminator, which is kept. A trailing partial run that never
        # terminates is dropped, and max_runs stops the scan early.
        runs: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            current.append(line)
            if landmark in line:
                runs.append(current)
                current = []
                if max_runs is not None and len(runs) >= max_runs:
                    break
    else:
        if num_runs is not None and len(hits) != num_runs:
            raise AssertionError(
                f"expected {num_runs} runs at {landmark!r}, "
                f"found {len(hits)}")
        if max_runs is not None:
            hits = hits[:max_runs]
        ends = hits[1:] + [len(lines)]
        # The landmark line is the run's header, so it is dropped: a run
        # is everything between one landmark and the next, the last to EOF.
        runs = [lines[s + 1:e] for s, e in zip(hits, ends)]

    if not keep_blank:
        runs = [[l for l in run if l.strip()] for run in runs]
    return runs


def strip_trailing_blank_ish(lines: Sequence[str]) -> list[str]:
    """Drop trailing whitespace-only lines (report trailing-newline noise)."""
    out = list(lines)
    while out and out[-1].strip() == "":
        out.pop()
    return out
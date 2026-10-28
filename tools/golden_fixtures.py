"""Shared fixture-resolution helpers for the verification-coverage work
-- used by `regen_golden_ours.py`,
`tests/unit/test_golden_fidelity.py`, and `tests/unit/test_sd_diff_sweep.py`
so the "where is this fixture's .rbd" logic (and its exceptions) lives in
exactly one place instead of being re-derived per script.

**Not every `tests/golden/<name>/` directory ships its own `input.rbd`.**
A batch of fixtures (mostly the `ReleaseProbeN`-era and `*Probe`
single-mechanism probes) keep their canonical `.rbd` under
`tests/fixtures/<name>/input.rbd` instead, with only a `legacy/`
capture under `tests/golden/`. `resolve_rbd_path` checks both locations,
plus a small alias table for the two names that don't even match
(`CustExhaust` <-> `Cust_Exhaust`).

**`RESPOOL` is a phantom fixture, discovered while building this module
2026-09-08**: `tests/golden/RESPOOL/legacy/` exists and is completely
empty (no capture files at all) and no `.rbd` for it exists anywhere in
the repo. It is excluded from every helper below and flagged separately
by `verification_report.py` -- not a bug to silently paper over, a gap
to report.

**A capture can also end up orphaned from its fixture** (found and
resolved 2026-09-11): `ColdStandbyProbe`'s whole `legacy/` set was
provably `HierarchyExample`'s, because that probe's `.rbd` had been
overwritten with its sibling's. See `MISFILED_LEGACY_CAPTURE` below for
the proofs, the root cause and the resolution. The general lesson: when
a fixture's real draw count is wildly out of proportion to ours, check
WHOSE capture it is before theorising about the engine -- run the
suspect dump against every candidate `.rbd` in the family and look for a
byte-exact match, which is how this one was settled in one pass.
"""
from __future__ import annotations

import gzip
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "golden"
RBD_TEST_FIXTURES = REPO / "tests" / "fixtures"

#: fixture name (as it appears under tests/golden/) -> directory name
#: under tests/fixtures/, for the handful that don't match verbatim.
_NAME_ALIASES = {
    "CustExhaust": "Cust_Exhaust",
}

#: `tests/golden/<name>/` directories confirmed to carry NO usable
#: capture data at all (empty legacy/, no .rbd anywhere) -- see the
#: module docstring. Every helper below treats these as absent.
PHANTOM_FIXTURES = frozenset({"RESPOOL"})

#: Fixtures whose ENTIRE `legacy/` capture set is PROVEN to be a
#: DIFFERENT model's, so none of it may be compared against this
#: fixture's own `.rbd`. `simdetails_path` returns None for these and
#: `legacy_capture_is_foreign` lets the report-axis tests skip them --
#: the same "report the gap, don't paper over it" treatment
#: `PHANTOM_FIXTURES` gets, and deliberately NOT a deletion of the files
#: (they are real captured data, just filed under the wrong name).
#:
#: Empty is the intended end state: a misfiled
#: capture should be REATTRIBUTED to the fixture it actually belongs to,
#: not left excluded. This set is the holding pen for the interval
#: between "proven foreign" and "correctly refiled".
#:
#: Worked example, `ColdStandbyProbe` (found and resolved 2026-09-11):
#: its whole `legacy/` set was `HierarchyExample`'s. Three independent
#: proofs: (1) its `simDetails` was 578 draws over 10 runs ending at
#: `t=730.363727`, which this port's `HierarchyExample` run reproduces
#: BYTE-EXACT (578/578, no divergence) while `ColdStandbyProbe`'s own
#: `.rbd` produced 2538; (2) `rpt_events_ColdStandbyProbe.txt` was
#: BYTE-IDENTICAL to `rpt_events_hierarchy.txt` (2126 lines, differing
#: only in the header line naming the source `.rbd`); (3) its `.rbd` was
#: byte-identical (same sha1, same mtime) to `ColdStandbyProbe2`'s,
#: whose capture is 2376 draws -- one model cannot have two different
#: deterministic captures. ROOT CAUSE, from
#: the internal investigation log's 2026-08-27 entry: the two probes were
#: built as DIFFERENT models ("ColdStandbyProbe2 adds exp-MTBF-20h/
#: fixed-1h-repair comm units to force repeated switch attempts"), and
#: on 2026-08-29 `ColdStandbyProbe/input.rbd` was overwritten with
#: `ColdStandbyProbe2`'s content -- orphaning the capture rather than
#: misfiling it in the first place. RESOLUTION: the capture was moved to
#: `tests/golden/HierarchyExample/legacy/` (which had only an event log
#: before, and now has `/sd` + EOR + FR), and the duplicate fixture was
#: removed. Net effect was a coverage GAIN: `HierarchyExample` -- the
#: fixture the whole cold-standby module was originally built against --
#: now has a byte-exact `/sd` tripwire in the sweep.
#:
#: `ReleaseProbe4` (2026-09-23) is the exception that cannot be
#: reattributed. Its capture (18:42, 2026-09-01) is of the probe's FIRST
#: build, which had an `n=1` node `join2`; the probe was rebuilt without
#: that node at 19:01 the same day,
#: and that first build was never committed. The capture's own rows name
#: `join2`, which no version of the file in git has. (The rebuilt file was
#: itself corrupt -- duplicate block IDs 3/4 and links to non-existent
#: blocks 7/8 -- and was repaired through the builder on 2026-09-23.) The
#: capture files stay as real data; the probe needs recapturing.
MISFILED_LEGACY_CAPTURE: frozenset[str] = frozenset()


def resolve_rbd_path(name: str) -> Path | None:
    """The .rbd this fixture should be simulated from, or None if no
    source model can be found anywhere this module knows to look."""
    if name in PHANTOM_FIXTURES:
        return None
    local = GOLDEN / name / "input.rbd"
    if local.exists():
        return local
    alt_name = _NAME_ALIASES.get(name, name)
    alt = RBD_TEST_FIXTURES / alt_name / "input.rbd"
    if alt.exists():
        return alt
    return None


def has_real_legacy_capture(name: str) -> bool:
    """True if `tests/golden/<name>/legacy/` exists and contains at
    least one file (excludes PHANTOM_FIXTURES and any other directory
    that turns out to be similarly empty)."""
    if name in PHANTOM_FIXTURES:
        return False
    legacy = GOLDEN / name / "legacy"
    return legacy.is_dir() and any(legacy.iterdir())


def all_fixture_names() -> list[str]:
    return sorted(d.name for d in GOLDEN.iterdir() if d.is_dir())


def fixtures_with_legacy() -> list[str]:
    return [n for n in all_fixture_names() if has_real_legacy_capture(n)]


def capture(path: Path | str) -> Path:
    """`path`, or its gzipped twin `path.gz` when only that exists. The
    captures over 1 MB are stored gzipped; read them with
    `read_capture_bytes`/`read_capture_text`, which accept either."""
    path = Path(path)
    if path.exists() or path.suffix == ".gz":
        return path
    twin = path.with_name(path.name + ".gz")
    return twin if twin.exists() else path


def read_capture_bytes(path: Path | str) -> bytes:
    """A capture's bytes, decompressing a `.gz` (see `capture`)."""
    path = capture(path)
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as f:
            return f.read()
    return path.read_bytes()


def read_capture_text(path: Path | str, encoding: str = "latin-1") -> str:
    """A capture's text (latin-1 by default: byte-preserving)."""
    return read_capture_bytes(path).decode(encoding)


def single_match(legacy_dir: Path, pattern: str) -> Path | None:
    """The one file under `legacy_dir` matching glob `pattern` (as is or
    gzipped), or None if zero or more-than-one match (a bespoke rule is
    needed for the latter case, not a generic script)."""
    found: dict[str, Path] = {}
    for p in sorted(legacy_dir.glob(pattern)) + sorted(legacy_dir.glob(pattern + ".gz")):
        found.setdefault(p.name.removesuffix(".gz"), p)
    return next(iter(found.values())) if len(found) == 1 else None


def legacy_capture_is_foreign(name: str) -> bool:
    """True if this fixture's whole `legacy/` set belongs to a different
    model -- see `MISFILED_LEGACY_CAPTURE`. Callers comparing ANY axis
    (reports as well as `/sd`) against `legacy/` must skip these."""
    return name in MISFILED_LEGACY_CAPTURE


def simdetails_path(name: str) -> Path | None:
    """A fixture's real `/sd` draw capture, or None. Two real naming
    conventions coexist in `tests/golden/`: the plain `simDetails.TXT`
    (older captures) and `simDetails_<Name>.txt` (newer ones, name
    matching the fixture -- occasionally NOT verbatim, e.g.
    `MultiOutLink/legacy/simDetails_MultiOutLink1.txt`, so this falls
    back to "the one simDetails* file present" rather than requiring an
    exact name match)."""
    if name in PHANTOM_FIXTURES or name in MISFILED_LEGACY_CAPTURE:
        return None
    legacy = GOLDEN / name / "legacy"
    if not legacy.is_dir():
        return None
    plain = capture(legacy / "simDetails.TXT")
    if plain.exists():
        return plain
    return single_match(legacy, "simDetails*")

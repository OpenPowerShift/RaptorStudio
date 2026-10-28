"""ER3-05 -- `repairDistro=18` (NONE) must not skip `CheckForSpare`, for
EITHER sparing type (the ER3 label key is the appendix of the internal engine ledger;
see also the internal engine ledger's D.1 item for the related, NOT-yet-acted-
on first-failure-grace question this uncovered).

`NoRepairCheckForSpareProbe` (2026-09-19, real-captured, Custom sparing) had
sat with no test exercising it since it was built -- this file gives it one.
`NoRepairCheckForSparePooledProbe` (2026-09-23, real-captured, Pooled
sparing) is new: `RepairNoneSpareOrderProbe` (`tests/fixtures/`, same day)
tried to pin the SparePool half of this question first, on a cloned 10-block
satellite model, and crashed real Raptor on open (reproduced twice; a
`HierarchyExample` control launch in the same session opened cleanly,
proving it fixture-specific) -- abandoned in favour of this much simpler,
same-recipe-as-the-already-proven-safe-Custom-probe rebuild. See both
fixtures' own READMEs for detail.

Both real captures agree: `CheckForSpare`'s own `No_spare_in_stock-<pool>`
line logs BEFORE `Done No_repair`, for Custom AND Pooled sparing alike --
the port (before this fix) checked `repairDistro==NONE` first and returned,
skipping `CheckForSpare` entirely for BOTH sparing types. Fixed in
`block_lifecycle.py`'s `block_run_process` (the scheduler-driven generator
`run_simulation` actually uses) by moving each sparing type's own
`CheckForSpare`-equivalent (Part A: check stock, log, ESO-order) to run
BEFORE the `repairDistro==NONE` check, mirroring the Custom branch's own
existing structure; the SparePool branch's Part B (the seam boundary and the
actual grab, which is Repair-body work) stays gated behind it, since no
Repair spawns when the block is about to go straight to Done/No_repair.
"""
from __future__ import annotations

import pathlib

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.builder import load

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _rows(fixture: str) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    """(ours, real) row tuples (first 5 tab-separated columns), Begin_Run
    marker lines dropped (a harness artifact, not an ordering fact)."""
    real_lines = [
        l.rstrip("\r\n") for l in
        (GOLDEN / fixture / "legacy" / f"rpt_events_{fixture}.txt")
        .read_text(encoding="latin-1").splitlines()
        if "\t" in l
    ][1:]  # drop the header row
    rbd = load(str(GOLDEN / fixture / "input.rbd"))
    ours_lines = [l for run in run_simulation(rbd) for l in run if "\t" in l]

    def _to_rows(lines: list[str]) -> list[tuple[str, ...]]:
        rows = [tuple(c.strip() for c in l.split("\t")[:5]) for l in lines]
        return [r for r in rows if not r[2].startswith("Begin_Run")]

    return _to_rows(ours_lines), _to_rows(real_lines)


def test_no_repair_check_spare_probe_custom_matches_real() -> None:
    """Custom sparing: `Sparing No_spare_in_stock-Custom` +
    `Sparing Spare_ordered_(stock=0)` both log before `Done No_repair`."""
    ours, real = _rows("NoRepairCheckForSpareProbe")
    assert ours == real


def test_no_repair_check_spare_pooled_probe_matches_real() -> None:
    """Pooled sparing: `Sparing No_spare_in_stock-<pool>` logs before
    `Done No_repair`, same as Custom -- the ER3-05 fix itself -- and, since
    the same day's D.1 revision, byte-exact: both `Pool ... Spare_ordered`
    lines fire at this system-first failure (the old "system's first failure
    is silent" grace was wrong; the grace is "a same-instant grant emptied
    the pool"), queued as pool-owned TELLs that take their turn after the
    blocks' own (owner-group calendar), not ahead of them."""
    ours, real = _rows("NoRepairCheckForSparePooledProbe")
    assert ours == real

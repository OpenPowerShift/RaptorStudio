"""ER3-29's last piece, ER3-06, and ER3-04 -- all three settled by a live gdb
scheduler trace on 2026-09-22 (the ER3 label key is the appendix of the internal engine ledger;
see the internal engine ledger's dated entry for
the full trace protocol and validation checklist actually run).

All three fixtures were built via `tools/rbd_builder.py`, captured against
real Raptor under gdb, and validated non-
perturbing via the trace log's own marker-count/freshness checks before being
trusted.

- `InterruptTellCollideProbe` (ER3-29): a same-instant `Interrupt` against an
  independent TELL spawn -- the one same-instant-ordering shape ER3-29 still
  had unexercised. Byte-exact with NO engine change: the port's existing
  same-instant handling was already correct.
- `CS_DepOnStandbyPeer` (ER3-06): a `dep_type=RBDBlock` dependent watching a
  cold-standby block. Real revealed a genuine bug: `_dependent_block_process`
  skipped its own `op_accum`/`running_since` update specifically on
  `"Dependence_Standby"` (entering Standby), silently folding the whole
  Standby dwell time into the block's next `Operating_time=` once it was
  promoted back out -- fixed to update the same way `Dependence_All_Up`/
  `Dependence_SB_turnOn` already did.
- `DepCancelConservationProbe` (ER3-04): a block racing its own PM-prereq
  Custom-spare wait against an unrelated failure, contending for a second,
  shared RESOURCE_DATA pool. Real revealed the abandoned PM-side Custom-spare
  request was left queued (wrong `kind` tag meant it could never be found and
  withdrawn, unlike the sibling shared-resource-pool code's already-correct
  `pm_resource_pending`/`withdraw(...,"pm")` pairing) -- this let the block's
  later, unrelated Repair-path spare consumption get satisfied out of turn,
  which cascaded into the block obtaining a shared RESOURCE_DATA unit 17 time
  units before real does (real makes it wait for the actual holder's own
  release). Fixed by tagging the PM-side request `kind="pm"` and withdrawing
  it before the Repair path registers its own request, mirroring the
  resource-pool pattern exactly. The last residual (t=65) was FIXED
  2026-09-23: `DetermineNewState("Block Has Failed")` runs CheckForSpare only
  `IF (NOT spareObtained)`, and Repair skips its spare
  step on the same flag -- PmCustom still held the spare its t=63 PM
  attempt took before pmReqDefer'ing on Techs. The probe now matches real
  fully.
"""
from __future__ import annotations

import pathlib


from raptor.core.engine.runner import run_simulation
from tests.unit.log_compare import read_capture_text
from raptor.core.fileio.rbd import load_rbd

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"
FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


def _rows(fixture: str, real: bool) -> list[tuple[str, ...]]:
    if real:
        lines = [l.rstrip("\r\n") for l in
                 read_capture_text(GOLDEN / fixture / "legacy" / f"rpt_events_{fixture}.txt").splitlines()
                 if "\t" in l][1:]                                   # drop the header row
    else:
        rbd = load_rbd(str(FIXTURES / fixture / "input.rbd"))
        lines = [l for run in run_simulation(rbd) for l in run if "\t" in l]
    rows = [tuple(c.strip() for c in l.split("\t")[:5]) for l in lines]
    return [r for r in rows if not r[2].startswith("Begin_Run")]     # harness marker, not an ordering fact


def test_interrupt_tell_collide_probe_matches_real_fully() -> None:
    """ER3-29's last shape: a same-instant Interrupt vs an independent TELL
    spawn. Byte-exact across all 20 runs, no engine change needed."""
    assert _rows("InterruptTellCollideProbe", False) == _rows("InterruptTellCollideProbe", True)


def test_cs_dep_on_standby_peer_matches_real_fully() -> None:
    """ER3-06: dependent-lifecycle Standby-completion operating-time
    accounting. Byte-exact across all 5 runs after the op_accum fix."""
    assert _rows("CS_DepOnStandbyPeer", False) == _rows("CS_DepOnStandbyPeer", True)


def test_er3_32_dependence_standby_no_longer_crashes_block_run_process() -> None:
    """ER3-32: `block_run_process`'s five "benign dependency/switch
    transition, just resume" interrupt-reason checks all omitted
    `"Dependence_Standby"` (present at each site: `Dependence_Idle`,
    `Switched_On`, `Switched_Off`, `Dependence_All_Up`,
    `Dependence_SB_turnOn`, `Phase_Change`, `Switch_Failed_Path` -- every
    OTHER "already applied elsewhere, just continue" reason). A
    mixed-percentage `sysDepend` block (`Attitude`, unchanged from the base
    `CS_Probe` fixture, `dep_idle_percent=15.0`) reliably hit the resulting
    `else: raise AssertionError(resume)` catch-all once `CS_DepOnStandbyPeer`
    (ER3-06's own probe) ran past its own `NumRuns=5` default -- confirmed
    reproducible at `num_runs>=8` before this fix, and confirmed the
    ORIGINAL, unmodified `CS_Probe` never crashes at any run count, so this
    was a real, reachable engine bug, not a probe-construction artefact.

    NOT independently verified against a real capture beyond `num_runs=5`
    (the only captured range) -- this test only proves the crash is gone
    and the already-verified byte-exact range is undisturbed, matching the
    established pattern (`Dependence_Standby` treated identically to its
    already-verified siblings `Dependence_All_Up`/`Dependence_SB_turnOn`).
    """
    rbd = load_rbd(str(FIXTURES / "CS_DepOnStandbyPeer" / "input.rbd"))
    rows = run_simulation(rbd, num_runs=20)  # was: AssertionError(('interrupt', 'Dependence_Standby'))
    assert len(rows) == 20

    # The already-verified byte-exact range (the fixture's own committed
    # NumRuns=5) must be undisturbed by this fix.
    assert _rows("CS_DepOnStandbyPeer", False) == _rows("CS_DepOnStandbyPeer", True)


def test_dep_cancel_conservation_probe_matches_real_fully() -> None:
    assert _rows("DepCancelConservationProbe", False) == _rows("DepCancelConservationProbe", True)


def test_dep_cancel_conservation_probe_core_divergence_fixed() -> None:
    """The actual ER3-04 finding, isolated from the run-end residual above:
    PmCustom must wait for ResHolder's own resource release (t=53), not grab
    the shared pool 17 units early (t=36) via a stale abandoned-PM queue
    entry."""
    port = _rows("DepCancelConservationProbe", False)
    real = _rows("DepCancelConservationProbe", True)
    upto_61 = lambda rows: [r for r in rows if r[0] and float(r[0]) <= 61.0 + 1e-9]
    assert upto_61(port) == upto_61(real)
    # The specific fact: PmCustom's own resource grant lands at t=53, not t=36.
    grants = [r for r in port if r[2] == "PmCustom" and r[3] == "Sparing" and "Obtained" in r[4]]
    assert grants and grants[0][0] == "53.000000"

"""ER3-29 remainder: the PM-path and dependent-lifecycle Custom/SparePool
ESO `OrderASpare` spawns, both formerly plain synchronous `scheduler.tell()`
calls with no real fixture exercising them (ER3-29;
the ER3 label key is the appendix of the internal engine ledger).

Both fixtures were built via `tools/rbd_builder.py` (`PmCustomESOProbe`:
clone of `PMCustomSLOResourceDeferProbe` with Custom ESO instead of SLO;
`DepCustomESOProbe`: clone of `WakeOrderDependent` with its already-dependent
block given Custom ESO), then captured against real Raptor via a live gdb
scheduler trace -- not just a plain `/sd` run --
so the traced captures are confirmed non-perturbing
(an internal comparison script against an untraced run of the same
fixture, `IDENTICAL` both times) before being trusted as golden.

`DepCustomESOProbe` is byte-exact with NO engine change: the dependent path's
existing synchronous `scheduler.tell()` was already correct. `PmCustomESOProbe`
needed `deferred=True` on both the SparePool and Custom PM-path ESO spawns
(`block_lifecycle.py`) to match real's `pmReqDefer` (logged synchronously,
this turn) landing BEFORE `Sparing Spare_ordered` (the deferred TELL's own,
later selection) -- confirmed twice in one capture (t=10 and t=61).

One further real divergence, at t=34 (an extra `pmReqDefer
No_resource_in_stock-Techs` once the Custom spare had just arrived and PM
retried), was FIXED 2026-09-23: source logs that line only `IF ((NOT
FailWait) AND (NOT ZeroWait))`, and on the async
continuation after a deferred PM spare arrives the block's Run is back in a
wait. The probe now matches real fully.
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


def test_dep_custom_eso_probe_matches_real_fully() -> None:
    """Dependent-lifecycle ESO: the existing synchronous `tell()` was already
    correct -- byte-exact across all 20 runs, no engine change needed."""
    assert _rows("DepCustomESOProbe", False) == _rows("DepCustomESOProbe", True)


def _upto(rows, t):
    return [r for r in rows if r[0] and float(r[0]) <= t + 1e-9]


def test_pm_custom_eso_probe_pm_req_defer_precedes_sparing() -> None:
    """The specific ER3-29 fact, twice (t=10 and t=61): real logs pmReqDefer
    BEFORE Sparing/Spare_ordered -- the deferred-TELL order, not source's
    textual call order."""
    port, real = _rows("PmCustomESOProbe", False), _rows("PmCustomESOProbe", True)
    for t in (10.0, 61.0):
        acts = [(r[2], r[3]) for r in port if r[0] == f"{t:.6f}" and r[2] == "PmCustom"]
        assert [a for _b, a in acts].index("pmReqDefer") < [a for _b, a in acts].index("Sparing"), \
            f"port t={t}: {acts}"
        acts_real = [(r[2], r[3]) for r in real if r[0] == f"{t:.6f}" and r[2] == "PmCustom"]
        assert [a for _b, a in acts_real].index("pmReqDefer") < [a for _b, a in acts_real].index("Sparing"), \
            f"real t={t}: {acts_real}"


# Was a strict xfail (t=34 duplicate pmReqDefer on the PM retry after an
# async spare arrival); fixed 2026-09-23 -- source gates that line on
# NOT FailWait AND NOT ZeroWait.
def test_pm_custom_eso_probe_matches_real_fully() -> None:
    assert _rows("PmCustomESOProbe", False) == _rows("PmCustomESOProbe", True)

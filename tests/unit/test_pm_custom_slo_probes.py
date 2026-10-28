"""PM with Custom sparing and SLO-driven resource deferral.

A TELL spawned by `ObtainPMprereqs` starts as its own selection, and
`PerformPM` is itself a TELL boundary: `OrderStock` and `PerformPM` both
spawn from `ObtainPMprereqs` and start in creation order. When the PM is
deferred, `OrderStock`'s own start follows the deferring turn's row.

Fixtures `PMCustomSLOProbe` and `PMCustomSLOResourceDeferProbe` under
`tests/golden/` compare the port's event ordering against the real capture.
"""
from __future__ import annotations

import pathlib


from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _rows(fixture: str, real: bool) -> list[tuple[str, ...]]:
    if real:
        lines = [l.rstrip("\r\n") for l in
                 (GOLDEN / fixture / "legacy" / f"rpt_events_{fixture}.txt").read_text(encoding="latin-1").splitlines()
                 if "\t" in l][1:]                                   # drop the header row
    else:
        lines = [l for l in run_simulation(load_rbd(str(GOLDEN / fixture / "input.rbd")), num_runs=1)[0] if "\t" in l]
    rows = [tuple(c.strip() for c in l.split("\t")[:5]) for l in lines]
    return [r for r in rows if not r[2].startswith("Begin_Run")]     # harness marker, not an ordering fact


def test_pm_custom_slo_probe_matches_real() -> None:
    assert _rows("PMCustomSLOProbe", False) == _rows("PMCustomSLOProbe", True)


def _upto(rows, t):
    return [r for r in rows if r[0] and float(r[0]) <= t + 1e-9]


def test_pm_custom_slo_resource_defer_probe_t10_matches_real() -> None:
    port, real = _rows("PMCustomSLOResourceDeferProbe", False), _rows("PMCustomSLOResourceDeferProbe", True)
    assert _upto(port, 10.0) == _upto(real, 10.0)
    # and the specific fact: pmReqDefer precedes the order
    acts = [(r[2], r[3]) for r in _upto(real, 10.0) if r[2] == "PmCustom"]
    assert [a for _b, a in acts].index("pmReqDefer") < [a for _b, a in acts].index("Sparing")


def test_pm_custom_slo_resource_defer_probe_matches_real_fully() -> None:
    assert _rows("PMCustomSLOResourceDeferProbe", False) == _rows("PMCustomSLOResourceDeferProbe", True)

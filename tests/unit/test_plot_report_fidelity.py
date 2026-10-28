"""Capacity plot, availability (Ao) plot and interim cost report fidelity:
every real `rpt_capacity_plot*` / `rpt_availability_plot*` /
`rpt_interim_cost*` capture under tests/golden/*/legacy/ must match the
port's output line for line (trailing whitespace ignored).

One documented tolerance: in the interim cost report, `SystemLostFlowCost`
and `Grand_Total` may differ by at most 0.05. The lost-flow mean there is
float round-off sensitive: exact monitor times match real's interim values
to the cent, but the capacity monitor rounds its clock to 6 decimals
(`stats.py`), which FactoryDemo's Final Results `Units_Produced` std needs.
No single choice satisfies both; the 2026-09-25 corpus shows max 0.03
(the internal engine ledger, "plot and interim-cost reports").
`tools/plot_report_scan.py` is the exploratory version.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.builder import load

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "tools"))
import golden_fixtures as gf  # noqa: E402

GOLDEN = _REPO / "tests" / "golden"
KINDS = {"capacity": "rpt_capacity_plot*", "ao": "rpt_availability_plot*", "cost": "rpt_interim_cost*"}
_TOLERANT = ("SystemLostFlowCost", "Grand_Total")


def _cases() -> list[tuple[str, str]]:
    out = []
    for d in sorted(GOLDEN.iterdir()):
        legacy = d / "legacy"
        if not legacy.is_dir() or gf.legacy_capture_is_foreign(d.name):
            continue
        if gf.resolve_rbd_path(d.name) is None:
            continue
        out += [(d.name, k) for k, pat in KINDS.items() if gf.single_match(legacy, pat)]
    return out


def _norm(lines: list[str]) -> list[str]:
    out = [line.rstrip() for line in lines if not line.startswith("Interim Cost Report for")]
    while out and not out[-1]:
        out.pop()
    return out


_cache: dict[str, dict[str, list[str]]] = {}


def _ours(fixture: str) -> dict[str, list[str]]:
    if fixture not in _cache:
        sinks: dict[str, list[str]] = {"capacity": [], "ao": [], "cost": []}
        run_simulation_with_reports(
            load(str(gf.resolve_rbd_path(fixture))), date="PROBE",
            capacity_sink=sinks["capacity"], ao_sink=sinks["ao"], cost_sink=sinks["cost"])
        _cache.clear()  # one fixture at a time: keep memory flat
        _cache[fixture] = sinks
    return _cache[fixture]


@pytest.mark.parametrize(("fixture", "kind"), _cases())
def test_plot_and_interim_cost_match_real(fixture: str, kind: str) -> None:
    real = _norm(gf.read_capture_text(gf.single_match(GOLDEN / fixture / "legacy", KINDS[kind]))
                 .splitlines())
    ours = _norm(_ours(fixture)[kind])
    assert len(ours) == len(real), f"{fixture}/{kind}: {len(ours)} lines vs real {len(real)}"
    for k, (o, r) in enumerate(zip(ours, real)):
        if o == r:
            continue
        if kind == "cost" and r.startswith(_TOLERANT) and o.split()[0] == r.split()[0]:
            assert abs(float(o.split()[-1]) - float(r.split()[-1])) <= 0.05, (fixture, k, o, r)
            continue
        pytest.fail(f"{fixture}/{kind} line {k}: ours={o!r} real={r!r}")

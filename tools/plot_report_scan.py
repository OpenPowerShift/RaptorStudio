#!/usr/bin/env python3
"""Compare the port's capacity-plot, availability-plot and interim-cost
output against every real capture under tests/golden/*/legacy/.

The event-log scan (event_log_scan.py) and test_golden_fidelity cover the
event log and the End-of-Run / Final Results reports; these three outputs
had no whole-corpus check. Header lines carrying a date or file name are
skipped; every other line is compared after trailing-whitespace strip.

Usage (repo root): python3 tools/plot_report_scan.py [Fixture ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import golden_fixtures as gf  # noqa: E402
from raptor.core.engine.runner import run_simulation_with_reports  # noqa: E402
from raptor.core.fileio.builder import load  # noqa: E402

KINDS = {"capacity": "rpt_capacity_plot*", "ao": "rpt_availability_plot*", "cost": "rpt_interim_cost*"}


def _norm(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        line = line.rstrip()
        if line.startswith("Interim Cost Report for"):
            continue  # file name + wall-clock date
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return out


def main(only: set[str]) -> int:
    total = bad = 0
    for d in sorted((REPO / "tests" / "golden").iterdir()):
        fix = d.name
        if only and fix not in only:
            continue
        legacy = d / "legacy"
        caps = {k: gf.single_match(legacy, pat) for k, pat in KINDS.items()} if legacy.is_dir() else {}
        caps = {k: v for k, v in caps.items() if v}
        if not caps:
            continue
        rbd = gf.resolve_rbd_path(fix)
        if rbd is None or gf.legacy_capture_is_foreign(fix):
            continue
        sinks = {"capacity": [], "ao": [], "cost": []}
        try:
            run_simulation_with_reports(
                load(str(rbd)), date="PROBE",
                capacity_sink=sinks["capacity"] if "capacity" in caps else None,
                ao_sink=sinks["ao"] if "ao" in caps else None,
                cost_sink=sinks["cost"] if "cost" in caps else None,
            )
        except Exception as e:  # noqa: BLE001 - a scan, report and continue
            print(f"{fix}: ERROR {type(e).__name__}: {e}")
            bad += 1
            continue
        for kind, path in caps.items():
            total += 1
            real = _norm(gf.read_capture_text(path).splitlines())
            ours = _norm(sinks[kind])
            if real == ours:
                continue
            bad += 1
            j = next((k for k in range(min(len(real), len(ours))) if real[k] != ours[k]),
                     min(len(real), len(ours)))
            print(f"{fix} [{kind}]: differs @ line {j} (ours {len(ours)} real {len(real)})")
            print(f"   OURS {ours[j] if j < len(ours) else None!r}")
            print(f"   REAL {real[j] if j < len(real) else None!r}")
    print(f"TOTAL: {bad}/{total} captures differ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(set(sys.argv[1:])))

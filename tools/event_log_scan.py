#!/usr/bin/env python3
"""Corpus-wide event-log fidelity scan: for every golden fixture with a real
`legacy/rpt_events*.txt` capture, run the port and report how many runs are
byte-exact (first five tab-separated columns per line; `Begin_Run` markers
and untabbed lines such as `Started Gathering Statistics` are ignored).

Why this exists: `sd_diff` compares RNG draws and `test_golden_fidelity`
compares reports, so event-log TEXT goes unchecked for most fixtures -- on
2026-09-23 this scan found 880/1049 of Phasing1's `DependenceStress=` lines
wrong with every test green. Use it as the yardstick for any same-instant /
system-status change: a fix should lower the TOTAL and should never move an
existing run's first divergence earlier. Compare against a baseline by
running it once from a `git worktree` of the previous commit:

    python3 tools/event_log_scan.py > now.txt   # [Fixture ...] to filter
    RAPTOR_ROOT=/path/to/worktree python3 tools/event_log_scan.py > base.txt
    diff base.txt now.txt

2026-09-23 figures: 125/1690 runs differed at dfa3c3c, 66 after
f5f7d2c/caa9fff; 147/2113 once split-layout fixtures (input under
tests/fixtures/) and the imported ReleaseProbe captures were included.
The fully byte-exact subset is pinned by tests/unit/test_event_log_fidelity.py. Takes several minutes; don't run it alongside the pytest
engine tier (CPU contention trips conftest's silent 25 s watchdog).
"""
from __future__ import annotations

import glob
import os
import signal
import sys
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent.parent / "tests" / "golden"
sys.path.insert(0, os.environ.get("RAPTOR_ROOT", str(GOLDEN.parent.parent)))

from raptor.core.engine.runner import run_simulation  # noqa: E402

sys.path.insert(0, str(GOLDEN.parent.parent / "tools"))
import golden_fixtures  # noqa: E402
from raptor.core.fileio.builder import load  # noqa: E402


def _rows(lines):
    return [tuple(c.strip() for c in l.split("\t")[:5]) for l in lines if "\t" in l]


class _Timeout(Exception):
    pass


def _alarm(*_):
    raise _Timeout()


def main() -> int:
    signal.signal(signal.SIGALRM, _alarm)
    total = bad = 0
    only = set(sys.argv[1:])
    caps = glob.glob(str(GOLDEN / "*" / "legacy" / "rpt_events*.txt"))
    caps += glob.glob(str(GOLDEN / "*" / "legacy" / "rpt_events*.txt.gz"))   # captures over 1 MB
    for cap in sorted(caps):
        fix = Path(cap).parts[-3]
        if only and fix not in only:
            continue
        # The project resolver, not just GOLDEN/<fix>/input.rbd: split-layout
        # fixtures keep their input under tests/fixtures/ (e.g. the
        # ReleaseProbe series, captures imported 2026-09-23).
        rbd = golden_fixtures.resolve_rbd_path(fix)
        if rbd is None or not Path(rbd).exists():
            continue
        if golden_fixtures.legacy_capture_is_foreign(fix):
            continue  # capture proven to be another model's (golden_fixtures.MISFILED_LEGACY_CAPTURE)
        lines = [l.rstrip("\r\n") for l in golden_fixtures.read_capture_text(cap).splitlines()
                 if "\t" in l][1:]
        # Runs are keyed by the number in `Begin_Run_N`, not by position: a
        # capture whose head was overwritten (PmFailResetOff: a Final Results
        # report was written over the event log's first bytes, so the file
        # starts mid-run-1 at `Begin_Run_2`) must still compare run N with
        # run N. A leading partial run with no marker is skipped.
        real_by_num: dict[int, list] = {}
        cur = None
        for r in _rows(lines):
            if r[2].startswith("Begin_Run"):
                cur = real_by_num.setdefault(int(r[2].rsplit("_", 1)[1]), [])
                continue
            if cur is not None:
                cur.append(r)
        nums = sorted(real_by_num)
        real = [real_by_num[n] for n in nums]
        # A capture made with the Interim Costs output ticked (evidenced by
        # its `rpt_interim_cost_*` sibling) is compared with a cost sink:
        # that option changes real's t=0 trigger/phase draw order (see
        # orchestrator.py's module docstring).
        cost_on = bool(glob.glob(str(Path(cap).parent / "rpt_interim_cost*.txt")))
        signal.alarm(120)
        try:
            all_ours = [[r for r in _rows(run) if not r[2].startswith("Begin_Run")]
                        for run in run_simulation(load(str(rbd)), num_runs=max(nums),
                                                  _cost_sink=[] if cost_on else None,
                                                  # a `rpt_events_password_*` capture was made with the Avant-garde
                                                  # defer-trigger switch on (the Simulation dialog's Trigger combo)
                                                  defer_trigger="file" if "password" in Path(cap).name else "")]
            ours = [all_ours[n - 1] for n in nums]
        except _Timeout:
            print(f"{fix}: TIMEOUT")
            continue
        except Exception as e:  # noqa: BLE001 -- report and keep scanning
            print(f"{fix}: ERROR {type(e).__name__}: {str(e)[:80]}")
            continue
        finally:
            signal.alarm(0)
        diff = [i for i, (o, r) in enumerate(zip(ours, real)) if o != r]
        total += len(real)
        bad += len(diff)
        if diff:
            o, r = ours[diff[0]], real[diff[0]]
            j = next((k for k in range(min(len(o), len(r))) if o[k] != r[k]), min(len(o), len(r)))
            print(f"{fix}: {len(diff)}/{len(real)} runs differ, e.g. run {nums[diff[0]]} @ line {j}:")
            print("   OURS", o[j] if j < len(o) else None)
            print("   REAL", r[j] if j < len(r) else None)
    print(f"TOTAL: {bad}/{total} runs differ")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Feature-coverage matrix across the whole fixture corpus.

Answers "which engine features does any fixture with a REAL capture
actually exercise?" -- the map behind the internal fixture-coverage notes.
Re-run it after adding a fixture; a feature with 0 real-capture fixtures
is a hole in the verification, not necessarily a hole in the port.

    python3 tools/fixture_coverage.py            # summary + gaps
    python3 tools/fixture_coverage.py --full     # every feature row

NOTE: the dependency target is carried in `dependency_num` (-1 its own
node, -2 the whole system, >0 a node/block id).
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import golden_fixtures as gf                       # noqa: E402
from raptor.core.fileio.rbd import load_rbd        # noqa: E402
from raptor.core.engine.load import load_model   # noqa: E402
from raptor.core.rng import Distribution           # noqa: E402


def collect() -> tuple[dict[str, set[str]], list[tuple[str, str]]]:
    feats: dict[str, set[str]] = collections.defaultdict(set)
    errs: list[tuple[str, str]] = []

    def mark(feat: str, name: str) -> None:
        feats[feat].add(name)

    for n in sorted(gf.all_fixture_names()):
        p = gf.resolve_rbd_path(n)
        if p is None:
            continue
        try:
            m = load_model(load_rbd(str(p)))
        except Exception as exc:                    # noqa: BLE001
            errs.append((n, f"{type(exc).__name__}: {exc}"))
            continue
        tag = n if gf.has_real_legacy_capture(n) else n + "*"
        if m.phases:
            mark("phases", tag)
        if getattr(m, "cost_analysis", False):
            mark("cost_analysis", tag)
        if getattr(m, "weak_analysis", False):
            mark("weak_analysis", tag)
        if m.cap_node_group:
            mark("capacity", tag)
        mark(f"termination={m.termination_type}", tag)
        if m.spare_pools:
            mark("pool:SparePool", tag)
        if m.resource_pools:
            mark("pool:Resource", tag)
        if m.events_by_id:
            mark("RBDEvent", tag)
        if m.hiers_by_id:
            mark("RBDHier", tag)
        for b in m.blocks_by_id.values():
            mark(f"sparing={b.sparing_type.value}", tag)
            mark(f"distro.fail={Distribution(b.fail_distro).name}", tag)
            mark(f"distro.repair={Distribution(b.repair_distro).name}", tag)
            mark(f"GDType={b.gd_type}", tag)
            mark(f"simStart={b.sim_start_type}", tag)
            # dependency target lives in dependency_num, NOT the booleans
            if b.dependency_num == -1:
                mark("dep:locDepend", tag)
            elif b.dependency_num == -2:
                mark("dep:sysDepend", tag)
            elif b.dependency_num > 0:
                mark(f"dep:{b.dep_type}", tag)
            if b.dep_pm_percent:
                mark("dep:GoToPM%", tag)
            if b.dep_fail_percent:
                mark("dep:InducedFail%", tag)
            if b.uses_pm:
                mark("PM", tag)
                mark("PM:triggered" if b.pm_triggered else "PM:frequency", tag)
                mark(f"PM:pm_refresh={b.pm_refresh}", tag)
                mark(f"PM:pm_fail_reset={b.pm_fail_reset}", tag)
                mark(f"PM:distro={Distribution(b.pm_dist).name}", tag)
                if b.pm_spare_needed:
                    mark("PM:needs spare", tag)
                if b.pm_resources_needed:
                    mark("PM:needs resource", tag)
                if b.pm_mis_defer:
                    mark("PM:pmMisDefer", tag)
                if not b.pm_req_defer:
                    mark("PM:pmReqDefer=False", tag)
            for flag, label in (("routine_spare_ordering", "RSO"),
                                ("emergency_spare_ordering", "ESO"),
                                ("stock_level_ordering", "SLO")):
                if getattr(b, flag):
                    mark(label, tag)
        for nd in m.nodes_by_id.values():
            if nd.cold_standby:
                mark("cold standby node", tag)
    return feats, errs


def main(argv: list[str]) -> int:
    full = "--full" in argv
    feats, errs = collect()
    for name, err in errs:
        print(f"LOAD-FAIL {name}: {err}")

    def real(feat: str) -> list[str]:
        return sorted(x for x in feats[feat] if not x.endswith("*"))

    gaps = [f for f in feats if not real(f)]
    # Values that exist in the FILE FORMAT but that no user-built model
    # can actually carry -- the GUI either has no control for them or
    # coerces them on load, so they are vestigial rather than gaps, and
    # listing them just keeps a permanently-unclosable line on the
    # report. Each one is verified in the real GUI before it lands here;
    # see the internal fixture-coverage notes for the evidence.
    #   sparing=None -- Block Properties > Maintenance Information offers
    #     only Infinite / Spare pool / Custom; Raptor re-reads a
    #     `SpType=None` file as "Custom, stock 100" (verified
    #     2026-09-15). Source's own declaration comments it `{may be
    #     remnant}`.
    #   distro.fail=NONE -- source's DrawNumber has NO branch for distro
    #     18; its OTHERWISE pops "ERROR:
    #     Attempt to draw random number from unkown distribution!". 18 is
    #     a REPAIR-side sentinel only, tested as `IF repairDistro=18`
    # BEFORE any draw
    # -- there is no matching `failDistro=18` guard
    #     anywhere, so a fail distro of 18 always reaches DrawNumber and
    #     alerts. Verified 2026-09-16; a probe built for it was deleted
    #     rather than shipped, since it would pop a modal error dialog on
    #     every draw and block a capture session.
    UNREACHABLE = {"sparing=None", "distro.fail=NONE"}

    # Enumerated axes: report the values NEVER seen at all, too.
    for axis, values in (("sparing", ("Infinite", "SparePool", "Custom", "None")),
                         ("GDType", tuple(str(i) for i in range(6))),
                         # GetStartCond's CASE is 1..6;
                         # 0 is not a valid value -- source's OTHERWISE alerts
                         # "Unknown simStartType!".
                         ("simStart", tuple(str(i) for i in range(1, 7)))):
        for v in values:
            if f"{axis}={v}" not in feats:
                gaps.append(f"{axis}={v}")
    for d in Distribution:
        for role in ("fail", "repair"):
            if f"distro.{role}={d.name}" not in feats:
                gaps.append(f"distro.{role}={d.name}")
    # Boolean features are invisible to the scan when NO fixture sets
    # them -- the key never gets created. List them so "never exercised"
    # reads as a gap instead of as silence.
    for flag in ("PM:pmMisDefer", "PM:needs spare", "PM:needs resource",
                 "PM:pmReqDefer=False", "dep:GoToPM%", "dep:InducedFail%",
                 "dep:locDepend", "dep:sysDepend", "dep:RBDBlock", "dep:RBDHier",
                 "cold standby node", "RSO", "ESO", "SLO", "RBDEvent", "RBDHier",
                 "phases", "capacity", "cost_analysis", "weak_analysis",
                 "PM:triggered", "PM:frequency"):
        if flag not in feats:
            gaps.append(flag)

    if full:
        print(f"\n{'feature':34} {'#':>3}  example fixtures with a real capture")
        for f in sorted(feats):
            r = real(f)
            print(f"{f:34} {len(r):>3}  {', '.join(r[:3]) or '(none -- NO REAL CAPTURE)'}")

    unreachable_hits = sorted(g for g in gaps if g in UNREACHABLE)
    gaps = [g for g in gaps if g not in UNREACHABLE]
    print(f"\n=== {len(gaps)} feature(s) with NO real-capture coverage ===")
    for g in sorted(set(gaps)):
        print(f"  {g}")
    if unreachable_hits:
        print(f"\n=== {len(unreachable_hits)} vestigial, NOT a gap "
              "(file format only, no GUI control) ===")
        for g in unreachable_hits:
            print(f"  {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

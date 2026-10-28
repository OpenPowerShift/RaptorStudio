#!/usr/bin/env python3
"""Draw-for-draw `/sd` (simDetails.TXT) comparison against a real capture
-- the "large-scale RNG-draw-identity regression" tool this project has
never had (every prior investigation, including this one, built one ad
hoc and threw it away; see the internal investigation log's repeated "family
sweep" scripts). Runs OUR engine for the same number of runs a golden
fixture's real capture covers, and reports the FIRST point where a draw
(time, element name, value) disagrees -- with enough surrounding context
to start a real investigation from, not just a pass/fail verdict.

Usage:
    tools/sd_diff.py --fixture Phasing1
    tools/sd_diff.py --fixture Phasing1 --num-runs 5
    tools/sd_diff.py --rbd tests/data/models/Phasing1.rbd \\
        --real tests/golden/Phasing1/legacy/simDetails.TXT --num-runs 9
    tools/sd_diff.py --fixture Phasing1 --context 8

`--fixture NAME` is shorthand for the established golden-fixture layout:
`tests/golden/NAME/input.rbd` + `tests/golden/NAME/legacy/simDetails.TXT`.
Use `--rbd`/`--real` directly for anything that doesn't fit that layout
(e.g. `tests/data/models/Phasing1.rbd`, confirmed byte-identical to the golden copy,
but outside `tests/golden/` -- some fixtures' canonical `.rbd` still
lives under `legacy/rbd/` instead).

Real `simDetails.TXT` captures include non-draw header/graph-building
noise (RunEFPA output, "CAP STATS:", etc.) before the actual per-draw
lines -- this tool filters to only lines matching `<float> <name>
<float>`, same as every prior investigation's own ad hoc parser.

NOISE FLOOR (2026-09-03, user ask: "take into account the noise floor of
the calculations so we don't need to think about this each time"): two
REPEATEDLY-ENCOUNTERED non-structural difference classes kept tripping
manual re-judgment on every comparison, so the envelope is now baked in:

1. software-`ln` rounding (Modsim's own ~1e-9-relative-accuracy `ln` vs
   libm's correctly-rounded one, §10/§13): ~5.5e-10 RELATIVE on
   exp-family draws -- e.g. the CS-family's 842292.019207 vs
   ...018741 redraw values (the internal investigation log §19/§37).
2. the ~4e-10 uniform-stream offset AMPLIFIED by a steep inverse-CDF
   tail (§43.5's Weibull-tail signature, "do not chase below 4e-6"):
   observed up to 6e-6 ABSOLUTE on moderate values (Capacity1 index
   401: `LowGain 216.033373` vs `216.033367`, Weibull) and up to
   ~2.8e-8 RELATIVE on large values (Phasing3 index 5444: `17186.003325`
   vs `17186.003757`, Pearson5 -- a 4.3e-4 absolute diff that only the
   relative term can cover).

The envelope below sits ~3x above the largest observed noise on each
axis. Anything within it is classified NOISE -- skipped by
`first_divergence` (so the regression sweep tracks STRUCTURAL
divergence only) but REPORTED by the CLI (count + worst offenders), so
noise is never silently hidden, and an in-envelope drift that GROWS is
visible run over run. Structural divergence (name/time mismatch, or
value beyond the envelope) still flags exactly as before.

Tolerance matches every prior investigation in this project (software
`ln`-noise floor): `abs(a - b) <= max(TOL_ABS, abs(a) * TOL_REL)`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_fixtures as gf  # noqa: E402

#: Noise-floor envelope -- NOT a correctness tolerance.
#:
#: RE-DERIVED 2026-09-12, after the base-generator divisor fix (F.1:
#: `RaptorStream.uniform` divided by 2**31-1 where real divides by
#: 2**31). The OLD values -- TOL_ABS 1e-5, TOL_REL 1e-7 -- were
#: calibrated in 2026-09-03 against noise-class observations that were
#: THEMSELVES that bug, so the whole envelope was sized around an
#: artefact. It came within a factor of ~2 of hiding it: the divisor
#: error showed as 2.3e-7 relative on `CS_Probe_hot`, just over the
#: 1e-7 line, and was tolerated for months on the fixtures where the
#: magnitudes kept it under.
#:
#: The re-derivation: with the divisor corrected, all 15 byte-exact core
#: fixtures were compared draw-for-draw -- **128,374 draws, ZERO with any
#: nonzero delta**. Re-running the ENTIRE corpus (core + secondary) with
#: the envelope set to exactly 0.0 moved NO fixture's `first_divergence`.
#: The observed noise floor is therefore exactly zero, and every value
#: below is pure headroom.
#:
#: Not set to 0.0 deliberately: these are `math.log`/`math.exp` chains
#: through the platform libm, so a different machine, libc or Python
#: build could legitimately differ in the last bits (~2.2e-16 relative
#: per operation, maybe ~1e-14 accumulated). The values below leave
#: roughly three orders of magnitude over that while being 10,000x
#: tighter than the old relative line -- tight enough that a repeat of
#: the divisor bug (2.3e-7 relative) would be flagged immediately
#: instead of absorbed.
TOL_ABS = 1e-9
TOL_REL = 1e-11

def _names_match(row_name: str, old_name: str) -> bool:
    """Exact match only.

    Until 2026-09-11 this forgave real's `link <objectNum>` tag on a
    cold-standby link's `SetSwitchTime` draw against this port's generic
    `SysStream11` fallback -- a naming gap, never a value/position one.
    That gap is now closed at the source (`cold_standby.set_switch_time`
    tags its own draw via `sim_details.tag_next_draw`, and the phase tag
    is scoped to its own call site), so the names match outright and the
    alias would only hide a genuine one. Verified the same day: the full
    sweep is unchanged with the alias removed.
    """
    return row_name == old_name


def _values_match(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOL_ABS, abs(a) * TOL_REL)


def _parse_draw_line(line: str) -> tuple[float, str, str] | None:
    """(time, name, raw_value_text) if `line` is a real draw line, else
    None (silently drops header/graph-building noise lines).

    Splits from BOTH ends (time from the left, value from the right) so
    a multi-word name -- e.g. a cold-standby link's own `link 8` tag,
    real's own name for `SetSwitchTime`'s SysStream[11] draw -- doesn't
    swallow the value into the name field or fail the value float-parse
    and get silently (and WRONGLY) dropped as noise. Found 2026-09-05
    investigating `CS_SwitchFail`: the old `split(None, 2)` 3-way split
    turned `0.000000  link 8  0.025303` into parts `["0.000000", "link",
    "8  0.025303"]`, failed `float("8  0.025303")`, and dropped the line
    entirely -- real's `link N` draws were invisible to every comparison
    this tool ever ran, not just misnamed."""
    parts = line.split(None, 1)
    if len(parts) < 2:
        return None
    try:
        t = float(parts[0])
    except ValueError:
        return None
    rest = parts[1].rsplit(None, 1)
    if len(rest) != 2:
        return None
    name, value = rest
    try:
        float(value)
    except ValueError:
        if "#INF" not in value and "#IND" not in value:   # the C runtime's inf / nan text
            return None
    return t, name, value


def load_real_draws(path: Path) -> list[tuple[float, str, str]]:
    draws = []
    for line in gf.read_capture_text(path, encoding="utf-8").splitlines():
        parsed = _parse_draw_line(line)
        if parsed is not None:
            draws.append(parsed)
    return draws


def run_ours(rbd_path: Path, num_runs: int, out_path: Path) -> list[tuple[float, str, str]]:
    from raptor.core.fileio.rbd import load_rbd
    from raptor.core.engine.runner import run_simulation

    rbd = load_rbd(str(rbd_path))
    run_simulation(rbd, num_runs=num_runs, sim_details_path=str(out_path))
    return load_real_draws(out_path)  # same line format, same parser


def first_divergence(
    real: list[tuple[float, str, str]], ours: list[tuple[float, str, str]],
) -> int | None:
    """Index of the first draw where time, name, or value disagree
    BEYOND the noise-floor envelope (in-envelope value differences are
    classified noise and skipped -- see the module docstring), or None
    if one is a prefix of the other with no disagreement in the
    overlap."""
    for i in range(min(len(real), len(ours))):
        kind, _detail, _dv = _classify(real[i], ours[i])
        if kind == "diverge":
            return i
    return None


def _classify(
    r: tuple[float, str, str], o: tuple[float, str, str],
) -> tuple[str, str, float]:
    """('match' | 'noise' | 'diverge', detail string, |delta|) --
    match = zero difference (same double); noise = nonzero but within
    the noise-floor envelope (skipped by first_divergence, reported by
    the CLI -- see the module docstring); diverge = beyond the envelope,
    or a name/time mismatch (always structural)."""
    rt, real_name, rval_s = r
    ot, ours_name, oval_s = o
    time_ok = abs(rt - ot) <= max(TOL_ABS, abs(rt) * TOL_REL)
    name_ok = _names_match(real_name, ours_name)
    if not (time_ok and name_ok):
        what = "name" if not name_ok else "time"
        return "diverge", f"{what}: real {real_name}@{rt} vs ours {ours_name}@{ot}", abs(rt - ot)
    try:
        rv, ov = float(rval_s), float(oval_s)
    except ValueError:
        if rval_s == oval_s:
            return "match", "", 0.0
        return "diverge", f"value text: {rval_s!r} vs {oval_s!r}", float("inf")
    value_delta = abs(rv - ov)
    if value_delta == 0.0:
        return "match", "", 0.0
    tol = max(TOL_ABS, abs(rv) * TOL_REL)
    rel = value_delta / abs(rv) if rv else float("inf")
    if value_delta <= tol:
        return "noise", f"abs {value_delta:.3e} rel {rel:.2e}", value_delta
    return "diverge", f"abs {value_delta:.6g} rel {rel:.2e} (real {rval_s} vs ours {oval_s})", value_delta


def infer_num_runs(real_draws: list[tuple[float, str, str]], cap: int = 20) -> int:
    """Guess a run count from the real capture's own "kickoff" landmark
    (the first-declared block/event's own t=0.0 draw recurring once per
    run) -- avoids silently comparing against only PART of the real
    capture's own run family. Factored out of `main()` 2026-09-08 so
    `test_sd_diff_sweep.py` can use the same
    inference for fixtures that don't have a hand-tuned override."""
    if not real_draws:
        return 10
    first_name = real_draws[0][1]
    num_runs = sum(1 for t, n, _ in real_draws if t == 0.0 and n == first_name)
    return min(max(num_runs, 1), cap)


def compare_draws(
    real: list[tuple[float, str, str]], ours: list[tuple[float, str, str]],
) -> dict:
    """Full comparison summary for the CLI (and any future tooling):
    first structural divergence, every noise-classified draw with its
    magnitudes, and the draw-COUNT comparison -- the sharper instrument
    per §43.4 (PmFailResetOff's 73 phantom draws passed the old
    first-divergence metric while the count gap flagged them
    immediately)."""
    noises: list[tuple[int, str, str, float]] = []
    divergence = None
    for i in range(min(len(real), len(ours))):
        kind, detail, dv = _classify(real[i], ours[i])
        if kind == "noise":
            noises.append((i, detail, f"{real[i][1]} {real[i][2]} vs {ours[i][2]}", dv))
        elif kind == "diverge" and divergence is None:
            divergence = i
            break
    noises.sort(key=lambda t: -t[3])
    return {
        "first_divergence": divergence,
        "noise": noises,
        "count_real": len(real),
        "count_ours": len(ours),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", help="golden fixture name under tests/golden/ "
                    "(shorthand for --rbd/--real using its standard layout)")
    ap.add_argument("--rbd", help="path to the .rbd file to run")
    ap.add_argument("--real", help="path to the real captured simDetails.TXT")
    ap.add_argument("--num-runs", type=int, default=None,
                    help="how many runs to execute (default: enough to "
                    "cover every real draw available, capped at 20)")
    ap.add_argument("--context", type=int, default=5,
                    help="lines of context before/after the divergence "
                    "point to print (default 5)")
    args = ap.parse_args()

    if args.fixture:
        # golden_fixtures (added 2026-09-08)
        # knows the two real naming conventions (plain simDetails.TXT vs
        # simDetails_<Name>.txt) and the tests/fixtures/ fallback for
        # fixtures with no local input.rbd -- same resolution
        # test_sd_diff_sweep.py's new-fixture sweep uses, so this CLI
        # tool and that test never disagree about where a fixture lives.
        real_path = gf.simdetails_path(args.fixture)
        rbd_path = gf.resolve_rbd_path(args.fixture)
        if real_path is None:
            ap.error(f"no simDetails capture found for fixture {args.fixture!r}")
        if rbd_path is None:
            ap.error(f"no .rbd found for fixture {args.fixture!r}")
    else:
        if not args.rbd or not args.real:
            ap.error("either --fixture, or both --rbd and --real, are required")
        rbd_path = REPO / args.rbd
        real_path = REPO / args.real
    if not rbd_path.exists():
        ap.error(f"no such .rbd: {rbd_path}")
    if not real_path.exists():
        ap.error(f"no such real capture: {real_path}")

    real_draws = load_real_draws(real_path)
    print(f"Real capture: {len(real_draws)} draw lines ({real_path})")

    # Guess a run count from the real file's own "kickoff" landmark (the
    # first-declared block's own t=0.0 draw recurring once per run) if
    # not given explicitly -- avoids silently comparing against only
    # PART of the real capture's own run family.
    num_runs = args.num_runs
    if num_runs is None:
        num_runs = infer_num_runs(real_draws)
        print(f"(--num-runs not given, inferred {num_runs} from the real "
              f"capture's own kickoff landmark -- pass --num-runs to override)")

    out_path = Path("/tmp") / f"sd_diff_{rbd_path.stem}.TXT"
    our_draws = run_ours(rbd_path, num_runs, out_path)
    print(f"Our run: {len(our_draws)} draw lines ({num_runs} runs, {out_path})")

    result = compare_draws(real_draws, our_draws)
    idx = result["first_divergence"]

    # Draw-COUNT comparison (§43.4): a count mismatch is structural
    # signal even when the whole overlap classifies clean -- it caught
    # PmFailResetOff's 73 phantom draws that the first-divergence metric
    # sailed past. Reported prominently, not just in the passing case.
    cr, co = result["count_real"], result["count_ours"]
    if cr != co:
        print(f"\nDRAW COUNT MISMATCH: real {cr} vs ours {co} "
              f"({co - cr:+d}) -- a missing/extra-draw class divergence "
              f"the overlap comparison below cannot show by itself.")

    # Noise-floor draws: within the envelope, NOT divergences -- but
    # never silently hidden (count + worst offenders, so a growing
    # in-envelope drift stays visible run over run).
    noises = result["noise"]
    if noises:
        print(f"\nNOISE-FLOOR DRAW DIFFERENCES: {len(noises)}/{min(cr, co)} "
              f"draws within the envelope (not divergences). Worst by "
              f"absolute delta:")
        for i, detail, pair, _dv in noises[:3]:
            print(f"   [{i}] {pair}   ({detail})")

    if idx is None:
        n = min(cr, co)
        if cr == co:
            print(f"\nNO DIVERGENCE -- all {n} draws match (any nonzero "
                  f"differences were within the noise floor, see above).")
        else:
            shorter = "real" if cr < co else "ours"
            print(f"\nNO DIVERGENCE in the first {n} draws, but {shorter} has "
                  f"fewer lines than the other -- one is a strict prefix of "
                  f"the other. Re-run with a different --num-runs if this "
                  f"is unexpected.")
        return 0

    lo = max(0, idx - args.context)
    hi = min(min(len(real_draws), len(our_draws)), idx + args.context + 1)
    print(f"\nFIRST DIVERGENCE at draw index {idx}:")
    for i in range(lo, hi):
        marker = ">>>" if i == idx else "   "
        rt, row_name, rval = real_draws[i]
        ot, old_name, oval = our_draws[i]
        print(f"{marker} REAL: {rt:<14} {row_name:<24} {rval}")
        print(f"{marker} OURS: {ot:<14} {old_name:<24} {oval}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

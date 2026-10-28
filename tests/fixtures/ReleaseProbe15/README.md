# ReleaseProbe15 — does the trigger-tie absorption follow ROW ORDER or BLOCK ID? (separates two confounded variables in ReleaseProbe10)

`ReleaseProbe10`'s real capture (§46-47) found: when `T1`'s fire ties
with `BlockD`'s own second fail, the row-FIRST PM subscriber (`BlockS`)
has its wake silently ABSORBED (no `PM_is_due`, no PM, no draw), while
the row-SECOND subscriber (`BlockN`) dispatches normally. But
`ReleaseProbe10` never actually separated "row/registration order"
from "block ID order" as the operative rule — `BlockS` is BOTH listed
first in `BLOCK_DATA` AND has the lower ID (3 vs `BlockN`'s 4), so
either explanation fits the one data point equally well. This matters
because the internal investigation log §48.4 already DECODED real
RAPTOR's own `ChooseNext` source for same-instant calendar ties in
general: "non-blocks-first, then LOWEST BLOCK ID wins" — if THAT same
rule governs which trigger subscriber gets absorbed, the two open
threads (`ChooseNext`'s tie-break, and the trigger-release absorption
asymmetry) may be the SAME mechanism, not two separate ones.

This probe is `ReleaseProbe10`, byte-identical in every way EXCEPT the
two subscribers' block IDs are SWAPPED: `BlockS` (still listed FIRST
in `BLOCK_DATA`, still the chain-upstream block, still PM mean 1.0)
now has ID **4**; `BlockN` (still listed second, still chain-
downstream, still PM mean 2.0) now has ID **3** — the LOWER one. Every
link reference updated to match. (Non-monotonic block IDs relative to
row order are already proven safe in real Raptor -- `tests/golden/
Capacity1`'s own real-captured file has heavily non-monotonic IDs, e.g.
row order `[5, 1, 2, 3, 8, 11, 10, ...]`.)

## The question

At `T1`'s tied fire (t=5.0), which subscriber's wake gets absorbed?

- **`BlockS` absorbed again** (still row-first, now the HIGHER id) →
  registration/row order is the rule; `ChooseNext`'s block-Id rule
  (§48.4) does NOT govern this specific mechanism -- the two threads
  are separate after all.
- **`BlockN` absorbed instead** (now the LOWER id, despite being
  row-second) → block ID is the rule, matching `ChooseNext` exactly --
  the trigger-release absorption and the general same-instant tie-
  break may be the SAME underlying mechanism, a significant
  simplification for whoever picks up `ChooseNext`'s own group-
  composition question (§48.5) next.
- **Neither absorbed (both dispatch)** → the ID swap itself changed
  something else about the scenario (unlikely given `ReleaseProbe10`'s
  own structure is otherwise untouched, but check the full event log
  around t=5.0 if this happens).

## Readout (t=5.0 real event log + /sd, shared stream 203)

Count the `PM_is_due Triggered_PM_Due` lines for `BlockS` and `BlockN`
at t=5.0, and the /sd draws at that instant. Our port (unaffected by
block ID, since it doesn't implement either absorption rule yet):
both dispatch, `BlockS 0.602041` then `BlockN 2.944303` at t=5.0 (own
run) -- this port-side result is NOT the readout; only real Raptor's
answer distinguishes the two hypotheses above.

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs matches
`ReleaseProbe10`'s own capture), read the event log and `/sd` output
around SimTime=5.0.

## REAL ANSWER (2026-09-03 capture, `simDetails_ReleaseProbe15.txt`)

**`BlockS` absorbed again; `BlockN` dispatches — registration/row
order is the rule, NOT block ID.** All 3 runs: `BlockN` draws a value
at t=5.0 (`1.204082`, `2.509604`, `0.889234` — the SAME mechanism as
`ReleaseProbe10`'s own original N-dispatch, `1.204082 = -2·ln(U)` with
N's own EXP(2) mean, the SAME shared-stream `U` `BlockS`'s own EXP(1)
draw would have consumed); `BlockS` never draws at t=5.0 at all in any
run — absorbed, exactly like `ReleaseProbe10`'s original `BlockS`.
Since the IDs were swapped (`S`: now 4, the HIGHER; `N`: now 3, the
LOWER) but the absorbed/dispatched ROLES stayed exactly where they
were before the swap (still `S` absorbed, still `N` dispatches), block
ID cannot be what decides this -- something about registration/row
order (or an equivalent property tied to the block/subscriber itself,
not renumbered by an ID swap) is.

**Consequence**: `ChooseNext`'s "lowest block ID wins" rule (§48.4)
does NOT govern the trigger-release absorption mechanism. The two
threads are confirmed SEPARATE, not one mechanism in two guises --
`ChooseNext`'s own group-composition question (§48.5) still needs its
own path forward (the MODSIM Users Manual, or a systematic tie-shape
sweep), and trigger-release absorption specifically needs the
`ControlPMtriggers`/`TriggerObj.Release` micro-semantics §48.1-48.3
already identified as the remaining unknowns (WAIT FOR release
ordering, and Interrupt-on-pending-interrupt replace/queue/ignore
semantics) -- not derivable from either this probe or `ChooseNext`'s
own source.

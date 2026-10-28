# ReleaseProbe18 — does a second, non-drawing same-node recoverer flip B's redraw from hopped to sync? (bisecting Capacity1 run 5's own anomaly)

`ReleaseProbe`/`ReleaseProbe3` (the internal investigation log §37, §39)
already established the GENERAL rule this port's own recovery-redraw hop
implements: a Site-1-parked, node-dependent block's fresh recovery
redraw, racing ONE already-queued pool-grant resource pickup at the same
instant, is **HOPPED** (the pickup's own continuation goes first) — real
capture's order `[C's pickup, B's redraw]`, matching our port exactly.

`Capacity1` run 5 (`t=62.715126`) is the ONE known real instant where the
SAME structural situation — a Site-1-parked node-dependent block
(`BatteriesNiCd`) recovering at the same instant as an already-queued
pool-grant pickup (`SolarP_650W`'s own PM-begin continuation) — instead
runs **SYNC**: `NiCd`'s redraw lands in stream position 2, BEFORE the
pickup continuations, the opposite of `ReleaseProbe3`'s own answer. No
probe has yet reproduced this flip. §39's own "next step" note proposed
bisecting run 5's distinguishing features against `ReleaseProbe3`'s
baseline one at a time; this probe is the first bisection step.

**The candidate feature this probe isolates**: at `Capacity1` run 5,
`BatteriesNiCd` is NOT the only thing recovering from `500path`'s AllUp
cascade — `SolarP_500W` recovers at the exact same instant, from the
exact same node, via the exact same cascade, but draws NOTHING (its own
`FailTimeLeft` wasn't fully consumed when it went dependency-Idle, so its
own recovery is an ordinary interrupted-countdown resume, not a fresh
redraw). `ReleaseProbe3` has no such second recoverer. Does adding one
change anything about `BlockB`'s own redraw order?

## What's new here vs `ReleaseProbe3`

Byte-identical to `ReleaseProbe3` (`BlockA`→`BlockB` chain into `join`,
`BlockC` parallel, `join`→`end`, the single `Techs` resource pool `A`/`C`
compete for, `BlockB`'s own trigger-driven PM at t=4 causing its own
`downNum>1` direct-Idle Site-1 park at t=5) **PLUS one new block,
`BlockE`** (id 8):

- **Physically separate**: `start→BlockE→end`, its own dedicated links
  (7, 8) — does NOT touch `join` at all, so `join`'s own `n`/incoming-path
  counters (already well-established via probe-3/5/7/8/9's own downNum
  work, the internal engine ledger §C.7) are completely unaffected.
  `end`'s own `n` bumped `1→2` for its new second incoming link (the
  single-End-node-with-`n>1` pattern already proven safe, `MixedDepPerc`/
  `WakeOrderDependent`).
- **Dependency-only link to `join`**: `DepNum=5` (`join`'s own node id,
  the SAME node `BlockB` depends on via its own `DepNum=-1`), `DepType=
  RBDNode` (the explicit-positive-id case of `dependency.
  resolve_dependency_node_id`'s three-way mapping — lets `BlockE` depend
  on `join` WITHOUT being physically wired into it, unlike `BlockB`'s own
  `-1`/"depend on my own node" shorthand, which isn't available here
  since `BlockE`'s own node is `end`, not `join`). `DepIdle=100`,
  `defDepStateIdle=1` — same deterministic-GoIdle config as `BlockB`'s
  own dependency row.
- **`FDist=19` (FIXED), `FParam1=1000000.0`** — deliberately never fails
  naturally within the 30h run, and being FIXED, never draws at all, at
  either the pause OR the resume of an interrupted wait — a simplified
  stand-in for `SolarP_500W`'s own (Exponential, huge-mean) natural
  fail-wait. The specific distribution doesn't matter for what this probe
  tests (whether `BlockE`'s presence and recovery-without-a-draw changes
  `BlockB`'s own order) — only that `BlockE` is genuinely paused (not
  exhausted) when it goes dependency-Idle and resumes without drawing,
  which FIXED guarantees trivially. No PM, no resource (`usesPM=0`,
  `NumRes=0`) — kept as simple as possible to isolate this one variable.

**Confirmed via this port's own run** (not the readout, just a structural
sanity check): `join` goes `Bad` at `t=3.5` when `BlockC` also fails
(both `A`'s chain path and `C`'s own path down simultaneously — `join`'s
`k=1` needs only one), dependency-idling `BlockB` AND `BlockE` together
at that instant (`"BlockE Idle Dependence"` alongside `"BlockB Idle
Dependence"`, confirmed in the event log). At `t=25` (`A`'s repair
completing, releasing the `Techs` resource to the waiting `C`), `join`
recovers to at least `Degraded` (one good path is enough for `k=1`),
recovering `BlockB` (redraw) AND `BlockE` (no draw, `"BlockE Good
DependenceStress=1.000000"`, no `/sd` line for it at all) at the SAME
instant `C`'s own resource grant fires — structurally the three-way
collision `Capacity1` run 5 has, minus the trigger-PM machinery.

## The question

At `t=25`, does `BlockB`'s own recovery redraw land BEFORE or AFTER
`BlockC`'s own pickup continuation?

- **`BlockB`'s redraw moves to SYNC** (before `BlockC`'s pickup,
  matching `Capacity1` run 5's own order) → confirms a second,
  simultaneous, non-drawing same-node recoverer is (at least part of)
  the discriminator between `ReleaseProbe3`'s hopped answer and run 5's
  sync one. Next step: reproduce run 5's OTHER distinguishing feature
  (TWO pool-grant competitors, not one) to see whether it's independently
  sufficient or whether both are needed together.
- **`BlockB`'s redraw stays HOPPED** (same order as `ReleaseProbe3`,
  unaffected by `BlockE`'s presence) → this specific feature is NOT the
  discriminator — rules it out, and points at the two-pool-competitors
  feature as the next probe to build instead (this one, unchanged).

## Our port's own prediction (NOT the readout — our port doesn't implement a variable sync-vs-hop rule)

`sim_details_path` trace from our own `run_simulation`, own run 1:
```
0.000000  BlockB  792225.529423
25.000000  BlockC  1.357908
25.000000  BlockB  842292.018741
```
Still `[C, B]` (hopped) — identical to `ReleaseProbe3`'s own port
behavior, confirming `BlockE`'s addition doesn't touch this port's own
comparator at all (expected: `BlockE` has no physical link into `join`,
and our port's tie-break logic doesn't currently have any notion of
"how many things are recovering," only calendar seq order). `BlockE`
itself never appears in the trace (FIXED, never draws) — exactly as
designed. This prediction is NOT the subject of the probe; only real
Raptor's own answer distinguishes the two hypotheses above.

## Readout (t=25.0 /sd order, shared stream 203)

Count/order the `/sd` lines at `SimTime=25.000000`: if `BlockB` appears
BEFORE `BlockC`, that's SYNC (flip). If `BlockC` appears before `BlockB`
(as in `ReleaseProbe3`), that's still HOPPED (no flip). Also check the
full event log around t=25 for `BlockE`'s own line (`"BlockE Good
DependenceStress"`) to confirm it recovers at the same instant with no
draw, matching the design intent — if `BlockE`'s recovery timing itself
looks different from what's described above, note the full event log
context rather than just the draw order.

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs matches
`ReleaseProbe3`'s own capture count), read the event log and `/sd`
output around SimTime=25.0.

## REAL ANSWER (2026-09-03 capture, `simDetails_ReleaseProbe18.txt`)

**`BlockB`'s redraw STAYS HOPPED** — the order is `[BlockC, BlockB]` in
all 3 runs, and the values are BYTE-IDENTICAL to `ReleaseProbe3`'s own
real capture (`792225.529889`/`1.357908`/`842292.019207`, run 1;
matching across all 3 runs). `BlockE`'s presence — a second,
simultaneous, non-drawing same-node recoverer — has ZERO observable
effect on `BlockB`'s own redraw order or value.

**Consequence**: this rules out the first candidate feature cleanly. A
second non-drawing recoverer sharing the same AllUp cascade is NOT part
of what makes `Capacity1` run 5's instant special. The remaining
candidate from §39's own two-feature list — **TWO pool-grant
competitors queued ahead of the recovery, instead of one** — is now the
lead hypothesis; `ReleaseProbe19` (or next available number) should add
a SECOND pool-grant pickup competitor (mirroring `SolarP_650W`+
`BatteriesNiH`'s own dual PM-resource grants in `Capacity1`) to
`ReleaseProbe3`'s baseline, in place of (not in addition to) this
probe's own `BlockE` feature, to test that specific variable in
isolation next.

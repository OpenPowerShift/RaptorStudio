# ReleaseProbe36 — a deliberately-designed 3-incoming-path downNum probe (genuine park now reproduced)

The internal engine ledger C.7: the `downNum≤1 self-corrects /
downNum>1 parks` rule is SOLVED for every real 2-incoming-path data
point (7/7), but `ReleaseProbe6`'s own real capture — the only
3-incoming-path node ever captured — showed `BlockB` self-correcting
where the validated 2-path rule predicts a park. That entry explicitly
flags: "No real capture of this exact 3-path shape exists yet —
natural next probe."

## Root cause of `ReleaseProbe6`'s own non-park, and of this probe's own FIRST construction attempt also failing

An earlier version of this probe (made `BlockA` permanently inert,
retimed `BlockC`/`BlockD` to be down while `BlockB` completed) also
showed NO parking at all, and was left with an honest unresolved
caveat recommending a direct trace of `repair_completion_status`
(`raptor/core/engine/dependency.py:621`). That trace is now done, by
adding a temporary debug wrapper around the function and running this
port's own engine directly against both `ReleaseProbe6` and this probe
— **both non-parks are the SAME root cause, and it isn't a downNum bug
at all:**

`repair_completion_status`'s FIRST gate is `if dep_node.status in
_NODE_UP_LIKE: return RUNNING, False` — `_NODE_UP_LIKE = {ALL_UP,
DEGRADED}`. Every `join` node in every one of this project's
downNum probes (2-path AND 3-path alike, `ReleaseProbe3/5/7/8/9/6`)
declares `k=1` — an OR-node needing only ONE of its N incoming paths
up to read `DEGRADED` (not `Down`), and `DEGRADED` is itself already
"up-like". For a 2-path `k=1` node, that means BOTH paths must be down
simultaneously for the node to read genuinely `Down`/`Bad` — which is
exactly what `ReleaseProbe3/5/7/8/9`'s own real-captured scenarios
arranged. For a 3-path `k=1` node, ALL THREE paths must be down
simultaneously — `ReleaseProbe6`'s own construction (only `BlockA`+
`BlockB`'s own shared chain-link ever goes down; `BlockC`/`BlockD`
stay healthy the whole run) NEVER reaches that state, so
`repair_completion_status` returns at the very first line, every
time — the `down_num` computation (`fail_val+pm_val` for `BlockB`'s
own chain-shared array index) is **never even reached**, regardless of
its own value. Directly confirmed via instrumentation on
`ReleaseProbe6`'s own `t=5` completion:
```
dep_node.status=DEGRADED, array_index=0, fail_val=1, pm_val=1  (down_num would be 2)
-> result=(RUNNING, False)   <- returned at the FIRST gate, down_num never consulted
```
This project's own earlier framing ("3-incoming-path node behaves
differently") was an artifact of `ReleaseProbe6`'s own specific
construction, not a genuine arity-dependent divergence — nothing in
`repair_completion_status` is arity-aware at all. The FIRST construction
of THIS probe repeated the identical mistake in a different form: it
made `BlockA` permanently inert, which removes the one thing that could
ever make `BlockB`'s OWN chain-shared array index accumulate `down_num
>1` in the first place (per C.7's own "chain-shared EFPA-link
accumulation" — `RunEFPA` assigns every block in a series chain, here
`A→B`, the SAME array index into `join`). Getting `BlockC`/`BlockD`
down was necessary but not sufficient without `BlockA` ALSO being an
active, still-failing chain partner of `BlockB`.

## What this (rebuilt) probe does differently

Clone of `ReleaseProbe6`'s own already-crash-fixed topology
(`start→{A→B, C, D}→join(n=3)→end`) via `tools/rbd_builder.py`,
restoring `BlockA` to an ACTIVE chain partner and retiming `BlockC`/
`BlockD` to overlap `BlockA`'s own down window AND `BlockB`'s own PM
completions:
- `BlockA`: `FIXED(3.0)` fail / `FIXED(22.0)` repair — down `[3,25)`
  (same values as `ReleaseProbe6`'s own original `BlockA`).
- `BlockB`: `usesPM=1`, `pmTriggered=0`, `PMFreq=10.0`, PM duration
  `FIXED(2.0)` — never ordinary-fails (`FIXED(1e8)`); PM due every 10
  units of its own operating time, completing 2 units after each due
  instant. `NumRes=0` (no resource-contention confound).
- `BlockC`: `FIXED(9.0)` fail / `FIXED(4.0)` repair — down
  `[9,13), [22,26), [35,39), [48,52), ...` (period 22).
- `BlockD`: `FIXED(11.0)` fail / `FIXED(6.0)` repair — down
  `[11,17), [28,34), [45,51), ...` (period 28).
- Mission length unchanged: `TermCondition=110.0` (`TimeSim`).

Every distribution is `FIXED` (deterministic) — all 3 runs in this
port's own execution are byte-identical, so this describes ALL runs,
not just one.

`validate()`: zero warnings or errors.

## What this port's own run actually does (directly confirmed, not guessed)

`BlockB`'s own completion instants across the full `t=0-110` mission,
with the live-traced `dep_node.status`/`down_num` at each one:

| t | dep_node.status | down_num | result | log line |
|---|---|---|---|---|
| 12 | **DOWN** (0-of-3: A,C,D all down) | **2** | **IDLE** | `Idle change_block_state` |
| 25 | DEGRADED (A just recovered same instant) | 2 | RUNNING | `Good Stress=1.0` |
| 37 | DEGRADED | 1 | RUNNING | `Good Stress=1.0` |
| 49 | **DOWN** (0-of-3: A,C,D all down) | **2** | **IDLE** | `Idle change_block_state` |
| 62 | DEGRADED, but a SEPARATE ordinary Dependence-cascade fires same instant | 2 | RUNNING then re-idled | `Good Stress=1.0` immediately followed by `Idle Dependence` (different mechanism, empty-comment vs `"Dependence"`-tagged — not the downNum special case) |
| 77, 89, 101 | DEGRADED | 0 or 1 | RUNNING | `Good Stress=1.0` |

The two clean `t=12`/`t=49` instants are genuine downNum=2 parks at a
**3-incoming-path node**, reached because `BlockC`+`BlockD` are BOTH
down at the exact same instant `BlockA` (chain-linked to `BlockB`) is
ALSO still down — making `join` read fully `Bad`/`Down` (`0-1/3`), not
merely `Degraded`. Full event-log excerpt around `t=48-50`:
```
48.000000  Block  BlockC  Failed     Operating_time=9.000000
48.000000  Node   join    Bad        0-1/3_df3_df5_df7
48.000000  Node   end     Bad        0-1/1_df8
49.000000  Block  BlockB  PM_complete  MaintTime=2.000000
49.000000  Block  BlockB  Idle       change_block_state      <- direct-Idle park, empty comment
50.000000  Block  BlockA  Repaired   MaintTime=22.000000
50.000000  Block  BlockA  Good       Stress=1.000000
50.000000  Node   join    Idle       0-1/3_di3_df5_df7
50.000000  Node   end     Idle       0-1/1_di8
50.000000  Block  BlockB  Good       DependenceStress=1.000000   <- recovers once end comes back up
50.000000  Node   join    Degraded   1-1/3_ur3_df5_df7
50.000000  Node   end     Good       1-1/1_ur8
```

## The question

At `t=12` and `t=49` (and any other instant real's own event log shows
`join`/`end` reading fully `Bad`/`Down` right at `BlockB`'s own PM
completion, with `BlockA` also genuinely down at that instant), does
real ALSO show `BlockB` going directly to `Idle` with an EMPTY comment
(no `"Dependence"` tag), recovering only once `BlockA` (or whichever
sibling keeps the node down) itself repairs?

- **Matches this port's prediction** (direct-Idle park, recovers on
  `BlockA`'s own repair) → confirms `down_num`'s per-path,
  chain-shared-EFPA-link accumulation rule generalizes cleanly to a
  3-incoming-path node once the node is genuinely fully down — closes
  C.7 for good, and retroactively explains `ReleaseProbe6`'s own
  non-park as a construction artifact (documented above), not a port
  bug.
- **Real self-corrects instead** → the `_NODE_UP_LIKE`-gate-then-
  chain-shared-downNum mechanism this port implements is wrong in some
  way not yet identified for 3+ paths specifically — would need the
  full `/sd` RNG trace and a much closer read of `GetDownNum`
 / `RunEFPA` against
  real's own exact behavior at higher path counts.
- **Some other pattern** (e.g. real parks but WITHOUT the empty-comment
  distinction, or recovers at a different instant than `BlockA`'s own
  repair) → note the full real event log around `t=9-13` and `t=45-51`
  precisely; both windows are self-contained and directly comparable.

## Predicted (this port)

See "What this port's own run actually does" above — direct-Idle park
(`"change_block_state"`, empty comment) at `t=12` and `t=49`, self-
correcting `Good` everywhere else. `/sd`: `BlockB`'s own PM-duration
draws use its own dedicated stream pair per Raptor 7
conventions (same as every other fixture); nothing here depends on a
runtime RNG draw for the downNum question itself (all fail/repair
timings are `FIXED`).

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs), read the FULL
event log for `BlockA`/`BlockB`/`BlockC`/`BlockD`/`join`/`end` across
`t=9-13` AND `t=45-51` (both windows are self-contained instances of
the same scenario — if the first doesn't fully settle the question,
the second is a clean second look). Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe36.txt`.

## REAL ANSWER (2026-09-04 capture, `~/Documents/Raptor7/rpt_events_ReleaseProbe36.txt`)

**Confirms this port's own prediction exactly.** Real shows the SAME
two direct-Idle parks at the SAME instants, byte-identical comment:
```
11.000000  Node   join    Bad          0-1/3_df3_df5_df7
11.000000  Node   end     Bad          0-1/1_df8
12.000000  Block  BlockB  PM_complete  MaintTime=2.000000
12.000000  Block  BlockB  Idle         change_block_state
13.000000  Block  BlockC  Repaired     MaintTime=4.000000
13.000000  Node   end     Good         1-1/1_ur8
13.000000  Block  BlockB  Good         DependenceStress=1.000000
...
48.000000  Node   join    Bad          0-1/3_df3_df5_df7
48.000000  Node   end     Bad          0-1/1_df8
49.000000  Block  BlockB  PM_complete  MaintTime=2.000000
49.000000  Block  BlockB  Idle         change_block_state
50.000000  Block  BlockA  Repaired     MaintTime=22.000000
50.000000  Node   end     Good         1-1/1_ur8
50.000000  Block  BlockB  Good         DependenceStress=1.000000
```
Full-file comparison (3 runs, 564 real event lines vs 564 predicted,
after stripping the `Begin_Run_N` header lines this port doesn't emit
as a separate line): **546/564 exact matches (96.8%)**. The 18
mismatches are ALL the same-instant reordering of `BlockB`'s own
independent frequency-PM-begin vs `BlockC`'s own independent fail
(`t=35`/`t=87` each repeating run) — real puts `BlockC`'s own
`Failed→Repairing` cascade before `BlockB`'s `PM beginning_PM`; this
port puts them in the opposite order. This is the SAME general
same-instant tie-break question `A.1`/`ChooseNext`/`ActivityGroup`
already tracks as genuinely unsolved elsewhere in this project
(unrelated blocks with no dependency/PM connection to each other,
racing on row/registration order) — not a new gap, and NOT the downNum
mechanism this probe targeted, which matches perfectly.

**C.7 is now SOLVED for 3-incoming-path nodes too** — the root-cause
finding (the `_NODE_UP_LIKE`/`DEGRADED` gate requires ALL incoming
paths down before `down_num` is ever consulted, arity-independent) is
confirmed correct by direct real capture. `ReleaseProbe6`'s own
long-standing non-park is now fully explained as a construction
artifact (documented above), not a port bug — closes the last open
sub-question in C.7.

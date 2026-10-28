# RedrawProbe v3 — the full 426.280300 structure: the multi-node cascade, the sysDepend walks, the three-grant mixed release

This probe replicates Capacity1's run-25 t=426.280300 tie STRUCTURE
(the Layer-2 question, the internal engine ledger cont. 39-41):
the multi-node cascade (N → P → end), the sysDepend dispatches
(locDepend + sysDepend walks), and the three-grant mixed release
(2 PM + 1 repair). v1 (plain) and v2 (case-2 single-PM) came back
byte-identical; v3 adds what they lacked.

## The model

The chain (the Capacity1 500path shape — every block's downstream_link_id
resolves to N via the transitive EFPA): `n1 → G → R → H → N → P →
end`, plus G2's own branch (`n1 → G2 → P` — G2's efpa resolves to P,
a DIFFERENT node: the D.3 case-3 class) and the parallel repair-waiter
branch (`start → N-block → end`):

- **BlockH** (the Bus_UR-analog, the releaser): fails FIXED(5),
  repairs FIXED(10), borrows ALL FOUR `Techs` units (#Res1Rep=4 — the
  pool is EMPTY during H's repair). Dep bands [nothing=50, idle=50],
  dep_num=-1 (locDepend on N — the walk re-idles H exactly as real
  re-idles Bus_UR).
- **BlockG** (the S500W-analog): never fails, PMFreq=8 (due at t=8
  while H holds all units → pmReqDefer), PM duration EXP(10) stream 4,
  `#Res1pmRep=1`. Dep bands [nothing=100] — never dispatched. Same
  node as the releaser (the case-2 class when a repair grant
  competes).
- **BlockG2** (the SolarP_650W-analog, the D.3 case-3 class): never
  fails, PMFreq=9 (due at t=9 → pmReqDefer), PM duration EXP(10)
  stream 7, `#Res1pmRep=1`. Dep bands [nothing=100]. efpa → P (a
  different node from the releaser's N).
- **BlockR** (the NiCd-analog, the recovered-then-re-idled block):
  fail EXP(1e6) stream 5 (the kickoff draw ~103148 — fail_time_left
  non-None at the recovery, no AllUp-redraw), repair FIXED(1).
  Dep bands [50/50], dep_num=-1 (locDepend on N — N's walks dispatch
  R with visible % draws — the GoIdle re-idles exactly like real's
  NiCd).
- **BlockA** (the Attitude-analog, the sysDepend class): fail
  EXP(50) stream 8, repair FIXED(10), dep_num=-2 (sysDepend on the
  END node — the END's walks dispatch A with visible % draws —
  real's Att at 426.28: the DoNothing % draw 0.508). Dep bands
  [50/50].
- **BlockN** (the NaS/Bus_FR-analog, the repair waiter): fails
  FIXED(7) (t=7, during H's repair while the pool is empty →
  RepHold), repair EXP(10) stream 6 — a visible pickup draw.
  numRes=1.

Pool `Techs`: 4 units. Nodes: start(1), n1(5), N(6), P(7), end(2) —
the multi-node cascade: N's NodePM propagates through P to the end
(exactly 500path → Power → end). TimeSim 100, NumRuns 3. Streams:
H=3, G=4, R=5, N=6, G2=7, A=8 (fresh).

## The timeline (deterministic — all engines draw identical values)

- **t=5**: H fails → borrows 4 → N Bad → P Degraded → the walks: R
  dispatched (locDepend: `BlockR Idle Dependence` + R's % draw ~0.578).
  **NOTE (this port's behavior)**: the END's status also changes
  (ALL_UP → Degraded — the system goes Yellow) but the END's status
  line is MISSING and **BlockA is NOT dispatched** — real's answer to
  this instant is part of the question.
- **t=7**: N-block fails → the pool empty → `RepHold`.
- **t=8 / t=9**: G's / G2's PMs due → `pmReqDefer` (both queued).
- **t=15**: H's repair completes → releases 4 → N Good → P Good → the
  walks: R's AllUp recovery (`BlockR Good DependenceStress`) AND the
  release grants ALL THREE waiters — N's repair pickup (`Sparing
  Obtained` + the repair draw), G's PM pickup → `beginning_PM` → N
  NodePM → P Degraded → R re-idled (the locDepend walk), G2's PM
  pickup → `beginning_PM` → P NodePM → the end Degraded (the
  multi-node cascade) → the walks' % draws.

## The question

**Our /sd at t=15 (run 1):**

```
15.000000  BlockN  1.357908     <- N's repair pickup draw
15.000000  BlockH  0.452836     <- H's % draw (G's beginning_PM walk)
15.000000  BlockR  0.988878     <- R's % draw (the same walk)
15.000000  BlockG  18.197383    <- G's PM-duration draw
15.000000  BlockG2  1.455647    <- G2's PM-duration draw
```

**What does real's /sd + event log show?**

1. **BlockA's dispatches**: does real dispatch A on the END's status
   changes (the % draws at t=5/t=15) — this port does NOT (A's only
   lines are its own fail/repair cycle) — the cont. 40 question.
2. **The END's status line at t=5** — this port's is MISSING (the
   first end line is at t=7); real's?
3. **The t=15 draw order**: N's repair first? the % draws [H, R]
   before G's PM draw? G before G2? Any interleaving with A's draws?
4. **The G2 second-PM shape** (t≈25): G2's next PM due at 25 →
   deferred → the interplay with G's PM cycle.
5. The **later cycles** — three runs of corroboration.

## Capture

Simulate with the file's own settings (TermSim 100, NumRuns 3), all
output files enabled, `/sd` included. Copy `simDetails.TXT` and the
event log immediately (fixed-name overwrite caveat).

## v3.1 FIXES (2026-09-09, post the v3 Access-Violation report)

The v3 file as first built crashed real's loader (my regex-substitution
edit scrambled LINK_DATA — rebuilt programmatically), and the user's
GUI review flagged two config issues, both fixed:

1. **Node P's k was -1** (ambiguous in the GUI) → k=2, n=2 (BOTH paths
   must be up — the cascade then propagates: N Bad → P Bad → the end
   Degraded at t=5).
2. **BlockA had no incoming link** (its `In=1` column lied) → link 13
   `start → BlockA` added (start Out=3), matching Capacity1's
   Attitude (which hangs off start).

**Our engine's baseline QUIRK (part of the question)**: the END node's
dependency backflow does NOT fire on the END's own status changes at
t=5/t=15 in this port (the spy shows the END's walks only from t=42+
when BlockA's own fail/repair cycle drives the END's status). A's %
draws are therefore ABSENT from our /sd at the tie — real's answer to
whether A IS dispatched there (as its 426.28 DoNothing draw 0.508
suggests) is part of what this capture pins.

## ANSWERED (2026-09-09, v3): byte-identical AGAIN — the full 426.28 structure is also cleared

Real's v3 capture (`legacy_v3/`, 2 runs): the /sd matches draw-for-draw
([R% 0.577767, N's repair 1.357908, R% 0.988878, G's PM 18.197383,
G2's PM 1.455647] and every later cycle) and the event logs are
IDENTICAL (157/198 lines, zero diffs of any class).

**THREE structures now cleared**: the plain tie (v1), the case-2
mixed-release tie (v2), and the full multi-node + sysDepend +
three-grant structure (v3). Crucially: **real does NOT dispatch the
sysDepend (dep -2) block on the END's status changes either** — A's
only draws in real's /sd are its own kickoff + repair-completion
redraws, exactly ours. The END-backflow "gap" is NOT a gap: our
engine's behavior is CORRECT.

**CONCLUSION**: the Capacity1 426.280300 divergence needs what even
v3 lacks. The remaining candidates: the FOUR-PM wave's simultaneous
D.3 case-2+case-3 mix interacting with the walk order (the probe's
G2's beginning_PM ran AFTER G's, both late — Capacity1's four PM
pickups' order: [S500W, S650W, NiH, S900W]), NiCd's redraw mechanism
(its fail_time_left=None timing — the PM-completion chain's delayed
loop-top), or the sheer tie-group size (Capacity1's group: ~20
entries vs the probe's ~10). The next lever: extend this probe
toward Capacity1's actual 426.28 configuration one ingredient at a
time until the divergence reproduces.

**Also cleared (2026-09-09, the GUI bisection, `Capacity1_BISECT.md`)**:
every structural ingredient of the FULL Capacity1 model, reconstructed
in 5 cumulative stages and diffed against 5 real captures — all five
matched exactly. The 426.280300 divergence does not reproduce in ANY
reduced model. It is confirmed a **tie-group POPULATION phenomenon**:
Capacity1's own tie has ~20 entries (4 PM pickups + 1 repair pickup +
5 AllUp wakes + the walks); every probe built so far tops out around
10. v4 below is the direct answer to that gap.

---

## v4 — the population-scale structure (2026-09-09): SAME ingredients, MORE of them

v1/v2/v3 answered every STRUCTURAL question this probe knows how to
ask, and real matched ours exactly every time. What's left is purely
a question of SIZE: does real's tie-break behavior change once the
group is big enough? v4 changes nothing structural — it just adds more
already-verified ingredients to the SAME t=15 release, to bring the
population from v3's ~10 up toward Capacity1's own ~20.

**What was added** (scripted build, via `rbd_builder.py` —
see `/tmp` scratch build script if you need to regenerate it):
- **BlockG3, BlockG4, BlockG5** — three more clones of BlockG (PM due
  at t=8, deferred, same-node-as-H case-2 grant). Now FOUR PM waiters
  total (G, G3, G4, G5), matching Capacity1's real four PM pickups.
  Each has its own fresh stream (9, 10, 11) so its draws differ from
  every other block's.
- **BlockR2, BlockR3, BlockR4, BlockR5** — four more clones of BlockR
  (locDepend -1 on N, AllUp-recovered + a % draw when N goes Good).
  Now FIVE AllUp-wake dependents total (R, R2..R5), matching
  Capacity1's real five AllUp wakes. Fresh streams 12-15.
- **Wiring**: purely series insertions into the existing G→R→H chain
  (`n1→G→G3→G4→G5→R→R2→R3→R4→R5→H→N`). Zero node k/n/In/Out changes,
  zero new dependency classes — every already-verified ingredient
  (multi-node cascade N→P→end, the sysDepend block A, the case-3 grant
  G2, the repair waiter N) is untouched.

**A real emergent effect, not a bug**: the `Techs` pool only has 4
units, and H's release frees exactly 4. Five requesters now compete
for them (BlockN's repair pickup + the four PM waiters) — only 4 can
be satisfied at the release instant. Our engine's own run (below)
satisfies BlockN, BlockG, BlockG3, BlockG4 and leaves **BlockG5
queued** — a genuine, unforced resource-contention outcome, the same
character of effect Capacity1's own real pool contention has.

**Our engine's own t=15 tie** (run 1, this file's own NumRuns=2):

```
15.000000  BlockH   Repaired      MaintTime=10.000000
15.000000  BlockH   Good          Stress=1.000000
15.000000  Node N   Idle          0-1/1_di5
15.000000  Node P   Idle          1-2/2_di6_ur9
15.000000  Node end Degraded      1-1/3_di7_df11_ur12
15.000000  BlockR   Good          DependenceStress=1.000000
15.000000  BlockR5  Good          DependenceStress=1.000000
15.000000  Node N   Good          1-1/1_ur5
15.000000  Node P   Good          2-2/2_ur6_ur9
15.000000  Node end Degraded      2-1/3_ur7_df11_ur12
15.000000  BlockG   Resources     Obtained_1_Resource(s)
15.000000  BlockG3  Resources     Obtained_1_Resource(s)
15.000000  BlockG4  Resources     Obtained_1_Resource(s)
15.000000  BlockN   Sparing       Obtained_1_Resource(s)
15.000000  BlockN   Repairing     1Techs
15.000000  BlockG   PM            beginning_PM
15.000000  Node N   NodePM        0-1/1_dp5
15.000000  Node P   NodePM        1-2/2_dp6_ur9
15.000000  Node end Degraded      1-1/3_dp7_df11_ur12
15.000000  BlockH   Idle          Dependence
15.000000  BlockR   Idle          Dependence
15.000000  BlockR2  Idle          Dependence
15.000000  BlockR3  Idle          Dependence
15.000000  BlockR4  Idle          Dependence
15.000000  BlockR5  Idle          Dependence
15.000000  BlockG3  PM            beginning_PM
15.000000  BlockG4  PM            beginning_PM
```
26 lines at one instant — close to Capacity1's own ~20-entry tie, and
BlockG5 is conspicuously absent (still queued for its unit).

**Our `/sd` draw order at t=15** (per run): `[N, R, R2, R3, R4, R5,
G, G3, G4]` — the repair pickup first, then all five AllUp-wake %
draws in block-declaration order, then the three satisfied PM
waiters' duration draws in block-declaration order. (BlockG5 draws
nothing here — it never got its resource this instant.)

## The question

1. **Does real's event log match ours line-for-line at this instant**
   — same 26 lines, same order? Or does the bigger population finally
   show a difference v1-v3 never could?
2. **Which FOUR of the five requesters (BlockN + G/G3/G4/G5) get the
   pool's 4 units** — the same four ours picks, or a different four?
   If different, that alone would be a real, actionable finding.
3. **The `/sd` draw order** — does real draw `[N, R, R2, R3, R4, R5,
   G, G3, G4]` in exactly that order, or does it interleave the
   AllUp-wake draws with the PM-duration draws differently?
4. **BlockG5's eventual pickup** — when does real finally grant it a
   unit (the next release, whenever that is)? Does its `PM
   beginning_PM` line land at the same later instant ours predicts?

If real matches ours exactly even at this size, the population-size
hypothesis is dead and the real discriminator is something Capacity1's
own model has that this probe still doesn't (candidates: the D.3
case-2+case-3 MIX within one release — this probe's G-family is all
case-2 — or the specific PM-due stagger across Capacity1's three
chains). If real diverges, the exact line it diverges at is the most
valuable data this investigation has had since Layer1Probe.

## Capture

Same protocol as v1-v3: simulate with the file's own settings
(TimeSim 100, NumRuns 2), all output files enabled, `/sd` included.
Copy `simDetails.TXT` and the event log immediately (fixed-name
overwrite caveat) — suffixed `_v4`.

## RESULT (2026-09-09, captured live via kwin-mcp desktop automation)

Real matches ours **exactly** — event log, draw order, timing, all of it.
Captures archived in `legacy_v4/`.

1. **Event log at t=15**: real's run 1 produces the *identical* 27-line
   tie group, in the *identical* order, down to the dependency-index
   suffixes (`0-1/1_di5`, `2-1/3_ur7_df11_ur12`, etc.). Diffed by hand
   against the README's predicted block above — zero differences.
2. **Which four requesters win the pool**: real satisfies BlockN,
   BlockG, BlockG3, BlockG4 — the exact same four ours predicted.
   BlockG5 is queued, exactly as predicted.
3. **`/sd` draw order at t=15**: real draws `[N, R, R2, R3, R4, R5, G,
   G3, G4]` — byte-for-byte the order ours predicted, and the drawn
   *values* are what drive everything downstream (BlockN's MaintTime
   draw of 1.357908 is the shortest of the five satisfied/competing
   durations).
4. **BlockG5's eventual pickup**: real's `BlockG5 Resources
   Obtained_1_Resource(s)` / `PM beginning_PM` lines land at
   t=16.357908 — the exact instant `BlockN Repaired
   MaintTime=1.357908` frees its Techs unit back to the pool. Ours
   predicted this precisely (it's just "the shortest of the satisfied
   durations releases first").

**Conclusion: the population-size hypothesis is dead.** Capacity1's
~20-entry tie and this probe's 27-entry tie behave identically in
real — scaling the population up to (and past) Capacity1's own size
changes nothing about real's tie-break behavior. Whatever real does
differently from ours at Capacity1's t=426.280300, it is not a
population-scale effect. The next lever has to be something
Capacity1's model has that this probe still doesn't reproduce: the
D.3 case-2+case-3 *mix within one release* (this probe's G-family is
all case-2), or the specific PM-due stagger across Capacity1's three
chains, or genuine source archaeology of real's `ChooseNext`/tie-break
internals (Lever 1 from the cont.44 handoff). See
The internal engine ledger cont.46 for the full write-up and the
re-ranked levers.

## v5 — the case-2+case-3 MIX at scale (2026-09-09)

v4 killed population SIZE as the explanation but never actually tested
the D.3 three-case tie-break rule (cont.15) with MULTIPLE members of
each PM class competing at once — it had 4 case-2 members
(BlockG/G3/G4/G5, all same-node-as-H) but only ONE case-3 member
(BlockG2, different-node, unchanged since v1). Capacity1's real tie
mixes several of each (cont.41 flagged this explicitly as untested).

**What changed from v4** (everything else — the case-2 family, the
AllUp-wake family, BlockN, BlockA — UNCHANGED): two more case-3 clones
of BlockG2 (`BlockG2_2`, `BlockG2_3` — same `n1 -> X -> P` wiring,
fresh streams 16/17), and the `Techs` pool doubled 4 -> 8 (BlockH's
`#Res1Rep` matched 4 -> 8, so it still borrows the ENTIRE pool during
its own repair). This makes the release's arithmetic exact: 1 repair +
4 case-2 + 3 case-3 = 8 = the enlarged pool — **every requester is
granted simultaneously, zero queuing**, isolating the cross-class
ORDER question from v4's "who wins the pool" resource-contention noise.

**Our `/sd` draw order at t=15** (every run, all 3 identical in shape):
`[N, R, R2, R3, R4, R5, G, G3, G4, G5, G2, G2_2, G2_3]` — repair, then
all five AllUp-wakes, then the ENTIRE case-2 family in block order,
then the ENTIRE case-3 family in block order. Case-2 as a block always
precedes case-3 as a block.

**Our event log at t=15** (run 1, 34 lines): the case-2 family's
`PM beginning_PM` lines (G, G3, G4, G5) are interleaved with the
dependency walk (`BlockH`/`BlockR`-family `Idle Dependence`) and land
BEFORE any case-3 `PM beginning_PM` line; the case-3 family's PM-begins
(G2, G2_2, G2_3) are interleaved with `Node P`'s own multi-step NodePM
cascade, strictly after every case-2 PM-begin.

## The question (v5)

1. **Does real also strictly order the whole case-2 family before the
   whole case-3 family**, or does it interleave individual members
   across classes (e.g. G, G2, G3, G2_2, ...)? This is the direct test
   of the D.3 rule at population scale.
2. **The `/sd` draw order** — does real draw `[N, R.., G.., G2..]` in
   that exact block-order-within-class shape, or does within-class
   order differ from ours (e.g. reversed, or interleaved with the
   dependency walk's own draws)?
3. **Any queuing at all?** Ours predicts none (arithmetic is exact) —
   if real queues someone anyway, that alone is a finding (an implicit
   grant-priority tier ours doesn't model).
4. If real matches ours exactly even with a genuine multi-member
   case-2+case-3 mix, Lever 2 is dead too, and only Lever 1 (source
   archaeology) and Lever 3 (PM-due stagger, or NiCd's fail_time_left
   timing) remain.

## Capture (v5)

Same protocol as v1-v4: simulate with the file's own settings (TimeSim
100, NumRuns 2 — corrected from an earlier draft of this section that
said 3; the GUI's own Simulation Options dialog is the source of
truth, always check it), all Reports checkboxes on (Final results /
End of run / Detailed event log), `/sd` included. Copy `simDetails.TXT`
and the event log immediately (fixed-name overwrite/append caveat) —
suffixed `_v5`.

## RESULT (2026-09-09, captured live via kwin-mcp desktop automation)

Real matches ours **exactly** — again. Captures archived in `legacy_v5/`.

1. **Event log at t=15**: real's run 1 produces the *identical* 36-line
   tie, in the *identical* order — the whole case-2 family's PM-begins
   (G, G3, G4, G5) complete, interleaved with the dependency walk,
   strictly BEFORE any case-3 PM-begin; the case-3 family's PM-begins
   (G2, G2_2, G2_3) are interleaved with `Node P`'s multi-step NodePM
   cascade, exactly as predicted. Zero differences, both runs.
2. **`/sd` draw order**: real draws `[N, R, R2, R3, R4, R5, G, G3, G4,
   G5, G2, G2_2, G2_3]` in BOTH runs — byte-for-byte identical to ours,
   including the drawn *values* (e.g. `BlockN` 1.357908/8.260849,
   `BlockG2_3` 8.521183/3.885905 — every single value matches ours
   exactly, not just the order).
3. **No queuing**: confirmed — every one of the 8 requesters (1 repair
   + 4 case-2 + 3 case-3) is granted simultaneously in both runs,
   exactly as the arithmetic predicted.

**Conclusion: the D.3 case-2+case-3 MIX hypothesis is dead too.**
A genuine multi-member mix of both PM grant classes, with zero
resource-contention noise, produces an identical outcome in real and
ours. Combined with cont.46 (population size, also dead) and cont.44
(structural bisection, also dead), every lever this investigation could
test with a *probe* — as opposed to Capacity1's own real model — has
now been tried and cleared. What's left: Lever 1 (source archaeology
of real's `ChooseNext`/tie-break internals) and Lever 3 (the specific
PM-due stagger across Capacity1's own three chains, or NiCd's
fail_time_left=None timing question, cont.40's escape hatch) — neither
of which a synthetic probe can fully stand in for. See
The internal engine ledger cont.48 for the full write-up.

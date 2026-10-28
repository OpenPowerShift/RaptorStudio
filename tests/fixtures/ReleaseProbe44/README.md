# ReleaseProbe44 — A.4's reentrant switch-interrupt oscillation, isolated

## What this tests

A.4 (the internal engine ledger) is a long-standing, high-risk, previously-
unresolved bug class: when a cold-standby node's async switch gets spawned and
then something else re-triggers `ReconfigureNode` for the same link within the
same top-level cascade turn, this port's own `nodes.reconfigure_node` fires an
"in-progress-switch interrupt" that real doesn't
seem to. This was found and confirmed across THREE real fixtures this session
(`CS_SwitchFail`, `CS_NoAutosFirst`, `ColdStandbyProbe2`) — all three show a
premature `link 8`/`SysStream11` `SetSwitchTime` redraw at the standby
candidate's OWN repair-completion instant, instead of waiting for the primary
candidate's later, genuine recovery.

Exhaustive line-by-line verification against Raptor 7 itself (`GetDownNum`,
`ReconfigureBlock`→`ReconfigureNode`'s own delta table, `ReconfigureNode`'s own
interrupt condition, `PerformSwitch`'s own `ON INTERRUPT` handler, `CheckColds`'s
own drain loop) found every ported condition matches source exactly — yet real
doesn't reproduce the interrupt. This probe isolates the EXACT scenario (a
cold-standby pair where the standby member fails and repairs WHILE the primary
member is ALSO down) from the three existing fixtures' own richer topology
(shared resource pools, separate dependency chains, PM, phasing) to get a
clean, minimal, fully-deterministic real-capture data point.

## Construction

Cloned from `tests/golden/HierarchyExample/input.rbd` (the ORIGINAL fixture
this whole cold-standby module was built and verified against, 2026-08-19) —
per this project's own established lesson, touching only already-exercised
scalar fields, not hand-building topology. Changed:

- `LowGain` (the cold-standby/switching candidate, `SBStress=0.05`): `FDist`
  4→19 (EXPONENTIAL→FIXED), `FParam1` 12350.0→**0.005** (fails almost
  instantly while Standby: `0.005/0.05 = 0.1` wall-time); `RDist` 7→19
  (LOGNORMAL→FIXED), `RParam1` →**1.0** (deterministic 1.0 repair duration).
  Pre-LDT/Post-LDT were ALREADY `FIXED(0.5)` in the base fixture, untouched.
- `HighGain` (the always-primary candidate, never itself cold-standby-switched):
  `FDist` 4→19, `FParam1` 350.0→**1.0** (fails at `t=1.0` — deliberately
  BEFORE `LowGain`'s own repair completes at `t=2.1`, forcing the overlapping-
  double-failure scenario); `RDist` 10→19, `RParam1` →**1.0**. Pre-LDT/Post-LDT
  were already `FIXED(2.0)`/`FIXED(1.0)`, untouched (matches `CS_SwitchFail`'s
  own values exactly).
- `SYSTEM_DATA`: `NumRuns` 10→**1** (single deterministic run is enough),
  `TermCondition` 730.5→**6.0** (short mission — just long enough to observe
  `HighGain`'s own first recovery at `t=5.0`, avoids the later repeat-failure
  cycling this FIXED-distribution construction otherwise produces past that
  point).

Link 8 (`LowGain`→`Comm`) keeps its ORIGINAL `AProb=0.98`/`AutoTime=0.5` —
untouched, matching `HierarchyExample`'s own already-verified values.

Verified: diffed field-by-field against the base clone (only the fields
listed above changed, confirmed via `rbd_builder.get_row` comparison — no
other `BLOCK_DATA` row touched); `rbd_builder.validate()` clean; this port's
own run bounded (103 event-log lines, 1 run, no runaway).

## This port's own predicted output

Event log (`Block`/`Node` lines only, LowGain/HighGain/Comm):

```
0.000000   LowGain   Good        InitializeStress=1.000000
0.000000   LowGain   Standby     Too_many_on
0.000000   Comm      Good        1/2_up15_s8
0.000020   LowGain   Failed      Operating_time=0.000020
0.500020   LowGain   Repairing   1Astronauts
1.000000   HighGain  Failed      Operating_time=1.000000      <- HighGain fails WHILE LowGain still repairing
1.000000   Comm      Bad         0/2_df15_df8
1.500020   LowGain   Repaired    MaintTime=1.000000
2.000020   LowGain   Good        Stress=1.000000               <- LowGain's own repair completes
2.000020   Comm      Bad         0/2_df15_dsw8                 <- switch spawns (autoInProgress)
2.000020   LowGain   Idle        change_block_state             <- self-correct: own dependency still down
2.000020   LowGain   Good        DependenceStress=1.000000      <- nets back to Running, same instant
2.005020   LowGain   Failed      Operating_time=0.005000       <- BUG: fails again almost instantly
2.505020   LowGain   Repairing   1Astronauts
3.000000   HighGain  Repairing   1Astronauts
3.505020   LowGain   Repaired    MaintTime=1.000000
4.005020   LowGain   Good        Stress=1.000000
4.005020   Comm      Bad         0/2_df15_dsw8                 <- same pattern repeats
4.005020   LowGain   Idle        change_block_state
4.005020   LowGain   Good        DependenceStress=1.000000
4.010020   LowGain   Failed      Operating_time=0.005000       <- fails again, still mid-repair at t=5.0
4.510020   LowGain   Repairing   1Astronauts
5.000000   HighGain  Good        Stress=1.000000                <- HighGain's own genuine recovery
5.000000   Comm      Degraded    1/2_up15_df8                   <- LowGain's link still down (mid its 2nd repair)
5.510020   LowGain   Repaired    MaintTime=1.000000
6.000000   HighGain  Failed      Operating_time=1.000000        <- mission ends here (TermCondition=6.0)
```

`/sd` draws (`link 8` is real's own name for this port's `SysStream11` tag —
same value, same mechanism, `tools/sd_diff.py`'s own established alias):

```
0.000000   link 8   0.025303      <- kickoff SetSwitchTime (auto, <0.98)
2.000020   link 8   0.822104      <- THE QUESTION: does real also draw here?
2.005020   link 8   0.310979      <- LowGain's own genuine demotion (Standby<-Running) -- NOT in question
4.005020   link 8   0.790641      <- same pattern repeats (2nd cycle)
4.010020   link 8   0.940435      <- 2nd genuine demotion
```
(no draw at `t=5.000000`, `HighGain`'s own recovery — by then `LowGain`'s
link has already demoted itself via its own 2nd failure, so there's nothing
"in progress" left to interrupt)

## The question

**Primary**: does real's own `simDetails.TXT` show a `link 8` draw at
`t=2.000020` (matching this port's own prediction above), or does the FIRST
`link 8` draw after kickoff (`0.025303`) land somewhere else?

**Secondary** (a separate, likely-unrelated mechanism, but observable in the
same capture for free): does `LowGain` genuinely fail again at `t=2.005020`
(`Operating_time=0.005000`), or does it stay `Good`/Running past that point?

## REAL ANSWER (captured 2026-09-10, this same session -- self-captured via
kwin-mcp desktop automation, not a user round-trip)

**Decisive.** Real's `simDetails.TXT` (`tests/golden/ReleaseProbe44/legacy/simDetails_ReleaseProbe44.txt`)
shows exactly:
```
0.000000  link 8  0.025303
1.000000  Attitude  0.779210
6.000000  Attitude  0.603136
```
**ZERO further `link 8` draws anywhere in the whole 6-hour run** — not at
`t=2.000020`, not at `t=2.005020`, not at `t=4.005020`/`4.010020`, not at
`t=5.000000`. This port's own PREDICTED event log (this port's `Idle`/`Good`
self-correct dance, `LowGain`'s repeated `Operating_time=0.005000` re-fails,
the exact instant sequence above) matched real's own event log
(`tests/golden/ReleaseProbe44/legacy/rpt_events_ReleaseProbe44.txt`) BYTE-EXACT — confirming the
SECONDARY question's answer (`LowGain` DOES genuinely fail again at
`t=2.005020`/`4.010020`) — while the `/sd` draw sequence diverged completely:
this port (pre-fix) drew all 4 predicted values; real drew none of them.

This resolves the PRIMARY question decisively in the "real does NOT draw"
direction, and sharpens it further: it's not just `t=2.000020` that's
spurious (the flicker case this README's own prediction focused on) — `t=
2.005020`/`4.010020` (LowGain's own SEPARATE, genuine re-failure while its
switch is still in-flight) are ALSO spurious in this port, a second,
narrower sub-case the original prediction didn't distinguish from the first.

**Fix landed same day** (`orchestrator.py`'s `_interrupt_switch_from_
reconfigure`, grounded in SIMSCRIPT II.5's own `interrupt` statement
semantics — see the internal engine ledger A.4's own entry for the full
citation and mechanism) resolves the FIRST sub-case (`t=2.000020`/`4.005020`,
the flicker-triggered one) cleanly — this port's own draw count dropped
21->19.

**SECOND sub-case CLOSED 2026-09-11 — this fixture now matches real
BYTE-EXACT on BOTH streams** (all 17 `/sd` draws AND the full event log;
17, not 21, because the capture had this same 1-run dump appended three
times and the golden copy at `tests/golden/ReleaseProbe44/legacy/` is now
sliced to its first `RunEFPA` block). The residual was NOT "LowGain's own
genuine re-failure while switching" at all — the last spurious draw was
`Attitude`'s, at `t=4.010020`, and it was a CONSEQUENCE of four spurious
event-log lines one instant earlier at `t=4.005020`
(`LowGain Idle Dependence` / `Comm Idle 0/2_df15_di8` /
`Attitude Good DependenceStress` / `LowGain Good DependenceStress`).

Root cause: the `IgnoreDep` lifetime. Compare this fixture's own
`t=2.000020` cycle (correct) against its `t=4.005020` cycle (wrong) — the
event sequences are identical and the ONLY differing state is
`LowGain.ignore_dep`: `True` at 2.000020 (a switch had just re-spawned via
`PrepForSwitch`), `False` at 4.005020 (no new switch spawns, because link 8
is still frozen `autoInProgress` from the first cycle, and the v6 fix had
already cleared the flag). With it `False`, `ResendBackFlow`'s
`specialBackFlow` replay re-idles `LowGain` (`previous_dependency_state=="GoIdle"`),
flipping link 8 to `LinkIdle`, which makes `Comm` and then the END node
read `NodeIdle` — a RECOVERY trigger — promoting `Attitude` out of Idle a
full time unit early; `Attitude`'s next dependency roll at `t=4.010020` is
the extra draw. Real's `Attitude` is still Idle there and recovers at
`t=5.000000`, where `HighGain` genuinely returns.

The fix is a literal port of `RunColdLogic`'s own two `SetIgnoreDep`
statements (and `3046-3059`), which this port had
never carried, plus removal of v6's unconditional `block.ignore_dep = False`
stand-in. Full write-up: the internal engine ledger A.4, "Current
mechanism, part 2 (v7)".

This fixture and `ReleaseProbe45` are now registered golden fixtures
(`tests/golden/ReleaseProbe44`/`45`, `num_runs=1`), so
`tests/unit/test_sd_diff_sweep.py` guards the fix directly. Their captures
moved from `tests/fixtures/<name>/legacy/` to `tests/golden/<name>/
legacy/` accordingly; the `.rbd` and this README stay here, matching the
layout `tools/golden_fixtures.py` documents.

### What each answer means

- **Real matches this port's prediction EXACTLY** (draws at `2.000020`,
  `2.005020`, `4.005020`, `4.010020`, nothing at `5.000000`; `LowGain` fails
  again at `2.005020`/`4.010020`): would mean A.4's mechanism, AS ISOLATED
  HERE, is NOT actually a divergence from source — the exhaustive source
  verification (every condition checked line-by-line, all matching) would be
  vindicated, and `CS_SwitchFail`/`CS_NoAutosFirst`/`ColdStandbyProbe2`'s own
  divergence must come from something ELSE in their own richer topology
  (shared `Astronauts` pool contention, `Attitude`'s own separate dependency
  chain, or something not present in this minimal probe) — a genuinely
  different, narrower question than A.4's current framing.
- **Real does NOT draw at `t=2.000020`** (the first post-kickoff `link 8`
  draw lands later — plausibly at `t=5.000000`, `HighGain`'s own genuine
  recovery, matching the pattern the three existing fixtures show): CONFIRMS
  the premature-interrupt bug precisely, in total isolation from every other
  confound — the cleanest possible ground truth for designing A.4's eventual
  fix. Also worth checking whether `LowGain` correspondingly does NOT fail
  again at `t=2.005020` in this scenario (since it would still be `cold_path_
  done=False`/actively-switching rather than genuinely Running with a fresh
  drawn life) — that would additionally show the "genuine redraw" and the
  "genuine demotion" are the SAME single event in source, not two.
- **Something else entirely** (a totally different pattern, e.g. `LowGain`
  never fails again, or the switch resolves differently): a new, unpredicted
  finding — re-derive from the actual real event log rather than assuming
  either scenario above.

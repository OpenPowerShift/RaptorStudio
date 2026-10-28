# Layer1Probe — at a same-instant tie, does real pop a wave continuation (grant pickup's `beginning_PM`) or a carried natural fail?

This probe isolates the **Layer-1 same-instant tie-break question**
(the internal investigation log / the internal engine ledger cont. 34-36)
that the Capacity1 40-run campaign narrowed to two contradictory real
patterns:

- **Wave-first** (Capacity1 runs 6/9/10): a grant target's PM-begin
  continuation pops BEFORE a same-instant natural fail expiry whose
  scheduler entry was inserted ~85/362 time units earlier
  (`S900W beginning_PM` before `Bus_QR Failed`; `S650W/NiH beginning_PM`
  before `Attitude Failed`).
- **Carried-first** (Capacity1, /sd position 233, t=71.675997): the
  carried natural fail pops BEFORE the wave continuation
  (`BatteriesNaS`'s fail-repair draw before `SolarP_900W`'s
  PM-begin draw).

Neither FIFO-by-seq, min-block-id, nor the off-by-one ChooseNext scan
explains both. This fixture reproduces the EXACT tie shape with fully
known group contents, so ONE capture pins the rule.

## The model

`ReleaseProbe20` byte-preserved plus ONE added block (`BlockD2`, id 9)
wired `start→D2→end` (links 9/10; `start.Out=4`, `end.n=3`,
Blocks=5, Links=10):

- **BlockA** (id 3): fails FIXED 3.0, repairs FIXED 22.0, borrows BOTH
  `Techs` units at t=3, releases them at **t=25** (its repair
  completion) — the releaser.
- **BlockB** (id 4): never fails; trigger PM at t=4 (unchanged from
  probe-20).
- **BlockC** (id 6): fails FIXED 3.5, repair-waiter (`RepHold`
  Waiting_for_resource(s) at 3.5), granted at t=25 (unchanged).
- **BlockD** (id 8): never fails; frequency PM due t=10, pool empty →
  `pmReqDefer No_resource_in_stock-Techs`; granted at t=25;
  PM duration EXP(10) stream 8 (unchanged — the deferred-PM wave
  member).
- **BlockD2** (id 9, NEW): fails **FIXED 25.0** (fails at exactly
  t=25.0 — the tie instant), repairs FIXED 10.0, no resources, no PM.
  Its fail-wait entry was inserted at t=0 (kickoff) — a CARRIED entry
  whose seq is SMALLER than every entry pushed at the tie instant.

Streams: D2 fail stream 9, repair stream 10 (fresh; A/B/C/D streams
untouched from probe-20, so probe-20's own draw-order findings remain
cross-checkable).

## The tie at t=25.0

Group contents (in this port's seq order):

1. `BlockD2`'s fail-wait expiry — carried, seq from t=0
2. `BlockA`'s repair-completion — carried, seq from t=3
3. `BlockC`'s `Pool_Available` pickup — wave (interrupt queued during
   A's turn), late-tagged per D.3
4. `BlockD`'s `Pool_Available` pickup — wave, late-tagged per D.3
5. `BlockC`'s continuation (`Sparing`/`Repairing`) — wave requeue
6. `BlockD`'s continuation (`beginning_PM`) — wave requeue
   (sticky-late)
7. `BlockD2`'s repair entry — its own turn's continuation

**This port's output (seq order)**, run 1:

```
25.000000  BlockD2  Failed            Operating_time=25.000000
25.000000  BlockA   Repaired          MaintTime=22.000000
25.000000  BlockA   Good              Stress=1.000000
...        (A's node cascade)
25.000000  BlockD2  Repairing         No_resources_used
25.000000  BlockD   Resources         Obtained_1_Resource(s)
25.000000  BlockC   Sparing           Obtained_1_Resource(s)
25.000000  BlockC   Repairing         1Techs
25.000000  BlockD   PM                beginning_PM
```

## The question

**Does real log `BlockD PM beginning_PM` BEFORE or AFTER
`BlockD2 Failed`/`BlockD2 Repairing`?**

- **D2's `Failed` first** (this port's order): the carried-first class
  (t=71.676) generalizes — the tie-break is plain seq FIFO and the
  runs 6/9/10 wave-first instants need a DIFFERENT discriminator
  (something about those specific instants, not the tie rule itself).
- **D's `beginning_PM` first** (the runs 6/9/10 pattern): the
  wave-first class generalizes — the tie-break promotes the release
  wave's continuations over carried entries. Since D's id (8) is LOWER
  than D2's (9), this outcome is ALSO what source's ChooseNext
  min-block-id (with or without the off-by-one) predicts — so it
  would confirm the ChooseNext family and justify the owner-tagging
  campaign for the full port.

Secondary read-outs (bonus, same capture):

- `BlockD2 Failed` vs `BlockA Repaired` ordering (both carried;
  D2's seq is smaller — FIFO predicts D2 first).
- The `BlockC`/`BlockD` pickup order at t=25 (probe-20 already
  answered this: `[D Obtained, C Sparing, C Repairing, D beginning_PM]`
  — a cross-check that the added D2 didn't disturb it).
- 3 runs: the tie repeats every run (all Fixed distros); only C's
  repair draw and D's PM duration vary.

## Capture

Simulate with the file's own settings (TermSim 30.0, NumRuns 3), all
output files enabled, `/sd` included. Copy `simDetails.TXT` and the
event log immediately (fixed-name overwrite caveat).

## ANSWERED (2026-09-08): byte-exact 3/3 — carried-first, seq-FIFO

Real's capture (`legacy/rpt_events_Layer1Probe.txt`) matches this port
line-for-line in all 3 runs: `BlockD2 Failed` BEFORE `BlockD PM
beginning_PM`, and the /sd draw order [C's repair 1.357908, B's
next-fail, D's PM 8.484571] identical (real's file carries two identical
RunEFPA dumps — an append re-run; readout unaffected).

**The clean-tie rule is seq-FIFO (carried-first)** — the t=71.676 class
generalizes; this port's order is CORRECT for natural carried entries.
The Capacity1 runs-6/9/10 wave-first pattern is a DIFFERENT entry class:
direct instrumentation shows those fails are **INDUCED**
(`Induced_Failure` interrupts from the PM cascades — the fail line logs
at the interrupt's delivery position, not the fail-wait entry's seq
position). Natural-carried vs induced never conflicted. Remaining
residual: run 10's Bus_QR natural fail (no induction) pops 1-2
positions earlier than real's — see cont. 37 in
The internal engine ledger.

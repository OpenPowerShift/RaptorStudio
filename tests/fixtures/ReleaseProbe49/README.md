# ReleaseProbe49 — does a `Too_many_on` demotion's fail-time redraw belong to the demoting turn, or a later one?

## What this tests

The question `ReleaseProbe47`/`48` left behind. Those proved this port's
same-instant tie-break is CORRECT (FIFO-by-insertion; neither
`ChooseNext`'s lowest-block-Id nor continuation-first governs), which means
`CS_HigherCold`'s residual divergence at `t=66.234574` is **not an ordering
bug** — the engines must differ in how many scheduler hops a single turn
takes.

The suspect sequence, from `CS_HigherCold`'s own real capture: a
cold-standby candidate finishes its repair, goes `Good`, and the cold
survey running inside that same turn immediately demotes it
`Standby Too_many_on`. Its previous life was consumed by the failure, so it
needs a fresh fail-time draw. **Real puts an unrelated block's draw BETWEEN
the demotion and that redraw; this port emits them back to back.**

- **Hypothesis S (same turn)** — this port today: the demoted block's
  process runs straight through the cascade and redraws before yielding.
- **Hypothesis D (deferred)**: the turn ends at the demotion and the redraw
  lands in a LATER turn, consistent with source's own
  `SetInterruptReason("Switched_Off"); IF FailWait: Interrupt(block,"Run")`
 being a QUEUED interrupt, not a synchronous
  continuation.

## Construction

Cloned from `ReleaseProbe45` (HierarchyExample-derived, byte-exact against
real, so the cold-standby wiring is known-good and untouched). Built through
`tools/rbd_builder.py`; `validate()` clean.

**The isolation this probe is built around.** An earlier attempt cloned the
same template but used `Attitude` as the observer, which sits in the main
series path — its failure downs the system, which idles the `sysDepend`
blocks, which re-triggers the cold switch-on, which MOVES the very instant
being aimed at. That attempt never converged and errored real Raptor. Here
the observer is `SolarP_500W`, on the `500path` branch under `Power`, which
is **k=2 of n=3**: its failure degrades `Power` without downing the system,
so nothing reaches `Comm`. It also has `DepNum`=0 and `NumRes`=0, so it
joins no dependency cascade, contends for no pool, and never takes
`Repair`'s `WAIT DURATION 0.0` hop.

- `SYSTEM_DATA`: `NumRuns`=**1**, `TermCondition`=**10.0**.
- Every block except `LowGain` and `SolarP_500W`: `FDist`
  **FIXED(1000.0)**, `DepNum`=0 — never fails in a 10-hour mission and is
  skipped by `DrawNumber`'s `currentDistro<>19` gate, so the capture stays
  minimal.
- `HighGain` (PRIMARY, Id 7): FIXED(1000.0) — never fails, so `Comm`'s
  primary is always up and `LowGain`'s return from repair is
  unconditionally a "too many on" demotion.
- `LowGain` (DEMOTED candidate, Id 6): `FDist`
  **UNIFORM_REAL(1.0, 1.0)** — a degenerate range is still a REAL draw
  (so every redraw shows in `/sd`, unlike FIXED) but is exactly
  deterministic, which is what puts the demotion on a round instant.
  `SBStress` **0.5** ⇒ wall life 2.0; repair FIXED(2.0); LDTs FIXED(0);
  **`AmtExhausted` reset to 0.0** (the clone hazard documented in
  the internal engine ledger's `.rbd` gotchas — inheriting the
  template's 2000.0 against a 1.0 life is what errored real Raptor).
  ⇒ fails at `t=2.0`, repairs, returns and is demoted at **`t=4.0`**.
- `SolarP_500W` (OBSERVER, Id 1): `FDist` **FIXED(3.0)**, `Pre-LDT`
  **FIXED(1.0)** ⇒ its pre-LDT timer is pushed at `t=3.0` and matures at
  **`t=4.0`** — the same instant, with a LATER insertion sequence than
  `LowGain`'s repair timer (pushed at `t=2.0`). `RDist`
  UNIFORM_REAL(0,1).

## This port's own predicted output

Event log at the instant:
```
4.000000  Block  LowGain      Repaired     MaintTime=2.000000
4.000000  Block  LowGain      Good         Stress=1.000000
4.000000  Block  LowGain      Standby      Too_many_on
4.000000  Node   Comm         Good         1/2_up15_s8
4.000000  Block  LowGain      StessLevel   CombStress=0.500000
4.000000  Block  SolarP_500W  End_Pre-LDT  Time=1.000000
4.000000  Block  SolarP_500W  Repairing    No_resources_used
```
`/sd` at the instant:
```
4.000000  SysStream11  0.822104
4.000000  LowGain      1.000000
4.000000  SolarP_500W  0.547693
```

## The question

**At `t=4.000000`, does real draw `LowGain` before `SolarP_500W`
(hypothesis S, matching this port) or `SolarP_500W` before `LowGain`
(hypothesis D)?**

Two independent signals, both in one capture:
1. the `/sd` ORDER of those two lines;
2. `SolarP_500W`'s VALUE. Both blocks draw from shared stream 203, and
   `LowGain`'s degenerate `1.000000` is position-independent — so if real
   draws `SolarP_500W` first it takes the position `LowGain` takes here
   and its value will NOT be `0.547693`.

The event log corroborates a third way: under S, `LowGain StessLevel`
precedes `SolarP_500W End_Pre-LDT`; under D, the observer's two lines come
first.

### What each answer means

- **`LowGain` then `SolarP_500W`** (matching this port): the redraw IS
  same-turn and this port is right here too. `CS_HigherCold`'s
  `t=66.234574` divergence is then something narrower than the generic
  demotion-redraw shape, and the next suspect is the `Attitude` side of
  that instant (its resource grant / hop count), not `LowGain`'s.
- **`SolarP_500W` then `LowGain`**: the redraw is DEFERRED to a later
  turn — source's `Interrupt(block,"Run")` at the demotion really does end
  the turn. That is a located port gap with a named source line, and it
  explains `CS_HigherCold` directly. The fix would make the demoted block
  park and be resumed by its own queued `Switched_Off` interrupt instead of
  falling through, with the full sweep as the gate since every
  cold-standby fixture exercises this path.
- **Anything else**: re-derive from the capture before theorising.

## REAL ANSWER (captured 2026-09-11 via kwin-mcp, this same session)

**`SolarP_500W` then `LowGain` — HYPOTHESIS D. The redraw is DEFERRED to a
later turn, and this port is WRONG here.**

```
4.000000  link 8       0.822104
4.000000  SolarP_500W  0.189819     <- the observer's matured timer runs FIRST
4.000000  LowGain      1.000000     <- the demotion's redraw lands AFTER it
```
against this port's
```
4.000000  SysStream11  0.822104
4.000000  LowGain      1.000000     <- redrawn inside the demoting turn
4.000000  SolarP_500W  0.547693
```

**Both predicted signals agree**, which is what makes this airtight:
1. the ORDER is reversed, exactly as hypothesis D predicts;
2. `SolarP_500W`'s VALUE is **0.189819** in real vs **0.547693** here —
   the two blocks share stream 203, and `LowGain`'s degenerate `1.000000`
   is position-independent, so real's observer is demonstrably taking the
   stream position this port gives to `LowGain`. The shift repeats at the
   next cycle (`8.189819` vs `8.547693`), so it is systematic, not a
   one-off.

### What it means

Source's `Too_many_on` branch ends the demoted block's turn:
```
ASK block TO ChangeBlockState(Standby,activeStatus,"Too_many_on");
...
IF SimTime>0.0
   ASK block TO SetInterruptReason("Switched_Off");
   IF block.FailWait
      Interrupt(block,"Run");          <- Raptor 7
```
That `Interrupt` is a QUEUED delivery, not a synchronous continuation — so
the demoted block PARKS at the demotion and is resumed in a later turn,
where it then redraws. This port instead falls straight through from the
cascade into the redraw, because the `yield 0.0` guarding that path is
gated on `was_idle` (source's `IF ((opStatus=Idle) AND (FailTimeLeft=0.0))`,
Raptor 7) and a `Too_many_on` block is **Standby**, not
Idle — so it never takes the hop.

This is a located gap with a named source line, and it explains
`CS_HigherCold`'s `t=66.234574` residual directly: there too, real slots an
unrelated block's draw between the demotion and the redraw.

**Blast radius is wide** — every cold-standby fixture in the corpus reaches
this path — so the fix needs the full sweep as its gate, and this probe is
the tripwire that says whether it landed.

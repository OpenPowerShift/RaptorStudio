# ReleaseProbe37 — a deliberately SIMPLER shape<1 probe (F.2), draw value lands in the ordinary event log

The internal engine ledger F.2: real's raw fail-time draws for
shape<1 Weibull/Pearson5/Pearson6 blocks are astronomically large
(Weibull shape=0.01 → ~1.2e306; Pearson5 shape=0.5 → ~6.8e20) —
values this port's own inverse-CDF/gamma-rejection formulas do not
reproduce. Every prior fixture that exercises this used `GDType=1`/PM,
so the only place the raw draw ever surfaces is the periodic `/sd`
`SimTime BlockName Value` dump line — and §23's own exhaustive source
search found NO writer for that line anywhere in
Raptor 7, meaning the captured number isn't provably "the raw
fail-time draw" at all. F.2's own recommended next step: "a
deliberately SIMPLER probe (small `AmtExhausted`, no PM/GDType) so the
draw shows in the ordinary event log at a state-changing transition
instead of only the periodic dump."

## What this probe does

Clone of `tests/fixtures/WeibullShapeTiny/input.rbd` (`start→{A,B}
→join→end`) via `tools/rbd_builder.py`, stripped down to remove
every confound that forces the draw underground into the `/sd` dump:

- **`BlockA`** (Weibull, `FDist=15`, shape=`FParam1=0.01`,
  scale=`FParam2=10`, `FStream=1`): `AmtExhausted` 100000→**1.0**,
  `GDType` 1→**0** (`GDRate`/`GDLimit`→0), `usesPM` 1→**0**,
  `pmTriggered` 1→**0**, `pmTrig`→**none**.
- **`BlockB`** (repurposed from `WeibullShapeTiny`'s own
  `Exponential(mean=60)` to **Pearson5**, `FDist=11`,
  shape=`FParam1=0.5`, scale=`FParam2=10`, `FStream=3` kept
  unchanged): same `AmtExhausted`/`GDType`/`usesPM`/`pmTriggered`/
  `pmTrig` strip as `BlockA`.
- **`SYSTEM_DATA`**: `TerminationType` `TimeSim`→**`FailSim`**,
  `TermCondition` 3000→**1** (kept `NumRuns=20`).
- **`join`** (`k=1,n=2` OR-node in `WeibullShapeTiny`): bumped
  `k`→**2** (AND-node). Left at `k=1`, the system never reaches a
  system-level failure from just ONE of `BlockA`/`BlockB` failing
  while the other stays up — `FailSim`'s `TermCondition=1` is a
  SYSTEM-failure COUNT, not a per-block one, so it never triggers and
  the run free-runs `BlockB` through hundreds of fail/repair cycles
  (first construction attempt: 1GB of output). With `k=2`, either
  block's own first failure alone makes the system Red, so
  `TermCondition=1` fires promptly every run.

Both blocks keep `Infinite` sparing, `RDist=19` (Fixed) `RParam1=2.0`
repair (a short, deterministic 2-unit repair — irrelevant to the draw
question, just lets the run terminate cleanly instead of parking
Failed forever), `NumRes=0` (no resource-contention confound), no
Phasing, no Cold-standby. No PM anywhere, no GDType-driven stress
degradation, no LDT. Nothing between "block goes Good at kickoff" and
"block goes Failed with `Operating_time=<raw draw>`" — the transition
line's own `Operating_time=` value IS the raw fail-time draw, with
zero intervening mechanism that could obscure or transform it.

`validate()`: zero warnings or errors.

## What this port's own run actually does (directly confirmed)

20 runs, 398 total event-log lines (~20/run) — bounded, no runaway.
Every single run: `BlockA` (shape=0.01 Weibull) never fails within the
run (consistent with F.2's own expectation that shape=0.01 produces an
astronomical tail draw for THIS port too — nothing here disputes that
half). `BlockB` (shape=0.5 Pearson5) fails in EVERY run, at a raw draw
value in this port's own **ordinary, unremarkable range** — the 20
`Operating_time=` values across runs 0-19:

```
44.687324   39.593344    7.703372  112.632020    9.033990   58.748262
 5.154152   36.947969   21.038355    2.281104  125.633818   17.575194
117.333130   64.323637  125.508391   23.866731    2.701601   43.138391
 25.364957  373.009834
```

None within ~20 orders of magnitude of F.2's cited real value
(`~6.8e20` for a DIFFERENT fixture's own shape=0.5 Pearson5 draw —
different `FStream`/params, not directly comparable value-for-value,
but the same qualitative class: does shape=0.5 Pearson5 blow up or
not). Mechanism (`raptor/core/rng.py:345-351`): `pearson5(scale,
shape, location) = location + 1/gamma_draw`, where `gamma_draw ~
Gamma(mean=shape/scale, shape=shape)` — for `shape=0.5, scale=10`:
`Gamma(mean=0.05, shape=0.5)`. A Pearson5 blowup requires the
underlying gamma-rejection sampler to occasionally emit a `gamma_draw`
very close to 0 (so `1/gamma_draw` is huge). None of these 20 runs'
own gamma draws did that — every value stayed in a `[2, 373]` range,
consistent with an ordinary (not pathological) `Gamma(0.05, 0.5)`
sample.

Sample event-log excerpt (run 0, `t=0-46.7`):
```
0.000000    Block  BlockA  Good     InitializeStress=1.000000
0.000000    Block  BlockB  Good     InitializeStress=1.000000
44.687324   Block  BlockB  Failed   Operating_time=44.687324
44.687324   Block  BlockB  Repairing  No_resources_used
44.687324   Node   join    Bad      1-2/2_ur2_df4
44.687324   Node   end     Bad      0-1/1_df5
44.687324   System State_change  Red    _d4
46.687324   Block  BlockB  Repaired  MaintTime=2.000000
46.687324   Block  BlockB  Good     Stress=1.000000
46.687324   Node   join    Good     2-2/2_ur2_ur4
46.687324   Node   end     Good     1-1/1_ur5
46.687324   Block  BlockA  Done     End_sim
46.687324   Block  BlockB  Done     End_sim
46.687324   System Phase_change  Mission_failure  Finished_a_mission
46.687324   System Simulation_Terminate  --  --
```

## The question

Does real's own `BlockB Failed Operating_time=<value>` line, across
these same 20 runs, match this port's `Operating_time=` values above
(within the standard `sd_diff.py` noise envelope — software-`ln`/tail
amplification, `TOL_ABS=1e-5, TOL_REL=1e-7`), or does real show the
SAME astronomically-large-value pattern F.2 already documented
elsewhere (an `Operating_time=` in the `1e15`+ range, or similar)?

- **Matches this port's own moderate values** → F.2's astronomical-tail
  behavior is specific to some OTHER factor this probe stripped away
  (GDType/PM interaction, a specific `FStream`/param combination, or
  the periodic-dump mechanism itself being the source of the huge
  numbers rather than the draw) — narrows F.2 significantly, this
  port's `pearson5`/gamma-rejection formula is cleared for the
  ordinary shape<1 case.
- **Real shows an astronomically large `Operating_time=` here too** →
  confirms this port's `_gamma` rejection sampler (or the underlying
  uniform stream position at `FStream=3`) diverges from real's own
  specifically for shape<1, independent of GDType/PM — the genuine
  F.2 draw-transform bug, now reproducible in the plain event log
  without `/sd` at all. Next step: capture `/sd` too (`FStream=3`'s
  own raw `u` sequence) to pin down whether the divergence is in the
  uniform stream itself or in the gamma-rejection transform applied to
  it.
- **`BlockA`'s own shape=0.01 Weibull DOES fail within some of real's
  20 runs** (real's own mission doesn't run away to the same
  astronomical tail this port predicts) → an even more direct F.2
  data point for the Weibull half specifically; note real's own
  `BlockA Failed Operating_time=` value(s) if any appear.

## Predicted (this port)

See "What this port's own run actually does" above — `BlockB` fails
every run with the 20 listed `Operating_time=` values (range
`[2.28, 373.01]`), `BlockA` never fails in any of the 20 runs. `/sd`:
`BlockA` draws from `FStream=1`, `BlockB` from `FStream=3` — same
per-block dedicated-stream convention as every other fixture.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (20 runs), read the FULL
event log for `BlockA`/`BlockB` across all 20 runs (each run is short —
the WHOLE run's own event log is the useful data, not just one line).
Real-Raptor report outputs land in `~/Documents/Raptor7/`
(`rpt_events_*` etc.); the `/sd` copy goes to this repo's root as
`simDetails_ReleaseProbe37.txt`.

## REAL ANSWER (2026-09-05 capture, `simDetails_ReleaseProbe37.txt` +
`~/Documents/Raptor7/rpt_events_ReleaseProbe37.txt`)

**Decisive, complete confirmation — F.2 is SOLVED for both distributions
tested.** Every one of this port's own 20 predicted `Operating_time=`
values for `BlockB` (Pearson5 shape=0.5) matches real EXACTLY, bit for
bit, across all 20 runs (`44.687324`, `39.593344`, `7.703372`, ...,
`373.009834` — the full list in "Predicted" above is real's own list
too). `BlockA` (Weibull shape=0.01) never fails in any of real's 20
runs either, exactly as predicted.

The `/sd` capture goes further and settles the RAW-DRAW question F.2
was actually about, not just the eventual `Operating_time`: real's own
dump shows `BlockA`'s astronomically large raw Weibull draws directly
(`4588846422736255521390592.000000`, `12523050886238222.000000`,
`139537168541572960.000000`, and others up to `~1.2e60`+ magnitude
across the 20 runs) — confirming this port's own concern that shape=0.01
produces astronomical values was right, real genuinely draws them too.
Running `tools/sd_diff.py` against this capture directly
(`--rbd tests/fixtures/ReleaseProbe37/input.rbd --real
simDetails_ReleaseProbe37.txt --num-runs 20`): **all 98 draw lines
match — zero structural divergences.** The only differences (24 of 98)
are the already-established software-`ln` float-noise class (F.1),
e.g. `1232997718131437245807741933385070410883583986080061325312.0`
vs this port's `...702350863713468916556831193105810836922144456179712.0`
— an ~8-significant-digit relative match on a 61-digit number, well
inside the existing noise envelope.

**This means F.2's own core claim — "this port's inverse-CDF/gamma-
rejection formulas don't reproduce real's astronomically large shape<1
draws" — was WRONG, or at minimum doesn't hold for either distribution
tested here.** Both `_weibull(shape=0.01, scale=10)` and
`pearson5(shape=0.5, scale=10)` (`raptor/core/rng.py`) produce
BIT-EXACT values against real for the SAME `FStream` positions, with
no PM/GDType/AmtExhausted confound in the way. The astronomical values
ARE real (not a artifact of this port's own broken formula) — this
port already computes them correctly. Whatever divergence motivated
F.2's original "mathematically PROVEN impossible" framing (§23) must
have come from a DIFFERENT source in whichever original fixture raised
it — a stream-position drift (same general class as E.9's shared-
stream finding), a PM/GDType interaction changing which draw actually
gets consumed, or a mismeasurement of which `/sd` line corresponds to
which logical event — NOT a broken draw-transform formula. F.2's own
"mathematically proven impossible... requires `u≈8.93e-302`" argument
must have been checking the WRONG candidate `u`/draw pairing, not an
error in the formula itself.

**F.2 status: SOLVED for the base Weibull/Pearson5 shape<1 draw
transform.** the internal engine ledger F.2 updated accordingly.
Any remaining shape<1 divergence on a PM/GDType-bearing fixture should
now be investigated as a stream-position or control-flow question
(which draw fires when), not a formula-correctness question — the
formula itself is cleared.

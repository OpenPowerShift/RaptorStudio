# Fixture: UniformProbe.rbd — RNG recovery fixture (CONFIRMED base generator)

Captured 2026-08-14 from Raptor 7 Student Edition under Wine. This is a
hand-built, minimal model: one block, Fail Distribution = Uniform Real(0,1)
on **independent stream 1**, Repair Distribution = Uniform Real(0,1) on
**independent stream 2** — chosen specifically so every drawn value is the
*raw*, untransformed output of the base generator (no Bernoulli/Lognormal/etc.
transform hiding it), and so neither stream is contaminated by other system
bookkeeping draws (using a *system* stream, as the default config does, mixes
in unrelated draws from validation/RunEFPA/etc.).

## Result: base RNG algorithm recovered

```
x_{n+1} = (630360016 * x_n) mod 2147483647      # 2147483647 = 2^31 - 1
output  = x_{n+1} / 2147483647
```

This is a Lehmer/multiplicative-congruential generator. The multiplier
630360016 is a documented "good" full-period multiplier for modulus 2^31-1
from the classic simulation literature (Fishman & Moore 1986 / Law & Kelton
"Simulation Modeling and Analysis") — consistent with CACI's SIMSCRIPT/Modsim
II lineage using a textbook Lehmer generator rather than something bespoke.

Seeding: `SetSeed(s)` sets internal state `x_0 = s`; the first *output* is
one recurrence step applied to the seed (`x_1`), not the seed itself.

Verified against `run verify_rng.py` in this directory: matches the legacy
engine's output **exactly** (to the printed 6 decimal places, modulo the
occasional single-ULP difference from the legacy engine almost certainly
computing in 32-bit float vs our 64-bit float) for **400 consecutive draws**
on stream 1 and **96 consecutive draws** on stream 2, across two
independently-seeded streams. That is not a coincidence — treat the base
generator as **confirmed**.

## RESOLVED 2026-08-15: the "draw-count desync" was a false alarm

The section below was written when the leading theory was that the legacy
engine burns hidden extra draws somewhere. **That theory was wrong.**
`raptor/core/engine/block_lifecycle.py` replays this exact scenario
end-to-end (RNG + `ChangeBlockState` + the fail/repair cycle sequencing)
and reproduces **the entire first run — ~97 cycles, 387 log lines — with
zero structural mismatches** against the real captured output (only
expected float32-vs-float64 last-digit noise, ~4% of lines; see
`tests/unit/test_block_lifecycle.py`).

The real explanation: `UniformProbe.rbd` runs for a **fixed 100.0 time-unit
length per run** (`TimeSim` termination, see its `SYSTEM_DATA` row), and at
`SimTime=100.0` the engine forces the block to `Done`/`"End_sim"` and starts
the next run **from a freshly-reset seed state** (each run does not
continue the previous run's stream position indefinitely — or at minimum,
something about the run boundary changes the effective draw sequence in a
way this project hasn't characterized yet). The original `stream1_fail_
draws.txt`/`stream2_repair_draws.txt` extraction (below) flattened
`Operating_time=`/`MaintTime=` values **across all 500 runs** without
accounting for that boundary, so comparing them against one continuous
LCG stream was comparing the wrong thing past run 1 — not a sign of a
missing draw. **Always compare within a single run, never across the flat
multi-run extraction**, unless/until run-boundary reset behavior is
itself characterized (it isn't yet — see `block_lifecycle.py`'s TODO on
run-length termination).

Original (now-superseded) write-up, kept for the record:

<details>
<summary>Original "Known open issue" section</summary>

Both streams eventually disagree with the legacy output by a large amount,
suddenly, at a fixed point (draw #402 for stream 1, draw #97 for stream 2)
— not a gradual drift, a hard offset, consistent with **the legacy engine
consuming one or more extra draws from these streams that our extraction
didn't count**. Leading suspects, to check when porting
Raptor 7's block-state algorithms and the spare/resource logic in Phase 3/4:
spare-availability checks, something in `DetermineNewState`/`GetStartCond`,
run-boundary effects. (The last one was it — see above.)

</details>

## Files

- `input.rbd` — the model itself (one block, independent streams 1 & 2, both
  Uniform Real(0,1); NumRuns=500).
- `legacy/rpt_uniform_event_log_2.txt.gz` — full detailed event log from the
  legacy run (gzipped, ~34MB uncompressed). The `_2` suffix is because the
  app auto-versions output filenames rather than overwriting existing ones —
  useful to know for the file-I/O port.
- `legacy/rpt_uniform_end_of_run_2.txt`, `legacy/rpt_uniform_final_results_2.txt`
  — the other two output files from the same run.
- `stream1_fail_draws.txt` — every `Operating_time=` value extracted from the
  event log, in order (raw stream-1 draws).
- `stream2_repair_draws.txt` — every `MaintTime=` value extracted, in order
  (raw stream-2 draws).
- `verify_rng.py` — reimplements the recovered generator and checks it
  against both extracted draw sequences; run it directly (`python3
  verify_rng.py`), no dependencies beyond the stdlib.

## How this was derived (for anyone reproducing/extending this)

1. Built the model in the GUI: File → New, one block, Distribution tab →
   Fail Distribution = "Uniform Real", params (0, 1); Stream section →
   "Independent Stream", fail stream = 1; Repair Distribution likewise
   Uniform Real(0,1) on independent stream 2. NumRuns = 500 via Sim Options.
   Ran with the "Event Log" output file checked (Sim Options → File tab).
2. Extracted `Operating_time=`/`MaintTime=` values from the resulting event
   log with `grep -o`.
3. Looked up the exact seed each stream uses: block's `FStream`/`RStream`
   columns in the saved `.rbd` (values 1 and 2 respectively — values <70 mean
   "use an independent stream seeded via `FetchRaptorSeed(streamNum)`", see
   `GetStream` in  and
   `FetchRaptorSeed` in).
4. Brute-force tested a shortlist of well-known LCG multipliers (Park-Miller,
   L'Ecuyer variants, RANDU, and the classic Fishman-Moore full-period-mod-
   (2^31-1) list) against the known seed and first 10 observed draws of each
   stream, across a few modulus/normalization conventions. `630360016` with
   `m=2^31-1`, `output=x/m` matched both streams' first 10 values exactly.
5. Extended the check across the full ~50k-draw fixture to find how far the
   match holds (see "Known open issue" above).

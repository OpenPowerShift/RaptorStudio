# F2_Pearson5Probe — captured 2026-09-17, byte-exact

## Result (2026-09-17) — the parent fixture's divergence is a FIXTURE bug

The first capture of this probe (BlockA with `#FParam=2`, as built) read a
constant, seed-independent BlockA value in every run whose IEEE bytes are
ASCII text. Cause: `DrawNumber` adds `dataArray[3]` as a location for
Gamma/Pearson5/Weibull, but the loader sizes the array to `#FParam`, so
with 2 params real reads heap garbage one slot past the end. Every
real-authored model stores 3. Rebuilt with `#FParam=3` (one-character
change, through `rbd_builder`) and re-captured: **Pearson5(0.5, 10) is byte-exact on
every draw, the event log, End_Of_Run and Final_Results.** So the formula
was right and the parent fixture's draws are garbage + draw, unportable by
construction. `rbd_builder.validate()` now rejects `#FParam<3` for these
distributions. The `#FParam=2` capture is kept in `evidence_np2/`.
The text below is the pre-capture design, kept as the record.

Built 2026-09-16 via `tools/rbd_builder.py` from `FailMeanUndef`.
**No real capture yet.** Sibling of `F2_WeibullProbe` (same surgery,
Weibull instead of Pearson5) — capture both together.

## What it is for

`[sd_diff.FailMeanUndef]` is `first_divergence = 0` with real at 4001
BlockA draws over 20 runs and this port at 5922. Its own entry says the
question is unresolved: *"whether this port's OWN very first BlockA draw
reads a genuinely different uniform value than real at the same logical
stream position, or real's own kickoff sequencing differs from what a
literal /sd reading suggests"*, and asks for a minimal isolating probe.

`FailMeanUndef`'s BlockA carries THREE confounds that make its raw draws
impossible to read off the capture:

| field | FailMeanUndef | here |
|---|---|---|
| `GDType` | 1 (linear growth/decay) | **0** |
| `AmtExhausted` | 100000.0 | **0.0** |
| `usesPM` | 1 | **0** |

`AmtExhausted=100000` is the worst of them: with `simStart=3` it drives
`GetStartCond`'s retry loop, which redraws (up to 100 times) until a draw
EXCEEDS 100000 — so the number of draws is itself a function of the draw
values, and a single early difference cascades. With it at 0 there is no
retry loop, the first draw is the whole story, and `/sd` shows the raw
Pearson5(0.5, 10) values directly.

BlockB is left exactly as it was (Exponential(60), its own streams) and
its PM stripped: it already matches real almost exactly in the parent
fixture (4758 vs 4759 draws), so it stays as a CONTROL. Keeping it also
means the topology is untouched — no node/link surgery, which is the
thing that has historically crashed real Raptor.

## What the capture answers

Compare BlockA's draw SEQUENCE against real's, position by position:

- **values match** — the Pearson5 formula is bit-exact and the parent
  fixture's divergence is entirely the retry loop / GDType machinery;
- **values differ** — the draw itself differs, and `rng.py`'s Pearson5
  inverse-CDF is the place to look.

This port's prediction: BlockA's first five draws are
`7.326247, 8.503007, 67.744492, 17.903341, 34.563246` — ordinary
magnitudes, NOT the ~6.76e20 cluster real shows in the parent fixture.
That contrast is precisely what needs explaining.

## To capture

Load `input.rbd`, Launch > Simulation..., 20 runs, Files & Reports >
tick "Detailed event log" (and "End of run" if convenient), OK. The
`/sd` `simDetails.TXT` is the important artefact — save it as
`legacy/simDetails_F2_Pearson5Probe.txt`.

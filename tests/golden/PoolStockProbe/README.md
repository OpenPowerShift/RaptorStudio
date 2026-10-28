# PoolStockProbe — captured 2026-09-17, byte-exact

Built 2026-09-16 via `tools/rbd_builder.py` (cloned from `PreventMxDemo`,
which has a real capture and whose event log this port already matches
byte-for-byte). **No real capture yet** — until it has one it verifies
nothing, it only makes the question runnable.

## Result (2026-09-17)

Real reports `Electronics` **MaxStock 4 in all 10 runs**. Neither candidate
rule below was right on its own: the rule is **held duration** — a level
counts toward Min/Max only if held for positive time (a timed monitor), so
the initial level counts unless it is replaced at t=0.000000, which is exactly
what Phasing3 runs 2/4/7 do. Implemented in `pool.py`; this probe is now
byte-exact on event log, End_Of_Run, Final_Results and simDetails, and is
registered in `test_orchestrator.py` and `test_sd_diff_sweep.py`. The text
below is the pre-capture design, kept as the record of the question.

## The question it settles

`ResourceQueue.begin_run` seeds a pool's `min_stock`/`max_stock` with the
INITIAL level. Source assigns `PoolResAvailable[i]` and THEN resets its
monitor, which reads as "the initial level is not
a sample" — and real has rows that agree AND rows that contradict it:

| fixture | Init | NewSpares | real Max | implies |
|---|---|---|---|---|
| PreventMxDemo Electronics run 1 | 2 | 1 | **2** | init IS a sample (stock fell from 2 and never returned; the one arrival logged `stock=0`) |
| Phasing3 Electronics run 1 | 4 | 12 | **4** | init IS a sample |
| Phasing3 Electronics run 2 | 4 | 18 | **3** | init is NOT a sample |

The last two are the SAME pool in the SAME fixture. Three attempts at a
reset-sample rule have been measured; see `pool.py`'s `begin_run` for the
full write-up and for what each attempt cost (the blanket change takes
report-fidelity from 0 to 38 failures).

## Why this probe is decisive

`Electronics` here has **RSO, ESO and SLO all OFF**, so `NewSpares=0` and
the stock can only ever FALL: 4 -> 3 -> 2 -> 1 -> 0, never back up. So
real's reported `MaxStock` can only be:

- **4** — the initial level IS a sample, and this port's current seeding
  is right (and Phasing3's run-2 `3` needs a different explanation);
- **3** — it is NOT a sample, and the seeding is wrong (and the 38
  failures it currently masks are real bugs to be chased).

Run 4 is a free CONTROL: its stock never changes at all, so both rules
predict 4 there and a `4` is only informative on the other runs.

This port's prediction (all 10 runs): `MaxStock 4`.

## To capture

Load `input.rbd`, Launch > Simulation... > Files & Reports > tick
"End of run" (the stock table lives there; "Detailed event log" is also
worth having for its `stock=N` trace), OK, and save both into `legacy/`.
Then compare the `Electronics` row of `SparePool_Sparing_Information`.

NOTE: loading this file also closed `rbd_builder`'s own documented
"`render_rbd`'s reformatting is untested against the real loader" gap —
real Raptor 7 opened the re-rendered file without complaint (2026-09-16).

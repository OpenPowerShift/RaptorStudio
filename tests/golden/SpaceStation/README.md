# Fixture: SpaceStation — the real bundled Student Edition `SpaceStation.rbd`

`input.rbd` copied verbatim from the Student Edition's own
`Examples/SpaceStation.rbd`. `legacy/rpt_events_spacestation.txt` — real
capture, 10 runs. `legacy/simDetails.TXT` (new 2026-08-18) — real
per-draw RNG ground truth, captured via the `/sd` command-line flag (see
the memory system's `reference-simdetails-flag` note): every distribution
draw's exact value, tagged by block name, in call order — no inference
from event-log timestamps required.

## Status: SOLVED 2026-08-18 — all 10 runs bit-exact

Pooled-sparing consumption (`SpType=Pooled`, RSO/ESO replenishment) is
now ported and this file runs end-to-end without error. Getting from
0/10 (crash) to 10/10 bit-exact took SIX real bugs found and fixed the
same session, each one uncovering the next once the previous stopped
masking it:

1. **`rng._gamma`, whole-number shape (`d==0.0`)**: the real routine
   consumes ONE EXTRA "burn" draw before the integer-part product loop,
   then returns via the `(mean/k)*z` shortcut UNCONDITIONALLY (not gated
   by `k<5` as an earlier, unverified reading of the SIMSCRIPT manual's
   pseudocode had assumed). Confirmed via `SolarP_500W`'s real
   `simDetails.TXT` value (`864.349614`) matching to 1e-7 — see `rng.py`'s
   `_gamma` docstring.
2. **`block_run_process`'s `original_draw`** was reassigned on every loop
   iteration, so `Operating_time=` after a `Dependence`-triggered pause/
   resume showed the REDUCED remaining time instead of the block's true
   original draw. Port of `OperatingTime`'s real running-accumulator
   semantics.
3. **Spare-pool consumption was ordered before Pre-LDT**, but the real
   source checks Pre-LDT FIRST (`Repair`'s own `CASE "Off"` branch) —
   only `CheckForSpare`'s own logging happens earlier, in
   `DetermineNewState`. Split the `SparePool` branch into its two real
   call sites.
4. **`Scheduler.interrupt` resumed synchronously, nested inside the
   waking process's own turn** — wrong for a `TELL`-style resumption; the
   waking process's OWN remaining code (e.g. its own next fail-time draw)
   must run to completion FIRST. Changed to a QUEUED zero-delay calendar
   entry instead (see that method's own docstring) — this ALSO required
   adding `Scheduler.tell` (genuinely synchronous spawn, for `TELL METHOD
   OrderASpare`'s own startup line, which DOES need to run immediately)
   and suppressing `_Cascade.apply`'s own system-status check whenever it
   just queued a `wake_ids` resumption (the woken block's own eventual
   transition does that check instead).
5. **`repair_completion_status` only handled block-to-block dependencies**
   — extended to the node/`sysDepend` branch,
   including `GetDownNum`'s "is this block's own path the sole cause"
   special case (`nodes.py`'s `path_fail_value`/`path_pm_value`) and its
   nested Running-then-possibly-Idle-again re-check (needs the FIRST
   transition's own cascade to actually run before checking `end_node
   .status` again — an earlier version of this fix read the STALE
   pre-cascade status and got it backwards for every case).
6. **The final timing collision**: a block's repair-completion can be
   BOTH `needs_recheck` (self-correcting a `sysDepend` node, #5 above)
   AND simultaneously free a resource for a DIFFERENT waiting block
   (`pending_wake_ids`) in the SAME transition. `block_run_process`'s
   manual `check_system_status()` call (for the "recheck says stay
   Running, no follow-up Idle needed" case) fired UNCONDITIONALLY,
   ignoring whether a resource-pool wake was ALSO pending from the same
   transition — one synchronous call too early, before the OTHER block's
   QUEUED resumption (see #4) got a chance to run and log its own lines
   first. Fixed by gating that manual check (and the follow-up Idle
   transition's own `top_level`) on `not pending_wake_ids` too, mirroring
   `_Cascade.apply`'s own `top_level and not wake_ids` suppression.
   Diagnosed by tracing the EXACT call sequence around the one remaining
   divergence (`APPLY`/`INTERRUPT`/`CHECK_SYSTEM_STATUS` instrumentation)
   rather than more `simDetails.TXT` archaeology — the values were
   already all correct by this point, only the ORDER was wrong, and the
   call trace pinpointed the exact premature check immediately.

Verification: all 10 runs, all lines compared, 0 structural mismatches
(849 exact matches + 452 fp32-noise-only, using a magnitude-scaled
tolerance — see `test_orchestrator.py`'s `_classify` docstring for why
a flat 1.5e-6 absolute tolerance, fine for every smaller-magnitude
fixture, needed widening for this one's larger Gamma/Pearson5 values).

## Files

- `input.rbd` — copied verbatim from the Student Edition's own
  `Examples/SpaceStation.rbd`.
- `legacy/rpt_events_spacestation.txt` — real capture, 10 runs.
- `legacy/simDetails.TXT` — real per-draw RNG ground truth (`/sd` flag),
  519 lines, captured same session as the fixes above.

## What this is for

The fixture that drove the Pooled-sparing port and SIX real bug fixes
this session (RNG, logging, ordering, scheduler semantics, dependency
propagation, and a final cross-mechanism timing collision). Now a
standing passing fixture in `test_orchestrator.py`, same status as every
other verified fixture.

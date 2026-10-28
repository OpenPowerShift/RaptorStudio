# ReleaseProbe17 — the complementary swap: is BEING raced sufficient for absorption on its own, or does it depend on the OTHER subscriber too?

`ReleaseProbe16`'s real capture confirmed the per-block `InterruptReason`
field-race theory (§48.1, the internal engine ledger §G.3): with
`BlockS`'s dependency on `BlockD` removed (so only `BlockN` stays raced by
the dep-idle cascade), BOTH subscribers dispatch cleanly at `T1`'s tied
fire — ruling out a group-level "trigger release itself starves under any
tie" alternative. But `ReleaseProbe10`/`15` established something that
looks, on the surface, in tension with a *purely* per-block theory: when
**both** `BlockS` and `BlockN` are raced by the same cascade at the same
instant, `BlockS` (row-first) is absorbed while `BlockN` (row-second) is
NOT — even though both are independently touched by the exact same
cascade. `ReleaseProbe16` only tested removing `BlockS`'s race (leaving
`BlockN` alone raced) and got "neither absorbed." Nobody has yet tested
the OTHER swap: leave `BlockS` (row-first) raced, and remove `BlockN`'s
(row-second) race instead — making `BlockS` the ONLY one raced.

This probe is `ReleaseProbe10`, byte-identical except **`BlockN`'s
dependency is removed entirely**: `DepNum: -1 → 0`, `defDepStateIdle: 1 →
0` — the exact mirror of `ReleaseProbe16`'s edit, applied to the other
block. `BlockN` still subscribes to `T1` (`pmTriggered=1, pmTrig=T1`,
unchanged) but is now completely untouched by `BlockD`'s failure or its
cascade. `BlockS` is unchanged: still node-dependent on the same node,
still gets dep-Idled by `BlockD`'s t=5.0 failure, tied with `T1`'s own
t=5.0 fire, exactly as in `ReleaseProbe10`/`15`.

Built via a direct text-level edit on a byte-for-byte copy of
`ReleaseProbe10/input.rbd` (changing only `BlockN`'s row, the same two
fields `ReleaseProbe16` changed on `BlockS`'s row) — **not** through
`tools/rbd_builder.py`'s `save()`, which was tried first but discarded:
`render_rbd` re-renders the WHOLE file, and on this file it collapsed the
original padded/aligned section banners and header rows into compact
single-space formatting (e.g. `/___________________________________
SYSTEM_DATA_________...` → `/___SYSTEM_DATA___`). The individual block
DATA rows were already single-space-tokenized in the source file so
those were unaffected, but no probe in this family has ever sent a
whole-file `render_rbd` round-trip to real Raptor before, so there's no
precedent that its reformatted banners/headers are tolerated by the real
loader — an unnecessary, untested confound to introduce on top of an
otherwise clean isolated experiment. `rbd_builder.py`'s `load()`/
`get_row()`/`set_fields()`/`validate()` API was still useful for staging
and validating the intended edit before applying it as a direct text
patch instead of via `save()`; validated 0 ERRORs (`python3 tools/
rbd_builder.py validate tests/fixtures/ReleaseProbe17/input.rbd`).
**Improvement worth making to `rbd_builder.py`**: `save()`/`render_rbd`
should either preserve the source file's original section-banner/header
formatting, or this should be documented as a known limitation so nobody
sends an unverified whole-file re-render to real Raptor by default.

## The question

At `T1`'s tied fire (t=5.0), with `BlockS` as the ONLY raced subscriber
(row-first, and now the only one dep-idled by `BlockD`'s cascade), does
it still get absorbed?

- **`BlockS` is absorbed anyway** (no `PM_is_due`, no draw) → confirms
  being independently raced by the cascade is **SUFFICIENT** for a
  block's own absorption, regardless of what happens to any other
  subscriber. The row-first/row-second asymmetry seen in
  `ReleaseProbe10`/`15` was coincidental to both being raced there, not
  because `BlockN`'s presence or behavior matters at all — the mechanism
  is purely per-block. This would fully close the "is it per-block or
  relational-between-subscribers" question in favor of per-block; the
  ONLY remaining open piece becomes the exact interleaving TIMING within
  a single block's own `ControlPMtriggers` Fire-wait vs. dependency-cascade
  race (§48.2's `WAIT FOR`/`Interrupt` MODSIM micro-semantics) — not which
  subscriber wins relative to another.
- **`BlockS` dispatches cleanly** (gets its own `PM_is_due`, draws its own
  PM value) when raced ALONE → surprising and would REOPEN the question:
  means being raced is NECESSARY but not SUFFICIENT — something about
  MULTIPLE subscribers being simultaneously raced (or specifically
  row-first losing to a LATER-position raced subscriber, i.e. a genuinely
  relational/comparative effect between the two `ControlPMtriggers`
  instances) is required for absorption to manifest at all. This would
  mean the mechanism isn't purely local to one block's own field, contrary
  to §48.1's literal theory as currently understood, and would need
  rethinking before any fix is attempted.
- **Neither prior pattern (e.g. some other change in behavior entirely)**
  → check the full event log around t=5.0 for what's different, not just
  the draw count — same caveat as `ReleaseProbe16`'s own third bullet.

## Our port's own prediction (unaffected by either hypothesis — it doesn't implement absorption at all yet)

Both dispatch, as always — and notably, removing `BlockN`'s dependency
this time does NOT flip our port's own PM-begin ordering back the way it
was in `ReleaseProbe10`/`15`: `BlockS` still begins PM and draws FIRST
(matching `ReleaseProbe10`'s own order), THEN `BlockN` begins PM and draws
SECOND — i.e. this probe's own port-side event order matches
`ReleaseProbe10`'s original order exactly (unlike `ReleaseProbe16`, whose
removal of `BlockS`'s dependency flipped the order to N-first). The actual
drawn VALUES are identical to `ReleaseProbe16`'s own real capture, because
RNG stream 203's state up to t=5.0 is unaffected by either dependency
edit: `BlockS` draws `0.602041`, `BlockN` draws `2.944303` — same shared-
stream position either way.

Own run 1, `/sd` draws at t=5.0: `BlockS 0.602041`, `BlockN 2.944303`.
Event shape: `T1 Fired → D Failed → S PM_is_due → N PM_is_due → j1/end Bad
→ S Idle Dependence → S beginning_PM → N beginning_PM → (5.602041) S
PM_complete MaintTime=0.602041 → (7.944303) N PM_complete
MaintTime=2.944303`.

## Readout (t=5.0 real event log + /sd, shared stream 203)

Look for `PM_is_due`/`beginning_PM` lines for `BlockS` specifically at
t=5.0, and count the `/sd` draws at that instant (1 draw = only `N`
dispatched = `S` absorbed; 2 draws = both dispatched). `BlockN`'s own
behavior isn't the question here (expected to dispatch regardless, since
it's no longer raced at all — same logic as `ReleaseProbe16`'s `BlockS`)
— the informative signal is entirely whether `BlockS` shows a
`PM_is_due`/draw or not.

## REAL ANSWER (2026-09-03 capture, `simDetails_ReleaseProbe17.txt`)

**`BlockS` (raced alone) is absorbed again** — no draw at t=5.0 in any of
the 3 runs; `BlockN` (not raced) dispatches, drawing `1.204082`/
`2.509604`/`0.889234` — the SAME values as `ReleaseProbe10`'s own
original N-dispatch. Literally, this matches the README's first
predicted outcome ("`BlockS` absorbed anyway").

**But the full picture across all four probes in this pair is sharper
than either predicted outcome alone captures.** Laying out every data
point:

| Probe | `BlockS` (row-first) raced? | `BlockN` (row-second) raced? | `BlockS` outcome | `BlockN` outcome |
|---|---|---|---|---|
| 10 / 15 | yes | yes | **absorbed** | dispatches |
| 16 | no | yes | dispatches | dispatches |
| 17 | yes | no | **absorbed** | dispatches |

`BlockS`'s own outcome tracks its OWN race status perfectly (raced →
absorbed, in both 10/15 and 17; not raced → dispatches, in 16) — the
per-block race requirement is fully confirmed, 3/3. But `BlockN` NEVER
gets absorbed in any variant, including `10`/`15` where it IS raced
(`BlockN`'s own dependency on `BlockD` is unchanged there) exactly the
same way `BlockS` is. If absorption were purely "any block that's raced
this instant gets absorbed, full stop, no positional component," `BlockN`
being raced in `10`/`15` should have made it vulnerable too, symmetrically
with `BlockS` — it never is. **So the mechanism is NOT purely per-block
in the fully symmetric sense either — there's still a genuine row-order
component, but it's narrower than "row/registration order decides which
of several raced subscribers loses": row-second appears structurally
IMMUNE to absorption regardless of its own race status, while row-first
is vulnerable exactly when (and only when) it is itself raced.**

**Consequence**: G.3's behavioral rule is now fully characterized by data
(not just theorized): *the row-first trigger subscriber's PM wake is
absorbed if and only if that same block is independently dep-idled by a
same-instant dependency cascade; the row-second (or later) subscriber's
wake is never absorbed, regardless of its own race status.* This is
precise enough to be genuinely implementable — the remaining unknown is
now purely EXPLANATORY (why row-first specifically is the vulnerable
slot — presumably `ControlPMtriggers`'s per-subscriber processes and the
dependency cascade's own visit order are both row-ordered, so only the
first subscriber's dispatch window happens to overlap the cascade's
pass), not behavioral. `§48.2`'s `WAIT FOR`/`Interrupt` MODSIM
micro-semantics questions are the likely SOURCE of the "why," but a fix
may not need to wait on them if the behavioral rule above is implemented
directly and verified against the full regression sweep.

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs, matching
`ReleaseProbe10`/`15`/`16`'s own capture count), read the event log and
`/sd` output around SimTime=5.0.

# ReleaseProbe16 — decoupling the dep-idle RACE from row position (does absorption need a per-block field collision, or is it group-level?)

`ReleaseProbe10`'s real capture (§45-46) found: when `T1`'s fire ties with
`BlockD`'s own second fail, the row-FIRST PM subscriber (`BlockS`) has its
wake silently ABSORBED while the row-SECOND subscriber (`BlockN`)
dispatches normally. `ReleaseProbe15` (§52) proved this is about
row/registration order, not block ID. The decoded source mechanism
(§48.1-48.2, the internal engine ledger §G.3) theorizes a **per-block
shared-field race**: `InterruptReason` is a single field on EACH block,
written both by `ControlPMtriggers`'s own dispatch (`"Triggered_PM_Due"`)
and by the dependency cascade's `SetInterruptReason("Dependence_Idle")`
when that SAME block gets dep-Idled by `BlockD`'s failure at the same
instant — whichever write lands last, for THAT block, wins its own
resume. But every probe so far (`10`, `11`, `14`, `15`) has BOTH `BlockS`
and `BlockN` dependent on `BlockD` (`DepNum=-1`, node-dep, `DepIdle=100`)
— so BOTH subscribers are ALWAYS independently raced by the cascade at
the same instant. No probe has yet asked what happens when only ONE
subscriber has a dependency-driven race at all.

This probe is `ReleaseProbe10`, byte-identical except **`BlockS`'s
dependency is removed entirely**: `DepNum: -1 → 0`, `defDepStateIdle: 1 →
0` (matching `BlockD`'s own already-proven-safe non-dependent
configuration in this very file — no structural risk, single-field
edit). `BlockS` still subscribes to `T1` (`pmTriggered=1, pmTrig=T1`,
unchanged) but is now completely untouched by `BlockD`'s failure or its
dependency cascade — it just sits in its own ordinary (very long)
fail-wait the whole time, like an isolated, uncontested trigger
subscriber. `BlockN` is unchanged: still node-dependent on the same
node, still gets dep-Idled by `BlockD`'s t=5.0 failure, tied with `T1`'s
own t=5.0 fire, exactly as before.

## The question

At `T1`'s tied fire (t=5.0), does `BlockS` — now with NO dependency
race on its own `InterruptReason` field at all — still get absorbed, or
does it dispatch cleanly?

- **`BlockS` dispatches cleanly** (gets its own `PM_is_due`, draws its
  own PM value) → **confirms the per-block shared-field-race theory
  literally**: absorption requires THIS SPECIFIC block to be
  independently touched by the SAME-instant dep-idle cascade. Since
  nothing but `ControlPMtriggers` itself ever writes to `BlockS`'s
  `InterruptReason` this instant, there's no race to lose. This narrows
  the eventual fix to "detect this block's own `InterruptReason` being
  clobbered by a same-instant dep-idle for this exact block" — a
  local, per-block condition, not a group-wide trigger-release rule.
  (`BlockN`, still raced, should behave as in every prior probe:
  dispatch normally — since raced-but-row-second has always dispatched.)
- **`BlockS` is ALSO absorbed** (no `PM_is_due`, no draw), despite having
  zero connection to `BlockD` or the dependency cascade → **REFUTES the
  per-block field-race theory as the root cause.** Points instead to
  something at the TRIGGER RELEASE ITSELF — `BlockTrigger[seqNum].Release`
  or the `WAIT FOR myTrig TO Fire` group release — being partially
  starved whenever the trigger's fire ties with ANY other same-instant
  calendar entry, independent of dependency chains entirely. This would
  be the bigger, more surprising finding, and would redirect G.3's fix
  target from "the dependency cascade's field write" to whatever MODSIM
  primitive governs a trigger's own group release under a same-instant
  tie.
- **Neither absorbed (both dispatch)** → the missing dependency changes
  something else about the scenario entirely (e.g. the tie itself
  requires BOTH subscribers to be dep-idled to manifest at all) — check
  the full event log around t=5.0 for what's different, not just the
  draw count.

## Our port's own prediction (unaffected by either hypothesis above — it doesn't implement absorption at all yet)

Both dispatch, as always. But notably, removing `BlockS`'s dependency
flips OUR OWN engine's announcement/draw order relative to
`ReleaseProbe10`/`15` (own run 1): `BlockN` now begins PM and draws
FIRST (`1.204082`, same value as `ReleaseProbe10`'s own N draw — RNG
state up to t=5.0 is identical, only S's dependency config changed),
THEN `BlockS` begins PM and draws SECOND (`1.472151`). In
`ReleaseProbe10`/`15`, S (whichever had the lower row position) always
went first. This order flip is purely a symptom of how our port's own
scheduler tie-break happens to sequence an undependent vs. a
dependency-recovering same-instant PM-begin — not itself the subject of
this probe, but worth having on record as an internal inconsistency to
revisit separately if it turns out to matter.

Own run 1, `/sd` draws at t=5.0: `BlockN 1.204082`, `BlockS 1.472151`.
Event shape: `T1 Fired → D Failed → S PM_is_due → N PM_is_due → j1/end
Bad → N Idle Dependence → N beginning_PM → S beginning_PM`.

## Readout (t=5.0 real event log + /sd, shared stream 203)

Look for `PM_is_due`/`beginning_PM` lines for `BlockS` specifically at
t=5.0, and count the `/sd` draws at that instant (1 draw = only N
dispatched = S absorbed; 2 draws = both dispatched). `BlockN`'s own
behavior isn't the question here (expected to dispatch regardless,
matching every prior probe) — the informative signal is entirely
whether `BlockS` shows a `PM_is_due`/draw or not.

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs, matching
`ReleaseProbe10`/`15`'s own capture count), read the event log and `/sd`
output around SimTime=5.0.

## REAL ANSWER (2026-09-03 capture, `simDetails_ReleaseProbe16.txt`)

**`BlockS` dispatches cleanly in all 3 runs — CONFIRMS the per-block
shared-field-race theory.** At t=5.0, every run shows BOTH blocks
drawing: `BlockS 0.602041`/`BlockN 2.944303` (run 1), `BlockS
0.688190`/`BlockN 4.335882` (run 2), `BlockS 0.009488`/`BlockN 5.680894`
(run 3) — two draws per run, not one. With `BlockS`'s dependency on
`BlockD` removed, it is no longer independently touched by the same-instant
dep-idle cascade, and its `PM_is_due` wake is no longer absorbed.

**Consequence**: this rules out the "group-level trigger-release
starvation" alternative hypothesis entirely (§G.3's second possible
outcome) — a trigger's own `WAIT FOR .. TO Fire` group release is NOT
partially starved just because the fire ties with another same-instant
calendar entry in general. Absorption specifically requires THIS block to
be independently raced on its own `InterruptReason` field by a same-instant
dependency cascade, exactly as the literal §48.1 source-level theory
describes. This is a real narrowing: the fix target is a LOCAL, per-block
condition (detect a same-instant collision between `ControlPMtriggers`'s
own `"Triggered_PM_Due"` write and the dependency cascade's
`"Dependence_Idle"` write on that same block's `InterruptReason` field),
not a global trigger-group primitive.

**What's still NOT pinned**: `ReleaseProbe10`/`15` showed that when BOTH
`BlockS` and `BlockN` are raced (both dependent on `D`), `BlockS`
(row-first) is absorbed while `BlockN` (row-second) is not — this probe
didn't test "only `BlockS` raced, `BlockN` unraced" (the complementary
case), so the exact interleaving rule that produces the row-first-loses
asymmetry WHEN BOTH are raced is still open — §48.2's two MODSIM
micro-semantics questions (`WAIT FOR` release ordering; `Interrupt`-on-
pending-interrupt replace/queue/ignore behavior) remain unanswered. What's
newly closed is *whether a race is required at all* — it is.

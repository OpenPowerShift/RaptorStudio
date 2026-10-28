# ReleaseProbe14 — a THIRD same-instant competitor of a DIFFERENT kind (not a trigger tie)

Continues the `ChooseNext`/wake-order investigation (`docs/engine-gap-
analysis.md` §45-48) after `ReleaseProbe12` ruled out "just add a third
`pmTrig` subscriber" — that crashes real Raptor outright (`MODSIM
Runtime error: Access Violation`, confirmed §50; a genuine limitation
in Raptor's own trigger-release machinery, not fixable). This probe
gets a third same-instant competitor a different way: an ORDINARY,
structurally UNRELATED block failing at the same instant, not another
trigger subscriber.

`ReleaseProbe3`'s own `join`/`BlockA`/`BlockB`/`BlockC` are carried
over **byte-identical** (same node `k`/`n`, same block config) — that
exact shape is already proven to load and simulate in real Raptor with
no crash, and already gives the known [C repair-draw, B redraw] HOPPED
answer at t=25. `BlockD` is added on its own, completely separate
branch (`start→D→end`) feeding directly into the SAME `end` node
`join` already uses (`end`'s own `n` bumped `1→2` to match its now-two
real incoming links — a pattern already proven safe in real Raptor,
matching `tests/golden/MixedDepPerc`/`WakeOrderDependent`'s own real-
captured `end` nodes, both `n=2`). `D`'s own link does NOT touch
`join` at all, so it can't perturb `join`'s own `k`/`n` path counting
(confirmed the hard way this session: simply adding a third path INTO
`join` itself, even after fixing the resulting node/link-count
mismatch, silently changed `BlockB`'s own downNum outcome from parking
to self-correcting — a separate, not-yet-understood effect worth its
own investigation, not routed around here; see `docs/engine-gap-
analysis.md` §51). An EARLIER version of this file routed `D` to its
own SECOND `end`-type node instead — flagged as almost certainly
invalid (no existing fixture, probe or golden, has more than one
Type=3 node) before ever being sent to real Raptor; fixed to the
proven single-`end`/`n=2` shape before capture. `BlockD` fails at a
FIXED t=25.0 (no stream draw for the fail itself) and its repair draws
`Exponential(10)` from the SAME shared stream 203 everything else here
uses — landing a THIRD real draw at the exact same instant as B's
recovery redraw and C's repair draw, with no dependency, no PM, no
trigger involved at all.

## The question

`ReleaseProbe3`'s real capture already answered [C, B] (C's repair
draw before B's recovery redraw) for the two-competitor case. Adding
`D` (an ordinary, unrelated natural failure with nothing else touching
it) asks: does its own draw slot in AHEAD of both, BETWEEN them, or
BEHIND both? This is new ground — every probe so far in this thread
(`10`, `11`, `12`) varied the trigger-tie side; this is the first to
vary the OTHER kind of same-instant entry (an ordinary calendar
completion with no cascade, no dependency, no resource).

## Readout (t=25.0 /sd order, shared stream 203)

Our port currently predicts **`[D, C, B]`** — `BlockD`'s draw first,
`BlockC`'s repair draw second, `BlockB`'s recovery redraw third (own
run: `D 6.032477, C 1.357908, B 842292.018741`). Three possible real
answers, each meaningfully different:

- Real matches `[D, C, B]` → our port's calendar-position rule already
  handles an unrelated third entry correctly; the open `ChooseNext`
  question is narrower than this class of case.
- Real gives `[C, B, D]` or `[C, D, B]` → an ordinary, dependency-free
  failure sorts differently than our port assumes relative to a
  cascade-adjacent recovery — a genuinely new, minimal counter-example
  for whatever the eventual `ChooseNext`/group-composition rule turns
  out to be.
- Real gives `[D, ...]` but with D FIRST for a reason unrelated to
  ordering (e.g. D's own fail line appears in the real event log
  before the others at all) — check the full event log around t=25,
  not just the `/sd` draw order, in case something about D's isolation
  changes more than intended.

## REAL ANSWER (2026-09-03 capture, `simDetails_ReleaseProbe14.txt`)

**`[D, C, B]` — all 9 draws (3 runs × 3) match our port exactly**
(`25.000000 BlockD 6.032477`, `25.000000 BlockC 1.357908`,
`25.000000 BlockB 842292.019207` — B's value within the usual ~1e-6
noise floor of our own `842292.018741`). The `RunEFPA` dump also
confirms the intended topology landed correctly (`join → end` through
link 6, `D → end` through link 8, `D` never touching `join` at all).

**Consequence**: our port's calendar-position rule ALREADY handles an
ordinary, dependency-free, trigger-free third same-instant competitor
correctly — this class of case is NOT where the `ChooseNext`/wake-order
gap lives. Combined with `ReleaseProbe10`/`11` (§46-47: the absorption
anomaly is specifically TIE-related, not just "a third thing exists at
this instant"), the open mechanism is narrower than either "any third
entry" or "trigger ties generally" — it's specific to how a `pmTrig`
release's OWN wake interacts with something else at the exact same
instant, not to ordinary calendar completions sharing that instant.

## Two structural bugs found and fixed before this could be captured (same class, different nodes)

Both are the "declared node/link count doesn't match the file's real
wiring" crash class first diagnosed via `ReleaseProbe12`/`ReleaseProbe6`
(§50-51) — confirmed real Raptor tolerates a mismatched `Out` count
(several already-working probes have one) but NOT a mismatched `n`
(an incoming-path count, used to size an internal per-path array):

1. `end`'s own `n` needed bumping `1→2` once `D` became its second
   incoming link (done before the first attempt).
2. **Missed on the first attempt, causing an actual real-Raptor crash**:
   `start`'s own `Out` field still said `2` (copied from `ReleaseProbe3`,
   which only has 2 outputs) after `D` became a THIRD output of
   `start`. Bumped to `3`. Systematically re-checked EVERY node's
   declared in/out count against the file's real link count afterward
   (`ReleaseProbe10`/`11`/`12`/`4` also have `Out` mismatches on
   `start` -- confirmed harmless there via those files' own already-
   successful captures, so `Out` itself isn't what crashes; something
   about `end`'s missing `n` bump for THIS specific file was -- not
   fully isolated which of the two fixes was load-bearing before the
   corrected file was sent for capture, since both were fixed together).

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs matches the other
probes in this family; the model is otherwise fully deterministic
except the shared-stream draws under test), read the event log and
`/sd` output around SimTime=25.0.

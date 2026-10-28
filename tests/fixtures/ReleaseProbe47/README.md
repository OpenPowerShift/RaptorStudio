# ReleaseProbe47 — A.1: does a fresh `WAIT DURATION 0.0` continuation beat a pending matured timer?

## What this tests

The internal engine ledger A.1 (`ChooseNext`/`ActivityGroup`) is the
oldest genuinely-unsolved item in the corpus. 2026-09-11 produced its first
fully-decoded instance (`CS_HigherCold` run 4, `t=66.234574`) and falsified
two candidate rules. This probe settles the one that survived closest.

**The shape.** At one instant, two block processes are runnable:
- one arrived as a **matured timer** (its wait was pushed at an earlier
  instant and comes due now);
- the other arrived as a **fresh same-instant continuation** — it ran
  earlier in *this* instant, took a resource grant, and hit `Repair`'s own
  unconditional `WAIT DURATION 0.0` ("to get X to
  jive with previous Raptors"; confirmed unconditional in source, NOT inside
  the `IF NOT granted` branch, so this port's hop is faithful).

Plain FIFO-by-insertion runs the matured timer first. `CS_HigherCold`'s real
capture runs the continuation first — but there the continuation also
belonged to the LOWER block Id, so `ChooseNext`'s literal lowest-Id rule
explains it equally well. **This probe inverts that**: here the continuation
belongs to the HIGHER Id, so the two hypotheses predict opposite answers.

## Construction

Cloned from `tests/golden/ResourcePool/input.rbd` — the smallest fixture in
the corpus with a resource pool (2 real blocks, Ids 3 and 4, in parallel
under a k=1/n=2 `join`, one `Techs` pool, no cold standby, no dependencies,
no PM, no phasing). Built entirely through `tools/rbd_builder.py`;
`validate()` clean. Changed:

- `SYSTEM_DATA`: `NumRuns` 5→**1**, `TermCondition` 100→**20.0**.
- `RESOURCE_DATA` `Techs`: `NumIn` 1→**2**, so `Hi`'s grant at `t=12` is
  ALWAYS immediate and can never queue (that would change the shape).
- Block Id **3 → `Lo`** (the LOWER Id, the matured-timer arm): `FDist`
  **FIXED(2.0)**, `Pre-LDT` **FIXED(10.0)**, `Post-LDT` FIXED(0),
  `RDist` UNIFORM_REAL(0,1) on `RStream=2`, **`NumRes`=0 /
  `Resource1Name`=None**.
- Block Id **4 → `Hi`** (the HIGHER Id, the continuation arm): `FDist`
  **FIXED(1.0)**, `Pre-LDT` **FIXED(11.0)**, `Post-LDT` FIXED(0),
  `RDist` UNIFORM_REAL(0,1) on `RStream=4`, **`NumRes`=1 /
  `Resource1Name`=Techs**.

Why those numbers: `Hi` fails at `t=1` so its pre-LDT wait is pushed at
`t=1`; `Lo` fails at `t=2` so its pre-LDT wait is pushed at `t=2`. Both
mature at **`t=12`**, with `Hi`'s entry holding the EARLIER insertion
sequence despite its HIGHER Id — which is what makes the two rules
disagree. `Lo` uses no resources, so it never hops and draws its repair time
in its own turn; `Hi` does hop. Every other draw in the model is FIXED and
therefore invisible in `/sd` (`DrawNumber` skips `currentDistro=19`), so the
capture contains exactly the two draws in question.

## This port's own predicted output

Event log at the instant:
```
12.000000  Block  Hi  End_Pre-LDT  Time=11.000000
12.000000  Block  Lo  End_Pre-LDT  Time=10.000000
12.000000  Block  Lo  Repairing    No_resources_used
12.000000  Block  Hi  Repairing    1Techs
```
`/sd` — the whole run produces exactly two lines:
```
12.000000  Lo  0.184654
12.000000  Hi  0.162068
```

## The question

**At `t=12`, does real draw `Lo` first or `Hi` first?**

### What each answer means

- **`Lo` then `Hi`** (matching this port): a fresh `WAIT DURATION 0.0`
  continuation does NOT jump ahead of a pending matured timer.
  Variant 2 of A.1's 2026-09-11 experiment ("continuations before matured
  timers") is REFUTED, and `CS_HigherCold`'s ordering must come from
  something else — most likely `ChooseNext`'s literal lowest-block-Id rule
  operating on a narrower `ActivityGroup` than the whole tie group. That
  would make `ReleaseProbe48` (the companion) the decisive follow-up.
- **`Hi` then `Lo`**: the continuation DOES jump ahead, and it does so even
  when its block Id is HIGHER — so the rule is about entry provenance
  (same-instant continuation vs matured timer), not block Id. That confirms
  variant 2's direction and localises why it over-corrected on
  `CS_HigherCold` (there it must additionally stay behind a same-instant
  cascade's own synchronous draws).
- **Anything else** (e.g. real doesn't draw both at `t=12`, or an extra
  draw appears): re-derive from the actual capture — the construction
  assumption that both blocks reach their repair draw at `t=12` would be
  wrong, and the probe needs redesigning before it can answer anything.

## REAL ANSWER (captured 2026-09-11 via kwin-mcp, this same session)

**`Lo` then `Hi` — the continuation waits its turn. This port is BYTE-EXACT.**

```
12.000000  Lo  0.184654
12.000000  Hi  0.162068
```
(`tests/golden/ReleaseProbe47/legacy/simDetails_ReleaseProbe47.txt` — one
`RunEFPA` dump; `sd_diff` reports NO DIVERGENCE, 2/2 draws.)

Real runs `Lo`'s still-pending matured timer BEFORE `Hi`'s fresh
same-instant `WAIT DURATION 0.0` continuation — matching this port's plain
FIFO exactly. So:

> **A fresh zero-delay continuation does NOT jump ahead of a matured timer
> pending at the same instant.**

That **REFUTES variant 2** of A.1's 2026-09-11 experiment
("same-instant continuations before matured timers"), which had looked
promising because it nearly doubled `DistributionProbe`/`DistributionProbe2`'s
matching prefix without breaking any byte-exact fixture. It was wrong, and
this probe is why it must not be landed on that evidence.

## What the pair establishes together

| | real | this port | verdict |
| --- | --- | --- | --- |
| **P48** two matured timers | `Hi`, `Lo` | `Hi`, `Lo` | FIFO-by-insertion CORRECT; lowest-block-Id does NOT apply |
| **P47** continuation vs matured timer | `Lo`, `Hi` | `Lo`, `Hi` | continuation-first REFUTED |

**Both of A.1's surviving candidate rules are now falsified, and this
port's scheduler is confirmed correct on both of the cleanest tie shapes
that exist.** The consequence for `CS_HigherCold` `t=66.234574` (A.1's one
fully-decoded divergence) is significant and was not obvious before: since
FIFO is right and the hop is faithful, that divergence is **NOT a
tie-break-ordering bug at all**. The two engines must differ in WHICH
entries are on the calendar at that instant, or in how many hops a turn
takes — not in how a tied group is ordered. That is where the next
investigation should look, and it is a different question from the one A.1
has been framed around since 2026-08-21.

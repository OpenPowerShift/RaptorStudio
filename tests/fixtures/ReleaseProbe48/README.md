# ReleaseProbe48 — A.1: FIFO-by-insertion vs `ChooseNext`'s lowest-block-Id, isolated

## What this tests

The companion to `ReleaseProbe47`, and the more fundamental of the pair.
The internal engine ledger A.1 records that `RBDSimControlObj.ChooseNext` breaks ties among block activities by **lowest
`block.Id`**, textually confirmed, but that a naive full port fails the
sweep badly (2026-09-11 re-measured it: 27 of 51 fixtures). The standing
explanation is that `ChooseNext` scans an `ActivityGroup` that MODSIM
assembles first, and that assembly rule is not in the source we have — so
nobody has ever seen the rule tested in isolation, on a tie with no other
mechanism in it.

**The shape.** At one instant, exactly two block processes are runnable,
both as plain **matured timers**, and their insertion order DISAGREES with
their block-Id order:
- `Hi` (block Id **4**) pushed its wait at `t=1` → EARLIER seq, HIGHER Id.
- `Lo` (block Id **3**) pushed its wait at `t=2` → LATER seq, LOWER Id.

Neither uses resources, so neither takes `Repair`'s `WAIT DURATION 0.0`
hop — removing the continuation-vs-timer variable `ReleaseProbe47` isolates.
Whichever draws first names the rule directly.

## Construction

**Identical to `ReleaseProbe47` except one field pair** — verified by
diffing the two `.rbd` files: only `Hi`'s `NumRes` (1→0) and
`Resource1Name` (`Techs`→`None`) differ, on one line. Built through
`tools/rbd_builder.py` from the `ReleaseProbe47` file itself;
`validate()` clean. See `ReleaseProbe47/README.md` for the full derivation
of the timings (`Hi` fails at `t=1` with pre-LDT 11.0, `Lo` at `t=2` with
pre-LDT 10.0, both maturing at `t=12`; every other draw FIXED and therefore
absent from `/sd`).

## This port's own predicted output

Event log at the instant:
```
12.000000  Block  Hi  End_Pre-LDT  Time=11.000000
12.000000  Block  Hi  Repairing    No_resources_used
12.000000  Block  Lo  End_Pre-LDT  Time=10.000000
12.000000  Block  Lo  Repairing    No_resources_used
```
`/sd` — the whole run produces exactly two lines:
```
12.000000  Hi  0.162068
12.000000  Lo  0.184654
```

## The question

**At `t=12`, does real draw `Hi` first (insertion order) or `Lo` first
(lowest block Id)?**

### What each answer means

- **`Hi` then `Lo`** (matching this port): FIFO-by-insertion is right for
  this tie shape and `ChooseNext`'s lowest-Id rule does NOT govern two
  plain matured block timers. Combined with the source text, that means the
  `ActivityGroup` `ChooseNext` scans is narrower than "everything tied at
  this instant" — the lowest-Id rule is real but applies to a subset this
  probe doesn't construct. A.1's remaining work is then entirely about
  which entries JOIN the group, and this probe pins one shape that does not.
- **`Lo` then `Hi`**: lowest-block-Id governs even plain matured timers.
  That is a major result — it means the rule IS general and this port's
  FIFO is simply wrong here, and the 27-fixture sweep failure of the naive
  port must come from the port's own extra, source-less helper processes
  polluting the tie group rather than from the rule itself. The follow-up
  would be to re-run the lowest-Id variant with helper/untagged entries
  excluded from the Id comparison.
- **Anything else**: re-derive from the capture before theorising.

## Why the pair matters

`ReleaseProbe47` and `48` differ by one field and separate two variables
that have been confounded in every A.1 instance so far:

| | `Hi` arrives as | `Lo` arrives as | discriminates |
| --- | --- | --- | --- |
| **P48** | matured timer | matured timer | FIFO vs lowest-Id |
| **P47** | fresh `WAIT 0.0` continuation | matured timer | continuation-first vs both |

## REAL ANSWER (captured 2026-09-11 via kwin-mcp, this same session)

**`Hi` then `Lo` — FIFO-by-insertion. This port is BYTE-EXACT.**

```
12.000000  Hi  0.162068
12.000000  Lo  0.184654
```
(`tests/golden/ReleaseProbe48/legacy/simDetails_ReleaseProbe48.txt` — one
`RunEFPA` dump, no append contamination; `sd_diff` reports NO DIVERGENCE,
2/2 draws, values matching to the last digit.)

`Hi` is block Id **4**; `Lo` is block Id **3**. Real ran the **higher**-Id
block first, because its wait was pushed first. So:

> **`ChooseNext`'s lowest-block-Id rule does NOT govern a tie between two
> plain matured block timers.** Insertion order does.

This is the first time A.1's rule has been tested in isolation, and it
settles the first branch of the question. The lowest-Id rule is real in the
source text — so the `ActivityGroup` that
`ChooseNext` scans must be **narrower than "everything tied at this
instant"**, exactly as A.1's own standing note suspected. Two ordinary
matured block timers do not both join it.

It also explains the 2026-09-11 sweep result where a blanket lowest-Id port
failed **27 of 51** fixtures: the rule was being applied to a tie shape it
does not govern.

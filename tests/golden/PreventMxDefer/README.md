# Fixture: PreventMxDefer — pm_req_defer=True ground truth, phasing-free

Built 2026-08-21, an UNEDITED copy of the textbook corpus's `legacy/rbd/
PreventMx1_Completed.rbd` (Chapter 6, Fig 6.15). Same satellite/block
roster as `Phasing1`, but with zero phasing (`activePhases=0` on every
block) — isolates `PMReqDefer=1`'s real behavior from phasing's own
complexity, which is why this exists as a SEPARATE fixture from
`PreventMxNoDefer` rather than one combined file.

## What this is for

Every PM-using block in this file has `PMReqDefer=1`. Running it
through this port currently raises immediately:

```
NotImplementedError: block 'SolarP_500W' has pm_req_defer=True and its
PM resource pool is empty -- ObtainPMprereqs's defer-instead-of-hold
branch (a direct WriteEvents + re-entrant Run
call) is not ported
```

Source's real branch here (`ObtainPMprereqs`)
is genuinely unusual: when the PM resource pool is empty and
`pmReqDefer` is set, it logs a hand-built `WriteEvents(...,"pmReqDefer",
"No_resource_in_stock-"+poolName)` line (bypassing `ChangeBlockState`'s
own action-string formatting entirely) and then calls `Run;` as a
**plain nested call while the original call is still on the stack**
(source's own comment: "This makes block active in two methods") —
abandoning this PM attempt and returning the block to its ordinary run
loop WITHOUT the resource, a genuinely different (and much harder to
reason about statically) control-flow shape than the ordinary
PMhold-and-wait path this port already supports. What actually happens
to the shared RNG stream position and the resulting event-log ORDER
once that recursive re-entry unwinds isn't something worth guessing at
from source alone — this fixture's real capture is what should settle
it.

`Phasing1`'s own real capture already shows this exact branch firing
(`SolarP_650W`/`BatteriesNiH` at `t=48.086113`),
but entangled with phase-change machinery on top — this fixture isolates
the same mechanism cleanly.

## Files

- `input.rbd` — the model, unedited copy of `legacy/rbd/PreventMx1_Completed.rbd`.
- `legacy/` — not yet populated. Needs a real Wine capture: open
  `input.rbd` in Raptor7Student, Simulate, save the Event Log output.

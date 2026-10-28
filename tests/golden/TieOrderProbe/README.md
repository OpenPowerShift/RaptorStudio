# TieOrderProbe

Deterministic probe (all `Fixed`, no RNG) for **same-instant ordering of equal-time
timer entries that were inserted at different times** (ER3-29 / the internal engine ledger
A.2). Built by an internal build script from `SPFProbe`, timings only.

| block | Id | fail | repair | event at t=100 | inserted at |
|---|---|---|---|---|---|
| TieB3 | 3 | 20 | 80 | repair completes | t=20 |
| TieB4 | 4 | 50 | 50 | repair completes | t=50 |
| TieB5 | 5 | 100 | 10 | fails | t=0 |

Further ties at t=200, 220, 300, 320 with different insertion patterns.

## Capture provenance

`legacy/rpt_events_TieOrderProbe.txt` is real Raptor 7 Student 7.0.12 run under the
32-bit debug prefix (`.wine-raptor32`) with **no debugger attached** (2026-09-20).
A second run with the scheduler trace attached produced byte-identical rows, so the
tracer is non-perturbing (an internal comparison script). Only the event log
was saved; there is no EOR/final-results/`simDetails` capture (none is needed: no RNG).

## What it pins (observed in real)

- Equal-time resumptions run **FIFO by insertion order** (t=100: B5 fail, then B3's and
  B4's repair completions) -- not LIFO, not lowest/highest block Id.
- A spawned `TELL Repair` starts at the **tail of the instant**, after every already
  pending resumption. Real never emits the intermediate `join Bad` at t=100.

## Port status

**Diverges** (2026-09-20): the port runs the failure-spawned `Repair` body inline, so it
logs `Repairing` and its cascade immediately after `Failed`. Differences at t=100, 220
and 320. Pinned by `tests/unit/test_tie_order_probe.py` (strict xfail). The order was established by a gdb
trace of Raptor 7.

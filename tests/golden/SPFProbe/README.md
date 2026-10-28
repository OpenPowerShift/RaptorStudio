# SPFProbe — Ch.10 Single Point Failure listing

Built 2026-09-19 via an internal build script (clones
`TwoBlockRedundancy`, turns its parallel pair into a series block feeding
a parallel pair).

## Topology

```
start --1--> Series(id3) --2--> split(id6) --3--> ParallelA(id4) --4--> join(id5) --5--> end
                                    \--6--> ParallelB(id5) --7------^
```

`Series` lies on every Start->End path → **the single point failure**.
`ParallelA`/`ParallelB` are redundant → **not** SPFs. All distributions
are `Fixed` (deterministic).

## What it pins

Source's Ch.10 "Create Single Point Failure File" (`WriteSPFfile`): a per-element what-if that fails each block/
event and checks whether the system turns Red. The port implements this
in `raptor/core/engine/spf_fev.py`, which runs the same procedure on its
Failure Effects View engine (`fev.py`), and `spf.py` renders the exact listing
layout. (Until 2026-10-10 `spf.py` walked the static graph instead; real files
for models with dependencies, cold standby and an unphased series event showed
that was wrong, see `tests/unit/test_spf.py`.)

## Status: CAPTURED AND MATCHING

`legacy/rpt_single_point_failures_SPFProbe.txt` (2026-09-19) is the real
listing; the port reproduces it **byte-for-byte**
(`tests/unit/test_spf.py`). Real SPF set = `['Series']`.

Five further real listings captured the same session confirm the
algorithm and layout across shapes: `Cascade` (none), `HierarchyExample`
(`Thermal`, `Attitude`), `MotorDemo` (five series blocks), and the phased
`Phasing3`/`PhasingDemo` (per-phase `Thermal`/`Attitude`). See
`tests/golden/*/legacy/rpt_single_point_failures_*.txt`.

## Remaining divergence (ER3-25)

The port's what-if uses the static graph and node `GoodPathsRequired`
(it covers series/parallel, K-of-N, hierarchy ovals and per-phase k) but
does NOT yet run the source's `CheckColds` cold-standby switching or the
`FevDepGroup` dependency bookkeeping, so a cold-standby recovery path may
be under/over-counted. No captured fixture exercises that yet.

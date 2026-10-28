# ReleaseProbe46 -- dependent block (`dep_type=RBDBlock`) + PM needing a SparePool spare

Clone of `tests/fixtures/DepPM/input.rbd` (2 blocks, `BlockA` ->
`BlockB` via `DepNum`, `BlockB` is `DepType=RBDBlock`/frequency PM
`PMFreq=50.0`) with exactly one change: `BlockB` now draws its PM
spares from a new, PRIVATE `SparePool` (`BlockB_Spares`, `Stock=15`,
no RSO/ESO/SLO replenishment) instead of `Infinite`/`PMSpare=0`.
`pmTriggered` stays `0` and `NumRes` stays `0` -- only the SparePool
sub-case of `orchestrator.py:3135`'s guard is exercised here, not the
trigger-PM or PM-resource sub-cases (those need their own, separately
isolated probes if this one turns out to matter).

`num_runs=20`, `run_length=500.0` -- unchanged from `DepPM`, a
known-working baseline this project's own `_dependent_block_process`
frequency-PM port was already verified against.

## Why this probe

`raptor/core/engine/orchestrator.py`'s `_dependent_block_process` (the
generator for `dep_type=RBDBlock` blocks) raises `NotImplementedError`
unconditionally the moment such a block also needs a PM spare/resource,
or is trigger-based PM, citing "no fixture exercises that combination."
Confirmed 2026-09-10/11 that, unlike two sibling B.1 gaps (`ColdPool`
sparing, `resource_type_count>1`) which are very likely UNREACHABLE through
real Raptor Student Edition's own file format, this specific
combination has no such red flag -- `DepType=RBDBlock` and
`SpType=Pooled`+`PMSpare=1` are both independently ordinary, real,
already-exercised `.rbd` columns (see `tests/golden/DepPM` for the
former, `PM_Prereq`'s `BatteriesNiH` block for the latter). Nothing in Raptor 7 suggests they can't coexist on one block.
This probe exists to find out what real Raptor actually DOES with that
combination -- no manual, no prior documentation lead, fixture + real
capture is the only tool available (there is no new information beyond
what a real run of this exact model produces).

## The question

**Does real Raptor 7 Student handle a `dep_type=RBDBlock` block that
also needs a PM spare from a `SparePool`, and if so, what's the actual
event sequence?**

- **If real crashes, refuses to load, or the GUI rejects this
  configuration outright**: this combination is likely unreachable in
  practice, the same conclusion already reached for `ColdPool`/
  `resource_type_count>1` -- document that finding and consider closing this
  as "structurally unreachable," not "unported."
- **If real runs cleanly and BlockB's PM-spare consumption looks
  IDENTICAL in shape to an ordinary (non-dependent) block's own
  SparePool PM consumption** (e.g. `PM_Prereq`'s `BatteriesNiH`) **--
  just interleaved with BlockB's own `Dependence_Idle`/`Dependence_
  All_Up` cycles from `BlockA`'s status** -- the fix is likely a
  straightforward port: reuse `ObtainPMprereqs`'s existing SparePool
  branch logic (already ported for the ordinary generator in
  `block_lifecycle.py`) inside `_dependent_block_process`, the same way
  Custom sparing was already added there for `MotorDemo`.
- **If real does something UNEXPECTED** -- e.g. the PM spare draw
  behaves differently while the block is `Dependence_Idle`-frozen, or
  the spare consumption interacts with the dependency machinery in a
  way `ObtainPMprereqs`'s ordinary-block logic doesn't anticipate --
  that is itself the finding, and likely means this needs its own,
  not-yet-designed port rather than a direct reuse of the existing
  SparePool branch.

## Capture instructions

Launch via the internal Raptor launcher with `/sd`, open this
file, enable Events + End-of-Run + Final-Results reports (Options >
Simulation Options > Files & Reports), Simulate for all 20 runs. Copy
`simDetails.TXT` and the report files into `legacy/` immediately, then
delete the root copies. Watch specifically for:
- Any `PM_is_due`/`Sparing`/`pmReqDefer` event-log lines for `BlockB`.
- Whether `BlockB`'s PM ever coincides with a `Dependence_Idle`/
  `Dependence_All_Up` transition in the same run, and what the log
  ordering looks like if so.
- Whether the run completes normally at all, vs. an in-app error.

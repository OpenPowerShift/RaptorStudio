# Analytical solver

RAPTOR Studio can evaluate a coherent RBD **exactly**, without sampling, for
the subset of models where a static answer exists. The solver is separate
from the simulation engine. It is reached from **Results ▸ Analytical…** in
the application, with `raptor analytic` on the
command line, through the MCP tool `analytical_analysis`, and through
`raptor.api.analytical.analyse`.

The solver is not a replacement for simulation. It answers a smaller
question and says so when the model is outside its assumptions.

## Independence

The solver treats every block and event as **statistically independent** of
every other. Each element is evaluated from its own distribution alone. In
particular the random-number stream a block draws from has no effect on the
answer: two blocks that share a stream number are still independent in
this analysis, and a simulation using separate streams or shared ones
converges to the same value. Dependence that is modelled in the diagram
itself (dependencies, standby, shared spares or crews) is refused, not
approximated. If real components share a common cause (so their failures are positively
associated), the independent answer is optimistic for parallel structures and
pessimistic for series ones.

## Two modes

### Reliability: non-repairable, time-dependent

```bash
raptor analytic new_docs/models/Lantern1.rbds --at 48
```

Returns:

- `R(t)` at the mission time;
- the system density and hazard at that time;
- MTTF, by numerical integration of `R(t)`;
- `BX` life, for example B10;
- conditional reliability for a second mission (`--tau`);
- exact Birnbaum, RAW, RRW, Vesely–Fussell and criticality importance;
- the path-product equation (`--equation`);
- a curve with `--times`; the application plots one from 0 to the mission
  time when none is given.

The solver works on a **BDD** (binary decision diagram): a compact, exact map
of which combinations of working and failed elements keep the system up. It
is a calculation aid, not a simulation, and its node count ("solver size") is
not the number of blocks in the diagram; a k-out-of-n group of a few blocks
can need a hundred nodes.

Every block must be non-repairable (`Repair Dist = None`). If the model has
repair distributions, the command refuses by default. `--ignore repairs`
(or the older `--ignore-repairs`) computes the first-failure reliability of
the same structure and labels that assumption in the result.

### Availability: steady state, repairable

```bash
raptor analytic new_docs/models/Vendo1.rbds --mode availability
```

For independent components with perfect repair, the steady-state up
probability of a coherent system is the system success function evaluated
at

```text
A_i = MTTF_i / (MTTF_i + MTTR_i)
```

This is exact, not an approximation. The same BDD supplies the system
availability and exact availability importance. The result includes each
component's MTTF and MTTR.

Curve times, BX life and conditional reliability are reliability-mode
quantities; in availability mode the steady-state value does not depend on
time, so curve times are ignored with a warning.

The steady-state result is not the finite-horizon number a simulation
reports. Use simulation when you need the startup transient, PM, crews,
spares or repair queues.

## Leaving things out: ignore, phase and standby

Instead of refusing, the solver can be told what to assume. Each choice is
recorded in the result's `scope` and listed under the assumptions, and the
model is never changed.

- **Ignore** (`--ignore repairs,pm,spares,resources`): drop a whole class of
  simulation-only behaviour. *Repairs* (reliability mode only) gives the
  first-failure reliability. *PM* leaves out preventive maintenance (no PM
  downtime, no age renewal). *Spares* assumes a replacement part is always
  available at once. *Resources* assumes crews and tools are always
  available. The answer then describes a better-supported system than the
  real one; use simulation for the real behaviour.
- **Phase** (`--phase N`, 1-based): analyse a phased model as if it stayed in
  phase N for the whole time. Phase changes during the mission are not
  modelled, and the result says so. In that phase an Active block's phase
  value multiplies its duty (a value of 0 means it does not age), a Cut block
  is out of the circuit (no path through it), a Linked block is bypassed
  (perfect), a node uses the phase's k-of-n requirement, and an event takes
  the phase's success probability. A phased model with no phase chosen is
  refused.
- **Standby** (`--standby active|absent`): cold-standby nodes are refused
  unless you choose. `active` treats every standby path as fully in service
  (an ordinary parallel path); `absent` keeps only the first k paths in
  switching-priority order and removes the rest (their elements show no
  importance). Switching time and switch failures are ignored either way.
  This is independent of the phase choice.

Dependencies and non-Bernoulli events are always refused.

## What is in scope

- Acyclic RBDs with one Start and one End node.
- Series, parallel, mixed, k-out-of-n and bridge/reconvergent networks.
- Hierarchies, flattened to their internal in/out boundary.
- Bernoulli events with a constant success probability.
- All supported continuous life distributions: Fixed, Exponential, Weibull,
  Normal, Lognormal, Gamma, Erlang, Beta, Chi-square, Uniform real, Triangular,
  Pearson 5, Pearson 6, Extreme value, Laplace and Empirical. The engine's
  Exponential-power stub is not a life distribution and is refused.
- `Failure Dist = None` as a perfect block.

## What is out of scope

The solver refuses, naming the first offending element:

- repairs in reliability mode (unless ignored);
- PM, spares, resources, crews (unless ignored);
- dependencies;
- cold standby (unless you choose how to treat it) and phases (unless you
  choose a phase);
- capacity/flow as the success criterion: capacity analysis is ignored with a warning, and success is the End node being up;
- non-Bernoulli events;
- non-coherent logic.

Duty cycle scales effective age in reliability mode and the calendar MTTF in
availability mode. A block with a start age is treated as having already
survived to that age (start condition 3), so its reliability is conditional
on that; other start conditions, and a non-zero amount already consumed,
are refused in reliability mode. Start age does not affect steady-state
availability.

### Progress and limits

The application shows a determinate progress bar while the BDD is built and
while importance measures are calculated. It reports the graph nodes visited
and the current BDD node count, so a long build is visible rather than an
indefinite spinner.

On the command line, `--progress auto` draws a bar on a terminal,
`--progress json` writes one JSON event per line to stderr, and
`--progress none` is silent. Progress never goes to stdout, so `-f json`
stays parseable.

Resource bounds: 5,000 blocks/events and 2,000,000 BDD nodes by default.
Raise them per call with `raptor analytic --max-variables N` and
`--max-bdd-nodes N`, or in the application under Settings ▸ Limits. Raising
a limit does not make an intractable diagram tractable: the BDD can grow
exponentially, and the solver refuses rather than approximate. For a large
model with repeated hierarchy blocks, solve the series submodels separately
and combine them; the result is exact for the decomposition.

The solver also caps curve times and BX percentages. These are refusals, not
approximations.

## The application window

**Results ▸ Analytical…** solves on a background thread, so the window stays
responsive on a large diagram. Choose the mode, mission time (it defaults to
the model's run length, and a model that is not time-terminated asks for
one), optional curve times, BX percentages, which extras to include, which
simulation-only features to ignore and (when the model has them) which phase
and standby treatment to use, then press **Compute**. Changing a setting after a result leaves the old result in
place with a "press Compute" note rather than silently recomputing.

When a phase, a standby treatment or any ignored feature was chosen, a note
above the tabs says so ("This result assumes: Phase 3 (Cruise) analysed as
if the system stayed in it the whole time; …"), the Elements tab gains a
Treatment column (cut, linked, stress ×, standby treated as absent), and the
same facts head the copied text and the exported Summary sheet.

The result has these tabs:

- **Summary**: the headline figures, one card each, with the exact value in
  the tooltip;
- **Elements**: one row per block or event with its reliability (or
  availability), MTTF and, in availability mode, MTTR, plus the importance
  measures. Click a header to sort; the default order is by criticality;
- **Curve**: a plot of `R(t)` with the values beside it (reliability mode);
- **Equation**: the exact sum of path products, when requested;
- **Assumptions**: what the answer rests on, with any warnings first.

A model outside the solver's subset shows the reason, naming the first
offending element, in place of the tabs. The window never changes the model.
**Copy** puts the full-precision plain-text result on the clipboard, with a
short note on what a BDD is at the top. **Export…** saves a spreadsheet
(.xlsx) with Summary, Elements (with the importance measures), Curve,
Equation and Assumptions sheets. The simulation's table export does not
include analytical results; use this button for them.

**Results ▸ Availability and Criticality…** (simulation metrics) has its own
**Copy** (the visible tab, tab-separated) and **Export…** (both tabs, .xlsx).

## Reading the result

- `reliability` is the probability that the system has not failed by `--at`.
- `pdf` and `hazard` are the system density and failure rate at `--at`.
  A Fixed distribution is a point mass: its density is zero away from the
  atom and undefined exactly on it, so the system `pdf` and `hazard` are
  `N/A` when `--at` falls exactly on a Fixed block's life (or an Empirical
  distribution's knot).
- `MTTF` is `integral R(t) dt` from zero to infinity. If the tail does not
  decay (an infinite mean), the result is `N/A`, not a made-up number.
- `BX` is the time at which reliability reaches `1 - X/100`. `B100` is the
  earliest time at which reliability is exactly zero. It exists only when the
  elements whose life distributions have a finite upper end cut every
  Start-to-End path: a series chain reaches zero at its *earliest* such end,
  a parallel group at its *latest*, and a model with no such cut (for
  example one made only of Weibull blocks) has no B100, reported as `N/A`.
- Importance columns:
  - Birnbaum: how far the system moves if this element's probability moves;
  - RAW: how much worse the system is with the element dead;
  - RRW: how much better it could be if the element never failed;
  - Vesely–Fussell: the share of the system's unavailability that
    disappears if the element is made perfect;
  - criticality: the probability that the element is failed and critical,
    given that the system has failed.
- `Bx life` is read as "x % of systems fail by this time".

Ratios are `N/A` when the system has no unavailability to divide by, or when
the denominator of that particular ratio is zero (RRW when making the element
perfect leaves no unavailability). A ranking finer than the model's input
precision is noise; round the answer to the precision the inputs support.

## Existing models

No rebuild is needed. Open an existing model exactly as before:

- a legacy `.rbd` loads with the new age settings at their defaults
  (`duty_cycle = 1`, `start_age = 0`, `restoration_factor = 0`,
  `restoration_type = 1`);
- an older `.rbds` (version 1 or 2) loads unchanged; the next save writes
  version 3, including any non-default age fields;
- a model with `PM Refresh` already on keeps its existing as-good-as-new
  behaviour; restoration factor only matters when it is non-zero.

The legacy `.rbd` format has no columns for the age settings. Saving an
edited model to `.rbd` drops them (the application warns before doing so).
Use **Save As** with `.rbds` if those settings matter. Analytical results for
an untouched model are identical under either format.

## API

```python
from raptor.api.analytical import analyse
from raptor.api.workspace import Workspace

workspace = Workspace("/tmp/raptor-analytic")
model_id = workspace.open_model("new_docs/models/Lantern1.rbds")["model_id"]
result = analyse(workspace, model_id, mode="reliability", at=48.0,
                 times=[0, 10, 48], bx=[10], importance=True)
```

The result is JSON-safe: undefined values are `null`, never NaN or Infinity.
The schema string is `raptor-analytic/1`.

## Validation

The solver is checked against:

- the engine's own random draws for every supported distribution;
- brute-force truth tables for small BDDs, including k-of-n and bridge
  networks;
- the worked values in `new_docs/models` and hand calculations from their
  distributions;
- an independent simulation of the same model (`pytest -m long`).

The tests are in `tests/unit/test_analytical_*.py`.

## Report data

The simulation's per-run JSON now also carries MTTFF, the inherent /
achieved / operational availability classifications, Red-time cause buckets,
and per-block failure, downtime and cost criticality. These are additive:
they do not alter the legacy `End_Of_Run` or `Final_Results` text, which is
byte-compared against captured reference output. Read them with
`raptor.api.availability` or from `per_run.json`.

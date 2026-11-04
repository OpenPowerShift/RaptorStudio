# Analytical solver

RAPTOR Studio can evaluate a coherent RBD **exactly**, without sampling, for
the subset of models where a static answer exists. The solver is separate
from the simulation engine. It is reached from **Results ▸ Analytical…** in
the application, with `raptor analytic` on the
command line, through the MCP tools `analytical_analysis` (the result) and
`export_analytical` (a spreadsheet), and through
`raptor.api.analytical.analyse`. All four take the same settings and give
the same numbers.

The solver is not a replacement for simulation. It answers a smaller
question and says so when the model is outside its assumptions.

## Independence

The solver treats every block and event as **statistically independent** of
every other. Each element is evaluated from its own distribution alone. In
particular the random-number stream a block draws from has no effect on the
answer: two blocks that share a stream number are still independent in
this analysis, and a simulation using separate streams or shared ones
converges to the same value. A dependency drawn in the diagram is refused
unless you choose how to treat it (see *Dependencies* below), and shared
spares or crews are refused rather than approximated. If real components
share a common cause (so their failures are positively
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

### Dependencies

A block or node can depend on another element: when the depended-upon
element is down, the dependent one is forced idle. A static success function
has no state to carry that interaction, so the solver refuses a model with
dependencies until you choose a treatment:

```bash
raptor analytic new_docs/models/EspressoDep1.rbds --at 100 --ignore repairs --dependencies down
```

- `--dependencies up` (API/MCP `dependencies="up"`) ignores the dependency:
  every dependent block or node is analysed as if the depended-upon element
  were always up. This is optimistic — the dependency never blocks it.
- `--dependencies down` treats every dependency as failed for the whole
  mission, including the first instant: each dependent element is forced out
  of the success path (its availability and reliability are 0). This is
  conservative.

`down` is never more reliable than `up`. The choice is reported in the
result's `scope.dependencies` and in the assumptions. The GUI shows a
**Dependencies** row only for a model that has them; like **Standby**, it
starts unchosen, so the model is refused until a treatment is picked.

It is independent of the phase and standby choices below.

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

### Both modes at once

```bash
raptor analytic new_docs/models/Lantern1.rbds --mode both --xlsx .
```

`--mode both` solves reliability and then availability with the same
settings; the reliability-only ones (`--at`, `--times`, `--bx`, `--tau`,
ignoring repairs) apply to the reliability solve only. Text output prints
both summaries, `-f json` gives `{"results": [reliability, availability]}`,
and `-f csv` adds a leading `mode` column. In MCP, `export_analytical` takes
`modes=["reliability", "availability"]`; in the application, use
**Export ▸ Reliability and availability…** on a result. The API is
`raptor.api.analytical.analyse_modes`.

## Leaving things out: ignore, phase, standby and dependencies

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
  refused. `--phase all` (MCP `export_analytical(phase="all")`, and *All
  phases, one at a time* in the application) solves each phase on its own,
  one result per phase; the changes from one phase to the next are still
  not modelled. The application shows one phase at a time (*Showing
  phase* above the tabs) and exports them all, a set of sheets each
  ("Phase 2 Summary"); `-f json` gives `{"results": [...]}` and `-f csv` a
  `phase` column.
- **Standby** (`--standby active|absent`): cold-standby nodes are refused
  unless you choose. `active` treats every standby path as fully in service
  (an ordinary parallel path); `absent` keeps only the first k paths in
  switching-priority order and removes the rest (their elements show no
  importance). Switching time and switch failures are ignored either way.
  This is independent of the phase choice.
- **Dependencies** (`--dependencies up|down`): a dependent block or node is
  refused unless you choose. `up` ignores the dependency and analyses the
  dependent element's own failure law; `down` forces every dependent element
  out of the success path. See *Dependencies* above. This is independent of
  the standby choice.

Dependencies with no treatment and non-Bernoulli events are always refused.

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
- dependencies (unless you choose `up` or `down`);
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

Progress is one percentage for the whole solve, the same in the
application, on the command line and in MCP. A solve builds the diagram
and then works the results out from it. The size of the diagram is not
known until it is built, but the split between the two is: building costs
about 100 times as much per node as one walk over the finished diagram,
so the build's share is about 100 / (100 + walks), whatever the size.
The walks are known from the settings (about 900 for MTTF, 360 per BX
percentage, one per curve point, two per element for importance). On the
BESS model with importance the build is about 3 % of the solve; in
availability mode without importance it is nearly all of it. The figure
never goes back: MTTF and BX are adaptive, and when one runs long the
figure holds rather than steps back.

Beside the figure is the stage: the nodes so far and their memory while
building, then reliability, MTTF, BX life, the curve and importance, each
with the time left. Importance on a large diagram can be the longest
stage (25-45 s for the 476,000-node BESS model); its walks are counted as
they finish.

On the command line, `--progress auto` draws a one-line bar on a
terminal, `--progress json` writes one JSON event per line to stderr
(`done` of `total` = 1000 is the figure), and `--progress none` is silent.
Progress never goes to stdout, so `-f json` stays parseable. MCP sends the
figure as progress notifications, with the percentage and stage in the
message.

### How large the diagram may grow

The solver works on a BDD, a diagram of which combinations of working and
failed elements keep the system up. Its size depends on the structure, not
just the number of blocks.

**Before building**, the solver checks the structure and reports a size
outlook (the application shows it as "Diagram size: …"; the CLI and MCP get
it as a progress message, and a size refusal repeats it):

- If no junction's inputs share blocks, the diagram grows with the size of
  the model, not exponentially. Blocks that every input reaches through one
  common element (a feeder in series ahead of a k-of-n stage, say) do not
  count: the solver factors that element out exactly, as
  `cond(J) = cond(D) AND cond(J with D working)`.
- Otherwise it names the junctions whose inputs share blocks, worst first
  (k-of-n with 1 < k < n grows fastest). That is a warning sign, not a
  forecast: the exact size is only known by building.

**While building**, the progress line shows the nodes so far and their
memory, and says when the build has used most of its limit with most of the
junctions still to go.

**The limit** is 5,000 blocks/events and 2,000,000 BDD nodes by default:
a few seconds and well under 1 GB. Raise it per call with
`raptor analytic --max-variables N` and `--max-bdd-nodes N`, with the MCP
`max_variables` / `max_bdd_nodes` arguments, or in the application under
Show advanced settings ▸ Limits, which shows the memory a limit needs. The
most this machine allows is half of the memory free at the time, at about
350 bytes per node (8,000,000 when free memory cannot be read); anything
higher is refused before solving, because a diagram that outgrows memory
gets the application killed by the operating system. A size refusal says
how far the build got, and the application offers a step up.

A larger diagram is slower to use as well as to build. The results walk
it many times: twice per element for importance, once per curve point,
about 350 times per BX percentage, and hundreds to over a thousand times
for MTTF. The walks
are vectorised with NumPy: every node that tests the same element is worked
out at once, and many walks (all the importance cases, all the curve
points, one level of the MTTF integration) share one pass, which made
evaluation 8-20 times faster on the large example models. The progress
line counts the walks and estimates the time left: MTTF and BX are
adaptive, so their counts are guesses that grow if a stage runs long. Raising the limit does
not make an intractable diagram tractable, and the solver refuses rather
than approximate. Solving one hierarchy at a time is usually the better
route for a large model.

The solver also caps curve times and BX percentages. These are refusals, not
approximations.

## The application window

**Results ▸ Analytical…** solves on a background thread, so the window stays
responsive on a large diagram. Nothing is solved on opening: a large model
can take minutes. Choose the mode, mission time (it defaults to the model's
run length, and a model that is not time-terminated asks for one), optional
curve times, BX percentages, which extras to include, which
simulation-only features to ignore and (when the model has them) which phase,
standby and dependency treatment to use, then press **Compute** (or Enter).
**Cancel**, or closing the window, stops a solve within about half a second.

Choosing a different mode, treatment or option clears the result ("Ready to
compute"), so an answer is never shown for settings it was not worked out
for. A typed change (times, BX) leaves the result in place with a "press
Compute" note. Results already worked out are kept while the window is
open, for the model as it is: going back to earlier settings, or pressing
Compute again, shows theirs at once. An edit to the model, or opening
another model, starts over. The one-click fixes a refusal offers ("Ignore
repairs and compute", "Choose a phase…") apply the fix and solve.

When a phase, a standby or dependency treatment or any ignored feature was
chosen, a note above the tabs says so ("This result assumes: Phase 3 (Cruise)
analysed as if the system stayed in it the whole time; …"), the Elements tab
gains a Treatment column (cut, linked, stress ×, standby treated as absent,
dependency forced down), and the same facts head the copied text and the
exported Summary sheet.

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
short note on what a BDD is at the top. **Export ▸ This result…** saves a
spreadsheet (Excel .xlsx or OpenDocument .ods, chosen in the Save dialog)
with Summary, Elements (with the importance measures),
Curve, Equation and Assumptions sheets. **Export ▸ Reliability and
availability…** saves the result shown and the other mode with the same
settings in one workbook, a set of sheets each ("Reliability Summary",
"Availability Elements", ...), as `--mode both` does on the command line.
If the other mode has not been worked out, the window asks for the file,
works it out (with the usual progress; the page keeps the result shown),
then writes the workbook. The file is named after the model, the
hierarchy if one was solved on its own, and the modes:
`Plant_analytical_reliability.xlsx`,
`Plant_h12_analytical_reliability_availability.xlsx`. The simulation's table export does not
include analytical results; use this button, `raptor analytic --xlsx` (or
`--ods`) or the MCP tool `export_analytical` (`file_format="ods"` for
OpenDocument) for them. The workbook's layout is the same as every other
spreadsheet export's (manual, "How the spreadsheets are laid out"): the
Elements sheet is ranked by importance, or weakest first without it, with
bars on the Birnbaum, Fussell-Vesely and criticality columns.

**Results ▸ Availability and Criticality…** (simulation metrics) has its own
**Copy** (the visible tab, tab-separated) and **Export…** (both tabs, .xlsx
or .ods).

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
  `restoration_type = 0`, which is off);
- an older `.rbds` (version 1 or 2) loads unchanged;
- an older `.rbds` (version 3 or earlier) is read as it was written: a
  restoration factor of 0 is off, and a factor saved without a type is
  Type I; the next save writes version 4;
- a model with `PM Refresh` already on keeps its existing as-good-as-new
  PM; restoration only applies when the restoration type is Type I or
  Type II;
- availability mode refuses a block with restoration Type I or II and a
  factor below 1, because a repair then leaves the block partly worn.

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

```python
from raptor.api.analytical import analyse_modes
from raptor.api.analytical_export import export_file_name, export_workbook

results = analyse_modes(workspace, model_id, ["reliability", "availability"],
                        bx=[10])
export_workbook(results, export_file_name("Lantern1", results))         # .xlsx
export_workbook(results, export_file_name("Lantern1", results, "ods"))  # .ods
```

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

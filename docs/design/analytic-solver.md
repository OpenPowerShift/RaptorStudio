# Design note: analytical solver for coherent RBDs

Status: **implemented on `feat/analytic-solver`.** This note is the
implementation contract for the analytical solver and the report-layer
metrics that ship with it. The scope is deliberately narrow: it covers the
subset of RBDs for which an exact answer exists, and it says so when a model
is outside that subset.

Implemented in this branch:

- `raptor/core/analytical/` — BDD, survival functions, network builder,
  metrics and equation rendering.
- `raptor/api/analytical.py` — `analyse` (workspace) and `analyse_rbd`
  (in-memory model, used by the GUI).
- `raptor/cli/cmd_analytic.py` — `raptor analytic`.
- `raptor/gui/ui/analytic_dialog.py` — Results ▸ Analytical…
- `raptor/mcp/tools_analytic.py` — `analytical_analysis`.
- Per-run MTTFF, availability classifications, Red-time buckets and
  criticality in `RunReportData` / `per_run.json`, with read helpers in
  `raptor/api/availability.py`.
- Duty cycle, start age and restoration factor in the engine and `.rbds`.

## 1. Why

The simulator answers "what does this model do?" by sampling. It cannot
answer "what is the exact probability of success?" and it cannot supply an
independent check on its own answer. A second, static solver does both for
the subset where a static answer is meaningful:

- non-repairable, time-dependent, coherent RBDs;
- repairable RBDs in steady state, when component states are independent
  and repair is perfect.

The second case is the one that matters for availability studies. For
independent alternating-renewal components the steady-state up probability
of a coherent system is the system success function evaluated at

```text
A_i = MTTF_i / (MTTF_i + MTTR_i)
```

That is exact, not an approximation, and it needs the same Boolean success
function as the reliability case. One solver, two questions.

The solver is also the only way to turn the report's *estimated* importance
measures into exact ones on this subset.

## 2. Scope

### In scope

- Acyclic RBDs with Start and End nodes.
- Series, parallel, mixed, k-out-of-n, bridge/reconvergent networks.
- Hierarchies, flattened to their internal in/out boundary.
- Blocks with one input and one output link.
- Bernoulli events (constant success probability).
- All continuous life distributions the engine offers.
- Non-repairable reliability: `R(t)`, `f(t)`, `λ(t)`, MTTF, BX life,
  conditional reliability, exact importance.
- Steady-state availability for repairable models: `A_ss`, availability
  importance, RAW/RRW/Vesely-Fussell, criticality.
- Equation text in block-reliability tokens.

### Out of scope

- Any repair/restoration process at finite time, except the steady-state
  up probability above.
- Preventive maintenance, inspections, on-condition tasks.
- Spare pools, resource pools, crews, queues.
- Dependencies and switching (cold standby is analysed only when the caller
  chooses to treat it as in service or absent).
- Phase changes during a mission (one chosen phase can be analysed as if
  the system stayed in it).
- Capacity/flow as the success criterion.
- Non-coherent logic (inverted blocks, fault trees).
- Load sharing, common cause, multi-state blocks.
- Symbolic integration or algebraic simplification. The equation is a
  multilinear polynomial in block reliabilities; it is evaluated
  numerically at each requested time.

The model gate is a feature. A fast wrong answer is worse than a refusal.

## 3. Architecture

```text
raptor/core/analytical/
    __init__.py       public surface: build(), evaluate, survival
    bdd.py            reduced ordered BDD: apply, not, threshold, restrict,
                      probability, node count, size cap
    survival.py       R_i(t), f_i(t), support/quantile for every supported
                      distribution; incomplete gamma/beta helpers
    network.py        RbdFile -> Boolean success function; hierarchy
                      flattening; applicability gate
    evaluate.py       R(t), f(t), hazard, MTTF, BX, conditional,
                      importance/criticality, steady-state availability and
                      the BDD -> token equation (sum of path products)
raptor/api/analytical.py
raptor/cli/cmd_analytic.py
```

The GUI and MCP layers consume `raptor/api/analytical.py`; they do not
import the BDD directly.

### 3.1 Boolean success function

For each node `v`:

```text
F(v) = Threshold_kv( { F(u_b) AND x_b : b in incoming(v) } )
```

- `x_b` is the block variable.
- `u_b` is the node feeding block `b`; if blocks are chained directly, the
  condition walks back to the first real node.
- `k_v` is `good_paths_required` (`-1` is already normalised to 1 by the
  reader; a Start node is TRUE; an End node is 1).
- A direct node-to-node link contributes `F(source)` with no variable.
- A hierarchy boundary link is already resolved by `load_model` to the
  internal in/out node. Flattening is therefore a graph splice, not a
  second model.

`Threshold_k` is built by dynamic programming over the branch BDDs:

```text
G_0 = TRUE, G_j = FALSE for j > 0
for each branch b:
    G'_j = (G_j AND NOT b) OR (G_{j-1} AND b)
result = OR_{j >= k} G_j
```

Special cases: `k <= 0` is TRUE, `k > n` is FALSE, `k = 1` is OR, `k = n` is
AND. The DP is exact even when branches share variables. A node cap
(`MAX_BDD_NODES`, default 250,000) turns a combinatorial explosion into a
clear refusal, never a silently truncated answer.

Variable order is the first-encounter order of a depth-first walk from
Start. That groups series chains and keeps the common k-of-n cases small.

### 3.2 Distribution survival functions

`survival.py` mirrors the engine's parameter conventions exactly, not the
textbook ones. Each supported distribution gets:

```text
sf(code, params, t) -> float       # P(T > t), 0 <= sf <= 1
pdf(code, params, t) -> float|None # None at a point mass
mean(code, params) -> float|None   # None when undefined/infinite
support(code, params) -> (lo, hi|None)
```

Supported: Fixed, Exponential, Weibull, Normal, Lognormal, Gamma, Erlang,
Beta, Chi-square, Uniform real, Triangular, Pearson 5, Pearson 6,
Extreme value, Laplace, Empirical. `FDist = None` is a perfect block
(`R = 1`).

Rejected: Binomial, Poisson, Uniform integer, Exponential-power (the engine
stub), and any parameter set outside the distribution's valid range.

Incomplete gamma and beta are implemented with the standard series and
continued-fraction forms from *Numerical Recipes*, using `math.lgamma` and
`math.erfc`. No new runtime dependency.

The parameter conventions are the part most likely to be wrong, so every
distribution is tested against `raptor.core.rng.RandomGen` draws:

1. draw 200,000 samples with a fixed seed;
2. compare the empirical survival at several quantiles with the analytic
   value;
3. fail on a tolerance derived from the binomial standard error.

This catches a swapped shape/scale before it can reach a result.

### 3.3 Metrics

With `p_i = R_i(t)` for reliability mode or `p_i = A_i` for availability
mode:

```text
P_sys             = prob(BDD, p)
I_B(i)            = prob(BDD | x_i = 1) - prob(BDD | x_i = 0)
f_sys(t)          = sum_i I_B(i, t) * f_i(t)
lambda_sys(t)     = f_sys(t) / P_sys(t)
MTTF              = integral_0^inf P_sys(t) dt
B_X               = solve P_sys(t) = 1 - X/100
R_cond(tau | t)   = P_sys(t + tau) / P_sys(t)
RAW(i)            = (1 - P_sys|x_i=0) / (1 - P_sys)
RRW(i)            = (1 - P_sys) / (1 - P_sys|x_i=1)
VF(i)             = (P_sys|x_i=1 - P_sys) / (1 - P_sys)
Criticality(i)    = I_B(i) * (1 - p_i) / (1 - P_sys)
                      # unavailability removable by making i perfect
```

The `f_sys` identity follows because the BDD probability is multilinear in
the `p_i` and `dR_i/dt = -f_i`. It gives the system pdf without finite
differences.

MTTF uses adaptive Simpson on `[0, T]`, where `T` is found by doubling
until `P_sys(T) < 1e-12` or a hard limit is reached. If the tail does not
decay, MTTF is reported as `None` ("does not converge"), never as a
made-up number. BX uses bisection on the same monotone interval. Hazard is
`None` when `P_sys` underflows or `f_sys` is undefined (a Fixed point mass).

For availability mode, `A_ss` is the BDD probability with constant `p_i`.
For a non-repairable block `p_i = 0` at steady state; a perfect block
(`FDist = None`) is `p_i = 1`. This is why a non-repairable block in series
correctly gives `A_ss = 0`.

### 3.4 Applicability gate

`network.build()` returns a `BuildResult` with the BDD plus:

- `mode`: reliability or availability;
- `warnings`: capacity analysis ignored, unreachable elements, start
  failures ignored, non-repairable blocks in availability mode. (Shared
  random streams are deliberately not a warning: independence is an
  assumption stated in the docs.);
- `errors`: unsupported shape.

Reliability mode requires:

- every block's `repair_distro` is `NONE`, or `repairs` is in `ignore`
  (the result says "repair distributions were ignored");
- no PM, spares or resources unless in `ignore`; no capacity success
  criterion or non-Bernoulli events; dependencies only with a
  `dependencies` choice (`up` or `down`); cold standby only with a `standby`
  choice, and phases only with a chosen `phase`.

Availability mode requires:

- every block is repairable (`repair_distro != NONE`), perfect
  (`fail_distro == NONE`), or explicitly non-repairable and accepted as
  `A_i = 0` at steady state. A series path through a non-repairable block
  correctly gives `A_sys = 0`; a parallel path may still keep the system up.
- the same scope rules as reliability mode (`ignore`, `phase`, `standby`);
- pre/post logistic delays are allowed only when their distributions are
  non-random or their means are finite; they are added to MTTR;
- no warning about shared random streams: draws from one stream are still
  independent, and independence is documented as an assumption of the
  solver (see `docs/ANALYTICAL.md`, "Independence").

Hierarchies are always supported. Any model that fails the gate gets a
message naming the first offending element and field.

## 4. Public API

```python
from raptor.api import analytical

result = analytical.analyse(
    workspace, model_id,
    mode="reliability",          # or "availability"
    at=1000.0,                   # mission time; default model run length
    times=[0, 100, 1000],        # optional curve
    bx=[10],                     # optional B_X lives
    importance=True,
    equation=False,
    ignore_repairs=False,
)
```

The result is JSON-safe: `None` for undefined/infinite values, never NaN or
Infinity. Shape:

```json
{
  "schema": "raptor-analytic/1",
  "mode": "reliability",
  "applicable": true,
  "assumptions": ["independent components", "no repairs"],
  "warnings": [],
  "time": 1000.0,
  "reliability": 0.9876,
  "pdf": 0.0000123,
  "hazard": 0.0000124,
  "mttf": 12345.6,
  "bx": {"10": 4567.8},
  "conditional": {"tau": 100.0, "reliability": 0.998},
  "importance": [{"id": 1, "name": "Pump", "birnbaum": 0.9,
                  "raw": 1.1, "rrw": 1.2, "vesely_fussell": 0.3,
                  "criticality": 0.4}],
  "equation": "R_Pump * (1 - (1 - R_Standby))",
  "bdd_nodes": 42
}
```

For availability mode the keys are `availability`, `unavailability`,
`mttf`, `mttr`, and the same importance block.

`at` defaults to `SYSTEM_DATA.TermCondition` when the model is
time-terminated; otherwise it is required. `times` is evaluated on the same
BDD, so a curve is cheap.

## 5. CLI

```text
raptor analytic MODEL
    --mode reliability|availability
    --at T
    --times T1,T2,...
    --mttf
    --bx 10,5
    --importance
    --equation
    --ignore repairs,pm,spares,resources   (--ignore-repairs still works)
    --phase N
    --standby active|absent
    -f text|json|csv
```

Text output is a compact report; JSON is the API shape with a
`raptor-analytic/1` schema. The command refuses an inapplicable model with
exit code 3 and a message naming the first offending element.

MCP gets one tool, `analytical_analysis`, wrapping the same API. It is
added after the CLI is stable so the CLI and the MCP schema cannot drift.

## 6. Validation plan

Existing repository models only. No external study model is used.

| Test | Fixture / method |
|---|---|
| Single block, each distribution | `new_docs/models/Toaster_Fixed.rbds`, `Toaster_Weibull`, `Toaster_Expo`; synthetic parameter matrix |
| Series | `new_docs/models/Lantern1.rbds` (3 non-repairable blocks) |
| Parallel | synthetic 2-block parallel; `LadL1Parallel` for the availability analogue |
| k-of-n | synthetic 2-of-3, 3-of-5; IEC 61078 worked values |
| Bridge | synthetic 5-block bridge; brute-force truth table |
| Hierarchy | `new_docs/models/Van1.rbds` vs flattened `VanFlat.rbds` (availability) |
| Mixed distributions | `new_docs/models/Drone1.rbds` (Normal, Exponential, Weibull) |
| Availability | `Bakery1`, `CarCare0`, `Laundromat1`, `Vendo1`; compare to a long simulation with `--stats-start` |
| Simulation oracle | non-repairable synthetic models; compare `R(t)` to simulation Reliability within 4 SEM |
| BDD truth table | every `n <= 12` assignment, independent graph traversal |
| Report metrics | existing golden fidelity fixtures; new report rows must not change old output |

The book models are already number-gated by `new_docs/tools/check_numbers.py`.
The solver's values for those models are registered there when they are
stable, so the docs cannot drift from the code.

## 7. Report-layer metrics shipping with the solver

These are cheap, use existing accumulators, and are not blocked on the
solver:

- criticality indices per block (failure, downing event, downtime, cost);
- point availability at a chosen time, distinct from mean availability;
- MTTFF, distinct from MTBDE;
- two-flavour MTBF/MTBE (total-time and uptime denominators);
- inherent / achieved / operational availability from the existing state
  buckets, with the formulas named;
- event-count breakdown (failures, corrective maintenance, inspections,
  PM, on-condition, total).

The report additions are additive. The existing columns and the legacy
Single Point Failure `Criticality` value are not changed.

## 8. Age features shipping with the solver

These are per-block fields and an engine arithmetic change, not a new
subsystem:

- **Duty cycle**: positive multiplier on accumulated operating age. Phase
  and block duty cycles compound.
- **Restoration factor**: 0..1; 1 = as good as new, 0 = no restoration,
  with Kijima Type I and Type II variants. `pm_refresh` remains the
  boolean shorthand and is interpreted as factor 1.
- **Start age**: non-zero age at t=0; the first failure draw uses the
  conditional reliability at that age.

They share one age accumulator, so they are implemented together and
tested against the existing start-condition and PM-refresh fixtures.

## 9. Phases

1. **Core**: BDD, survival, truth-table tests.
2. **Network**: hierarchy flattening, applicability gate, reliability
   metrics, CLI.
3. **Availability**: steady-state mode, exact importance, criticality.
4. **Report**: criticality, availability classifications, point
   availability, MTTFF, event counts.
5. **Age**: duty cycle, restoration factor, start age.
6. **Interfaces**: MCP tool, GUI tab, docs.

Phase 1–3 is the user-testable "analytic solver". Phase 4–5 are the
"Build now" report and correctness items. Phase 6 is follow-through.

## 10. Risks

- **Parameterisation drift.** The engine's shape/scale order is not the
  textbook order. Mitigation: RNG-distribution tests for every
  distribution.
- **Model-semantics drift.** Node k, hierarchy boundaries, direct links and
  block chains are easy to get subtly wrong. Mitigation: independent
  truth-table traversal and simulation oracle tests.
- **BDD blow-up.** k-of-n with shared branches can explode. Mitigation:
  node cap, clear refusal, special-case OR/AND, first-encounter variable
  order.
- **Steady-state assumptions.** Independence and perfect repair are
  strong. Mitigation: gate, warnings, the documented independence
  assumption, and an explicit "this is not the finite-horizon simulation number" note.
- **Expectation creep.** Users will ask for PM, spares and crews in the
  analytical mode. Mitigation: scope statement in every result and in the
  manual; those stay simulation-only.

"""Port of `RBDBlockObj.BackFlowDependencies`'s deterministic subset and the dispatch guard that decides when to call
it, from `ReconfigureBlock` -- plus
`repair_completion_status`, a second, independent dependency mechanism from
`DetermineNewState`'s "Maintenance Completed" handler (see its docstring).

Ordering: `ReconfigureBlock` calls `BackFlowDependencies` before its own
`ReconfigureNode` call on the depended-upon block's downstream node. A
dependent block's Idle/Running transition (and its own node/system cascade)
completes and is logged first, then the depended-upon block's link status
update. The real log shows this as two separate `"Node"`/`"join"` lines at the
same `SimTime`: the first reflecting only the dependent block's link, the
second the depended-upon block's own link. Reversing the order changes the
intermediate aggregation lines even though the final state is the same.

The deterministic `DepIdlePerc=100.0` case (dependent always goes Idle on
`Down`, always returns to Running on `AllUp`) is ported, as is the RNG-draw
mixed-outcome branch (`DepNothingPerc`/`DepPMPerc`/`DepFailPerc`) via the
`draw_action`/`induce` callbacks, for a block depending on `RBDBlock`,
`RBDHier` or `RBDEvent`. The `changeType=NodeStandby` dispatch transitions
dependent Running/Idle blocks to `Standby`. `orchestrator.py`'s own setup-time
guard raises for anything else (`dependency_num > 0` with a `dep_type` this
module doesn't dispatch) rather than silently mis-simulating.

Checked against `tests/golden/BlockDependency` in `tests/unit/test_dependency.py`.
"""
from __future__ import annotations

from typing import Literal, Protocol
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from raptor.core.model import (
    ActiveStatus, BlockStatus, EventStatus, Link, NodeStatus, RBDBasic, RBDBlock, RBDEvent, RBDNode,
)
from raptor.core.rng import Distribution, RandomSource
from raptor.core.engine.diststats import gamma_function
from raptor.core.engine.errors import EngineInvariantError
from raptor.core.engine.interrupts import InterruptReason

DependencyTrigger = Literal["Down", "AllUp", "NodeStandby"]

#: Signature for the caller-supplied full-transition callback: perform
#: `change_block_state` + `reconfigure_after_block_change` (+ any further
#: node/system cascade) for `dep_block`, exactly like a normal top-level
#: block transition -- this module doesn't touch node/system state
#: directly, it only decides WHICH blocks need a "Dependence"-triggered
#: transition and in what order.
TransitionFn = Callable[[RBDBlock, BlockStatus, str, float], None]

#: Signature for the caller-supplied "wake this block with a new interrupt
#: reason, but don't transition it" callback, for the `"GoToPM"`/
#: `"InducedFailure"` mixed-percentage outcomes. Unlike `TransitionFn`, the
#: dispatcher does not call `ChangeBlockState` itself for these outcomes -- it
#: only sets the interrupt reason and wakes the block's own suspended process,
#: which decides what happens next (`block_lifecycle.block_run_process`'s
#: `"OBPM"`/`"Induced_Failure"` handling), matching Raptor 7
#: having no `ChangeBlockState` alongside their `SetInterruptReason`+
#: `Interrupt`.
#:
#: Returns whether the interrupt was actually delivered (`Scheduler.interrupt`
#: genuinely called). A block not currently `fail_wait`/`zero_wait` (the
#: source's `blockHasStarted`-gated `IF FailWait ... ELSIF ZeroWait ... END
#: IF`, no `ELSE`) receives no wake-up at all, and `returning_from_maintenance` must not
#: be set in that case either: setting it unconditionally poisons any later
#: re-check of the same block even though the induced action was never applied.
InduceFn = Callable[[RBDBlock, str], bool]

#: Signature for the caller-supplied mixed-percentage action draw --
#: `(dep_block, sim_time) -> action`, bound to `dep_block`'s OWN fail
#: stream by the caller (matching Raptor 7's `GetStream(
#: tempBlock.failStream,...)` -- the DEPENDENT block's stream, not the
#: depended-upon thing's). See `draw_dependency_action`.
DrawActionFn = Callable[[RBDBlock, float], str]


class FevDependencyHooks(Protocol):
    """What Failure Effects View substitutes for a `Down` dependency
    dispatch (`engine/fev.py`): no random draw, a worst-case reaction, and
    the hatching bookkeeping. `None` everywhere in an ordinary run."""

    def down(self, dep_block: RBDBlock, sim_time: float) -> None: ...


def draw_dependency_action(block: RBDBlock, rng: RandomSource, sim_time: float) -> str:
    """Port of `BackFlowDependencies`'s mixed-percentage action draw.
   Returns one of `"GoIdle"`/`"DoNothing"`/
    `"GoToPM"`/`"InducedFailure"`.

    Each of the four `Dep*Perc` fields at exactly `100.0` short-circuits to
    that outcome deterministically, with no draw; otherwise this draws a fresh
    `UniformReal(0,1)*100` from the block's own fail stream and buckets it
    against the cumulative percentages in the source's order
    (`DepNothingPerc` -> `DepIdlePerc` -> `DepPMPerc` -> else
    `InducedFailure`).

    `TzeroAction` (`RBDBlock.
    time_zero_action`): at `SimTime=0.0` specifically, the first draw is cached on
    the block and reused for every later `Down` trigger during the same run
    rather than drawing fresh each time, which would otherwise desync the RNG
    stream. `run_control.py` resets the field to `""` each run via its
    dataclass default.
    """
    if block.dep_idle_percent == 100.0:
        return "GoIdle"
    if block.dep_nothing_percent == 100.0:
        return "DoNothing"
    if block.dep_pm_percent == 100.0:
        return "GoToPM"
    if block.dep_fail_percent == 100.0:
        return "InducedFailure"
    if sim_time == 0.0 and block.run_state.time_zero_action:
        return block.run_state.time_zero_action

    draw = rng.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0]) * 100.0
    if draw < block.dep_nothing_percent:
        action = "DoNothing"
    elif draw < block.dep_nothing_percent + block.dep_idle_percent:
        action = "GoIdle"
    elif draw < block.dep_nothing_percent + block.dep_idle_percent + block.dep_pm_percent:
        action = "GoToPM"
    else:
        action = "InducedFailure"

    if sim_time == 0.0:
        block.run_state.time_zero_action = action
    return action


def fail_mean(block: RBDBlock) -> float:
    """The block's own fail-distribution mean -- a thin wrapper over
    `distro_mean` (the distribution-generic port). `GetStartCond`'s own
    `repairMean`/`preLDT`/`postLDT` branch arithmetic (types 1/4/5) reaches the same formulas for the repair and LDT
    distributions, since the source's `SetStats` writes both `failMean` and
    `repairMean` from one shared `CASE distroInUse`."""
    return distro_mean(block.fail_distro, block.fail_values)


def distro_mean(d: int, p: list[float]) -> float:
    """Port of `SetStats`'s mean formula
    (the `CASE distroInUse` block, `i=1` arm).

    Returns the statistical mean for each supported distribution.
    Weibull shape<0.05, Pearson5 shape<=1.0, and Pearson6 shape<=1.0
    return the literal `12345.6789` "Murphy identifier" sentinel source
    substitutes there -- the mean is undefined for those shapes, but
    Raptor 7 doesn't raise or skip the stat, it just stores that
    magic value (also matched by  to special-case
    rendering it). It does not affect the draw itself, which has no shape
    guard either.
    """
    if d == int(Distribution.BETA):
        return p[0] / (p[0] + p[1]) if (p[0] + p[1]) > 0 else 0.0
    if d == int(Distribution.CHI_SQUARE):
        return p[0]
    if d == int(Distribution.BINOMIAL):
        return p[0] * p[1]
    if d == int(Distribution.EXPONENTIAL):
        return p[0] + p[1]
    if d == int(Distribution.ERLANG):
        return p[0] * p[1]
    if d == int(Distribution.GAMMA):
        return p[0] * p[1] + p[2]
    if d == int(Distribution.LOGNORMAL):
        return p[0]
    if d == int(Distribution.NORMAL):
        return p[0]
    if d == int(Distribution.UNIFORM_INTEGER):
        return (p[0] + p[1]) / 2.0
    if d == int(Distribution.UNIFORM_REAL):
        return (p[0] + p[1]) / 2.0
    if d == int(Distribution.PEARSON5):
        # Port of SetStats's WHEN 11: shape <= 1
        # makes the mean formula's own (shape-1) denominator blow up. The
        # source does not raise or skip the stat; it substitutes the literal
        # out-of-range Pearson 5 sentinel 12345.6789 (also used by
        # Raptor 7 to special-case rendering it). The draw side has
        # no shape guard either.
        if p[0] <= 1.0:
            return 12345.6789
        return p[1] / (p[0] - 1.0) + p[2]
    if d == int(Distribution.PEARSON6):
        # Port of SetStats's WHEN 12 -- same
        # Murphy-sentinel shape as Pearson5 above.
        if p[1] <= 1.0:
            return 12345.6789
        return (p[2] * p[0]) / (p[1] - 1.0)
    if d == int(Distribution.POISSON):
        return p[0]
    if d == int(Distribution.TRIANGULAR):
        return (p[0] + p[1] + p[2]) / 3.0
    if d == int(Distribution.WEIBULL):
        # Port of SetStats's WHEN 15 -- same
        # Murphy-sentinel shape as Pearson5/6 above.
        if p[0] < 0.05:
            return 12345.6789
        if p[0] == 1.0 or p[0] > 10_000_000.0:
            return p[1] + p[2]
        return p[1] * gamma_function(1.0 + 1.0 / p[0]) + p[2]
    if d == int(Distribution.FIXED):
        return p[0]
    if d == int(Distribution.NONE):
        # Port of SetStats WHEN 18: source
        # stores the 12345.6788 "Murphy no-mean" sentinel, NOT 0.0.
        return 12345.6788
    if d == int(Distribution.EMPIRICAL):
        # Port of SetStats WHEN 16: the mean is
        # the arithmetic mean of the empirical VALUES (`failVals`), which
        # are this port's `p`.
        return (sum(p) / len(p)) if p else 0.0
    if d == int(Distribution.BERNOULLI):
        # Port of SetStats WHEN 17 (Raptor 7, "Point
        # Estimate"): source stores the 12345.6788 Murphy no-mean
        # sentinel, NOT p[0].
        return 12345.6788
    if d == int(Distribution.EXPONENTIAL_POWER):
        # Port of SetStats WHEN 20: the mean is
        # the LOCATION (`valsInUse[3]` = p[2]).
        return p[2]
    if d == int(Distribution.EXTREME_VALUE):
        # Port of SetStats WHEN 21: scale is
        # var[1] (p[0]), location is var[2] (p[1]);
        # mean = location + 0.57721*scale.
        return p[1] + 0.57721 * p[0]
    if d == int(Distribution.LAPLACE):
        # Port of SetStats WHEN 22: scale is
        # var[1] (p[0]), location is var[2] (p[1]); mean = location.
        return p[1]
    raise NotImplementedError(f"distro mean formula not ported for distro={d}")



def back_flow_dependency_trigger(
    run_change: int, fail_change: int, idle_change: int, standby_change: int, pm_change: int,
    *, has_dep_objects: bool = True,
) -> DependencyTrigger | None:
    """Port of `ReconfigureBlock`'s `IF hasDepObjects ...` dispatch guard.
   Returns `None` if this transition
    shouldn't trigger `BackFlowDependencies` at all.

    `has_dep_objects` (default `True` for callers that do not pass it): the
    source's `ReconfigureBlock` wraps this whole dispatch in `IF hasDepObjects`,
    so a block nobody depends on (e.g. one switched to `Standby` by
    cold-standby redistribution) never reaches the real call. Returning `None`
    for `standby_change == 1` when it is false preserves that gate.
    """
    if fail_change == 1 or pm_change == 1:
        return "Down"
    if standby_change == 1:
        # NodeStandby: a block going to Standby (cold-standby switch-off)
        # triggers BackFlowDependencies(NodeStandby) on its dependents.
        # The dispatch is a NO-OP for each dependent: a Standby block is
        # still counted for the node's k-out-of-n availability, so no
        # dependent state changes. The dependents just see the block as
        # present. Port of Raptor 7's NodeStandby branch.
        return "NodeStandby" if has_dep_objects else None
    if run_change == 1 and (fail_change == -1 or standby_change == -1 or pm_change == -1):
        return "AllUp"
    if idle_change == 1 and (fail_change == -1 or standby_change == -1 or pm_change == 1):
        return "AllUp"
    return None


def _dispatch_dependent_block(
    dep_block: RBDBlock,
    change_type: DependencyTrigger,
    transition_dep: TransitionFn,
    sim_time: float,
    *,
    special_back_flow: bool,
    draw_action: DrawActionFn | None,
    induce: InduceFn | None,
    origin: str,
    fev: FevDependencyHooks | None = None,
) -> None:
    """`BackFlowDependencies`'s per-dependent dispatch -- ONE copy (review E-05).

    This body was written out twice in this module, once for the
    block/event-sourced view (`back_flow_dependencies`) and once for the
    node-sourced one (`back_flow_dependencies_from_node`). §2.4 records why
    the source does not have two: `BackFlowDependencies(IN changeType)` is a
    single routine taking a discriminator, and the two Python copies differed
    only in the *membership guard* that got a block into the loop in the first
    place. Parameterising on that guard is exactly the source's shape, so that
    is what this does -- `origin` is the only remaining per-caller difference,
    and it exists solely to word an error message.

    A mechanical diff of the two bodies found **every executable line
    identical**, with three exceptions, all recorded here rather than papered
    over:

    1. The node copy carried a **second, unreachable** `special_back_flow`
       block. The first one (identical) already handled the case and
       `continue`d, so the second could never run -- orphaned when a
       `returning_from_maintenance` arm was inserted above it. It is gone.
    2. The `draw_action is None` error message differed. `origin` supplies the
       distinguishing words.
    3. **Only the node copy had a trailing `else: raise
       EngineInvariantError(action)`** for an unrecognised action; the block
       copy fell through silently. The guard is kept, so the block path now
       raises on an impossible action instead of quietly doing nothing.

    Everything else that differed was comment text. The union is preserved.
    """
    if change_type == "NodeStandby":
        # `BackFlowDependencies(NodeStandby)`: a
        # dependent Running/Idle block follows the switch-off block into
        # `Standby` (`transition` = `_dispatch_dependency`, which applies the
        # state change and sets the `Dependence_Standby` reason + wake).
        # Reached from a NODE entering NodeStandby after a Down/NodePM prior
        # (`ChangeNodeState`).
        if dep_block.op_status not in (BlockStatus.RUNNING, BlockStatus.IDLE):
            return
        transition_dep(dep_block, BlockStatus.STANDBY, "Dependence", sim_time)
        return
    if change_type == "Down":
        if dep_block.op_status not in (BlockStatus.RUNNING, BlockStatus.STANDBY):
            return

        if fev is not None:
            # `IF (NOT FEVmode) ... ELSE {in FEVmode}` (Raptor 7,
            # Raptor 7): Failure Effects View takes
            # no draw and ignores `returnFromMaint`/`specialBackFlow`.
            fev.down(dep_block, sim_time)
            return

        if special_back_flow:
            # `IF specialBackFlow`, reached via
            # `resend_back_flow`. Does NOT re-draw or recompute anything --
            # just replays whichever action was already decided
            # (`previous_dependency_state`) the last time this SAME block was dispatched,
            # unless it's still mid-repair-completion (`returning_from_maintenance`).
            # The source's trailing (implicit) ELSE covers both
            # `returning_from_maintenance` AND any `previous_dependency_state` other than
            # `"GoIdle"`/`""` (`"DoNothing"`/`"GoToPM"`/`"InducedFailure"`
            # were already fully applied the first time).
            if dep_block.run_state.returning_from_maintenance:
                return
            if dep_block.run_state.previous_dependency_state == "GoIdle":
                transition_dep(dep_block, BlockStatus.IDLE, "Dependence", sim_time)
            elif dep_block.run_state.previous_dependency_state == "" and dep_block.def_dep_state_idle:
                transition_dep(dep_block, BlockStatus.IDLE, "Dependence", sim_time)
            return

        if dep_block.run_state.returning_from_maintenance:
            # The source's trailing ELSE (Raptor 7,
            # `returnFromMaint=TRUE`) -- guarded on the WHOLE branch by
            # `tempBlock.InterruptReason=""`: if a PRIOR dispatch's own
            # interrupt (e.g. the `"Induced_Failure"`/`"OBPM"` one that set
            # `returning_from_maintenance`) hasn't actually been delivered/consumed by
            # the target block's own process yet, the source does nothing at
            # all -- no transition, no clearing, not even a log line.
            # (ReleaseProbe32 run 3 t=190.345389: a pending
            # "Obtained_PM_Prereqs" wake shields SolarP_500W.)
            #
            # Only changes state -- NO `Interrupt(Run)`: the block's fail-wait
            # keeps running (and consuming life) while it is logged Idle,
            # until its next real wake (PhasedFailSimOrderProbe run 1:
            # SolarP_500W's 9.881132-long `Idle change_block_state` stretch
            # from t=1791.939786, a unique subset sum from the /sd capture).
            if dep_block.run_state.interrupt_reason != "":
                return
            dep_block.run_state.no_wake_once = True
            if dep_block.dep_idle_percent == 100.0 or dep_block.def_dep_state_idle:
                transition_dep(dep_block, BlockStatus.IDLE, "", sim_time)
            else:
                transition_dep(dep_block, BlockStatus.RUNNING, "", sim_time)
            dep_block.run_state.no_wake_once = False
            dep_block.run_state.returning_from_maintenance = False
            return

        if dep_block.dep_idle_percent == 100.0:
            action = "GoIdle"
        elif draw_action is not None:
            action = draw_action(dep_block, sim_time)
        else:
            raise EngineInvariantError(
                f"{origin} needs a draw_action callback for "
                f"{dep_block.name!r}'s non-100% percentages"
            )
        dep_block.run_state.previous_dependency_state = action
        if action == "GoIdle":
            transition_dep(dep_block, BlockStatus.IDLE, "Dependence", sim_time)
        elif action == "DoNothing":
            pass
        elif action == "GoToPM":
            # Raptor 7's guard around
            # `SetInterruptReason("OBPM")`: `usesPM AND NOT(pmMisDefer AND
            # missionStatus=Mission AND activePhases>0)`. Phasing is out of
            # scope, so `activePhases` is always 0 and the inner NOT()
            # collapses, leaving just the `usesPM` check. A block that does
            # not use PM silently consumes the draw.
            if induce is None:
                raise EngineInvariantError(f"{action!r} drawn but no induce callback was provided")
            if dep_block.uses_pm:
                induce(dep_block, InterruptReason.OBPM)
        elif action == "InducedFailure":
            if induce is None:
                raise EngineInvariantError(f"{action!r} drawn but no induce callback was provided")
            # Raptor 7's `SetreturnFromMaint(TRUE)`, gated on
            # `blockHasStarted` in the source (the `IF FailWait ... ELSIF
            # ZeroWait ... END IF` gate `induce`'s return value reports, see
            # `InduceFn`). Gating matters at kickoff: `op_status=RUNNING` is
            # set by the orchestrator-level Initialize pass before
            # `block_run_process`'s generator has started, so setting
            # `returning_from_maintenance` there would poison every later re-check of
            # the same block even though the induced action was never
            # delivered.
            if induce(dep_block, InterruptReason.INDUCED_FAILURE):
                dep_block.run_state.returning_from_maintenance = True
        else:
            raise EngineInvariantError(action)  # pragma: no cover

    elif change_type == "AllUp":
        # Raptor 7's member loop: both `Idle` and `Standby`
        # dependents get the direct `ChangeBlockState(Running, activeStatus,
        # "Dependence")`. A Standby dependent promoted this way logs "Good
        # DependenceStress=...", and through its own node reconfiguration can
        # re-mark its cold-standby node for the next CheckColds -- which
        # typically demotes it right back via the survey's Too_many_on branch.
        if dep_block.op_status not in (BlockStatus.IDLE, BlockStatus.STANDBY):
            return
        transition_dep(dep_block, BlockStatus.RUNNING, "Dependence", sim_time)


def back_flow_dependencies(
    change_type: DependencyTrigger,
    depended_block: RBDBlock | RBDEvent,
    all_blocks: list[RBDBlock],
    transition: TransitionFn,
    sim_time: float,
    *,
    special_back_flow: bool = False,
    draw_action: DrawActionFn | None = None,
    induce: InduceFn | None = None,
    fev: FevDependencyHooks | None = None,
    dependents: Sequence[RBDBlock] | None = None,
) -> None:
    """Port of `BackFlowDependencies`'s `RBDBlock`-dependency branches
    (for `Down` and for `AllUp`).

    For each block in `all_blocks` whose `dependency_num`/`dep_type`
    point at `depended_block` (and isn't `ignore_dep`), applies the
    "Dependence"-triggered transition via the caller-supplied
    `transition` callback -- in `all_blocks` order, matching the
    source's `FOREACH tempBlock IN BlockDepGroup` (group-membership
    order isn't modeled; `all_blocks` order is the best available stand-
    in, not exercised by a fixture with more than one dependent block).

    `dependents` is an optional precomputed subset of *exactly* the
    blocks that depend on `depended_block`, in `all_blocks` order. Raptor
    7's `ReconfigureBlock` only calls `BackFlowDependencies` when the
    depended-upon object's `hasDepObjects` flag is set; passing the
    indexed subset lets the port apply the same gate without scanning
    every block on every transition. When omitted, the historical full
    scan is kept for callers that have no index.

    `depended_block` is only ever read for its `.id` and its own Python type
    here, so an `RBDEvent` works identically to an `RBDBlock` (ER3-08): a
    block CAN depend on an event in the real model (`dep_type="RBDEvent"`),
    dispatched here exactly like `dep_type="RBDBlock"`. The type check
    matters because block/node/event ids are numbered independently in a
    real `.rbd` (a `Block` and an `Event` can share the same numeric id --
    see `diagram.py`'s K-of-N badge fix for a concrete case), so a
    `dep_block.dep_type="RBDBlock"` whose `dependency_num` happens to equal
    some unrelated EVENT's id must NOT fire when that event changes, and
    vice versa. `orchestrator.py`'s `_Cascade.apply_event` already calls
    this function on every event state change.

    `draw_action`/`induce`: same mixed-percentage Down machinery as
    `back_flow_dependencies_from_node`; the two callers run the same source
    statements.
    """
    #: `depended_block`'s own kind, as a `dep_type` string -- block/node/
    #: event ids are independent numbering spaces, so a dependent's
    #: `dep_type` must match WHAT actually changed, not just its id.
    _depended_kind = "RBDEvent" if isinstance(depended_block, RBDEvent) else "RBDBlock"
    candidates = all_blocks if dependents is None else dependents
    for dep_block in candidates:
        if (dep_block.dep_type != _depended_kind
                or dep_block.dependency_num != depended_block.id):
            continue
        if dep_block.ignore_dep:
            continue

        _dispatch_dependent_block(
            dep_block, change_type, transition, sim_time,
            special_back_flow=special_back_flow, draw_action=draw_action,
            induce=induce, origin="BackFlowDependencies(Down)", fev=fev,
        )


_NODE_DOWN_LIKE = {NodeStatus.DOWN, NodeStatus.PM}
_NODE_RECOVERY_LIKE = {NodeStatus.ALL_UP, NodeStatus.DEGRADED, NodeStatus.IDLE}


def node_dependency_trigger(prior_status: NodeStatus, new_status: NodeStatus,
                            higher_cold: bool = False) -> DependencyTrigger | None:
    """Port of `ChangeNodeState`'s OWN `BackFlowDependencies` dispatch
    guard -- a DIFFERENT classification from
    `system_status.end_node_system_up_transition`'s `_UP_LIKE_NODE`/
    `_DOWN_LIKE_NODE` sets (that one tracks the SYSTEM's own Up/Down for
    Green/Yellow/Red purposes; this one decides when a NODE's status
    change should propagate to whatever depends on it). They disagree on
    `NodeIdle`: `end_node_system_up_transition` treats it as down-like,
    but the real source's own dependency dispatch groups `NodeIdle` with
    `AllUp`/`Degraded` as a RECOVERY trigger (`ELSIF (Status=AllUp) OR
    (Status=Degraded) OR (Status=NodeIdle): BackFlowDependencies(AllUp)`)
    -- different concerns, so this does not reuse one set for both. From a
    Down/NodePM prior, a `NodeStandby` new status or a `higher_cold` flag
    returns `"NodeStandby"`, matching the source's own priority there.
    """
    if prior_status not in _NODE_DOWN_LIKE and new_status in _NODE_DOWN_LIKE:
        return "Down"
    if prior_status in _NODE_DOWN_LIKE:
        # `IF ((Status=NodeStandby) OR (higherCold)): BackFlowDependencies(NodeStandby)
        # ELSIF AllUp/Degraded/NodeIdle: BackFlowDependencies(AllUp)`.
        # `higherCold` is the CHANGING node's own flag. Only reached from a Down/NodePM prior.
        if new_status == NodeStatus.STANDBY or higher_cold:
            return "NodeStandby"
        if new_status in _NODE_RECOVERY_LIKE:
            return "AllUp"
    return None


def resolve_dependency_node_id(
    block: RBDBasic, end_node_id: int | None, own_node_id: int | None,
) -> int | None:
    """Port of `Initialize`'s `CASE DependencyNum` resolution,
    which sets `simDependencyNum`
    ONCE per block to whichever node its dependency actually targets:
    `sysDepend` (`DependencyNum=-2`) -> the model's single End node;
    `locDepend` (`DependencyNum=-1`) -> the block's OWN EFPA node
    (`own_node_id`); a positive `DependencyNum` combined with `depType=
    "RBDNode"` -> that node id directly (already a real node id, no
    translation needed). Returns `None` if `block` isn't dependent on a
    node at all (`dep_type=="RBDBlock"`, or no dependency configured) --
    `back_flow_dependencies_from_node`/`repair_completion_status` treat
    that as "doesn't match, skip."
    """
    if block.dependency_num == -2:
        return end_node_id
    if block.dependency_num == -1:
        return own_node_id
    if block.dependency_num > 0 and block.dep_type in ("RBDNode", "RBDHier"):
        return block.dependency_num
    return None


@dataclass(frozen=True)
class BackFlowDeps:
    """Everything a node-sourced back-flow needs, bound once (review E-09).

    `back_flow_dependencies_from_node` took a ten-parameter bundle, and
    **seven** call sites — not the two the review recorded — assembled it
    by hand from the same orchestrator state. Four of those sites spelled
    out the identical eight expressions (`end_node.id if end_node else -1`,
    `_all_blocks`, `links_by_id`, `_dispatch_dependency`, `_draw_dependency_action`,
    `_induce_dependency`, `nodes_by_id`, and a `run_cold_logic_now` closure
    over `sim_time`).

    The review's "ten keyword arguments" conflated two calls: `hiers_by_id`,
    `interrupt_link`, `backflow_all_up`, `backflow_down` and `emit_log` are
    arguments to `run_cold_logic`, not to this function. That call site does
    still hand-roll a long bundle, and `cascade_cold.py` is the only place
    it happens — so it is deliberately left as it is, and noted in the review
    rather than consolidated on the strength of a miscount.

    What this bundle removes is the part that genuinely had to agree: the
    set of orchestrator callbacks, and the `-1` sentinel for an absent end
    node. `resend_back_flow` was reaching into `self.cascade` to get several
    of these by hand, so a renamed attribute would have broken it in a place
    no test named.

    `sim_time` is **not** in the bundle, because it changes on every call
    and the `run_cold_logic_now` each site had to hand-build was a closure
    over it (`lambda n: cascade.apply_cold_logic(n, sim_time)`) — the same
    closure written out seven times. What the bundle stores instead is the
    raw two-argument `apply_cold_logic`, and
    `BackFlowDeps.cold_logic_now(sim_time)` builds the closure. So the
    bundle is built **once** per run and reused across every back-flow,
    rather than rebuilt per call.
    """

    end_node_id: int
    all_blocks: list[RBDBlock]
    links_by_id: dict[int, Link]
    transition: TransitionFn
    nodes_by_id: dict[int, RBDNode] | None = None
    draw_action: DrawActionFn | None = None
    induce: InduceFn | None = None
    special_back_flow: bool = False
    #: `cascade.apply_cold_logic(node, sim_time)` — the *unbound* method.
    #: Deliberately stored with both parameters so it is not a closure over
    #: an instant the bundle does not know.
    apply_cold_logic: Callable[[RBDNode, float], None] | None = None
    #: Failure Effects View's own `Down` rule (`engine/fev.py`); None in a run.
    fev: FevDependencyHooks | None = None

    def cold_logic_now(
        self, sim_time: float,
    ) -> Callable[[RBDNode], None] | None:
        """The one-argument `run_cold_logic_now` this instant needs.

        `None` when the bundle has no `apply_cold_logic`, which is the
        documented "no cold dependents" no-op rather than a silent skip.
        """
        apply_cold_logic = self.apply_cold_logic
        if apply_cold_logic is None:
            return None
        # `apply_cold_logic(node, sim_time)` takes sim_time POSITIONALLY, so
        # this has to be a lambda rather than a partial with a keyword.
        #
        # Bound to a local first: mypy does not carry an attribute narrowing
        # (`self.apply_cold_logic is None`) into a lambda body, because the
        # attribute could change between the check and the call. Reading it
        # once makes the narrowing hold AND is the honest expression of the
        # intent -- the closure captures this instant's function, not
        # "whatever the attribute says later".
        return lambda node: apply_cold_logic(node, sim_time)


def back_flow_dependencies_from_node(
    change_type: DependencyTrigger,
    node: RBDNode,
    deps: BackFlowDeps,
    sim_time: float,
) -> None:
    """Port of `ChangeNodeState`'s own `BackFlowDependencies` call
    (the node's own method of that name, distinct
    from `RBDBlockObj.BackFlowDependencies` above). It is unconditional on
    node type -- the block sits after the `typeNode=3`-specific `SystemUp`
    handling closes, not nested inside it -- so `node` can be any node whose
    status just changed. Every dependent block whose resolved target matches
    `node.id` is dispatched, whether that target came from `sysDepend`,
    `locDepend` or a positive `DependencyNum` + `RBDNode`.

    `draw_action`/`induce`: same mixed-percentage machinery as
    `back_flow_dependencies`, passed through identically. `links_by_id` is
    only needed to resolve a dependent block's own `own_node_id` for
    `resolve_dependency_node_id`'s `locDepend` case; each dependent block in
    `all_blocks` may have a different own node than `node` itself.

    `all_blocks` order stands in for `NodeDepGroup`'s membership order, same
    caveat as `back_flow_dependencies`.
    """
    # Unpacked once so the body below is unchanged from the ten-parameter
    # version -- the diff of this refactor is the signature, not the logic.
    end_node_id = deps.end_node_id
    all_blocks = deps.all_blocks
    links_by_id = deps.links_by_id
    transition = deps.transition
    draw_action = deps.draw_action
    induce = deps.induce
    special_back_flow = deps.special_back_flow
    # `run_cold_logic_now` is NOT unpacked here: the block loop below never
    # calls it -- only the node-dependents half does, and that derives its own
    # from `deps`. Unpacking it here would have been the mirror image of the
    # duplication this replaced.

    for dep_block in all_blocks:
        if dep_block.ignore_dep:
            continue
        # `own_node_id` is only ever consulted for `locDepend`
        # (`DependencyNum=-1`) -- a block with no real `downstream_link_id`/no
        # `links_by_id` entry (sysDepend/positive-DepNum/no dependency)
        # never reaches that branch inside `resolve_dependency_node_id`,
        # so resolve it tolerantly rather than requiring every block to
        # have a valid link (same reasoning as `resend_back_flow_would_
        # apply`'s own `_resolved_target` helper).
        link = links_by_id.get(dep_block.downstream_link_id)
        own_node_id = link.connect_to_id if link is not None else None
        target_id = resolve_dependency_node_id(dep_block, end_node_id, own_node_id)
        if target_id != node.id:
            continue

        _dispatch_dependent_block(
            dep_block, change_type, transition, sim_time,
            special_back_flow=special_back_flow, draw_action=draw_action,
            induce=induce,
            origin="node-triggered BackFlowDependencies(Down)",
            fev=deps.fev,
        )

    _back_flow_node_dependents(change_type, node.id, deps, sim_time)


def _back_flow_node_dependents(
    change_type: DependencyTrigger,
    source_id: int,
    deps: BackFlowDeps,
    sim_time: float,
) -> None:
    """Port of `BackFlowDependencies`'s own `NodeDepGroup` FOREACH loops
    -- the node-sourced half of that method. `BackFlowDependencies` is a
    method on `RBDBasicObj`,
   the shared base of blocks, nodes and events,
    so ONE source method backs both `back_flow_dependencies` (the
    block-sourced view) and `back_flow_dependencies_from_node` (the
    node-sourced one); each has a `BlockDepGroup` loop (ported) AND a
    `NodeDepGroup` loop (this). The three branches:

      Down cold dependent -> SetHigherCold(TRUE),
                                then recurse BackFlowDependencies(Down)
      AllUp SetHigherCold(FALSE) if set, recurse
                                (AllUp), then RunColdLogic if cold
      NodeStandby cold dependent -> SetHigherCold(TRUE),
                                then recurse (NodeStandby)

    all under the same outer gate `(tempNode.Status<>Down) AND (NOT
    tempNode.IgnoreDep)` plus the membership test `tempNode.simDependencyNum=Id`
    (resolved per-node via `resolve_dependency_node_id`, exactly as the block
    loops do, rather than materialising the source's `NodeDepGroup`).

    This is what `higherCold` is for: a cold-standby node that is itself a
    dependent. When its dependency target goes Down the flag goes true, which
    flips `RunColdLogic` to its `ELSE ReconfigureNode(0,0,0,0,0,0)` arm
    (ordinary K-of-N aggregation, the `"N-K/M..."` line format) and suppresses
    all switching -- no `Too_many_on` demotion, no `SetSwitchTime` draw --
    until the target recovers.

    `run_cold_logic_now` is the AllUp branch's own `ASK tempNode TO
    RunColdLogic`, orchestrator-owned (it alone can drive a full cold survey
    plus its cascade). Omitting it (the default) makes that call a no-op,
    correct for callers with no cold dependents.
    """
    nodes_by_id = deps.nodes_by_id
    end_node_id = deps.end_node_id
    run_cold_logic_now = deps.cold_logic_now(sim_time)
    if nodes_by_id is None:
        return
    for dep_node in nodes_by_id.values():
        # Source's own outer gate, both loops: `IF ((tempNode.Status<>Down)
        # AND (NOT tempNode.IgnoreDep))`.
        if dep_node.status == NodeStatus.DOWN or dep_node.ignore_dep:
            continue
        if dep_node.id == source_id:
            continue  # a node is never its own dependent
        if resolve_dependency_node_id(dep_node, end_node_id, None) != source_id:
            continue

        if change_type == "Down":
            if dep_node.cold_standby:
                dep_node.higher_cold = True
            _recurse_node_backflow("Down", dep_node, deps, sim_time)
        elif change_type == "AllUp":
            if dep_node.higher_cold:
                dep_node.higher_cold = False
            _recurse_node_backflow("AllUp", dep_node, deps, sim_time)
            if dep_node.cold_standby and run_cold_logic_now is not None:
                run_cold_logic_now(dep_node)
        elif change_type == "NodeStandby":
            if dep_node.cold_standby:
                dep_node.higher_cold = True
            _recurse_node_backflow("NodeStandby", dep_node, deps, sim_time)


def _recurse_node_backflow(
    change_type: DependencyTrigger, dep_node: RBDNode, deps: BackFlowDeps,
    sim_time: float,
) -> None:
    """`ASK tempNode TO BackFlowDependencies(<changeType>)` -- source
    recurses into the dependent node's OWN copy of the same
    `RBDBasicObj` method, so whatever depends on IT is dispatched too.

    A pure forwarder: it exists to name the source's recursion in one place
    and adds no logic. Before `BackFlowDeps` it re-listed ten parameters and
    unpacked all ten -- the same bundle the seven external call sites were
    assembling by hand, so there were eight places to forget one, not two."""
    back_flow_dependencies_from_node(change_type, dep_node, deps, sim_time)


_NODE_UP_LIKE = {NodeStatus.ALL_UP, NodeStatus.DEGRADED}


_NODE_DOWN_OR_PM = {NodeStatus.DOWN, NodeStatus.PM}


def repair_completion_status(
    block: RBDBlock, all_blocks: list[RBDBlock], dep_node: RBDNode | None = None,
    own_node: RBDNode | None = None, *, all_events: list[RBDEvent] | None = None,
) -> tuple[BlockStatus, bool]:
    """Port of `DetermineNewState`'s `"Maintenance Completed"` handler,
    narrowed to the `DepIdlePerc=100.0` case
    (same scope as `back_flow_dependencies`), covering its three branches:
    `depType="RBDBlock"`, `depType="RBDEvent"`
    (ER3-08 -- same shape as the RBDBlock branch, keyed on the event's own
    `activeStatus`/`opStatus` instead; `all_events` resolves it) and
    `(depType="RBDNode") OR (DependencyNum<0)`'s `ELSE
    {block is active and depends on a node}` sub-branch.
    The `Cut`/`Linked` sub-branch is ported too, unit-tested only: no captured
    fixture has a Cut/Linked block finish a repair (`ER3-09`). This covers all three
    node-dependency kinds `resolve_dependency_node_id` does (`sysDepend`,
    `locDepend`, positive `DependencyNum` + `depType="RBDNode"`); the
    caller resolves `dep_node` the same way.

    This is a different mechanism from `back_flow_dependencies`/`back_flow_
    dependencies_from_node`, even though both produce Idle/Running in our
    scope: this one runs on the dependent block's own repair-completion path
    (no `BackFlowDependencies` call, no `Interrupt`) and asks whether the
    thing it depends on is currently up at the moment it finishes repairing,
    rather than reacting to an external Down/AllUp signal. A block that
    finishes repair while what it depends on is still down goes straight to
    Idle with an empty comment (`ChangeBlockState(Idle, activeStatus, "")`,
    so `change_block_state`'s `"change_block_state"` fallback ends up in the
    log).

    Returns `(status, needs_recheck)`; for the ordinary cases `needs_recheck`
    is always false (apply `status` and stop). The node/`sysDepend` branch has
    a `GetDownNum` special case (`nodes.py`'s
    `path_fail_value`/`path_pm_value` are the per-path counters it reads). When
    the depended-upon node is down, it checks whether this block's own
    incoming path into its own direct node (`own_node`, not `dep_node`, which
    may be several hops further downstream) is the only thing keeping that
    node down (`down_num <= 1`). If so, the source unconditionally transitions
    to Running first -- which triggers this block's own full node/dependency
    cascade synchronously, so `dep_node.status` may change as a result -- then
    immediately re-checks `dep_node.status`: if it is still Down or NodePM and
    `defDepStateIdle` is set, a second transition to Idle follows at the same
    instant.

    That is why `needs_recheck=True` is returned instead of a precomputed final
    answer: the caller must apply `status` (Running) first, run its cascade
    (which may update `dep_node.status` in place), and only then call
    `needs_repair_idle_recheck` to decide on the follow-up Idle.

    Raises for a `STANDBY` target (Standby propagation not ported), for
    `dep_idle_percent != 100.0` (mixed-percentage branches not ported, matching
    `back_flow_dependencies`), or if `defDepStateIdle` is false when the nested
    re-check would otherwise apply. Returns `(RUNNING, False)` unconditionally
    if `block` is not dependent on anything this function covers.
    """
    if block.dep_type == "RBDBlock":
        # Port of Raptor 7 -- keyed on the DEP BLOCK's own
        # activeStatus first (Linked / Cut / else), then its opStatus.
        dep_block = next(b for b in all_blocks if b.id == block.dependency_num)
        if dep_block.active_status == ActiveStatus.LINKED:
            return BlockStatus.RUNNING, False
        if dep_block.active_status == ActiveStatus.CUT:
            # DepIdlePerc=100 -> repaired normally to find dep down;
            # else repaired from an induced maintenance.
            if block.dep_idle_percent == 100.0:
                return BlockStatus.IDLE, False
            return (BlockStatus.IDLE, False) if block.def_dep_state_idle else (BlockStatus.RUNNING, False)
        # depBlock is not Linked/Cut (Active or Standby active-status):
        if dep_block.op_status in (BlockStatus.RUNNING, BlockStatus.IDLE):
            return BlockStatus.RUNNING, False
        if dep_block.op_status == BlockStatus.STANDBY:
            # -- completing block follows into Standby.
            return BlockStatus.STANDBY, False
        # RepHold/Repairing/Failure/PM/PMhold/Done: dep down-like.
        if block.dep_idle_percent == 100.0:
            return BlockStatus.IDLE, False
        return (BlockStatus.IDLE, False) if block.def_dep_state_idle else (BlockStatus.RUNNING, False)

    if block.dep_type == "RBDEvent":
        # Port of Raptor 7 (ER3-08) -- same shape as the
        # RBDBlock branch above, keyed on the depended EVENT's own
        # activeStatus first (Linked / Cut / else), then its opStatus.
        # Events have no Standby state, so the "else" (Active) case only
        # splits Armed/Success (up) vs Failure (down-like).
        dep_event = next(e for e in (all_events or []) if e.id == block.dependency_num)
        if dep_event.active_status == ActiveStatus.LINKED:
            return BlockStatus.RUNNING, False
        if dep_event.active_status == ActiveStatus.CUT:
            if block.dep_idle_percent == 100.0:
                return BlockStatus.IDLE, False
            return (BlockStatus.IDLE, False) if block.def_dep_state_idle else (BlockStatus.RUNNING, False)
        # depEvent is not Linked/Cut (Active active-status):
        if dep_event.op_status in (EventStatus.ARMED, EventStatus.SUCCESS):
            return BlockStatus.RUNNING, False
        # opStatus=Failure: down-like.
        if block.dep_idle_percent == 100.0:
            return BlockStatus.IDLE, False
        return (BlockStatus.IDLE, False) if block.def_dep_state_idle else (BlockStatus.RUNNING, False)

    is_node_dependent = block.dependency_num in (-1, -2) or (
        block.dependency_num > 0 and block.dep_type in ("RBDNode", "RBDHier"))
    if not is_node_dependent:
        return BlockStatus.RUNNING, False

    if dep_node is None:
        raise ValueError("block has a node-based dependency but no dep_node was provided")
    if block.active_status in (ActiveStatus.CUT, ActiveStatus.LINKED):
        # Port of Raptor 7 (`ER3-09`): the downstream node's status is not
        # affected by a Cut/Linked block's own, so there is no `GetDownNum` special case.
        if dep_node.higher_cold or dep_node.status == NodeStatus.STANDBY:
            return BlockStatus.STANDBY, False
        if dep_node.status in _NODE_UP_LIKE:
            return BlockStatus.RUNNING, False
        return (BlockStatus.IDLE, False) if (
            block.dep_idle_percent == 100.0 or block.def_dep_state_idle) else (BlockStatus.RUNNING, False)
    if dep_node.higher_cold:
        # Port of Raptor 7 -- a `higherCold` dependency
        # node puts the completing active block into Standby first,
        # regardless of the node's own status.
        return BlockStatus.STANDBY, False
    if dep_node.status in _NODE_UP_LIKE:
        return BlockStatus.RUNNING, False
    if dep_node.status == NodeStatus.STANDBY:
        # Port of Raptor 7 -- a node in NodeStandby puts the
        # completing ACTIVE block into Standby as well ("Dep-"+name tag
        # handled by change_block_state's caller).
        return BlockStatus.STANDBY, False

    # depNode.Status is Down/Finished/PM -- GetDownNum's "special case".
    if own_node is None:
        raise ValueError("block has a node-based dependency with the target node currently down, but no own_node was provided")
    array_index = own_node.in_paths_array.index(block.downstream_link_id)
    fail_val = own_node.path_fail_value[array_index] if array_index < len(own_node.path_fail_value) else 0
    pm_val = own_node.path_pm_value[array_index] if array_index < len(own_node.path_pm_value) else 0
    down_num = fail_val + pm_val

    if down_num > 1:
        # Port of Raptor 7's own `IF DepIdlePerc=100.0:
        # Idle. ELSE: IF defDepStateIdle: Idle ELSE Running.` -- no fresh
        # mixed-percentage draw here (unlike the `Down`-trigger dispatch in
        # `back_flow_dependencies_from_node`), just a direct check of
        # `defDepStateIdle` -- after the `DepIdlePerc=100.0` test, which alone gives Idle (a file can
        # carry 100 % idle with defDepStateIdle off; the Cut arm above reads them the same way,
        # `CutRepairIdleProbe`).
        return (BlockStatus.IDLE, False) if (
            block.dep_idle_percent == 100.0 or block.def_dep_state_idle) else (BlockStatus.RUNNING, False)

    # down_num<=1: self-correcting -- Running first, unconditionally;
    # caller must re-check dep_node.status AFTER applying this (see
    # docstring) to decide on a follow-up Idle transition. Source's own
    # `IF defDepStateIdle` gate is INSIDE each
    # of the two post-recheck branches (still-down / recovered), not a
    # precondition on reaching this whole special case at all -- when
    # `defDepStateIdle=False`, the block simply stays Running either
    # way (no follow-up Idle, no `prevDepState` mark), but the
    # unconditional `ResendBackFlow` + second `CheckColds` tail
    # still fires regardless -- that is what
    # `needs_recheck=True` signals to the caller, not "will go Idle". The
    # caller (`_apply_maintenance_completed`) must therefore gate its own
    # follow-up Idle transition on `def_dep_state_idle` too, not just
    # `needs_repair_idle_recheck`.
    return BlockStatus.RUNNING, True


def needs_repair_idle_recheck(dep_node: RBDNode) -> bool:
    """Call AFTER applying the `RUNNING` transition `repair_completion_
    status` returned with `needs_recheck=True`, once that transition's
    own cascade has had a chance to update `dep_node.status` in place --
    `True` means a follow-up `IDLE` transition (empty comment) is needed;
    see `repair_completion_status`'s own docstring for the full story."""
    return dep_node.status in _NODE_DOWN_OR_PM


_BLOCK_DOWN_LIKE_FOR_RESEND = {
    BlockStatus.REPAIRING, BlockStatus.PM, BlockStatus.REP_HOLD, BlockStatus.PM_HOLD,
}


def resend_back_flow_would_apply(
    all_blocks: list[RBDBlock], nodes_by_id: dict[int, RBDNode], links_by_id: dict[int, Link],
    end_node_id: int | None,
) -> bool:
    """Detects whether `ResendBackFlow` would have
    any real effect right now. `block_lifecycle.block_run_process` calls this
    instead of porting `ResendBackFlow`: the `repair_completion_status`
    `needs_recheck=True`/`down_num<=1` branch calls
    `ResendBackFlow` unconditionally right after deciding Running-vs-Idle.

    `ResendBackFlow` sets a module flag (`specialBackFlow:=TRUE`) that changes
    `BackFlowDependencies`'s own dispatch logic for the duration of the resend;
   it does not merely re-run the ordinary
    dispatch. It also branches on `prevDepState`/`returnFromMaint` and on
    Modsim WAIT-primitive bookkeeping (`FailWait`/`ZeroWait`/`Interrupt`/
    `BlockTrigger.Release`) that this project's scheduler abstracts away, so
    modelling it would mean a second, mostly-parallel dependency-state thread
    with no fixture to verify against.

    It is safe to skip when no currently Running/Standby dependent block points
    at a currently down-like target. `BackFlowDependencies` only ever acts on a
    dependent block currently Running or Standby, and a
    dependent already Idle was caught by Raptor 7 edge-triggered dispatch,
    so both branches skip it. This function therefore returns true only outside
    that safe case, i.e. only when skipping would risk dropping a real
    transition.
    """
    has_deps_block_ids = {b.dependency_num for b in all_blocks if b.dep_type == "RBDBlock"}
    down_like_block_ids = set()
    for block in all_blocks:
        if block.id not in has_deps_block_ids:
            continue
        if block.active_status == ActiveStatus.CUT:
            down_like_block_ids.add(block.id)
        elif block.active_status == ActiveStatus.ACTIVE and block.op_status in _BLOCK_DOWN_LIKE_FOR_RESEND:
            down_like_block_ids.add(block.id)

    def _resolved_target(block: RBDBlock) -> int | None:
        # `own_node_id` is only ever consulted for `locDepend` (`Dependency
        # Num=-1`) -- a block with no real `downstream_link_id`/no `links_by_id`
        # entry (e.g. a hand-built test double, or a non-node-dependent
        # block) never reaches that branch inside `resolve_dependency_
        # node_id`, so resolving it lazily/tolerantly here (rather than
        # requiring every block to have a valid link) keeps this function
        # usable without a fully-wired model.
        link = links_by_id.get(block.downstream_link_id)
        own_node_id = link.connect_to_id if link is not None else None
        return resolve_dependency_node_id(block, end_node_id, own_node_id) if end_node_id is not None else None

    # Node-side "down-like" targets, covering locDepend/positive-DepNum+
    # RBDNode alongside sysDepend (matching `resolve_dependency_node_id`'s
    # three-way resolution). Only `Status=Down` counts, matching
    # `ResendBackFlow`'s own node loop (`IF (tempNode.Status=Down)`, no
    # `NodePM` check unlike the block side).
    down_like_node_ids = set()
    for block in all_blocks:
        target_id = _resolved_target(block)
        if target_id is not None and nodes_by_id.get(target_id) is not None and nodes_by_id[target_id].status == NodeStatus.DOWN:
            down_like_node_ids.add(target_id)

    if not down_like_block_ids and not down_like_node_ids:
        return False

    for dep_block in all_blocks:
        if dep_block.op_status not in (BlockStatus.RUNNING, BlockStatus.STANDBY):
            continue
        if dep_block.dep_type == "RBDBlock" and dep_block.dependency_num in down_like_block_ids:
            return True
        target_id = _resolved_target(dep_block)
        if target_id is not None and target_id in down_like_node_ids:
            return True

    return False


def resend_back_flow(
    all_blocks: list[RBDBlock],
    nodes_by_id: dict[int, RBDNode],
    links_by_id: dict[int, Link],
    end_node_id: int | None,
    transition: TransitionFn,
    sim_time: float,
    *,
    draw_action: DrawActionFn | None = None,
    induce: InduceFn | None = None,
    fev: FevDependencyHooks | None = None,
) -> None:
    """Port of `ResendBackFlow`. Its
    `blockHasDepsGroup` loop is ported here, and its `nodeHasDepsGroup` loop
    is ported via `back_flow_dependencies_from_node`; the latter iterates
    every node, and that function's own `resolve_dependency_node_id` filter
    makes a node with no dependents a harmless no-op, matching the effect of
    the source's `hasDepObjects`-filtered group.

    The `eventHasDepsGroup` loop is not ported. It is checked by
    `resend_back_flow_would_apply`, which raises rather than silently skipping
    a real effect.
    """
    # Port of ResendBackFlow's blockHasDepsGroup FOREACH :
    # blocks with RBDBlock dependents, re-firing their Down dispatch when
    # Cut, or Active-and-mid-maintenance (Repairing/PM/RepHold/PMhold).
    # `back_flow_dependencies`'s specialBackFlow variant re-idles only
    # members whose prevDepState records a prior GoIdle (or clean record
    # + defDepStateIdle) -- matching.
    has_deps_block_ids = {b.dependency_num for b in all_blocks if b.dep_type == "RBDBlock"}
    if has_deps_block_ids:
        for block in all_blocks:
            if block.id not in has_deps_block_ids:
                continue
            down_like = block.active_status == ActiveStatus.CUT or (
                block.active_status == ActiveStatus.ACTIVE and block.op_status in _BLOCK_DOWN_LIKE_FOR_RESEND
            )
            if down_like:
                back_flow_dependencies(
                    "Down", block, all_blocks, transition, sim_time,
                    special_back_flow=True, fev=fev,
                )

    for node in nodes_by_id.values():
        if node.status != NodeStatus.DOWN:
            continue
        back_flow_dependencies_from_node(
            "Down", node,
            # `nodes_by_id` is NOT forwarded, and that is NOT an oversight --
            # see the note added to this call. Forwarding it would enable the
            # node-dependents half (`_back_flow_node_dependents`), which is a
            # behaviour change requiring its own investigation.
            BackFlowDeps(
                end_node_id=end_node_id if end_node_id is not None else -1,
                all_blocks=all_blocks, links_by_id=links_by_id,
                transition=transition, nodes_by_id=None,
                draw_action=draw_action, induce=induce, special_back_flow=True,
                fev=fev,
            ),
            sim_time,
        )

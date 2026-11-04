"""Build the Boolean success function of a coherent RBD.

The network layer is the bridge between the engine's typed model and the
BDD. It flattens hierarchies (the loader already resolves hierarchy boundary
links to the internal in/out nodes), walks block chains, applies the engine's
node k-of-n rule, and refuses shapes outside the solver's assumptions.

The graph traversal here is deliberately independent of the simulation
engine's status code. If both used the same status function, an agreeing
result would not prove anything.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field

from raptor.core.engine.load import SimulationModel
from raptor.core.model import ActiveStatus, RBDBlock, RBDEvent
from raptor.core.rng import Distribution as RngDistribution

from . import survival
from .bdd import BDD, FALSE, MAX_BDD_NODES, TRUE, recursion_headroom
from .errors import BddTooLarge, NotApplicable
from .limits import memory_text

#: Engine values that mean "no spare constraint".
_INFINITE_SPARING = "INFINITE"
_NONE_SPARING = "NONE"

#: The BDD probability walk is recursive; keep well inside Python's default
#: recursion limit. Practical models are far smaller; this is a refusal, not
#: an approximation. A caller that knows its model can raise it explicitly.
MAX_VARIABLES = 5_000

#: ``progress(stage, done, total)``, matching the simulation engine's set-up
#: callback. The analytical solver reports graph/BDD construction and the
#: importance pass.
ProgressCallback = Callable[[str, int, int], None]


IGNORABLE = ("repairs", "pm", "spares", "resources")
STANDBY_MODES = ("active", "absent")
DEPENDENCY_MODES = ("up", "down")


@dataclass(frozen=True)
class Scope:
    """What the caller asks the solver to assume.

    ``ignore`` drops a whole class of simulation-only behaviour instead of
    refusing the model (see `IGNORABLE`). ``phase`` is the 1-based phase to
    analyse as if the system stayed in it. ``standby`` says how to treat
    cold-standby paths: ``"active"`` (in service) or ``"absent"``.
    ``dependencies`` says how to treat dependent elements: ``"up"`` ignores
    the dependency and analyses the element's own law, ``"down"`` removes it
    from the success path.
    """

    ignore: frozenset[str] = frozenset()
    phase: int | None = None
    standby: str | None = None
    #: How to treat dependencies: None refuses them, "up" treats a
    #: dependent element as an independent element in its own right, and
    #: "down" forces it out of the success path.
    dependencies: str | None = None


@dataclass
class BlockInfo:
    """One block or event that appears as a BDD variable."""

    id: int
    name: str
    kind: str  # "block" | "event"
    variable: int | None
    fail_code: int
    fail_params: list[float]
    repair_code: int
    repair_params: list[float]
    pre_code: int
    pre_params: list[float]
    post_code: int
    post_params: list[float]
    perfect: bool = False
    repairable: bool = False
    mean_fail: float | None = None
    mean_repair: float | None = None
    mean_pre: float = 0.0
    mean_post: float = 0.0
    event_success: float | None = None
    duty_cycle: float = 1.0
    start_age: float = 0.0
    #: Cut in the analysed phase: out of the circuit, so no path through it.
    forced_down: bool = False
    #: A note on how this element was treated ("cut", "linked", "stress x1.8",
    #: "standby treated as absent"), or empty when it was analysed as is.
    state: str = ""

    @property
    def mean_down(self) -> float | None:
        """Mean corrective + logistic down time, or ``None`` if infinite."""
        if not self.repairable:
            return None
        if self.mean_repair is None:
            return None
        return self.mean_repair + self.mean_pre + self.mean_post

    @property
    def availability(self) -> float:
        """Steady-state up probability for an independent component."""
        if self.forced_down:
            return 0.0
        if self.perfect:
            return 1.0
        if self.event_success is not None:
            return self.event_success
        if not self.repairable:
            return 0.0
        down = self.mean_down
        if down is None:
            return 0.0
        if self.mean_fail is None:
            return 1.0
        total = self.mean_fail + down
        if total <= 0.0:
            raise NotApplicable(
                f"block {self.id} {self.name!r} has zero mean life and zero "
                "mean down time; its steady-state availability is undefined")
        return self.mean_fail / total

    def reliability(self, t: float) -> float:
        if self.forced_down:
            return 0.0
        if self.perfect:
            return 1.0
        if self.event_success is not None:
            return self.event_success
        effective = self.start_age + self.duty_cycle * t
        if self.start_age > 0.0:
            base = survival.sf(self.fail_code, self.fail_params,
                               self.start_age)
            if base <= 0.0:
                return 0.0
            return min(1.0, survival.sf(self.fail_code, self.fail_params,
                                        effective) / base)
        return survival.sf(self.fail_code, self.fail_params, effective)

    def density(self, t: float) -> float | None:
        if self.perfect or self.forced_down:
            return 0.0
        if self.event_success is not None:
            return None
        effective = self.start_age + self.duty_cycle * t
        value = survival.pdf(self.fail_code, self.fail_params, effective)
        if value is None:
            return None
        if self.start_age > 0.0:
            base = survival.sf(self.fail_code, self.fail_params,
                               self.start_age)
            if base <= 0.0:
                return None
            return value * self.duty_cycle / base
        return value * self.duty_cycle


@dataclass
class BuildResult:
    """A built Boolean success function and its applicability record."""

    model: SimulationModel
    mode: str
    bdd: BDD
    root: int
    start_node_id: int
    end_node_id: int
    variables: list[BlockInfo]
    by_key: dict[tuple[str, int], BlockInfo]
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    horizon: float | None = None
    capacity_ignored: bool = False
    scope: Scope = field(default_factory=Scope)

    @property
    def node_count(self) -> int:
        return self.bdd.node_count

    def probabilities(self, t: float) -> list[float]:
        """Variable probabilities at time ``t`` for this build's mode."""
        if self.mode == "availability":
            return [info.availability for info in self._ordered()]
        return [info.reliability(t) for info in self._ordered()]

    def derivatives(self, t: float) -> list[float] | None:
        """dp_i/dt for reliability mode, or ``None`` if any is undefined.

        Events and perfect blocks have zero slope. A block's density is
        undefined at an atom of its life distribution (a Fixed life, or an
        Empirical knot); the system density is then undefined too.
        """
        if self.mode != "reliability":
            return [0.0] * len(self.variables)
        out: list[float] = []
        for info in self._ordered():
            if (info.perfect or info.forced_down
                    or info.event_success is not None):
                out.append(0.0)
                continue
            density = info.density(t)
            if density is None:
                return None
            out.append(-density)
        return out

    def _ordered(self) -> list[BlockInfo]:
        """Variables in BDD index order."""
        return self.variables


def build(model: SimulationModel, *, mode: str = "reliability",  # noqa: C901, PLR0912, PLR0915
          ignore_repairs: bool = False, ignore: Collection[str] = (),
          phase: int | None = None,
          standby: str | None = None,
          dependencies: str | None = None,
          progress: ProgressCallback | None = None,
          max_variables: int = MAX_VARIABLES,
          max_bdd_nodes: int = MAX_BDD_NODES) -> BuildResult:
    """Build the success BDD for ``model``.

    Raises `NotApplicable` when the model uses a construct the static solver
    cannot represent. The first offending element is named in the message.

    ``progress(stage, done, total)`` reports graph-node and BDD-node
    construction. ``max_variables`` and ``max_bdd_nodes`` override the
    module defaults for one call; both are hard refusals, never
    approximations.
    """
    if mode not in ("reliability", "availability"):
        raise ValueError(f"unknown analytical mode {mode!r}")
    if max_variables < 1:
        raise ValueError("max_variables must be at least 1")
    if max_bdd_nodes < 2:
        raise ValueError("max_bdd_nodes must be at least 2")
    scope = _scope(model, mode, ignore_repairs, ignore, phase, standby,
                   dependencies)
    if progress is not None:
        progress("Preparing model", 0, 1)
    _gate(model, mode=mode, scope=scope)
    if progress is not None:
        progress("Preparing model", 1, 1)

    start = _one_node(model, 1, "Start")
    end = _one_node(model, 3, "End")
    incoming, block_in = _index_links(model)

    # Variable order is first encounter on a depth-first walk from Start.
    order = _variable_order(model, start, max_variables=max_variables)
    if len(order) > max_variables:
        raise NotApplicable(
            f"the static solver supports at most {max_variables} blocks/"
            f"events; this model has {len(order)}. Use simulation or raise "
            "the variable limit.")

    total_nodes = max(1, len(model.nodes_by_id))
    done = [0]


    def bdd_progress(count: int) -> None:
        if progress is not None:
            progress(_build_stage(count, done[0], total_nodes, max_bdd_nodes,
                                  time.monotonic() - build_began),
                     done[0], total_nodes)

    build_began = time.monotonic()
    bdd = BDD(max_nodes=max_bdd_nodes, progress=bdd_progress)
    # Reverse the first-encounter order for BDD indices. A series chain then
    # adds each new variable BELOW the existing diagram, which is linear;
    # adding it above would rebuild the whole chain (quadratic nodes).
    var_index = _variable_index(order, end, incoming, block_in)
    x = {key: bdd.variable(i) for key, i in var_index.items()}

    standby_absent: set[tuple[str, int]] = set()
    # Dominator factoring. If every path into a junction J passes through one
    # element D (D dominates J), then exactly
    #     cond(J) = cond(D) AND cond(J with D taken as working),
    # because each input's condition is cond(D) AND a local part, and
    # threshold(cond(D) AND local_i, k) = cond(D) AND threshold(local_i, k)
    # for k >= 1. Building the k-of-n over the small local parts, rather
    # than over inputs that each carry D's whole upstream, keeps the build
    # from creating millions of intermediate nodes. A node forced TRUE in
    # the middle of the graph (a phase-linked hierarchy Out) breaks the
    # identity, so such a model is built without factoring.
    start_key = ("RBDNode", start)
    factor_ok = not any(
        node.type_node == 5 and scope.phase is not None
        and _node_k(node, scope, len(incoming.get(node_id, []))) <= 0
        for node_id, node in model.nodes_by_id.items())
    idom = _immediate_dominators(start_key, incoming, block_in) \
        if factor_ok else {}
    outlook = size_outlook(model, order, incoming, block_in, scope, idom)
    if progress is not None:
        progress("Outlook: " + outlook.text(), 0, 0)
    Key = tuple[str, int]
    node_memo: dict[tuple[int, Key | None], int] = {}
    visiting: set[tuple[int, Key | None]] = set()
    finished: set[int] = set()

    def element_condition(kind: str, ident: int, cut: Key | None) -> int:
        """When ``kind ident`` passes a signal, given ``cut`` works."""
        if cut is not None and (kind, ident) == cut:
            return TRUE
        if kind == "RBDNode":
            return node_condition(ident, cut)
        if kind in ("RBDBlock", "RBDEvent"):
            variable = x.get((kind, ident))
            if variable is None:
                # Not reachable from Start, so it can never carry a signal.
                return FALSE
            return bdd.And(input_condition(kind, ident, cut), variable)
        raise NotApplicable(f"unresolved {kind} {ident}")

    def input_condition(kind: str, ident: int, cut: Key | None) -> int:
        link = block_in.get((kind, ident))
        if link is None:
            raise NotApplicable(
                f"{kind} {ident} has no input link; the analytical solver "
                "needs a single Start-to-End path")
        return branch_condition(link, cut)

    def branch_condition(link, cut: Key | None) -> int:
        """The condition under which ``link`` carries a Start-to-End signal."""
        if scope.dependencies == "down" and (
                getattr(link, "switch_status", False)
                or getattr(link, "sb_link", False)):
            return FALSE
        source_kind, source_id = _link_source(link)
        if source_kind in ("RBDNode", "RBDBlock", "RBDEvent"):
            return element_condition(source_kind, source_id, cut)
        raise NotApplicable(
            f"link {link.object_num} has an unresolved {source_kind} source")

    def _finish(node_id: int, key: tuple[int, Key | None], out: int) -> int:
        visiting.discard(key)
        node_memo[key] = out
        if node_id not in finished:
            finished.add(node_id)
            done[0] += 1
            if progress is not None:
                progress(_build_stage(bdd.node_count, done[0], total_nodes,
                                      max_bdd_nodes,
                                      time.monotonic() - build_began), done[0], total_nodes)
        return out

    def node_condition(node_id: int, cut: Key | None = None) -> int:  # noqa: C901, PLR0912
        key = (node_id, cut)
        got = node_memo.get(key)
        if got is not None:
            return got
        if key in visiting:
            raise NotApplicable(
                f"node {node_id} is part of a cycle; the analytical solver "
                "needs an acyclic RBD")
        visiting.add(key)
        node = model.nodes_by_id[node_id]
        if has_node_dependency(node) and scope.dependencies == "down":
            # The caller asked to force every dependent element down: a
            # dependent junction cannot carry a success path.
            return _finish(node_id, key, FALSE)
        if node.type_node == 1:
            return _finish(node_id, key, TRUE)
        links = incoming.get(node_id, [])
        k = _node_k(node, scope, len(links))
        if k <= 0 and node.type_node == 5 and scope.phase is not None:
            return _finish(node_id, key, TRUE)  # hierarchy Out Linked in this phase
        if node.type_node == 3 or k <= 0:
            k = 1
        if node.cold_standby and scope.standby == "absent":
            kept, dropped = _split_standby(node, links, k)
            links = kept
            for link in dropped:
                kind, ident = _link_source(link)
                if kind in ("RBDBlock", "RBDEvent"):
                    standby_absent.add((kind, ident))
        dom = idom.get(("RBDNode", node_id))
        if dom is not None and dom != cut and dom != start_key:
            # Factor out the dominator (see above): the junction relative to
            # it, then AND with the dominator's own condition.
            local = node_condition(node_id, dom)
            out = bdd.And(element_condition(*dom, cut), local)
        else:
            out = bdd.threshold([branch_condition(link, cut) for link in links], k)
        return _finish(node_id, key, out)

    try:
        root = node_condition(end)
    except BddTooLarge as exc:
        # Name the junctions the pre-check expected to be large: they are
        # the place to start (solve that hierarchy alone, or simplify it).
        if outlook.risky:
            raise BddTooLarge(f"{exc} {outlook.text()}", limit=exc.limit,
                              seconds=exc.seconds,
                              allowed=exc.allowed) from None
        raise
    if progress is not None:
        progress("Building BDD", total_nodes, total_nodes)

    infos: list[BlockInfo] = []
    for key in order:
        kind, ident = key
        if kind == "RBDBlock":
            infos.append(_block_info(model.blocks_by_id[ident],
                                     var_index[key], mode=mode, scope=scope))
        else:
            infos.append(_event_info(model.events_by_id[ident],
                                     var_index[key], scope=scope))
        if key in standby_absent:
            infos[-1].state = "standby, treated as absent"

    warnings: list[str] = []
    if model.capacity_analysis:
        warnings.append(
            "capacity analysis is ignored: success is the End node being up, "
            "not a flow target")
    if mode == "availability":
        non_repairable = [info for info in infos
                          if not info.repairable and not info.perfect]
        if non_repairable:
            warnings.append(
                f"{len(non_repairable)} block(s) are non-repairable and are "
                "down at steady state (A_i = 0); a series path through one "
                "makes the system availability 0")
    if model.ignore_start_fail:
        warnings.append(
            "the model ignores start failures; the analytical R(0) still "
            "counts a distribution draw at the first instant")
    reachable = {key[1] for key in order}
    unreachable = sorted((set(model.blocks_by_id) | set(model.events_by_id))
                         - reachable)
    if unreachable:
        warnings.append(
            f"{len(unreachable)} block(s)/event(s) are not reachable from "
            "Start and are ignored by the analytical solver")

    assumptions = [
        "independent components",
        "one Start and one End node",
        "success means at least one Start-to-End path",
    ]
    if mode == "reliability":
        assumptions.append("no repairs: R(t) is the first-failure reliability")
        # Only say it when it applies: listing it for every model read as
        # though the model had duty cycles or start ages it does not have.
        if any(info.duty_cycle != 1.0 for info in infos):
            assumptions.append(
                "a block with a duty cycle ages at that fraction of "
                "calendar time")
        if any(info.start_age > 0.0 for info in infos):
            assumptions.append(
                "a block with a start age is treated as having already "
                "survived to that age, so its reliability is conditional "
                "on that")
    else:
        assumptions.append(
            "steady state, perfect repair, independent alternating-renewal "
            "components")
        assumptions.append("A_i = MTTF_i / (MTTF_i + MTTR_i)")
    assumptions.extend(_scope_notes(model, scope, mode, infos))
    if scope.phase is not None:
        warnings.append(
            "phase transitions are ignored: the system is analysed as if it "
            "stayed in this phase for the whole time")

    horizon = model.run_length if model.termination_type == "TimeSim" else None
    return BuildResult(
        model=model,
        mode=mode,
        bdd=bdd,
        root=root,
        start_node_id=start,
        end_node_id=end,
        variables=sorted(infos, key=lambda item: item.variable if item.variable is not None else 0),
        by_key={(item.kind, item.id): item for item in infos},
        warnings=warnings,
        assumptions=assumptions,
        horizon=horizon,
        capacity_ignored=model.capacity_analysis,
        scope=scope,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _one_node(model: SimulationModel, node_type: int, label: str) -> int:
    found = [node.id for node in model.nodes_by_id.values()
             if node.type_node == node_type]
    if len(found) != 1:
        raise NotApplicable(
            f"the analytical solver needs exactly one {label} node; found "
            f"{len(found)}")
    return found[0]


def has_node_dependency(node) -> bool:
    """True when a *node* row names a dependency the engine resolves.

    Nodes use ``-2`` for the system/End and a positive id for another node.
    ``-1`` is the block-only "own node" target and
    ``dependency.resolve_dependency_node_id`` deliberately returns ``None``
    for a node, so it is not a dependency here.
    """
    number = getattr(node, 'dependency_num', 0)
    if number == -1:
        return bool(getattr(node, 'cold_dep', False))
    if number:
        return True
    if getattr(node, 'cold_dep', False):
        return True
    dep_type = str(getattr(node, 'dep_type', '') or '').strip().lower()
    return dep_type not in ('', 'none')


def _gate(model: SimulationModel, *, mode: str, scope: Scope) -> None:  # noqa: C901
    if model.phases and scope.phase is None:
        raise NotApplicable(
            "the model has phases, and the structure or k-requirement can "
            "change by phase. Choose one phase to analyse as if the system "
            "stayed in it")
    if model.startup_failures:
        raise NotApplicable(
            "the model uses the StartUpFaiLures trigger, which is a startup "
            "redraw rule outside the static solver")
    for block in model.blocks_by_id.values():
        _gate_block(block, mode=mode, scope=scope)
    cold_links: set[int] = set()
    for node in model.nodes_by_id.values():
        if has_node_dependency(node) and scope.dependencies is None:
            raise NotApplicable(
                f"node {node.id} {node.node_name!r} has a dependency; the "
                "analytical solver covers plain success paths. Choose "
                "whether dependencies are up or down")
        if node.cold_standby or node.cold_links:
            if scope.standby is None:
                raise NotApplicable(
                    f"node {node.id} {node.node_name!r} uses cold standby. "
                    "Choose whether to treat the standby paths as in "
                    "service or as absent")
            cold_links.update(node.cold_links)
        if node.active_status != ActiveStatus.ACTIVE:
            raise NotApplicable(
                f"node {node.id} {node.node_name!r} is not Active; cut "
                "structures are outside the analytical solver")
    for event in model.events_by_id.values():
        _gate_event(event, scope)
    for link in model.links_by_id.values():
        # A dependency choice has nothing to do with standby: an unchosen
        # cold-standby path is still outside the solver.
        if (link.switch_status or link.sb_link) \
                and link.object_num not in cold_links:
            raise NotApplicable(
                f"link {link.object_num} is a cold-standby link; the "
                "analytical solver covers plain success paths. Choose "
                "whether to treat the standby paths as in service or as "
                "absent")


def _phase_of(item, scope: Scope) -> tuple[str, float] | None:
    """The (type, value) a block/event takes in the analysed phase."""
    if scope.phase is None or not item.uses_phasing or not item.phase_type:
        return None
    index = scope.phase - 1
    if index >= len(item.phase_type):
        return None
    value = item.phase_value[index] if index < len(item.phase_value) else 1.0
    return item.phase_type[index].strip().upper()[:1], float(value)


def _gate_block(block: RBDBlock, *, mode: str, scope: Scope) -> None:  # noqa: C901, PLR0912, PLR0915
    name = f"block {block.id} {block.name!r}"
    if block.active_status != ActiveStatus.ACTIVE:
        raise NotApplicable(f"{name} is not Active")
    phase = _phase_of(block, scope)
    if scope.phase is None and (block.uses_phasing or block.phase_type
                                or block.phase_value):
        raise NotApplicable(f"{name} uses phases")
    if phase is not None and phase[0] not in ("A", "C", "L"):
        raise NotApplicable(
            f"{name} has phase type {phase[0]!r}, which the analytical "
            "solver does not understand")
    if (has_dependency(block) and scope.dependencies is None
            and not (block.cold_dep and scope.standby)):
        raise NotApplicable(
            f"{name} has a dependency. Choose whether dependencies are up "
            "or down")
    if (block.uses_pm or block.pm_triggered) and "pm" not in scope.ignore:
        raise NotApplicable(f"{name} uses preventive maintenance")
    # A dependency choice is not a standby choice: it relaxes these two
    # gates only for the dependent block itself, whose standby relationship
    # is what the choice resolves. A block with standby stress but no
    # dependency still needs a standby treatment.
    standby_resolved = (scope.standby is not None
                        or (has_dependency(block)
                            and scope.dependencies is not None))
    if block.cold_dep and not standby_resolved:
        raise NotApplicable(f"{name} uses cold standby")
    if block.sb_stress not in (0.0, 1.0) and not standby_resolved:
        raise NotApplicable(
            f"{name} has standby stress {block.sb_stress!r}; standby stress "
            "is outside the analytical solver")
    spares_ok = "spares" in scope.ignore
    if (block.sparing_type.name not in (_NONE_SPARING, _INFINITE_SPARING)
            and not spares_ok):
        raise NotApplicable(
            f"{name} uses sparing type {block.sparing_type.value!r}; spare "
            "pools are simulation-only")
    if (block.pool_name and not spares_ok
            and block.pool_name.lower() not in ("", "none", "unnamed")):
        raise NotApplicable(
            f"{name} names spare pool {block.pool_name!r}; spare pools are "
            "simulation-only")
    if (block.resource_pool_name and "resources" not in scope.ignore
            and block.resource_pool_name.lower() not in ("", "none")):
        raise NotApplicable(
            f"{name} names resource pool {block.resource_pool_name!r}; "
            "resources are simulation-only")

    if block.fail_distro == int(RngDistribution.NONE):
        pass
    elif block.fail_distro not in survival.SUPPORTED:
        raise NotApplicable(
            f"{name} uses failure distribution code {block.fail_distro}, "
            "which has no analytical survival function")

    if block.duty_cycle <= 0.0:
        raise NotApplicable(f"{name} has duty cycle {block.duty_cycle!r}; "
                            "it must be greater than zero")
    if block.gd_type != 0:
        raise NotApplicable(
            f"{name} uses growth/decay (GDType={block.gd_type}); the static "
            "solver has no time-dependent failure-rate model")
    if mode == "reliability":
        if block.start_age > 0.0 and block.sim_start_type != 3:
            raise NotApplicable(
                f"{name} has start age {block.start_age!r} but start "
                f"condition {block.sim_start_type!r}; start age is used only "
                "with SimStart=3")
        if block.fail_distro == int(RngDistribution.NONE):
            return
        # The solver starts every block up, at age zero. A non-default
        # start condition or a non-zero amount already consumed changes the
        # first failure draw and is not representable here yet.
        if block.sim_start_type != 3 or block.amount_exhausted > 0.0:
            raise NotApplicable(
                f"{name} has start condition {block.sim_start_type!r} with "
                f"{block.amount_exhausted!r} already consumed; reliability "
                "mode assumes every block starts up at age zero")
        if block.repair_distro != int(RngDistribution.NONE):
            if "repairs" not in scope.ignore:
                raise NotApplicable(
                    f"{name} has a repair distribution, but reliability "
                    "mode means non-repairable. Ignore repairs to compute "
                    "the first-failure reliability anyway")
            return
    else:  # availability
        if block.fail_distro == int(RngDistribution.NONE):
            return
        if block.restoration_type in (1, 2) and block.restoration_factor < 1.0:
            raise NotApplicable(
                f"{name} uses a restoration factor of "
                f"{block.restoration_factor!r}, so a repair leaves it partly "
                "worn; availability mode assumes every repair is as good as "
                "new (restoration off, or factor 1)")
        if block.repair_distro == int(RngDistribution.NONE):
            # A non-repairable block is allowed; it is simply down at steady
            # state. A parallel path can still keep the system up.
            return
        if block.repair_distro not in survival.SUPPORTED:
            raise NotApplicable(
                f"{name} uses repair distribution code {block.repair_distro}, "
                "which has no analytical mean")
        if block.pre_dist not in survival.SUPPORTED and block.pre_dist != int(RngDistribution.NONE):
            raise NotApplicable(
                f"{name} uses pre-LDT distribution code {block.pre_dist}")
        if block.post_dist not in survival.SUPPORTED and block.post_dist != int(RngDistribution.NONE):
            raise NotApplicable(
                f"{name} uses post-LDT distribution code {block.post_dist}")


def has_dependency(item) -> bool:
    """True when a block or node names a real dependency.

    The legacy file writes ``DepType=None`` for "no dependency" and an
    empty/absent type for older models, so both the numeric target and the
    type text are needed. ``cold_dep`` is engine bookkeeping for a cold
    standby link and is honoured because a caller may pass a runtime model.
    ``has_dep_objects`` is deliberately NOT used: it means other elements
    depend on *this* one, which is the opposite question.
    """
    dep_type = str(getattr(item, 'dep_type', '') or '').strip().lower()
    return bool(getattr(item, 'dependency_num', 0)
                or getattr(item, 'cold_dep', False)
                or dep_type not in ('', 'none'))


def _gate_event(event: RBDEvent, scope: Scope) -> None:
    name = f"event {event.id} {event.name!r}"
    if event.active_status != ActiveStatus.ACTIVE:
        raise NotApplicable(f"{name} is not Active")
    phase = _phase_of(event, scope)
    if scope.phase is None and (event.uses_phasing or event.phase_type
                                or event.phase_value):
        raise NotApplicable(f"{name} uses phases")
    if phase is not None and phase[0] not in ("A", "C", "L"):
        raise NotApplicable(
            f"{name} has phase type {phase[0]!r}, which the analytical "
            "solver does not understand")
    if event.fail_distro != int(RngDistribution.BERNOULLI):
        raise NotApplicable(
            f"{name} uses failure distribution code {event.fail_distro}; "
            "only Bernoulli events are static")


# ---------------------------------------------------------------------------
# Graph indexing
# ---------------------------------------------------------------------------

def _immediate_dominators(start: tuple[str, int], incoming: dict,  # noqa: C901
                          block_in: dict) -> dict[tuple[str, int], tuple[str, int]]:
    """Immediate dominator of every element reachable from ``start``.

    Cooper, Harvey and Kennedy's iterative algorithm over the element graph
    (nodes, blocks and events). Element D dominates X when every path from
    Start to X passes through D.
    """
    preds: dict[tuple[str, int], list[tuple[str, int]]] = {}
    succs: dict[tuple[str, int], list[tuple[str, int]]] = {}

    def add(src: tuple[str, int], dst: tuple[str, int]) -> None:
        preds.setdefault(dst, []).append(src)
        succs.setdefault(src, []).append(dst)

    for node_id, links in incoming.items():
        for link in links:
            add(_link_source(link), ("RBDNode", node_id))
    for key, link in block_in.items():
        add(_link_source(link), key)
    # Reverse postorder from Start (iterative DFS).
    order: list[tuple[str, int]] = []
    seen = {start}
    stack = [(start, iter(succs.get(start, [])))]
    while stack:
        top, children = stack[-1]
        child = next(children, None)
        if child is None:
            order.append(top)
            stack.pop()
        elif child not in seen:
            seen.add(child)
            stack.append((child, iter(succs.get(child, []))))
    order.reverse()
    index = {key: i for i, key in enumerate(order)}
    dom: dict[tuple[str, int], tuple[str, int]] = {start: start}

    def intersect(a: tuple[str, int], b: tuple[str, int]) -> tuple[str, int]:
        while a != b:
            while index[a] > index[b]:
                a = dom[a]
            while index[b] > index[a]:
                b = dom[b]
        return a

    changed = True
    while changed:
        changed = False
        for key in order[1:]:
            ready = [p for p in preds.get(key, []) if p in dom and p in index]
            if not ready:
                continue
            new = ready[0]
            for other in ready[1:]:
                new = intersect(other, new)
            if dom.get(key) != new:
                dom[key] = new
                changed = True
    dom.pop(start, None)
    return dom


def _build_stage(count: int, done: int, total: int, limit: int,
                 elapsed: float = 0.0) -> str:
    """The progress line while the diagram is built.

    The size of a BDD cannot be known before it is built, so this reports
    what is known (nodes so far, their memory, junctions done) with a rough
    time left at the rate so far, and flags a build that has used most of
    its limit with most of the junctions still to go: it is then unlikely
    to finish within the limit. The time is rough because junctions differ
    a great deal in cost.
    """
    text = f"Building BDD ({count:,} nodes, {memory_text(count)})"
    if elapsed > 2.0 and total and done >= max(1, total // 20) and done < total:
        left = elapsed * (total - done) / done
        text += (f", roughly {round(left)} s left at this rate" if left < 90
                 else f", roughly {round(left / 60)} min left at this rate")
    if count > limit // 2 and done < total // 2:
        text += (f" — {count / limit:.0%} of the limit used with "
                 f"{done:,} of {total:,} junctions done; unlikely to finish "
                 "within it")
    return text


@dataclass
class SizeRisk:
    """A junction whose inputs share blocks (see `size_outlook`)."""

    name: str
    k: int
    inputs: int
    shared: int

    @property
    def threshold(self) -> bool:
        """k-of-n with 1 < k < n: the case that grows fastest."""
        return 1 < self.k < self.inputs


@dataclass
class SizeOutlook:
    """What the structure says about the diagram's size, before building."""

    elements: int
    risks: list[SizeRisk] = field(default_factory=list)

    @property
    def risky(self) -> bool:
        return bool(self.risks)

    def text(self) -> str:
        if not self.risks:
            return ("no junction's inputs share blocks (apart from a "
                    "common element ahead of them, which is factored out), "
                    "so the diagram grows with the size of the model, not "
                    "exponentially.")
        worst = self.risks[:3]
        named = "; ".join(
            f"{r.name} ({r.k} of {r.inputs}, inputs share {r.shared} "
            f"block{'s' if r.shared != 1 else ''})" for r in worst)
        more = (f" and {len(self.risks) - 3} more" if len(self.risks) > 3
                else "")
        kind = ("k-of-n junctions whose inputs share blocks"
                if worst[0].threshold else
                "junctions whose inputs share blocks")
        return (f"the diagram may grow large: {kind}: {named}{more}. "
                "Solving the hierarchy that holds them on its own usually "
                "helps most.")


def size_outlook(model: SimulationModel, order: list,  # noqa: C901
                 incoming: dict, block_in: dict, scope: Scope,
                 idom: dict | None = None) -> SizeOutlook:
    """Find the junctions whose inputs share upstream blocks.

    Cheap (bit masks) and done before the BDD is built. Blocks that every
    input reaches through one element (the junction's dominator) are not
    counted: the build factors them out. If no junction's inputs share
    anything else, every merge combines independent parts and the diagram
    stays small. A junction whose inputs do share blocks is where it can
    grow, fastest for k-of-n with 1 < k < n. This is a warning sign, not a
    forecast: the exact size is known only by building.
    """
    bit = {key: 1 << i for i, key in enumerate(order)}
    memo: dict[tuple[tuple[str, int], tuple[str, int] | None], int] = {}
    idom = idom or {}

    def upstream(kind: str, ident: int, stop: tuple[str, int] | None) -> int:
        key = (kind, ident)
        if key == stop:
            return 0
        got = memo.get((key, stop))
        if got is not None:
            return got
        memo[(key, stop)] = 0    # cycle guard; build() refuses cycles itself
        if kind == "RBDNode":
            mask = 0
            for link in incoming.get(ident, []):
                mask |= upstream(*_link_source(link), stop)
        else:
            mask = bit.get(key, 0)
            link = block_in.get(key)
            if link is not None:
                mask |= upstream(*_link_source(link), stop)
        memo[(key, stop)] = mask
        return mask

    risks: list[SizeRisk] = []
    with recursion_headroom():
        for node_id, links in incoming.items():
            if len(links) < 2:
                continue
            seen = dup = 0
            stop = idom.get(("RBDNode", node_id))
            for link in links:
                mask = upstream(*_link_source(link), stop)
                dup |= seen & mask
                seen |= mask
            if not dup:
                continue
            node = model.nodes_by_id.get(node_id)
            if node is None:
                continue
            k = max(1, _node_k(node, scope, len(links)))
            risks.append(SizeRisk(
                name=node.node_name or f"node {node_id}", k=k,
                inputs=len(links), shared=bin(dup).count("1")))
    risks.sort(key=lambda r: (not r.threshold, -r.shared * r.inputs, r.name))
    return SizeOutlook(elements=len(order), risks=risks)


def _index_links(model: SimulationModel):
    incoming: dict[int, list] = {}
    block_in: dict[tuple[str, int], object] = {}
    for link in model.links_by_id.values():
        target_kind = link.resolved_to_kind or link.connect_t_ref
        target_id = (link.resolved_to_id if link.resolved_to_kind
                     else link.connect_to_id)
        if target_kind == "RBDNode":
            incoming.setdefault(target_id, []).append(link)
        elif target_kind in ("RBDBlock", "RBDEvent"):
            key = (target_kind, target_id)
            if key in block_in:
                raise NotApplicable(
                    f"{target_kind} {target_id} has more than one input link")
            block_in[key] = link
        elif target_kind == "RBDHier":
            raise NotApplicable(
                f"link {link.object_num} enters a hierarchy but was not "
                "resolved to its internal node")
        else:
            raise NotApplicable(
                f"link {link.object_num} has unknown target {target_kind!r}")
    return incoming, block_in


def _link_source(link) -> tuple[str, int]:
    kind = link.resolved_from_kind or link.connect_f_ref
    ident = link.resolved_from_id if link.resolved_from_kind else link.connect_from_id
    if kind == "RBDHier":
        raise NotApplicable(
            f"link {link.object_num} leaves a hierarchy but was not resolved "
            "to its internal node")
    return kind, ident


def _link_target(link) -> tuple[str, int]:
    kind = link.resolved_to_kind or link.connect_t_ref
    ident = (link.resolved_to_id if link.resolved_to_kind
             else link.connect_to_id)
    if kind == "RBDHier":
        raise NotApplicable(
            f"link {link.object_num} enters a hierarchy but was not resolved "
            "to its internal node")
    return kind, ident


#: Which variable order the BDD uses (see `_variable_index`).
VARIABLE_ORDER = "backward"


def _variable_index(order: list[tuple[str, int]], end: int, incoming: dict,
                    block_in: dict) -> dict[tuple[str, int], int]:
    """BDD index of each variable (0 = top of the diagram).

    "backward" (the default) numbers variables in the order the condition
    walk meets them going back from End, each element before what feeds
    it. A sub-network's variables then sit together, and an element sits
    above everything upstream of it, so ANDing a junction's local part with
    its dominator's condition stays cheap. This is the usual depth-first
    ordering heuristic for fault-tree BDDs. "forward" is the earlier order:
    first encounter going forward from Start, reversed. Variables only
    reachable going forward (dead ends) go last.
    """
    if VARIABLE_ORDER == "forward":
        return {key: len(order) - 1 - i for i, key in enumerate(order)}
    wanted = set(order)
    out: dict[tuple[str, int], int] = {}
    seen_nodes: set[int] = set()
    stack: list[tuple[str, int]] = [("RBDNode", end)]
    while stack:
        kind, ident = stack.pop()
        if kind == "RBDNode":
            if ident in seen_nodes:
                continue
            seen_nodes.add(ident)
            links = incoming.get(ident, [])
            stack.extend(_link_source(link) for link in reversed(links))
            continue
        key = (kind, ident)
        if key in out or key not in wanted:
            continue
        out[key] = len(out)
        link = block_in.get(key)
        if link is not None:
            stack.append(_link_source(link))
    for key in order:
        if key not in out:
            out[key] = len(out)
    return out


def _variable_order(model: SimulationModel,  # noqa: C901
                    start: int, *,
                    max_variables: int = MAX_VARIABLES) -> list[tuple[str, int]]:
    """Blocks/events in depth-first first-encounter order."""
    outgoing: dict[int, list] = {}
    outputs: dict[tuple[str, int], list] = {}
    for link in model.links_by_id.values():
        source_kind, source_id = _link_source(link)
        if source_kind == "RBDNode":
            outgoing.setdefault(source_id, []).append(link)
        elif source_kind in ("RBDBlock", "RBDEvent"):
            outputs.setdefault((source_kind, source_id), []).append(link)

    order: list[tuple[str, int]] = []
    seen_elements: set[tuple[str, int]] = set()
    seen_nodes: set[int] = set()

    def follow(link) -> None:
        target_kind, target_id = _link_target(link)
        if target_kind == "RBDNode":
            visit_node(target_id)
        elif target_kind in ("RBDBlock", "RBDEvent"):
            key = (target_kind, target_id)
            if key not in seen_elements:
                seen_elements.add(key)
                order.append(key)
                if len(order) > max_variables:
                    raise NotApplicable(
                        f"the static solver supports at most {max_variables} "
                        f"blocks/events; this model exceeds that")
            for out in outputs.get(key, []):
                follow(out)
        else:
            raise NotApplicable(
                f"link {link.object_num} has unknown target {target_kind!r}")

    def visit_node(node_id: int) -> None:
        if node_id in seen_nodes:
            return
        seen_nodes.add(node_id)
        for link in outgoing.get(node_id, []):
            follow(link)

    visit_node(start)
    return order


def _mean_or_inf(code: int, params: list[float]) -> float:
    """A mean time, or infinity when the distribution has no finite mean."""
    value = survival.mean(code, params)
    return math.inf if value is None else value


def _scope(model: SimulationModel, mode: str, ignore_repairs: bool,
           ignore: Collection[str], phase: int | None,
           standby: str | None,
           dependencies: str | None = None) -> Scope:
    chosen = {str(item).lower() for item in ignore}
    if ignore_repairs:
        chosen.add("repairs")
    unknown = chosen - set(IGNORABLE)
    if unknown:
        raise ValueError(
            f"cannot ignore {sorted(unknown)}; choose from {list(IGNORABLE)}")
    if mode == "availability":
        chosen.discard("repairs")   # availability is defined by repairs
    if standby is not None and standby not in STANDBY_MODES:
        raise ValueError(f"standby must be one of {list(STANDBY_MODES)}")
    if dependencies is not None and dependencies not in DEPENDENCY_MODES:
        raise ValueError(
            f"dependencies must be one of {list(DEPENDENCY_MODES)}")
    if phase is not None:
        if not model.phases:
            raise NotApplicable("the model has no phases to choose from")
        if not 1 <= phase <= len(model.phases):
            raise ValueError(
                f"phase must be between 1 and {len(model.phases)}")
    return Scope(frozenset(chosen), phase, standby, dependencies)


def _node_k(node, scope: Scope, inputs: int) -> int:
    """The good-paths requirement of ``node`` in the analysed phase.

    A phase value of -1 is the engine's "impossible" sentinel (more paths
    than the node has).
    """
    if scope.phase is not None and node.uses_phasing and node.phase:
        index = scope.phase - 1
        if index < len(node.phase):
            value = node.phase[index]
            return inputs + 1 if value == -1 else value
    return node.good_paths


def _split_standby(node, links: list, k: int):
    """Split a cold node's input links into the first ``k`` by switching
    priority (in service) and the rest (standby)."""
    order = {lid: pos for pos, lid in enumerate(node.cold_links)}
    ranked = sorted(links, key=lambda link: order.get(link.object_num, -1))
    return ranked[:k], ranked[k:]


_IGNORE_NOTES = {
    "repairs": "repair distributions were ignored: blocks are treated as "
               "non-repairable",
    "pm": "preventive maintenance was ignored: results do not include PM "
          "downtime or age renewal",
    "spares": "spares were ignored: a replacement part is assumed always "
              "available at once",
    "resources": "resources were ignored: crews and tools are assumed "
                 "always available",
}


def _scope_notes(model: SimulationModel, scope: Scope, mode: str,
                 infos: list[BlockInfo]) -> list[str]:
    notes = [_IGNORE_NOTES[item] for item in IGNORABLE
             if item in scope.ignore]
    if scope.phase is not None:
        phase = model.phases[scope.phase - 1]
        label = phase.phase_name or f"phase {scope.phase}"
        notes.append(
            f"analysed in phase {scope.phase} ({label}) for the whole time; "
            "phase types and values were applied to each element")
    if scope.standby == "active":
        notes.append(
            "standby paths are treated as fully in service; switching "
            "time and switch failures are ignored")
    elif scope.standby == "absent":
        count = sum(1 for info in infos if "absent" in info.state)
        notes.append(
            f"standby paths are treated as absent ({count} element(s) "
            "removed); switching is ignored")
    if scope.dependencies == "up":
        notes.append(
            "dependencies are treated as up: every dependent block or node "
            "is analysed as if the depended-upon element were always up")
    elif scope.dependencies == "down":
        count = sum(1 for info in infos
                    if info.kind == "block" and info.forced_down
                    and has_dependency(model.blocks_by_id[info.id]))
        count += sum(1 for node in model.nodes_by_id.values()
                     if has_node_dependency(node))
        notes.append(
            f"dependencies are treated as down: {count} dependent "
            "block(s)/node(s) are forced out of the success path")
    return notes


def _block_info(block: RBDBlock, variable: int, *, mode: str,  # noqa: C901
                scope: Scope) -> BlockInfo:
    perfect = block.fail_distro == int(RngDistribution.NONE)
    duty = block.duty_cycle
    forced_down = False
    state = ""
    phase = _phase_of(block, scope)
    if phase is not None:
        kind, value = phase
        if kind == "C":
            forced_down, state = True, "cut in this phase"
        elif kind == "L":
            perfect, state = True, "linked (bypassed) in this phase"
        elif value != 1.0:
            duty = block.duty_cycle * value
            state = f"stress x{value:g} in this phase"
            if duty <= 0.0:
                perfect = True
                state = "does not age in this phase"
    if has_dependency(block):
        if scope.dependencies == "down":
            forced_down = True
            # A phase cut already says why this element is out; otherwise
            # the dependency is the reason.
            if state != "cut in this phase":
                state = "dependency forced down"
        elif scope.dependencies == "up" and not state and not forced_down:
            state = "dependency ignored (element treated as independent)"
    repairable = block.repair_distro != int(RngDistribution.NONE)
    mean_fail = None if perfect else survival.mean(block.fail_distro,
                                                   block.fail_values)
    mean_repair = (survival.mean(block.repair_distro, block.repair_values)
                   if repairable else None)
    mean_pre = 0.0
    mean_post = 0.0
    if repairable:
        if block.pre_dist not in (int(RngDistribution.NONE),):
            mean_pre = _mean_or_inf(block.pre_dist, block.pre_params)
        if block.post_dist not in (int(RngDistribution.NONE),):
            mean_post = _mean_or_inf(block.post_dist, block.post_params)
    return BlockInfo(
        id=block.id,
        name=block.name,
        kind="block",
        variable=variable,
        fail_code=block.fail_distro,
        fail_params=list(block.fail_values),
        repair_code=block.repair_distro,
        repair_params=list(block.repair_values),
        pre_code=block.pre_dist,
        pre_params=list(block.pre_params),
        post_code=block.post_dist,
        post_params=list(block.post_params),
        perfect=perfect,
        repairable=repairable,
        mean_fail=(mean_fail / duty
                   if mean_fail is not None and duty > 0 else None),
        mean_repair=mean_repair,
        mean_pre=mean_pre,
        mean_post=mean_post,
        duty_cycle=duty if duty > 0 else block.duty_cycle,
        start_age=block.start_age,
        forced_down=forced_down,
        state=state,
    )


def _event_info(event: RBDEvent, variable: int, *, scope: Scope) -> BlockInfo:
    probability = float(event.fail_mean)
    state = ""
    phase = _phase_of(event, scope)
    forced_down = False
    if phase is not None:
        kind, value = phase
        if kind == "A":
            probability, state = value, "probability set by this phase"
        elif kind == "C":
            forced_down, state = True, "cut in this phase"
        elif kind == "L":
            probability, state = 1.0, "linked (bypassed) in this phase"
    if not (0.0 <= probability <= 1.0):
        raise NotApplicable(
            f"event {event.id} {event.name!r} has success probability "
            f"{probability!r}, not in [0, 1]")
    return BlockInfo(
        id=event.id,
        name=event.name,
        kind="event",
        variable=variable,
        fail_code=event.fail_distro,
        fail_params=[probability],
        repair_code=int(RngDistribution.NONE),
        repair_params=[],
        pre_code=int(RngDistribution.FIXED),
        pre_params=[0.0],
        post_code=int(RngDistribution.FIXED),
        post_params=[0.0],
        event_success=probability,
        forced_down=forced_down,
        state=state,
    )

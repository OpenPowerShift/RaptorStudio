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

from dataclasses import dataclass, field

from raptor.core.engine.load import SimulationModel
from raptor.core.model import ActiveStatus, RBDBlock, RBDEvent
from raptor.core.rng import Distribution as RngDistribution

from . import survival
from .bdd import BDD, TRUE
from .errors import NotApplicable

#: Engine values that mean "no spare constraint".
_INFINITE_SPARING = "INFINITE"
_NONE_SPARING = "NONE"

#: The BDD probability walk is recursive; keep well inside Python's default
#: recursion limit. Practical models are far smaller; this is a refusal, not
#: an approximation.
MAX_VARIABLES = 750


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
        if self.perfect:
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

    @property
    def node_count(self) -> int:
        return self.bdd.node_count

    def probabilities(self, t: float) -> list[float]:
        """Variable probabilities at time ``t`` for this build's mode."""
        if self.mode == "availability":
            return [info.availability for info in self._ordered()]
        return [info.reliability(t) for info in self._ordered()]

    def derivatives(self, t: float) -> list[float]:
        """dp_i/dt for reliability mode (events and perfect blocks are 0)."""
        if self.mode != "reliability":
            return [0.0] * len(self.variables)
        out: list[float] = []
        for info in self._ordered():
            if info.perfect or info.event_success is not None:
                out.append(0.0)
            else:
                density = info.density(t)
                out.append(0.0 if density is None else -density)
        return out

    def _ordered(self) -> list[BlockInfo]:
        """Variables in BDD index order."""
        return self.variables


def build(model: SimulationModel, *, mode: str = "reliability",  # noqa: C901, PLR0912, PLR0915
          ignore_repairs: bool = False) -> BuildResult:
    """Build the success BDD for ``model``.

    Raises `NotApplicable` when the model uses a construct the static solver
    cannot represent. The first offending element is named in the message.
    """
    if mode not in ("reliability", "availability"):
        raise ValueError(f"unknown analytical mode {mode!r}")
    _gate(model, mode=mode, ignore_repairs=ignore_repairs)

    start = _one_node(model, 1, "Start")
    end = _one_node(model, 3, "End")
    incoming, block_in = _index_links(model)

    # Variable order is first encounter on a depth-first walk from Start.
    order = _variable_order(model, start, block_in)
    if len(order) > MAX_VARIABLES:
        raise NotApplicable(
            f"the static solver supports at most {MAX_VARIABLES} blocks/"
            f"events; this model has {len(order)}. Use simulation.")

    bdd = BDD()
    var_index = {key: i for i, key in enumerate(order)}
    x = {key: bdd.variable(i) for key, i in var_index.items()}

    node_memo: dict[int, int] = {}
    visiting: set[int] = set()

    def input_condition(kind: str, ident: int) -> int:
        link = block_in.get((kind, ident))
        if link is None:
            raise NotApplicable(
                f"{kind} {ident} has no input link; the analytical solver "
                "needs a single Start-to-End path")
        source_kind, source_id = _link_source(link)
        if source_kind == "RBDNode":
            return node_condition(source_id)
        if source_kind in ("RBDBlock", "RBDEvent"):
            return bdd.And(input_condition(source_kind, source_id),
                           x[(source_kind, source_id)])
        raise NotApplicable(
            f"link {link.object_num} has an unresolved {source_kind} source")

    def branch_condition(link) -> int:
        source_kind, source_id = _link_source(link)
        if source_kind == "RBDNode":
            return node_condition(source_id)
        if source_kind in ("RBDBlock", "RBDEvent"):
            return bdd.And(input_condition(source_kind, source_id),
                           x[(source_kind, source_id)])
        raise NotApplicable(
            f"link {link.object_num} has an unresolved {source_kind} source")

    def node_condition(node_id: int) -> int:
        got = node_memo.get(node_id)
        if got is not None:
            return got
        if node_id in visiting:
            raise NotApplicable(
                f"node {node_id} is part of a cycle; the analytical solver "
                "needs an acyclic RBD")
        visiting.add(node_id)
        node = model.nodes_by_id[node_id]
        if node.type_node == 1:
            out = TRUE
        else:
            branches = [branch_condition(link)
                        for link in incoming.get(node_id, [])]
            k = node.good_paths
            if node.type_node == 3 or k <= 0:
                k = 1
            out = bdd.threshold(branches, k)
        visiting.remove(node_id)
        node_memo[node_id] = out
        return out

    root = node_condition(end)

    infos: list[BlockInfo] = []
    for key in order:
        kind, ident = key
        if kind == "RBDBlock":
            infos.append(_block_info(model.blocks_by_id[ident],
                                     var_index[key], mode=mode))
        else:
            infos.append(_event_info(model.events_by_id[ident],
                                     var_index[key]))

    warnings: list[str] = []
    if model.capacity_analysis:
        warnings.append(
            "capacity analysis is ignored: success is the End node being up, "
            "not a flow target")
    shared = _shared_stream_warning(model)
    if shared:
        warnings.append(shared)
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
        assumptions.append("duty cycle scales effective age; Start=3 start "
                           "ages are conditional")
        if ignore_repairs:
            assumptions.append("repair distributions were explicitly ignored")
    else:
        assumptions.append(
            "steady state, perfect repair, independent alternating-renewal "
            "components")
        assumptions.append("A_i = MTTF_i / (MTTF_i + MTTR_i)")

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


def _gate(model: SimulationModel, *, mode: str, ignore_repairs: bool) -> None:
    if model.phases:
        raise NotApplicable(
            "phased models are outside the analytical solver: the structure "
            "or k-requirement can change by phase")
    if model.startup_failures:
        raise NotApplicable(
            "the model uses the StartUpFaiLures trigger, which is a startup "
            "redraw rule outside the static solver")
    for block in model.blocks_by_id.values():
        _gate_block(block, mode=mode, ignore_repairs=ignore_repairs)
    for node in model.nodes_by_id.values():
        if node.cold_standby or node.cold_links or _has_dep(node):
            raise NotApplicable(
                f"node {node.id} {node.node_name!r} uses cold standby or a "
                "dependency; the analytical solver covers plain success paths")
        if node.active_status != ActiveStatus.ACTIVE:
            raise NotApplicable(
                f"node {node.id} {node.node_name!r} is not Active; phased or "
                "cut structures are outside the analytical solver")
    for event in model.events_by_id.values():
        _gate_event(event)
    for link in model.links_by_id.values():
        if link.switch_status or link.sb_link:
            raise NotApplicable(
                f"link {link.object_num} is a standby/dependency link; the "
                "analytical solver covers plain success paths")


def _gate_block(block: RBDBlock, *, mode: str, ignore_repairs: bool) -> None:  # noqa: C901, PLR0912
    name = f"block {block.id} {block.name!r}"
    if block.active_status != ActiveStatus.ACTIVE:
        raise NotApplicable(f"{name} is not Active")
    if block.uses_phasing or block.phase_type or block.phase_value:
        raise NotApplicable(f"{name} uses phases")
    if _has_dep(block):
        raise NotApplicable(f"{name} has a dependency")
    if block.uses_pm or block.pm_triggered:
        raise NotApplicable(f"{name} uses preventive maintenance")
    if block.cold_dep:
        raise NotApplicable(f"{name} uses cold standby")
    if block.sb_stress not in (0.0, 1.0):
        raise NotApplicable(
            f"{name} has standby stress {block.sb_stress!r}; standby stress "
            "is outside the analytical solver")
    if block.sparing_type.name not in (_NONE_SPARING, _INFINITE_SPARING):
        raise NotApplicable(
            f"{name} uses sparing type {block.sparing_type.value!r}; spare "
            "pools are simulation-only")
    if block.pool_name and block.pool_name.lower() not in ("", "none", "unnamed"):
        raise NotApplicable(
            f"{name} names spare pool {block.pool_name!r}; spare pools are "
            "simulation-only")
    if block.resource_pool_name and block.resource_pool_name.lower() not in ("", "none"):
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
            if not ignore_repairs:
                raise NotApplicable(
                    f"{name} has a repair distribution; reliability mode "
                    "means non-repairable. Pass --ignore-repairs to compute "
                    "the first-failure reliability anyway")
            return
    else:  # availability
        if block.fail_distro == int(RngDistribution.NONE):
            return
        if block.restoration_factor not in (0.0, 1.0):
            raise NotApplicable(
                f"{name} has restoration factor "
                f"{block.restoration_factor!r}; availability mode assumes "
                "perfect repair (factor 0 or 1)")
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


def _has_dep(item) -> bool:
    '''True when a block/node names a real dependency (not the string ``None``).'''
    dep_type = str(getattr(item, 'dep_type', '') or '').strip().lower()
    return bool(getattr(item, 'dependency_num', 0)
                or getattr(item, 'has_dep_objects', False)
                or getattr(item, 'cold_dep', False)
                or dep_type not in ('', 'none'))


def _gate_event(event: RBDEvent) -> None:
    name = f"event {event.id} {event.name!r}"
    if event.active_status != ActiveStatus.ACTIVE:
        raise NotApplicable(f"{name} is not Active")
    if event.uses_phasing or event.phase_type or event.phase_value:
        raise NotApplicable(f"{name} uses phases")
    if event.fail_distro != int(RngDistribution.BERNOULLI):
        raise NotApplicable(
            f"{name} uses failure distribution code {event.fail_distro}; "
            "only Bernoulli events are static")


# ---------------------------------------------------------------------------
# Graph indexing
# ---------------------------------------------------------------------------

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


def _variable_order(model: SimulationModel, start: int,  # noqa: C901
                    block_in: dict[tuple[str, int], object]) -> list[tuple[str, int]]:
    """Blocks/events in depth-first first-encounter order."""
    del block_in  # kept in the signature for the caller's readability
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
                if len(order) > MAX_VARIABLES:
                    raise NotApplicable(
                        f"the static solver supports at most {MAX_VARIABLES} "
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


def _shared_stream_warning(model: SimulationModel) -> str | None:
    """Warn when the simulation's streams are shared across elements.

    The solver assumes independence; Raptor's numbers >= 200 select one shared
    system stream, so two elements on the same stream are correlated in a
    simulation even though the solver treats them as independent.
    """
    users: dict[int, set[tuple[str, int]]] = {}
    for block in model.blocks_by_id.values():
        for stream in (block.fail_stream, block.repair_stream):
            if stream >= 200:
                users.setdefault(stream, set()).add(("block", block.id))
    for event in model.events_by_id.values():
        if event.fail_stream >= 200:
            users.setdefault(event.fail_stream, set()).add(
                ("event", event.id))
    shared = sorted(stream for stream, elements in users.items()
                    if len(elements) > 1)
    if not shared:
        return None
    streams = ", ".join(str(stream) for stream in shared)
    return (f"shared random stream(s) {streams} are used by more than one "
            "element; the analytical solver assumes independent components")


def _block_info(block: RBDBlock, variable: int, *, mode: str) -> BlockInfo:
    perfect = block.fail_distro == int(RngDistribution.NONE)
    repairable = block.repair_distro != int(RngDistribution.NONE)
    mean_fail = None if perfect else survival.mean(block.fail_distro,
                                                   block.fail_values)
    mean_repair = (survival.mean(block.repair_distro, block.repair_values)
                   if repairable else None)
    mean_pre = 0.0
    mean_post = 0.0
    if repairable:
        if block.pre_dist not in (int(RngDistribution.NONE),):
            mean_pre = survival.mean(block.pre_dist, block.pre_params) or 0.0
        if block.post_dist not in (int(RngDistribution.NONE),):
            mean_post = survival.mean(block.post_dist, block.post_params) or 0.0
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
        mean_fail=(mean_fail / block.duty_cycle
                   if mean_fail is not None and block.duty_cycle > 0 else None),
        mean_repair=mean_repair,
        mean_pre=mean_pre,
        mean_post=mean_post,
        duty_cycle=block.duty_cycle,
        start_age=block.start_age,
    )


def _event_info(event: RBDEvent, variable: int) -> BlockInfo:
    probability = float(event.fail_mean)
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
    )

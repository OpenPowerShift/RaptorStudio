"""Load a parsed `.rbd` file into the model objects a run consumes.

`load_model` turns an `RbdFile` into a `SimulationModel`: the typed blocks,
events, nodes, hierarchies, links, pools, phases and triggers, with the EFPA
graph and (when enabled) the capacity graph built over them. The model is
read-only configuration for the per-run drivers, apart from the live status
fields a run updates in place.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from raptor.core.engine.capacity import build_capacity_graph
from raptor.core.engine.cold_standby import sort_cold_links
from raptor.core.engine.errors import UnrunnableModel
from raptor.core.engine.model_checks import ModelProblem, refuse_unrunnable
from raptor.core.engine.graph import build_efpa_graph
from raptor.core.fileio.rbd import RbdFile
from raptor.core.fileio.typed import (
    block_from_row, event_from_row, hier_from_row, link_from_row, node_from_row, phase_from_row,
    resource_pool_from_row, spare_pool_from_row, system_config_from_row, trigger_from_row,
)
from raptor.core.model import Link, Phase, RBDBlock, RBDEvent, RBDHier, RBDNode, RBDTrigger, SparePool


@dataclass
class SimulationModel:
    """Everything `load_model` derives from a loaded `.rbd` file, ready to
    hand to `run_simulation`."""

    blocks_by_id: dict[int, RBDBlock]
    events_by_id: dict[int, RBDEvent]
    nodes_by_id: dict[int, RBDNode]
    hiers_by_id: dict[int, RBDHier]
    links_by_id: dict[int, Link]
    num_runs: int
    run_length: float
    termination_type: str
    sys_streams: list[int]
    resource_pools: dict[str, SparePool]
    spare_pools: dict[str, SparePool]
    triggers: list[RBDTrigger]
    phases: list[Phase]
    cost_analysis: bool = False
    weak_analysis: bool = False
    # --- capacity analysis ---
    capacity_analysis: bool = False
    flow_generated: int = 0
    system_red_cost: float = 0.0
    lost_flow_unit_cost: float = 0.0
    #: `startUpFailures`: TRUE when a trigger named exactly
    #: "StartUpFaiLures" exists (a magic-name global flag). Gates the
    #: startup-redraw branches in `Run` and `BackFlowDependencies`.
    startup_failures: bool = False
    #: `StartStats` (`SYSTEM_DATA`) -- `UnitsProduced`'s integration base.
    stats_start_time: float = 0.0
    #: `dZeroFail`: do not count a system failure at time zero (SYSTEM_DATA
    #: `IgnoreStartFail`; Raptor 7).
    ignore_start_fail: bool = False
    #: What the file itself carries: the system comment `The defer trigger is <name>` (read at
    #: Raptor 7), or "". Not what a run uses -- see `defer_trigger`.
    saved_defer_trigger: str = ""
    #: `deferTrig`: the trigger a block commented `maintDefer` waits for after a natural failure
    #: "" = the feature is off, which is Raptor 7's state unless its
    #: master switch is on: the Simulation dialog's OK button always overwrites `deferTrig` from
    #: its (hidden) trigger combo. Chosen per run; see `resolve_defer_trigger`.
    defer_trigger: str = ""
    start_node_id: int | None = None
    end_node_id: int | None = None
    #: `capNodeGroup`/`capLinkGroup` (build order), empty unless
    #: `capacity_analysis`.
    cap_node_group: list[RBDNode] = field(default_factory=list)
    cap_link_ids: list[int] = field(default_factory=list)


_DEFER_PREFIX = "The defer trigger is "


def saved_defer_trigger(rbd_file: RbdFile) -> str:
    """The trigger the file's system comment names: a comment whose
    first 21 characters are `The defer trigger is ` -- the rest is the name. """
    section = rbd_file.sections.get("COMMENTS")
    for row in (section.rows if section is not None else []):
        if row.get("Type") == "System":
            comment = row.get("Comment", "")
            if len(comment) >= len(_DEFER_PREFIX) and comment.startswith(_DEFER_PREFIX):
                return comment[len(_DEFER_PREFIX):]
    return ""


def resolve_defer_trigger(rbd_file: RbdFile, choice: str) -> str:
    """The run's `deferTrig` from the `defer_trigger` developer option: "" is off; `file`
    is what Raptor 7's combo shows on opening (the file's saved trigger, else the first
    trigger, "" if the model has none); anything else names the trigger."""
    choice = (choice or "").strip()
    if choice.lower() != "file":
        return choice
    saved = saved_defer_trigger(rbd_file)
    if saved:
        return saved
    rows = rbd_file.sections["TRIGGERS"].rows
    return rows[0].get("TriggerName", "") if rows else ""


def load_model(rbd_file: RbdFile, *, defer_trigger: str = "",
               progress: Callable[[str, int, int], None] | None = None) -> SimulationModel:
    """Load a parsed `.rbd` file's `BLOCK_DATA`/`EVENT_DATA`/`NODE_DATA`/
    `HIERARCHY_DATA`/`LINK_DATA`/`SYSTEM_DATA` sections into ready-to-run
    model objects and build the EFPA graph over them. Skips the mandatory
    `ID=0` `BLOCK_DATA` sentinel row present in every Raptor 7 file.

    Raises `NotImplementedError` for `termination_type != "TimeSim"` unless
    it is a FailSim events-only model.

    `progress(stage, done, total)` reports the slow part of set-up (see
    `build_efpa_graph`)."""
    blocks_by_id: dict[int, RBDBlock] = {}
    for row in rbd_file.sections["BLOCK_DATA"].rows:
        if row.get("ID") == "0":
            continue
        block = block_from_row(row)
        if block.id in blocks_by_id:
            # A duplicate ID would silently drop a block (ReleaseProbe4 ran
            # 3 of its 5 blocks for three weeks this way). Raptor 7's
            #own file loader has no such repair; refuse rather than guess.
            raise ValueError(
                f"BLOCK_DATA has a duplicate ID {block.id} ({blocks_by_id[block.id].name!r} "
                f"and {block.name!r}) -- see raptor.core.fileio.builder.validate()")
        blocks_by_id[block.id] = block
    # Block comments: only `maintDefer` is read by the engine.
    _comments = rbd_file.sections.get("COMMENTS")
    for row in (_comments.rows if _comments is not None else []):
        if row.get("Type") == "block":
            try:
                target = blocks_by_id.get(int(row.get("ID", "")))
            except ValueError:
                continue
            if target is not None:
                target.comment = row.get("Comment", "")

    events_by_id = {event_from_row(row).id: event_from_row(row) for row in rbd_file.sections["EVENT_DATA"].rows}
    # A model with both blocks and events is supported: source runs its
    # block `GetStartCond`/`SetToInitValues` loop first, then a separate
    # event `SetToInitValues` loop, matching the
    # block-then-event kickoff order used in `run_simulation`.

    nodes_by_id = {node_from_row(row).id: node_from_row(row) for row in rbd_file.sections["NODE_DATA"].rows}
    hiers_by_id = {hier_from_row(row).id: hier_from_row(row) for row in rbd_file.sections["HIERARCHY_DATA"].rows}
    links_by_id: dict[int, Link] = {}
    # Raptor 7 renumbers LINKS 1..N on load, whatever ids the file holds
    # (block/node/event/hierarchy ids are kept). Evidence, 2026-09-23:
    # `LinkRenumberProbe` (HierarchyExample with every link id shifted to
    # 101..130) produced an event log byte-identical to HierarchyExample's own
    # Raptor 7 capture -- node comment strings show `_df15`, not `_df115` -- and
    # `HierDegradeProbe`'s gapped ids (a deleted link 2) were closed up the
    # same way. Numbered in FILE order: no real-authored file has link ids out
    # of ascending order, so file order vs. sorted-id order is undistinguished
    # by any capture. Link ids reach the event log (node comment strings) and
    # tie-breaks that pick the lowest id (`graph._drop_surplus_outgoing`).
    for new_id, row in enumerate(rbd_file.sections["LINK_DATA"].rows, 1):
        link = link_from_row(row)
        link.object_num = new_id
        links_by_id[link.object_num] = link

    build_efpa_graph(nodes_by_id, blocks_by_id, events_by_id, hiers_by_id, links_by_id,
                     progress=progress)

    # Port of the kickoff-time `IdAndSortColds` call
    # The sort is deterministic over static `cold_priority`
    # values; nodes without priority return are re-sorted per run at
    # kickoff, undoing `ReOrderColds` (`nodes.set_link_status_mirrored`).
    for node in nodes_by_id.values():
        if node.cold_standby:
            sort_cold_links(node, links_by_id)

    refuse_unrunnable(blocks_by_id, nodes_by_id, links_by_id)

    system_config = system_config_from_row(rbd_file.sections["SYSTEM_DATA"].rows[0])
    # FailSim (termType=2) is ported (`_Cascade`'s term_counter and
    # `_end_sim_process`); termType=3 (cycles) is supported only with
    # phases. Raptor 7 refuses a cycle-terminated model with no phases defined,
    # so mirror that rejection here instead of hanging on an end condition
    # that never fires.
    if system_config.termination_type == "CycleSim" and not rbd_file.sections["PHASES"].rows:
        raise UnrunnableModel([ModelProblem(
            "system",
            "the run is terminated by cycles but no phases are defined. Raptor 7 refuses this "
            "model too ('cycle terminated without phases defined'). Add phases, or end the run by "
            "time or failures.")])

    resource_pools = {
        pool.pool_name: pool
        for pool in (resource_pool_from_row(row) for row in rbd_file.sections["RESOURCE_DATA"].rows)
    }
    # Cost-analysis rates resolved onto each borrowing block once per
    # model (`PoolArray[resPoolNum].fixedPerUse`/`.costPerTime`, reached via
    # the block's own `resource_pool_name`).
    for block in blocks_by_id.values():
        if block.resource_type_count == 1:
            cfg = resource_pools.get(block.resource_pool_name)
            if cfg is not None:
                block.res_fixed_per_use = cfg.fixed_per_use
                block.res_cost_per_time = cfg.cost_per_time
    spare_pools = {
        pool.pool_name: pool
        for pool in (spare_pool_from_row(row) for row in rbd_file.sections["SPARE_POOL_DATA"].rows)
    }
    triggers = [trigger_from_row(row) for row in rbd_file.sections["TRIGGERS"].rows]
    phases = [phase_from_row(row) for row in rbd_file.sections["PHASES"].rows]

    # Capacity analysis graph build. Requires a Start (type 1) and End
    # (type 3) node -- flow enters and exits there; `ValidateRBD` enforces
    # this upstream in the Raptor 7 app.
    start_node_id: int | None = None
    end_node_id: int | None = None
    cap_node_group: list[RBDNode] = []
    cap_link_ids: list[int] = []
    if system_config.capacity_analysis:
        start_node_id = next((n.id for n in nodes_by_id.values() if n.type_node == 1), None)
        end_node_id = next((n.id for n in nodes_by_id.values() if n.type_node == 3), None)
        if start_node_id is None or end_node_id is None:
            raise UnrunnableModel([ModelProblem(
                "system",
                "capacity analysis needs a Start node and an End node; this model has "
                + ("neither" if start_node_id is None and end_node_id is None
                   else "no Start node" if start_node_id is None else "no End node")
                + ". Add them, or turn capacity analysis off.")])
        cap_node_group, cap_link_ids = build_capacity_graph(
            nodes_by_id, blocks_by_id, events_by_id, hiers_by_id, links_by_id,
            system_config.flow_generated,
        )

    return SimulationModel(
        blocks_by_id=blocks_by_id, events_by_id=events_by_id, nodes_by_id=nodes_by_id, hiers_by_id=hiers_by_id,
        links_by_id=links_by_id, num_runs=system_config.num_runs, run_length=system_config.term_condition,
        termination_type=system_config.termination_type,
        sys_streams=system_config.sys_streams, resource_pools=resource_pools, spare_pools=spare_pools,
        triggers=triggers, phases=phases,
        cost_analysis=system_config.cost_analysis, weak_analysis=system_config.weak_analysis,
        capacity_analysis=system_config.capacity_analysis,
        flow_generated=system_config.flow_generated,
        system_red_cost=system_config.system_red_cost,
        lost_flow_unit_cost=system_config.lost_flow_unit_cost,
        startup_failures=any(t.trigger_name == "StartUpFaiLures" for t in triggers),
        stats_start_time=system_config.start_stats,
        ignore_start_fail=system_config.ignore_start_fail,
        start_node_id=start_node_id, end_node_id=end_node_id,
        cap_node_group=cap_node_group, cap_link_ids=cap_link_ids,
        saved_defer_trigger=saved_defer_trigger(rbd_file), defer_trigger=defer_trigger,
    )

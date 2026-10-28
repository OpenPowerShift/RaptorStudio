"""A run's cost tables (`Cost Analysis`).

`build_cost_run_report` builds the end-of-run `CostRunReport` from the
per-object stats accumulators; `interim_cost_snapshot` builds the same
tables mid-run for the Interim Costs output, charging each object's current
state up to now. Both roll hierarchy costs up through nested hierarchies
with `rollup_nested_hier_costs`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from raptor.core.engine.blocks import BlockStatistics
from raptor.core.engine.events import EventStatistics
from raptor.core.engine.load import SimulationModel
from raptor.core.engine.pool import PoolStats
from raptor.core.engine.reporting import BlockCostRow, CostRunReport, EventCostRow, HierCostRow, PoolCostRow
from raptor.core.engine.system_status import SystemColor, SystemState
from raptor.core.model import ActiveStatus, BlockStatus, RBDBlock, SparePool, SparingType

if TYPE_CHECKING:
    from raptor.core.engine.capacity import CapacityRunReport


def rollup_nested_hier_costs(hiers: dict[int, HierCostRow],
                              model: SimulationModel) -> None:
    """Raptor 7 `mamaHier` rollup: a hierarchy nested inside
    another hierarchy has its accumulated totals added into its parent's row.

    Rows are processed deepest-first so a child is complete before it is
    folded into its own parent, regardless of iteration order."""
    def depth(hid: int) -> int:
        d = 0
        seen = {hid}
        cur = hid
        while True:
            hier = model.hiers_by_id.get(cur)
            pid = hier.parent_id if hier is not None else 0
            if pid == 0 or pid in seen:
                return d
            seen.add(pid)
            cur = pid
            d += 1

    for hid in sorted(hiers, key=depth, reverse=True):
        hier = model.hiers_by_id.get(hid)
        if hier is None or hier.parent_id == 0:
            continue
        parent = hiers.get(hier.parent_id)
        if parent is None:
            continue
        child = hiers[hid]
        parent.purchase += child.purchase
        parent.operating += child.operating
        parent.sparing += child.sparing
        parent.disposal += child.disposal
        parent.events += child.events
        parent.total += child.total


def build_hier_cost_rows(
    model: SimulationModel,
    blocks: dict[int, BlockCostRow],
    events: dict[int, EventCostRow],
) -> dict[int, HierCostRow]:
    """Hierarchy cost rows, rolled up. One owner (review E-04).

    Port of Raptor 7 (blocks) (events):
    every hierarchy first accumulates its own *direct* blocks and events,
    `hc.own` snapshots that pre-nesting figure, and
    `rollup_nested_hier_costs` then folds nested hierarchies into their
    parents.

    This 19-line body was written out twice in this file -- once in
    `build_cost_run_report` and once in `interim_cost_snapshot` --
    differing only in a comment and in where `events_total` was summed. Two
    cost-aggregation entry points that had to agree on the category set and
    the ordering, with nothing enforcing it.

    The totals each caller needs (`pools_total`, `resources_total`,
    `events_total`) are *its own* concern and stay with it: this returns the
    per-hierarchy rows only.
    """
    hiers: dict[int, HierCostRow] = {}
    for seq, (hid, hier) in enumerate(model.hiers_by_id.items(), 1):
        hier_cost_row = HierCostRow(name=hier.name, hier_id=hier.id, purchase=0.0,
                         operating=0.0, sparing=0.0, disposal=0.0, events=0.0,
                         total=0.0, parent_id=hier.parent_id, seq=seq)
        for b in model.blocks_by_id.values():
            if b.parent_id == hid:
                r = blocks[b.id]
                hier_cost_row.purchase += r.purchase
                hier_cost_row.operating += r.operating
                hier_cost_row.sparing += r.sparing
                hier_cost_row.disposal += r.disposal
                hier_cost_row.total += r.total
        for event in events.values():
            if event.parent_id == hid:
                hier_cost_row.events += event.total
                hier_cost_row.total += event.total
        hier_cost_row.own = (hier_cost_row.purchase, hier_cost_row.operating, hier_cost_row.sparing, hier_cost_row.disposal,
                  hier_cost_row.events, hier_cost_row.total)
        hiers[hid] = hier_cost_row
    rollup_nested_hier_costs(hiers, model)
    return hiers


def build_cost_run_report(
    model: SimulationModel,
    stats_by_block: dict[int, BlockStatistics],
    stats_by_event: dict[int, EventStatistics],
    run_pool_stats: dict[str, PoolStats],
    system_state: SystemState,
    capacity_report: CapacityRunReport | None,
    end_time: float,
) -> CostRunReport:
    """Raptor 7 (blocks) (pools/system)
    per-run cost extraction. `ZeroInitCost` stays false through the normal
    flow, so purchase costs are charged every run."""

    def _block_cost_row(block: RBDBlock) -> BlockCostRow:
        stats = stats_by_block[block.id]
        sic = float(block.init_stock) * block.spare_cost if block.sparing_type == SparingType.CUSTOM else 0.0
        sparing = sic + stats.sp_cost + stats.es_cost
        operating = (
            stats.op_cost + stats.rep_cost + stats.idle_cost + stats.hold_cost
            + stats.sb_cost + stats.done_cost + stats.pm_cost + stats.pm_hold_cost
        )
        purchase = 0.0 if system_state.zero_init_cost else block.initial_cost
        total = purchase + operating + sparing + stats.disp_cost
        return BlockCostRow(
            name=block.name,
            init_spare_cost=sic, new_spares_cost=stats.sp_cost, emer_ship_cost=stats.es_cost,
            purchase=purchase, operating=operating, sparing=sparing,
            disposal=stats.disp_cost, total=total,
            op_cost=stats.op_cost, rep_cost=stats.rep_cost, idle_cost=stats.idle_cost,
            hold_cost=stats.hold_cost, pm_cost=stats.pm_cost, pm_hold_cost=stats.pm_hold_cost,
            sb_cost=stats.sb_cost, done_cost=stats.done_cost,
            in_hier=block.parent_id,
            # `IF (block.sparingType<>SparePool)` gate : pooled-
            # sparing blocks are excluded from the Sparing table rows and
            # print N/A in Life_cycle's Sparing column.
            sparing_col=block.sparing_type != SparingType.SPARE_POOL,
        )

    def _pool_row(name: str, cfg: SparePool, is_resource: bool) -> PoolCostRow:
        ps = run_pool_stats.get(name, PoolStats())
        initial = float(cfg.initial_spares) * cfg.spare_cost
        if is_resource:
            # -- the Resources table swaps the middle columns:
            # PerUseCost reads PoolEndESCost, PerTimeCost reads PoolEndAddCost.
            return PoolCostRow(
                name=name, initial=initial, new_spares=ps.add_cost,
                emer_ship=ps.es_cost, total=initial + ps.add_cost + ps.es_cost,
                is_resource=True,
            )
        return PoolCostRow(
            name=name, initial=initial, new_spares=ps.add_cost,
            emer_ship=ps.es_cost, total=initial + ps.add_cost + ps.es_cost,
        )

    blocks = {bid: _block_cost_row(b) for bid, b in model.blocks_by_id.items()}
    spare_pools = {
        name: _pool_row(name, cfg, False)
        for name, cfg in model.spare_pools.items()
    }
    resources = {
        name: _pool_row(name, cfg, True)
        for name, cfg in model.resource_pools.items()
    }
    blocks_total = sum(r.total for r in blocks.values())
    events = {
        eid: EventCostRow(
            name=e.name,
            initial=0.0 if system_state.zero_init_cost else e.initial_cost,
            success=stats_by_event[eid].op_cost,
            failure=stats_by_event[eid].rep_cost,
            total=(0.0 if system_state.zero_init_cost else e.initial_cost)
            + stats_by_event[eid].op_cost + stats_by_event[eid].rep_cost,
            parent_id=e.parent_id,
        )
        for eid, e in model.events_by_id.items()
    }
    events_total = sum(r.total for r in events.values())
    # HierCosts rollup: DELEGATED to `build_hier_cost_rows` (review E-04),
    # which names the Raptor 7 procedure it ports.
    hiers = build_hier_cost_rows(model, blocks, events)
    pools_total = sum(r.total for r in spare_pools.values())
    resources_total = sum(r.total for r in resources.values())
    # Raptor 7 -- capacity analysis only:
    # `AveLostFlow * (SimTime - StatsStartTime) * sysLostCost`.
    lost_flow_cost = (
        capacity_report.ave_lost_flow * max(end_time - model.stats_start_time, 0.0) * model.lost_flow_unit_cost
        if capacity_report is not None else 0.0
    )
    grand = blocks_total + events_total + pools_total + resources_total + system_state.total_red_cost + lost_flow_cost
    return CostRunReport(
        blocks=blocks, spare_pools=spare_pools, resources=resources,
        events=events, hiers=hiers,
        system_down_cost=system_state.total_red_cost,
        red_cost_configured=model.system_red_cost > 0.0,
        system_lost_flow_cost=lost_flow_cost,
        grand_total=grand,
    )


def interim_cost_snapshot(
    model: SimulationModel,
    stats_by_block: dict[int, BlockStatistics],
    stats_by_event: dict[int, EventStatistics],
    run_pool_stats: dict[str, PoolStats],
    system_state: SystemState,
    capacity_report: CapacityRunReport | None,
    sim_time: float,
) -> CostRunReport:
    """`CalculateInterimCost`'s in-progress extraction
    : the per-run accumulators plus each block's current state's
    accrued-but-uncommitted cost, folded into the matching column. Disposal
    stays at whatever the accumulator holds (0.0 mid-run)."""
    blocks: dict[int, BlockCostRow] = {}
    pools_add_delta: dict[str, float] = {}
    for bid, b in model.blocks_by_id.items():
        s = stats_by_block[bid]
        st = max(sim_time - b.state_start_time, 0.0)
        op, rep, idle, hold, pm, pmh, sb, done = (
            s.op_cost, s.rep_cost, s.idle_cost, s.hold_cost,
            s.pm_cost, s.pm_hold_cost, s.sb_cost, 0.0,
        )
        if b.op_status == BlockStatus.IDLE:
            idle += b.idle_cost * st
        elif b.op_status == BlockStatus.REP_HOLD:
            hold += b.rep_hold_cost * st
        elif b.op_status == BlockStatus.REPAIRING:
            rep += b.repairing_cost * st + b.rep_fixed_cost
            if b.resource_type_count == 1:
                pools_add_delta[b.resource_pool_name] = pools_add_delta.get(b.resource_pool_name, 0.0) + (
                    st * b.res_cost_per_time * float(b.repair_resources_needed)
                )
        elif b.op_status == BlockStatus.PM_HOLD:
            pmh += b.pm_hold_cost * st
        elif b.op_status == BlockStatus.PM:
            pm += b.pm_cost * st + b.pm_fixed_cost
            if b.resource_type_count == 1:
                pools_add_delta[b.resource_pool_name] = pools_add_delta.get(b.resource_pool_name, 0.0) + (
                    st * b.res_cost_per_time * float(b.pm_resources_needed)
                )
        elif b.op_status == BlockStatus.RUNNING:
            # Raptor 7: Running+Active is operating, Running+
            # Linked is standby, Running+Cut is idle.
            if b.active_status == ActiveStatus.ACTIVE:
                op += b.operating_cost * st
            elif b.active_status == ActiveStatus.LINKED:
                sb += b.standby_cost * st
            else:
                idle += b.idle_cost * st
        elif b.op_status == BlockStatus.STANDBY:
            sb += b.standby_cost * st
        elif b.op_status == BlockStatus.DONE:
            done = b.done_cost * st
        sic = float(b.init_stock) * b.spare_cost if b.sparing_type == SparingType.CUSTOM else 0.0
        sparing = sic + s.sp_cost + s.es_cost
        operating = op + rep + idle + hold + sb + done + pm + pmh
        total = b.initial_cost + operating + sparing + s.disp_cost
        blocks[bid] = BlockCostRow(
            name=b.name,
            init_spare_cost=sic, new_spares_cost=s.sp_cost, emer_ship_cost=s.es_cost,
            purchase=b.initial_cost, operating=operating, sparing=sparing,
            disposal=s.disp_cost, total=total,
            op_cost=op, rep_cost=rep, idle_cost=idle, hold_cost=hold,
            pm_cost=pm, pm_hold_cost=pmh, sb_cost=sb, done_cost=done,
            in_hier=b.parent_id,
            sparing_col=b.sparing_type != SparingType.SPARE_POOL,
        )
    spare_pools = {}
    resources = {}
    for name, cfg in model.spare_pools.items():
        ps = run_pool_stats[name]
        initial = float(cfg.initial_spares) * cfg.spare_cost
        spare_pools[name] = PoolCostRow(
            name=name, initial=initial, new_spares=ps.add_cost,
            emer_ship=ps.es_cost, total=initial + ps.add_cost + ps.es_cost,
        )
    for name, cfg in model.resource_pools.items():
        ps = run_pool_stats[name]
        add = ps.add_cost + pools_add_delta.get(name, 0.0)
        initial = float(cfg.initial_spares) * cfg.spare_cost
        resources[name] = PoolCostRow(
            name=name, initial=initial, new_spares=add,
            emer_ship=ps.es_cost, total=initial + add + ps.es_cost,
            is_resource=True,
        )
    # Events + hierarchy rollup (the interim variant of
    # `build_cost_run_report`).
    events = {
        eid: EventCostRow(
            name=e.name,
            initial=0.0 if system_state.zero_init_cost else e.initial_cost,
            success=stats_by_event[eid].op_cost,
            failure=stats_by_event[eid].rep_cost,
            total=(0.0 if system_state.zero_init_cost else e.initial_cost)
            + stats_by_event[eid].op_cost + stats_by_event[eid].rep_cost,
            parent_id=e.parent_id,
        )
        for eid, e in model.events_by_id.items()
    }
    hiers = build_hier_cost_rows(model, blocks, events)
    events_total = sum(r.total for r in events.values())
    lost_flow_cost = (
        capacity_report.ave_lost_flow * max(sim_time - model.stats_start_time, 0.0) * model.lost_flow_unit_cost
        if capacity_report is not None else 0.0
    )
    # `sysIntRedCost:=TotalRedCost` plus, while the system is Red, the
    # current stretch `System.redCost*(SimTime-LastColorChange)`
    int_red_cost = system_state.total_red_cost
    if system_state.color == SystemColor.RED:
        int_red_cost += model.system_red_cost * (sim_time - system_state.last_color_change)
    grand = (
        sum(r.total for r in blocks.values()) + events_total
        + sum(r.total for r in spare_pools.values())
        + sum(r.total for r in resources.values())
        + int_red_cost + lost_flow_cost
    )
    return CostRunReport(
        blocks=blocks, spare_pools=spare_pools, resources=resources,
        events=events, hiers=hiers,
        system_down_cost=int_red_cost,
        red_cost_configured=model.system_red_cost > 0.0,
        system_lost_flow_cost=lost_flow_cost,
        grand_total=grand,
    )

"""`End_Of_Run` / `Final_Results_Report` text builders -- ports of
`DoEndOfRunUpdate` and `DoEndOfSimUpdate`, covering the SUMMARY, LOGISTICS, SPARING,
CAPACITY, COST, stock and weak-link sections. Sections with no fixture to
verify them against raise `NotImplementedError` rather than being silently
skipped.

`termType` 1 (TimeSim), 2 (FailSim) and 3 (CycleSim) are handled. The
aggregate report's `>`-censoring and `"(N-runs)"` label suffix are applied
per the source's own branches.

Source: Raptor 7. Relevant fixtures: `tests/golden/Capacity1`,
`Phasing1`, `PreventMxDefer`, `PreventMxNoDefer`, `CYCLESIM`.
"""
from __future__ import annotations

import math

from typing import TYPE_CHECKING

from raptor.core.engine.reporting import (
    BlockCostRow, CostRunReport, EventCostRow, HierCostRow, PoolCostRow, RunReportData,
)
if TYPE_CHECKING:
    from raptor.core.engine.capacity import CapacityRunReport
    from raptor.core.engine.reporting import StockSnapshot
from raptor.core.engine.report_format import (
    format_stats, get_display_str, int_to_str, pop_to_sample, real_to_sn, real_to_str,
)
from raptor.core.engine.stats import RunningStat
from raptor.core.model import RBDBlock, SparingType
from raptor.core.engine.errors import EngineInvariantError


def _pad(s: str, width: int) -> str:
    """Port of the `SUBSTR(1, width, s + spaces)` left-justify/truncate
    pattern used throughout Raptor 7's report builders (same
    convention as `eventlog._pad`)."""
    return (s + " " * width)[:width]


def _output_to_fr(label: str) -> str:
    """Port of `OutputToFR`: every space in
    `label` EXCEPT the first character becomes `_` (file-output label
    convention, e.g. `"System Failures"` -> `"System_Failures"`,
    `"Conditional Reliability"` -> `"Conditional_Reliability"`)."""
    if not label:
        return label
    return label[0] + label[1:].replace(" ", "_")


def _spare_source_field(block: RBDBlock) -> str:
    """Port of the `block.sparingType` dispatch shared by both report
    types' Sparing tables -- a
    fixed 21-char left-justified field."""
    if block.sparing_type == SparingType.INFINITE:
        return "Infinite             "
    if block.sparing_type == SparingType.CUSTOM:
        return "Custom               "
    if block.sparing_type == SparingType.SPARE_POOL:
        return _pad(block.pool_name, 20) + " "
    if block.sparing_type == SparingType.NONE:
        return "None                 "
    raise EngineInvariantError(block.sparing_type)  # pragma: no cover


def _stat_or_none_str(value: float | None) -> str:
    return real_to_str(value) if value is not None else "N/A"


def _int_field(value: int, width: int) -> str:
    """Port of Modsim's `WriteInt(v, width)` -- right-justified integer."""
    return str(value).rjust(width)


def _real_field(value: float, width: int, decimals: int) -> str:
    """Port of Modsim's `WriteReal(v, width, decimals)` -- fixed-notation,
    right-justified (used by FlowLoss's `WriteReal(AveLostFlow, 12, 2)`), rather than a REALTOSN significant-figure format."""
    return f"{value:{width}.{decimals}f}"


_CAPACITY_TABLE_HEADER = (
    "                          MinFlow      AveFlow      MaxFlow"
    "    UsageRate    MinCapacity  AveCapacity  MaxCapacity  CapableRate"
)


#: A node's (usage, min_cap, ave_cap, max_cap, capability) -- all present,
#: or the whole group absent for a fullFlow boundary node.
CapacityColumns = tuple[float, int, float, int, float]


def _capacity_stat_row(
    min_flow: int, ave_flow: float, max_flow: int, capacity: CapacityColumns | None,
) -> str:
    """The shared numeric-tail shape of both capacity table rows.
   `capacity=None` renders the fullFlow N/A
    variant (Raptor 7's exact literal spacing)."""
    row = _int_field(min_flow, 12)
    row += " " + real_to_sn(ave_flow, 12, 2, 1) + " "
    row += _int_field(max_flow, 12)
    if capacity is None:
        row += " " + "         N/A" + "   "
        row += "         N/A"
        row += " " + "         N/A" + " "
        row += "         N/A"
        row += " " + "         N/A" + "   "
    else:
        usage, min_cap, ave_cap, max_cap, capability = capacity
        row += " " + real_to_sn(usage, 12, 2, 1) + "   "
        row += _int_field(min_cap, 12)
        row += " " + real_to_sn(ave_cap, 12, 2, 1) + " "
        row += _int_field(max_cap, 12)
        row += " " + real_to_sn(capability, 12, 2, 1) + "   "
    return row


def _build_capacity_section(run_data: RunReportData) -> list[str]:
    """Port of DoEndOfRunUpdate's ***CAPACITY*** section
   : FlowGenerated/UnitsProduced/FlowLoss
    scalars, the SystemCapacity row, and the per-node NodeCapacity table
    (every capNode except Start, alpha order per `NodeAlphaSort`;
    fullFlow nodes show N/A usage/capability columns)."""
    cap = run_data.capacity
    if cap is None:
        raise EngineInvariantError('invariant failed: cap is not None')
    sys_stats = cap.nodes[cap.system_node_id]
    lines: list[str] = []
    lines.append("***CAPACITY***")
    lines.append("")
    lines.append("FlowGenerated           " + _int_field(cap.flow_generated, 9))
    lines.append("UnitsProduced        " + real_to_sn(cap.units_produced, 12, 2, 1))
    lines.append("")
    lines.append("FlowLoss              MinLostFlow  AveLostFlow  MaxLostFlow")
    lines.append(
        "                     "
        + _int_field(cap.flow_generated - sys_stats.max_flow, 12)
        + " "
        + _real_field(cap.ave_lost_flow, 12, 2)
        + " "
        + _int_field(cap.flow_generated - sys_stats.min_flow, 12)
    )
    lines.append("")
    lines.append(_CAPACITY_TABLE_HEADER)
    # Name column widened like the cost tables above when a node name
    # exceeds 20 characters.
    cap_w = max(20, max(len(s.name) for s in cap.nodes.values()))
    lines.append(_pad("SystemCapacity", cap_w) + " " + _capacity_stat_row(
        sys_stats.min_flow, sys_stats.ave_flow, sys_stats.max_flow,
        (sys_stats.usage, sys_stats.min_cap, sys_stats.ave_cap, sys_stats.max_cap,
         sys_stats.capability),
    ))
    # -- one blank after SystemCapacity's row, then NodeCapacity
    # and its header.
    lines.append("")
    lines.append("NodeCapacity")
    lines.append(_CAPACITY_TABLE_HEADER)
    for stats in sorted(cap.nodes.values(), key=lambda s: s.name.lower()):
        # Case-insensitive alpha order (`NodeAlphaSort`); a plain ASCII sort
        # would put uppercase names first.
        if stats.node_id == cap.system_node_id:
            continue
        # The source's gate is `node.capNode AND node.typeNode<>1`
        # every node in `cap.nodes` is already a capNode,
        # so only Start is excluded. Full-flow boundary nodes are listed with
        # the N/A usage/capability variant.
        #
        # No separating space after the name here, unlike the SystemCapacity
        # row above: source writes that one as
        # `SUBSTR(1,20,"SystemCapacity"+...)+" "` but the node rows
        # as a bare `SUBSTR(1,20,node.name+...)`, so the node
        # table's numeric columns sit one character left of the system row's.
        lines.append(_pad(stats.name, cap_w) + _capacity_stat_row(
            stats.min_flow, stats.ave_flow, stats.max_flow,
            None if stats.full_flow else (
                stats.usage, stats.min_cap, stats.ave_cap, stats.max_cap, stats.capability),
        ))
    lines.append("")
    lines.append("")
    lines.append("")
    return lines


def format_interim_cost_report(
    cost: CostRunReport, sim_time: float, *, capacity_on: bool,
) -> list[str]:
    """Port of `CalculateInterimCost`'s report body
    : the same tables as the End_Of_Run ***COST*** section,
    preceded by the `SimTime=` stamp, with the InHier column separated
    by four spaces (`WriteInt(parentID,4); WriteString("    ")`)
    instead of the EOR section's three. `cost` must be the
    IN-PROGRESS snapshot (current state time already folded in -- see
    `costs.interim_cost_snapshot`)."""
    lines = ["SimTime=  " + f"{sim_time:16.6f}", ""] + _cost_table_lines(
        cost, in_hier_sep="    ", capacity_on=capacity_on, name_pad_delta=1, pool_name_pad_delta=0, interim=True,
        operating_header="Block                 InHier        Good      Repair        Idle"
                         "     RepHold          PM      PMHold     Standby        Done",
        totals_prefix="Totals                       ",
        resource_cols_swapped=True,
    )
    # Interim tail : one blank after Grand_Total, the 29-star
    # separator, then two blanks -- unlike the EOR section's
    # three trailing blanks.
    while lines and lines[-1] == "":
        lines.pop()
    lines.extend(["", "*" * 29, ""])  # Raptor 7: the stars, then one WriteLn
    return lines


def _require_capacity(run_data: RunReportData) -> CapacityRunReport:
    """`run_data.capacity`, which is set for every run or for none (capacity
    analysis is a model-wide option)."""
    if run_data.capacity is None:
        raise ValueError(f"run {run_data.run_number} has no capacity data but earlier runs do")
    return run_data.capacity


def _build_cost_section(cost: CostRunReport, *, capacity_on: bool) -> list[str]:
    """Port of DoEndOfRunUpdate's ***COST*** section
    : the per-block Operating table (per-state cost columns), Sparing
    table, Life_cycle_cost, SparePools/Resources pool tables (the Resources
    one relabels its middle columns PerUseCost/PerTimeCost), and the
    Cost_Summary/Grand_Total tail. The Events and Hierarchy sub-tables are
    not ported, so the Cost_Summary's Events row prints 0.00 and Grand_Total
    omits event costs."""
    return _cost_table_lines(cost, in_hier_sep="   ", capacity_on=capacity_on, header=True)


def _cost_table_lines(
    cost: CostRunReport, *, in_hier_sep: str, capacity_on: bool, header: bool = False,
    operating_header: str = "Block                 InHier       Good      Repair        Idle"
                             "     RepHold          PM      PMHold     Standby        Done",
    totals_prefix: str = "Totals                      ",
    #: The interim Resources table prints PoolEndAddCost under PerUseCost and
    #: PoolEndESCost under PerTimeCost -- the opposite pairing of the
    #: End_Of_Run section ( prints ESCost first; interim's own
    #: loop prints AddCost first).
    resource_cols_swapped: bool = False,
    #: CalculateInterimCost's tables sit one column wider than the End_Of_Run
    #: section's everywhere a block/pool name is printed -- 0 for EOR, 1 for
    #: interim.
    name_pad_delta: int = 0,
    #: Same idea for the pool tables (SparePools/Resources), which the
    #: Final-Results variant does not widen even though it widens the block
    #: tables -- pass it separately. Defaults to `name_pad_delta` so the EOR
    #: and interim callers are unaffected.
    pool_name_pad_delta: int | None = None,
    #: The Final-Results pool rows carry no trailing space where the EOR
    #: and interim ones do.
    pool_row_trailing_space: bool = True,
    #: CalculateInterimCost's own event table and cost summary
    #: a wider Event header, four
    #: spaces after the parent id, a 29-char Event_Totals prefix, and
    #: SystemDownCost / SystemLostFlowCost printed unconditionally.
    interim: bool = False,
) -> list[str]:
    # Two header variants: the End_Of_Run section's Operating header has one
    # space between InHier and Good, while CalculateInterimCost's own
    # has two -- pass `operating_header` accordingly.

    def _money(v: float) -> str:
        return real_to_sn(v, 11, 2, 1)

    # Name-column widths. Raptor 7 truncates element names to 20
    # characters in every report table (`SUBSTR(1,20,...)`); this port keeps
    # that width whenever every name fits and widens only when a name is
    # longer, so results tables show full names.
    block_max = max((len(r.name) for r in cost.blocks.values()), default=0)
    event_max = max((len(r.name) for r in cost.events.values()), default=0)
    hier_max = max((len(r.name) for r in cost.hiers.values()), default=0)
    pool_max = max([len(r.name) for r in cost.spare_pools.values()]
                   + [len(r.name) for r in cost.resources.values()],
                   default=0)
    op_w = max(21, block_max + 1 + name_pad_delta)   # Op/Life/Events/Hiers
    sp_w = max(20 + name_pad_delta, block_max)       # Sparing
    _pool_delta = name_pad_delta if pool_name_pad_delta is None else pool_name_pad_delta
    pool_w = max(21 + _pool_delta, pool_max + 1)     # SparePools/Resources
    _pool_tail = " " if pool_row_trailing_space else ""
    event_width = max(21, event_max + 1)
    hi_w = max(21, hier_max + 1)

    lines: list[str] = []
    if header:
        lines.append("***COST***")
        lines.append("")
    lines.append("Operating")
    lines.append("")
    lines.append(operating_header)
    tot = {"op": 0.0, "rep": 0.0, "idle": 0.0, "hold": 0.0,
           "pm": 0.0, "pmh": 0.0, "sb": 0.0, "done": 0.0}
    # `InHier` column is the block's parentID -- always 0 for a
    # top-level block; printed via WriteInt(parentID,4).
    for row in sorted(cost.blocks.values(), key=lambda r: r.name.lower()):
        lines.append(
            # Source: `SUBSTR(1,20,name+" ")+" "` then WriteInt(parentID,4)
            # then `" "+REALTOSN(...)` -- one space AFTER
            # the 20-char name field, before the right-justified InHier.
            _pad(row.name, op_w) + str(row.in_hier).rjust(4) + in_hier_sep
            + _money(row.op_cost) + " "
            + _money(row.rep_cost) + " "
            + _money(row.idle_cost) + " "
            + _money(row.hold_cost) + " "
            + _money(row.pm_cost) + " "
            + _money(row.pm_hold_cost) + " "
            + _money(row.sb_cost) + " "
            + _money(row.done_cost) + " "
        )
        tot["op"] += row.op_cost
        tot["rep"] += row.rep_cost
        tot["idle"] += row.idle_cost
        tot["hold"] += row.hold_cost
        tot["pm"] += row.pm_cost
        tot["pmh"] += row.pm_hold_cost
        tot["sb"] += row.sb_cost
        tot["done"] += row.done_cost
    lines.append("")
    lines.append(totals_prefix + " " * max(0, op_w - 21)
        + _money(tot["op"]) + " "
        + _money(tot["rep"]) + " "
        + _money(tot["idle"]) + " "
        + _money(tot["hold"]) + " "
        + _money(tot["pm"]) + " "
        + _money(tot["pmh"]) + " "
        + _money(tot["sb"]) + " "
        + _money(tot["done"]) + " "
    )
    lines.append("")
    lines.append("Sparing")
    lines.append("")

    lines.append("Block                 InitSpares   NewSpares    EmerShip")
    bsict = bspct = besct = 0.0
    for row in sorted(cost.blocks.values(), key=lambda r: r.name.lower()):
        bsict += row.init_spare_cost
        bspct += row.new_spares_cost
        besct += row.emer_ship_cost
        if not row.sparing_col:
            continue
        lines.append(
            _pad(row.name, sp_w)
            + _money(row.init_spare_cost) + " "
            + _money(row.new_spares_cost) + " "
            + _money(row.emer_ship_cost) + " "
        )
    lines.append("")
    lines.append("Totals" + " " * max(0, sp_w - 6)
                 + _money(bsict) + " " + _money(bspct) + " " + _money(besct) + " ")
    lines.append("")
    # -- the Totals row's own terminating WriteLn plus one
    # blank before Life_cycle_cost.
    lines.append("Life_cycle_cost")
    lines.append("")
    lines.append("Block                   Purchase   Operating     Sparing    Disposal       Total")
    bict = btoct = btsct = bdfct = btct = 0.0
    for row in sorted(cost.blocks.values(), key=lambda r: r.name.lower()):
        bict += row.purchase
        btoct += row.operating
        btsct += row.sparing
        bdfct += row.disposal
        btct += row.total
        sparing_field = _money(row.sparing) + " " if row.sparing_col else "        N/A "
        lines.append(
            _pad(row.name, op_w)
            + _money(row.purchase) + " "
            + _money(row.operating) + " "
            + sparing_field
            + _money(row.disposal) + " "
            + _money(row.total) + " "
        )
    lines.append("")
    lines.append("Block_Totals" + " " * max(0, op_w - 12)
                 + _money(bict) + " " + _money(btoct) + " " + _money(btsct) + " "
                 + _money(bdfct) + " " + _money(btct) + " ")
    # -- totals row's terminating WriteLn, one blank, separator.
    lines.append("")
    lines.append("_" * 80)
    lines.append("")
    if cost.events:
        event_initial_cost = sum(r.initial for r in cost.events.values())
        event_success_cost = sum(r.success for r in cost.events.values())
        event_failure_cost = sum(r.failure for r in cost.events.values())
        event_total_cost = sum(r.total for r in cost.events.values())
        lines.append("Event                 InHier     Initial     Success     Failure       Total" if interim
                     else "Event                 InHier    Initial     Success     Failure       Total")
        for event_row in sorted(cost.events.values(), key=lambda r: r.name.lower()):
            lines.append(
                _pad(event_row.name, event_width) + str(event_row.parent_id).rjust(4) + ("    " if interim else "   ")
                + _money(event_row.initial) + " "
                + _money(event_row.success) + " "
                + _money(event_row.failure) + " "
                + _money(event_row.total) + " "
            )
        lines.append("")
        lines.append(("Event_Totals                 " if interim else "Event_Totals                ")
                     + _money(event_initial_cost) + " " + _money(event_success_cost) + " " + _money(event_failure_cost) + " " + _money(event_total_cost) + " ")
        # -- one blank, then the separator.
        lines.append("")
        lines.append("_" * 80)
        lines.append("")
    if cost.hiers:
        # -- one blank between the Events section's
        # separator and the Hierarchy header.
        lines.append("")
        lines.append("Hierarchy            HierNum    Purchase   Operating     Sparing"
                     "    Disposal      Events       Total")
        for hier_row in sorted(cost.hiers.values(), key=lambda r: r.name.lower()):
            lines.append(
                _pad(hier_row.name, hi_w) + str(hier_row.hier_id).rjust(4) + "    "
                + _money(hier_row.purchase) + " "
                + _money(hier_row.operating) + " "
                + _money(hier_row.sparing) + " "
                + _money(hier_row.disposal) + " "
                + _money(hier_row.events) + " "
                + _money(hier_row.total) + " "
            )
        # -- three blanks, then the hierarchy section's own
        # LONGER separator (100 underscores vs 80 elsewhere).
        lines.append("")
        lines.append("")
        lines.append("_" * 100)
        lines.append("")
    pict = pact = pesct = ptct = 0.0
    if cost.spare_pools:
        lines.append("SparePools               Initial   NewSpares    EmerShip       Total")
        for pool_row in cost.spare_pools.values():
            pict += pool_row.initial
            pact += pool_row.new_spares
            pesct += pool_row.emer_ship
            ptct += pool_row.total
            lines.append(
                # -- SUBSTR(1,20,name+" ")+" ", i.e. 21-char field.
                _pad(pool_row.name, pool_w)
                + _money(pool_row.initial) + " "
                + _money(pool_row.new_spares) + " "
                + _money(pool_row.emer_ship) + " "
                + _money(pool_row.total) + _pool_tail
            )
        lines.append("")
        lines.append("SparePool_Totals" + " " * max(0, pool_w - 16)
                     + _money(pict) + " " + _money(pact) + " " + _money(pesct) + " " + _money(ptct) + " ")
        lines.append("")
        lines.append("_" * 80)
        lines.append("")
    rict = rpuct = rptct = rtct = 0.0
    if cost.resources:
        lines.append("Resources                Initial  PerUseCost PerTimeCost       Total")
        for res_row in cost.resources.values():
            rict += res_row.initial
            # -- PerUseCost reads PoolEndESCost, PerTimeCost
            # reads PoolEndAddCost (the snapshot's emer_ship/new_spares).
            rpuct += res_row.emer_ship
            rptct += res_row.new_spares
            rtct += res_row.total
            per_use, per_time = (
                (res_row.new_spares, res_row.emer_ship) if resource_cols_swapped
                else (res_row.emer_ship, res_row.new_spares)
            )
            lines.append(
                # -- same 21-char field as the spare-pool table.
                _pad(res_row.name, pool_w)
                + _money(res_row.initial) + " "
                + _money(per_use) + " "
                + _money(per_time) + " "
                + _money(res_row.total) + " "
            )
        lines.append("")
        # Totals row follows the same column pairing as the rows above
        # (see `resource_cols_swapped`).
        lines.append("Resource_Totals      "
                     + _money(rict) + " "
                     + _money(rptct if resource_cols_swapped else rpuct) + " "
                     + _money(rpuct if resource_cols_swapped else rptct) + " "
                     + _money(rtct) + " ")
        lines.append("")
        lines.append("_" * 80)
        lines.append("")
    lines.append("Cost_Summary")
    lines.append("")
    lines.append("Blocks               " + _money(btct))
    lines.append("Events               "
                 + real_to_sn(sum(r.total for r in cost.events.values()), 11, 2, 1))
    lines.append("SparePools           " + _money(ptct))
    lines.append("Resources            " + _money(rtct))
    # Source's gate, `IF System.redCost>0.0` -- the
    # CONFIGURED cost, not the accumulated total. The config value rides on
    # `CostRunReport.red_cost_configured`.
    if cost.red_cost_configured or interim:
        lines.append("SystemDownCost       " + real_to_sn(cost.system_down_cost, 11, 2, 1))
    if capacity_on or interim:
        lines.append("SystemLostFlowCost   " + real_to_sn(cost.system_lost_flow_cost, 11, 2, 1))
    lines.append("")
    lines.append("________________________________")
    lines.append("Grand_Total          " + real_to_sn(cost.grand_total, 11, 2, 1))
    lines.append("")
    lines.append("")
    lines.append("")
    return lines


def build_end_of_run_report(run_data: RunReportData, blocks_by_id: dict[int, RBDBlock]) -> list[str]:
    """Port of `DoEndOfRunUpdate`'s SUMMARY/LOGISTICS/SPARING sections,
   one call per run."""
    lines: list[str] = []

    lines.append(f"***Run_{run_data.run_number}" + "*" * 69)
    lines.append("")
    lines.append("")
    lines.append("***SUMMARY***")
    lines.append("")
    lines.append("Availability               " + format_stats(run_data.availability))
    lines.append("MTBDE                      " + _stat_or_none_str(run_data.mtbde))
    lines.append("MDT                        " + _stat_or_none_str(run_data.mdt))
    if run_data.has_phases:
        # Port of Raptor 7's own `IF (activePhases>0)` gate
        # -- Dependability is only ever PRINTED for a phased model, even
        # though it's always COMPUTED (`reporting.build_run_report_data`).
        dependability_str = "N/A" if run_data.dependability is None else format_stats(run_data.dependability)
        lines.append("Dependability              " + dependability_str)
    lines.append("Reliability                " + format_stats(run_data.reliability))
    lines.append("ConditionalReliability     " + format_stats(run_data.conditional_reliability))
    lines.append(_pad("EndingSimTime", 25) + "  " + real_to_str(run_data.ending_sim_time))
    lines.append(_pad("NumberOfSystemFailures", 25) + "  " + int_to_str(run_data.num_system_failures))
    lines.append(_pad("NumberOfComponentFailures", 25) + "  " + int_to_str(run_data.num_component_failures))
    lines.append("")
    lines.append("")
    lines.append("")
    lines.append("***LOGISTICS***")
    lines.append("")
    lines.append("MTBM                  " + _stat_or_none_str(run_data.mtbm))
    lines.append("MTBMu                 " + _stat_or_none_str(run_data.mtbmu))
    lines.append("MTBMs                 " + _stat_or_none_str(run_data.mtbms))
    lines.append("MMT                   " + _stat_or_none_str(run_data.mmt))
    lines.append("MMTu                  " + _stat_or_none_str(run_data.mmtu))
    lines.append("MMTs                  " + _stat_or_none_str(run_data.mmts))
    lines.append("GreenPercent          " + real_to_str(run_data.green_percent or 0.0))
    lines.append("YellowPercent         " + real_to_str(run_data.yellow_percent or 0.0))
    lines.append("RedPercent            " + real_to_str(run_data.red_percent or 0.0))
    if run_data.has_phases:
        # Port of Raptor 7's own `IF activePhases>0` gate --
        # same "unconditional WriteString, 0.0 fallback" convention as the
        # ordinary Green/Yellow/RedPercent lines above (not "N/A").
        lines.append("MissionGreenPercent   " + real_to_str(run_data.mission_green_percent or 0.0))
        lines.append("MissionYellowPercent  " + real_to_str(run_data.mission_yellow_percent or 0.0))
        lines.append("MissionRedPercent     " + real_to_str(run_data.mission_red_percent or 0.0))
    lines.append("")
    lines.append("                                Minimum           Mean        Maximum         St.Dev")
    lines.append(_stat_row_or_missing("TBDE", run_data.sys_fail_stat, "No System Failures!"))
    lines.append(_stat_row_or_missing("DT", run_data.red_stat, "No System Repairs!"))
    lines.append(_stat_row_or_missing("TBM", run_data.comp_fail_stat, "No Component Failures!"))
    lines.append(_stat_row_or_missing("RT", run_data.comp_repair_stat, "No Component Repairs!"))
    lines.append("")
    lines.append("")
    lines.append("")

    if blocks_by_id:
        lines.append("***SPARING***")
        lines.append("")
        lines.append("Spare_Utilization")
        lines.append("")
        # Same widened name column as the cost/capacity tables.
        spu_w = max(20, max(len(b.name) for b in blocks_by_id.values()))
        lines.append("blockName".ljust(spu_w + 2)
                     + "Failures   SpareSource           Consumed")
        # Case-insensitive (`BlockAlphaSort`), the same rule the node tables
        # use.
        for block in sorted(blocks_by_id.values(), key=lambda b: b.name.lower()):
            failures, consumed = run_data.block_spares[block.id]
            # The data row pads the name to `spu_w`, while the header above
            # pads to `spu_w + 2` -- Raptor 7's own widths, not a typo: the
            # `Failures` column is right-justified to end at column 29 under a
            # header whose `blockName` runs to 22.
            lines.append(
                _pad(block.name, spu_w) + str(failures).rjust(9) + "   "
                + _spare_source_field(block) + str(consumed).rjust(9)
            )
        lines.append("")
        lines.append("")

    lines.extend(_build_stock_sections(run_data))

    if run_data.capacity is not None:
        lines.extend(_build_capacity_section(run_data))

    if run_data.cost is not None:
        lines.extend(_build_cost_section(run_data.cost, capacity_on=run_data.capacity is not None))

    lines.extend(_build_weak_section(run_data))

    # Two blank lines always separate this run's report tail from the next
    # run's "***Run_N***" header, regardless of whether the SPARING section
    # printed. The exact source mechanism is not pinned down; it likely comes
    # from trailing WriteLn calls in DoEndOfRunUpdate's out-of-scope
    # Custom-sparing/Capacity/Cost sections.
    lines.append("")
    lines.append("")

    return lines


def _stock_row(snap, *, gap: str, with_new: bool) -> str:
    """One row of a sparing/resource stock table.

    Literal `WriteInt(...,9)` / `REALTOSN(...,11,2,1)` / `SUBSTR(1,20,...)`
    widths from Raptor 7 (Custom) (SparePool)
 (Resource). The name field is a hard 20 here, matching
    the source's own truncation.

    `gap`: the extra run of spaces Raptor 7 puts after the name on the pool
    tables but not the Custom one. `with_new`: the Resource table has no
    `NewSpares` column, and spaces its remaining columns differently.
    """
    row = _pad(snap.name, 20) + gap + str(snap.init).rjust(9)
    if with_new:
        row += "  " + str(snap.arrivals).rjust(9)
        row += "  " + str(snap.minimum).rjust(9)
        row += "  " + real_to_sn(snap.mean, 11, 2, 1)
        row += "  " + str(snap.maximum).rjust(9)
        row += "  " + str(snap.ending).rjust(9)
    else:
        row += "    " + str(snap.minimum).rjust(9)
        row += "  " + real_to_sn(snap.mean, 11, 2, 1)
        row += "    " + str(snap.maximum).rjust(9)
        row += "    " + str(snap.ending).rjust(9)
    return row + "  " + str(snap.waits).rjust(9) + " "


def _build_stock_sections(run_data: RunReportData) -> list[str]:
    """`Custom_Sparing_Information` / `SparePool_Sparing_Information` /
    `***RESOURCES***` -- the three End_Of_Run tables at Raptor 7, each behind its own presence guard
    (`SparesTrackingGroup.numberIn>0` / `totalSpares>0` / `totalRes>0`,
    expressed here as "the model has any of that kind").
    """
    lines: list[str] = []
    if run_data.custom_stock:
        lines.append("Custom_Sparing_Information")
        lines.append("")
        lines.append("blockName            InitNumSpares  NewSpares   MinStock"
                     "   AveStock   MaxStock   EndStock  NumDelays")
        for snap in run_data.custom_stock.values():
            lines.append(_stock_row(snap, gap="", with_new=True))
        lines.append("")
    if run_data.spare_pool_stock:
        lines.append("SparePool_Sparing_Information")
        lines.append("")
        lines.append("poolName             InitNumSpares  NewSpares   MinStock"
                     "     AveStock   MaxStock   EndStock  NumDelays")
        for snap in run_data.spare_pool_stock.values():
            lines.append(_stock_row(snap, gap="     ", with_new=True))
        lines.append("")
        lines.append("")
        lines.append("")
    if run_data.resource_stock:
        lines.append("***RESOURCES***")
        lines.append("")
        lines.append("Resource_Information")
        lines.append("")
        lines.append("resourceName            InitNumRes    MinNumRes    AveNumRes"
                     "    MaxNumRes EndingNumRes    NumDelays")
        for snap in run_data.resource_stock.values():
            lines.append(_stock_row(snap, gap="     ", with_new=False))
        # Three, not two.
        lines.append("")
        lines.append("")
        lines.append("")
    return lines


_NODE_STATE_KEYS = ("green", "yellow", "red", "blue", "brown", "orange")
_BLOCK_STATE_KEYS = ("run", "standby", "idle", "repair", "rep_hold", "pm", "pm_hold", "done")


def _weak_node_metrics(v: dict, missions_attempted: int) -> tuple[float, float, float, dict]:
    """`(Ao, Do, R, percentages)` for one node-run."""
    total = sum(v[k] for k in _NODE_STATE_KEYS)
    dep_total = v["green"] + v["yellow"] + v["red"]
    if total > 0.0:
        pct = {k: (v[k] / total) * 100.0 for k in _NODE_STATE_KEYS}
        ao = (v["green"] + v["yellow"] + v["blue"]) / total
    else:
        pct = dict.fromkeys(_NODE_STATE_KEYS, 0.0)
        pct["green"] = 100.0
        ao = 1.0
    do = ((v["green"] + v["yellow"]) / dep_total) if dep_total > 0.0 else 1.0
    r = (float(v.get("m_completed", 0)) / missions_attempted) if missions_attempted > 0 else 1.0
    return ao, do, r, pct


def _weak_block_metrics(v: dict, missions_attempted: int) -> tuple[float, float, float, dict]:
    """`(Ao, Do, R, percentages)` for one block-run."""
    ao_denom = sum(v[k] for k in _BLOCK_STATE_KEYS)
    do_denom = v["run"] + v["repair"] + v["rep_hold"] + v["done"]
    if ao_denom > 0.0:
        ao = (v["run"] + v["standby"]) / ao_denom
        do = (v["run"] / do_denom) if do_denom > 0.0 else 1.0
        pct = {k: (v[k] / ao_denom) * 100.0 for k in _BLOCK_STATE_KEYS}
    else:
        ao = do = 1.0
        pct = dict.fromkeys(_BLOCK_STATE_KEYS, 0.0)
        pct["run"] = 100.0
    r = (float(v.get("m_completed", 0)) / missions_attempted) if missions_attempted > 0 else 1.0
    return ao, do, r, pct


def _weak_event_metrics(v: dict, missions_attempted: int) -> tuple[float, float, float, dict]:
    """`(Ao, Do, R, percentages)` for one event-run.
    `AoDenom=run+standby+repair`, `DoDenom=run+repair`; the three
    percentages are run/standby/repair over `AoDenom` (Success/Armed/
    Failure)."""
    ao_denom = v["run"] + v["standby"] + v["repair"]
    do_denom = v["run"] + v["repair"]
    if ao_denom > 0.0:
        ao = (v["run"] + v["standby"]) / ao_denom
        do = (v["run"] / do_denom) if do_denom > 0.0 else 1.0
        pct = {"success": (v["run"] / ao_denom) * 100.0,
               "armed": (v["standby"] / ao_denom) * 100.0,
               "failure": (v["repair"] / ao_denom) * 100.0}
    else:
        ao = do = 1.0
        pct = {"success": 100.0, "armed": 0.0, "failure": 0.0}
    r = (float(v.get("m_completed", 0)) / missions_attempted) if missions_attempted > 0 else 1.0
    return ao, do, r, pct


def _weak_event_row(v: dict, missions_attempted: int) -> str:
    """One EVENT_RESULTS row."""
    ao, do, r, pct = _weak_event_metrics(v, missions_attempted)
    row = _pad(v["name"], 20) + "  "
    row += "  " + format_stats(ao) + "  " + format_stats(do) + "  " + format_stats(r)
    row += "  " + real_to_sn(pct["success"], 12, 6, 1)
    row += real_to_sn(pct["armed"], 12, 6, 1)
    row += real_to_sn(pct["failure"], 12, 6, 1)
    return row


def _weak_hier_rows(v: dict, missions_attempted: int) -> tuple[str, str]:
    """The two HIERARCHY_RESULTS rows for one hierarchy.
    Ao/Do/R and the percentages come from the hierarchy's
    OUT node (`_weak_node_metrics`); `MTBDE`/`MDT` are
    `(green+yellow+blue)/hierDE` and `(red+orange)/hierDE`,
    falling back to the raw uptime and `"N/A"` when `hierDE==0`."""
    ao, do, r, pct = _weak_node_metrics(v, missions_attempted)
    row1 = _pad(v["name"], 20) + "  "
    row1 += "  " + format_stats(ao) + "  " + format_stats(do) + "  " + format_stats(r)
    down_event_count = v.get("down_event_count", 0)
    uptime = v["green"] + v["yellow"] + v["blue"]
    if down_event_count > 0:
        mtbde = uptime / down_event_count
        mdt = (v["red"] + v["orange"]) / down_event_count
        row1 += ("    " + real_to_str(mtbde) + "  ")[:12]
        row1 += ("    " + real_to_str(mdt) + "  ")[:10]
    else:
        row1 += ("    " + real_to_str(uptime) + "  ")[:12]
        row1 += "N/A"
    row2 = _pad(v["name"], 20) + "  "
    for k in ("green", "yellow", "red", "orange", "blue", "brown"):
        row2 += real_to_sn(pct[k], 11, 6, 1)
    return row1, row2


def _weak_node_row(v: dict, missions_attempted: int) -> str:
    """One NODE_RESULTS row."""
    ao, do, r, pct = _weak_node_metrics(v, missions_attempted)
    row = _pad(v["name"], 20) + "  "
    row += "  " + format_stats(ao) + "  " + format_stats(do) + "  " + format_stats(r)
    for k in ("green", "yellow", "red", "orange", "blue", "brown"):
        row += real_to_sn(pct[k], 11, 6, 1)
    return row


def _build_weak_section(run_data: RunReportData) -> list[str]:
    """Port of `DoEndOfRunUpdate`'s ***WEAK_LINK_ANALYSIS*** section
   : NODE_RESULTS (only `typeNode=2` nodes with
    `reportNodeAnal`) and BLOCK_RESULTS' two tables. `Ao`/`Do` and the
    percentage columns are ratios of the per-state times this port
    already accumulates; `R` is `mCompleted/missionsAttempted`, whose
    per-element numerator comes from the mission bookkeeping in
    `blocks.change_block_state`/`nodes.change_node_state`/
    `system_status.change_system_status`.

    `EVENT_RESULTS` is not written -- no fixture enables weak
    analysis on a model with events, so its column semantics are
    unverifiable here. Omitted rather than guessed.
    """
    if not run_data.weak_stats and not run_data.weak_nodes:
        return []
    ma = run_data.missions_attempted
    lines = ["***WEAK_LINK_ANALYSIS***", ""]
    nodes = [v for v in run_data.weak_nodes.values()
             if v.get("type_node") == 2 and v.get("report_anal")]
    if nodes:
        lines.append("NODE_RESULTS")
        lines.append("")
        lines.append("Node                             Ao           Do            R"
                     "     %Green    %Yellow       %Red    %Orange      %Blue     %Brown")
        for v in sorted(nodes, key=lambda x: x["name"].lower()):
            lines.append(_weak_node_row(v, ma))
    lines.append("")
    lines.append("")
    lines.append("")
    blocks = list(run_data.weak_stats.values())
    if blocks:
        blocks.sort(key=lambda x: x["name"].lower())
        lines.append("BLOCK_RESULTS")
        lines.append("")
        lines.append("Block                            Ao           Do            R")
        for v in blocks:
            ao, do, r, _pct = _weak_block_metrics(v, ma)
            lines.append(_pad(v["name"], 20) + "  " + "  " + format_stats(ao)
                         + "  " + format_stats(do) + "  " + format_stats(r))
        lines.append("")
        lines.append("Block                     %Running    %Standby       %Idle"
                     "  %Repairing    %RepHold         %PM     %PMHold       %Done")
        for v in blocks:
            _ao, _do, _r, pct = _weak_block_metrics(v, ma)
            row = _pad(v["name"], 20) + "  "
            for k in _BLOCK_STATE_KEYS:
                row += real_to_sn(pct[k], 12, 6, 1)
            lines.append(row)
        lines.append("")
        lines.append("")
        lines.append("")
    # EVENT_RESULTS.
    events = list(run_data.weak_events.values())
    if events:
        events.sort(key=lambda x: x["name"].lower())
        lines.append("EVENT_RESULTS")
        lines.append("")
        lines.append("Event                            Ao           Do            R"
                     "      %Success      %Armed    %Failure")
        for v in events:
            lines.append(_weak_event_row(v, ma))
    # HIERARCHY_RESULTS.
    hiers = list(run_data.weak_hiers.values())
    if hiers:
        hiers.sort(key=lambda x: x["name"].lower())
        lines.append("")
        lines.append("HIERARCHY_RESULTS")
        lines.append("")
        lines.append("Hierarchy                        Ao           Do            R"
                     "       MTBDE       MDT")
        for v in hiers:
            lines.append(_weak_hier_rows(v, ma)[0])
        lines.append("")
        lines.append("Hierarchy                  %Green    %Yellow       %Red"
                     "    %Orange      %Blue     %Brown")
        for v in hiers:
            lines.append(_weak_hier_rows(v, ma)[1])
        lines.append("")
    return lines


def _stat_row_or_missing(label: str, stat: RunningStat, missing: str) -> str:
    if stat.count == 0:
        return missing
    row = _pad(label, 23) + "  "
    row += real_to_sn(stat.minimum, 14, 6, 1) + " "
    row += real_to_sn(stat.mean, 14, 6, 1) + " "
    row += real_to_sn(stat.maximum, 14, 6, 1) + " "
    if stat.count > 1:
        sigma = pop_to_sample(stat.population_stdev, stat.count)
        row += real_to_sn(sigma, 14, 6, 1)
    else:
        row += " " * 11 + "N/A"
    return row


_TABLE_HEADER = _pad("", 25) + "Minimum         Mean            Maximum         St.Dev          SEM"


def _table_row(label: str, stat: RunningStat) -> str:
    if stat.count == 0:
        cols = ["N/A"] * 5
    else:
        cols = [
            real_to_sn(stat.minimum, 14, 6, 3),
            real_to_sn(stat.mean, 14, 6, 3),
            real_to_sn(stat.maximum, 14, 6, 3),
            get_display_str(stat.population_stdev, stat.count, 14, 6, 3, "stdev"),
            get_display_str(stat.population_stdev, stat.count, 14, 6, 3, "sem"),
        ]
    row = _pad(_output_to_fr(label), 25)
    for col in cols:
        row += _pad(col, 14) + "  "
    return row


def _table_row_9dec(label: str, stat: RunningStat) -> str:
    """Same as `_table_row` but for Availability/Reliability/Conditional
    Reliability's `FormatStats` (9-decimal) columns instead of `real_to_sn`."""
    if stat.count == 0:
        cols = ["N/A"] * 5
    else:
        cols = [
            format_stats(stat.minimum),
            format_stats(stat.mean),
            format_stats(stat.maximum),
            get_display_str(stat.population_stdev, stat.count, 11, 9, 1, "stdev"),
            get_display_str(stat.population_stdev, stat.count, 11, 9, 1, "sem"),
        ]
    row = _pad(_output_to_fr(label), 25)
    for col in cols:
        row += _pad(col, 14) + "  "
    return row


def _table_row_realtostr(label: str, stat: RunningStat) -> str:
    """Green/Yellow/Red Percent's own row:
    Min/Mean/Max via plain `REALTOSTR` (unlike MTBM/MMT's `REALTOSN`),
    StDev/SEM still via `GetDisplayStr(...,14,6,3,...)`."""
    if stat.count == 0:
        cols = ["N/A"] * 5
    else:
        cols = [
            real_to_str(stat.minimum),
            real_to_str(stat.mean),
            real_to_str(stat.maximum),
            get_display_str(stat.population_stdev, stat.count, 14, 6, 3, "stdev"),
            get_display_str(stat.population_stdev, stat.count, 14, 6, 3, "sem"),
        ]
    row = _pad(_output_to_fr(label), 25)
    for col in cols:
        row += _pad(col, 14) + "  "
    return row


def _int_table_row(label: str, stat: RunningStat) -> str:
    """`System_Failures`' own row: Min/Max as
    truncated ints, Mean as a plain `REALTOSTR`."""
    if stat.count == 0:
        cols = ["N/A"] * 5
    else:
        cols = [
            int_to_str(int(stat.minimum)),
            real_to_str(stat.mean),
            int_to_str(int(stat.maximum)),
            get_display_str(stat.population_stdev, stat.count, 14, 6, 3, "stdev"),
            get_display_str(stat.population_stdev, stat.count, 14, 6, 3, "sem"),
        ]
    row = _pad(_output_to_fr(label), 25)
    for col in cols:
        row += _pad(col, 14) + "  "
    return row


def _cycles_table_row(label: str, stat: RunningStat) -> str:
    """`Cycles_Completed`'s own row. Same shape
    as `_int_table_row` with one difference: Max is `INTTOSTR(ROUND(...))`
    where Min is `INTTOSTR(TRUNC(...))`. This matters
    because `cyclesCount` is fractional (see
    `RunReportData.cycles_completed`): a run ending on 30.5 cycles prints
    Min 30 / Mean 30.500000 / Max 31. Truncating both ends would print Max
    30.

    ROUND is MODSIM's round-half-away-from-zero, not Python's banker's
    `round()` -- `round(30.5)` is 30 in Python."""
    if stat.count == 0:
        cols = ["N/A"] * 5
    else:
        cols = [
            int_to_str(math.floor(stat.minimum)),
            real_to_str(stat.mean),
            int_to_str(math.floor(stat.maximum + 0.5)),
            get_display_str(stat.population_stdev, stat.count, 14, 6, 3, "stdev"),
            get_display_str(stat.population_stdev, stat.count, 14, 6, 3, "sem"),
        ]
    row = _pad(_output_to_fr(label), 25)
    for col in cols:
        row += _pad(col, 14) + "  "
    return row


def _censored_table_row(label: str, stat: RunningStat, avail_count: int,
                        suspected: bool, notes: list[str],
                        censor_on_short: bool = True) -> str:
    """`MTBDE`/`MDT`/MTBM-family Final-Results rows per Raptor 7's own
    branches ( Summary Logistics):

    - count == 0: plain label, whole row N/A (no note flags).
    - count == Availability's count: normal row, unless the run values were
      the "suspected" raw-uptime fallback (`mtbde_suspected`), in which case
      Mean/Maximum get the `>` prefix and St.Dev/SEM go N/A.
    - count < Availability's count: label becomes ``Label(k-runs)``. With
      `censor_on_short`, the MTBDE/MTBM family `>`-censors Mean/Maximum with
      N/A St.Dev/SEM, while the MDT/MMT family
      keeps the plain min/mean/max and computes sigma whenever count > 1.
     Only the MDT/MMT family passes False.

    `notes` collects the human-readable Note lines for the caller to display
    (Raptor 7 shows them in its Final_Results dialog, they are not written to the report file).
    """
    if stat.count == 0:
        return _table_row(label, stat)
    if suspected:
        notes.append(
            "Note: Parameters marked with a greater than sign are likely "
            "to be poor estimators.")
        cols = [
            real_to_sn(stat.minimum, 14, 6, 3),
            ">" + real_to_sn(stat.mean, 13, 6, 3),
            ">" + real_to_sn(stat.maximum, 13, 6, 3),
            "N/A",
            "N/A",
        ]
    elif stat.count < avail_count and censor_on_short:
        notes.append(
            "Note: Some parameter calculations are based on less than "
            "the specified number of trials.")
        label = f"{label}({stat.count}-runs)"
        cols = [
            real_to_sn(stat.minimum, 14, 6, 3),
            ">" + real_to_sn(stat.mean, 13, 6, 3),
            ">" + real_to_sn(stat.maximum, 13, 6, 3),
            "N/A",
            "N/A",
        ]
    elif stat.count < avail_count:
        # MDT/MMT family: the source keeps the plain min/mean/max and
        # computes sigma whenever count > 1 -- only the (k-runs) label marks
        # the short sample.
        notes.append(
            "Note: Some parameter calculations are based on less than "
            "the specified number of trials.")
        label = f"{label}({stat.count}-runs)"
        if stat.count > 1:
            sigma = pop_to_sample(stat.population_stdev, stat.count)
            stdev_col = real_to_sn(sigma, 14, 6, 3)
            sem_col = real_to_sn(sigma / math.sqrt(stat.count), 14, 6, 3)
        else:
            stdev_col = sem_col = "N/A"
        cols = [
            real_to_sn(stat.minimum, 14, 6, 3),
            real_to_sn(stat.mean, 14, 6, 3),
            real_to_sn(stat.maximum, 14, 6, 3),
            stdev_col,
            sem_col,
        ]
    else:
        if stat.count > 1:
            sigma = pop_to_sample(stat.population_stdev, stat.count)
            stdev_col = real_to_sn(sigma, 14, 6, 3)
            sem_col = real_to_sn(sigma / math.sqrt(stat.count), 14, 6, 3)
        else:
            stdev_col = sem_col = "N/A"
        cols = [
            real_to_sn(stat.minimum, 14, 6, 3),
            real_to_sn(stat.mean, 14, 6, 3),
            real_to_sn(stat.maximum, 14, 6, 3),
            stdev_col,
            sem_col,
        ]
    row = _pad(_output_to_fr(label), 25)
    for col in cols:
        row += _pad(col, 14) + "  "
    return row


def build_final_results_report(
    all_run_data: list[RunReportData], blocks_by_id: dict[int, RBDBlock], file_name: str, run_length: float, date: str,
    *, termination_type: str = "TimeSim", notes_sink: list[str] | None = None,
    stats_reset: bool = False, start_stats: float = 0.0, random_draws: int | None = None,
) -> list[str]:
    """Port of `DoEndOfSimUpdate`'s SUMMARY/LOGISTICS/SPARING sections,
   called once after all runs -- cross-run
    aggregation reuses the same recursive `RStatObj` mechanism as the real
    source (one sample per run, fed from that run's already-computed scalar;
    see `RunReportData`).

    `termination_type`: `"TimeSim"` (`termType=1`) emits the `System_Failures`
    row; `"FailSim"` (`termType=2`) emits
    `Ending_Sim_Time` instead, fed from each run's
    `RunReportData.ending_sim_time`; `"CycleSim"` (`termType=3`) emits both
    rows plus a "... of N cycles:" header."""
    if termination_type not in ("TimeSim", "FailSim", "CycleSim"):
        raise NotImplementedError(f"termination_type={termination_type!r} not ported -- only TimeSim/FailSim/CycleSim")
    n = len(all_run_data)

    avail_stat, mtbde_stat, mdt_stat, rel_stat, rcond_stat, sysfail_stat, endsimtime_stat = (
        RunningStat(), RunningStat(), RunningStat(), RunningStat(), RunningStat(), RunningStat(), RunningStat(),
    )
    mtbm_stat, mtbmu_stat, mmt_stat, mmtu_stat = RunningStat(), RunningStat(), RunningStat(), RunningStat()
    #: `Term3Mon`, fed `cyclesCount` per run.
    #: Only added to for a phased model -- see the `cycles_completed is not
    #: None` guard at its `add` site.
    cycles_stat = RunningStat()
    # `MTBMs`/`MMTs`/`Dependability`/`Mission Green/Yellow/Red Percent`
    # use the same "one sample per
    # run" cross-run aggregation as every other stat here. `has_phases` is
    # model-level, taken from the first run's copy.
    mtbms_stat, mmts_stat, dependability_stat = RunningStat(), RunningStat(), RunningStat()
    mission_green_pct_stat, mission_yellow_pct_stat, mission_red_pct_stat = (
        RunningStat(), RunningStat(), RunningStat(),
    )
    has_phases = all_run_data[0].has_phases if all_run_data else False
    green_pct_stat, yellow_pct_stat, red_pct_stat = RunningStat(), RunningStat(), RunningStat()
    # OR of every run's own "suspected" flag (source's module-level flags
    # are STICKY across the simulation -- reporting.py's docstring).
    mtbde_suspected_any = any(d.mtbde_suspected for d in all_run_data)
    mtbm_suspected_any = any(d.mtbm_suspected for d in all_run_data)
    mtbmu_suspected_any = any(d.mtbmu_suspected for d in all_run_data)
    mtbms_suspected_any = any(d.mtbms_suspected for d in all_run_data)
    notes: list[str] = notes_sink if notes_sink is not None else []
    # Cross-run capacity monitors: one sample
    # per run of UnitsProduced / NodeAveFlow[start] / NodeAveCap[start],
    # aggregated exactly like every other Final_Results stat.
    units_stat, mean_flow_stat, mean_cap_stat = RunningStat(), RunningStat(), RunningStat()
    has_capacity = all_run_data and all_run_data[0].capacity is not None
    if has_capacity:
        for run_data in all_run_data:
            cap = _require_capacity(run_data)
            units_stat.add(cap.units_produced)
            sys_stats = cap.nodes[cap.system_node_id]
            mean_flow_stat.add(sys_stats.ave_flow)
            mean_cap_stat.add(sys_stats.ave_cap)
    spares_stat_by_block: dict[int, RunningStat] = {bid: RunningStat() for bid in blocks_by_id}

    for run_data in all_run_data:
        avail_stat.add(run_data.availability)
        # `MTBDE` is a monitored global and `MTBDEMon:=GETMONITOR(MTBDE,
        # RStatObj)`, so every assignment is a sample --
        # including the suspected raw-uptime fallback. Only the
        # `MTBDEupdated:=FALSE` branch (no uptime at all) assigns
        # nothing and so contributes none; that is what produces a short count
        # and the `(k-runs)` suffix, not the suspected flag. The same holds
        # for MTBM/MTBMu/MTBMs below.
        if run_data.mtbde is not None:
            mtbde_stat.add(run_data.mtbde)
        if run_data.mdt is not None:
            mdt_stat.add(run_data.mdt)
        rel_stat.add(run_data.reliability)
        # Only `RCond` assignments become RCondMon samples (GETMONITOR on the
        # global; Raptor 7 assigns only `IF missionsBegan>0`,
        # with no ELSE) -- runs with missionsBegan=0 contribute nothing.
        if run_data.rcond_assigned:
            rcond_stat.add(run_data.conditional_reliability)
        sysfail_stat.add(float(run_data.num_system_failures))
        # Term2 (`Term2Cond:=SimTime-StatsStartTime`): with no
        # in-run reset, StatsStartTime is 0.0, so this is just the ending sim
        # time.
        endsimtime_stat.add(run_data.ending_sim_time - run_data.stats_start_time)
        # Term3 (`Term3Cond:=cyclesCount`). `None` for an
        # unphased model, which never reaches the row's own
        # `activePhases>0` guard below either.
        if run_data.cycles_completed is not None:
            cycles_stat.add(run_data.cycles_completed)
        if run_data.mtbm is not None:
            mtbm_stat.add(run_data.mtbm)
        if run_data.mtbmu is not None:
            mtbmu_stat.add(run_data.mtbmu)
        if run_data.mtbms_agg is not None:
            mtbms_stat.add(run_data.mtbms_agg)
        if run_data.mmt is not None:
            mmt_stat.add(run_data.mmt)
        if run_data.mmtu is not None:
            mmtu_stat.add(run_data.mmtu)
        if run_data.mmts is not None:
            mmts_stat.add(run_data.mmts)
        if run_data.dependability is not None:
            dependability_stat.add(run_data.dependability)
        if run_data.green_percent is not None:
            green_pct_stat.add(run_data.green_percent)
        if run_data.yellow_percent is not None:
            yellow_pct_stat.add(run_data.yellow_percent)
        if run_data.red_percent is not None:
            red_pct_stat.add(run_data.red_percent)
        if run_data.mission_green_percent is not None:
            mission_green_pct_stat.add(run_data.mission_green_percent)
        if run_data.mission_yellow_percent is not None:
            mission_yellow_pct_stat.add(run_data.mission_yellow_percent)
        if run_data.mission_red_percent is not None:
            mission_red_pct_stat.add(run_data.mission_red_percent)
        for bid, (_, consumed) in run_data.block_spares.items():
            spares_stat_by_block[bid].add(float(consumed))

    lines: list[str] = []
    lines.append(f"Final_Results_Report for {file_name}  {date}")
    lines.append("")
    lines.append("")
    run_word = "run" if n == 1 else "runs"
    # Header wording per termType: TimeSim
    # "of sim time X:", FailSim "to N failures:"/"to 1 failure:",
    # CycleSim "of N cycles:"/"of 1 cycle:".
    if termination_type == "CycleSim":
        # Header for termType=3: "Results from 2 runs of 3000 cycles:" --
        # the TermCondition (stored in `run_length` for a CycleSim model) is
        # a cycle count, printed as a plain integer.
        cycles = int(run_length)
        cyc_word = "of 1 cycle:" if cycles == 1 else f"of {cycles} cycles:"
        lines.append(f"Results from {n} {run_word} {cyc_word}")
    elif termination_type == "FailSim":
        fails = int(run_length)
        fail_word = ("to 1 failure:" if fails == 1
                     else f"to {fails} failures:")
        lines.append(f"Results from {n} {run_word} {fail_word}")
    else:
        lines.append(f"Results from {n} {run_word} of sim time {real_to_str(run_length)}:")
    lines.append("")
    if random_draws is not None:
        # `IF password`: the batch's draw count, then a blank line.
        lines.append(f"Random draws: {random_draws}")
        lines.append("")
    lines.append("***SUMMARY***")
    lines.append("")
    lines.append(_TABLE_HEADER)
    # `IF costAnalysis`, immediately BEFORE the
    # capacity rows below: the cross-run stats of each run's own
    # Grand_Total, with N/A St.Dev/SEM on a single run.
    cost_runs = [r.cost for r in all_run_data if r.cost is not None]
    if cost_runs:
        grand_total_stat = RunningStat()
        for c in cost_runs:
            grand_total_stat.add(c.grand_total)
        cols = [
            real_to_sn(grand_total_stat.minimum, 14, 2, 3),
            real_to_sn(grand_total_stat.mean, 14, 2, 3),
            real_to_sn(grand_total_stat.maximum, 14, 2, 3),
        ]
        if grand_total_stat.count > 1:
            sigma = pop_to_sample(grand_total_stat.population_stdev, grand_total_stat.count)
            cols.append(real_to_sn(sigma, 14, 2, 3))
            cols.append(real_to_sn(sigma / math.sqrt(float(grand_total_stat.count)), 14, 2, 3))
        else:
            cols += ["N/A", "N/A"]
        row = _pad(_output_to_fr("Total Cost"), 25)
        for col in cols:
            row += _pad(col, 14) + "  "
        lines.append(row)
    if has_capacity:
        # Port of Raptor 7's FinalArray rows -- these come
        # BEFORE the Availability row in the Raptor 7 report's row order.
        for label, stat, sig in (
            ("Units Produced", units_stat, 2),
            ("Mean Flow", mean_flow_stat, 6),
            ("Mean Capacity", mean_cap_stat, 6),
        ):
            if stat.count == 0:
                cols = ["N/A"] * 5
            else:
                cols = [
                    real_to_sn(stat.minimum, 14, sig, 3),
                    real_to_sn(stat.mean, 14, sig, 3),
                    real_to_sn(stat.maximum, 14, sig, 3),
                    get_display_str(stat.population_stdev, stat.count, 14, sig, 3, "stdev"),
                    get_display_str(stat.population_stdev, stat.count, 14, sig, 3, "sem"),
                ]
            row = _pad(_output_to_fr(label), 25)
            for col in cols:
                row += _pad(col, 14) + "  "
            lines.append(row)
    lines.append(_table_row_9dec("Availability", avail_stat))
    # MTBDE/MDT rows implement Raptor 7 exactly: a `(k-runs)`
    # label suffix when that monitor saw fewer samples than Availability
    # (runs with no downing event contribute none), `>`-censoring of
    # Mean/Maximum with N/A St.Dev/SEM when the mean is only the raw
    # uptime fallback (mtbde_suspected), and the matching header notes.
    lines.append(_censored_table_row("MTBDE", mtbde_stat, avail_stat.count,
                                     mtbde_suspected_any, notes))
    lines.append(_censored_table_row("MDT", mdt_stat, avail_stat.count,
                                     False, notes, censor_on_short=False))
    if has_phases:
        # Port of Raptor 7's own `IF (activePhases>0)` gate,
        # same position as the per-run report's own Dependability line.
        lines.append(_table_row_9dec("Dependability", dependability_stat))
    lines.append(_table_row_9dec("Reliability", rel_stat))
    lines.append(_table_row_9dec("Conditional Reliability", rcond_stat))
    if termination_type == "TimeSim":
        # `IF StatsReset -> "Failures After Reset" ELSE "System
        # Failures"` -- Term1Mon is fed SysFailMon.Count
        # per run either way.
        lines.append(_int_table_row(
            "Failures After Reset" if stats_reset else "System Failures", sysfail_stat))
    elif termination_type == "CycleSim":
        # termType=3 emits both rows, System_Failures first
        # (Raptor 7's own IF/ELSE falls through to a second
        # unconditional block for cycles-based termination, producing these
        # two rows in this order).
        lines.append(_int_table_row(
            "Failures After Reset" if stats_reset else "System Failures", sysfail_stat))
        lines.append(_table_row_realtostr(
            "Active Stat Time" if stats_reset else "Ending Sim Time", endsimtime_stat))
    else:
        lines.append(_table_row_realtostr(
            "Active Stat Time" if stats_reset else "Ending Sim Time", endsimtime_stat))
    # `IF (((termType=1) OR (termType=2)) AND (activePhases>0))`
    # -- a phased TimeSim or FailSim model reports
    # how many phase cycles the runs got through. Not emitted for CycleSim,
    # where the cycle count is the termination criterion and `Ending_Sim_Time`
    # already carries the same information.
    if termination_type in ("TimeSim", "FailSim") and cycles_stat.count:
        lines.append(_cycles_table_row(
            "Cycles Since Reset" if stats_reset else "Cycles Completed", cycles_stat))
    if stats_reset:
        # Raptor 7's StatsReset footer. `start_stats` is the raw
        # `StartStats` value: termType 1 prints it as a time, 2/3 as a count
        # (truncated) with a plural "s" when >1.0. Layout: one blank line,
        # then the footer, then three blanks before ***LOGISTICS*** (five
        # blanks when there is no footer).
        if termination_type == "TimeSim":
            footer = f"Stats started at time {real_to_str(start_stats)}"
        else:
            word = "failure" if termination_type == "FailSim" else "cycle"
            if start_stats > 1.0:
                word += "s"
            footer = f"Stats started after {int(start_stats)} {word}"
        lines.append("")
        lines.append(footer)
        for _ in range(3):
            lines.append("")
    elif (termination_type == "FailSim" and any(rd.out_of_spares for rd in all_run_data)
          and all_run_data and all_run_data[-1].ending_sim_time > 0.0):
        # Raptor 7: `ELSIF termType=2: IF (OutOfSpares=TRUE)
        # IF (SimTime>0.0) FinalArray[k,1]:="Unable to complete "+
        # INTTOSTR(TRUNC(StopCriteria))+" failure/repair cycles on at least
        # one run  (out of spares)"` (two spaces before the parenthesis, as
        # in source). Same layout as the StatsReset footer above -- one
        # blank, the note, three blanks (`FailSimOutOfSparesProbe`'s real
        # capture). Both at once has no capture, so the footer wins here.
        lines.append("")
        lines.append(f"Unable to complete {int(run_length)} failure/repair cycles on at least one run  (out of spares)")
        for _ in range(3):
            lines.append("")
    else:
        for _ in range(5):
            lines.append("")
    lines.append("***LOGISTICS***")
    lines.append("")
    lines.append(_TABLE_HEADER)
    lines.append(_censored_table_row("MTBM", mtbm_stat, avail_stat.count,
                                     mtbm_suspected_any, notes))
    lines.append(_censored_table_row("MTBMu", mtbmu_stat, avail_stat.count,
                                     mtbmu_suspected_any, notes))
    # `IF didPM=TRUE`: without a single PM completion in any run the MTBMs row is all N/A,
    # whatever the monitor holds.
    lines.append(_censored_table_row("MTBMs", mtbms_stat if any(d.did_pm for d in all_run_data) else RunningStat(),
                                     avail_stat.count, mtbms_suspected_any, notes))
    lines.append(_censored_table_row("MMT", mmt_stat, avail_stat.count,
                                     False, notes, censor_on_short=False))
    lines.append(_censored_table_row("MMTu", mmtu_stat, avail_stat.count,
                                     False, notes, censor_on_short=False))
    lines.append(_censored_table_row("MMTs", mmts_stat, avail_stat.count,
                                     False, notes, censor_on_short=False))
    lines.append(_table_row_realtostr("Green Percent", green_pct_stat))
    lines.append(_table_row_realtostr("Yellow Percent", yellow_pct_stat))
    lines.append(_table_row_realtostr("Red Percent", red_pct_stat))
    if has_phases:
        # Port of Raptor 7's own `IF (activePhases>0)` gate.
        lines.append(_table_row_realtostr("Mission Green Percent", mission_green_pct_stat))
        lines.append(_table_row_realtostr("Mission Yellow Percent", mission_yellow_pct_stat))
        lines.append(_table_row_realtostr("Mission Red Percent", mission_red_pct_stat))
    # An unphased report carries one more row here than a phased one: an
    # empty table row padded to the full row width (label 25 plus five
    # 14-wide columns each followed by two spaces = 105), where a phased
    # report goes straight to the blank run. Then four blanks either way, and
    # ***SPARING***. The filler tracks `activePhases>0`, the same gate the
    # three Mission rows above use.
    if not has_phases:
        lines.append(" " * 105)
    for _ in range(4):
        lines.append("")

    if blocks_by_id:
        lines.append("***SPARING***")
        lines.append("")
        lines.append("Spares_Utilization")
        lines.append("")
        # Same widened name column as the cost tables; the 20-character cap
        # applies only on legacy `.rbd` save.
        fr_w = max(20, max(len(b.name) for b in blocks_by_id.values()))
        lines.append("blockName".ljust(fr_w + 2)
                     + "SpareSource            MinUsed      AveUsed"
                       "   MaxUsed        StDev")
        # Case-insensitive (`BlockAlphaSort`), the same rule the node tables
        # use.
        for block in sorted(blocks_by_id.values(), key=lambda b: b.name.lower()):
            stat = spares_stat_by_block[block.id]
            row = _pad(block.name, fr_w) + "  " + _spare_source_field(block)

            def _finite_int(v):
                # A GUI Stop can abort with no completed samples, leaving
                # min/max at +/-inf; int(inf) raises OverflowError. The
                # legacy format prints an integer here -- use 0.
                return 0 if v in (float("inf"), float("-inf")) or v != v \
                    else int(v)
            # count==0 after an abort-with-no-samples: mean/stdev would
            # divide by zero. Legacy never sees this (it only reports
            # after full runs); print zero fields for the dead run.
            if stat.count == 0:
                row += "0".rjust(9) + " " + real_to_sn(0.0, 12, 2, 1) + " "
                row += "0".rjust(9) + " " + real_to_sn(0.0, 12, 2, 1)
                continue
            row += str(_finite_int(stat.minimum)).rjust(9) + " "
            row += real_to_sn(stat.mean, 12, 2, 1) + " "
            row += str(_finite_int(stat.maximum)).rjust(9) + " "
            if stat.count > 1:
                sigma = pop_to_sample(stat.population_stdev, stat.count)
                # Trailing space is the StDev field's own padding.
                row += real_to_sn(sigma, 12, 2, 1) + " "
            else:
                # Trailing space, same as the StDev branch above.
                row += " " * 9 + "N/A "
            lines.append(row)
        # Two blanks here (source's `WriteLn; WriteLn` after the utilization
        # FOR, Raptor 7). The third blank is the unconditional
        # `WriteLn` that closes the Custom_Sparing section below
        # (outside its own `IF SparesTrackingGroup>0` guard).
        lines.append("")
        lines.append("")

    lines.extend(_build_fr_stock_sections(all_run_data))
    lines.extend(_build_fr_capacity_section(all_run_data))
    lines.extend(_build_fr_cost_section(all_run_data))
    lines.extend(_build_fr_weak_section(all_run_data))

    return lines


def _fr_weak_stat_row(name: str, vals: list[float]) -> str:
    """One Availability/Dependability/Reliability_Data row
   : Min/Mean/Max each `REALTOSN(...,11,9,1)`
    behind three spaces, with the StdDev field padded/truncated to 14 so
    an `N/A` still lines up, then SEM."""
    st = RunningStat()
    for v in vals:
        st.add(v)
    row = _pad(name, 20)
    row += "   " + real_to_sn(st.minimum, 11, 9, 1)
    row += "   " + real_to_sn(st.mean, 11, 9, 1)
    row += "   " + real_to_sn(st.maximum, 11, 9, 1)
    row += _pad("   " + get_display_str(st.population_stdev, st.count, 11, 9, 1, "stdev")
                + "          ", 14)
    row += "   " + get_display_str(st.population_stdev, st.count, 11, 9, 1, "sem")
    return row


def _build_fr_weak_section(all_run_data: list[RunReportData]) -> list[str]:
    """Final-Results ***WEAK_LINK_ANALYSIS***.
    Where the per-run section prints one figure per element, this prints
    that figure's cross-run Min/Mean/Max/StdDev/SEM -- three such tables
    (Availability/Dependability/Reliability) plus a State_Data table of
    cross-run MEAN percentages, for nodes then blocks.

    `EVENT_ANALYSIS` and `HIERARCHY_ANALYSIS` follow (`_build_fr_weak_event_hier`).
    """
    runs = [r for r in all_run_data if r.weak_stats or r.weak_nodes]
    if not runs:
        return []

    def collect(attr, keep=lambda v: True):
        out: dict = {}
        for r in runs:
            for k, v in getattr(r, attr).items():
                if keep(v):
                    out.setdefault(k, []).append((v, r.missions_attempted))
        return out

    nodes = collect("weak_nodes",
                    lambda v: v.get("type_node") == 2 and v.get("report_anal"))
    blocks = collect("weak_stats")

    def emit(title: str, label: str, items: dict, metrics) -> list[str]:
        if not items:
            return []
        keys = sorted(items, key=lambda k: items[k][0][0]["name"].lower())
        out = [title, ""]
        state_keys = _NODE_STATE_KEYS if label == "Node" else _BLOCK_STATE_KEYS
        for _idx, (sub, col) in enumerate((("Availability_Data", 0),
                                          ("Dependability_Data", 1),
                                          ("Reliability_Data", 2))):
            out.append(sub)
            tag = ("Ao", "Do", "R")[col]
            pad = 23 - len(label)
            out.append(label + " " * pad
                       + _pad(tag + "Min", 14) + _pad(tag + "Mean", 14)
                       + _pad(tag + "Max", 14) + _pad(tag + "StdDev", 14) + tag + "SEM")
            for k in keys:
                rows = items[k]
                out.append(_fr_weak_stat_row(
                    rows[0][0]["name"], [metrics(v, ma)[col] for v, ma in rows]))
            out.append("")
        out.append("State_Data")
        if label == "Node":
            out.append("Node                      %Green     %Yellow        %Red"
                       "       %Blue      %Brown     %Orange")
        else:
            out.append("Block                   %Running    %Standby       %Idle"
                       "  %Repairing    %RepHold         %PM     %PMHold       %Done")
        for k in keys:
            rows = items[k]
            row = _pad(rows[0][0]["name"], 20)
            for sk in state_keys:
                row += real_to_sn(
                    _fr_mean(metrics(v, ma)[3][sk] for v, ma in rows), 12, 6, 1)
            out.append(row)
        return out

    lines = ["***WEAK_LINK_ANALYSIS***", ""]
    lines += emit("NODE_ANALYSIS", "Node", nodes, _weak_node_metrics)
    lines.append("")
    lines.append("")
    lines += emit("BLOCK_ANALYSIS", "Block", blocks, _weak_block_metrics)
    lines.append("")
    lines += _build_fr_weak_event_hier(runs)
    return lines


def _build_fr_weak_event_hier(runs: list[RunReportData]) -> list[str]:
    """`EVENT_ANALYSIS` and `HIERARCHY_ANALYSIS` of the Final-Results
    weak-link block: the same Availability/Dependability/Reliability cross-run tables as nodes and blocks,
    then a State_Data table of cross-run MEAN percentages. Events use Success/Armed/Failure columns
    (`REALTOSN(mean,14,6,1)`); hierarchies use their Out node's figures plus a leading MTBDE/MDT summary.
    `hMTBDE`/`hMDT` are monitored variables (each per-run assignment is one observation): MTBDE is assigned
    every run (`uptime/hierDE`, or the raw uptime when `hierDE=0`), MDT only when `hierDE>0` -- so its
    monitor count can be smaller, and prints `N/A` when empty ."""
    out: list[str] = []

    def collect(attr: str) -> dict:
        acc: dict = {}
        for r in runs:
            for k, v in (getattr(r, attr, None) or {}).items():
                acc.setdefault(k, []).append((v, r.missions_attempted))
        return acc

    def stat_tables(label: str, items: dict, keys: list, metrics) -> list[str]:
        lines: list[str] = []
        for sub, col, tag in (("Availability_Data", 0, "Ao"), ("Dependability_Data", 1, "Do"),
                              ("Reliability_Data", 2, "R")):
            lines.append(sub)
            lines.append(label + " " * (23 - len(label))
                         + _pad(tag + "Min", 14) + _pad(tag + "Mean", 14)
                         + _pad(tag + "Max", 14) + _pad(tag + "StdDev", 14) + tag + "SEM")
            for k in keys:
                rows = items[k]
                lines.append(_fr_weak_stat_row(rows[0][0]["name"], [metrics(v, ma)[col] for v, ma in rows]))
            lines.append("")
        return lines

    events = collect("weak_events")
    if events:
        keys = sorted(events, key=lambda k: events[k][0][0]["name"].lower())
        out += ["", "EVENT_ANALYSIS", ""]
        out += stat_tables("Event", events, keys, _weak_event_metrics)
        out.append("State_Data")
        out.append("Event                     %Success        %Armed      %Failure ")
        for k in keys:
            rows = events[k]
            row = _pad(rows[0][0]["name"], 20)
            for sk in ("success", "armed", "failure"):
                row += real_to_sn(_fr_mean(_weak_event_metrics(v, ma)[3][sk] for v, ma in rows), 14, 6, 1)
            out.append(row)
        out.append("")
        out.append("")

    hiers = collect("weak_hiers")
    if hiers:
        keys = sorted(hiers, key=lambda k: k)  # source iterates hierGroup (creation order = id order)
        if not events:
            out.append("")   # the events block's leading blank line is written whether or not there are events
        out += ["HIERARCHY_ANALYSIS", "", " " * 23 + "MTBDE         MDT "]
        for k in keys:
            rows = hiers[k]
            mtbde: list[float] = []
            mdt: list[float] = []
            for v, _ma in rows:
                uptime = v["green"] + v["yellow"] + v["blue"]
                de = v.get("down_event_count", 0)
                if de > 0:
                    mtbde.append(uptime / de)
                    mdt.append((v["red"] + v["orange"]) / de)
                else:
                    mtbde.append(uptime)
            row = _pad(rows[0][0]["name"], 23) + real_to_sn(_fr_mean(mtbde), 10, 2, 0) + "    "
            row += real_to_sn(_fr_mean(mdt), 10, 2, 0) if mdt else "N/A"
            out.append(row)
        out.append("")
        out += stat_tables("Hierarchy", hiers, keys, _weak_node_metrics)
        out.append("State_Data")
        out.append("Hierarchy                 %Green     %Yellow        %Red       %Blue      %Brown     %Orange")
        for k in keys:
            rows = hiers[k]
            row = _pad(rows[0][0]["name"], 20)
            for sk in ("green", "yellow", "red", "blue", "brown", "orange"):
                row += real_to_sn(_fr_mean(_weak_node_metrics(v, ma)[3][sk] for v, ma in rows), 12, 6, 1)
            out.append(row)
        out.append("")
    return out


def _fr_hier_costs(runs: list[CostRunReport]) -> dict[int, HierCostRow]:
    """Final Results' hierarchy cost table, which is NOT the mean of the per-run rows.

    Source fills `HierCosts` by hierarchy `seqNum` from the cross-run MEAN block/event costs, then folds nested
    hierarchies into their parents with a loop over the ALPHABETICALLY sorted hierarchies that indexes the source row by
    the ALPHA position `i` (`HierCosts[papa.seqNum,k] += HierCosts[i,k]`) and prints row `i` of the same array under
    the i-th name. When the alphabetical order differs from `seqNum` order (a nested hierarchy named between two
    others, say) the printed rows are not the hierarchies' own: `LadL8A` (real 2026-10-01) shows the cost of `H6`
    under `H3a`. Identical to a plain rollup when the two orders agree."""
    first = runs[0].hiers
    if not first:
        return {}
    n = len(runs)
    own: dict[int, list[float]] = {}
    for hid in first:
        own[hid] = [sum(r.hiers[hid].own[k] for r in runs) / n for k in range(6)]
    by_seq = sorted(first.values(), key=lambda h: h.seq)
    alpha = sorted(first.values(), key=lambda h: h.name.lower())
    cost = {h.seq: list(own[h.hier_id]) for h in by_seq}              # HierCosts[seqNum, 1..6]
    seq_of = {h.hier_id: h.seq for h in by_seq}
    for i in range(len(alpha), 0, -1):                                 # FOR i := totalHiers DOWNTO 1
        h = alpha[i - 1]
        if h.parent_id != 0 and h.parent_id in seq_of:
            papa = seq_of[h.parent_id]
            for k in range(6):
                cost[papa][k] += cost[i][k]                            # the source row is the ALPHA index
    out: dict[int, HierCostRow] = {}
    for i, h in enumerate(alpha, 1):
        c = cost[i]
        out[h.hier_id] = HierCostRow(name=h.name, hier_id=h.hier_id, purchase=c[0], operating=c[1], sparing=c[2],
                                     disposal=c[3], events=c[4], total=c[5], parent_id=h.parent_id, seq=h.seq,
                                     own=h.own)
    return out


def _avg_cost_report(runs: list[CostRunReport]) -> CostRunReport:
    """Cross-run mean of every cost cell -- source aggregates each one
    through its own monitor and prints `.Mean`,
    which for a value sampled once per run is a plain average."""
    n = len(runs)

    def avg_rows(attr: str, ctor, keyfields: tuple[str, ...]):
        keys: dict = {}
        for r in runs:
            for k, row in getattr(r, attr).items():
                keys.setdefault(k, []).append(row)
        out = {}
        for k, rows in keys.items():
            fields = {}
            for f, val in rows[0].__dict__.items():
                if f in keyfields:
                    fields[f] = val
                else:
                    fields[f] = sum(getattr(rw, f) for rw in rows) / len(rows)
            out[k] = ctor(**fields)
        return out

    return CostRunReport(
        blocks=avg_rows("blocks", BlockCostRow, ("name", "in_hier", "sparing_col")),
        spare_pools=avg_rows("spare_pools", PoolCostRow, ("name", "is_resource")),
        resources=avg_rows("resources", PoolCostRow, ("name", "is_resource")),
        events=avg_rows("events", EventCostRow, ("name", "parent_id")),
        hiers=_fr_hier_costs(runs),
        red_cost_configured=runs[0].red_cost_configured,
        system_down_cost=sum(r.system_down_cost for r in runs) / n,
        system_lost_flow_cost=sum(r.system_lost_flow_cost for r in runs) / n,
        grand_total=sum(r.grand_total for r in runs) / n,
    )


def _build_fr_cost_section(all_run_data: list[RunReportData]) -> list[str]:
    """Final-Results ***COST*** (+): the same tables as
    the End_Of_Run section with every cell a cross-run mean. Shares
    `_cost_table_lines` with the other two variants; the column metrics
    match `CalculateInterimCost`'s (one wider at every NAME, two spaces
    between InHier and Good) rather than the End_Of_Run section's."""
    runs = [r.cost for r in all_run_data if r.cost is not None]
    if not runs:
        return []
    capacity_on = any(r.capacity is not None for r in all_run_data)
    return _cost_table_lines(
        _avg_cost_report(runs), in_hier_sep="    ", capacity_on=capacity_on,
        header=True, name_pad_delta=1,
        pool_name_pad_delta=0, pool_row_trailing_space=False,
        operating_header="Block                 InHier        Good      Repair        Idle"
                         "     RepHold          PM      PMHold     Standby        Done",
        totals_prefix="Totals                       ",
    )


def _fr_mean(vals) -> float:
    vals = list(vals)
    return sum(float(v) for v in vals) / len(vals) if vals else 0.0


def flow_loss_across_runs(caps) -> tuple[float, float, float]:
    """The Final Results `FlowLoss` row: `(MinLostFlow, AveLostFlow, MaxLostFlow)`,
    each `FlowGenerated` less a cross-run mean of the system row's flow. Note the INVERSION: the minimum
    loss comes from the MAXIMUM flow mean and vice versa."""
    first = caps[0]

    def sys_field(attr: str) -> float:
        return _fr_mean(c.nodes[c.system_node_id].__dict__[attr] for c in caps)
    g = first.flow_generated
    return (g - sys_field("max_flow"), g - sys_field("ave_flow"), g - sys_field("min_flow"))


def _build_fr_capacity_section(all_run_data: list[RunReportData]) -> list[str]:
    """Final-Results ***CAPACITY***. Same shape
    as the per-run section but every cell is the cross-run MEAN, and the
    flow columns print as REALTOSN decimals where the per-run report uses
    WriteInt -- so this cannot reuse `_build_capacity_section`.

    Unlike the End_Of_Run table, the FR node rows DO carry the separating
    space after the name (matching its own SystemCapacity row);
    only the per-run NodeCapacity rows omit it.
    """
    caps = [r.capacity for r in all_run_data if r.capacity is not None]
    if not caps:
        return []
    first = caps[0]
    flow_generated = first.flow_generated

    def sys_field(attr: str) -> float:
        return _fr_mean(c.nodes[c.system_node_id].__dict__[attr] for c in caps)

    # A leading blank: source's FR capacity section opens with its own
    # `WriteLn` before the title, where the per-run one
    # does not.
    lines = ["", "***CAPACITY***", ""]
    lines.append("FlowGenerated           " + _int_field(flow_generated, 9))
    lines.append("UnitsProduced        " + real_to_sn(_fr_mean(c.units_produced for c in caps), 12, 2, 1))
    lines.append("")
    lines.append("FlowLoss              MinLostFlow  AveLostFlow  MaxLostFlow")
    # `WriteReal(FLOAT(flowGenerated)-Node<X>FlowMon.Mean, 12, 2)` -- note
    # the INVERSION: MinLostFlow comes from the MAX flow mean and vice
    # versa.
    lost_min, lost_ave, lost_max = flow_loss_across_runs(caps)
    lines.append("                     " + _real_field(lost_min, 12, 2) + " "
                 + _real_field(lost_ave, 12, 2) + " " + _real_field(lost_max, 12, 2))
    lines.append("")
    lines.append(_CAPACITY_TABLE_HEADER)
    lines.append(_pad("SystemCapacity", 20) + " "
                 + real_to_sn(sys_field("min_flow"), 12, 2, 1) + " "
                 + real_to_sn(sys_field("ave_flow"), 12, 2, 1) + " "
                 + real_to_sn(sys_field("max_flow"), 12, 2, 1) + " "
                 + real_to_sn(sys_field("usage"), 12, 2, 1) + "   "
                 + real_to_sn(sys_field("min_cap"), 12, 2, 1) + " "
                 + real_to_sn(sys_field("ave_cap"), 12, 2, 1) + " "
                 + real_to_sn(sys_field("max_cap"), 12, 2, 1) + " "
                 + real_to_sn(sys_field("capability"), 12, 2, 1) + "   ")
    lines.append("")
    lines.append("NodeCapacity")
    lines.append(_CAPACITY_TABLE_HEADER)
    by_node: dict[int, list] = {}
    for c in caps:
        for nid, st in c.nodes.items():
            if nid != c.system_node_id:
                by_node.setdefault(nid, []).append(st)
    # Case-INSENSITIVE alpha order, same `NodeAlphaSort` rule the per-run
    # table uses (see its own comment).
    for nid in sorted(by_node, key=lambda n: by_node[n][0].name.lower()):
        stats = by_node[nid]
        full = stats[0].full_flow

        def m(attr: str, stats: list = stats) -> float:
            return _fr_mean(st.__dict__[attr] for st in stats)

        row = _pad(stats[0].name, 20) + " "
        row += real_to_sn(m("min_flow"), 12, 2, 1) + " "
        row += real_to_sn(m("ave_flow"), 12, 2, 1) + " "
        row += real_to_sn(m("max_flow"), 12, 2, 1) + " "
        if full:
            row += "         N/A" + "   " + "         N/A" + " "
            row += "         N/A" + " " + "         N/A" + " "
            row += "         N/A" + "   "
        else:
            row += real_to_sn(m("usage"), 12, 2, 1) + "   "
            row += real_to_sn(m("min_cap"), 12, 2, 1) + " "
            row += real_to_sn(m("ave_cap"), 12, 2, 1) + " "
            row += real_to_sn(m("max_cap"), 12, 2, 1) + " "
            row += real_to_sn(m("capability"), 12, 2, 1) + "   "
        lines.append(row)
    # Three trailing blanks close the section.
    # They belong here, not to whatever follows: `ProduceCostResults` opens
    # straight on `***COST***` with no leading WriteLn of its own,
    # so a model with capacity off must not get them.
    lines.append("")
    lines.append("")
    lines.append("")
    return lines


def _mean_stock(rows: list[StockSnapshot], field_name: str) -> float:
    """Cross-run mean of one stock column -- source takes each column's
    own `GETMONITOR(...).Mean` over the per-run values,
   i.e. a plain average across runs."""
    if not rows:
        return 0.0
    return sum(float(getattr(r, field_name)) for r in rows) / len(rows)


def _fr_stock_row(name: str, rows: list[StockSnapshot], *, with_new: bool) -> str:
    """One aggregate row: every column is `REALTOSN(<col>Mon.Mean,12,2,1)`
    followed by a space, after a 20-char name
    field plus two spaces."""
    cols = ["init"] + (["arrivals"] if with_new else []) + [
        "minimum", "mean", "maximum", "ending", "waits"]
    row = _pad(name, 20) + "  "
    for col in cols:
        row += real_to_sn(_mean_stock(rows, col), 12, 2, 1) + " "
    return row


def _build_fr_stock_sections(all_run_data: list[RunReportData]) -> list[str]:
    """Final-Results counterparts of the End_Of_Run stock tables
   : same three sections, but every cell is the
    MEAN ACROSS RUNS of that run's own value rather than the value
    itself, and the Custom one is titled `Custom_Sparing` (not
    `..._Information` -- source's own inconsistency)."""
    lines: list[str] = []
    if not all_run_data:
        return lines

    def collect(attr: str) -> dict[str, list]:
        out: dict[str, list] = {}
        for run in all_run_data:
            for key, snap in getattr(run, attr).items():
                out.setdefault(key, []).append(snap)
        return out

    custom, pools, resources = collect("custom_stock"), collect("spare_pool_stock"), collect("resource_stock")
    if custom:
        lines.append("Custom_Sparing")
        lines.append("")
        lines.append("blockName            InitNumSpares    NewSpares     MinStock"
                     "     AveStock     MaxStock     EndStock    NumDelays")
        for rows in custom.values():
            # The dict key is a block ID (see `RunReportData.custom_stock`);
            # the DISPLAY name lives on the snapshot.
            lines.append(_fr_stock_row(rows[0].name, rows, with_new=True))
    # Unconditional -- fires whether or not the Custom section
    # above printed. See the utilization table's note.
    lines.append("")
    if pools:
        lines.append("")
        lines.append("SparePool_Sparing_Information")
        lines.append("")
        lines.append("poolName             InitNumSpares    NewSpares     MinStock"
                     "     AveStock     MaxStock     EndStock    NumDelays")
        for name, rows in pools.items():
            lines.append(_fr_stock_row(name, rows, with_new=True))
        lines.append("")
        lines.append("")
        lines.append("")
    if resources:
        lines.append("***RESOURCES***")
        lines.append("")
        lines.append("Resource_Information")
        lines.append("")
        lines.append("resourceName            InitNumRes    MinNumRes    AveNumRes"
                     "    MaxNumRes    EndNumRes    NumDelays")
        for name, rows in resources.items():
            lines.append(_fr_stock_row(name, rows, with_new=False))
        lines.append("")
        lines.append("")
    return lines

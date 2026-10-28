"""Typed conversion from `rbd.RbdSection` rows (raw `{column: string}` dicts)
to `raptor.core.model` dataclasses.

Column meanings come from Raptor 7's save/read code and
are cross-checked against checked-in fixtures where possible (see
`tests/unit/test_fileio_rbd.py`). One mapping is best-effort: the block-level
per-state cost columns (`initial`, `Running`, `standby`, `idle`, `repHold`,
`repPerTime`, `repFixed`, `donePerTime`, `doneFixed`) do not match
Raptor 7's field names verbatim (unlike almost everything else), so they
are inferred from position/context. Every fixture parsed so far has these at
their default values (mostly 1.00), so there is no non-default data point to
check the mapping against; verify against Raptor 7's
Costs tab (`BlockPropObj` field wiring) or a fixture with real cost values
before trusting them for anything that matters.
"""
from __future__ import annotations

from typing import Any, cast

from raptor.core.model import (
    ActiveStatus,
    Link,
    Phase,
    RBDBlock,
    RBDEvent,
    RBDHier,
    RBDNode,
    RBDTrigger,
    SparePool,
    SparingType,
    SystemConfig,
)

_SPARING_TYPE_BY_NAME = {
    # What Raptor 7's loader does with a block's SpType string
    #: "Infinite", "Pooled" and "Custom"
    # map to their types and ANY other string -- including "ColdPool" and
    # "Resource" -- becomes None sparing. Those two are real internal
    # sparingType enum values (1076) but only pool objects
    # carry them, and the save routine never writes them for a block
    # so a block can never have them.
    "Infinite": SparingType.INFINITE,
    "Custom": SparingType.CUSTOM,
    "Pooled": SparingType.SPARE_POOL,
    "ColdPool": SparingType.NONE,
    "Resource": SparingType.NONE,
    "None": SparingType.NONE,
}

#: Reverse of `_SPARING_TYPE_BY_NAME` -- enum member -> the real on-disk
#: SpType string. The GUI write-back path (`diagram.py`'s `_apply_value`
#: "sparing type" row update, `add_block`'s new-row SpType, Block
#: Defaults/Mass Edit's sparing combos in `dialogs.py`) must use this,
#: not `SparingType.name` -- writing the raw enum member name ("CUSTOM")
#: into the file would silently revert every non-None sparing-type change
#: back to `SparingType.NONE` on the next load.
SPARING_TYPE_TO_FILE_STRING = {v: k for k, v in _SPARING_TYPE_BY_NAME.items()}


def _f(row: dict[str, str], key: str, default: float = 0.0) -> float:
    v = row.get(key)
    return float(v) if v not in (None, "") else default


def _i(row: dict[str, str], key: str, default: int = 0) -> int:
    return row_int(row.get(key), default)


def row_int(value: object, default: int = -1) -> int:
    """Coerce a raw cell to an int the way the file format means it.

    Cells are text, and `"3.0"` must read as 3 -- the legacy writer emits
    six-decimal floats for integer columns, so `int(value)` alone would
    reject half the corpus. Anything genuinely unreadable takes *default*.

    **The default is the caller's decision, and it is not cosmetic.**
    Three sentinel values were in use across five copies of this function
    before it was consolidated (review E-12), and they mean different
    things:

    - ``-1`` -- "no element has this id". Right for building lookup keys,
      where a garbage id must not collide with a real one.
    - ``0``  -- "unset". Right where ID 0 is a *meaningful* value, namely
      the file's injected template row that callers deliberately skip.
    - ``None`` -- "absent", for code that distinguishes a missing value
      from a zero one.

    This function does not pick for you; `default` is required to be stated
    so the choice is visible at the call site. The default here is ``-1``,
    matching the majority and the "no element has this id" reading.
    """
    try:
        # `value` is `object` because a row cell can hold anything the writer
        # put there. `float()` accepts str/bytes/SupportsFloat/SupportsIndex,
        # so a narrowing cast says "this is one of those or it raises" rather
        # than pretending the annotation can know. The `except TypeError`
        # below is exactly that case, and it is why the function is total.
        return int(float(cast("Any", value)))
    except (TypeError, ValueError):
        return default


def int_or_none(value: object) -> int | None:
    """As `row_int`, but ``None`` rather than a sentinel for unreadable.

    For callers that must distinguish "absent/unreadable" from a real
    ``0`` -- a sentinel cannot express that, which is why one of the five
    copies existed at all.
    """
    try:
        return int(float(cast("Any", value)))
    except (TypeError, ValueError):
        return None


#: Cell spellings that mean "set". Deliberately an ALLOW-list (review E-13).
#:
#: The legacy writer emits these cells with `str(rso)` -- i.e. `"0"` or
#: `"1"` and nothing else (`rbdlib.make_spare_pool_row`), and every fixture in the repo
#: agrees: 393 pool rows across 235 `.rbd` files carry `RSO`/`ESO`/`SLO` as
#: exactly `'0'`, `'1'`, or absent. Raptor 7 holds these as real
#: booleans (`RSO:=TRUE` / `RSO:=FALSE`), so the
#: on-disk form is the writer's `str()`, not a word.
#:
#: An allow-list therefore costs nothing in fidelity and is the safe
#: default: an unrecognised spelling reads as NOT-set rather than set. The
#: deny-list this replaces had it backwards, so a row containing `"false"`
#: or a bare `"F"` read as **True** -- the flag silently enabled by a
#: spelling that means the opposite. `1`/`true`/`t`/`yes`/`on` cover every
#: positive case any of the three previous readers accepted.
_TRUTHY = frozenset({"1", "t", "true", "y", "yes", "on"})


def cell_truthy(value: object, default: bool = False) -> bool:
    """Is this cell/string "set"? One rule for every boolean-ish cell.

    Lives here, next to the row readers, because it is a property of the
    FILE FORMAT rather than of any one screen. Four readers had their own
    version (see the E-13 finding), and they disagreed in both directions:
    `"2"`, `"0.5"`, `"-1"`, `"yes"`, `"on"`, `"no"`, `"off"`, `"N"`, `"F"`
    and `"false"` were all True to the old deny-list and False to the
    allow-list the GUI's pool dialog used.

    That disagreement was reachable end to end: the pool dialog seeds a
    checkbox from the row with the allow-list, and the engine reads the
    same cell with the deny-list, so a checkbox could render unchecked
    while the engine considered the flag set.
    """
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return text.lower() in _TRUTHY


def _b(row: dict[str, str], key: str, default: bool = False) -> bool:
    return cell_truthy(row.get(key), default)


def _s(row: dict[str, str], key: str, default: str = "") -> str:
    return row.get(key, default)


def _block_event_phase_columns(row: dict[str, str]) -> tuple[list[str], list[float]]:
    """`BLOCK_DATA`/`EVENT_DATA`'s `"P 001"`, `"P 002"`, ... columns --
    present only on a phased row (`rbd.py`'s own ragged-row handling
    already leaves them absent otherwise, so plain absence IS "not
    phased"), each holding a two-token `"X: value"` cell (e.g. `"A:
    1.000"`) that `rbd._zip_row` already merged into ONE string per
    column. Splits that back into parallel `phase_type`/`phase_value`
    lists, in phase-number order."""
    keys = sorted((k for k in row if k.startswith("P ") and k[2:].isdigit()), key=lambda k: int(k[2:]))
    types: list[str] = []
    values: list[float] = []
    for key in keys:
        code, _, value = row[key].partition(":")
        types.append(code.strip())
        values.append(float(value.strip()))
    return types, values


def _node_phase_column(row: dict[str, str]) -> list[int]:
    """`NODE_DATA`'s `"P001"`, `"P002"`, ... columns -- a single bare
    token per phase: `"C"` (Cut) -> `-1`, `"L"` (Linked) -> `0`, anything
    else -> that literal integer (the node's own k-requirement for that
    phase)."""
    keys = sorted((k for k in row if k.startswith("P") and k[1:].isdigit()), key=lambda k: int(k[1:]))
    result: list[int] = []
    for key in keys:
        token = row[key]
        if token == "C":
            result.append(-1)
        elif token == "L":
            result.append(0)
        else:
            result.append(int(token))
    return result


def _norm_dist(d: int) -> int:
    """Port of Raptor 7's `IF nextInt=0 THEN nextInt:=19` for the
    pre-LDT / post-LDT / PM distribution codes :
    a stored `0` means `Fixed(19)`, NOT an invalid code 0."""
    return 19 if d == 0 else d


def _distribution_values(row: dict, dist_col: str, points_key: str, param_prefix: str) -> list[float]:
    """The values a distribution draws from: its three parameters, or for an
    Empirical one (16) the data points from the row's continuation line
    (`rbd._read_empirical_points`; the three parameter columns are 0 then)."""
    points = row.get(points_key)
    if points and _i(row, dist_col) == 16:
        return [float(p) for p in points.split()]
    return [_f(row, f"{param_prefix}{n}") for n in (1, 2, 3)]


def block_from_row(row: dict[str, str]) -> RBDBlock:
    """`BLOCK_DATA` row -> `RBDBlock`. See module docstring for confidence
    notes on the per-state cost fields specifically."""
    phase_type, phase_value = _block_event_phase_columns(row)
    return RBDBlock(
        name=_s(row, "blockName"),
        id=_i(row, "ID"),
        x_position=_f(row, "xPos"),
        y_position=_f(row, "yPos"),
        parent_id=_i(row, "ParentId"),
        connect_into_num=_i(row, "In"),
        connect_out_of_num=_i(row, "Out"),
        uses_phasing=_b(row, "Phasing"),
        sim_start_type=_i(row, "SimStart"),
        amount_exhausted=_f(row, "AmtExhausted"),
        fail_distro=_i(row, "FDist"),
        num_fail_params=_i(row, "#FParam"),
        fail_stream=_i(row, "FStream"),
        fail_values=_distribution_values(row, "FDist", "FEmp", "FParam"),
        repair_distro=_i(row, "RDist"),
        num_repair_params=_i(row, "#RParam"),
        repair_stream=_i(row, "RStream"),
        repair_values=_distribution_values(row, "RDist", "REmp", "RParam"),
        sb_stress=_f(row, "SBStress"),
        gd_type=_i(row, "GDType"),
        gd_rate=_f(row, "GDRate"),
        gd_limit=_f(row, "GDLimit"),
        pool_name=_s(row, "SparePoolName"),
        sparing_type=_SPARING_TYPE_BY_NAME.get(_s(row, "SpType"), SparingType.NONE),
        routine_spare_ordering=_b(row, "RSO"),
        init_stock=_i(row, "Stock"),
        new_spares=_i(row, "NewSp"),
        new_spares_arrival=_f(row, "ArriveEvery"),
        emergency_spare_ordering=_b(row, "ESO"),
        emer_time=_f(row, "EmerTime"),
        stock_level_ordering=_b(row, "SLO"),
        slo_order_level=_i(row, "Level"),
        slo_new_spares=_i(row, "Quan"),
        slo_time=_f(row, "SLOTime"),
        pre_dist=_norm_dist(_i(row, "Pre-LDTDist")),
        pre_params=[_f(row, "Param1#1"), _f(row, "Param2#1"), _f(row, "Param3#1")],
        post_dist=_norm_dist(_i(row, "Post-LDTDist")),
        post_params=[_f(row, "Param1#2"), _f(row, "Param2#2"), _f(row, "Param3#2")],
        resource_type_count=_i(row, "NumRes"),
        repair_resources_needed=_i(row, "#Res1Rep"),
        pm_resources_needed=_i(row, "#Res1pmRep"),
        resource_pool_name=_s(row, "Resource1Name"),
        uses_pm=_b(row, "usesPM"),
        pm_spare_needed=_b(row, "PMSpare"),
        pm_refresh=_b(row, "PMRefresh"),
        pm_mis_defer=_b(row, "PMMisDefer"),
        pm_fail_reset=_b(row, "PMFailReset"),
        pm_req_defer=_b(row, "PMReqDefer"),
        pm_stagger=_f(row, "PMStaggerTime"),
        pm_freq=_f(row, "PMFreq"),
        pm_triggered=_b(row, "pmTriggered"),
        pm_trig=_s(row, "pmTrig"),
        pm_dist=_norm_dist(_i(row, "Dist")),
        pm_params=[_f(row, "Param1#3"), _f(row, "Param2#3"), _f(row, "Param3#3")],
        dependency_num=_i(row, "DepNum"),
        dep_nothing_percent=_f(row, "DepNothing"),
        dep_idle_percent=_f(row, "DepIdle"),
        dep_pm_percent=_f(row, "DepPM"),
        dep_fail_percent=_f(row, "DepFail"),
        dep_pm_threshold=_f(row, "DepPMThresh"),
        def_dep_state_idle=_b(row, "defDepStateIdle"),
        dep_type=_s(row, "DepType"),
        # --- best-effort cost fields (see module docstring) ---
        initial_cost=_f(row, "initial"),
        operating_cost=_f(row, "Running"),
        standby_cost=_f(row, "standby"),
        idle_cost=_f(row, "idle"),
        rep_hold_cost=_f(row, "repHold"),
        repairing_cost=_f(row, "repPerTime"),
        rep_fixed_cost=_f(row, "repFixed"),
        done_cost=_f(row, "donePerTime"),
        done_fixed_cost=_f(row, "doneFixed"),
        pm_hold_cost=_f(row, "pmHoldCost"),
        pm_cost=_f(row, "pmCost"),
        pm_fixed_cost=_f(row, "pmFixedCost"),
        spare_cost=_f(row, "perSpare"),
        emergency_shipping_cost=_f(row, "emerShipping"),
        always_add_done_cost=_b(row, "addDone"),
        active_status=ActiveStatus.ACTIVE,
        phase_type=phase_type,
        phase_value=phase_value,
    )


def event_from_row(row: dict[str, str]) -> RBDEvent:
    """`EVENT_DATA` row -> `RBDEvent`."""
    phase_type, phase_value = _block_event_phase_columns(row)
    return RBDEvent(
        name=_s(row, "eventName"),
        id=_i(row, "ID"),
        x_position=_f(row, "xPos"),
        y_position=_f(row, "yPos"),
        parent_id=_i(row, "ParentId"),
        connect_into_num=_i(row, "In"),
        connect_out_of_num=_i(row, "Out"),
        uses_phasing=_b(row, "Phasing"),
        fail_distro=_i(row, "Distro"),
        fail_stream=_i(row, "failStream"),
        fail_mean=_f(row, "failVal"),
        initial_cost=_f(row, "InitCost"),
        operating_cost=_f(row, "OpCost"),
        repairing_cost=_f(row, "RepCost"),
        active_status=ActiveStatus.ACTIVE,
        phase_type=phase_type,
        phase_value=phase_value,
    )


def node_from_row(row: dict[str, str]) -> RBDNode:
    """`NODE_DATA` row -> `RBDNode`. The flow/capacity columns (`repCap`,
    `repNA`) are not mapped -- no clear model field yet.

    `k=-1` maps to `good_paths=1`, not literal `-1`: source's `.rbd` reader
   treats `-1` as "the user never opened the
    K-of-N dialog for this node" and calls `SetGoodPaths(1)`. Taking `-1`
    at face value makes `determine_node_status`'s `runs>=good_paths_required`
    check trivially true (`0>=-1`), producing `"Degraded"` where the real
    engine reports `"Bad"` (and a malformed `"0--1/1"` format-string
    artifact). The sentinel is a per-node flag set by the GUI, not tied to
    node type or `n`; any code writing this column back (`raptor/gui/ui/
    diagram.py`'s `DiagramView.write_back_to_rbd`) must track "was this
    specific node explicitly edited" as separate state.
    """
    raw_good_paths = _i(row, "k")
    good_paths = 1 if raw_good_paths == -1 else raw_good_paths
    if _i(row, "Type") == 3:
        # EMPIRICAL, not source-derived: Raptor 7 needs one good path at an End node whatever the file says
        # (`LadL2Res`, real 2026-10-01: an End node written k=2 n=3 logs `2-1/3` and stays up on one good
        # path, in 100 runs). The .rbd reader applies `k` to every node type and the
        # source shows no reset, so the mechanism is unknown; the GUI never offers K-of-N for an End node
        # so every real-authored file has k -1 or 1 and nothing else exercises this.
        good_paths = 1
    node_phase = _node_phase_column(row)
    uses_phasing = _b(row, "Phases")
    # Port of Raptor 7 -- `MinGoodPathsReq` is the MINIMUM
    # per-phase k when the node is phased, else `goodPaths`. Used by
    # `ReconfigureSystem`'s permanent-path-deletion threshold.
    phase_min_good_paths = min(node_phase) if (uses_phasing and node_phase) else good_paths
    return RBDNode(
        node_name=_s(row, "Name"),
        id=_i(row, "ID"),
        x_position=_f(row, "xPos"),
        y_position=_f(row, "yPos"),
        parent_id=_i(row, "parent"),
        type_node=_i(row, "Type"),
        good_paths=good_paths,
        min_good_paths_required=phase_min_good_paths,
        # "n" is the node's own out-of-N count in the source; the .rbd
        # column is literally "n" — kept implicit (not stored on RBDNode
        # today, no dedicated field) rather than guessed onto the wrong slot.
        connect_out_of_num=_i(row, "Out"),
        cold_standby=_b(row, "Cold"),
        priority_return=_b(row, "PR"),
        check_autos_first=_b(row, "Autos"),
        k_star=_i(row, "k*"),
        dependency_num=_i(row, "depNum"),
        dep_type=_s(row, "depType"),
        full_flow=_b(row, "fullflow"),
        # `divFlow` column IS `anyPath` -- Raptor 7's own node save
        # code writes it there (`IF node.anyPath: WriteInt(1,1)`;
        # `fullflow` is the NEXT column). `repCap`
        # ("node.reportCapacity, not used") stays
        # unmapped -- genuinely dead, source's own comment says so.
        # `repNA` (= `reportNodeAnal`) is mapped below.
        any_path=_b(row, "divFlow"),
        # `repNA` (= `reportNodeAnal`) -- the GUI's
        # Node Properties Advanced tab exposes it as "Include node analysis
        # information for this node in the output tables and reports",
        # matching the Raptor 7 dialog.
        report_node_anal=_b(row, "repNA"),
        active_status=ActiveStatus.ACTIVE,
        uses_phasing=uses_phasing,
        phase=node_phase,
    )


def system_config_from_row(row: dict[str, str]) -> SystemConfig:
    """`SYSTEM_DATA` row -> `SystemConfig`."""
    return SystemConfig(
        image_string=_s(row, "imageString"),
        termination_type=_s(row, "TerminationType"),
        term_condition=_f(row, "TermCondition"),
        start_stats=_f(row, "StartStats"),
        num_runs=_i(row, "NumRuns"),
        graph_time_slice=_f(row, "graphTimeSlice"),
        graph_ymin=_f(row, "graphYmin"),
        graph_ymax=_f(row, "graphYmax"),
        zoom_val=_f(row, "zoomVal"),
        units=_s(row, "units"),
        graphics_use=_s(row, "graphicsUse"),
        sys_streams=[int(v) for v in row.get("SysStreamsArray", "").split()],
        x_origin=_f(row, "xOrigin"),
        y_origin=_f(row, "yOrigin"),
        show_neg_err_msg=_b(row, "ShowNegErrMsg"),
        ignore_start_fail=_b(row, "IgnoreStartFail"),
        flow_generated=_i(row, "flowGenerated"),
        system_red_cost=_f(row, "systemRedCost"),
        lost_flow_unit_cost=_f(row, "sysLostCost"),
        weak_analysis=_b(row, "weakAnalysis"),
        cost_analysis=_b(row, "costAnalysis"),
        capacity_analysis=_b(row, "capacityAnalysis"),
        node_anal_type=_s(row, "nodeAnalType"),
        gy_threshold=_f(row, "GYthreshold"),
        yr_threshold=_f(row, "YRthreshold"),
    )


def link_from_row(row: dict[str, str]) -> Link:
    """`LINK_DATA` row -> `Link`.

    `FromType`/`ToType` -> `connect_f_ref`/`connect_t_ref` (the RAW/nominal
    endpoint-kind strings, e.g. `"RBDNode"`/`"RBDBlock"`/`"RBDHier"`).
    Distinct from `resolved_from_kind`/`resolved_to_kind`, which are
    `RunEFPA`-time-derived and not populated here -- see `engine/graph.py`."""
    return Link(
        object_num=_i(row, "ID"),
        connect_from_id=_i(row, "FromID"),
        connect_to_id=_i(row, "ToID"),
        connect_f_ref="RBD" + _s(row, "FromType"),
        connect_t_ref="RBD" + _s(row, "ToType"),
        parent_id=_i(row, "pID"),
        cold_priority=_i(row, "ColdPri"),
        auto_switch_probability=_f(row, "AProb"),
        auto_switch_time=_f(row, "AutoTime"),
        manual_switch_time=_f(row, "ManTime"),
        cap_priority=_i(row, "CapPri"),
        nom_flow=_i(row, "nomFlow"),
        max_flow=_i(row, "MaxFlow"),
    )


def hier_from_row(row: dict[str, str]) -> RBDHier:
    """`HIERARCHY_DATA` row -> `RBDHier`.

    Only `name`/`id`/`parent_id`/`in_id`/`out_id` are needed so far --
    `engine/nodes.py`'s `hier_name` threading and `engine/graph.py`'s
    type_node=5 redirect."""
    return RBDHier(
        name=_s(row, "hierarchyName"),
        id=_i(row, "ID"),
        x_position=_f(row, "xPos"),
        y_position=_f(row, "yPos"),
        parent_id=_i(row, "Parent"),
        in_id=_i(row, "in"),
        out_id=_i(row, "out"),
        level=_i(row, "level"),
        zoom=_f(row, "zoom"),
        x_center=_f(row, "xCenter"),
        y_center=_f(row, "yCenter"),
        x_origin=_f(row, "xOrigin"),
        y_origin=_f(row, "yOrigin"),
        my_depth=_i(row, "myDepth"),
        # `Phs` -- a hierarchy has no per-phase VALUE
        # columns of its own (RBDHier has no phase_type/phase_value,
        # unlike block/event), just this bare flag.
        uses_phasing=_b(row, "Phs"),
    )


def spare_pool_from_row(row: dict[str, str]) -> SparePool:
    """`SPARE_POOL_DATA` row -> `SparePool`. `sparing_type` is set to
    `SPARE_POOL` unconditionally -- this section only ever holds Pooled-
    sparing pools (Resource pools are a SEPARATE section, `RESOURCE_DATA`,
    see `resource_pool_from_row` below), matching `SetData`'s own
    `sparingType:=SparePool`."""
    return SparePool(
        pool_name=_s(row, "PoolName"),
        sparing_type=SparingType.SPARE_POOL,
        initial_spares=_i(row, "Stock"),
        new_spares=_i(row, "NewSp"),
        new_spares_arrival=_f(row, "ArriveEvery"),
        routine_spare_ordering=_b(row, "RSO"),
        emergency_spare_ordering=_b(row, "ESO"),
        emergency_time=_f(row, "EmerTime"),
        stock_level_ordering=_b(row, "SLO"),
        slo_order_level=_i(row, "Level"),
        slo_new_spares=_i(row, "Quan"),
        slo_time=_f(row, "SLOTime"),
        spare_cost=_f(row, "SpCost"),
        emergency_shipping_cost=_f(row, "emerShipCost"),
    )


def resource_pool_from_row(row: dict[str, str]) -> SparePool:
    """`RESOURCE_DATA` row -> `SparePool` (reusing the same dataclass as
    `spare_pool_from_row`, discriminated by `sparing_type`, matching how
    the source's own `SparePoolObj` covers both via `SetData`/
    `SetResData`). No RSO/SLO/ESO fields exist for this section at all --
    a Resource pool has a FIXED capacity for the whole run (`NumIn`),
    never replenished; blocks borrow units during repair and return them
    via `TakeBack` afterward, a fundamentally
    simpler mechanism than spare-pool consumption (which is permanent,
    only replenished by RSO/SLO/ESO ordering)."""
    return SparePool(
        pool_name=_s(row, "PoolName"),
        sparing_type=SparingType.RESOURCE,
        initial_spares=_i(row, "NumIn"),
        spare_cost=_f(row, "SpareCost"),
        fixed_per_use=_f(row, "FixedPerUse"),
        cost_per_time=_f(row, "PerTimeUnit"),
        uses_phasing=_b(row, "Phases"),
    )


def trigger_from_row(row: dict[str, str]) -> RBDTrigger:
    """`TRIGGERS` row -> `RBDTrigger`. `IDnum` is unused/always `0` in
    Raptor 7 files and isn't ported, matching `RapTriggerObj`'s own fields,
    which have no id at all -- `TrigName` is the only real identity."""
    return RBDTrigger(
        trigger_name=_s(row, "TriggerName"),
        repeats=_b(row, "Repeats"),
        init_used=_f(row, "InitUsed"),
        dist=_i(row, "Dist", 19),
        params=[_f(row, "Param1"), _f(row, "Param2"), _f(row, "Param3")],
    )


def phase_from_row(row: dict[str, str]) -> Phase:
    """`PHASES` row -> `Phase` -- a fixed-width row, no ragged columns."""
    return Phase(
        phase_name=_s(row, "PhaseName"),
        id=_i(row, "Num"),
        mission=_b(row, "Mission"),
        dist=_i(row, "Dist", 19),
        params=[_f(row, "Param1"), _f(row, "Param2"), _f(row, "Param3")],
    )
